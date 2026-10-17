"""思维快照只读汇总及观测异常隔离。"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.context.cache_stats import cache_usage_tracker
from agent.context.prompt_layers import prompt_cache_manager
from agent.mind.pfc import PrefrontalCortex
from agent.mind.tool_assembly import ToolAssembly
from services import status


@pytest.mark.parametrize("cache_failed", [False, True])
def test_thinking_snapshot_reads_owners_without_changing_work(monkeypatch, cache_failed):
    memory = PrefrontalCortex(SimpleNamespace())
    tools = ToolAssembly()
    memory.pending_user.append("user_qq:42")
    memory.set_message_preview("user_qq:42", "pending", created_ts_ns=123)
    memory.set_adapter_key("user_qq:42", "qq")
    memory.add_temporary({"role": "system", "content": "notice"}, scope="user_qq:42")
    tools._tool_recall = {"read_file": 3}
    tools._discovered_tools.add("inspect_workspace")
    tools._tag_activated_tools.add("recognize_image")
    runtime = SimpleNamespace(mind=SimpleNamespace(pfc=memory, tool_assembly=tools))
    monkeypatch.setattr(status, "get_runtime", lambda: runtime)
    expected_cache = {"requests": 2}
    for target, method in [(prompt_cache_manager, "stats"), (cache_usage_tracker, "summary")]:
        monkeypatch.setattr(target, method, Mock(return_value=expected_cache, side_effect=RuntimeError("unavailable") if cache_failed else None))
    before = memory.peek_all_tasks(), tools.tools_version
    snapshot = status.AgentStatusService().get_thinking_snapshot()
    assert snapshot["tool_recall"] == [{"name": "read_file", "count": 3}]
    assert snapshot["discovered_tools"] == ["inspect_workspace"]
    assert snapshot["tag_activated_tools"] == ["recognize_image"]
    assert snapshot["pending_messages"] == [{"scope": "user_qq:42", "uid": "42", "group_id": "0", "preview": "pending", "adapter_key": "qq"}]
    assert snapshot["short_term_memory_count"] == 1
    assert snapshot["prompt_cache"] == snapshot["provider_cache"] == ({} if cache_failed else expected_cache)
    assert before == (memory.peek_all_tasks(), tools.tools_version)
    assert memory.get_pending_signal("user_qq:42").created_ts_ns == 123
    monkeypatch.setattr(status, "get_runtime", lambda: None)
    assert status.AgentStatusService().get_thinking_snapshot() is None
