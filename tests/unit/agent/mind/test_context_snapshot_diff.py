"""上下文快照 section 变更对比与缓存观测区块单元测试。"""

from __future__ import annotations

import json

import pytest

from agent.llm.types import UsageInfo
from agent.mind.cache_stats import CacheUsageTracker
from agent.mind.context_snapshot import ContextSnapshot


@pytest.fixture
def snapshot(tmp_path, monkeypatch: pytest.MonkeyPatch) -> ContextSnapshot:
    """独立快照实例（持久化指向临时目录，避免污染仓库 logs/）。"""
    monkeypatch.setattr("agent.mind.context_snapshot._SNAPSHOT_DIR", str(tmp_path))
    return ContextSnapshot()


def _messages(recall_content: str = "召回A") -> list[dict]:
    return [
        {"role": "system", "content": "人设", "_layer": "stable"},
        {"role": "system", "content": "摘要", "_layer": "summary"},
        {"role": "user", "content": "你好", "_layer": "conversation"},
        {"role": "system", "content": recall_content, "_layer": "memory"},
    ]


class TestSectionDiff:
    async def test_first_capture_no_baseline(self, snapshot: ContextSnapshot) -> None:
        """首次快照无基线：changed 为 None。"""
        await snapshot.arm()
        assert await snapshot.try_capture(_messages(), [], "fake")
        data = snapshot.get()
        assert data is not None
        for section in data["sections"]:
            assert section["changed"] is None
            assert section["hash"]

    async def test_second_capture_marks_changes(self, snapshot: ContextSnapshot) -> None:
        """第二次快照：仅内容变化的 section 标记 changed=True，其余 False。"""
        await snapshot.arm()
        await snapshot.try_capture(_messages(), [], "fake")
        await snapshot.arm()
        await snapshot.try_capture(_messages(recall_content="召回B"), [], "fake")

        data = snapshot.get()
        assert data is not None
        by_layer = {s["layer"]: s for s in data["sections"]}
        assert by_layer["stable"]["changed"] is False
        assert by_layer["summary"]["changed"] is False
        assert by_layer["conversation"]["changed"] is False
        assert by_layer["memory"]["changed"] is True

    async def test_new_layer_labels(self, snapshot: ContextSnapshot) -> None:
        """新增 layer（summary/profile/provider）有中文标签且按固定顺序排列。"""
        msgs = [
            {"role": "system", "content": "人设", "_layer": "stable"},
            {"role": "system", "content": "摘要", "_layer": "summary"},
            {"role": "user", "content": "你好", "_layer": "conversation"},
            {"role": "system", "content": "画像", "_layer": "profile"},
            {"role": "system", "content": "召回", "_layer": "memory"},
        ]
        await snapshot.arm()
        await snapshot.try_capture(msgs, [], "fake")
        data = snapshot.get()
        assert data is not None
        layers = [s["layer"] for s in data["sections"]]
        assert layers == ["stable", "summary", "conversation", "profile", "memory"]
        by_layer = {s["layer"]: s for s in data["sections"]}
        assert "摘要" in by_layer["summary"]["label"]
        assert "画像" in by_layer["profile"]["label"]


class TestCacheBlock:
    async def test_cache_block_present(
        self, snapshot: ContextSnapshot, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """cache 区块包含上次调用真实用量与可复用前缀估算。"""
        tracker = CacheUsageTracker()
        tracker.record(UsageInfo(
            prompt_tokens=1000, completion_tokens=50, total_tokens=1050,
            cache_read_input_tokens=800,
        ))
        # _build_cache_block 内部延迟 import cache_usage_tracker 单例，替换之
        import agent.mind.cache_stats as cache_mod
        monkeypatch.setattr(cache_mod, "cache_usage_tracker", tracker)

        await snapshot.arm()
        await snapshot.try_capture(_messages(), [], "fake")
        # 第二次快照（内容不变）→ 全部 section 未变更 → 前缀估算覆盖全部
        await snapshot.arm()
        await snapshot.try_capture(_messages(), [], "fake")

        data = snapshot.get()
        assert data is not None
        cache = data["cache"]
        assert cache["last_call"]["cache_read_input_tokens"] == 800
        assert cache["recent"]["sample_count"] == 1
        # 全部 section 未变更：可复用前缀 = 全部 section tokens 之和
        total = sum(s["estimated_tokens"] for s in data["sections"])
        assert cache["estimated_cacheable_prefix_tokens"] == total

    async def test_prefix_estimation_stops_at_change(
        self, snapshot: ContextSnapshot, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """头部连续未变更才计入前缀：conversation 变化后 memory 不计入。"""
        import agent.mind.cache_stats as cache_mod
        monkeypatch.setattr(cache_mod, "cache_usage_tracker", CacheUsageTracker())

        await snapshot.arm()
        await snapshot.try_capture(_messages(), [], "fake")

        changed = _messages()
        changed[2] = {"role": "user", "content": "新消息", "_layer": "conversation"}
        await snapshot.arm()
        await snapshot.try_capture(changed, [], "fake")

        data = snapshot.get()
        assert data is not None
        by_layer = {s["layer"]: s for s in data["sections"]}
        expected = (
            by_layer["stable"]["estimated_tokens"]
            + by_layer["summary"]["estimated_tokens"]
        )
        assert data["cache"]["estimated_cacheable_prefix_tokens"] == expected


class TestContinuousCapture:
    async def test_continuous_captures_every_call(self, snapshot: ContextSnapshot) -> None:
        """连续模式：无需布防，每次调用都捕获且不解除。"""
        snapshot.set_continuous(True)
        try:
            assert not snapshot.armed
            assert await snapshot.try_capture(_messages(), [], "fake")
            assert await snapshot.try_capture(_messages("召回B"), [], "fake")
            data = snapshot.get()
            assert data is not None
            # 第二次捕获有 diff 基线
            by_layer = {s["layer"]: s for s in data["sections"]}
            assert by_layer["memory"]["changed"] is True
            assert by_layer["stable"]["changed"] is False
        finally:
            snapshot.set_continuous(False)

    async def test_continuous_appends_records(self, snapshot: ContextSnapshot) -> None:
        """连续模式追加紧凑记录到 records.jsonl（外部调试数据流）。"""
        snapshot.set_continuous(True)
        try:
            await snapshot.try_capture(_messages(), [], "fake")
            await snapshot.try_capture(_messages(), [], "fake")
            records = snapshot.list_records()
            assert len(records) == 2
            rec = records[-1]
            assert rec["model"] == "fake"
            assert rec["file"].startswith("snapshot_")
            assert rec["sections"][0]["layer"] == "stable"
            assert "cache" in rec
            # 记录不含消息正文（紧凑）
            assert "content" not in json.dumps(rec["sections"])
        finally:
            snapshot.set_continuous(False)

    async def test_disabled_zero_capture(self, snapshot: ContextSnapshot) -> None:
        """未布防且未开连续：不捕获（零开销路径）。"""
        assert not await snapshot.try_capture(_messages(), [], "fake")
        assert snapshot.get() is None
