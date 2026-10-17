"""技能声明依赖、本地 MCP 关联与召回投影契约。"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.skills.dependencies import (
    MCPServerState,
    dependency_notice,
    mcp_binding_notice,
    mcp_binding_status,
    mcp_status_port,
    missing_mcp_dependencies,
    set_mcp_bindings,
)
from agent.skills.runtime import SkillRuntime
from agent.skills.skill_store import Skill, SkillStore


@pytest.fixture()
def state_source():
    previous = mcp_status_port.get() if mcp_status_port.bound else None
    states = {"docs": MCPServerState(True, True, 2, "sleeping"),
              "maps": MCPServerState(False, False, 0, "unavailable")}
    mcp_status_port.set(lambda scope: states)
    yield states
    mcp_status_port.set(previous) if previous else mcp_status_port.unbind()


def test_bindings_are_local_and_survive_counters_edits_and_reload(tmp_path: Path, state_source) -> None:
    store = SkillStore(str(tmp_path))
    skill = store.save(Skill(name="inspect", description="Inspect deployment", content="original"))
    path = tmp_path / skill.name / "SKILL.md"
    original = path.read_bytes()
    version = store.version
    set_mcp_bindings(store, skill.name, ["docs", "maps", "docs"])
    assert path.read_bytes() == original
    assert store.version == version
    store.record_match(skill.name)
    store.record_use(skill.name)
    store.patch(skill.name, description="Updated")
    reloaded = SkillStore(str(tmp_path)).get(skill.name)
    assert reloaded is not None and reloaded.mcp_servers == ["docs", "maps"]
    assert reloaded.use_count == 1 and reloaded.match_count == 1
    assert "mcp_servers" not in path.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="不存在"):
        set_mcp_bindings(store, skill.name, ["missing"])
    state_source.pop("docs")
    assert mcp_binding_status(reloaded)[0] == {"name": "docs", "configured": False}
    set_mcp_bindings(store, skill.name, ["docs"])
    assert store.get(skill.name).mcp_servers == ["docs"]
    mcp_status_port.unbind()
    set_mcp_bindings(store, skill.name, [])
    assert store.get(skill.name).mcp_servers == []


def test_imported_frontmatter_cannot_set_local_bindings(tmp_path: Path) -> None:
    directory = tmp_path / "community"
    directory.mkdir()
    (directory / "SKILL.md").write_text("---\nname: community\nmcp_servers: [docs]\n---\nBody", encoding="utf-8")
    assert SkillStore(str(tmp_path)).get("community").mcp_servers == []


def test_unavailable_status_does_not_claim_missing(state_source) -> None:
    mcp_status_port.set(lambda scope: (_ for _ in ()).throw(RuntimeError("offline")))
    skill = Skill(name="inspect", mcp_servers=["docs"])
    assert mcp_binding_status(skill) == [{"name": "docs", "configured": None}]
    assert "状态未知" in mcp_binding_notice(skill, "scope-a")


async def test_recall_refreshes_links_without_auto_wakeup_or_prefix_changes(tmp_path: Path, state_source) -> None:
    from agent.context.message_schema import normalize_for_send
    from agent.context.observation import context_skills
    from agent.mind.recall.inputs import match_skills
    from agent.skills.catalog import catalog_section

    store = SkillStore(str(tmp_path))
    stale_skill = store.save(Skill(name="inspect", description="Inspect deployment", content="Steps"))
    before = catalog_section(store)
    set_mcp_bindings(store, "inspect", ["docs"])
    mind = SimpleNamespace(
        pfc=SimpleNamespace(add_temporary=MagicMock()),
        skills=SkillRuntime(store, SimpleNamespace(match=AsyncMock(return_value=[(stale_skill, 0.9)])), MagicMock()),
    )
    messages = await match_skills(mind, "inspect", scope="scope-a")
    assert "docs（启用、已连接、分组未激活）" in messages[0]["content"]
    facts = context_skills(messages)
    assert facts[0]["mcp_status"][0]["activation"] == "sleeping"
    assert catalog_section(store) == before
    assert "_source" not in normalize_for_send(messages)[0]
    messages[0]["content"] = messages[0]["content"].split("关联 MCP")[0]
    assert "mcp_status" not in context_skills(messages)[0]


async def test_web_and_ai_share_the_binding_operation(tmp_path: Path, state_source, monkeypatch) -> None:
    from agent.skills import tools
    from agent.skills.skill_matcher import SkillMatcher
    from services.skill import SkillService
    from web.routers import skills

    service = SkillService(str(tmp_path))
    service.create_skill("inspect", "Inspect", "Body")
    monkeypatch.setattr(skills, "_skill_svc", service)
    await skills.set_mcp_bindings("inspect", skills.SkillMCPBindingsRequest(servers=["docs"]))
    previous = tools.skill_tools_port.get() if tools.skill_tools_port.bound else None
    store = SkillStore(str(tmp_path))
    tools.skill_tools_port.set(tools.SkillToolDeps(store, SkillMatcher(store)))
    try:
        assert json.loads(tools.get_skill("inspect"))["mcp_status"][0]["name"] == "docs"
        assert json.loads(tools.set_skill_mcp_servers("inspect", ["maps"]))["ok"]
        snapshot = await skills.get_mcp_bindings("inspect")
        assert [item["name"] for item in snapshot["servers"]] == ["maps"]
        assert snapshot["servers"][0]["enabled"] is False
        assert "url" not in str(snapshot) and "api_key" not in str(snapshot)
    finally:
        tools.skill_tools_port.set(previous) if previous else tools.skill_tools_port.unbind()


def test_runtime_snapshot_is_scope_isolated_and_read_only(tmp_path: Path, monkeypatch) -> None:
    from agent.messages.everything import build_entity_scope
    from agent.mind.tool_activation import ToolActivationManager
    from agent.runtime import skill_dependencies
    from entities.mcp import config

    monkeypatch.setattr(config, "_writable_config_path", lambda: tmp_path / "mcp.json")
    config.MCPServerStore().save_config({"mcpServers": {"docs": {"command": "demo", "enabled": True}}})
    monkeypatch.setattr(skill_dependencies, "get_mcp_bridge", lambda: SimpleNamespace(get_connected_servers=lambda: {"docs": ["read"]}))
    monkeypatch.setattr(skill_dependencies, "_mcp_sleep_enabled", lambda name, **kwargs: True)
    activation = ToolActivationManager()
    monkeypatch.setattr(skill_dependencies, "tool_activation", activation)
    scope_a = build_entity_scope("user", "webui", "a")
    scope_b = build_entity_scope("user", "qq", "b")
    activation.activate("mcp:docs", scope=scope_a)
    version = activation.version
    assert skill_dependencies.skill_mcp_snapshot(scope_a)["docs"].activation == "active"
    assert skill_dependencies.skill_mcp_snapshot(scope_b)["docs"].activation == "sleeping"
    assert skill_dependencies.skill_mcp_snapshot()["docs"].activation == "on_demand"
    assert activation.version == version
    activation.deactivate("mcp:docs", scope=scope_a)
    assert skill_dependencies.skill_mcp_snapshot(scope_a)["docs"].activation == "deactivated"
    monkeypatch.setattr(skill_dependencies, "_mcp_sleep_enabled", lambda name, **kwargs: False)
    assert skill_dependencies.skill_mcp_snapshot(scope_a)["docs"].activation == "active"


@pytest.fixture()
def mcp_env(tmp_path, monkeypatch):
    """隔离 MCP 配置文件。"""
    monkeypatch.setenv("ANELF_MCP_CONFIG", str(tmp_path / "mcp_servers.json"))
    monkeypatch.setenv("ANELF_MCP_CONFIG_PATH", "")
    from agent.runtime.skill_dependencies import skill_mcp_snapshot
    from agent.skills.dependencies import mcp_status_port
    previous = mcp_status_port.get() if mcp_status_port.bound else None
    mcp_status_port.set(skill_mcp_snapshot)
    yield tmp_path
    if previous is None:
        mcp_status_port.unbind()
    else:
        mcp_status_port.set(previous)


class TestMissingDependencies:
    def test_no_dependencies(self, mcp_env):
        skill = Skill(name="s1")
        assert missing_mcp_dependencies(skill) == []
        assert dependency_notice(skill) == ""

    def test_missing_reported(self, mcp_env):
        skill = Skill(name="s1", dependencies=[
            {"type": "mcp", "name": "notion", "url": "https://mcp.notion.com/mcp"}])
        missing = missing_mcp_dependencies(skill)
        assert len(missing) == 1
        notice = dependency_notice(skill)
        assert "notion" in notice and "mcp_manage" in notice

    def test_installed_satisfied(self, mcp_env):
        from entities.mcp.config import MCPServerStore

        MCPServerStore().create_server("notion", {"url": "https://mcp.notion.com/mcp"})
        skill = Skill(name="s1", dependencies=[{"type": "mcp", "name": "notion"}])
        assert missing_mcp_dependencies(skill) == []

    def test_plugin_prefixed_satisfied(self, mcp_env):
        """插件合并的冲突前缀名视为已满足。"""
        from entities.mcp.config import MCPServerStore

        MCPServerStore().create_server("plug__notion", {"url": "https://x"})
        skill = Skill(name="s1", dependencies=[{"type": "mcp", "name": "notion"}])
        assert missing_mcp_dependencies(skill) == []

    def test_non_mcp_types_ignored(self, mcp_env):
        skill = Skill(name="s1", dependencies=[{"type": "pip", "name": "foo"}])
        assert missing_mcp_dependencies(skill) == []


class TestFrontmatterRoundTrip:
    def test_dependencies_survive_save_load(self, tmp_path):
        from agent.skills.skill_store import SkillStore

        store = SkillStore(str(tmp_path / "skills"))
        skill = Skill(name="dep_skill", description="d", content="body",
                      dependencies=[{"type": "mcp", "name": "notion"}])
        store.save(skill)
        loaded = store.get("dep_skill")
        assert loaded.dependencies == [{"type": "mcp", "name": "notion"}]
