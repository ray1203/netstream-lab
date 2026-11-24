"""보고서용 측정. 서버랑 PC 클라이언트를 같이 띄워서 조건별로 30초씩 돌리고 CSV로 저장
실행: python -m tools.measure  (pc 폴더에서, 11분쯤 걸림)
결과: ../report/raw.csv, runs.csv, abr.csv
"""

from __future__ import annotations

import asyncio
import csv
import random
import sys
import time
from dataclasses import asdict
from pathlib import Path

from netstream.client.core import Client
from netstream.server.app import NetStreamServer
from netstream.server.impair import Counters
from netstream.server.library import Library
from netstream.server.transcoder import Transcoder, find_tool
from netstream.timing import use_precise_clock
from tests.e2e import SAMPLES, make_sample

OUT = Path(__file__).resolve().parents[2] / "report"
PORTS = dict(control_port=19100, tcp_data_port=19101, udp_data_port=19102)
RUN_SECONDS = 30

NORMAL = {"loss": 0, "delay_ms": 0, "jitter_ms": 0, "bandwidth_kbps": 0}
CONDITIONS = [
    ("정상", {}),
    ("손실 1%", {"loss": 0.01}),
    ("손실 5%", {"loss": 0.05}),
    ("손실 5% + 지연 100ms", {"loss": 0.05, "delay_ms": 100}),
    ("지연 50ms + 지터 50ms", {"delay_ms": 50, "jitter_ms": 50}),
    ("대역폭 1Mbps", {"bandwidth_kbps": 1000}),
]
MODES = [("TCP", "tcp", False), ("UDP", "udp", False), ("UDP+NACK", "udp", True)]


async def main() -> int:
    use_precise_clock()
    random.seed(1)
    ffmpeg, ffprobe = find_tool("ffmpeg"), find_tool("ffprobe")
    if not ffmpeg or not ffprobe:
        print("ffmpeg 없음")
        return 1
    make_sample(ffmpeg)
    library = Library([SAMPLES], ffprobe)
    library.scan()
    transcoder = Transcoder(ffmpeg)
    encoder = await transcoder.detect_encoder()
    app = NetStreamServer(library, transcoder, host="127.0.0.1", **PORTS)
    await app.start()
    client = Client("127.0.0.1", PORTS["control_port"])
    reports: list[dict] = []
    client.on_report = reports.append
    await client.connect()
    imp = app.impairment
    OUT.mkdir(exist_ok=True)

    raw_rows, run_rows = [], []
    for cond_name, cond in CONDITIONS:
        for mode_name, mode, nack in MODES:
            imp.update({**NORMAL, **cond})
            await client.set_options(nack=nack, quality="720p")
            session = next(iter(app.sessions.values()))
            before = Counters(**asdict(session.counters))
            reports.clear()
            await client.play(0, mode, 0.0)
            await asyncio.sleep(RUN_SECONDS + 0.5)
            after = Counters(**asdict(session.counters))
            position = client.position()
            stalls = client.clock.stalls
            rows = list(reports)
            await client.stop()
            for t, r in enumerate(rows, 1):
                raw_rows.append({
                    "condition": cond_name, "mode": mode_name, "t": t, "kbps": r["kbps"],
                    "raw_loss_pct": round(r["raw_loss_rate"] * 100, 3), "loss_pct": round(r["loss_rate"] * 100, 3),
                    "packets": r["packets"], "lost": r["lost"], "recovered": r["recovered"], "reordered": r["reordered"],
                    "jitter_ms": r["jitter_ms"], "rtt_ms": r["rtt_ms"], "buffer_ms": r["buffer_ms"], "stalled": int(r["stalled"]),
                })
            run_rows.append({
                "condition": cond_name, "mode": mode_name, "seconds": len(rows),
                "media_received_s": round(position, 1), "stalls": stalls,
                "sent_packets": after.sent_packets - before.sent_packets,
                "sent_bytes": after.sent_bytes - before.sent_bytes,
                "rtx_sent": after.rtx_sent - before.rtx_sent,
                "dropped_random": after.dropped_random - before.dropped_random,
                "dropped_congestion": after.dropped_congestion - before.dropped_congestion,
                "tcp_retx_model": after.tcp_retx - before.tcp_retx,
            })
            print(f"{cond_name:22} {mode_name:9} 받은 분량 {position:5.1f}s  멈춤 {stalls}회", flush=True)
            await asyncio.sleep(1)

    # ABR: 처음 10초는 정상, 그 뒤 대역폭 1Mbps
    imp.update(NORMAL)
    await client.set_options(nack=False, quality="720p")
    await client.set_options(quality="auto")
    reports.clear()
    abr_rows = []
    await client.play(0, "udp", 0.0)
    start = time.perf_counter()
    limited = False
    while time.perf_counter() - start < 50:
        await asyncio.sleep(1.0)
        elapsed = time.perf_counter() - start
        if not limited and elapsed >= 10:
            imp.update({"bandwidth_kbps": 1000})
            limited = True
        r = reports[-1] if reports else {}
        abr_rows.append({
            "t": round(elapsed), "bandwidth_kbps": imp.bandwidth_kbps or "", "quality": client.quality,
            "kbps": r.get("kbps"), "loss_pct": round((r.get("loss_rate") or 0) * 100, 2),
            "buffer_ms": r.get("buffer_ms"), "stalled": int(bool(r.get("stalled"))),
        })
    print("ABR", [row["quality"] for row in abr_rows][::5], flush=True)

    for name, rows in (("raw.csv", raw_rows), ("runs.csv", run_rows), ("abr.csv", abr_rows)):
        with open(OUT / name, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    (OUT / "env.txt").write_text(
        f"date: {time.strftime('%Y-%m-%d %H:%M')}\nencoder: {encoder}\nvideo: testsrc 1280x720 30fps 60s\n"
        f"quality: 720p (2500k video + 128k audio)\nrun: {RUN_SECONDS}s each, loopback 127.0.0.1\n", encoding="utf-8")
    await client.close()
    await app.stop()
    print("끝", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
