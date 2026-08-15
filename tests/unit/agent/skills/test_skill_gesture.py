"""用户技能手势（agent/skills/gesture + recollection._match_skills 消费）单元测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from agent.skills.gesture import parse_skill_gesture
from agent.skills.skill_store import Skill, parse_skill_md, render_skill_md


class TestGestureParsing:
    def test_simple_gesture(self) -> None:
        assert parse_skill_gesture("/deploy-check 请检查部署") == "deploy-check"

    def test_gesture_only(self) -> None:
        assert parse_skill_gesture("/git-helper") == "git-helper"

    def test_leading_whitespace_ok(self) -> None:
        assert parse_skill_gesture("  /foo bar") == "foo"

    def test_no_gesture(self) -> None:
        assert parse_skill_gesture("普通消息 /foo") is None
        assert parse_skill_gesture("帮我 /foo") is None
        assert parse_skill_gesture("") is None

    def test_slash_alone_not_gesture(self) -> None:
        assert parse_skill_gesture("/ 斜杠开头但无名字") is None

    def test_invalid_name_chars_rejected(self) -> None:
        # 名字必须字母/数字开头，只含 [A-Za-z0-9_-]
        assert parse_skill_gesture("/中文技能") is None
        assert parse_skill_gesture("/-foo") is None
        assert parse_skill_gesture("/foo.bar") is None  # 点不在集合内 → 不命中


class TestUserInvocableField:
    def test_frontmatter_roundtrip_false(self) -> None:
        skill = Skill(name="internal-only", description="d", user_invocable=False,
                      content="正文")
        text = render_skill_md(skill)
        meta, body = parse_skill_md(text)
        assert meta.get("user_invocable") is False
        assert "正文" in body

    def test_default_true(self) -> None:
        meta, _ = parse_skill_md(render_skill_md(Skill(name="x", description="d")))
        # 未显式写 false 时保持可调用（对齐 dsh 双调用面默认）
        assert meta.get("user_invocable", True) is True


def _fake_skill(name: str, user_invocable: bool = True) -> Skill:
    return Skill(name=name, description=f"技能 {name}", content=f"{name} 的完整正文",
                 user_invocable=user_invocable)


def _fake_mind(store_map: dict, pending: dict):
    """最小 Mind 替身：skill_store/skill_matcher/pfc/_pending_skill_gestures。"""
    store = SimpleNamespace(
        get=lambda name: store_map.get(name),
        record_use=MagicMock(),
    )

    async def match(query_texts, *, top_k=3, min_score=0.15, query_vec=None):
        return []

    return SimpleNamespace(
        _skills_enabled=lambda: True,
        skill_store=store,
        skill_matcher=SimpleNamespace(match=match),
        pfc=SimpleNamespace(add_temporary=MagicMock()),
        _pending_skill_gestures=pending,
    )


class TestMatchSkillsGesture:
    async def test_forced_injection_bypasses_scoring(self) -> None:
        from agent.mind import recollection
        skill = _fake_skill("deploy-check")
        mind = _fake_mind({"deploy-check": skill}, {"user_qq:1": ["deploy-check"]})
        msgs = await recollection._match_skills(
            mind, [{"role": "user", "content": "hi"}], scope="user_qq:1")
        assert msgs and "deploy-check" in msgs[0]["content"]
        assert "的完整正文" in msgs[0]["content"]  # 正文注入
        assert mind._pending_skill_gestures.get("user_qq:1") is None  # 已消费
        mind.skill_store.record_use.assert_called_once_with("deploy-check")

    async def test_not_found_writes_hint(self) -> None:
        from agent.mind import recollection
        mind = _fake_mind({}, {"user_qq:1": ["ghost-skill"]})
        msgs = await recollection._match_skills(
            mind, [{"role": "user", "content": "hi"}], scope="user_qq:1")
        assert msgs == []
        mind.pfc.add_temporary.assert_called_once()
        hint = mind.pfc.add_temporary.call_args[0][0]
        assert "ghost-skill" in hint["content"] and "不存在" in hint["content"]

    async def test_user_invocable_false_skips(self) -> None:
        from agent.mind import recollection
        skill = _fake_skill("internal-only", user_invocable=False)
        mind = _fake_mind({"internal-only": skill}, {"user_qq:1": ["internal-only"]})
        msgs = await recollection._match_skills(
            mind, [{"role": "user", "content": "hi"}], scope="user_qq:1")
        assert msgs == []
        # 被拒绝的手势不应写"不存在"提示（技能存在但关闭了手势）
        mind.pfc.add_temporary.assert_not_called()

    async def test_no_pending_no_change(self) -> None:
        from agent.mind import recollection
        mind = _fake_mind({}, {})
        msgs = await recollection._match_skills(
            mind, [{"role": "user", "content": "hi"}], scope="user_qq:1")
        assert msgs == []
