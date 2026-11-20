"""ABR (자동 화질 조절)

클라이언트가 1초마다 보내는 REPORT 기준.
- 2번 연속 나쁨(손실 3% 초과 or 버퍼 바닥) -> 수신 속도의 70%에 맞는 화질로 내림
- 10번 연속 좋음 -> 한 칸 올림. 올리고 15초 안에 다시 내려오면 다음엔 2배 더 기다림
"""

from __future__ import annotations

from .transcoder import LADDER

SETTLE_SECONDS = 4.0     # 화질 바꾼 직후 보고는 무시
BAD_REPORTS = 2
GOOD_REPORTS = 10
MAX_GOOD_REPORTS = 100
LOSS_THRESHOLD = 0.03
LOW_BUFFER_MS = 300
SAFETY = 0.7


class Abr:
    def __init__(self, level: int, max_level: int):
        self.level = min(level, max_level)
        self.max_level = max_level
        self._bad = 0
        self._good = 0
        self._need_good = GOOD_REPORTS
        self._last_switch = float("-inf")
        self._last_up = float("-inf")

    def on_report(self, report: dict, now: float) -> tuple[int, str] | None:
        # 바꿀 거면 (level, 이유), 아니면 None
        if now - self._last_switch < SETTLE_SECONDS:
            return None
        loss = float(report.get("loss_rate") or 0.0)
        buffer_ms = report.get("buffer_ms")
        stalled = bool(report.get("stalled")) or (buffer_ms is not None and buffer_ms < LOW_BUFFER_MS)
        kbps = float(report.get("kbps") or 0.0)

        if loss > LOSS_THRESHOLD or stalled:
            self._bad += 1
            self._good = 0
        else:
            self._good += 1
            self._bad = 0

        if self._bad >= BAD_REPORTS and self.level > 0:
            target = self.level - 1
            while target > 0 and kbps > 0 and LADDER[target].total_kbps > SAFETY * kbps:
                target -= 1
            if now - self._last_up < 15.0:
                self._need_good = min(self._need_good * 2, MAX_GOOD_REPORTS)
            reason = f"손실 {loss * 100:.1f}%" if loss > LOSS_THRESHOLD else "버퍼 부족"
            return self._switch(target, now), reason

        if self._good >= self._need_good and self.level < self.max_level:
            self._last_up = now
            return self._switch(self.level + 1, now), "네트워크 안정"
        return None

    def _switch(self, level: int, now: float) -> int:
        self.level = level
        self._bad = 0
        self._good = 0
        self._last_switch = now
        return level
