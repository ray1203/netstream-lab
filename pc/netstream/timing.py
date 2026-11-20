"""윈도우 타이머 보정

윈도우 asyncio는 시계가 15.6ms 단위라 지연 20ms 같은 게 제대로 안 먹음.
루프 시계를 perf_counter로 바꾸고 timeBeginPeriod(1) 호출.
_clock_resolution은 비공개 속성이라 있을 때만 건드림.
그래도 IOCP 대기 때문에 15ms 정도 오차는 남을 수 있음
"""

from __future__ import annotations

import asyncio
import sys
import time


def use_precise_clock(loop: asyncio.AbstractEventLoop | None = None) -> None:
    loop = loop or asyncio.get_running_loop()
    if sys.platform != "win32":
        return
    loop.time = time.perf_counter  # type: ignore[method-assign]
    if hasattr(loop, "_clock_resolution"):
        loop._clock_resolution = 0.0005
    try:
        import ctypes
        ctypes.windll.winmm.timeBeginPeriod(1)
    except (OSError, AttributeError):
        pass
