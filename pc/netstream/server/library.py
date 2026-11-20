"""영상 폴더 스캔 + ffprobe로 길이/해상도 읽기"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("netstream.library")

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".ts", ".m4v", ".wmv", ".flv", ".mpg", ".mpeg"}


@dataclass
class Video:
    id: int
    path: Path
    name: str
    size: int
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    probed: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "size": self.size,
            "duration": self.duration,
            "width": self.width,
            "height": self.height,
        }


def _parse_rate(text: str | None) -> float | None:
    if not text or text in ("0/0", "N/A"):
        return None
    try:
        if "/" in text:
            num, den = text.split("/", 1)
            return float(num) / float(den) if float(den) else None
        return float(text)
    except ValueError:
        return None


class Library:
    def __init__(self, roots: list[Path], ffprobe: str):
        self.roots = roots
        self.ffprobe = ffprobe
        self.videos: list[Video] = []
        self._probe_limit = asyncio.Semaphore(4)

    def scan(self) -> None:
        found: list[tuple[str, Path]] = []
        for root in self.roots:
            if root.is_file():
                found.append((root.name, root))
                continue
            for folder, _dirs, files in os.walk(root):
                for file in files:
                    path = Path(folder) / file
                    if path.suffix.lower() in VIDEO_EXTENSIONS:
                        found.append((str(path.relative_to(root)).replace("\\", "/"), path))
        found.sort(key=lambda item: item[0].lower())
        videos = []
        for index, (name, path) in enumerate(found):
            try:
                size = path.stat().st_size
            except OSError:
                continue
            videos.append(Video(id=index, path=path, name=name, size=size))
        self.videos = videos
        log.info("영상 %d개를 찾았습니다", len(videos))

    def get(self, video_id) -> Video | None:
        if isinstance(video_id, int) and 0 <= video_id < len(self.videos):
            return self.videos[video_id]
        return None

    async def probe(self, video: Video) -> None:
        if video.probed:
            return
        async with self._probe_limit:
            if video.probed:
                return
            try:
                proc = await asyncio.create_subprocess_exec(
                    self.ffprobe, "-v", "error", "-print_format", "json",
                    "-show_format", "-show_streams", str(video.path),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                )
                out, _ = await proc.communicate()
                info = json.loads(out.decode("utf-8", "replace") or "{}")
                duration = info.get("format", {}).get("duration")
                video.duration = float(duration) if duration else None
                for stream in info.get("streams", []):
                    if stream.get("codec_type") == "video":
                        video.width = stream.get("width")
                        video.height = stream.get("height")
                        video.fps = _parse_rate(stream.get("avg_frame_rate")) or _parse_rate(stream.get("r_frame_rate"))
                        break
            except (OSError, ValueError) as error:
                log.warning("ffprobe 실패 (%s): %s", video.name, error)
            video.probed = True

    async def probe_all(self) -> None:
        await asyncio.gather(*(self.probe(video) for video in self.videos))
