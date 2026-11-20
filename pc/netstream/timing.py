"""Windows 타이머 보정.

Windows에서 Python 3.12 이하의 asyncio는 15.6ms 단위 시계(GetTickCount64)로 타이머를 돌린다.
그대로 두면 "20ms 지연" 같은 설정이 15.6ms 단위로 뭉개지고, 패킷이 한 묶음씩 몰려 나가서
지터 측정이 부정확해진다. 그래서 두 가지를 바꾼다.

1. 이벤트 루프의 시계를 고해상도 시계(perf_counter)로 바꾼다.
2. 루프가 "시계 해상도만큼 일찍 깨워도 된다"고 보는 여유(_clock_resolution)를 15.6ms에서 0.5ms로 줄인다.
   이 값은 asyncio의 비공개 속성이라 Python 버전에 따라 없을 수 있어, 있을 때만 바꾼다.
3. timeBeginPeriod(1)로 시스템 타이머 주기를 1ms로 낮춘다 (sleep과 I/O 대기의 정밀도).

그래도 Windows의 I/O 대기(IOCP)는 버전에 따라 15ms 단위로 깨어나는 경우가 있어,
시뮬레이터의 지연은 최대 15ms 정도 거칠게 적용될 수 있다. 시연에는 지장이 없는 수준이다.
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
