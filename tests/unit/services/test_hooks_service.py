"""hooks 管理服务测试（config/hooks.json 读写/校验/热重载）。"""

import pytest

from core.path import ConfigPaths
from services.hooks import HookService


@pytest.fixture()
def svc(tmp_path, monkeypatch):
    """隔离 hooks.json 路径并重置 hook 注册表。"""
    import agent.hooks.runner as runner

    monkeypatch.setattr(ConfigPaths, "HOOKS", str(tmp_path / "config" / "hooks.json"))
    monkeypatch.setattr(runner, "_registry", runner.HookRegistry())
    return HookService()


class TestGetHooks:
    def test_empty_when_missing(self, svc):
        data = svc.get_hooks()
        assert data["exists"] is False
        assert data["hooks"] == {}
        assert data["active"] == {"tool_pre": 0, "tool_post": 0, "reply_end": 0}

    def test_reads_existing(self, svc):
        svc.save_hooks({"tool_pre": [{"matcher": "delete_*", "command": "true"}]})
        data = svc.get_hooks()
        assert data["exists"] is True
        assert data["hooks"]["tool_pre"][0]["matcher"] == "delete_*"
        assert data["active"]["tool_pre"] == 1

    def test_returns_enabled_integrations_stats(self, svc):
        from agent.hooks.runner import HOOK_EVENTS

        data = svc.get_hooks()
        assert data["enabled"] is True
        assert data["integrations"] and len(data["integrations"]) == len(HOOK_EVENTS)
        assert {i["event"] for i in data["integrations"]} == set(HOOK_EVENTS)
        assert all(i["where"] for i in data["integrations"])
        assert set(data["stats"]["events"]) == set(HOOK_EVENTS)

    def test_active_counts_enabled_only(self, svc):
        svc.save_hooks({"tool_pre": [
            {"command": "true"},
            {"command": "true", "enabled": False},
        ]})
        data = svc.get_hooks()
        assert data["active"]["tool_pre"] == 1


class TestSaveHooks:
    def test_save_validates_and_reloads(self, svc):
        from agent.hooks.runner import get_hook_registry

        result = svc.save_hooks({
            "tool_post": [{"command": "wc -c", "timeout": 999}],
        })
        assert result == {"saved": True, "count": 1}
        # timeout clamp 到上限 60
        spec = get_hook_registry().for_event("tool_post")[0]
        assert spec.timeout == 60.0

    def test_invalid_rejected_without_write(self, svc):
        with pytest.raises(ValueError, match="未知 hook 事件"):
            svc.save_hooks({"bad_event": []})
        with pytest.raises(ValueError, match="缺少 command"):
            svc.save_hooks({"tool_pre": [{"matcher": "*"}]})
        assert svc.get_hooks()["exists"] is False

    def test_empty_config_removes_file(self, svc):
        svc.save_hooks({"tool_pre": [{"command": "true"}]})
        result = svc.save_hooks({})
        assert result == {"saved": True, "count": 0}
        assert svc.get_hooks()["exists"] is False

    def test_corrupt_existing_file_tolerated(self, svc, tmp_path):
        import os
        path = svc._path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("{bad json")
        assert svc.get_hooks()["hooks"] == {}


class TestRunHooksTest:
    async def test_executes_and_reports_per_hook(self, svc):
        from agent.hooks.runner import reset_hook_stats

        reset_hook_stats()
        try:
            svc.save_hooks({"tool_pre": [
                {"matcher": "delete_*", "command": "exit 0"},
                {"matcher": "*", "command": "exit 0", "enabled": False},
            ]})
            result = await svc.run_hooks_test("tool_pre", "delete_file")
            assert result["event"] == "tool_pre"
            assert result["enabled"] is True
            assert result["executed"] == 1  # 停用条目不执行
            assert len(result["results"]) == 1
            assert result["results"][0]["ok"] is True
        finally:
            reset_hook_stats()

    async def test_invalid_event_rejected(self, svc):
        with pytest.raises(ValueError, match="未知 hook 事件"):
            await svc.run_hooks_test("bogus_event")


class TestExample:
    def test_example_loadable_and_valid(self, svc):
        from agent.hooks.runner import parse_hooks_data

        example = svc.get_example()
        specs = parse_hooks_data(example)  # 样例本身必须过运行时校验
        assert sum(len(v) for v in specs.values()) == 3
