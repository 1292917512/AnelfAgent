"""devops 重启请求守卫单元测试（守护检测 / 重复去重 / 来源透传 / 重启交接）。"""
from __future__ import annotations

import asyncio

import pytest

from entities.devops import service, tools


@pytest.fixture(autouse=True)
def _reset_restart_state(monkeypatch: pytest.MonkeyPatch):
    """用例间复位重启挂起旗标，避免全局状态串扰。"""
    monkeypatch.setattr(service, "_restart_pending", False)


@pytest.fixture
def handoff_file(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """把交接状态文件指到临时目录，返回文件路径。"""
    path = tmp_path / "restart_handoff.json"
    monkeypatch.setattr(service, "_handoff_path", lambda: path)
    return path


def test_request_restart_refused_without_supervisor(monkeypatch: pytest.MonkeyPatch):
    """进程非 start.sh 守护拉起时拒绝重启（防止"重启变关机"）。"""
    monkeypatch.setattr(service, "_is_supervised", lambda: False)
    scheduled: list[bool] = []
    monkeypatch.setattr(service, "schedule_restart", lambda: scheduled.append(True))

    result = service.request_restart(source="tool:restart_app")
    assert result["ok"] is False
    assert result["error"] == "no_supervisor"
    assert scheduled == []


def test_request_restart_dedupes_pending(monkeypatch: pytest.MonkeyPatch):
    """重复请求只调度一次，后续返回 already_pending。"""
    monkeypatch.setattr(service, "_is_supervised", lambda: True)
    scheduled: list[bool] = []
    monkeypatch.setattr(service, "schedule_restart", lambda: scheduled.append(True))

    first = service.request_restart(source="http:127.0.0.1")
    second = service.request_restart(source="tool:restart_app")
    assert first["ok"] and first["restarting"]
    assert second["ok"] and second.get("already_pending") is True
    assert len(scheduled) == 1


@pytest.mark.parametrize("source", ["tool:restart_app", "http:127.0.0.1", "host"])
def test_restart_requests_shutdown_before_arming_watchdog(monkeypatch: pytest.MonkeyPatch, source: str) -> None:
    monkeypatch.setattr(service, "_is_supervised", lambda: True)
    events: list[str] = []
    monkeypatch.setattr(service.Lifecycle, "request_shutdown", lambda restart: events.append("shutdown"))
    monkeypatch.setattr(service, "_arm_restart_watchdog", lambda: events.append("watchdog"))
    assert service.request_restart(source=source)["ok"]
    assert events == ["shutdown", "watchdog"]


def test_restart_watchdog_forces_exit_42(monkeypatch: pytest.MonkeyPatch):
    """数据库关闭后才允许看门狗兜底退出。"""
    monkeypatch.setattr(service.Lifecycle, "shutdown_blockers", lambda: [])
    monkeypatch.setattr(service.time, "sleep", lambda seconds: None)
    exits: list[int] = []
    monkeypatch.setattr("os._exit", exits.append)

    service._restart_watchdog()
    assert exits == [42]


def test_restart_watchdog_waits_for_native_database(monkeypatch: pytest.MonkeyPatch):
    states = iter([["cognee_memory"], ["cognee_memory"], []])
    monkeypatch.setattr(service.Lifecycle, "shutdown_blockers", lambda: next(states))
    events = []
    monkeypatch.setattr(service.time, "sleep", lambda seconds: events.append("wait"))
    monkeypatch.setattr("os._exit", lambda code: events.append(code))
    service._restart_watchdog()
    assert events == ["wait", "wait", "wait", 42]


def test_handoff_roundtrip(handoff_file):
    """交接写入后可读取，且只消费一次。"""
    assert service.consume_handoff() is None
    service.write_handoff("user_webui:web_user#chat_1", "webui", "回来后继续整理", "restart_app")
    data = service.consume_handoff()
    assert data is not None
    assert data["scope"] == "user_webui:web_user#chat_1"
    assert data["channel"] == "webui"
    assert data["message"] == "回来后继续整理"
    assert data["source"] == "restart_app"
    assert service.consume_handoff() is None


def test_handoff_message_truncated(handoff_file):
    """超长留言按上限截断，防止异常输入灌爆状态文件。"""
    service.write_handoff("user_qq:1", "qq", "x" * 5000, "restart_app")
    data = service.consume_handoff()
    assert data is not None
    assert len(data["message"]) == service._HANDOFF_MESSAGE_LIMIT


def test_consume_handoff_drops_stale(handoff_file):
    """陈旧交接（超 TTL）只清理不投递，防止过期留言在延迟启动时误触发。"""
    service.write_handoff("user_qq:1", "qq", "过期留言", "restart_app")
    import json
    import time

    payload = json.loads(handoff_file.read_text(encoding="utf-8"))
    payload["ts"] = time.time() - service._HANDOFF_TTL_SECONDS - 10
    handoff_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    assert service.consume_handoff() is None
    assert not handoff_file.exists()  # 文件已清理，后续启动不会重复处理


def test_already_pending_empty_message_keeps_handoff(
        monkeypatch: pytest.MonkeyPatch, handoff_file):
    """重启已排定时，空留言的重复调用不覆盖已写入的交接。"""
    monkeypatch.setattr(service, "_is_supervised", lambda: True)
    monkeypatch.setattr(service, "schedule_restart", lambda: None)

    first = service.request_restart(
        source="tool:restart_app",
        handoff={"scope": "user_qq:1", "channel": "qq", "message": "旧留言"},
    )
    second = service.request_restart(
        source="tool:restart_app",
        handoff={"scope": "user_qq:1", "channel": "qq", "message": "  "},
    )
    assert first["ok"] and second.get("already_pending") is True
    data = service.consume_handoff()
    assert data is not None and data["message"] == "旧留言"


def test_request_restart_with_handoff_persists_state(
        monkeypatch: pytest.MonkeyPatch, handoff_file):
    """重启确认排定后交接才落盘。"""
    monkeypatch.setattr(service, "_is_supervised", lambda: True)
    monkeypatch.setattr(service, "schedule_restart", lambda: None)

    result = service.request_restart(
        source="tool:restart_app",
        handoff={"scope": "user_qq:1", "channel": "qq", "message": "继续"},
    )
    assert result["ok"]
    data = service.consume_handoff()
    assert data is not None and data["message"] == "继续"


def test_request_restart_refused_writes_no_handoff(
        monkeypatch: pytest.MonkeyPatch, handoff_file):
    """重启被拒绝（无守护）时不写交接，避免残留状态在无关重启时误触发。"""
    monkeypatch.setattr(service, "_is_supervised", lambda: False)

    result = service.request_restart(
        source="tool:restart_app",
        handoff={"scope": "user_qq:1", "channel": "qq", "message": "继续"},
    )
    assert result["ok"] is False
    assert service.consume_handoff() is None


def test_already_pending_updates_handoff(
        monkeypatch: pytest.MonkeyPatch, handoff_file):
    """重启已排定时重复调用允许补充/更新交接留言。"""
    monkeypatch.setattr(service, "_is_supervised", lambda: True)
    monkeypatch.setattr(service, "schedule_restart", lambda: None)

    first = service.request_restart(
        source="tool:restart_app",
        handoff={"scope": "user_qq:1", "channel": "qq", "message": "旧留言"},
    )
    second = service.request_restart(
        source="tool:restart_app",
        handoff={"scope": "user_qq:1", "channel": "qq", "message": "新留言"},
    )
    assert first["ok"] and second.get("already_pending") is True
    data = service.consume_handoff()
    assert data is not None and data["message"] == "新留言"


def test_restart_handoff_watcher_delivers(
        monkeypatch: pytest.MonkeyPatch, handoff_file):
    """启动时消费交接并向原会话推送"重启成功 + 留言"（唤醒思维）。"""
    service.write_handoff("user_qq:1", "qq", "继续处理", "restart_app")
    pushed: list[dict] = []

    def _fake_push(content: str, source: str, scope: str = "",
                 channel: str = "", trigger: bool = True) -> bool:
        pushed.append({"content": content, "source": source, "scope": scope,
                       "channel": channel, "trigger": trigger})
        return True

    monkeypatch.setattr(tools, "push_notify", _fake_push)
    monkeypatch.setattr(tools, "_WAKE_DELAY_SECONDS", 0)
    watcher = tools.RestartHandoffWatcher()

    async def _run() -> None:
        await watcher.on_start()
        if watcher._tasks:
            await asyncio.gather(*watcher._tasks)

    asyncio.run(_run())
    assert len(pushed) == 1
    assert pushed[0]["scope"] == "user_qq:1"
    assert pushed[0]["channel"] == "qq"
    assert pushed[0]["trigger"] is True
    assert "重启" in pushed[0]["content"] and "继续处理" in pushed[0]["content"]
    assert service.consume_handoff() is None


def test_restart_handoff_watcher_no_handoff(
        monkeypatch: pytest.MonkeyPatch, handoff_file):
    """无交接（正常启动）时不推送。"""
    pushed: list[dict] = []
    monkeypatch.setattr(
        tools, "push_notify",
        lambda content, source, scope="", channel="", trigger=True: pushed.append({}) or True)
    watcher = tools.RestartHandoffWatcher()

    asyncio.run(watcher.on_start())
    assert pushed == []
    assert watcher._tasks == set()
