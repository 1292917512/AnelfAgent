"""标签写入闸门（tag_gate）测试：前缀 lane / topic 四层闸 / 咨询模式 / fail-open / 词表。"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import agent.memory.auto_capture as ac
import agent.memory.tag_gate as tag_gate
from agent.memory import tools as mem_tools
from agent.memory.capture_state import CaptureItem
from agent.memory.memory_types import MemoryEntry, MemoryType
from agent.memory.tag_gate import apply_tag_gate, enforce_tag_gate, topic_vocab_line


async def _seed(store, content: str, tags: list[str], times: int = 1) -> None:
    for i in range(times):
        await store.add(
            MemoryEntry(
                memory_type=MemoryType.SEMANTIC,
                content=f"{content}{i}",
                importance=0.6,
                timestamp=time.time(),
                tags=tags,
            )
        )


class TestPrefixLanes:
    @pytest.mark.parametrize("tag", ["topic:", "topic:   ", "user::123", "group:qq:#chat", "date:20260101"])
    def test_new_tags_require_unambiguous_structure(self, tag: str) -> None:
        assert apply_tag_gate([tag], {}).tags == []

    async def test_invalid_type_dropped(self, store) -> None:
        result = apply_tag_gate(["type:fact", "type:乱造"], await store.list_tags())
        assert result.tags == ["type:fact"]
        assert result.dropped == [{"tag": "type:乱造", "reason": "未知类型"}]

    async def test_unknown_prefix_dropped(self, store) -> None:
        result = apply_tag_gate(["foo:bar", "topic:火锅"], await store.list_tags())
        assert result.tags == ["topic:火锅"]
        assert result.dropped[0]["reason"] == "未知前缀"

    async def test_bad_date_and_empty_value_dropped(self, store) -> None:
        result = apply_tag_gate(["date:2026-13-99", "user:"], await store.list_tags())
        assert result.tags == []
        assert {d["reason"] for d in result.dropped} == {"日期格式非法", "空前缀值"}

    async def test_hub_and_entity_tags_pass(self, store) -> None:
        tags = ["main:hub", "user:qq:123", "group:qq:789", "goal:abc"]
        assert apply_tag_gate(tags, await store.list_tags()).tags == tags


class TestTopicTiers:
    async def test_tier1_rewrites_to_established_form(self, store) -> None:
        await _seed(store, "电竞", ["topic:LOL"], times=2)
        result = apply_tag_gate(["topic:lol", "type:fact"], await store.list_tags())
        assert result.tags == ["topic:LOL", "type:fact"]
        assert result.rewrites == [{"from": "topic:lol", "to": "topic:LOL", "rule": "写法变体"}]

    async def test_established_form_never_touched(self, store) -> None:
        # 双侧立住的分裂对不改写（语义归并归议程）
        await _seed(store, "a", ["topic:RimTalk"], times=2)
        await _seed(store, "b", ["topic:rimtalk"], times=2)
        result = apply_tag_gate(["topic:RimTalk"], await store.list_tags())
        assert result.tags == ["topic:RimTalk"]
        assert result.rewrites == []

    async def test_tier1_ambiguous_targets_downgrade_to_hint(self, store) -> None:
        # 同归一形态存在多个既有形态时（分裂未收敛），不改写、降级为人工确认提示
        await _seed(store, "a", ["topic:Rimtalk"], times=2)
        await _seed(store, "b", ["topic:RIMTALK"], times=2)
        result = apply_tag_gate(["topic:ｒｉｍｔａｌｋ"], await store.list_tags())
        assert result.tags == ["topic:ｒｉｍｔａｌｋ"]
        assert any("同形" in h for h in result.hints)

    async def test_new_batch_variants_do_not_invent_a_canonical_tag(self, store) -> None:
        result = apply_tag_gate(["topic:火锅", "topic:火锅 "], await store.list_tags())
        assert result.tags == ["topic:火锅", "topic:火锅 "]
        assert result.rewrites == []

    async def test_tier0_prefers_established_representative(self, store) -> None:
        await _seed(store, "电竞", ["topic:LOL"], times=2)
        result = apply_tag_gate(["topic:lol ", "topic:LOL"], await store.list_tags())
        assert result.tags == ["topic:LOL"]

    async def test_tier2_generic_core_dropped(self, store) -> None:
        result = apply_tag_gate(["topic:方法"], await store.list_tags())
        assert result.tags == []
        assert result.dropped == [{"tag": "topic:方法", "reason": "泛义词"}]

    async def test_tier2_mod_only_name_dropped(self, store) -> None:
        result = apply_tag_gate(["topic:很多"], await store.list_tags())
        assert result.tags == []
        assert result.dropped[0]["reason"] == "泛义词"

    @pytest.mark.parametrize("name,target", [("小游戏", "游戏"), ("小米", "米"), ("大学", "学"), ("大数据", "数据")])
    def test_topic_meaning_is_not_inferred_from_affixes(self, name: str, target: str) -> None:
        result = apply_tag_gate([f"topic:{name}"], {f"topic:{target}": 2})
        assert result.tags == [f"topic:{name}"]
        assert not result.rewrites and not result.dropped

    def test_all_existing_forms_survive_in_the_same_batch(self) -> None:
        tags = ["topic:LOL", "topic:lol", "topic:方法", "custom:legacy"]
        result = apply_tag_gate(tags, dict(zip(tags, [2, 1, 1, 1], strict=True)))
        assert result.tags == tags
        assert not result.rewrites and not result.dropped

    def test_write_normalization_preserves_word_boundaries(self) -> None:
        result = apply_tag_gate(["topic:the rapist"], {"topic:therapist": 2})
        assert result.tags == ["topic:the rapist"]
        assert not result.rewrites
        result = apply_tag_gate(["topic:NEW  YORK"], {"topic:New York": 2})
        assert result.tags == ["topic:New York"]

    async def test_tier2_strip_without_target_keeps_original(self, store) -> None:
        # 剥除残核未立住时保留原名（防「大学」被剥成「学」）
        result = apply_tag_gate(["topic:大学"], await store.list_tags())
        assert result.tags == ["topic:大学"]
        assert result.dropped == []

    async def test_whitelist_rescues_generic_drop(self, store, monkeypatch) -> None:
        monkeypatch.setattr(
            tag_gate,
            "get_config",
            lambda key, default=None: "topic:方法" if key == "memory_tag_gate_whitelist" else default,
        )
        result = apply_tag_gate(["topic:方法"], await store.list_tags())
        assert result.tags == ["topic:方法"]

    async def test_tier3_containment_only_hints(self, store) -> None:
        await _seed(store, "卡牌", ["topic:卡牌游戏"], times=3)
        result = apply_tag_gate(["topic:卡牌"], await store.list_tags())
        assert result.tags == ["topic:卡牌"]
        assert any("topic:卡牌游戏" in h for h in result.hints)
        assert result.rewrites == []


class TestModesAndDegradation:
    async def test_cancellation_is_not_swallowed(self, store, monkeypatch) -> None:
        async def cancelled(**kwargs):
            raise asyncio.CancelledError

        monkeypatch.setattr(store, "list_tags", cancelled)
        with pytest.raises(asyncio.CancelledError):
            await enforce_tag_gate(["topic:方法"], store=store)

    def test_custom_generic_words_use_documented_full_tags(self, monkeypatch) -> None:
        monkeypatch.setattr(
            tag_gate, "get_config",
            lambda key, default=None: "topic:无区分度" if key == "memory_tag_gate_blacklist_extra" else default,
        )
        assert apply_tag_gate(["topic:无区分度"], {}).tags == []
        assert apply_tag_gate(["topic:无区分度"], {"topic:无区分度": 1}).tags == ["topic:无区分度"]

    async def test_advisory_keeps_original(self, store) -> None:
        await _seed(store, "电竞", ["topic:LOL"], times=2)
        result = apply_tag_gate(["topic:lol"], await store.list_tags(), enforce=False)
        assert result.tags == ["topic:lol"]
        assert result.rewrites == []
        assert any("写法变体" in h for h in result.hints)

    async def test_gate_disabled_passthrough(self, store, monkeypatch) -> None:
        monkeypatch.setattr(tag_gate, "get_config_bool", lambda key, default: False)
        result = await enforce_tag_gate(["topic:方法"], store=store)
        assert result.tags == ["topic:方法"]

    async def test_failopen_on_df_error(self, store, monkeypatch) -> None:
        async def boom(**kwargs):
            raise RuntimeError("db down")

        monkeypatch.setattr(store, "list_tags", boom)
        result = await enforce_tag_gate(["topic:方法"], store=store)
        assert result.tags == ["topic:方法"]
        assert result.dropped == []

    async def test_failopen_includes_rule_evaluation(self, store, monkeypatch) -> None:
        def boom(*args, **kwargs):
            raise RuntimeError("rule failure")

        monkeypatch.setattr(tag_gate, "apply_tag_gate", boom)
        assert (await enforce_tag_gate(["topic:方法"], store=store)).tags == ["topic:方法"]

    async def test_advice_does_not_report_drops_or_increment_write_metrics(self, store, monkeypatch) -> None:
        from agent.memory import metrics

        monkeypatch.setattr(metrics, "_counters", {})
        result = await enforce_tag_gate(["topic:方法"], store=store, enforce=False)
        assert result.tags == ["topic:方法"]
        assert result.hints and not result.dropped and not result.rewrites
        assert metrics.snapshot().get("write.tag_gate_drop", 0) == 0

    async def test_gate_sees_committed_tags_despite_warm_cache(self, store) -> None:
        assert await store.list_tags() == {}
        await _seed(store, "人工保留的主题", ["topic:方法"])
        result = await enforce_tag_gate(["topic:方法"], store=store)
        assert result.tags == ["topic:方法"]

    async def test_empty_tags_passthrough(self, store) -> None:
        assert (await enforce_tag_gate([], store=store)).tags == []


class TestVocabLine:
    async def test_vocab_line_ranks_by_df_and_filters(self, store) -> None:
        await _seed(store, "a", ["topic:火锅"], times=3)
        await _seed(store, "b", ["topic:烧烤"], times=2)
        await _seed(store, "c", ["topic:孤独"], times=1)
        line = topic_vocab_line(await store.list_tags())
        assert line.index("火锅") < line.index("烧烤")
        assert "孤独" not in line

    async def test_vocab_line_empty_without_established(self, store) -> None:
        await _seed(store, "c", ["topic:孤独"], times=1)
        assert topic_vocab_line(await store.list_tags()) == ""


class TestMemorizeIntegration:
    @pytest.mark.parametrize("tag", ["user:123", "group:qq:", "user::123"])
    async def test_invalid_explicit_identity_is_not_reassigned_to_current_user(self, store, tag: str) -> None:
        from agent.messages import build_entity_scope
        from core.conversation_scope import bind_scope, reset_scope

        token = bind_scope(build_entity_scope("user", "qq", "456"))
        mem_tools.memory_tools_port.set(mem_tools.MemoryToolDeps(store, None))
        try:
            result = json.loads(await mem_tools.memorize("另一人的偏好", tags=tag))
            assert result.get("error")
            assert await store.count() == 0
        finally:
            mem_tools.memory_tools_port.unbind()
            reset_scope(token)

    async def test_memorize_gate_rewrites_and_reports(self, store) -> None:
        mem_tools.memory_tools_port.set(mem_tools.MemoryToolDeps(store, None))
        try:
            await _seed(store, "电竞相关", ["topic:LOL"], times=2)
            raw = await mem_tools.memorize("打野出新装备了", tags="topic:lol, type:fact")
            data = json.loads(raw)
            assert data["ok"] is True
            assert data["tags"] == ["topic:LOL", "type:fact"]
            assert data["tag_gate"]["rewrites"] == [{"from": "topic:lol", "to": "topic:LOL", "rule": "写法变体"}]

            # 包含关系只提示不改写
            raw = await mem_tools.memorize("中单又秀了", tags="topic:LOL梗, type:fact")
            data = json.loads(raw)
            assert data["tags"] == ["topic:LOL梗", "type:fact"]
            assert any("topic:LOL" in h for h in data["tag_gate"]["hints"])

            # 复用既有标签（闸门无动作）不附摘要字段
            raw = await mem_tools.memorize("下路组合换了", tags="topic:LOL, type:fact")
            data = json.loads(raw)
            assert "tag_gate" not in data
        finally:
            mem_tools.memory_tools_port.unbind()

    async def test_update_memory_advisory_keeps_tags(self, store) -> None:
        mem_tools.memory_tools_port.set(mem_tools.MemoryToolDeps(store, None))
        try:
            await _seed(store, "电竞相关", ["topic:LOL"], times=2)
            mid = (await store.list_recent(limit=1))[0].id
            raw = await mem_tools.update_memory(mid, tags="topic:lol")
            data = json.loads(raw)
            assert data["ok"] is True
            assert data["tags"] == ["topic:lol"]  # 咨询模式不改写
            assert any("写法变体" in h for h in data.get("tag_gate", {}).get("hints", []))
        finally:
            mem_tools.memory_tools_port.unbind()


class TestAutoCaptureIntegration:
    async def test_store_item_applies_tag_gate(self, store, monkeypatch) -> None:
        """提取产出的 topic 变体经写入闸门归一到既有形态。"""
        await _seed(store, "游戏事实", ["group:qq:789", "topic:游戏"], times=2)

        async def fake_gather(store, embedder, content):
            return []

        async def fake_judge(content, candidates, *, tags=None):
            return {"action": "store"}

        monkeypatch.setattr(ac, "gather_dedup_candidates", fake_gather)
        monkeypatch.setattr(ac, "judge_write", fake_judge)
        pipeline = ac.AutoCapturePipeline(SimpleNamespace(memory_store=store, embedder=None), AsyncMock())
        item = CaptureItem(content="群里聊了新出的桌游", type="fact", topic="游戏　", importance=0.7)
        await pipeline._store_item("group:qq:789", [], item, "a" * 64)

        entry = (await store.list_recent(limit=1))[0]
        assert "topic:游戏" in entry.tags
        assert "topic:游戏　" not in entry.tags

    async def test_extract_prompt_includes_vocab(self, store, monkeypatch) -> None:
        """词表注入启用时，提取 prompt 携带高 df 主题词表。"""
        await _seed(store, "游戏事实", ["topic:游戏"], times=2)
        captured: dict = {}

        async def fake_llm(prompt: str, **kwargs):
            captured["prompt"] = prompt
            return "[]"

        monkeypatch.setattr(ac, "light_llm", fake_llm)
        pipeline = ac.AutoCapturePipeline(SimpleNamespace(memory_store=store, embedder=None), AsyncMock())
        message = {
            "id": 1,
            "role": "user",
            "ts_ns": int(time.time() * 1e9),
            "content": "[uid:1][name:小明] 周末开黑去不去",
        }
        await pipeline._extract([message], [])
        assert "既有主题标签词表" in captured["prompt"]
        assert "游戏" in captured["prompt"]

    async def test_extract_prompt_vocab_disabled(self, store, monkeypatch) -> None:
        await _seed(store, "游戏事实", ["topic:游戏"], times=2)
        captured: dict = {}

        async def fake_llm(prompt: str, **kwargs):
            captured["prompt"] = prompt
            return "[]"

        monkeypatch.setattr(ac, "light_llm", fake_llm)
        monkeypatch.setattr(ac, "get_config_bool", lambda key, default=True: False)
        pipeline = ac.AutoCapturePipeline(SimpleNamespace(memory_store=store, embedder=None), AsyncMock())
        message = {"id": 1, "role": "user", "ts_ns": int(time.time() * 1e9), "content": "闲聊"}
        await pipeline._extract([message], [])
        assert "既有主题标签词表" not in captured["prompt"]
