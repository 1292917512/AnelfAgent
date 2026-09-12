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
        """消息级断链：conversation 第 3 条被原地改写，前缀计入其前 2 条。"""
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
        # 断链点落在 conversation 层首条（该层唯一消息被原地改写）
        assert data["prefix_break"]["layer"] == "conversation"
        assert data["prefix_break"]["index"] == 0

    async def test_append_keeps_stable_prefix(
        self, snapshot: ContextSnapshot, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """追加式增长（工具链/会话常态）：既有消息全稳定，仅新增计入重写区。"""
        import agent.mind.cache_stats as cache_mod
        monkeypatch.setattr(cache_mod, "cache_usage_tracker", CacheUsageTracker())

        msgs = [
            {"role": "system", "content": "人设", "_layer": "stable"},
            {"role": "assistant", "content": "调用A", "tool_calls": [{"id": "c1", "function": {"name": "recall"}}], "_layer": "tool_chain"},
            {"role": "tool", "content": "结果A", "tool_call_id": "c1", "_layer": "tool_chain"},
        ]
        await snapshot.arm()
        await snapshot.try_capture(msgs, [], "fake")

        grown = msgs + [
            {"role": "assistant", "content": "调用B", "tool_calls": [{"id": "c2", "function": {"name": "recall"}}], "_layer": "tool_chain"},
            {"role": "tool", "content": "结果B", "tool_call_id": "c2", "_layer": "tool_chain"},
        ]
        await snapshot.arm()
        await snapshot.try_capture(grown, [], "fake")

        data = snapshot.get()
        assert data is not None
        by_layer = {s["layer"]: s for s in data["sections"]}
        # 工具链：前 2 条已缓存、新增 2 条
        assert by_layer["tool_chain"]["stable_count"] == 2
        assert by_layer["tool_chain"]["new_count"] == 2
        # 断链点在工具链第 2 条；此前 stable 层全部可命中
        assert data["prefix_break"]["layer"] == "tool_chain"
        assert data["prefix_break"]["index"] == 2
        assert data["prefix_break"]["before_tokens"] >= by_layer["stable"]["estimated_tokens"]

    async def test_shrunk_chain_breaks_at_layer_tail(
        self, snapshot: ContextSnapshot, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """链收缩（压缩删除尾部）：stable==count 但整层变化，断链点不得穿过。"""
        import agent.mind.cache_stats as cache_mod
        monkeypatch.setattr(cache_mod, "cache_usage_tracker", CacheUsageTracker())

        msgs = [
            {"role": "system", "content": "人设", "_layer": "stable"},
            {"role": "assistant", "content": "调用A", "tool_calls": [{"id": "c1", "function": {"name": "recall"}}], "_layer": "tool_chain"},
            {"role": "tool", "content": "结果A", "tool_call_id": "c1", "_layer": "tool_chain"},
            {"role": "tool", "content": "结果B", "tool_call_id": "c2", "_layer": "tool_chain"},
        ]
        await snapshot.arm()
        await snapshot.try_capture(msgs, [], "fake")

        shrunk = msgs[:3]  # 压缩删除最后一条
        await snapshot.arm()
        await snapshot.try_capture(shrunk, [], "fake")

        data = snapshot.get()
        assert data is not None
        by_layer = {s["layer"]: s for s in data["sections"]}
        chain = by_layer["tool_chain"]
        # 既有 2 条全匹配（stable==count），但整层 hash 变化（尾部被删）
        assert chain["stable_count"] == 2
        assert chain["changed"] is True
        # 断链点不得穿过收缩层：停在工具层层末
        assert data["prefix_break"]["layer"] == "tool_chain"
        assert data["prefix_break"]["index"] == 2
        # 前缀估算只计到收缩层为止（不把不存在的下游当命中）
        assert data["cache"]["estimated_cacheable_prefix_tokens"] == (
            by_layer["stable"]["estimated_tokens"] + chain["estimated_tokens"]
        )

    async def test_new_layer_breaks_at_its_head(
        self, snapshot: ContextSnapshot, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """新层首现：断链点在该层第 0 条，而非整份快照"无基线"。"""
        import agent.mind.cache_stats as cache_mod
        monkeypatch.setattr(cache_mod, "cache_usage_tracker", CacheUsageTracker())

        base = [
            {"role": "system", "content": "人设", "_layer": "stable"},
            {"role": "user", "content": "你好", "_layer": "conversation"},
        ]
        await snapshot.arm()
        await snapshot.try_capture(base, [], "fake")

        # memory 层首现（召回首次产生）
        grown = base + [{"role": "system", "content": "召回", "_layer": "memory"}]
        await snapshot.arm()
        await snapshot.try_capture(grown, [], "fake")

        data = snapshot.get()
        assert data is not None
        assert data["prefix_break"]["layer"] == "memory"
        assert data["prefix_break"]["index"] == 0
        by_layer = {s["layer"]: s for s in data["sections"]}
        assert data["prefix_break"]["before_tokens"] >= (
            by_layer["stable"]["estimated_tokens"] + by_layer["conversation"]["estimated_tokens"]
        )


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


class TestPrefixStableCaliber:
    """prefix_stable 判定口径：每轮必变层（tool_chain/provider/exec_context）
    的变更不计入前缀稳定性（否则时间类注入会被误报为前缀断裂）。"""

    def _write_snapshot(self, tmp_path, sections: list) -> None:
        (tmp_path / "snapshot_20260909_000000.json").write_text(
            json.dumps({
                "captured_at": 1.0, "model": "m", "kind": "reply",
                "sections": sections, "cache": {},
            }, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_provider_change_not_counted(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "agent.mind.context_snapshot._SNAPSHOT_DIR", str(tmp_path),
        )
        self._write_snapshot(tmp_path, [
            {"layer": "stable", "changed": False},
            {"layer": "conversation", "changed": False},
            {"layer": "provider", "changed": True},
            {"layer": "tool_chain", "changed": True},
            {"layer": "exec_context", "changed": True},
        ])
        items = ContextSnapshot.list_snapshots()
        assert items[0]["prefix_stable"] is True

    def test_memory_layer_change_marks_drift(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "agent.mind.context_snapshot._SNAPSHOT_DIR", str(tmp_path),
        )
        self._write_snapshot(tmp_path, [
            {"layer": "stable", "changed": False},
            {"layer": "memory", "changed": True},
            {"layer": "provider", "changed": True},
        ])
        items = ContextSnapshot.list_snapshots()
        assert items[0]["prefix_stable"] is False
