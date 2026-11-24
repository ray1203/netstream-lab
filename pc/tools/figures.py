"""보고서용 그림 생성 (matplotlib 필요)
실행: python -m tools.figures  ->  ../report/figures/*.png
measure.py 결과(raw.csv, summary.csv, abr.csv)를 읽음
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle

REPORT = Path(__file__).resolve().parents[2] / "report"
FIG = REPORT / "figures"

BLUE, ORANGE, GREEN = "#2a78d6", "#eb6834", "#1baf7a"
INK, INK2, MUTED, GRID, AXIS = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
MODE_COLOR = {"TCP": BLUE, "UDP": ORANGE, "UDP+NACK": GREEN}
MODES = ["TCP", "UDP", "UDP+NACK"]
COND_EN = {
    "정상": "No impairment",
    "손실 1%": "1% loss",
    "손실 5%": "5% loss",
    "손실 5% + 지연 100ms": "5% loss\n+ 100 ms delay",
    "지연 50ms + 지터 50ms": "50 ms delay\n+ 50 ms jitter",
    "대역폭 1Mbps": "1 Mbps\nbandwidth cap",
}

plt.rcParams.update({
    "font.family": "Arial", "font.size": 10, "axes.edgecolor": AXIS, "axes.labelcolor": INK2,
    "xtick.color": INK2, "ytick.color": INK2, "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True,
    "legend.frameon": False, "figure.dpi": 200, "savefig.bbox": "tight", "savefig.facecolor": "white",
})


def read(name: str) -> list[dict]:
    with open(REPORT / name, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def grouped_bars(ax, conditions, values, ylabel, fmt="{:.0f}"):
    width = 0.26
    for i, mode in enumerate(MODES):
        xs = [c + (i - 1) * width for c in range(len(conditions))]
        ys = [values[(cond, mode)] for cond in conditions]
        bars = ax.bar(xs, ys, width * 0.92, color=MODE_COLOR[mode], label=mode)
        for bar, y in zip(bars, ys):
            ax.text(bar.get_x() + bar.get_width() / 2, y, fmt.format(y), ha="center", va="bottom", fontsize=6.3, color=INK2)
    ax.set_xticks(range(len(conditions)))
    ax.set_xticklabels([COND_EN[c] for c in conditions], fontsize=8.5)
    ax.set_ylabel(ylabel)
    ax.grid(axis="x", visible=False)
    ax.legend(ncol=3, loc="upper center", bbox_to_anchor=(0.5, 1.12))


def fig_throughput(summary):
    conditions = list(COND_EN)
    values = {(r["조건"], r["방식"]): float(r["수신 kbps"]) for r in summary}
    fig, ax = plt.subplots(figsize=(9.2, 3.6))
    grouped_bars(ax, conditions, values, "Received rate (kbps)")
    ax.axhline(2891, color=MUTED, linewidth=1, linestyle="--")
    ax.text(len(conditions) - 0.45, 2891, "stream bitrate", fontsize=7.5, color=MUTED, va="bottom", ha="right")
    ax.set_ylim(0, 4200)
    fig.savefig(FIG / "throughput.png")
    plt.close(fig)


def fig_loss(summary):
    conditions = ["손실 1%", "손실 5%", "손실 5% + 지연 100ms"]
    values = {(r["조건"], r["방식"]): float(r["최종 손실률 %"]) for r in summary}
    fig, ax = plt.subplots(figsize=(6.2, 3.4))
    grouped_bars(ax, conditions, values, "Packets never received (%)", fmt="{:.2f}")
    ax.set_ylim(0, 6.5)
    fig.savefig(FIG / "loss.png")
    plt.close(fig)


def fig_media(summary):
    conditions = list(COND_EN)
    values = {(r["조건"], r["방식"]): float(r["받은 영상 s"]) for r in summary}
    fig, ax = plt.subplots(figsize=(9.2, 3.4))
    grouped_bars(ax, conditions, values, "Video received in 30 s (seconds)", fmt="{:.0f}")
    ax.axhline(30, color=MUTED, linewidth=1, linestyle="--")
    ax.text(-0.45, 30, "real time", fontsize=7.5, color=MUTED, va="bottom")
    ax.set_ylim(0, 42)
    fig.savefig(FIG / "media_received.png")
    plt.close(fig)


def fig_timeseries(raw):
    cond = "손실 5% + 지연 100ms"
    fig, axes = plt.subplots(2, 1, figsize=(7.2, 4.6), sharex=True)
    for mode in ("TCP", "UDP"):
        rows = [r for r in raw if r["condition"] == cond and r["mode"] == mode]
        t = [int(r["t"]) for r in rows]
        axes[0].plot(t, [float(r["kbps"]) for r in rows], color=MODE_COLOR[mode], linewidth=2, label=mode)
        axes[1].plot(t, [float(r["buffer_ms"]) / 1000 for r in rows], color=MODE_COLOR[mode], linewidth=2, label=mode)
    axes[0].set_ylabel("Received rate (kbps)")
    axes[0].set_ylim(0, 6500)
    axes[0].legend(ncol=2, loc="upper right")
    axes[1].set_ylabel("Client buffer (s)")
    axes[1].set_xlabel("Time since playback request (s)")
    axes[1].set_ylim(0, 3.6)
    fig.align_ylabels(axes)
    fig.savefig(FIG / "tcp_vs_udp_timeseries.png")
    plt.close(fig)


def fig_mathis(summary):
    mss = 1460
    rtts = [x / 1000 for x in range(5, 301)]
    fig, ax = plt.subplots(figsize=(6.6, 3.6))
    for p, color in ((0.01, BLUE), (0.05, ORANGE)):
        ax.plot([r * 1000 for r in rtts], [1.22 * mss * 8 / (r * math.sqrt(p)) / 1000 for r in rtts],
                color=color, linewidth=2, label=f"Mathis limit, p = {p:.0%}")
    ax.axhline(2891, color=MUTED, linewidth=1, linestyle="--")
    ax.text(298, 2891, "720p stream (2,891 kbps)", fontsize=8, color=MUTED, va="bottom", ha="right")
    measured = {r["조건"]: float(r["수신 kbps"]) for r in summary if r["방식"] == "TCP"}
    ax.scatter([110], [measured["손실 5% + 지연 100ms"]], s=46, color=ORANGE, edgecolor="white", linewidth=1.5, zorder=3)
    ax.annotate("measured: 585 kbps\n(5% loss, RTT 110 ms)", (110, measured["손실 5% + 지연 100ms"]),
                textcoords="offset points", xytext=(-6, -14), fontsize=8, color=INK2, va="top", ha="right")
    ax.set_yscale("log")
    ax.set_xlabel("Round-trip time (ms)")
    ax.set_ylabel("TCP throughput limit (kbps, log scale)")
    ax.set_xlim(0, 300)
    ax.legend(loc="upper right")
    fig.savefig(FIG / "mathis.png")
    plt.close(fig)


def fig_abr(abr):
    bitrate = {"144p": 218, "240p": 400, "360p": 821, "480p": 1461, "720p": 2891, "1080p": 5676}
    t = [int(r["t"]) for r in abr]
    fig, axes = plt.subplots(2, 1, figsize=(7.2, 4.6), sharex=True)
    axes[0].step(t, [bitrate[r["quality"]] for r in abr], where="post", color=BLUE, linewidth=2, label="Selected quality (bitrate)")
    axes[0].plot(t, [float(r["kbps"] or 0) for r in abr], color=ORANGE, linewidth=1.6, label="Received rate")
    axes[0].hlines(1000, 10, max(t), color=MUTED, linewidth=1, linestyle="--")
    axes[0].text(max(t), 1000, "1 Mbps cap", fontsize=8, color=MUTED, va="bottom", ha="right")
    last = None
    for r in abr:
        if r["quality"] != last:
            axes[0].text(int(r["t"]) + 0.3, bitrate[r["quality"]] + 120, r["quality"], fontsize=8, color=INK2)
            last = r["quality"]
    axes[0].set_ylabel("kbps")
    axes[0].set_ylim(0, 6500)
    axes[0].legend(ncol=2, loc="upper right")
    axes[1].plot(t, [float(r["loss_pct"]) for r in abr], color=BLUE, linewidth=2)
    axes[1].set_ylabel("Packet loss (%)")
    axes[1].set_xlabel("Time (s)")
    axes[1].set_ylim(0, 80)
    for ax in axes:
        ax.axvline(10, color=AXIS, linewidth=1)
    fig.align_ylabels(axes)
    fig.savefig(FIG / "abr.png")
    plt.close(fig)


def box(ax, x, y, w, h, text, fill="#f1f0ea", size=9, bold=False):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.08", facecolor=fill, edgecolor=AXIS, linewidth=1))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=size, color=INK, fontweight="bold" if bold else "normal")


def arrow(ax, x1, y1, x2, y2, text, color, both=False):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="<|-|>" if both else "-|>", mutation_scale=11, color=color, linewidth=1.6))
    ax.text((x1 + x2) / 2, y1 + 0.13, text, ha="center", va="bottom", fontsize=8, color=INK2)


def fig_architecture():
    fig, ax = plt.subplots(figsize=(8.4, 3.9))
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 5.6)
    ax.axis("off")
    ax.add_patch(Rectangle((0.1, 0.2), 4.6, 5.2, facecolor="none", edgecolor=AXIS, linestyle="--", linewidth=1))
    ax.text(0.25, 5.15, "PC server (Python asyncio)", fontsize=9, color=INK2, va="top")
    ax.add_patch(Rectangle((8.3, 0.2), 3.6, 5.2, facecolor="none", edgecolor=AXIS, linestyle="--", linewidth=1))
    ax.text(8.45, 5.15, "Client (Android app / PC test client)", fontsize=9, color=INK2, va="top")
    box(ax, 0.4, 3.75, 1.8, 0.8, "Video files")
    box(ax, 2.6, 3.75, 1.8, 0.8, "ffmpeg\nH.264 + AAC\nMPEG-TS", size=8)
    box(ax, 0.4, 2.2, 4.0, 1.0, "Session / Stream\npacketizer, pacing, NACK buffer, ABR", size=8.5)
    box(ax, 0.4, 1.15, 4.0, 0.7, "Impairment simulator\nloss, delay, jitter, bandwidth cap", size=8)
    box(ax, 0.4, 0.35, 4.0, 0.55, "Dashboard (HTTP + SSE, port 9080)", size=8)
    ax.add_patch(FancyArrowPatch((2.2, 4.15), (2.6, 4.15), arrowstyle="-|>", mutation_scale=10, color=MUTED, linewidth=1.3))
    ax.add_patch(FancyArrowPatch((3.5, 3.75), (3.5, 3.2), arrowstyle="-|>", mutation_scale=10, color=MUTED, linewidth=1.3))
    ax.add_patch(FancyArrowPatch((2.4, 2.2), (2.4, 1.85), arrowstyle="-|>", mutation_scale=10, color=MUTED, linewidth=1.3))
    box(ax, 8.6, 3.55, 3.0, 0.9, "Control client\nlist, play, seek, quality", size=8.5)
    box(ax, 8.6, 2.2, 3.0, 0.9, "Receiver\nreordering, loss detection, NACK", size=8.5)
    box(ax, 8.6, 0.8, 3.0, 0.9, "Player\nExoPlayer / ffplay", size=8.5)
    ax.add_patch(FancyArrowPatch((10.1, 2.2), (10.1, 1.7), arrowstyle="-|>", mutation_scale=10, color=MUTED, linewidth=1.3))
    arrow(ax, 4.75, 4.0, 8.55, 4.0, "TCP 9000  control (JSON messages)", INK2, both=True)
    arrow(ax, 4.75, 2.95, 8.55, 2.95, "TCP 9001  video data (TCP mode)", BLUE)
    arrow(ax, 4.75, 2.25, 8.55, 2.25, "UDP 9002  video data (UDP mode)", ORANGE)
    fig.savefig(FIG / "architecture.png")
    plt.close(fig)


def fig_header():
    fields = [("stream_id", 2), ("seq", 4), ("send_ms", 4), ("media_ms", 4), ("flags", 1), ("len", 2)]
    fig, ax = plt.subplots(figsize=(8.8, 1.9))
    ax.axis("off")
    ax.set_xlim(-0.2, 34.4)
    ax.set_ylim(-1.4, 2.2)
    x = 0.0
    for name, size in fields:
        w = max(size * 1.1, 3.0)
        ax.add_patch(Rectangle((x, 0), w, 1, facecolor="#e6f0fb", edgecolor=AXIS, linewidth=1))
        ax.text(x + w / 2, 0.5, name, ha="center", va="center", fontsize=8.5, color=INK)
        ax.text(x + w / 2, -0.3, f"{size} B", ha="center", va="top", fontsize=8, color=INK2)
        x += w
    header_end = x
    ax.add_patch(Rectangle((x, 0), 34 - x, 1, facecolor="#f1f0ea", edgecolor=AXIS, linewidth=1))
    ax.text((x + 34) / 2, 0.5, "payload: up to 7 MPEG-TS packets\n(7 x 188 = 1316 B)", ha="center", va="center", fontsize=8.5, color=INK)
    ax.text((x + 34) / 2, -0.3, "0 to 1316 B", ha="center", va="top", fontsize=8, color=INK2)
    ax.annotate("", (0, 1.35), (header_end, 1.35), arrowprops=dict(arrowstyle="<->", color=MUTED, linewidth=1))
    ax.text(header_end / 2, 1.5, "header: 17 bytes, big-endian", ha="center", va="bottom", fontsize=8.5, color=INK2)
    ax.text(17, -1.25, "17 + 1316 + 8 (UDP) + 20 (IP) = 1361 bytes, below the 1500-byte Ethernet MTU", ha="center", fontsize=8.5, color=INK2)
    fig.savefig(FIG / "packet_header.png")
    plt.close(fig)


def main() -> None:
    FIG.mkdir(parents=True, exist_ok=True)
    summary, raw, abr = read("summary.csv"), read("raw.csv"), read("abr.csv")
    fig_throughput(summary)
    fig_loss(summary)
    fig_media(summary)
    fig_timeseries(raw)
    fig_mathis(summary)
    fig_abr(abr)
    fig_architecture()
    fig_header()
    print("saved:", ", ".join(sorted(p.name for p in FIG.glob("*.png"))))


if __name__ == "__main__":
    main()
