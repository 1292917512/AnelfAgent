"""自画像（self_profile.py）与积极性（proactivity.py）单元测试。"""

from __future__ import annotations

from agent.memory.memory_types import MemoryEntry, MemoryType
from agent.memory.self_profile import (
    SELF_PROFILE_SOURCE,
    SELF_SCOPE,
    load_self_profile,
    render_self_profile_block,
    resolve_promotion_target,
)
from agent.mind.proactivity import (
    get_proactivity,
    idle_beats_factor,
    proactivity_guidance,
)


class TestPromotionTarget:
    def test_self_when_no_owner_tag(self):
        entry = MemoryEntry(memory_type=MemoryType.EPISODIC, content="x", tags=[])
        assert resolve_promotion_target(entry) == SELF_SCOPE

    def test_user_tag_routes_to_user(self):
        entry = MemoryEntry(
            memory_type=MemoryType.EPISODIC, content="x",
            tags=["user:qq:123", "type:reflection"],
        )
        assert resolve_promotion_target(entry) == "user:qq:123"

    def test_group_tag_routes_to_group(self):
        entry = MemoryEntry(
            memory_type=MemoryType.EPISODIC, content="x",
            tags=["group:qq:456"],
        )
        assert resolve_promotion_target(entry) == "group:qq:456"


class TestSelfProfileStore:
    async def test_empty_by_default(self, store):
        assert await load_self_profile(store) == ""

    async def test_render_block(self):
        assert render_self_profile_block("") == ""
        block = render_self_profile_block("我解释偏啰嗦，正在改进")
        assert "[系统注入·自我画像]" in block
        assert "啰嗦" in block

    async def test_loads_entity_mirror(self, store):
        """ENTITY 镜像条目可被读取（晋升写入路径的读侧）。"""
        entry = MemoryEntry(
            memory_type=MemoryType.ENTITY,
            content="我对细节要求高",
            source=SELF_PROFILE_SOURCE,
            tags=[SELF_SCOPE, "type:profile"],
        )
        await store.add(entry)
        assert await load_self_profile(store) == "我对细节要求高"


class TestProactivity:
    def test_default_level(self):
        from core.config import ConfigManager
        ConfigManager.set("proactivity_level", 0.5)
        assert get_proactivity() == 0.5
        assert idle_beats_factor() == 1.0

    def test_extremes_clamped(self):
        from core.config import ConfigManager
        ConfigManager.set("proactivity_level", 9.9)
        assert get_proactivity() == 1.0
        assert idle_beats_factor() == 0.5
        ConfigManager.set("proactivity_level", -3)
        assert get_proactivity() == 0.0
        assert idle_beats_factor() == 1.5

    def test_guidance_mentions_self_adjust(self):
        from core.config import ConfigManager
        ConfigManager.set("proactivity_level", 0.5)
        text = proactivity_guidance()
        assert "update_entity_config" in text
        assert "proactivity_level" in text
