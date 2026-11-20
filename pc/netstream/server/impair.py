"""네트워크 열화 시뮬레이터.

실제 Wi-Fi는 시연 때 원하는 만큼 나빠져 주지 않으므로, 서버가 내보내는 길목에서
손실, 지연, 지터, 대역폭 제한을 일부러 만든다.

UDP 모드는 패킷을 실제로 버리거나 늦게 보낸다. 있는 그대로다.

TCP 모드는 사정이 다르다. 응용 프로그램은 TCP 세그먼트를 직접 버릴 수 없고,
버린다 해도 커널이 재전송해서 응용에는 손실이 보이지 않는다. 그래서 손실이
TCP에 미치는 "결과"를 모델로 흉내 낸다 (UdpLink와 TcpLink의 차이).

1. Head-of-line blocking: 세그먼트 하나를 잃으면 재전송이 도착할 때까지(약 1 RTT)
   그 뒤의 데이터도 응용에 전달되지 않는다.
2. 혼잡 제어: 손실을 혼잡 신호로 보고 창을 줄이므로 처리량이 떨어진다.
   Mathis 공식  처리량 = 1.22 x MSS / (RTT x sqrt(p))  으로 상한을 둔다.

모델이지 실제 손실이 아니라는 점은 대시보드와 보고서에 밝혀 둔다.
"""

from __future__ import annotations

import asyncio
import math
import random
from dataclasses import asdict, dataclass

MSS = 1460            # 이더넷에서 TCP 세그먼트 하나의 최대 payload
BASE_RTT = 0.010      # 지연을 넣지 않았을 때 가정하는 LAN 왕복 시간
UDP_QUEUE_LIMIT = 0.2  # 대역폭 제한 시 병목 큐 길이(초). 넘치면 버린다 (tail drop)
TCP_BACKLOG_LIMIT = 0.3  # TCP 송신 대기열이 이만큼 밀리면 ffmpeg 읽기를 멈춘다 (backpressure)


@dataclass
class Impairment:
    loss: float = 0.0            # 0~1
    delay_ms: float = 0.0        # 서버 -> 클라이언트 편도 지연
    jitter_ms: float = 0.0       # 패킷마다 0~jitter_ms 사이의 추가 지연
    bandwidth_kbps: float = 0.0  # 0이면 제한 없음

    def update(self, values: dict) -> None:
        def number(key: str, low: float, high: float, current: float) -> float:
            try:
                return min(high, max(low, float(values.get(key, current))))
            except (TypeError, ValueError):
                return current

        self.loss = number("loss", 0.0, 0.9, self.loss)
        self.delay_ms = number("delay_ms", 0.0, 2000.0, self.delay_ms)
        self.jitter_ms = number("jitter_ms", 0.0, 1000.0, self.jitter_ms)
        self.bandwidth_kbps = number("bandwidth_kbps", 0.0, 100000.0, self.bandwidth_kbps)

    def to_dict(self) -> dict:
        return asdict(self)

    def model_rtt(self) -> float:
        return BASE_RTT + self.delay_ms / 1000.0

    def tcp_rate_limit(self) -> float | None:
        """TCP 모드의 처리량 상한(바이트/초). 제한이 없으면 None."""
        limits = []
        if self.bandwidth_kbps > 0:
            limits.append(self.bandwidth_kbps * 1000 / 8)
        if self.loss > 0:
            limits.append(1.22 * MSS / (self.model_rtt() * math.sqrt(self.loss)))
        return min(limits) if limits else None


@dataclass
class Counters:
    """서버가 센 누적 값. 대시보드가 1초마다 차이를 구해 초당 값으로 바꾼다."""
    sent_packets: int = 0
    sent_bytes: int = 0
    dropped_random: int = 0      # 시뮬레이터가 무작위로 버린 패킷
    dropped_congestion: int = 0  # 대역폭 제한 큐가 넘쳐 버린 패킷
    rtx_sent: int = 0            # NACK를 받아 다시 보낸 패킷
    nack_messages: int = 0
    tcp_retx: int = 0            # TCP 모델에서 재전송이 일어난 것으로 친 횟수


class UdpLink:
    """클라이언트 한 명에게 가는 UDP 길. 열화를 실제 패킷에 적용한다."""

    def __init__(self, transport: asyncio.DatagramTransport, addr, impairment: Impairment, counters: Counters):
        self.transport = transport
        self.addr = addr
        self.impairment = impairment
        self.counters = counters
        self._loop = asyncio.get_running_loop()
        self._queue_free_at = 0.0

    def send(self, packet: bytes) -> None:
        imp = self.impairment
        now = self._loop.time()
        delay = imp.delay_ms / 1000.0
        if imp.jitter_ms > 0:
            delay += random.uniform(0.0, imp.jitter_ms / 1000.0)
        if imp.bandwidth_kbps > 0:
            rate = imp.bandwidth_kbps * 1000 / 8
            start = max(now, self._queue_free_at)
            if start - now > UDP_QUEUE_LIMIT:
                self.counters.dropped_congestion += 1
                return
            self._queue_free_at = start + len(packet) / rate
            delay += self._queue_free_at - now
        if imp.loss > 0 and random.random() < imp.loss:
            self.counters.dropped_random += 1
            return
        if delay > 0.001:
            self._loop.call_later(delay, self._sendto, packet)
        else:
            self._sendto(packet)

    def _sendto(self, packet: bytes) -> None:
        if not self.transport.is_closing():
            self.transport.sendto(packet, self.addr)


class TcpLink:
    """클라이언트 한 명에게 가는 TCP 데이터 연결. 손실의 영향을 모델로 적용한다."""

    def __init__(self, writer: asyncio.StreamWriter, impairment: Impairment, counters: Counters):
        self.writer = writer
        self.impairment = impairment
        self.counters = counters
        self._loop = asyncio.get_running_loop()
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=4096)
        self._last_release = 0.0
        self._next_slot = 0.0
        self._pump_task = asyncio.create_task(self._pump())

    async def send(self, packet: bytes) -> None:
        imp = self.impairment
        now = self._loop.time()
        base_delay = imp.delay_ms / 1000.0
        release = now + base_delay
        if imp.jitter_ms > 0:
            release += random.uniform(0.0, imp.jitter_ms / 1000.0)
        if imp.loss > 0 and random.random() < imp.loss:
            release += imp.model_rtt()  # 재전송이 도착할 때까지 기다린다
            self.counters.tcp_retx += 1
        # TCP는 순서를 지키므로 앞 패킷보다 먼저 전달될 수 없다 (head-of-line blocking)
        release = max(release, self._last_release)
        rate = imp.tcp_rate_limit()
        if rate:
            release = max(release, self._next_slot)
            self._next_slot = release + len(packet) / rate
        self._last_release = release
        await self._queue.put((release, packet))
        backlog = release - now - base_delay - TCP_BACKLOG_LIMIT
        if backlog > 0:
            await asyncio.sleep(backlog)

    def flush(self) -> None:
        """아직 보내지 않은 패킷을 버린다 (탐색이나 모드 전환 때)."""
        while not self._queue.empty():
            self._queue.get_nowait()
        self._last_release = 0.0
        self._next_slot = 0.0

    async def _pump(self) -> None:
        try:
            while True:
                release, packet = await self._queue.get()
                wait = release - self._loop.time()
                if wait > 0.002:
                    await asyncio.sleep(wait)
                self.writer.write(packet)
                await self.writer.drain()  # 실제 TCP 송신 버퍼가 차면 여기서 멈춘다
        except (ConnectionError, OSError):
            pass

    def close(self) -> None:
        self._pump_task.cancel()
        self.writer.close()
