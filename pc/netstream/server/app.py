"""서버 본체 (세션, 스트림, 제어/데이터 채널)"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from time import perf_counter
from collections import OrderedDict, deque

from .. import protocol as P
from .abr import Abr
from .impair import Counters, Impairment, TcpLink, UdpLink
from .library import Library, Video
from .transcoder import LADDER, Transcoder, quality_index

log = logging.getLogger("netstream.server")

LEAD_MS = 3000          # 실시간보다 3초 앞서 보냄 = 클라이언트 버퍼
START_BURST_MS = 500    # 처음 0.5초 분량은 바로 보냄
CATCH_UP_RATE = 2.0     # 그 뒤엔 2배속까지만. 한번에 쏟으면 대역폭 제한에 걸려서 손실남
RTX_BUFFER_PACKETS = 4096  # 재전송용 보관 개수 (약 5MB)
MAX_NACK_SEQS = 200


class Stream:
    # ffmpeg 출력 -> 1316바이트씩 -> 헤더 붙임 -> 속도 조절 -> TCP/UDP 전송

    def __init__(self, session: "Session", stream_id: int, video: Video, mode: str, level: int, position: float):
        self.session = session
        self.id = stream_id
        self.video = video
        self.mode = mode
        self.level = level
        self.quality = LADDER[level]
        self.start_position = position
        self.media_ms = 0.0
        self.next_seq = 0
        self.finished = False
        self._rtx: OrderedDict[int, tuple[int, bytes]] = OrderedDict()
        self._proc: asyncio.subprocess.Process | None = None
        self._task: asyncio.Task | None = None
        self._loop = asyncio.get_running_loop()
        self._t0 = perf_counter()

    def position(self) -> float:
        return self.start_position + self.media_ms / 1000.0

    async def start(self) -> None:
        # ffmpeg 먼저 띄우고 task 생성 (spawn 중에 cancel되면 프로세스가 남음)
        self._proc = await self.session.server.transcoder.spawn(self.video, self.quality, self.start_position)
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        elif self._proc and self._proc.returncode is None:
            self._proc.kill()
            await self._proc.wait()
        if self.mode == "tcp" and self.session.tcp_link:
            self.session.tcp_link.flush()

    def _now_ms(self) -> int:
        return int((perf_counter() - self._t0) * 1000)

    async def _run(self) -> None:
        session = self.session
        stderr_tail: deque[str] = deque(maxlen=5)
        stderr_task = None
        try:
            stderr_task = asyncio.create_task(self._drain_stderr(self._proc.stderr, stderr_tail))
            stdout = self._proc.stdout
            first_pcr = None
            pace_start = None
            while True:
                try:
                    chunk = await stdout.readexactly(P.CHUNK_SIZE)
                except asyncio.IncompleteReadError as end:
                    chunk = end.partial
                if not chunk:
                    break
                pcr = P.last_pcr_ms(chunk)
                if pcr is not None:
                    if first_pcr is None:
                        first_pcr = pcr
                    self.media_ms = max(self.media_ms, pcr - first_pcr)
                # 속도 조절. 영상 시각이 허용치보다 앞서면 sleep
                # (ffmpeg는 파이프가 차면 알아서 멈춤)
                now = self._loop.time()
                if pace_start is None:
                    pace_start = now
                elapsed_ms = (now - pace_start) * 1000
                allowed_ms = min(elapsed_ms + LEAD_MS, elapsed_ms * CATCH_UP_RATE + START_BURST_MS)
                ahead_ms = self.media_ms - allowed_ms
                if ahead_ms > 0:
                    await asyncio.sleep(ahead_ms / 1000)
                await self._send(chunk)
                if len(chunk) < P.CHUNK_SIZE:
                    break

            await self._proc.wait()
            if self.next_seq == 0:
                detail = " / ".join(stderr_tail) or "ffmpeg가 아무것도 출력하지 않았습니다"
                session.event(f"트랜스코딩 실패: {detail}")
                session.send({"type": "ERROR", "stream_id": self.id, "message": f"트랜스코딩 실패: {detail}"})
                return
            # EOS는 제어 채널로도 보냄 (UDP면 EOS 패킷이 없어질 수 있어서)
            await self._send(b"", flags=P.FLAG_EOS)
            self.finished = True
            session.send({"type": "EOS", "stream_id": self.id, "packets": self.next_seq})
        except asyncio.CancelledError:
            raise
        except (ConnectionError, OSError) as error:
            log.info("스트림 %d 전송 중단: %s", self.id, error)
        finally:
            if self._proc and self._proc.returncode is None:
                try:
                    self._proc.kill()
                except ProcessLookupError:
                    pass
                await self._proc.wait()
            # 윈도우에서 이거 안 하면 closed pipe 경고 뜸
            transport = getattr(self._proc, "_transport", None) if self._proc else None
            if transport:
                transport.close()
            if stderr_task:
                stderr_task.cancel()

    @staticmethod
    async def _drain_stderr(stderr: asyncio.StreamReader, tail: deque) -> None:
        while True:
            line = await stderr.readline()
            if not line:
                return
            text = line.decode("utf-8", "replace").strip()
            if text:
                tail.append(text)
                log.debug("ffmpeg: %s", text)

    async def _send(self, payload: bytes, flags: int = 0) -> None:
        session = self.session
        seq = self.next_seq
        self.next_seq += 1
        media_ms = int(self.media_ms)
        packet = P.pack_data(self.id, seq, self._now_ms(), media_ms, flags, payload)
        counters = session.counters
        counters.sent_packets += 1
        counters.sent_bytes += len(packet)
        if self.mode == "udp":
            self._rtx[seq] = (media_ms, payload)
            if len(self._rtx) > RTX_BUFFER_PACKETS:
                self._rtx.popitem(last=False)
            link = session.udp_link
            if link:
                link.send(packet)
        else:
            link = session.tcp_link
            if link is None:
                raise ConnectionError("TCP 데이터 연결이 끊어졌습니다")
            await link.send(packet)

    def retransmit(self, seqs: list) -> None:
        # 재전송도 시뮬레이터를 거침 (또 손실될 수 있음)
        link = self.session.udp_link
        if self.mode != "udp" or link is None:
            return
        for seq in seqs[:MAX_NACK_SEQS]:
            stored = self._rtx.get(seq) if isinstance(seq, int) else None
            if stored is None:
                continue
            media_ms, payload = stored
            packet = P.pack_data(self.id, seq, self._now_ms(), media_ms, P.FLAG_RTX, payload)
            self.session.counters.rtx_sent += 1
            self.session.counters.sent_bytes += len(packet)
            link.send(packet)


class Session:

    def __init__(self, server: "NetStreamServer", reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.server = server
        self.reader = reader
        self.writer = writer
        self.token = secrets.token_hex(8)
        self.peer = writer.get_extra_info("peername")
        self.name = "?"
        self.flush_on_change = False
        self.nack = False
        self.quality_setting = server.default_quality  # auto 또는 화질 이름
        self.counters = Counters()
        self.tcp_link: TcpLink | None = None
        self.udp_link: UdpLink | None = None
        self.stream: Stream | None = None
        self.abr: Abr | None = None
        self.report: dict = {}
        self.report_time = 0.0
        self._stream_ids = 0
        self._closed = False
        # 재생/탐색/화질변경이 겹치면 안 됨 (대시보드 요청은 다른 task에서 옴)
        self._stream_lock = asyncio.Lock()


    def send(self, message: dict) -> None:
        if not self.writer.is_closing():
            self.writer.write(P.encode_frame(message))

    def reply(self, request: dict, message: dict) -> None:
        if "id" in request:
            message["id"] = request["id"]
        self.send(message)

    def error(self, request: dict, text: str) -> None:
        self.reply(request, {"type": "ERROR", "message": text})

    def event(self, text: str) -> None:
        self.server.event(text)

    def options(self) -> dict:
        return {
            "type": "OPTIONS",
            "nack": self.nack,
            "quality": self.quality_setting,
            "current_quality": self.stream.quality.name if self.stream else None,
        }


    async def run(self) -> None:
        log.info("클라이언트 연결: %s", self.peer)
        try:
            while True:
                message = await P.read_frame(self.reader)
                if message is None:
                    break
                await self.handle(message)
        except (ConnectionError, OSError, ValueError) as error:
            log.info("제어 채널 오류 (%s): %s", self.peer, error)
        finally:
            await self.close()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        log.info("클라이언트 종료: %s", self.peer)
        async with self._stream_lock:
            if self.stream:
                await self.stream.stop()
                self.stream = None
        if self.tcp_link:
            self.tcp_link.close()
            self.tcp_link = None
        self.udp_link = None
        self.writer.close()
        self.server.remove_session(self)

    async def handle(self, m: dict) -> None:
        kind = m.get("type")
        if kind == "PING":
            # 시뮬레이터 지연을 여기도 적용해야 RTT가 맞게 나옴
            delay = self.server.impairment.delay_ms / 1000.0
            pong = {"type": "PONG", "t": m.get("t")}
            if delay > 0:
                asyncio.get_running_loop().call_later(delay, self.send, pong)
            else:
                self.send(pong)
        elif kind == "REPORT":
            await self.on_report(m)
        elif kind == "NACK":
            self.counters.nack_messages += 1
            if self.stream and m.get("stream_id") == self.stream.id and isinstance(m.get("seqs"), list):
                self.stream.retransmit(m["seqs"])
        elif kind == "HELLO":
            self.name = str(m.get("client", "?"))[:60]
            self.flush_on_change = bool(m.get("flush_on_change"))
            self.event(f"{self.name} 접속 ({self.peer[0]})")
            self.reply(m, {
                "type": "HELLO_OK",
                "version": P.VERSION,
                "server": self.server.name,
                "token": self.token,
                "tcp_data_port": self.server.tcp_data_port,
                "udp_data_port": self.server.udp_data_port,
                "qualities": [q.to_dict() for q in LADDER],
                "nack": self.nack,
                "quality": self.quality_setting,
            })
        elif kind == "LIST":
            self.reply(m, {"type": "LIST_OK", "videos": [v.to_dict() for v in self.server.library.videos]})
        elif kind == "PLAY":
            await self.on_play(m)
        elif kind == "STOP":
            async with self._stream_lock:
                if self.stream:
                    await self.stream.stop()
                    self.stream = None
            self.reply(m, {"type": "STOP_OK"})
        elif kind == "SET_OPTIONS":
            await self.apply_options(m)
            self.reply(m, self.options())
        else:
            self.error(m, f"알 수 없는 메시지: {kind}")

    async def on_play(self, m: dict) -> None:
        video = self.server.library.get(m.get("video_id"))
        if video is None:
            return self.error(m, "그런 영상이 없습니다")
        mode = m.get("mode")
        if mode not in ("tcp", "udp"):
            return self.error(m, "mode는 tcp 또는 udp여야 합니다")
        if mode == "tcp" and self.tcp_link is None:
            return self.error(m, "TCP 데이터 채널이 연결되지 않았습니다 (ATTACH 필요)")
        if mode == "udp" and self.udp_link is None:
            return self.error(m, "UDP 주소가 등록되지 않았습니다 (ATTACH 데이터그램 필요)")
        await self.server.library.probe(video)
        try:
            position = max(0.0, float(m.get("position") or 0.0))
        except (TypeError, ValueError):
            position = 0.0
        if video.duration and position >= video.duration:
            position = 0.0

        max_level = self._max_level(video)
        if self.quality_setting == "auto":
            start = self.abr.level if self.abr else quality_index("720p")
            self.abr = Abr(start, max_level)
            level = self.abr.level
        else:
            self.abr = None
            level = min(quality_index(self.quality_setting), max_level)

        async with self._stream_lock:
            previous = self.stream
            if previous:
                await previous.stop()
            self.report = {}
            stream = self._new_stream(video, mode, level, position)
            self.server.active_session = self
            if previous is None or previous.mode != mode or previous.video is not video:
                self.event(f"재생 시작: {video.name} / {mode.upper()} / {stream.quality.name}")
            self.reply(m, {
                "type": "PLAY_OK",
                "stream_id": stream.id,
                "video_id": video.id,
                "mode": mode,
                "quality": stream.quality.name,
                "position": position,
                "duration": video.duration,
            })
            await self._start(stream)

    async def _start(self, stream: Stream) -> None:
        try:
            await stream.start()
        except OSError as error:
            self.send({"type": "ERROR", "stream_id": stream.id, "message": f"ffmpeg를 실행하지 못했습니다: {error}"})

    def _max_level(self, video: Video) -> int:
        if not video.height:
            return len(LADDER) - 1
        level = 0
        for index, quality in enumerate(LADDER):
            if quality.height <= video.height:
                level = index
        return level

    def _new_stream(self, video: Video, mode: str, level: int, position: float) -> Stream:
        self._stream_ids = (self._stream_ids % 0xFFFF) + 1
        self.stream = Stream(self, self._stream_ids, video, mode, level, position)
        return self.stream

    async def apply_options(self, values: dict) -> None:
        if "nack" in values:
            nack = bool(values["nack"])
            if nack != self.nack:
                self.nack = nack
                self.event(f"NACK 재전송 {'켬' if nack else '끔'}")
        setting = values.get("quality")
        if setting is not None and setting != self.quality_setting:
            if setting == "auto":
                self.quality_setting = "auto"
                if self.stream:
                    self.abr = Abr(self.stream.level, self._max_level(self.stream.video))
                self.event("자동 화질 조절(ABR) 켬")
            elif quality_index(setting) is not None:
                self.quality_setting = setting
                self.abr = None
                self.event(f"화질 고정: {setting}")
                if self.stream and not self.stream.finished:
                    level = min(quality_index(setting), self._max_level(self.stream.video))
                    if level != self.stream.level:
                        await self.change_quality(level, "사용자 선택")
        self.send(self.options())

    async def on_report(self, m: dict) -> None:
        stream = self.stream
        if stream is None or m.get("stream_id") != stream.id:
            return
        self.report = m
        self.report_time = time.time()
        if self.abr and not stream.finished:
            decision = self.abr.on_report(m, asyncio.get_running_loop().time())
            if decision:
                level, reason = decision
                if level != stream.level:
                    await self.change_quality(level, f"ABR, {reason}")

    async def change_quality(self, level: int, reason: str) -> None:
        # 같은 위치에서 다른 화질로 ffmpeg 재시작
        async with self._stream_lock:
            old = self.stream
            if old is None or old.finished or old.level == level:
                return
            position = old.position()
            # 안드로이드는 버퍼를 비우니까 재생 위치부터 다시 보냄
            if self.flush_on_change and isinstance(self.report.get("position"), (int, float)):
                position = min(position, max(old.start_position, float(self.report["position"])))
            await old.stop()
            self.report = {}
            new = self._new_stream(old.video, old.mode, level, position)
            self.event(f"화질 변경: {old.quality.name} → {new.quality.name} ({reason})")
            self.send({
                "type": "STREAM_CHANGE",
                "stream_id": new.id,
                "quality": new.quality.name,
                "position": position,
                "reason": reason,
            })
            self.send(self.options())
            await self._start(new)


class _UdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, server: "NetStreamServer"):
        self.server = server
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        # 클라이언트가 보내는 건 주소 등록뿐. 출발지 주소를 기억해둠
        if not data.startswith(P.UDP_ATTACH_MAGIC):
            return
        token = data[len(P.UDP_ATTACH_MAGIC):].decode("ascii", "replace")
        session = self.server.sessions.get(token)
        if session is None:
            return
        if session.udp_link is None or session.udp_link.addr != addr:
            session.udp_link = UdpLink(self.transport, addr, self.server.impairment, session.counters)
            log.info("UDP 주소 등록: %s -> %s", session.name, addr)
        session.send({"type": "UDP_ATTACHED", "port": addr[1]})

    def error_received(self, exc) -> None:
        log.debug("UDP 오류: %s", exc)


class NetStreamServer:
    def __init__(self, library: Library, transcoder: Transcoder, host: str = "0.0.0.0",
                 control_port: int = P.CONTROL_PORT, tcp_data_port: int = P.TCP_DATA_PORT,
                 udp_data_port: int = P.UDP_DATA_PORT, default_quality: str = "720p", name: str = "NetStream Lab"):
        self.library = library
        self.transcoder = transcoder
        self.host = host
        self.control_port = control_port
        self.tcp_data_port = tcp_data_port
        self.udp_data_port = udp_data_port
        self.default_quality = default_quality
        self.name = name
        self.impairment = Impairment()
        self.sessions: dict[str, Session] = {}
        self.active_session: Session | None = None
        self.events: deque[dict] = deque(maxlen=100)
        self._event_ids = 0
        self._servers: list = []
        self._udp_transport = None

    def event(self, text: str) -> None:
        self._event_ids += 1
        self.events.append({"id": self._event_ids, "t": time.time(), "text": text})
        log.info("%s", text)

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._servers.append(await asyncio.start_server(self._on_control, self.host, self.control_port))
        self._servers.append(await asyncio.start_server(self._on_tcp_data, self.host, self.tcp_data_port))
        self._udp_transport, _ = await loop.create_datagram_endpoint(
            lambda: _UdpProtocol(self), local_addr=(self.host, self.udp_data_port))

    async def stop(self) -> None:
        for session in list(self.sessions.values()):
            await session.close()
        for server in self._servers:
            server.close()
            await server.wait_closed()
        if self._udp_transport:
            self._udp_transport.close()

    def remove_session(self, session: Session) -> None:
        if self.sessions.pop(session.token, None) is not None:
            self.event(f"{session.name} 접속 종료")
        if self.active_session is session:
            self.active_session = None

    async def _on_control(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        session = Session(self, reader, writer)
        self.sessions[session.token] = session
        await session.run()

    async def _on_tcp_data(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        session = None
        link = None
        try:
            hello = await asyncio.wait_for(P.read_frame(reader), timeout=10)
            session = self.sessions.get(str((hello or {}).get("token")))
            if session is None or (hello or {}).get("type") != "ATTACH":
                writer.write(P.encode_frame({"type": "ERROR", "message": "토큰이 맞지 않습니다"}))
                await writer.drain()
                return
            if session.tcp_link:
                session.tcp_link.close()
            link = TcpLink(writer, self.impairment, session.counters)
            session.tcp_link = link
            writer.write(P.encode_frame({"type": "ATTACH_OK"}))
            log.info("TCP 데이터 채널 연결: %s", session.name)
            while await reader.read(4096):
                pass
        except (ConnectionError, OSError, ValueError, asyncio.TimeoutError):
            pass
        finally:
            if link:
                link.close()
                if session and session.tcp_link is link:
                    session.tcp_link = None
            else:
                writer.close()
