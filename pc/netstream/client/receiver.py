"""UDP 수신 측 로직: 순서 맞추기, 손실 감지, NACK 대상 고르기, 지터와 버퍼 계산.

네트워크 코드와 분리해 순수한 계산만 담았다 (시각을 인자로 받는다). 그래서 단위 테스트가 쉽고,
Android 앱의 ReorderBuffer.kt도 같은 알고리즘을 그대로 옮긴 것이다.
"""

from __future__ import annotations

from typing import Callable

from ..protocol import FLAG_EOS, FLAG_RTX

REORDER_WAIT = 0.06    # NACK를 쓰지 않을 때: 늦게 오는 패킷을 이만큼만 기다리고 포기한다
NACK_WAIT = 0.6        # NACK를 쓸 때: 재전송이 올 때까지 기다리는 최대 시간
NACK_GRACE = 0.02      # 빈자리를 발견하고 이만큼 지나야 NACK를 보낸다 (단순한 순서 뒤바뀜일 수 있으므로)
NACK_MIN_INTERVAL = 0.08
MAX_NACKS_PER_PACKET = 4
MAX_NACK_BATCH = 100
MAX_GAP = 3000         # 이보다 큰 구멍은 복구를 포기하고 건너뛴다


class ReorderBuffer:
    """seq 순서대로 payload를 내보낸다. TCP가 커널 안에서 하는 일의 일부를 직접 구현한 것."""

    def __init__(self, deliver: Callable[[int, int, int, bytes], None]):
        self.deliver = deliver          # deliver(seq, media_ms, flags, payload)
        self.next_seq = 0               # 다음에 내보낼 번호
        self.highest = -1               # 지금까지 본 가장 큰 번호
        self.total: int | None = None   # 스트림의 총 패킷 수 (EOS를 받으면 알게 된다)
        self._buffer: dict[int, tuple[int, int, bytes]] = {}
        self._missing: dict[int, list[float]] = {}  # seq -> [발견 시각, NACK 횟수, 마지막 NACK 시각]
        # 누적 통계
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
            self.duplicates += 1  # 이미 받았거나, 포기한 뒤에 도착했다
            return
        if seq in self._missing:
            # 빈자리가 채워졌다: 재전송으로 왔으면 복구, 원래 패킷이 늦게 왔으면 순서 뒤바뀜
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
        """제어 채널의 EOS로 총 패킷 수를 알게 됐다. 끝부분에서 잃은 패킷도 빈자리로 잡는다."""
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
        """주기적으로(10ms) 부른다. 너무 오래 기다린 빈자리는 포기하고, NACK로 요청할 번호를 돌려준다."""
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
    """도착 간격 지터 (RFC 3550, RTP와 같은 방식).

    transit = 도착 시각 - 송신 시각. 두 시계의 기준이 달라도 "transit의 변화량"은 의미가 있다.
    J = J + (|D| - J) / 16
    """

    def __init__(self):
        self.jitter_ms = 0.0
        self._last_transit: float | None = None

    def on_packet(self, send_ms: int, arrival_ms: float) -> None:
        transit = arrival_ms - send_ms
        if self._last_transit is not None:
            self.jitter_ms += (abs(transit - self._last_transit) - self.jitter_ms) / 16.0
        self._last_transit = transit


class PlayClock:
    """플레이어 버퍼를 흉내 낸다 (PC 테스트 클라이언트용. Android 앱은 ExoPlayer의 실제 값을 쓴다).

    받은 영상 분량(ms)을 쌓고, 재생 중에는 실제 시간만큼 뺀다. 0이 되면 멈춘 것(stall)으로 센다.
    """

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
