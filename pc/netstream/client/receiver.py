"""UDP 수신 로직 (순서 맞추기, 손실 감지, NACK, 지터)
소켓 코드랑 분리해둠. 안드로이드 ReorderBuffer.kt도 같은 방식
"""

from __future__ import annotations

from typing import Callable

from ..protocol import FLAG_EOS, FLAG_RTX

REORDER_WAIT = 0.06    # NACK 꺼져 있을 때 기다리는 시간
NACK_WAIT = 0.6        # NACK 켜져 있을 때
NACK_GRACE = 0.02      # 바로 NACK 안 보내고 조금 기다림 (순서만 바뀐 걸 수도 있어서)
NACK_MIN_INTERVAL = 0.08
MAX_NACKS_PER_PACKET = 4
MAX_NACK_BATCH = 100
MAX_GAP = 3000         # 이것보다 크게 비면 그냥 건너뜀


class ReorderBuffer:

    def __init__(self, deliver: Callable[[int, int, int, bytes], None]):
        self.deliver = deliver
        self.next_seq = 0  # 다음에 내보낼 번호
        self.highest = -1
        self.total: int | None = None  # EOS 받으면 알게 됨
        self._buffer: dict[int, tuple[int, int, bytes]] = {}
        self._missing: dict[int, list[float]] = {}  # seq -> [발견 시각, nack 횟수, 마지막 nack 시각]
        self.received = 0
        self.delivered = 0
        self.lost = 0
        self.recovered = 0
        self.reordered = 0
        self.duplicates = 0
        self.nacks_sent = 0

    @property
    def finished(self) -> bool:
        return self.total is not None and self.next_seq >= self.total

    @property
    def pending(self) -> int:
        return len(self._buffer)

    def on_packet(self, seq: int, media_ms: int, flags: int, payload: bytes, now: float) -> None:
        self.received += 1
        if seq < self.next_seq or seq in self._buffer:
            self.duplicates += 1  # 중복이거나 포기한 뒤에 온 것
            return
        if seq in self._missing:
            # RTX로 왔으면 복구, 아니면 순서만 바뀐 것
            if flags & FLAG_RTX:
                self.recovered += 1
            else:
                self.reordered += 1
            del self._missing[seq]
        elif seq > self.highest:
            self._mark_missing(self.highest + 1, seq, now)
            self.highest = seq
        if flags & FLAG_EOS:
            self.total = seq + 1
        self._buffer[seq] = (media_ms, flags, payload)
        self._flush()

    def mark_end(self, total: int, now: float) -> None:
        self.total = total
        if total - 1 > self.highest:
            self._mark_missing(self.highest + 1, total, now)
            self.highest = total - 1

    def _mark_missing(self, start: int, end: int, now: float) -> None:
        if end - start > MAX_GAP:
            self.lost += end - start
            self._skip_to(end)
            return
        for seq in range(start, end):
            self._missing[seq] = [now, 0, 0.0]

    def _skip_to(self, seq: int) -> None:
        for held in sorted(self._buffer):
            media_ms, flags, payload = self._buffer.pop(held)
            self.delivered += 1
            self.deliver(held, media_ms, flags, payload)
        self.lost += len(self._missing)
        self._missing.clear()
        self.next_seq = seq

    def _flush(self) -> None:
        while self.next_seq in self._buffer:
            media_ms, flags, payload = self._buffer.pop(self.next_seq)
            self.delivered += 1
            self.deliver(self.next_seq, media_ms, flags, payload)
            self.next_seq += 1

    def poll(self, now: float, nack_enabled: bool, rtt: float) -> list[int]:
        # 10ms마다 호출. 오래된 빈자리는 포기하고 NACK 보낼 번호를 리턴
        wait = max(NACK_WAIT, 5 * rtt) if nack_enabled else REORDER_WAIT
        while self.next_seq in self._missing and now - self._missing[self.next_seq][0] >= wait:
            del self._missing[self.next_seq]
            self.lost += 1
            self.next_seq += 1
            self._flush()
        if not nack_enabled:
            return []
        interval = max(NACK_MIN_INTERVAL, 2 * rtt)
        request: list[int] = []
        for seq, state in self._missing.items():
            detected, count, last = state
            if now - detected < NACK_GRACE or count >= MAX_NACKS_PER_PACKET or now - last < interval:
                continue
            state[1] = count + 1
            state[2] = now
            request.append(seq)
            if len(request) >= MAX_NACK_BATCH:
                break
        self.nacks_sent += len(request)
        return request


class JitterMeter:
    # RFC 3550 지터.  J += (|D| - J) / 16
    # transit = 도착 - 송신. 시계 기준이 달라도 차이값은 쓸 수 있음

    def __init__(self):
        self.jitter_ms = 0.0
        self._last_transit: float | None = None

    def on_packet(self, send_ms: int, arrival_ms: float) -> None:
        transit = arrival_ms - send_ms
        if self._last_transit is not None:
            self.jitter_ms += (abs(transit - self._last_transit) - self.jitter_ms) / 16.0
        self._last_transit = transit


class PlayClock:
    # ffplay 버퍼는 알 수가 없어서 추정함 (받은 분량 - 재생된 시간)

    START_MS = 500.0

    def __init__(self):
        self.buffered_ms = 0.0
        self.playing = False
        self.stalled = False
        self.stalls = 0
        self._last: float | None = None

    def _advance(self, now: float) -> None:
        if self.playing and self._last is not None:
            self.buffered_ms -= (now - self._last) * 1000.0
            if self.buffered_ms <= 0:
                self.buffered_ms = 0.0
                self.playing = False
                self.stalled = True
                self.stalls += 1
        self._last = now

    def on_media(self, delta_ms: float, now: float) -> None:
        self._advance(now)
        self.buffered_ms += delta_ms
        if not self.playing and self.buffered_ms >= self.START_MS:
            self.playing = True
            self.stalled = False

    def level(self, now: float) -> float:
        self._advance(now)
        return self.buffered_ms
