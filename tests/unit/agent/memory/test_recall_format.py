"""召回格式权威与投影文档解析单元测试。"""

from __future__ import annotations

import pytest

from agent.memory.cognee.fusion import recall_projection
from agent.memory.cognee.types import CogneeRecallItem
from agent.memory.recall_format import format_memory_line, format_memory_time

# ==================================================================
# 投影文档解析（fusion 边界）
# ==================================================================

def test_recall_projection_preserves_text_and_restores_tags() -> None:
    parsed = recall_projection(CogneeRecallItem(id="chunk:1", content="阿辰喜欢火锅，尤其是麻辣锅",
        metadata={"anelf_attribution": {"tags": ["user:qq:123", "topic:火锅"]}}))
    assert parsed["snippet"] == "阿辰喜欢火锅，尤其是麻辣锅"
    assert parsed["tags"] == ["user:qq:123", "topic:火锅"]


def test_unknown_projection_keeps_text_and_conservative_privacy() -> None:
    text = "图谱综合回答：阿辰和老王是朋友"
    assert recall_projection(CogneeRecallItem(id="graph:1", content=text)) == {"snippet": text, "sensitivity": "private"}


async def test_extension_tags_remain_visible_and_separate_evidence_identity():
    from agent.memory.memory_types import MemorySearchResult
    from agent.memory.recall_format import format_recall_line, format_recall_result

    first = MemorySearchResult(id="cognee:1", snippet="相同正文", score=1,
                               source="cognee_chunk", tags=["project:a", "future:category"])
    second = first.model_copy(update={"id": "cognee:2", "tags": ["project:b"]})
    assert first.content_key() != second.content_key()
    line = await format_recall_line(None, first)
    assert "project:a" in line and "future:category" in line
    result = format_recall_result(first)
    assert "memory_id" not in result and result["evidence_kind"] == "source_chunk"


def test_content_that_looks_like_a_header_is_not_reinterpreted() -> None:
    text = "Memory type: semantic\nTags: unrelated\n\n这是用户原文"
    assert recall_projection(CogneeRecallItem(id="chunk:1", content=text))["snippet"] == text


# ==================================================================
# 行格式权威（recall_format）
# ==================================================================

@pytest.mark.asyncio
async def test_format_memory_line_attribution_and_time(store) -> None:
    await store.graph.upsert_node("user:qq:123", label="阿辰")
    line = await format_memory_line(
        store.graph,
        snippet="喜欢火锅",
        tags=["user:qq:123", "topic:美食"],
        timestamp=1_700_000_000.0,
    )
    assert line.startswith("💡 ")
    assert "[channel:qq][name:阿辰][uid:123]" in line and "美食" in line
    assert "记" in line and line.index("记") < line.index("喜欢火锅")


@pytest.mark.asyncio
async def test_format_memory_line_private_marker(store) -> None:
    line = await format_memory_line(
        store.graph, snippet="私事内容", sensitivity="private",
    )
    assert "私事" in line


@pytest.mark.asyncio
async def test_format_memory_line_none_graph_degrades(store) -> None:
    line = await format_memory_line(None, snippet="内容", tags=["user:qq:1"])
    # 图谱不可达时归属标注退化为裸 ID（不抛错、不丢行）
    assert "[uid:1]" in line and "内容" in line


def test_format_memory_time_date_granularity_only() -> None:
    import time as _time
    assert format_memory_time(0) == ""
    out = format_memory_time(_time.time() - 3600)  # 一小时前：年内 → 月-日
    assert len(out) == 5 and "-" in out  # 秒级粒度禁止出现在注入时间


@pytest.mark.asyncio
async def test_attribution_keeps_channel_and_full_id_with_stable_order() -> None:
    from agent.memory.recall_format import humanize_entity_tags

    tags = ["user:telegram:123", "user:qq:123", "group:webui:a:b", "topic:工作", "user:qq:123"]
    result = await humanize_entity_tags(None, tags)
    assert result == await humanize_entity_tags(None, list(reversed(tags)))
    assert "[channel:telegram][uid:123]" in result
    assert result.count("[channel:qq][uid:123]") == 1
    assert "[channel:webui][group_id:a:b]" in result


@pytest.mark.parametrize("unavailable", [False, True])
async def test_batch_attribution_looks_up_each_identity_once(store, monkeypatch, unavailable):
    from unittest.mock import AsyncMock

    from agent.memory.memory_retriever import MemoryRetriever
    from agent.memory.memory_types import MemorySearchResult
    from agent.memory.probe import DeepProbeHub

    nodes = {"user:qq:1": {"label": "甲"}, "user:webui:1": {"label": "乙"}}
    lookup = AsyncMock(return_value=nodes, side_effect=RuntimeError("unavailable") if unavailable else None)
    monkeypatch.setattr(store.graph, "get_nodes_by_keys", lookup)
    results = [MemorySearchResult(
        id=f"mem:{i}", snippet=f"事实{i}", score=.9,
        tags=["user:qq:1", "user:webui:1", "topic:项目"], sensitivity="private",
    ) for i in range(5)]
    expected = [await format_memory_line(store.graph, snippet=r.snippet, tags=r.tags,
                                        sensitivity=r.sensitivity, reference=r.id) for r in results]
    lookup.reset_mock()
    messages = await MemoryRetriever(store, None)._format_unified_results(results)
    lookup.assert_awaited_once_with(["user:qq:1", "user:webui:1"])
    assert "\n".join(expected) in messages[0]["content"]
    lookup.reset_mock()
    deltas = await DeepProbeHub._memory_deltas(iter(results), store, "补充记忆")
    lookup.assert_awaited_once_with(["user:qq:1", "user:webui:1"])
    assert [delta.text for delta in deltas] == expected


async def test_projection_roundtrip_preserves_attribution_and_privacy(store) -> None:
    from agent.memory.cognee.documents import memory_document
    from agent.memory.memory_retriever import MemoryRetriever
    from agent.memory.memory_types import MemoryEntry, MemorySearchResult, MemoryType
    from agent.memory.probe import DeepProbeHub
    from agent.memory.provenance import capture_origin
    from agent.memory.recall_format import format_recall_result
    from agent.memory.store._shared import entry_projection_payload

    metadata = capture_origin("group_qq:789", [{"id": 41, "ts_ns": 1_700_000_000_000_000_000}])
    metadata.update(sensitivity="private", activity_date="2023-11-13", temporal_scope="state")
    entry = MemoryEntry(id=7, memory_type=MemoryType.EPISODIC, content="暂住朋友家", source="auto_capture",
                        tags=["group:qq:789", "user:qq:123"], metadata=metadata, timestamp=1_700_000_000)
    native = MemorySearchResult.from_entry(entry, .8)
    document = memory_document(entry_projection_payload(entry, 7))
    assert document.text == entry.content
    projected = MemorySearchResult.model_validate({
        **recall_projection(CogneeRecallItem(id="chunk:1", content=document.text,
            metadata={"anelf_attribution": document.attribution})),
        "id": "chunk:1", "source": "cognee_chunk", "score": .7,
    })
    assert projected.memory_reference() == "mem:7"
    assert projected.content_key() == native.content_key()
    assert projected.sensitivity == "private"
    assert projected.timestamp == entry.timestamp
    for result in (native, projected):
        text = (await MemoryRetriever(store, None)._format_unified_results([result]))[0]["content"]
        assert "mem:7" in text and "[channel:qq][uid:123]" in text
        assert "材料[scope:group_qq:789]" in text
        assert "发生于 2023-11-13" in text and "过去时·可能已变" in text and "私事" in text
        assert text.index("私事") < text.index("暂住朋友家")
        delta = (await DeepProbeHub._memory_deltas([result], store, "记忆"))[0]
        assert delta.text in text
        tool = format_recall_result(result)
        assert tool["provenance"]["origins"][0]["message_ids"] == [41]


async def test_unknown_graph_never_borrows_query_identity() -> None:
    text = await format_memory_line(None, snippet="可能有关联", source="cognee_graph")
    assert "图谱综合·非原话" in text and "出处未记录" in text
    assert "[scope:" not in text and "[uid:" not in text


@pytest.mark.parametrize("metadata", ["broken", [], {"sensitivity": [], "origins": [None]}])
def test_invalid_projection_metadata_does_not_discard_content(metadata) -> None:
    parsed = recall_projection(CogneeRecallItem(id="chunk:1", content="事实", metadata={"anelf_attribution": metadata}))
    assert parsed["snippet"] == "事实"
    assert not parsed.get("timestamp") and parsed["sensitivity"] == "private"


async def test_origin_subject_and_file_location_are_distinct() -> None:
    from agent.memory.memory_types import MemorySearchResult
    from agent.memory.provenance import note_origin
    from agent.memory.recall_format import format_recall_result

    text = await format_memory_line(None, snippet="乙喜欢安静", tags=["user:telegram:2"],
                                    provenance=note_origin("user_qq:1"), reference="mem:3")
    assert "[channel:telegram][uid:2]" in text and "AI记录于[scope:user_qq:1]" in text
    file = MemorySearchResult(id="chunk:2", source="file", snippet="片段", score=.8,
                              path="notes/project.md", start_line=12, end_line=16)
    output = format_recall_result(file)
    assert (output["path"], output["start_line"], output["end_line"]) == ("notes/project.md", 12, 16)


def test_bounded_origin_display_does_not_mutate_full_lineage() -> None:
    from agent.memory.provenance import origins_from, visible_provenance

    provenance = {"origins": [{"kind": "agent_note", "scope": f"user_qq:{i}"} for i in range(5)]}
    visible = visible_provenance(provenance)
    assert visible["origin_count"] == 5 and len(visible["origins"]) == 3
    assert len(origins_from(provenance)) == 5
    assert origins_from({"origins": [{"kind": "agent_note", "scope": "user_qq:1", "started_at": 1e100}]}) == []


@pytest.mark.parametrize("scope", ["reflect:de8b4383", "reflect:", "reflect:invalid", "invalid"])
def test_material_origin_rejects_internal_and_invalid_scopes(scope: str) -> None:
    from pydantic import ValidationError

    from agent.memory.provenance import capture_origin, note_origin

    with pytest.raises(ValidationError, match="材料会话无效"):
        capture_origin(scope, [{"id": 41}])
    if scope != "reflect:de8b4383":
        with pytest.raises(ValidationError):
            note_origin(scope)


async def test_reflection_origin_is_agent_record_not_conversation_material() -> None:
    from agent.memory.provenance import note_origin

    text = await format_memory_line(None, snippet="整理结果", tags=["user:telegram:2"],
                                    provenance=note_origin("reflect:de8b4383"), reference="mem:3")
    assert "AI记录于[scope:reflect:de8b4383]" in text
    assert "材料[scope:" not in text and "出处未记录" not in text
