"""PC 테스트 클라이언트의 네트워크 부분. Android 앱과 같은 프로토콜을 쓴다."""

from __future__ import annotations

import asyncio
import itertools
import socket
import time

from .. import protocol as P
from .receiver import JitterMeter, PlayClock, ReorderBuffer


class NullSink:
    """받은 데이터를 버린다 (재생 없이 통계만 볼 때)."""

    def write(self, data: bytes) -> None:
        pass

    def restart(self) -> None:
        pass

    def close(self) -> None:
        pass


class _UdpReceiver(asyncio.DatagramProtocol):
    def __init__(self, client: "Client"):
        self.client = client

    def datagram_received(self, data: bytes, addr) -> None:
        try:
            packet = P.unpack_data(data)
        except ValueError:
            return
        self.client._on_data(len(data), *packet)

    def error_received(self, exc) -> None:
        pass


class Client:
    def __init__(self, host: str, port: int = P.CONTROL_PORT, sink=None, name: str = "PC 테스트 클라이언트"):
        self.host = host
        self.port = port
        self.sink = sink or NullSink()
        self.name = name
        self.on_report = None   # 1초마다 통계(dict)를 받는 콜백
        self.on_message = None  # 서버가 보낸 알림을 받는 콜백

        self.hello: dict = {}
        self.nack = False
        self.quality_setting = "720p"
        self.quality: str | None = None
        self.rtt_ms: float | None = None

        self.video_id: int | None = None
        self.mode: str | None = None
        self.stream_id: int | None = None
        self.ended = False
        self._stream_start = 0.0
        self._last_media_ms = 0
        self._reorder = ReorderBuffer(self._deliver)
        self._jitter = JitterMeter()
        self.clock = PlayClock()

        self._loop: asyncio.AbstractEventLoop | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._tasks: list[asyncio.Task] = []
        self._tcp_writer: asyncio.StreamWriter | None = None
        self._udp_transport: asyncio.DatagramTransport | None = None
        self._udp_attached = asyncio.Event()
        self._play_lock = asyncio.Lock()
        self._bytes = 0
        self._snapshot = (0, 0, 0, 0, 0, 0)

    # ----- 연결 -----

    async def connect(self) -> dict:
        self._loop = asyncio.get_running_loop()
        self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
        self._tasks.append(asyncio.create_task(self._control_loop()))
        self.hello = await self.request({"type": "HELLO", "client": self.name, "version": P.VERSION})
        self.nack = bool(self.hello.get("nack"))
        self.quality_setting = self.hello.get("quality", self.quality_setting)
        self._tasks.append(asyncio.create_task(self._ping_loop()))
        self._tasks.append(asyncio.create_task(self._report_loop()))
        self._tasks.append(asyncio.create_task(self._poll_loop()))
        return self.hello

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tcp_writer:
            self._tcp_writer.close()
        if self._udp_transport:
            self._udp_transport.close()
        if self._writer:
            self._writer.close()
        self.sink.close()

    def _send(self, message: dict) -> None:
        if self._writer and not self._writer.is_closing():
            self._writer.write(P.encode_frame(message))

    async def request(self, message: dict, timeout: float = 15.0) -> dict:
        """요청을 보내고 같은 id의 응답을 기다린다."""
        request_id = next(self._ids)
        future = self._loop.create_future()
        self._pending[request_id] = future
        self._send({**message, "id": request_id})
        try:
            reply = await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(request_id, None)
        if reply.get("type") == "ERROR":
            raise RuntimeError(reply.get("message", "서버 오류"))
        return reply

    async def _control_loop(self) -> None:
        try:
            while True:
                message = await P.read_frame(self._reader)
                if message is None:
                    break
                future = self._pending.get(message.get("id"))
                if future and not future.done():
                    future.set_result(message)
                    continue
                self._on_control(message)
        except (ConnectionError, OSError):
            pass
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionError("서버와의 연결이 끊어졌습니다"))

    def _on_control(self, m: dict) -> None:
        kind = m.get("type")
        if kind == "PONG":
            sample = (time.perf_counter() - float(m.get("t") or 0)) * 1000
            self.rtt_ms = sample if self.rtt_ms is None else self.rtt_ms * 0.7 + sample * 0.3
            return
        if kind == "UDP_ATTACHED":
            self._udp_attached.set()
            return
        if kind == "OPTIONS":
            self.nack = bool(m.get("nack"))
            self.quality_setting = m.get("quality", self.quality_setting)
            if m.get("current_quality"):
                self.quality = m["current_quality"]
        elif kind == "STREAM_CHANGE":
            # 서버가 화질을 바꿨다: 새 stream_id로 이어서 받는다
            self.quality = m.get("quality")
            self._begin_stream(m["stream_id"], float(m.get("position") or 0.0))
        elif kind == "SWITCH_MODE":
            if self.video_id is not None and m.get("mode") != self.mode:
                asyncio.create_task(self.play(self.video_id, m["mode"], self.position(), restart_sink=False))
        elif kind == "EOS":
            if m.get("stream_id") == self.stream_id:
                self._reorder.mark_end(int(m.get("packets") or 0), self._loop.time())
        if self.on_message:
            self.on_message(m)

    # ----- 데이터 채널 -----

    async def _ensure_tcp(self) -> None:
        if self._tcp_writer and not self._tcp_writer.is_closing():
            return
        reader, writer = await asyncio.open_connection(self.host, self.hello["tcp_data_port"])
        writer.write(P.encode_frame({"type": "ATTACH", "token": self.hello["token"]}))
        reply = await P.read_frame(reader)
        if not reply or reply.get("type") != "ATTACH_OK":
            writer.close()
            raise RuntimeError("TCP 데이터 채널 연결 실패")
        self._tcp_writer = writer
        self._tasks.append(asyncio.create_task(self._tcp_loop(reader)))

    async def _tcp_loop(self, reader: asyncio.StreamReader) -> None:
        try:
            while True:
                packet = await P.read_data_packet(reader)
                if packet is None:
                    break
                self._on_data(P.DATA_HEADER_SIZE + len(packet[5]), *packet)
        except (ConnectionError, OSError):
            pass

    async def _ensure_udp(self) -> None:
        if self._udp_transport is None:
            self._udp_transport, _ = await self._loop.create_datagram_endpoint(
                lambda: _UdpReceiver(self), remote_addr=(self.host, self.hello["udp_data_port"]))
            # 처음 3초 분량이 한꺼번에 오므로 수신 버퍼를 넉넉히 잡는다. 작으면 커널이 버려서 "가짜 손실"이 생긴다.
            sock = self._udp_transport.get_extra_info("socket")
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
            self._tasks.append(asyncio.create_task(self._udp_keepalive()))
        if not self._udp_attached.is_set():
            attach = P.UDP_ATTACH_MAGIC + self.hello["token"].encode("ascii")
            for _ in range(25):
                self._udp_transport.sendto(attach)
                try:
                    await asyncio.wait_for(self._udp_attached.wait(), 0.2)
                    return
                except asyncio.TimeoutError:
                    continue
            raise RuntimeError("UDP 주소 등록 실패 (방화벽을 확인하세요)")

    async def _udp_keepalive(self) -> None:
        attach = P.UDP_ATTACH_MAGIC + self.hello["token"].encode("ascii")
        while True:
            await asyncio.sleep(5)
            self._udp_transport.sendto(attach)

    def _begin_stream(self, stream_id: int, position: float) -> None:
        self.stream_id = stream_id
        self.ended = False
        self._stream_start = position
        self._last_media_ms = 0
        self._reorder = ReorderBuffer(self._deliver)
        self._jitter = JitterMeter()
        self._snapshot = (0, 0, 0, 0, 0, 0)
        self._bytes = 0

    def _on_data(self, size: int, stream_id: int, seq: int, send_ms: int, media_ms: int, flags: int, payload: bytes) -> None:
        if stream_id != self.stream_id:
            return  # 이전 스트림의 늦은 패킷
        now = self._loop.time()
        self._bytes += size
        if not flags & P.FLAG_RTX:
            # perf_counter: Windows의 기본 monotonic 시계는 15ms 단위라 지터를 재기에 너무 거칠다
            self._jitter.on_packet(send_ms, time.perf_counter() * 1000)
        self._reorder.on_packet(seq, media_ms, flags, payload, now)

    def _deliver(self, seq: int, media_ms: int, flags: int, payload: bytes) -> None:
        if flags & P.FLAG_EOS:
            self.ended = True
            return
        if media_ms > self._last_media_ms:
            self.clock.on_media(media_ms - self._last_media_ms, self._loop.time())
            self._last_media_ms = media_ms
        self.sink.write(payload)

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(0.01)
            if self.stream_id is None or self.mode != "udp":
                continue
            rtt = (self.rtt_ms or 20.0) / 1000.0
            seqs = self._reorder.poll(self._loop.time(), self.nack, rtt)
            if seqs:
                self._send({"type": "NACK", "stream_id": self.stream_id, "seqs": seqs})
            if self._reorder.finished:
                self.ended = True

    async def _ping_loop(self) -> None:
        while True:
            self._send({"type": "PING", "t": time.perf_counter()})
            await asyncio.sleep(1.0)

    async def _report_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            if self.stream_id is None:
                continue
            r = self._reorder
            now_snapshot = (self._bytes, r.delivered, r.lost, r.recovered, r.reordered, r.nacks_sent)
            d_bytes, d_delivered, d_lost, d_recovered, d_reordered, d_nacks = (
                a - b for a, b in zip(now_snapshot, self._snapshot))
            self._snapshot = now_snapshot
            expected = d_delivered + d_lost
            buffer_ms = self.clock.level(self._loop.time())
            report = {
                "type": "REPORT",
                "stream_id": self.stream_id,
                "mode": self.mode,
                "kbps": round(d_bytes * 8 / 1000),
                "packets": d_delivered,
                "lost": d_lost,
                "recovered": d_recovered,
                "reordered": d_reordered,
                "nacks": d_nacks,
                "loss_rate": d_lost / expected if expected else 0.0,
                "raw_loss_rate": (d_lost + d_recovered) / expected if expected else 0.0,
                "jitter_ms": round(self._jitter.jitter_ms, 2),
                "rtt_ms": round(self.rtt_ms, 1) if self.rtt_ms is not None else None,
                "buffer_ms": round(buffer_ms),
                "stalled": self.clock.stalled and not self.ended,
            }
            self._send(report)
            if self.on_report:
                self.on_report(report)

    # ----- 명령 -----

    async def list_videos(self) -> list[dict]:
        return (await self.request({"type": "LIST"}))["videos"]

    def position(self) -> float:
        """지금까지 받은 지점(영상 기준 초)."""
        return self._stream_start + self._last_media_ms / 1000.0

    async def play(self, video_id: int, mode: str, position: float = 0.0, restart_sink: bool = True) -> dict:
        async with self._play_lock:
            if mode == "tcp":
                await self._ensure_tcp()
            else:
                await self._ensure_udp()
            if restart_sink:
                self.sink.restart()
                self.clock = PlayClock()
            self.stream_id = None
            reply = await self.request({"type": "PLAY", "video_id": video_id, "mode": mode, "position": position})
            self.video_id = video_id
            self.mode = mode
            self.quality = reply.get("quality")
            self._begin_stream(reply["stream_id"], float(reply.get("position") or 0.0))
            return reply

    async def stop(self) -> None:
        self.stream_id = None
        await self.request({"type": "STOP"})

    async def set_options(self, **options) -> dict:
        return await self.request({"type": "SET_OPTIONS", **options})
