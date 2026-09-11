"""召回格式权威与投影文档解析单元测试。"""

from __future__ import annotations

import pytest

from agent.memory.cognee.fusion import parse_memory_projection
from agent.memory.recall_format import format_memory_line, format_memory_time

# ==================================================================
# 投影文档解析（fusion 边界）
# ==================================================================

def test_parse_memory_projection_strips_header_and_backfills_tags() -> None:
    doc = (
        "Memory type: semantic\n"
        "Source: entity_123\n"
        "Importance: 0.6\n"
        "Tags: user:qq:123, topic:火锅\n"
        "Metadata: {}\n\n"
        "阿辰喜欢火锅，尤其是麻辣锅"
    )
    content, tags = parse_memory_projection(doc)
    assert content == "阿辰喜欢火锅，尤其是麻辣锅"
    assert tags == ["user:qq:123", "topic:火锅"]


def test_parse_memory_projection_passthrough_non_projection() -> None:
    text = "图谱综合回答：阿辰和老王是朋友"
    content, tags = parse_memory_projection(text)
    assert content == text
    assert tags == []


def test_parse_memory_projection_malformed_header_falls_back() -> None:
    text = "Memory type: 只有头无空行分隔的残缺文档"
    content, tags = parse_memory_projection(text)
    # 无 "\n\n" 分隔时整文回退为正文，不丢内容
    assert "残缺文档" in content
    assert tags == []


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
    assert "阿辰[uid:123]" in line and "美食" in line
    assert line.endswith("）") and "记" in line


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
