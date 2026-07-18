"""技能自学习系统（agent.skills）单元测试。"""

from __future__ import annotations

import time

import pytest

from agent.skills.curator import SkillCurator
from agent.skills.skill_matcher import SkillMatcher
from agent.skills.skill_store import (
    Skill,
    SkillState,
    SkillStore,
    parse_skill_md,
    render_skill_md,
)


@pytest.fixture
def store(tmp_path) -> SkillStore:
    return SkillStore(str(tmp_path / "skills"))


class TestSkillMdFormat:
    def test_roundtrip(self) -> None:
        skill = Skill(
            name="web-research",
            description="网络调研流程",
            trigger_patterns=["调研", "查资料"],
            content="# 步骤\n1. 搜索\n2. 总结",
            use_count=3,
            patch_count=1,
        )
        text = render_skill_md(skill)
        meta, body = parse_skill_md(text)
        assert meta["name"] == "web-research"
        assert meta["description"] == "网络调研流程"
        assert meta["trigger_patterns"] == ["调研", "查资料"]
        assert meta["use_count"] == 3
        assert meta["state"] == "active"
        assert "步骤" in body

    def test_parse_without_frontmatter(self) -> None:
        meta, body = parse_skill_md("# 纯正文")
        assert meta == {} and body == "# 纯正文"


class TestSkillStore:
    def test_create_and_get(self, store: SkillStore) -> None:
        store.create("web-research", "调研", "# 内容", ["调研"])
        skill = store.get("web-research")
        assert skill is not None
        assert skill.description == "调研"
        assert skill.trigger_patterns == ["调研"]
        assert skill.state == SkillState.ACTIVE

    def test_create_existing_patches(self, store: SkillStore) -> None:
        store.create("s1", "v1", "内容1")
        store.create("s1", "v2", "内容2", ["新词"])
        skill = store.get("s1")
        assert skill.content == "内容2"
        assert skill.patch_count == 1
        assert "新词" in skill.trigger_patterns

    def test_patch(self, store: SkillStore) -> None:
        store.create("s1", "desc", "旧内容")
        patched = store.patch("s1", content="新内容", add_trigger_patterns=["a", "b"])
        assert patched.content == "新内容"
        assert patched.trigger_patterns == ["a", "b"]
        assert patched.patch_count == 1
        assert store.patch("nonexistent") is None

    def test_list_excludes_archived(self, store: SkillStore) -> None:
        store.create("s1", "d", "c")
        store.create("s2", "d", "c")
        store.set_state("s2", SkillState.ARCHIVED)
        names = [s.name for s in store.list_skills()]
        assert names == ["s1"]
        names_all = [s.name for s in store.list_skills(include_archived=True)]
        assert set(names_all) == {"s1", "s2"}

    def test_record_use(self, store: SkillStore) -> None:
        store.create("s1", "d", "c")
        store.record_use("s1")
        assert store.get("s1").use_count == 1

    def test_delete(self, store: SkillStore) -> None:
        store.create("s1", "d", "c")
        assert store.delete("s1")
        assert store.get("s1") is None
        assert not store.delete("s1")

    def test_name_normalization(self, store: SkillStore) -> None:
        store.create("Web Research 调研!", "d", "c")
        assert store.get("web-research") is not None

    def test_pinned(self, store: SkillStore) -> None:
        store.create("s1", "d", "c")
        store.set_pinned("s1", True)
        assert store.get("s1").pinned is True


class TestSkillMatcher:
    async def test_keyword_match(self, store: SkillStore) -> None:
        store.create("web-research", "网络调研", "内容", ["调研", "搜索"])
        store.create("code-review", "代码审查", "内容", ["审查", "review"])
        matcher = SkillMatcher(store)
        matched = await matcher.match(["帮我调研一下这个话题"])
        assert matched and matched[0][0].name == "web-research"

    async def test_no_match(self, store: SkillStore) -> None:
        store.create("web-research", "网络调研", "内容", ["调研"])
        matcher = SkillMatcher(store)
        matched = await matcher.match(["完全无关的内容 xyz"])
        assert matched == []

    async def test_archived_not_matched(self, store: SkillStore) -> None:
        store.create("s1", "d", "c", ["调研"])
        store.set_state("s1", SkillState.ARCHIVED)
        matcher = SkillMatcher(store)
        assert await matcher.match(["调研"]) == []

    async def test_top_k(self, store: SkillStore) -> None:
        for i in range(5):
            store.create(f"s{i}", "d", "c", ["调研"])
        matcher = SkillMatcher(store)
        matched = await matcher.match(["调研"], top_k=2)
        assert len(matched) == 2


class TestSkillCurator:
    def test_active_to_stale(self, store: SkillStore) -> None:
        skill = store.create("s1", "d", "c")
        # 模拟 40 天未活动
        skill.last_activity_at = time.time() - 40 * 86400
        store.save(skill)
        curator = SkillCurator(store)
        report = curator.apply_automatic_transitions()
        assert report["staled"] == ["s1"]
        assert store.get("s1").state == SkillState.STALE

    def test_stale_to_archived(self, store: SkillStore) -> None:
        skill = store.create("s1", "d", "c")
        skill.state = SkillState.STALE
        skill.last_activity_at = time.time() - 100 * 86400
        store.save(skill)
        curator = SkillCurator(store)
        report = curator.apply_automatic_transitions()
        assert report["archived"] == ["s1"]
        assert store.get("s1").state == SkillState.ARCHIVED

    def test_pinned_exempt(self, store: SkillStore) -> None:
        skill = store.create("s1", "d", "c")
        skill.pinned = True
        skill.last_activity_at = time.time() - 200 * 86400
        store.save(skill)
        curator = SkillCurator(store)
        report = curator.apply_automatic_transitions()
        assert report["staled"] == [] and report["archived"] == []
        assert report["skipped_pinned"] == 1

    def test_recent_untouched(self, store: SkillStore) -> None:
        store.create("s1", "d", "c")
        curator = SkillCurator(store)
        report = curator.apply_automatic_transitions()
        assert report["staled"] == [] and report["archived"] == []


class TestSkillTools:
    async def test_create_and_list(self, store: SkillStore, monkeypatch) -> None:
        from agent.skills import tools as skill_tools
        monkeypatch.setattr(skill_tools, "_store", store)
        monkeypatch.setattr(skill_tools, "_matcher", SkillMatcher(store))

        import json
        result = json.loads(skill_tools.create_skill(
            name="t1", description="测试", content="内容", trigger_patterns="测试,示例",
        ))
        assert result["ok"]

        listed = json.loads(skill_tools.list_skills())
        assert listed["count"] == 1
        assert listed["skills"][0]["name"] == "t1"

        detail = json.loads(skill_tools.get_skill("t1"))
        assert detail["content"] == "内容"

        updated = json.loads(skill_tools.update_skill("t1", content="新内容"))
        assert updated["patch_count"] == 1

        searched = json.loads(await skill_tools.search_skills("测试"))
        assert searched["count"] >= 1
