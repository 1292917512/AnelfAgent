"""使用真实受管子进程与模拟输入后端验证桌面边界；绝不发送真实桌面输入。"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import threading
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from agent.mind.interrupt import InterruptRegistry
from core.cancellation import ToolCancelled, get_execution_signal
from entities.operation import desktop, desktop_process, executor, framework, tools
from entities.operation.desktop_actions import ActionStopped, DesktopActions
from entities.operation.desktop_takeover import InputMonitor, physical_input


@pytest.fixture
def isolated_worker(tmp_path, monkeypatch):
    for name in ("desktop_worker.py", "desktop_actions.py", "desktop_takeover.py"):
        shutil.copy(Path(desktop.__file__).with_name(name), tmp_path / name)
    log = tmp_path / "inputs.jsonl"
    (tmp_path / "pyautogui.py").write_text('''
import json, time, os, subprocess, sys
from pathlib import Path
FAILSAFE = True
KEYBOARD_KEYS = ["ctrl", "shift", "enter", "a", "s"]
PAUSE = 0
def record(action, *args):
    with Path(__file__).with_name("inputs.jsonl").open("a") as f:
        f.write(json.dumps([action, *args]) + "\\n")
def size(): return (1920, 1080)
def position(): return (100, 100)
def moveTo(x, y): record("move", x, y)
def keyDown(key): record("down", key)
def keyUp(key): record("up", key)
def mouseDown(button): record("down", button)
def mouseUp(button): record("up", button)
def scroll(n, x, y): record("scroll", n)
def write(char):
    record("write", char)
    if char == "!":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        record("child", child.pid)
        time.sleep(60)
    time.sleep(.04)
''', encoding="utf-8")
    monkeypatch.setattr(desktop_process, "WORKER", tmp_path / "desktop_worker.py")
    monkeypatch.setattr(desktop, "get_config_bool", lambda key, default: False if key == "operation_user_takeover" else default)
    monkeypatch.setattr(desktop, "runtime_ready", lambda: True)
    controller = desktop.DesktopController()
    monkeypatch.setattr(desktop, "_controller", controller)
    monkeypatch.setattr(framework, "_store_path", lambda: tmp_path / "operations.json")
    executor._history.clear()
    return controller, log


async def until(predicate, timeout=5):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(.01)


async def test_cancel_waits_for_process_and_preserves_partial_history(isolated_worker):
    controller, log = isolated_worker
    task = asyncio.create_task(executor.execute("desktop.type", {"text": "abcdefghij", "interval": .1}))
    await until(lambda: controller.active and controller.active.progress["completed"] >= 2)
    pid = controller.active.progress["pid"]
    task.cancel()
    await asyncio.sleep(.01)
    task.cancel()
    with pytest.raises(ToolCancelled) as caught:
        await task
    receipt = json.loads(caught.value.result)
    assert receipt["status"] == "cancelled"
    assert 1 <= receipt["completed"] < receipt["total"] == 10
    assert receipt["process_stopped"] and receipt["input_released"]
    assert executor.history(1)[0]["receipt"]["completed"] == receipt["completed"]
    import psutil
    assert not psutil.pid_exists(pid)
    before = log.read_text()
    await asyncio.sleep(.15)
    assert log.read_text() == before
    assert controller.active is None


async def test_stop_during_creation_never_dispatches_input(isolated_worker, monkeypatch):
    from contextlib import asynccontextmanager
    controller, log = isolated_worker
    created, release = asyncio.Event(), asyncio.Event()
    original = desktop_process.managed_process

    @asynccontextmanager
    async def delayed(*args, **kwargs):
        async with original(*args, **kwargs) as process:
            created.set()
            await release.wait()
            yield process

    monkeypatch.setattr(desktop_process, "managed_process", delayed)
    task = asyncio.create_task(controller.run("type", {"text": "never"}))
    await asyncio.wait_for(created.wait(), 3)
    task.cancel()
    await until(lambda: controller.active.stop.is_set())
    release.set()
    with pytest.raises(ToolCancelled) as caught:
        await task
    receipt = json.loads(caught.value.result)
    assert receipt["input_outcome"] == "not_started" and receipt["completed"] == 0
    assert not log.exists()


async def test_hung_worker_tree_is_killed_and_not_reported_as_complete(isolated_worker):
    controller, log = isolated_worker
    task = asyncio.create_task(controller.run("type", {"text": "!abcd"}))
    await until(lambda: log.exists() and '"child"' in log.read_text())
    pid = controller.active.progress["pid"]
    child = next(json.loads(line)[1] for line in log.read_text().splitlines() if '"child"' in line)
    task.cancel()
    with pytest.raises(ToolCancelled) as caught:
        await asyncio.wait_for(task, 5)
    receipt = json.loads(caught.value.result)
    assert receipt["input_outcome"] == "unknown"
    assert receipt["completed"] == 0 and not receipt["ok"]
    import psutil
    assert not psutil.pid_exists(pid)
    assert not psutil.pid_exists(child) or psutil.Process(child).status() == psutil.STATUS_ZOMBIE


async def test_exclusive_lease_covers_screen_verification(isolated_worker):
    controller, _ = isolated_worker
    entered, finish = asyncio.Event(), asyncio.Event()

    async def observer(receipt):
        entered.set()
        await finish.wait()
        return {**receipt, "verification": "captured"}

    first = asyncio.create_task(controller.run("key", {"keys": "enter"}, observer))
    await asyncio.wait_for(entered.wait(), 3)
    rejected = await controller.run("key", {"keys": "enter"})
    assert rejected["status"] == "not_started"
    result = await controller.control("takeover", controller.revision)
    assert result["ok"] and result["control"]["state"] == "human"
    receipt = await first
    assert receipt["status"] == "cancelled" and receipt["verification"] == "cancelled"
    assert receipt["input_outcome"] == "completed"
    assert (await controller.run("key", {"keys": "enter"}))["status"] == "not_started"
    assert not (await controller.control("release", controller.revision - 1))["ok"]
    assert (await controller.control("release", controller.revision))["ok"]
    assert controller.state()["state"] == "idle"


async def test_sdk_signal_drives_same_worker_and_old_signal_stays_cancelled(isolated_worker):
    controller, _ = isolated_worker
    registry = InterruptRegistry()
    with registry.execution("run", "scope"):
        signal = get_execution_signal()
        assert signal is registry.signal("scope")
        task = asyncio.create_task(controller.run("type", {"text": "abcdef", "interval": .1}))
        await until(lambda: controller.active and controller.active.progress["completed"] >= 1)
        registry.request("scope", "频道请求停止")
        receipt = await task
        assert receipt["status"] == "cancelled" and "频道" in receipt["error"]
    with registry.execution("new", "scope"):
        assert signal.cancelled and not get_execution_signal().cancelled


async def test_shutdown_closes_entry_and_reaps_active_work(isolated_worker):
    controller, _ = isolated_worker
    running = asyncio.create_task(controller.run("type", {"text": "abcdef", "interval": .1}))
    await until(lambda: controller.active and controller.active.progress["completed"] >= 1)
    await controller.close()
    assert (await running)["status"] == "cancelled"
    assert (await controller.run("key", {"keys": "enter"}))["status"] == "not_started"


async def test_timeout_preserves_progress_and_releases_inputs(isolated_worker, monkeypatch):
    controller, _ = isolated_worker
    monkeypatch.setattr(desktop, "get_config_int", lambda *_: 1)
    receipt = await controller.run("type", {"text": "abcdefghijklmnopqrstuvwxyz", "interval": .1})
    assert receipt["status"] == "cancelled" and receipt["cause"] == "timeout"
    assert 0 < receipt["completed"] < receipt["total"]
    assert receipt["process_stopped"] and receipt["input_released"]


@pytest.mark.parametrize("interrupt", ["cancel", "timeout"])
async def test_entity_registry_preserves_cleanup_receipt(isolated_worker, interrupt):
    from core.entity import EntityRegistry
    controller, _ = isolated_worker

    async def invoke():
        return json.dumps(await controller.run("type", {"text": "abcdefghijklmnop", "interval": .1}))

    name = "test_owned_desktop_receipt"
    EntityRegistry.register_tool(name=name, func=invoke, description="test", group="test", params=[])
    task = asyncio.create_task(EntityRegistry.execute_tool(name, timeout=.6 if interrupt == "timeout" else 10))
    await until(lambda: controller.active and controller.active.progress["completed"] >= 1)
    if interrupt == "cancel":
        task.cancel()
        with pytest.raises(ToolCancelled) as caught:
            await task
        receipt = json.loads(caught.value.result)
    else:
        receipt = json.loads(await task)
    assert receipt["status"] == "cancelled"
    assert receipt["process_stopped"] and receipt["input_released"]
    assert 0 < receipt["completed"] < receipt["total"]


async def test_unconfirmed_input_release_holds_user_control(isolated_worker, monkeypatch):
    controller, _ = isolated_worker
    worker = AsyncMock(return_value={"status": "unknown", "input_released": False})
    monkeypatch.setattr(desktop, "run_worker", worker)
    receipt = await controller.run("key", {"keys": "enter"})
    assert not receipt["input_released"] and not receipt["ok"]
    assert controller.state()["state"] == "human"
    assert (await controller.run("key", {"keys": "enter"}))["status"] == "not_started"
    assert worker.await_count == 2


async def test_real_worker_probe_and_capture_failure_are_explicit(isolated_worker, monkeypatch):
    controller, log = isolated_worker
    assert (await controller.probe())["screen"] == [1920, 1080]
    monkeypatch.setattr(tools, "vision_look", AsyncMock(return_value='{"error":"unavailable"}'))
    result = await tools.desktop_act("key", keys="enter", verify="on")
    assert json.loads(result)["verification"] == "unavailable"
    assert executor.history(1)[0]["receipt"]["verification"] == "unavailable"
    assert '"up", "enter"' in log.read_text()


def test_cancelled_hotkey_releases_only_owned_inputs():
    from types import SimpleNamespace
    stop = threading.Event()
    released = []
    backend = SimpleNamespace(KEYBOARD_KEYS=["ctrl", "s"], keyDown=lambda _: stop.set(),
                              keyUp=released.append, FAILSAFE=True)
    engine = DesktopActions(backend, stop, lambda _: None)
    with pytest.raises(ActionStopped):
        try:
            engine.run("hotkey", {"keys": "ctrl+s"})
        finally:
            engine.release()
    assert released == ["ctrl"]
    assert not engine.keys and backend.FAILSAFE


@pytest.mark.parametrize("kind,flag", [("keyboard", 0x10), ("mouse", 1)])
def test_physical_input_does_not_confuse_automation(kind, flag):
    assert physical_input(kind, 0)
    assert not physical_input(kind, flag)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 原生接管钩子")
def test_native_monitor_installs_and_unhooks_without_sending_input():
    monitor = InputMonitor(threading.Event())
    try:
        monitor.start()
        assert monitor.ready.is_set() and not monitor.error
    finally:
        monitor.close()
    assert not monitor.thread.is_alive()
