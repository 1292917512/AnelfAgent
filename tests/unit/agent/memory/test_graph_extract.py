"""graph 关系抽取单元测试：LLM 输出解析（纯函数容错）与 prompt 组装。"""

from __future__ import annotations

import pytest

from agent.memory.graph.extract import (
    build_extract_prompt,
    parse_relation_candidates,
    render_material,
)
from agent.memory.memory_store import MemoryStore


def test_parse_candidates_happy_path() -> None:
    raw = """
    好的，我来分析：
    [
      {"subject": "user:qq:123", "predicate": "朋友", "object": "person:小明",
       "symmetric": true, "strength": 0.9, "evidence": "他说周末和小明打球"},
      {"subject": "user:qq:123", "predicate": "喜欢", "object": "topic:火锅",
       "strength": 0.8, "evidence": "多次提到每周必吃火锅", "object_label": "火锅"}
    ]
    """
    items = parse_relation_candidates(raw)
    assert len(items) == 2
    assert items[0]["symmetric"] is True
    assert items[1]["strength"] == 0.8


def test_parse_candidates_drops_invalid() -> None:
    raw = """[
      {"subject": "user:qq:1", "predicate": "朋友", "object": "user:qq:2", "evidence": "有证据"},
      {"subject": "user:qq:1", "predicate": "同事", "object": "user:qq:3"},
      {"subject": "非法key", "predicate": "认识", "object": "user:qq:4", "evidence": "x"},
      "垃圾项",
      {"subject": "user:qq:1", "predicate": "", "object": "user:qq:5", "evidence": "x"}
    ]"""
    items = parse_relation_candidates(raw)
    assert len(items) == 1
    assert items[0]["predicate"] == "朋友"


def test_parse_candidates_garbage_returns_empty() -> None:
    assert parse_relation_candidates("") == []
    assert parse_relation_candidates("没有发现任何关系") == []
    assert parse_relation_candidates("[{broken json") == []
    assert parse_relation_candidates('{"not": "array"}') == []


def test_parse_candidates_clamps_strength() -> None:
    raw = '[{"subject": "user:qq:1", "predicate": "喜欢", "object": "topic:酒", "strength": 5.0, "evidence": "x"}]'
    assert parse_relation_candidates(raw)[0]["strength"] == 1.0


def test_build_prompt_escapes_and_fills() -> None:
    prompt = build_extract_prompt("阿辰 (user)", "user:qq:123", "qq", "对话材料")
    assert "user:qq:123" in prompt
    assert '"subject"' in prompt  # JSON 示例的花括号转义正确
    assert "对话材料" in prompt


def test_render_material_keeps_uid_tags() -> None:
    conv = [
        {"role": "user", "content": "[uid:123] 我和小明是同事"},
        {"role": "assistant", "content": "好的"},
        {"role": "user", "content": "   "},
    ]
    material = render_material(conv)
    assert "[uid:123]" in material
    assert material.count("\n") == 1


@pytest.mark.asyncio
async def test_extract_and_store_end_to_end(tmp_path) -> None:
    """mock mind.reflect 返回候选 JSON，验证落库到 GraphStore。"""
    from agent.memory.graph.extract import extract_and_store_relations

    store = MemoryStore(str(tmp_path / "memory.sqlite3"))

    class _FakeEntity:
        adapter_key = "qq"

        @property
        def identity_parts(self):
            return ("user", "qq:123")

        def get_entity_desc(self):
            return "阿辰 (user)"

    class _FakeMind:
        memory_store = store

        async def reflect(self, messages, options=None):
            return (
                '[{"subject": "user:qq:123", "predicate": "朋友", "object": "person:小明",'
                ' "symmetric": true, "strength": 0.9, "evidence": "周末一起打球"}]'
            )

    try:
        added = await extract_and_store_relations(
            _FakeMind(), _FakeEntity(),
            [{"role": "user", "content": "[uid:123] 周末和小明打球，他是我好朋友，我们认识很多年了，经常一起吃饭聊天"}],
        )
        assert added == 1
        result = await store.graph.query_relations("user:qq:123")
        assert result["found"] is True
        assert result["edges"][0]["origin"] == "heartbeat_extract"
        assert result["edges"][0]["symmetric"] is True
    finally:
        await store.close()
