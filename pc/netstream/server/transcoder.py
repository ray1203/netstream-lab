"""ffmpeg 실행 (H.264 + AAC, MPEG-TS를 stdout으로)"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .library import Video

log = logging.getLogger("netstream.transcoder")

# TS 헤더, PAT/PMT 때문에 실제 전송률은 10%쯤 더 나옴
TS_OVERHEAD = 1.10


@dataclass(frozen=True)
class Quality:
    name: str
    height: int
    fps: int
    video_kbps: int
    audio_kbps: int

    @property
    def total_kbps(self) -> float:
        return (self.video_kbps + self.audio_kbps) * TS_OVERHEAD

    def to_dict(self) -> dict:
        return {"name": self.name, "height": self.height, "fps": self.fps, "kbps": round(self.total_kbps)}


# 낮은 화질 -> 높은 화질 순서
LADDER: list[Quality] = [
    Quality("144p", 144, 15, 150, 48),
    Quality("240p", 240, 24, 300, 64),
    Quality("360p", 360, 30, 650, 96),
    Quality("480p", 480, 30, 1200, 128),
    Quality("720p", 720, 30, 2500, 128),
    Quality("1080p", 1080, 30, 5000, 160),
]


def quality_index(name: str) -> int | None:
    for index, quality in enumerate(LADDER):
        if quality.name == name:
            return index
    return None


def find_tool(name: str) -> str | None:
    # 환경변수 -> PATH -> winget 설치 경로 순으로 찾음
    override = os.environ.get(f"NETSTREAM_{name.upper()}")
    if override and Path(override).is_file():
        return override
    found = shutil.which(name)
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA")
    if local:
        link = Path(local) / "Microsoft" / "WinGet" / "Links" / f"{name}.exe"
        if link.is_file():
            return str(link)
        packages = Path(local) / "Microsoft" / "WinGet" / "Packages"
        if packages.is_dir():
            for candidate in packages.glob(f"Gyan.FFmpeg*/**/bin/{name}.exe"):
                return str(candidate)
    return None


class Transcoder:
    def __init__(self, ffmpeg: str, encoder: str = "auto"):
        self.ffmpeg = ffmpeg
        self.encoder = encoder

    async def detect_encoder(self) -> str:
        # nvenc 되는지 짧게 돌려보고 안 되면 libx264
        if self.encoder != "auto":
            return self.encoder
        self.encoder = "libx264"
        try:
            proc = await asyncio.create_subprocess_exec(
                self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                "-f", "lavfi", "-i", "color=c=black:s=640x360:r=30:d=0.2",
                "-c:v", "h264_nvenc", "-f", "null", "-",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            if await asyncio.wait_for(proc.wait(), timeout=15) == 0:
                self.encoder = "h264_nvenc"
        except (OSError, asyncio.TimeoutError):
            pass
        return self.encoder

    def command(self, video: Video, quality: Quality, position: float) -> list[str]:
        source_fps = video.fps or 30.0
        fps = max(1, min(quality.fps, round(source_fps)))
        gop = fps  # 키프레임 1초마다 (UDP 손실 나도 1초 안에 복구)
        vf = f"scale=-2:'min(ih,{quality.height})',fps={fps},format=yuv420p"
        v = quality.video_kbps
        if self.encoder == "h264_nvenc":
            video_args = [
                "-c:v", "h264_nvenc", "-preset", "p4", "-tune", "ll", "-rc", "cbr",
                "-b:v", f"{v}k", "-maxrate", f"{v}k", "-bufsize", f"{v}k",
                "-g", str(gop), "-bf", "0",
            ]
        else:
            video_args = [
                "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
                "-b:v", f"{v}k", "-maxrate", f"{v}k", "-bufsize", f"{v * 2}k",
                "-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0",
            ]
        return [
            self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
            "-ss", f"{max(0.0, position):.3f}", "-i", str(video.path),
            "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
            "-vf", vf, *video_args,
            "-c:a", "aac", "-b:a", f"{quality.audio_kbps}k", "-ac", "2", "-ar", "48000",
            # 화질 바꿔서 다시 띄워도 타임스탬프가 이어지게
            "-output_ts_offset", f"{max(0.0, position):.3f}",
            # 이거 없으면 32KB씩 모아서 나옴
            "-flush_packets", "1",
            # 기본은 PCR이 100ms 간격이라 전송이 뭉침
            "-pcr_period", "20",
            "-f", "mpegts", "pipe:1",
        ]

    async def spawn(self, video: Video, quality: Quality, position: float) -> asyncio.subprocess.Process:
        cmd = self.command(video, quality, position)
        log.debug("ffmpeg 실행: %s", " ".join(cmd))
        return await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
