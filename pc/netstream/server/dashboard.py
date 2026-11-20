"""대시보드. 라이브러리 없이 asyncio로 만든 간단한 HTTP 서버

GET /         페이지
GET /events   SSE, 1초마다 통계 JSON
POST /api/..  열화 설정, NACK, 화질, 모드
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import asdict
from pathlib import Path

from .app import NetStreamServer
from .impair import Counters
from .transcoder import LADDER

log = logging.getLogger("netstream.dashboard")

WEB_DIR = Path(__file__).parent / "web"
HISTORY_SECONDS = 180
REPORT_FRESH_SECONDS = 3.0


class Dashboard:
    def __init__(self, app: NetStreamServer, host: str, port: int):
        self.app = app
        self.host = host
        self.port = port
        self.history: deque[dict] = deque(maxlen=HISTORY_SECONDS)
        self._subscribers: set[asyncio.Queue] = set()
        self._previous: tuple[object, Counters] | None = None
        self._server = None
        self._sampler = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._on_http, self.host, self.port)
        self._sampler = asyncio.create_task(self._sample_loop())

    async def stop(self) -> None:
        if self._sampler:
            self._sampler.cancel()
        if self._server:
            self._server.close()


    async def _sample_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            try:
                sample = self.sample()
            except Exception:
                log.exception("통계 수집 실패")
                continue
            self.history.append(sample)
            for queue in list(self._subscribers):
                if queue.full():
                    self._subscribers.discard(queue)
                else:
                    queue.put_nowait(sample)

    def sample(self) -> dict:
        app = self.app
        session = app.active_session
        sample: dict = {
            "t": time.time(),
            "impair": app.impairment.to_dict(),
            "tcp_model_kbps": None,
            "encoder": app.transcoder.encoder,
            "session": None,
        }
        limit = app.impairment.tcp_rate_limit()
        if limit:
            sample["tcp_model_kbps"] = round(limit * 8 / 1000)
        if session is None:
            self._previous = None
            return sample

        now_counters = Counters(**asdict(session.counters))
        if self._previous and self._previous[0] is session:
            before = self._previous[1]
        else:
            before = now_counters
        self._previous = (session, now_counters)

        def delta(name: str) -> int:
            return getattr(now_counters, name) - getattr(before, name)

        stream = session.stream
        report = session.report if time.time() - session.report_time < REPORT_FRESH_SECONDS else {}
        sample["session"] = {
            "client": session.name,
            "address": session.peer[0] if session.peer else None,
            "nack": session.nack,
            "quality_setting": session.quality_setting,
            "playing": bool(stream) and not stream.finished,
            "mode": stream.mode if stream else None,
            "quality": stream.quality.name if stream else None,
            "quality_level": stream.level if stream else None,
            "video": stream.video.name if stream else None,
            "position": round(stream.position(), 1) if stream else None,
            "duration": stream.video.duration if stream else None,
            # 서버 쪽 (최근 1초)
            "send_kbps": round(delta("sent_bytes") * 8 / 1000),
            "sent_packets": delta("sent_packets"),
            "dropped_random": delta("dropped_random"),
            "dropped_congestion": delta("dropped_congestion"),
            "rtx_sent": delta("rtx_sent"),
            "tcp_retx": delta("tcp_retx"),
            # 클라이언트 REPORT
            "recv_kbps": report.get("kbps"),
            "raw_loss_pct": _percent(report.get("raw_loss_rate")),
            "loss_pct": _percent(report.get("loss_rate")),
            "lost": report.get("lost"),
            "recovered": report.get("recovered"),
            "reordered": report.get("reordered"),
            "jitter_ms": report.get("jitter_ms"),
            "rtt_ms": report.get("rtt_ms"),
            "buffer_ms": report.get("buffer_ms"),
            "stalled": report.get("stalled"),
        }
        return sample


    async def _on_http(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=10)
            parts = request_line.decode("latin-1").split()
            if len(parts) < 2:
                return
            method, path = parts[0], parts[1].split("?", 1)[0]
            headers: dict[str, str] = {}
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=10)
                if line in (b"\r\n", b"\n", b""):
                    break
                key, _, value = line.decode("latin-1").partition(":")
                headers[key.strip().lower()] = value.strip()
            length = int(headers.get("content-length", "0") or 0)
            body = await reader.readexactly(length) if 0 < length <= 65536 else b""

            if method == "GET" and path in ("/", "/index.html"):
                self._respond(writer, 200, (WEB_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif method == "GET" and path == "/events":
                await self._serve_events(writer)
            elif method == "POST" and path.startswith("/api/"):
                # json만 받음 (다른 사이트에서 몰래 POST 못 하게)
                if not headers.get("content-type", "").startswith("application/json"):
                    self._respond_json(writer, 415, {"error": "application/json만 받습니다"})
                else:
                    status, result = await self._api(path[len("/api/"):], json.loads(body or b"{}"))
                    self._respond_json(writer, status, result)
            else:
                self._respond_json(writer, 404, {"error": "없는 주소입니다"})
            await writer.drain()
        except (ConnectionError, OSError, ValueError, asyncio.TimeoutError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    @staticmethod
    def _respond(writer: asyncio.StreamWriter, status: int, body: bytes, content_type: str) -> None:
        reason = {200: "OK", 400: "Bad Request", 404: "Not Found", 409: "Conflict", 415: "Unsupported Media Type"}.get(status, "OK")
        head = (
            f"HTTP/1.1 {status} {reason}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Cache-Control: no-store\r\n"
            "Connection: close\r\n\r\n"
        )
        writer.write(head.encode("latin-1") + body)

    def _respond_json(self, writer: asyncio.StreamWriter, status: int, value: dict) -> None:
        self._respond(writer, status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    async def _serve_events(self, writer: asyncio.StreamWriter) -> None:
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/event-stream; charset=utf-8\r\n"
            b"Cache-Control: no-store\r\n"
            b"Connection: keep-alive\r\n\r\n"
        )
        hello = {
            "history": list(self.history),
            "events": list(self.app.events),
            "qualities": [q.to_dict() for q in LADDER],
        }
        writer.write(_sse("hello", hello))
        await writer.drain()
        queue: asyncio.Queue = asyncio.Queue(maxsize=30)
        self._subscribers.add(queue)
        last_event = self.app.events[-1]["id"] if self.app.events else 0
        try:
            while True:
                sample = await queue.get()
                new_events = [e for e in self.app.events if e["id"] > last_event]
                if new_events:
                    last_event = new_events[-1]["id"]
                writer.write(_sse("sample", {"sample": sample, "events": new_events}))
                await writer.drain()
        finally:
            self._subscribers.discard(queue)

    async def _api(self, name: str, values: dict) -> tuple[int, dict]:
        app = self.app
        if not isinstance(values, dict):
            return 400, {"error": "JSON 객체가 필요합니다"}
        if name == "impair":
            app.impairment.update(values)
            imp = app.impairment
            app.event(
                f"열화 설정: 손실 {imp.loss * 100:.0f}%, 지연 {imp.delay_ms:.0f}ms, "
                f"지터 {imp.jitter_ms:.0f}ms, 대역폭 {'제한 없음' if not imp.bandwidth_kbps else f'{imp.bandwidth_kbps:.0f}kbps'}"
            )
            return 200, imp.to_dict()
        session = app.active_session
        if session is None:
            return 409, {"error": "재생 중인 클라이언트가 없습니다"}
        if name == "options":
            await session.apply_options(values)
            return 200, {"nack": session.nack, "quality": session.quality_setting}
        if name == "mode":
            mode = values.get("mode")
            if mode not in ("tcp", "udp"):
                return 400, {"error": "mode는 tcp 또는 udp"}
            # 모드는 클라이언트가 PLAY를 다시 보내야 바뀜
            session.send({"type": "SWITCH_MODE", "mode": mode})
            return 200, {"mode": mode}
        return 404, {"error": "없는 API입니다"}


def _percent(rate) -> float | None:
    return round(rate * 100, 2) if isinstance(rate, (int, float)) else None


def _sse(event: str, value: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(value, ensure_ascii=False)}\n\n".encode("utf-8")
