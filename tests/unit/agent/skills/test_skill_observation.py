"""技能观测以最终注入范围为准，不改动模型输入或混淆会话。"""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from agent.context.message_schema import normalize_for_send
from agent.context.observation import context_skills, emit_context_summary
from agent.mind.recollection import _apply_memory_budget, _match_skills
from agent.skills.skill_store import Skill, SkillState
from core.activity import activity_scope
from services.workspace_activity import WorkspaceActivityService


async def test_skill_evidence_tracks_actual_budget_and_never_changes_wire_messages(monkeypatch):
    forced = Skill(name="inspect", description="Inspect deployment", content="Steps " * 400)
    candidate = Skill(name="archive-check", description="Prior evidence", state=SkillState.ARCHIVED)
    mind = SimpleNamespace(
        _skills_enabled=lambda: True,
        skill_store=SimpleNamespace(get=lambda name: {forced.name: forced, candidate.name: candidate}.get(name), record_use=MagicMock(), record_match=MagicMock()),
        skill_matcher=SimpleNamespace(match=AsyncMock(return_value=[(candidate, 0.426)])),
        _pending_skill_gestures={"user_qq:1": [forced.name]},
    )
    messages = await _match_skills(mind, "deployment", scope="user_qq:1")
    original = deepcopy(messages)
    facts = context_skills(messages)
    assert [(fact["name"], fact["mode"], fact["score"]) for fact in facts] == [
        ("inspect", "explicit", None), ("archive-check", "candidate", 0.426)]
    assert normalize_for_send(messages) == normalize_for_send([
        {key: value for key, value in message.items() if key != "_source"} for message in original])
    monkeypatch.setattr("core.config.get_config_int", lambda key, default: 400)
    visible = context_skills(_apply_memory_budget(messages))
    assert len(visible) == 1 and visible[0]["name"] == "inspect" and visible[0]["truncated"]
    assert context_skills(_apply_memory_budget([{"content": "Earlier memories " * 400}, *messages])) == []
    assert messages == original


async def test_skill_context_projection_remains_bounded_and_per_run(monkeypatch):
    monkeypatch.setattr("services.workspace_activity.publish", lambda _: None)
    service = WorkspaceActivityService()
    service.start()

    async def build(turn, scope):
        async with activity_scope(turn, scope):
            await asyncio.sleep(0)
            fact = {"name": turn, "description": "Evidence", "mode": "candidate", "state": "active",
                    "score": 0.3, "truncated": False, "heading_end": 1, "end": 2}
            await emit_context_summary([
                {"role": "system", "_layer": "context", "content": "notes" * 4000},
                {"role": "system", "_layer": "memory", "content": "OK", "_source": {"origin": "skill_match", "skills": [fact]}},
            ], 42)
    try:
        await asyncio.gather(build("qq-run", "user_qq:1"), build("child-run", "reflect:child"))
        runs = service.snapshot()["runs"]
        for run in runs:
            entry = run["entries"][0]
            assert [skill["name"] for skill in entry["skills"]] == [run["id"]]
            assert [skill["name"] for skill in run["skills"]] == [run["id"]]
            assert entry["duration_ms"] == 42
            assert entry["truncated"]
    finally:
        service.stop()
