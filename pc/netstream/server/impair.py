"""네트워크 열화 시뮬레이터 (손실, 지연, 지터, 대역폭 제한)

UDP는 패킷을 진짜로 버리거나 늦게 보냄.

TCP는 앱에서 세그먼트를 버릴 방법이 없고 버려도 커널이 재전송해버림.
그래서 손실이 났을 때 생기는 결과만 흉내냄:
  - 재전송 올 때까지 뒤 데이터가 막힘 (HOL blocking, 1 RTT)
  - 처리량 감소: Mathis 공식 1.22 * MSS / (RTT * sqrt(p))
실제 손실은 아님. 보고서에 적어둘 것
"""

from __future__ import annotations

import asyncio
import math
import random
from dataclasses import asdict, dataclass

MSS = 1460
BASE_RTT = 0.010      # 지연 0일 때 LAN RTT 가정값
UDP_QUEUE_LIMIT = 0.2  # 대역폭 제한 큐 길이(초), 넘치면 drop
TCP_BACKLOG_LIMIT = 0.3  # 이만큼 밀리면 ffmpeg 읽기를 멈춤


@dataclass
class Impairment:
    loss: float = 0.0            # 0~1
    delay_ms: float = 0.0        # 편도
    jitter_ms: float = 0.0       # 0~jitter_ms 랜덤 추가 지연
    bandwidth_kbps: float = 0.0  # 0 = 제한 없음

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
        # bytes/s, 제한 없으면 None
        limits = []
        if self.bandwidth_kbps > 0:
            limits.append(self.bandwidth_kbps * 1000 / 8)
        if self.loss > 0:
            limits.append(1.22 * MSS / (self.model_rtt() * math.sqrt(self.loss)))
        return min(limits) if limits else None


@dataclass
class Counters:
    # 누적값. 대시보드가 1초마다 차이를 구함
    sent_packets: int = 0
    sent_bytes: int = 0
    dropped_random: int = 0
    dropped_congestion: int = 0  # 대역폭 제한으로 버린 것
    rtx_sent: int = 0
    nack_messages: int = 0
    tcp_retx: int = 0            # TCP 모델에서 재전송으로 친 횟수


class UdpLink:

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
            release += imp.model_rtt()  # 재전송 기다리는 시간
            self.counters.tcp_retx += 1
        # 앞 패킷보다 먼저 못 나감 (HOL blocking)
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
                await self.writer.drain()
        except (ConnectionError, OSError):
            pass

    def close(self) -> None:
        self._pump_task.cancel()
        self.writer.close()
