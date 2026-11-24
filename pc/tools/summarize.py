"""measure.py 결과(raw.csv, runs.csv)를 조건별 요약 표로 만듦
실행: python -m tools.summarize  ->  ../report/summary.csv 저장 + 마크다운 표 출력
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "report"
SKIP = 3  # 처음 3초는 시작 구간이라 평균에서 뺌


def read(name: str) -> list[dict]:
    with open(OUT / name, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def main() -> None:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in read("raw.csv"):
        groups[(row["condition"], row["mode"])].append(row)
    runs = {(r["condition"], r["mode"]): r for r in read("runs.csv")}

    summary = []
    for key, rows in groups.items():
        steady = [r for r in rows if int(r["t"]) > SKIP]
        packets = sum(int(r["packets"]) for r in steady)
        lost = sum(int(r["lost"]) for r in steady)
        recovered = sum(int(r["recovered"]) for r in steady)
        expected = packets + lost
        run = runs[key]
        data_packets = int(run["sent_packets"])
        summary.append({
            "조건": key[0],
            "방식": key[1],
            "수신 kbps": round(mean([float(r["kbps"]) for r in steady])),
            "손실률 %": round((lost + recovered) / expected * 100, 2) if expected else 0,
            "최종 손실률 %": round(lost / expected * 100, 2) if expected else 0,
            "멈춘 시간 s": sum(int(r["stalled"]) for r in rows),
            "멈춤 횟수": int(run["stalls"]),
            "받은 영상 s": float(run["media_received_s"]),
            "평균 버퍼 s": round(mean([float(r["buffer_ms"]) for r in steady]) / 1000, 1),
            "지터 ms": round(mean([float(r["jitter_ms"]) for r in steady]), 1),
            "RTT ms": round(mean([float(r["rtt_ms"]) for r in steady if r["rtt_ms"]]), 1),
            "순서 뒤바뀜": sum(int(r["reordered"]) for r in steady),
            "재전송 패킷": int(run["rtx_sent"]),
            "재전송 비율 %": round(int(run["rtx_sent"]) / data_packets * 100, 1) if data_packets else 0,
        })

    with open(OUT / "summary.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)

    columns = list(summary[0])
    print("| " + " | ".join(columns) + " |")
    print("|" + "---|" * len(columns))
    for row in summary:
        print("| " + " | ".join(str(row[c]) for c in columns) + " |")


if __name__ == "__main__":
    main()
