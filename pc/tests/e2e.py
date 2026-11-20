"""통합 테스트. 서버랑 클라이언트를 같이 띄워서 실제 소켓으로 주고받음
실행: python -m tests.e2e  (pc 폴더에서, ffmpeg 필요)
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

from netstream.client.core import Client
from netstream.server.app import NetStreamServer
from netstream.server.dashboard import Dashboard
from netstream.server.library import Library
from netstream.server.transcoder import Transcoder, find_tool
from netstream.timing import use_precise_clock

SAMPLES = Path(__file__).resolve().parents[2] / "samples"
PORTS = dict(control_port=19000, tcp_data_port=19001, udp_data_port=19002)


def make_sample(ffmpeg: str) -> Path:
    SAMPLES.mkdir(exist_ok=True)
    path = SAMPLES / "testsrc_720p_60s.mp4"
    if not path.exists():
        subprocess.run([
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=60",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=60",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-b:v", "3000k",
            "-c:a", "aac", "-shortest", str(path),
        ], check=True)
    return path


class Collector:
    def __init__(self, client: Client):
        self.reports: list[dict] = []
        self.messages: list[dict] = []
        client.on_report = self.reports.append
        client.on_message = self.messages.append

    def clear(self) -> None:
        self.reports.clear()
        self.messages.clear()

    def total(self, key: str) -> float:
        return sum(r.get(key) or 0 for r in self.reports)

    def loss(self, key: str = "lost") -> float:
        expected = self.total("packets") + self.total("lost")
        return self.total(key) / expected if expected else 0.0


failures: list[str] = []


def check(name: str, ok: bool, detail: str) -> None:
    print(f"  {'통과' if ok else '실패'}  {name}: {detail}")
    if not ok:
        failures.append(name)


async def main() -> int:
    use_precise_clock()
    ffmpeg, ffprobe = find_tool("ffmpeg"), find_tool("ffprobe")
    if not ffmpeg or not ffprobe:
        print("ffmpeg가 없습니다")
        return 1
    make_sample(ffmpeg)
    library = Library([SAMPLES], ffprobe)
    library.scan()
    transcoder = Transcoder(ffmpeg)
    print("인코더:", await transcoder.detect_encoder())
    app = NetStreamServer(library, transcoder, host="127.0.0.1", **PORTS)
    dashboard = Dashboard(app, "127.0.0.1", 19080)
    await app.start()
    await dashboard.start()

    client = Client("127.0.0.1", PORTS["control_port"])
    seen = Collector(client)
    await client.connect()
    videos = await client.list_videos()
    check("목록", len(videos) >= 1, f"{len(videos)}개")
    imp = app.impairment

    print("1. TCP, 열화 없음")
    await client.play(0, "tcp")
    await asyncio.sleep(7)
    check("TCP 수신", seen.total("packets") > 500, f"{seen.total('packets'):.0f} 패킷, {seen.reports[-1]['kbps']} kbps")
    check("TCP 손실 없음", seen.total("lost") == 0, f"손실 {seen.total('lost'):.0f}")
    check("버퍼 확보", seen.reports[-1]["buffer_ms"] > 1500, f"{seen.reports[-1]['buffer_ms']} ms")

    print("2. TCP, 손실 5% + 지연 100ms (모델)")
    imp.update({"loss": 0.05, "delay_ms": 100})
    seen.clear()
    await asyncio.sleep(12)
    rates = [r["kbps"] for r in seen.reports[-5:]]
    check("TCP 처리량 저하", max(rates) < 1500, f"최근 {rates} kbps (모델 상한 {imp.tcp_rate_limit() * 8 / 1000:.0f})")
    check("TCP 버퍼 고갈", any(r["stalled"] for r in seen.reports), f"버퍼 {[r['buffer_ms'] for r in seen.reports[-5:]]} ms")
    check("TCP는 손실이 보이지 않음", seen.total("lost") == 0, f"손실 {seen.total('lost'):.0f}")

    print("3. UDP로 전환, 같은 열화")
    await client.play(0, "udp", client.position(), restart_sink=False)
    seen.clear()
    await asyncio.sleep(10)
    loss = seen.loss()
    check("UDP 손실 관측", 0.025 < loss < 0.085, f"{loss * 100:.1f}%")
    check("UDP는 속도 유지", seen.reports[-1]["kbps"] > 1500, f"{seen.reports[-1]['kbps']} kbps")
    check("UDP는 멈추지 않음", not any(r["stalled"] for r in seen.reports[3:]), f"버퍼 {seen.reports[-1]['buffer_ms']} ms")
    check("RTT에 지연 반영", (seen.reports[-1]["rtt_ms"] or 0) > 90, f"{seen.reports[-1]['rtt_ms']} ms")

    print("4. NACK 켬")
    await client.set_options(nack=True)
    await asyncio.sleep(2)
    seen.clear()
    await asyncio.sleep(8)
    check("NACK 복구", seen.total("recovered") > 20, f"복구 {seen.total('recovered'):.0f}, 남은 손실 {seen.total('lost'):.0f}")
    check("복구 후 손실 감소", seen.loss() < 0.01, f"{seen.loss() * 100:.2f}% (원래 {seen.loss('recovered') * 100 + seen.loss() * 100:.1f}%)")

    print("5. 지터 30ms: 순서 뒤바뀜")
    await client.set_options(nack=False)
    imp.update({"loss": 0, "delay_ms": 20, "jitter_ms": 30})
    await asyncio.sleep(2)
    seen.clear()
    await asyncio.sleep(6)
    check("순서 뒤바뀜 감지", seen.total("reordered") > 10, f"{seen.total('reordered'):.0f}개")
    check("지터 측정", seen.reports[-1]["jitter_ms"] > 2, f"{seen.reports[-1]['jitter_ms']} ms")
    check("뒤바뀜은 손실이 아님", seen.loss() < 0.01, f"{seen.loss() * 100:.2f}%")

    print("6. ABR: 대역폭 1000 kbps 제한")
    imp.update({"loss": 0, "delay_ms": 0, "jitter_ms": 0, "bandwidth_kbps": 1000})
    await client.set_options(quality="auto")
    seen.clear()
    await asyncio.sleep(22)
    changes = [m for m in seen.messages if m.get("type") == "STREAM_CHANGE"]
    check("ABR 화질 내림", bool(changes) and changes[0]["quality"] in ("144p", "240p", "360p") and client.quality != "720p",
          f"{[m['quality'] for m in changes]} → 현재 {client.quality}")
    tail = seen.reports[-4:]
    tail_loss = sum(r["lost"] for r in tail) / max(1, sum(r["lost"] + r["packets"] for r in tail))
    check("내린 뒤 손실 안정", tail_loss < 0.03, f"{tail_loss * 100:.1f}%")

    print("7. 대시보드")
    sample = dashboard.sample()
    check("대시보드 표본", sample["session"] is not None and sample["session"]["mode"] == "udp",
          f"mode={sample['session'] and sample['session']['mode']}, quality={sample['session'] and sample['session']['quality']}")
    reader, writer = await asyncio.open_connection("127.0.0.1", 19080)
    writer.write(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
    page = await reader.read(200)
    writer.close()
    check("대시보드 페이지", page.startswith(b"HTTP/1.1 200"), page[:15].decode())

    print("8. 탐색해서 끝까지: EOS")
    imp.update({"bandwidth_kbps": 0})
    await client.set_options(quality="480p")
    await client.play(0, "udp", 55)
    for _ in range(100):
        if client.ended:
            break
        await asyncio.sleep(0.1)
    r = client._reorder
    check("EOS 수신", client.ended, f"위치 {client.position():.1f}초 (next={r.next_seq}, highest={r.highest}, total={r.total}, "
          f"cid={client.stream_id}, sid={app.active_session.stream.id}, finished={app.active_session.stream.finished})")

    await client.close()
    await dashboard.stop()
    await app.stop()
    print()
    print("모두 통과" if not failures else f"실패 {len(failures)}건: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
