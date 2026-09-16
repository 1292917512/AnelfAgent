"""temporal_scope 时间语义单元测试：超期判定 / 降权 / 注入行过去时标注。"""

from __future__ import annotations

import time

from agent.memory.recall_format import format_memory_line
from agent.memory.store._shared import temporal_is_past, temporal_weight

_NOW = time.time()


class TestTemporalPast:
    def test_state_expires_after_ttl(self):
        meta = {"temporal_scope": "state", "activity_date": "2026-09-01"}
        # activity_date 较老（默认 7 天窗口）→ 过去时
        assert temporal_is_past(meta, _NOW, now=_NOW) is True

    def test_fresh_state_active(self):
        today = time.strftime("%Y-%m-%d", time.localtime(_NOW))
        meta = {"temporal_scope": "state", "activity_date": today}
        assert temporal_is_past(meta, _NOW, now=_NOW) is False

    def test_episode_ttl_shorter(self):
        three_days_ago = _NOW - 3.5 * 86400
        meta = {"temporal_scope": "episode"}
        # episode 默认 3 天：3.5 天前写入 → 过去时
        assert temporal_is_past(meta, three_days_ago, now=_NOW) is True
        # state 默认 7 天：同样 3.5 天仍是当前
        assert temporal_is_past({"temporal_scope": "state"}, three_days_ago, now=_NOW) is False

    def test_pattern_never_expires(self):
        meta = {"temporal_scope": "pattern"}
        assert temporal_is_past(meta, _NOW - 365 * 86400, now=_NOW) is False

    def test_no_scope_never_expires(self):
        assert temporal_is_past({}, _NOW - 365 * 86400, now=_NOW) is False

    def test_activity_date_preferred_over_write_ts(self):
        """事件发生日优先于写入时刻：昨天发生的事今天才记，仍算"最近"。"""
        yesterday = time.strftime(
            "%Y-%m-%d", time.localtime(_NOW - 86400),
        )
        meta = {"temporal_scope": "episode", "activity_date": yesterday}
        assert temporal_is_past(meta, _NOW - 300 * 86400, now=_NOW) is False


class TestTemporalWeight:
    def test_expired_downweighted(self):
        meta = {"temporal_scope": "state", "activity_date": "2026-08-01"}
        w = temporal_weight(meta, _NOW, now=_NOW)
        assert 0 < w < 1

    def test_active_full_weight(self):
        today = time.strftime("%Y-%m-%d", time.localtime(_NOW))
        assert temporal_weight({"temporal_scope": "state", "activity_date": today}, _NOW, now=_NOW) == 1.0
        assert temporal_weight({}, _NOW, now=_NOW) == 1.0


class TestRenderPastTense:
    async def test_past_state_annotated(self):
        line = await format_memory_line(
            None,
            snippet="小明在上海出差",
            tags=["user:qq:123"],
            provenance={"temporal_scope": "state", "activity_date": "2026-08-01"},
            timestamp=_NOW,
        )
        assert "过去时" in line

    async def test_active_state_not_annotated(self):
        today = time.strftime("%Y-%m-%d", time.localtime(_NOW))
        line = await format_memory_line(
            None,
            snippet="小明在上海出差",
            tags=["user:qq:123"],
            provenance={"temporal_scope": "state", "activity_date": today},
            timestamp=_NOW,
        )
        assert "过去时" not in line

    async def test_pattern_not_annotated(self):
        line = await format_memory_line(
            None,
            snippet="小明习惯深夜写代码",
            tags=["user:qq:123"],
            provenance={"temporal_scope": "pattern"},
            timestamp=_NOW,
        )
        assert "过去时" not in line
