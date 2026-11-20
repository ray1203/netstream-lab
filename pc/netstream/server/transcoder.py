"""ffmpeg로 영상을 실시간 트랜스코딩해 MPEG-TS를 표준 출력으로 내보낸다."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .library import Video

log = logging.getLogger("netstream.transcoder")

# MPEG-TS는 188바이트 패킷마다 4바이트 헤더가 붙고 PAT/PMT/PCR도 주기적으로 들어가므로
# 실제 전송률은 영상+음성 비트레이트보다 10% 정도 크다.
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


# 낮은 화질에서 높은 화질 순서. ABR은 이 사다리를 한 칸씩 오르내린다.
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
    """ffmpeg/ffprobe 실행 파일을 찾는다. 환경 변수, PATH, winget 설치 경로 순서."""
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
        """NVENC(NVIDIA 하드웨어 인코더)를 쓸 수 있으면 쓰고, 아니면 libx264(CPU)."""
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
        gop = fps  # 키프레임을 1초마다: UDP에서 패킷을 잃어도 1초 안에 화면이 복구된다
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
            # 출력 타임스탬프를 영상 위치에 맞춘다: 화질이 바뀌어 ffmpeg를 다시 띄워도 시간이 이어진다
            "-output_ts_offset", f"{max(0.0, position):.3f}",
            # 기본값은 32KB씩 모아서 내보낸다. 패킷마다 바로 내보내야 전송이 고르게 된다.
            "-flush_packets", "1",
            # PCR을 20ms마다 넣는다 (기본은 약 100ms). 서버가 PCR을 보고 전송 속도를 맞추므로 촘촘할수록 고르게 나간다.
            "-pcr_period", "20",
            "-f", "mpegts", "pipe:1",
        ]

    async def spawn(self, video: Video, quality: Quality, position: float) -> asyncio.subprocess.Process:
        cmd = self.command(video, quality, position)
        log.debug("ffmpeg 실행: %s", " ".join(cmd))
        return await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
