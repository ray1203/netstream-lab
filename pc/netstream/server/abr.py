"""자동 화질 조절 (ABR, Adaptive Bitrate).

클라이언트가 1초마다 보내는 보고(손실률, 버퍼 잔량, 수신 속도)를 보고
화질 사다리를 오르내린다.

- 내리기: 2번 연속으로 나쁘면 (복구 후 손실 3% 초과 또는 재생 멈춤)
          수신 속도의 70%에 들어가는 가장 높은 화질로 내린다. 최소 한 칸.
- 올리기: 연속으로 좋으면 한 칸 올린다. 올렸다가 15초 안에 다시 내려오면
          다음에는 두 배 더 오래 기다린다 (같은 실패를 반복하지 않도록).
"""

from __future__ import annotations

from .transcoder import LADDER

SETTLE_SECONDS = 4.0     # 화질을 바꾼 직후의 보고는 믿지 않는다
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
        """새 화질 단계와 이유를 돌려준다. 바꿀 필요가 없으면 None."""
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
