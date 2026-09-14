"""证据数学（evidence.py）单元测试。"""

from __future__ import annotations

import math
import time

from agent.memory.evidence import (
    apply_disputation,
    apply_reinforcement,
    clear_sub_zero,
    evidence_score,
    get_evidence,
    initial_reinforcement,
    is_protected,
    tick_sub_zero,
)
from agent.memory.memory_types import MemoryEntry, MemoryType

DAY = 86400.0


def _entry(memory_type=MemoryType.SEMANTIC, metadata=None) -> MemoryEntry:
    return MemoryEntry(
        memory_type=memory_type, content="测试记忆",
        metadata=metadata if metadata is not None else {},
    )


class TestSeed:
    def test_importance_ladder(self):
        assert initial_reinforcement(0.95) == 0.8
        assert initial_reinforcement(0.85) == 0.6
        assert initial_reinforcement(0.75) == 0.4
        assert initial_reinforcement(0.65) == 0.2
        assert initial_reinforcement(0.5) == 0.0

    def test_protected_permanent(self):
        assert is_protected(_entry(MemoryType.PERMANENT))
        assert is_protected(_entry(metadata={"protected": True}))
        assert not is_protected(_entry())

    def test_protected_score_is_inf(self):
        assert evidence_score(_entry(MemoryType.PERMANENT)) == math.inf


class TestDecay:
    def test_half_life(self):
        now = time.time()
        md: dict = {}
        apply_reinforcement(md, 1.0, user_originated=False, now=now)
        entry = _entry(metadata=md)
        assert evidence_score(entry, now=now + 30 * DAY) == 0.5
        assert evidence_score(entry, now=now + 60 * DAY) == 0.25

    def test_disputation_outlives_reinforcement(self):
        """非对称半衰期：负向证据（180 天）比正向（30 天）衰减慢。"""
        now = time.time()
        md: dict = {}
        apply_reinforcement(md, 1.0, user_originated=False, now=now)
        apply_disputation(md, 1.0, now=now)
        entry = _entry(metadata=md)
        score = evidence_score(entry, now=now + 30 * DAY)
        assert score == 0.5 - math.pow(0.5, 30 / 180)

    def test_score_is_rein_minus_disp(self):
        now = time.time()
        md: dict = {}
        apply_reinforcement(md, 2.0, user_originated=False, now=now)
        apply_disputation(md, 0.5, now=now)
        assert evidence_score(_entry(metadata=md), now=now) == 1.5


class TestSignals:
    def test_user_combo_bonus(self):
        """同一来源反复确认超过阈值后每条 +0.5 连击加成（计数终生不清零）。"""
        now = time.time()
        md: dict = {}
        for _ in range(2):
            apply_reinforcement(md, 1.0, user_originated=True, now=now)
        assert get_evidence(md)["reinforcement"] == 2.0
        apply_reinforcement(md, 1.0, user_originated=True, now=now)
        assert get_evidence(md)["reinforcement"] == 3.5  # 第三条起 +0.5

    def test_non_user_signal_no_combo(self):
        now = time.time()
        md: dict = {}
        for _ in range(4):
            apply_reinforcement(md, 1.0, user_originated=False, now=now)
        assert get_evidence(md)["reinforcement"] == 4.0

    def test_sub_zero_daily_cap_and_clear(self):
        md: dict = {}
        tick_sub_zero(md, today="2026-09-14")
        tick_sub_zero(md, today="2026-09-14")  # 同日不重复
        assert get_evidence(md)["sub_zero_days"] == 1
        tick_sub_zero(md, today="2026-09-15")
        assert get_evidence(md)["sub_zero_days"] == 2
        clear_sub_zero(md)
        assert get_evidence(md)["sub_zero_days"] == 0
