"""AI 与 Web 共用钩子写入及召回策略配置。"""

import json

import pytest

from agent.hooks.configuration import hook_configuration
from agent.hooks.tools import get_hooks, set_script_hooks
from agent.hooks_llm import HookRegistry, LLMHookSpec
from core.path import ConfigPaths


@pytest.fixture
def hooks_path(tmp_path, monkeypatch):
    path = tmp_path / "hooks.json"
    monkeypatch.setattr(ConfigPaths, "HOOKS", str(path))
    from agent.hooks import runner
    monkeypatch.setattr(runner, "_registry", runner.HookRegistry())
    return path


async def test_script_tools_preserve_other_events_and_share_web_validation(hooks_path):
    from services.hooks import HookService
    HookService().save_hooks({"tool_pre": [{"command": "check-input", "mode": "sync"}]})
    result = json.loads(await set_script_hooks("reply_end", [{"command": "audit-result", "mode": "async"}]))
    assert result["saved"] and result["count"] == 2
    scripts = HookService().get_hooks()["hooks"]
    assert scripts["tool_pre"][0]["command"] == "check-input"
    assert scripts["reply_end"][0]["mode"] == "async"
    original = hooks_path.read_text()
    await set_script_hooks("reply_end", [{"command": "", "mode": "invalid"}])
    assert hooks_path.read_text() == original
    await set_script_hooks("reply_end", [])
    assert set(hook_configuration.get_hooks()["hooks"]) == {"tool_pre"}


async def test_damaged_script_configuration_is_not_overwritten(hooks_path):
    hooks_path.write_text("broken json")
    result = await set_script_hooks("reply_end", [{"command": "audit"}])
    assert "error" in result
    assert hooks_path.read_text() == "broken json"


async def test_ai_can_discover_hook_configuration_without_cross_scope_execution(hooks_path):
    async def handler(ctx):
        return None
    HookRegistry.register(LLMHookSpec("review", "after_reply", handler))
    try:
        result = json.loads(await get_hooks())
        row = next(hook for hook in result["llm"] if hook["name"] == "review")
        assert row["config_keys"]["context"] == "llm_hook_review_context"
        assert "execution" not in result
    finally:
        HookRegistry.unregister("review")


async def test_ai_recall_levels_are_turn_local_and_global_defaults_are_shared():
    from agent.config import save_config_value
    from agent.memory.recall_policy import RecallPolicy, current_recall_policy, recall_turn
    from agent.memory.tools import set_recall_policy
    from core.conversation_scope import bind_scope, reset_scope
    save_config_value("memory_recall_level", "fast")
    assert current_recall_policy().level == "fast"
    token = bind_scope("user_qq:42")
    try:
        async with recall_turn("user_qq:42"):
            await set_recall_policy("tags", tags="topic:deployment", background=False)
            assert current_recall_policy("user_qq:42") == RecallPolicy(level="tags", tags=("topic:deployment",), background=False)
            assert current_recall_policy("user_webui:42").level == "fast"
        assert current_recall_policy("user_qq:42").level == "fast"
    finally:
        reset_scope(token)
