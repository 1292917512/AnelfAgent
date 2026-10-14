"""本地服务部署操作的隔离、回退、启动提示与配置持久化契约。"""

import asyncio
import json
import subprocess
import sys
from unittest.mock import AsyncMock, Mock

import pytest

from agent.retrieval import deployment, tools
from agent.retrieval.deployment_config import initialize, operation_lock, read_values, write_private


@pytest.fixture
def manager(tmp_path, monkeypatch):
    (tmp_path / "compose.yml").write_text("services: {}", encoding="utf-8")
    (tmp_path / "settings.yml.example").write_text("secret: __GENERATED_SECRET__", encoding="utf-8")
    monkeypatch.setattr(deployment.shutil, "which", lambda name: "docker")
    monkeypatch.setattr(deployment, "get_config", lambda key, default=None: default)
    monkeypatch.setattr(deployment.ConfigManager, "set_persisted", Mock())
    return deployment.RetrievalDeployment(tmp_path)


async def finish(manager):
    await manager._task
    return manager.snapshot()


async def test_default_start_is_nonblocking_and_only_fills_empty_endpoints(manager, monkeypatch):
    released = asyncio.Event()
    calls = []

    async def compose(*args, **kwargs):
        calls.append(args)
        await released.wait()
        return "healthy"

    monkeypatch.setattr(manager, "compose", compose)
    monkeypatch.setattr(deployment, "get_config", lambda key, default=None: "https://existing.example" if key == "retrieval_crawl4ai_base_url" else default)
    await manager.auto_start()
    await asyncio.sleep(0)
    assert manager.snapshot()["operation"]["status"] == "running"
    with pytest.raises(RuntimeError, match="执行中"):
        manager.start("stop")
    released.set()
    result = await finish(manager)
    assert result["operation"]["status"] == "succeeded"
    assert calls == [("pull", "--policy", "missing"), ("up", "-d", "--pull", "never", "--wait", "--wait-timeout", "180")]
    deployment.ConfigManager.set_persisted.assert_called_once_with({"retrieval_searxng_base_url": "http://127.0.0.1:18080"})
    original = (manager.root / ".env").read_text(encoding="utf-8")
    initialize(manager.root)
    assert (manager.root / ".env").read_text(encoding="utf-8") == original


async def test_missing_runtime_is_visible_to_ai_without_attempting_install(manager, monkeypatch):
    monkeypatch.setattr(deployment.shutil, "which", lambda name: None)
    monkeypatch.setattr(deployment, "get_deployment", lambda: manager)
    await manager.auto_start()
    assert manager._task is None and "Docker" in manager.notice
    status = json.loads(await tools.retrieval_services())
    assert status["requirements"]["docker_cli"] is False
    assert "install" in status["requirements"]["install_guide"]
    error = json.loads(await tools.retrieval_services("up"))
    assert error["cause"] == "config"
    monkeypatch.setattr(deployment, "get_config", lambda key, default=None: False)
    manager.notice = ""
    await manager.auto_start()
    assert manager.notice == ""


@pytest.mark.parametrize("fail_at", [1, 2, 3])
async def test_failed_update_preserves_config_and_reports_recovery(manager, monkeypatch, fail_at):
    initialize(manager.root)
    original = (manager.root / ".env").read_text(encoding="utf-8")
    secret = read_values(manager.root)["CRAWL4AI_API_TOKEN"]
    calls = []

    async def compose(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == fail_at or (fail_at == 3 and len(calls) == 2):
            raise RuntimeError("failed " + secret)
        return "ok"

    monkeypatch.setattr(manager, "compose", compose)
    manager.start("update", {"crawl4ai": "unclecode/crawl4ai:0.9.5"})
    result = await finish(manager)
    assert result["operation"]["status"] == "failed"
    assert secret not in json.dumps(result)
    assert (manager.root / ".env").read_text(encoding="utf-8") == original
    assert calls[0][0] == ("pull", "crawl4ai")
    assert len(calls) == (1 if fail_at == 1 else 3)
    assert not (manager.root / ".env.previous").exists()


async def test_successful_update_and_rollback_preserve_current_credentials(manager, monkeypatch):
    monkeypatch.setattr(manager, "compose", AsyncMock(return_value="healthy"))
    manager.start("update", {"crawl4ai": "unclecode/crawl4ai:0.9.5"})
    assert (await finish(manager))["operation"]["status"] == "succeeded"
    values = read_values(manager.root)
    old = values["CRAWL4AI_API_TOKEN"]
    env = manager.root / ".env"
    write_private(env, env.read_text(encoding="utf-8").replace(old, "rotated-secret"))
    manager.start("rollback")
    assert (await finish(manager))["operation"]["status"] == "succeeded"
    assert read_values(manager.root)["CRAWL4AI_IMAGE"].endswith(":0.9.4")
    assert read_values(manager.root)["CRAWL4AI_API_TOKEN"] == "rotated-secret"
    manager.start("connect")
    await finish(manager)
    assert deployment.ConfigManager.set_persisted.call_args.args[0]["retrieval_crawl4ai_api_key"] == "rotated-secret"


async def test_cli_lock_prevents_api_mutation_and_does_not_overwrite_active_journal(manager):
    initialize(manager.root)
    journal = manager.root / ".operation.json"
    saved = json.dumps({"id": "cli", "status": "running", "started_at": 1})
    journal.write_text(saved, encoding="utf-8")
    with operation_lock(manager.root):
        manager.start("init")
        result = await finish(manager)
        assert result["operation"]["status"] == "failed"
        assert journal.read_text(encoding="utf-8") == saved
    fresh = deployment.RetrievalDeployment(manager.root)
    assert fresh.snapshot()["operation"]["status"] == "interrupted"


async def test_shutdown_restores_version_config_and_marks_interrupted(manager, monkeypatch):
    initialize(manager.root)
    before = (manager.root / ".env").read_text(encoding="utf-8")
    starting = asyncio.Event()

    async def compose(*args, **kwargs):
        if args[0] == "up":
            starting.set()
            await asyncio.Event().wait()
        return "downloaded"

    monkeypatch.setattr(manager, "compose", compose)
    manager.start("update", {"crawl4ai": "unclecode/crawl4ai:0.9.5"})
    await starting.wait()
    await manager.close()
    assert manager.snapshot()["operation"]["status"] == "interrupted"
    assert (manager.root / ".env").read_text(encoding="utf-8") == before


@pytest.mark.parametrize("image", ["unclecode/crawl4ai:latest", "other/image:v1", "unclecode/crawl4ai:v1\nX=bad", "--bad"])
async def test_update_requires_explicit_official_image(manager, image):
    with pytest.raises(ValueError):
        manager.start("update", {"crawl4ai": image})
    assert not (manager.root / ".env").exists()


async def test_compose_runner_redacts_output_and_reaps_cancelled_process(manager, monkeypatch):
    initialize(manager.root)
    token = read_values(manager.root)["CRAWL4AI_API_TOKEN"]
    real_popen = subprocess.Popen
    processes = []
    commands = []
    script = "import os; print(os.environ['TEST_TOKEN'])"
    monkeypatch.setenv("TEST_TOKEN", token)
    monkeypatch.setenv("COMPOSE_FILE", "unexpected.yml")

    def popen(command, **kwargs):
        commands.append((command, kwargs))
        proc = real_popen([sys.executable, "-c", script], **kwargs)
        processes.append(proc)
        return proc

    monkeypatch.setattr(deployment.subprocess, "Popen", popen)
    assert (await manager.compose("ps")).strip() == "[redacted]"
    assert "COMPOSE_FILE" not in commands[0][1]["env"]
    assert commands[0][0][-1] == "ps" and "--project-name" in commands[0][0]
    script = "import time; time.sleep(30)"
    task = asyncio.create_task(manager.compose("up"))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert all(proc.poll() is not None for proc in processes)


async def test_live_progress_is_bounded_redacted_and_survives_until_next_model_call(manager, monkeypatch):
    initialize(manager.root)
    token = read_values(manager.root)["CRAWL4AI_API_TOKEN"]
    real_popen = subprocess.Popen
    release = manager.root / "release"
    script = ("import os,time,pathlib; print('x'*6000 + os.environ['TEST_TOKEN'], flush=True); "
              "p=pathlib.Path(os.environ['TEST_RELEASE']);\nwhile not p.exists(): time.sleep(0.02)")
    monkeypatch.setenv("TEST_TOKEN", token)
    monkeypatch.setenv("TEST_RELEASE", str(release))
    monkeypatch.setattr(deployment.subprocess, "Popen", lambda command, **kwargs: real_popen([sys.executable, "-c", script], **kwargs))
    manager.start("up")
    try:
        for _ in range(150):
            await asyncio.sleep(0.02)
            if manager.snapshot()["operation"]["log"]:
                break
        state = manager.snapshot()
        assert state["operation"]["status"] == "running"
        assert state["operation"]["phase"] == "pulling"
        assert "[redacted]" in state["operation"]["log"]
        assert len(state["operation"]["log"]) <= 4000
        assert state["operation"]["elapsed_seconds"] >= 0
        assert state["operation"]["output_idle_seconds"] is not None
        assert token not in (manager.root / ".operation.json").read_text(encoding="utf-8")
        release.touch()
        completed = await finish(manager)
        assert completed["operation"]["status"] == "succeeded"
        assert completed["operation"]["id"] in manager.context_status()
        assert "已完成" in manager.context_status()
        assert "安装" not in completed["next_step"]
        monkeypatch.setattr(deployment, "read_values", lambda root: pytest.fail("dynamic context must not read disk"))
        assert "已完成" in manager.context_status()
        manager._notice_until = deployment.time.monotonic() - 1
        assert manager.context_status() == ""
    finally:
        await manager.close()


async def test_inspection_reports_completion_during_query_without_overwriting_operation(manager, monkeypatch):
    initialize(manager.root)
    gate = asyncio.Event()

    async def compose(*args, **kwargs):
        if args[0] == "ps":
            gate.set()
            await manager._task
            return '{"Service":"crawl4ai","State":"running","Health":"healthy"}'
        await gate.wait()
        return "operation output"

    monkeypatch.setattr(manager, "compose", compose)
    manager.start("up")
    status = await manager.inspect()
    assert status["operation"]["status"] == "succeeded"
    assert status["operation"]["log"] == "operation output"
    assert status["containers"][0]["health"] == "healthy"
    old_id = status["operation"]["id"]
    manager.start("stop")
    assert old_id not in manager.context_status()
    assert "后台执行中" in manager.context_status()
    await finish(manager)
