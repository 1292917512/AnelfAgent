"""图谱治理议程单元测试：确定性事实检测（弱边/歧义/疑似重复/枢纽）。"""

from __future__ import annotations

import pytest

from agent.memory.graph.curation import agenda_summary, build_agenda


@pytest.mark.asyncio
async def test_agenda_detects_weak_and_ambiguous(store) -> None:
    await store.graph.add_relation(
        "user:qq:1", "认识", "user:qq:2", strength=0.3, origin="heartbeat_extract",
    )
    # 同主语同谓词多对象（均高强度）→ 歧义组
    await store.graph.add_relation(
        "user:qq:1", "住在", "topic:北京", strength=0.8, evidence="明确说过",
    )
    await store.graph.add_relation(
        "user:qq:1", "住在", "topic:上海", strength=0.8, evidence="另一次说过",
    )

    agenda = await build_agenda(store.graph)
    assert any(item["strength"] < 0.4 for item in agenda["weak_edges"])
    assert len(agenda["ambiguous_groups"]) == 1
    group = agenda["ambiguous_groups"][0]
    assert group["predicate"] == "住在"
    assert len(group["edges"]) == 2
    # 摘要行包含各类计数
    summary = agenda_summary(agenda)
    assert "弱边 1" in summary and "歧义 1" in summary


@pytest.mark.asyncio
async def test_agenda_detects_duplicate_nodes(store) -> None:
    """同类型同称呼的多个节点 → 疑似重复（实体解析漏网的别名）。"""
    await store.graph.upsert_node("person:老王_1", label="老王")
    await store.graph.upsert_node("person:老王_2", label="老王")
    await store.graph.upsert_node("person:小张", label="小张")  # 不同称呼不误报

    agenda = await build_agenda(store.graph)
    assert len(agenda["duplicate_nodes"]) == 1
    dup = agenda["duplicate_nodes"][0]
    assert sorted(dup["nodes"]) == ["person:老王_1", "person:老王_2"]


@pytest.mark.asyncio
async def test_agenda_hub_threshold(store, monkeypatch) -> None:
    """度数超阈的自由型节点进入枢纽异常（阈值经配置注入）。"""
    import agent.memory.graph.curation as curation

    monkeypatch.setattr(curation, "get_config_int", lambda key, default=None: 2)
    await store.graph.upsert_node("topic:万物", label="万物")
    for i in range(3):
        await store.graph.add_relation("topic:万物", "关联", f"topic:节点{i}", strength=0.6)

    agenda = await build_agenda(store.graph)
    assert len(agenda["hub_nodes"]) == 1
    assert agenda["hub_nodes"][0]["degree"] == 3


@pytest.mark.asyncio
async def test_empty_agenda_summary_is_blank(store) -> None:
    agenda = await build_agenda(store.graph)
    assert agenda_summary(agenda) == ""


@pytest.mark.asyncio
async def test_exemption_filters_agenda_item(store) -> None:
    """登记豁免后对应议程项被过滤，summary 横幅随之消失。"""
    await store.graph.add_relation(
        "user:qq:1", "住在", "topic:北京", strength=0.8, evidence="明确说过",
    )
    await store.graph.add_relation(
        "user:qq:1", "住在", "topic:上海", strength=0.8, evidence="另一次说过",
    )
    agenda = await build_agenda(store.graph)
    assert len(agenda["ambiguous_groups"]) == 1
    assert "歧义 1" in agenda_summary(agenda)

    group = agenda["ambiguous_groups"][0]
    signature = store.graph.exemption_signature(
        "ambiguous_group",
        {"subject_key": "user:qq:1", "predicate": group["predicate"]},
    )
    assert signature == "user:qq:1|住在"
    await store.graph.set_exemption(
        "ambiguous_group", signature, reason="真实扇出维持", actor="test",
    )

    agenda = await build_agenda(store.graph)
    assert agenda["ambiguous_groups"] == []
    assert "歧义" not in agenda_summary(agenda)
    # 豁免清单随议程返回供复核
    assert any(
        e["signature"] == signature and e["kind"] == "ambiguous_group"
        for e in agenda["exemptions"]
    )


@pytest.mark.asyncio
async def test_exemption_revoke_restores_agenda(store) -> None:
    await store.graph.add_relation(
        "user:qq:1", "住在", "topic:北京", strength=0.8, evidence="明确说过",
    )
    await store.graph.add_relation(
        "user:qq:1", "住在", "topic:上海", strength=0.8, evidence="另一次说过",
    )
    await store.graph.set_exemption("ambiguous_group", "user:qq:1|住在", actor="test")
    assert (await build_agenda(store.graph))["ambiguous_groups"] == []

    assert await store.graph.revoke_exemption("ambiguous_group", "user:qq:1|住在")
    agenda = await build_agenda(store.graph)
    assert len(agenda["ambiguous_groups"]) == 1
    # 撤销后重登记（复活）再次过滤
    await store.graph.set_exemption("ambiguous_group", "user:qq:1|住在", actor="test")
    assert (await build_agenda(store.graph))["ambiguous_groups"] == []


@pytest.mark.asyncio
async def test_exemption_hub_signature_and_kind_validation(store) -> None:
    await store.graph.upsert_node("person:梦璃", label="梦璃")
    for i in range(35):
        await store.graph.add_relation(
            "person:梦璃", f"关系{i}", f"topic:话题{i}", strength=0.7,
        )
    agenda = await build_agenda(store.graph)
    assert any(h["node"] == "person:梦璃" for h in agenda["hub_nodes"])

    await store.graph.set_exemption("hub_node", "person:梦璃", reason="设计使然中心节点", actor="test")
    agenda = await build_agenda(store.graph)
    assert not any(h["node"] == "person:梦璃" for h in agenda["hub_nodes"])

    with pytest.raises(ValueError):
        await store.graph.set_exemption("not_a_kind", "x")
    with pytest.raises(ValueError):
        await store.graph.set_exemption("hub_node", "")
