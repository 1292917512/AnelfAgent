"""标签治理豁免测试：CRUD / 签名口径 / 候选过滤 / 豁免工具。"""

from __future__ import annotations

import json
import time

import pytest

from agent.memory import tools as mem_tools
from agent.memory.memory_types import MemoryEntry, MemoryType


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
