"""从公开安装入口到心跳、文件回执与冷启动恢复的离线契约。"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.artifacts import capture_task_files, history_for_scope, present_file
from agent.channel import output_tools
from agent.channel.channel_types import ChannelStatus
from agent.heartbeat.config import HeartbeatConfig, ScheduleMode, TaskSchedule
from agent.heartbeat.engine import HeartbeatEngine
from agent.task.history import get_history
from agent.task.tools import present_task_file
from core.entity import EntityRegistry
from core.path import ConfigPaths
from core.plugins.manager import PluginManager
from core.plugins.manifest import PluginError
from core.plugins.status import get_operation_board
from core.plugins.store import PluginRegistry
from entities.plugins.activation import deactivate_plugin, wire_plugin_manager
from entities.plugins.diagnostics import plugin_detail


def test_preflight_classifies_failures_and_rechecks_revision(manager, plugin_env):
    source = plugin_env.make_plugin("probe")
    info = manager.inspect(source=str(source))
    assert info["skills"] == ["probe_skill"] and info["mcp_servers"] == ["probe_srv"]
    assert info["tools_file"] == "tools.py"
    assert not manager.list_plugins() and not EntityRegistry.get("probe_ping")
    (source / "tools.py").write_text("raise RuntimeError()", encoding="utf8")
    with pytest.raises(PluginError, match="重新预检"):
        manager.install_from_source(str(source), expected_revision=info["revision"])
    latest = get_operation_board().operations()[0]
    assert latest["stage"] == "manifest" and latest["status"] == "failed"
    assert get_operation_board().operations(latest["id"]) == [latest]
    assert not manager.list_plugins() and not plugin_env.read_mcp_servers()
    (source / "plugin.json").write_text("[", encoding="utf8")
    assert manager.inspect(source=str(source))["status"] == "invalid"
    assert manager.inspect(source=str(source / "missing"))["status"] == "unverified"


@pytest.mark.parametrize("completed", [True, False], ids=["completed", "interrupted-after-send"])
async def test_scheduled_plugin_file_confirmed_once_across_restore(manager, plugin_env, monkeypatch, completed):
    root = plugin_env.root / "workspace"
    monkeypatch.setattr("agent.artifacts.workspace_root", lambda: str(root))
    tasks = plugin_env.root / "tasks"
    tasks.mkdir()
    monkeypatch.setattr(ConfigPaths, "TASKS_DIR", str(tasks))
    monkeypatch.setattr(ConfigPaths, "TASK_HISTORY", str(plugin_env.root / "history.json"))
    (tasks / "publish.json").write_text(json.dumps({
        "name": "publish", "prompt": "Publish the generated report", "scope": "global",
        "save_result_to_memory": False, "allow_output_tools": True,
    }), encoding="utf8")
    source = plugin_env.make_plugin("report", mcp=False, skill=False)
    file = root / "report.txt"
    (source / "tools.py").write_text(
        "from pathlib import Path\nfrom entities._sdk import tool\n"
        '@tool(name="make_report", group="report")\n'
        'def make_report() -> str:\n    """Write the report."""\n'
        f'    Path({str(file)!r}).write_text("ok", encoding="utf8")\n    return "ok"\n', encoding="utf8",
    )
    preview = manager.inspect(source=str(source))
    installed = manager.install_from_source(str(source), expected_revision=preview["revision"])
    send = AsyncMock(return_value={"success": True, "message_id": "receipt-1"})
    channel = SimpleNamespace(status=ChannelStatus.RUNNING, send_file=send,
                              conversation_scope_for_target=lambda *_: None)
    monkeypatch.setattr(output_tools, "_get_channel", lambda _: channel)
    monkeypatch.setattr(output_tools, "guard_empty_conversation", AsyncMock(return_value=None))
    monkeypatch.setattr(output_tools, "guard_outbound", lambda *_: None)
    monkeypatch.setattr(output_tools, "_record_sent_reply", AsyncMock(return_value=True))
    monkeypatch.setattr(output_tools, "note_outbound", lambda *_: None)

    async def reflect(messages, *, completion, **kwargs):
        await EntityRegistry.execute_tool("make_report", "{}")
        assert file.read_text(encoding="utf8") == "ok"
        assert json.loads(await present_task_file("report.txt"))["path"] == "report.txt"
        receipt = json.loads(await output_tools.send_file("demo", "user:42", str(file)))
        assert receipt["success"] is True
        completion["reason"] = "completed" if completed else "budget_exhausted"
        return "Report delivered"

    mind = SimpleNamespace(reflect=reflect, last_activity_ts=0.0, is_reply=False)
    monkeypatch.setattr("agent.task.executor.coordinator.prepare_recollection", AsyncMock(return_value=[]))
    monkeypatch.setattr("agent.mind.tools.scheduler.check_due_reminders", AsyncMock())
    monkeypatch.setattr(HeartbeatEngine, "_run_maintenance", AsyncMock())
    monkeypatch.setattr("agent.heartbeat.engine.get_heartbeat_config", lambda: HeartbeatConfig(
        task_schedules=[TaskSchedule(task_name="publish", mode=ScheduleMode.SCHEDULED, schedule_times=["00:00"])],
    ))

    def engine():
        return HeartbeatEngine(mind, SimpleNamespace(), memory=mind, skills=None)

    assert await engine().tick() == (["publish"] if completed else [])
    row, = get_history("publish")
    assert row["trigger"] == "scheduled" and row["status"] == ("success" if completed else "incomplete")
    item, = history_for_scope(row, "user_demo:42")["artifacts"]
    assert item == {"path": "report.txt", "size": 2,
                    "receipts": [{"scope": "user_demo:42", "message_id": "receipt-1"}]}
    assert "artifacts" not in history_for_scope(row, "user_demo:21")
    with capture_task_files("publish", row["artifact_scope"]):
        assert history_for_scope(row, "reflect:next")["artifacts"] == row["artifacts"]
    with capture_task_files("other", row["artifact_scope"]):
        assert "artifacts" not in history_for_scope(row, "reflect:other")
    with pytest.raises(ValueError):
        present_file("report.txt")

    deactivate_plugin(installed)
    assert not plugin_detail(installed)["runtime"]["registered"]
    restored = PluginManager(PluginRegistry())
    wire_plugin_manager(restored)
    assert restored.restore_enabled() == 1 and plugin_detail(installed)["runtime"]["registered"]
    assert EntityRegistry.get("make_report") is not None
    assert await engine().tick() == []
    send.assert_awaited_once()
    assert len(get_history("publish")) == 1
