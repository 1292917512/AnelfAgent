"""标签治理豁免测试：CRUD / 签名口径 / 候选过滤 / 豁免工具。"""

from __future__ import annotations

import json
import time

import pytest

from agent.memory import tools as mem_tools
from agent.memory.memory_store import MemoryStore
from agent.memory.memory_types import MemoryEntry, MemoryType
from agent.memory.tag_gate import TagGateResult


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


class TestExemptionStore:
    async def test_existing_form_signatures_keep_their_normalization(self, store) -> None:
        await _seed(store, "写法", ["topic:RimTalk", "topic:Rim Talk"], times=2)
        candidate = (await store.tag_merge_candidates())[0]
        assert candidate["exempt_signature"] == "topic:rimtalk"
        await store.set_tag_exemption("form_variant", "topic:rimtalk")
        assert await store.tag_merge_candidates() == []

    async def test_exemptions_do_not_hide_lower_ranked_candidates(self, store) -> None:
        await _seed(store, "高频", ["topic:AAA", "topic:aaa"], times=3)
        await _seed(store, "低频", ["topic:BBB", "topic:bbb"], times=2)
        first, second = await store.tag_merge_candidates(limit=2)
        await store.set_tag_exemption(first["kind"], first["exempt_signature"])
        assert await store.tag_merge_candidates(limit=1) == [second]
        assert await store.tag_merge_candidates(limit=0) == []

    async def test_rules_beyond_display_page_remain_active(self, store) -> None:
        await _seed(store, "记忆", ["topic:AAA", "topic:aaa"], times=2)
        candidate = (await store.tag_merge_candidates())[0]
        await store.set_tag_exemption(candidate["kind"], candidate["exempt_signature"])
        for i in range(200):
            await store.set_tag_exemption("form_variant", f"topic:other{i}")
        assert await store.tag_merge_candidates() == []
        await store.revoke_tag_exemption(candidate["kind"], candidate["exempt_signature"])
        assert await store.tag_merge_candidates() == [candidate]

    async def test_candidate_kinds_and_signatures(self, store) -> None:
        await _seed(store, "火锅", ["topic:火锅"], times=2)
        await _seed(store, "孪生", ["topic:火锅 "], times=1)
        await _seed(store, "蘸料", ["topic:火锅蘸料"], times=2)

        candidates = await store.tag_merge_candidates()
        form = next(c for c in candidates if c["kind"] == "form_variant")
        containment = next(c for c in candidates if c["kind"] == "containment")
        assert form["exempt_signature"] == "topic:火锅"
        assert containment["exempt_signature"] == "topic:火锅|topic:火锅蘸料"

    async def test_exemption_filters_candidates(self, store) -> None:
        await _seed(store, "火锅", ["topic:火锅"], times=2)
        await _seed(store, "蘸料", ["topic:火锅蘸料"], times=2)
        containment = next(c for c in await store.tag_merge_candidates() if c["kind"] == "containment")

        await store.set_tag_exemption("containment", containment["exempt_signature"], reason="判例：蘸料是独立话题")

        remaining = await store.tag_merge_candidates()
        assert not any(c["kind"] == "containment" for c in remaining)

        assert await store.revoke_tag_exemption("containment", containment["exempt_signature"])
        assert any(c["kind"] == "containment" for c in await store.tag_merge_candidates())

    async def test_re_register_revives_exemption(self, store) -> None:
        await store.set_tag_exemption("containment", "topic:a|topic:b", reason="r1")
        assert await store.revoke_tag_exemption("containment", "topic:a|topic:b")
        await store.set_tag_exemption("containment", "topic:a|topic:b", reason="r2")

        (entry,) = [e for e in await store.list_tag_exemptions() if e["signature"] == "topic:a|topic:b"]
        assert entry["reason"] == "r2"
        rows = [e for e in await store.list_tag_exemptions(active_only=False) if e["signature"] == "topic:a|topic:b"]
        assert len(rows) == 1  # 复活是原地更新，不产生新行

    async def test_unknown_kind_rejected(self, store) -> None:
        with pytest.raises(ValueError):
            await store.set_tag_exemption("bogus", "sig")


class TestExemptTool:
    async def test_tag_pages_survive_retagging_and_new_insertions(self, store: MemoryStore) -> None:
        mem_tools.memory_tools_port.set(mem_tools.MemoryToolDeps(store, None))
        try:
            original_ids = set()
            for i in range(23):
                original_ids.add(await store.add(MemoryEntry(
                    content=f"fact {i}", tags=["topic:old"], timestamp=1, memory_type=MemoryType.SEMANTIC,
                )))
            first = json.loads(await mem_tools.memory_index("topic:old"))
            assert first["total"] == 23
            assert first["count"] == 20
            seen = {item["id"] for item in first["memories"]}
            for item in first["memories"]:
                result = json.loads(await mem_tools.update_memory(
                    item["id"], tags="topic:new", expected_version=item["version"],
                ))
                assert result["ok"]
            await _seed(store, "new arrival", ["topic:old"])
            second = json.loads(await mem_tools.memory_index("topic:old", before_id=first["next_before_id"]))
            assert second["total"] == 4
            assert second["count"] == 3
            assert second["next_before_id"] is None
            assert not seen.intersection(item["id"] for item in second["memories"])
            assert seen | {item["id"] for item in second["memories"]} == original_ids
            assert json.loads(await mem_tools.memory_index("topic:old", before_id=-1))["cause"] == "param"
        finally:
            mem_tools.memory_tools_port.unbind()

    async def test_update_rejects_stale_version_and_changes_during_tag_validation(
        self, store: MemoryStore, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mem_tools.memory_tools_port.set(mem_tools.MemoryToolDeps(store, None))
        try:
            mid = await store.add(MemoryEntry(content="original", tags=["topic:old"], memory_type=MemoryType.SEMANTIC))
            entry = await store.get(mid)
            assert entry is not None
            old_version = entry.version
            entry.content = "new fact"
            assert await store.update(entry)
            stale = json.loads(await mem_tools.update_memory(mid, tags="topic:new", expected_version=old_version))
            assert stale["cause"] == "state"
            original_gate = mem_tools.enforce_tag_gate

            async def concurrent_gate(tags: list[str], *, store: MemoryStore, enforce: bool) -> TagGateResult:
                latest = await store.get(mid)
                assert latest is not None
                latest.content = "concurrent fact"
                assert await store.update(latest)
                return await original_gate(tags, store=store, enforce=enforce)

            monkeypatch.setattr(mem_tools, "enforce_tag_gate", concurrent_gate)
            conflict = json.loads(await mem_tools.update_memory(mid, tags="topic:new"))
            assert conflict["cause"] == "state"
            latest = await store.get(mid)
            assert latest is not None
            assert latest.content == "concurrent fact"
            assert latest.tags == ["topic:old"]
        finally:
            mem_tools.memory_tools_port.unbind()

    async def test_index_pages_allow_review_and_revocation(self, store) -> None:
        mem_tools.memory_tools_port.set(mem_tools.MemoryToolDeps(store, None))
        try:
            assert await store.list_tags() == {}
            await _seed(store, "新事实", ["topic:保留"])
            for i in range(21):
                await store.set_tag_exemption("form_variant", f"topic:kept{i}", reason="独立主题")
            first = json.loads(await mem_tools.memory_index())
            assert first["tags"] == {"topic:保留": 1}
            assert len(first["tag_exemptions"]) == 20
            second = json.loads(await mem_tools.memory_index(exemptions_offset=first["tag_exemptions_next_offset"]))
            assert second["tag_exemptions_next_offset"] is None
            assert len(second["tag_exemptions"]) == 1
            rule = second["tag_exemptions"][0]
            assert rule["reason"] == "独立主题"
            response = json.loads(await mem_tools.tag_curation_exempt(rule["kind"], rule["signature"], revoke=True))
            assert response["revoked"] is True
        finally:
            mem_tools.memory_tools_port.unbind()

    async def test_register_and_revoke_via_tool(self, store) -> None:
        mem_tools.memory_tools_port.set(mem_tools.MemoryToolDeps(store, None))
        try:
            raw = await mem_tools.tag_curation_exempt("containment", "topic:a|topic:b", reason="判例：独立话题")
            assert json.loads(raw)["ok"] is True

            raw = await mem_tools.tag_curation_exempt("bogus", "x")
            assert "未知豁免类型" in raw

            raw = await mem_tools.tag_curation_exempt("containment", "topic:a|topic:b", revoke=True)
            assert json.loads(raw)["revoked"] is True

            raw = await mem_tools.tag_curation_exempt("containment", "topic:a|topic:b", revoke=True)
            assert "未找到" in raw
        finally:
            mem_tools.memory_tools_port.unbind()
