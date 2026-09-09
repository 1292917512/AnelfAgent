"""技能 MCP 依赖检查测试。"""

import pytest

from agent.skills.dependencies import dependency_notice, missing_mcp_dependencies
from agent.skills.skill_store import Skill


@pytest.fixture()
def mcp_env(tmp_path, monkeypatch):
    """隔离 MCP 配置文件。"""
    monkeypatch.setenv("ANELF_MCP_CONFIG", str(tmp_path / "mcp_servers.json"))
    monkeypatch.setenv("ANELF_MCP_CONFIG_PATH", "")
    return tmp_path


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
