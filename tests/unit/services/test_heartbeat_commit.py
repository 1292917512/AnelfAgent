"""心跳组合配置的提交点、失败重试与进程中断恢复。"""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi import FastAPI

from agent.config import BotConfigProvider
from agent.heartbeat.config import HeartbeatConfig, ScheduleMode, TaskSchedule
from core import file_utils
from core.path import ConfigPaths
from services.heartbeat import HeartbeatService, HeartbeatServiceError


@pytest.fixture
def provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BotConfigProvider:
    mind_path = tmp_path / "mind_config.json"
    mind_path.write_text('{"heartbeat_interval":300}', encoding="utf-8")
    monkeypatch.setattr(BotConfigProvider, "mind_config_path", property(lambda self: str(mind_path)))
    monkeypatch.setattr(BotConfigProvider, "_sync_mind_to_config_manager", lambda self: None)
    monkeypatch.setattr(ConfigPaths, "HEARTBEAT_CONFIG", str(tmp_path / "heartbeat.json"))
    result = BotConfigProvider()
    monkeypatch.setattr("agent.config.get_config_provider", lambda: result)
    cfg = HeartbeatConfig(enabled=True, task_schedules=[TaskSchedule("check", ScheduleMode.HEARTBEAT, beat_count=7)])
    cfg.save()
    monkeypatch.setattr("agent.heartbeat.config._instance", cfg)
    monkeypatch.setattr("services.heartbeat.get_runtime", lambda: None)
    return result


@pytest.mark.parametrize("failed", ["heartbeat.json", "mind_config.json"])
def test_failed_mixed_write_preserves_both_settings(
    failed: str, provider: BotConfigProvider, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    write = file_utils.atomic_write_text
    notified = Mock()
    monkeypatch.setattr(provider, "_sync_mind_to_config_manager", notified)

    def fail(path: Path, content: str) -> None:
        if path.name == failed:
            raise OSError("file busy")
        write(path, content)

    monkeypatch.setattr(file_utils, "atomic_write_text", fail)
    with pytest.raises(HeartbeatServiceError) as exc:
        HeartbeatService.save_config({"enabled": False, "interval_seconds": 420})
    assert exc.value.status_code == 503
    assert HeartbeatService.get_config()["enabled"]
    loaded = HeartbeatConfig.load()
    assert loaded.enabled and loaded.task_schedules[0].beat_count == 7
    assert json.loads((tmp_path / "mind_config.json").read_text("utf-8"))["heartbeat_interval"] == 300
    assert provider.mind.heartbeat_interval == 300
    notified.assert_not_called()
    if failed == "heartbeat.json":
        assert HeartbeatService.save_config({"interval_seconds": 420})
        assert provider.mind.heartbeat_interval == 420


@pytest.mark.parametrize(("stop_after", "committed"), [("heartbeat.json", False), ("mind_config.json", True)])
def test_restart_resolves_interrupted_commit(
    stop_after: str, committed: bool, provider: BotConfigProvider, tmp_path: Path,
) -> None:
    script = """
import os, sys
from pathlib import Path
from agent.config import get_config_provider
from services.heartbeat import HeartbeatService
folder, stop = sys.argv[1:]
assert Path(get_config_provider().mind_config_path).resolve() == Path(folder) / 'mind_config.json'
replace = os.replace
def stop_after_replace(src, dst):
    replace(src, dst)
    if Path(dst).name == stop:
        os._exit(47)
os.replace = stop_after_replace
HeartbeatService.save_config({'enabled': False, 'interval_seconds': 420})
"""
    proc = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), stop_after], timeout=30,
        env={**os.environ, "ANELF_CONFIG_DIR": str(tmp_path), "ANELF_DATA_DIR": str(tmp_path / "data")},
        capture_output=True,
    )
    assert proc.returncode == 47, proc.stderr
    file = tmp_path / "heartbeat.json"
    assert "_pending_schedule" in json.loads(file.read_text("utf-8"))
    assert json.loads(Path(provider.mind_config_path).read_text("utf-8"))["heartbeat_interval"] == (420 if committed else 300)
    provider.reload()
    provider.save_mind_config(heartbeat_interval=600)
    loaded = HeartbeatConfig.load()
    assert loaded.enabled is not committed
    assert loaded.task_schedules[0].beat_count == 7
    assert "_pending_schedule" not in json.loads(file.read_text("utf-8"))


async def test_reload_retry_only_applies_committed_config(
    provider: BotConfigProvider, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from web.routers.config import router

    engine = SimpleNamespace(reload_pending=False)
    calls = 0

    def reload_engine() -> None:
        nonlocal calls
        calls += 1
        engine.reload_pending = calls == 1
        if engine.reload_pending:
            raise OSError("task registry busy")

    engine.reload = reload_engine
    monkeypatch.setattr("services.heartbeat.get_runtime", lambda: SimpleNamespace(mind=SimpleNamespace(heartbeat_engine=engine)))
    app = FastAPI()
    app.include_router(router)
    observed = []
    monkeypatch.setattr(provider, "_sync_mind_to_config_manager", lambda: observed.append(HeartbeatService.get_config()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put("/config/heartbeat", json={"enabled": False, "interval_seconds": 420})
        assert response.json()["status"] == "saved_reload_pending"
        assert response.json()["config"]["reload_pending"] is True
        assert len(observed) == 1 and not observed[0]["enabled"] and observed[0]["interval_seconds"] == 420
        interval = await client.put("/config/heartbeat", json={"interval_seconds": 480})
        assert interval.json()["status"] == "saved_reload_pending" and calls == 1
        monkeypatch.setattr(file_utils, "atomic_write_text", lambda *args: pytest.fail("reload repeated a write"))
        assert (await client.post("/config/heartbeat/reload")).json() == {"status": "ok"}
        assert (await client.get("/config/heartbeat")).json()["reload_pending"] is False
        assert calls == 2


def test_failed_hot_reload_retains_current_schedule(provider: BotConfigProvider, tmp_path: Path) -> None:
    from agent.heartbeat.config import get_heartbeat_config, reload_heartbeat_config

    live = get_heartbeat_config()
    path = tmp_path / "heartbeat.json"
    data = path.read_bytes()
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError):
        reload_heartbeat_config()
    assert get_heartbeat_config() is live and live.task_schedules[0].beat_count == 7
    path.write_bytes(data)
    assert reload_heartbeat_config().to_dict() == live.to_dict()
