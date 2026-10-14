"""本地检索服务部署入口，供 Web、AI 与 CLI 共用。

模型可见：工具返回阶段、耗时和有界进度；动态区提供当前操作及短期终态，不冒充会话消息。
token 影响：动态状态最多 200 tokens，成功回执保留五分钟；工具日志最多 4000 字符，减少盲等和重复查询。
缓存影响：部署状态只进入工具回执和 volatile 动态区，不修改稳定上下文前缀。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from core.async_helper import spawn
from core.command import terminate_process_group
from core.config import ConfigManager, get_config
from core.lifecycle import Lifecycle
from core.log import log
from core.path import project_root
from core.sanitizer import sanitize_text

from .deployment_config import (
    DEFAULTS,
    IMAGE_KEYS,
    initialize,
    operation_lock,
    read_values,
    replace_images,
    validate_images,
    write_private,
)

ACTIONS = {"init", "up", "update", "rollback", "stop", "connect"}
INSTALL_GUIDE = "https://docs.docker.com/engine/install/"


class RetrievalDeployment:
    """固定 Compose 项目的单操作管理器，状态保留最近一次操作。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._task: asyncio.Task[None] | None = None
        self._operation: dict[str, Any] | None = None
        self._owns_operation = False
        self.notice = ""
        self._notice_until = 0.0

    def context_status(self) -> str:
        """零 I/O 读取当前操作状态，成功后的短期回执到期自动隐去。"""
        return "" if self._notice_until and time.monotonic() >= self._notice_until else self.notice

    def _safe(self, text: str) -> str:
        try:
            token = read_values(self.root).get("CRAWL4AI_API_TOKEN", "")
        except OSError:
            token = ""
        return sanitize_text(text.replace(token, "[redacted]") if token else text)[-4000:]

    def snapshot(self) -> dict[str, Any]:
        """读取部署状态与安装指引，不启动 Docker、不返回凭据。"""
        values = read_values(self.root)
        operation = self._operation
        path = self.root / ".operation.json"
        if (self._task is None or self._task.done()) and path.exists():
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(saved, dict) and (operation is None or saved.get("started_at", 0) >= operation["started_at"]):
                    operation = saved
                if operation and operation.get("status") == "running":
                    try:
                        with operation_lock(self.root):
                            operation = {**operation, "status": "interrupted", "phase": "interrupted",
                                         "error": "部署进程已退出，请刷新容器状态并使用 up 恢复。"}
                    except RuntimeError:
                        pass
            except (OSError, ValueError):
                operation = None
        available = bool(shutil.which("docker")) and (self.root / "compose.yml").exists()
        observed_at = time.time()
        if operation:
            operation = dict(operation)
            end = operation.get("finished_at") or observed_at
            operation["elapsed_seconds"] = round(max(0, end - operation["started_at"]), 1)
            operation["phase_elapsed_seconds"] = round(max(0, end - operation.get("phase_started_at", operation["started_at"])), 1)
            last_output = operation.get("last_output_at")
            operation["output_idle_seconds"] = round(max(0, end - last_output), 1) if last_output else None
        if operation and operation.get("status") == "running":
            next_step = "后台操作仍在执行，后续模型调用可从动态上下文看到本进程操作状态。无需 shell sleep 或重复提交；需要诊断时查询 status 的阶段与日志。"
        elif not available:
            next_step = "安装并启动 Docker Engine/Desktop 和 Compose v2，然后调用 retrieval_services(action='up')。"
        elif operation and operation.get("status") in {"failed", "interrupted"}:
            next_step = "结合 operation.error、log 和容器状态处理故障，再调用 up 恢复；Docker CLI 存在不代表引擎或网络正常。"
        elif operation and operation.get("status") == "succeeded" and operation.get("action") in {"up", "update", "rollback"}:
            next_step = "部署操作已完成。用 web_search(provider='searxng') 和 web_crawl 验证实际能力，是否切换默认提供者由任务决定。"
        else:
            next_step = "按需使用 up 启动本地服务；已有容器的当前可用性以 containers 和实际能力测试为准。"
        return {
            "observed_at": observed_at, "next_step": next_step,
            "available": available, "initialized": (self.root / ".env").exists(),
            "rollback_available": (self.root / ".env.previous").exists(),
            "images": {service: values[key] for service, key in IMAGE_KEYS.items()},
            "ports": {"crawl4ai": values["CRAWL4AI_PORT"], "searxng": values["SEARXNG_PORT"]},
            "operation": dict(operation) if operation else None,
            "requirements": {"platform": os.name, "docker_cli": bool(shutil.which("docker")),
                "install_guide": INSTALL_GUIDE},
        }

    def _progress(self, **changes: Any) -> None:
        if self._operation is not None:
            if changes.get("phase") and changes["phase"] != self._operation.get("phase"):
                changes["phase_started_at"] = time.time()
                if changes.get("status", self._operation["status"]) == "running":
                    changes.setdefault("log", "")
                    changes.setdefault("last_output_at", None)
            self._operation.update(changes)
            self._notice_until = 0.0
            identity = f"本地检索部署[{self._operation['id']}] {self._operation['action']}"
            if self._operation["status"] == "running":
                self.notice = f"{identity} 后台执行中（{self._operation['phase']}）。后续模型调用会同步状态，无需 shell sleep 或重复提交；需要诊断时用 retrieval_services(action='status') 查看进度。"
            elif self._operation["status"] == "succeeded":
                elapsed = max(0, self._operation["finished_at"] - self._operation["started_at"])
                self.notice = f"{identity} 已完成，耗时 {elapsed:.1f} 秒。这是后台操作回执，不代表用户任务或检索测试已完成。"
                self._notice_until = time.monotonic() + 300
            else:
                self.notice = f"{identity} {self._operation['status']}: {self._operation.get('error', '')[:400]}。用 retrieval_services 查询并诊断。"
            if self._owns_operation:
                try:
                    write_private(self.root / ".operation.json", json.dumps(self._operation, ensure_ascii=False))
                except OSError as exc:
                    log(f"检索部署状态无法写盘: {self._safe(str(exc))}", "WARNING", tag="检索")

    async def compose(self, *args: str, overrides: dict[str, str] | None = None, timeout: int = 240) -> str:
        """固定项目、固定参数执行 Docker，限时并在取消时回收命令进程。"""
        excluded = {*DEFAULTS, "CRAWL4AI_API_TOKEN", "COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "COMPOSE_PROFILES", "COMPOSE_ENV_FILES"}
        env = {key: value for key, value in os.environ.items() if key not in excluded}
        env.update(overrides or {})
        command = ["docker", "compose", "--project-name", "anelf-retrieval", "--file", str(self.root / "compose.yml"),
                   "--env-file", str(self.root / ".env"), *args]
        with tempfile.TemporaryDirectory(prefix="anelf-compose-") as temporary:
            path = Path(temporary) / "output"
            with path.open("wb") as output:
                proc = subprocess.Popen(command, cwd=self.root, env=env, stdin=subprocess.DEVNULL,
                                        stdout=output, stderr=output, start_new_session=os.name != "nt",
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            started = time.monotonic()
            last_size = 0
            text = ""

            def read_output() -> None:
                nonlocal last_size, text
                with path.open("rb") as reader:
                    reader.seek(0, os.SEEK_END)
                    size = reader.tell()
                    if size == last_size:
                        return
                    reader.seek(max(0, size - 16000))
                    text = self._safe(reader.read().decode("utf-8", errors="replace"))
                last_size = size
                if self._owns_operation and asyncio.current_task() is self._task:
                    self._progress(log=text, last_output_at=time.time())

            try:
                while proc.poll() is None:
                    read_output()
                    if time.monotonic() - started > timeout:
                        raise TimeoutError(f"Docker 操作超过 {timeout} 秒，请检查镜像下载网络和服务日志")
                    await asyncio.sleep(0.5)
                read_output()
                if proc.returncode:
                    raise RuntimeError(text or f"Docker 退出码 {proc.returncode}")
                return text
            finally:
                if proc.poll() is None:
                    await asyncio.to_thread(terminate_process_group, proc)
                proc.wait()

    async def inspect(self) -> dict[str, Any]:
        """按需读取实际容器状态，区分 Docker 不可用与尚未启动。"""
        result = self.snapshot()
        result["containers"] = []
        if not result["available"] or not result["initialized"]:
            return result
        try:
            raw = await self.compose("ps", "--all", "--format", "json", timeout=10)
            rows = json.loads(raw) if raw.lstrip().startswith("[") else [json.loads(line) for line in raw.splitlines() if line.strip()]
            result = self.snapshot()
            result["containers"] = [{key.lower(): row.get(key, "") for key in ("Service", "Image", "State", "Health")}
                                    for row in rows]
        except Exception as exc:
            result = self.snapshot()
            result["containers"] = []
            result["error"] = self._safe(str(exc))
        return result

    def start(self, action: str, images: dict[str, str] | None = None) -> dict[str, Any]:
        """校验请求并立即返回操作 ID，实际操作在后台执行。"""
        if action not in ACTIONS:
            raise ValueError(f"不支持的部署操作: {action}")
        images = {key: value.strip() for key, value in (images or {}).items() if value.strip()}
        if (action == "update") != bool(images):
            raise ValueError("update 必须指定镜像版本，其他操作不接受镜像参数")
        validate_images(images)
        if self._task is not None and not self._task.done():
            raise RuntimeError("已有部署操作执行中，请查询状态")
        if not (self.root / "compose.yml").exists():
            raise ValueError("未找到项目随附的 deploy/retrieval/compose.yml")
        if action not in {"init", "connect"} and not shutil.which("docker"):
            raise ValueError(f"主机缺少 Docker Engine/Desktop 与 Compose v2，安装指引: {INSTALL_GUIDE}")
        self._operation = {"id": uuid.uuid4().hex, "action": action, "status": "running", "phase": "queued",
                           "started_at": time.time(), "finished_at": None, "error": "", "log": "", "last_output_at": None}
        self._progress()
        self._task = spawn(self._run(action, images), name="retrieval.deployment")
        return self.snapshot()

    def _connect(self, *, replace: bool) -> None:
        """接入本机服务；自动启动仅填补空配置，手动连接才替换地址。"""
        values = read_values(self.root)
        updates: dict[str, str] = {}
        for service in IMAGE_KEYS:
            port = values[f"{service.upper()}_PORT"]
            if not port.isdigit() or not 1 <= int(port) <= 65535:
                raise ValueError(f"{service} 端口配置无效")
            key = f"retrieval_{service}_base_url"
            if replace or not get_config(key, ""):
                updates[key] = f"http://127.0.0.1:{port}"
                if service == "crawl4ai":
                    updates["retrieval_crawl4ai_api_key"] = values["CRAWL4AI_API_TOKEN"]
        if updates:
            ConfigManager.set_persisted(updates)

    async def _run(self, action: str, images: dict[str, str]) -> None:
        try:
            with operation_lock(self.root):
                self._owns_operation = True
                try:
                    await self._execute(action, images)
                finally:
                    self._owns_operation = False
        except Exception as exc:
            self._progress(status="failed", error=self._safe(str(exc)), finished_at=time.time())

    async def _execute(self, action: str, images: dict[str, str]) -> None:
        try:
            self._progress(phase="preparing")
            initialize(self.root)
            if action == "connect":
                self._connect(replace=True)
            elif action in {"update", "rollback"}:
                await self._change_version(action, images)
            elif action in {"up", "stop"}:
                if action == "up":
                    self._progress(phase="pulling")
                    await self.compose("pull", "--policy", "missing", timeout=900)
                self._progress(phase="starting" if action == "up" else "stopping")
                args = ("up", "-d", "--pull", "never", "--wait", "--wait-timeout", "180") if action == "up" else ("stop",)
                self._progress(log=await self.compose(*args))
                if action == "up":
                    self._connect(replace=False)
            self._progress(status="succeeded", phase="done", finished_at=time.time())
        except asyncio.CancelledError:
            self._progress(status="interrupted", phase="interrupted", finished_at=time.time(),
                           error="服务关闭中断部署。请查看容器状态，使用 up 重新应用当前镜像配置。")
            raise
        except Exception as exc:
            self._progress(status="failed", error=self._safe(str(exc)), finished_at=time.time())

    async def _change_version(self, action: str, images: dict[str, str]) -> None:
        env_file, previous = self.root / ".env", self.root / ".env.previous"
        before = env_file.read_text(encoding="utf-8")
        if action == "rollback":
            if not previous.exists():
                raise ValueError("没有可回退的版本配置")
            old = dict(line.split("=", 1) for line in previous.read_text(encoding="utf-8").splitlines() if "=" in line)
            images = {service: old[key] for service, key in IMAGE_KEYS.items()}
        after = replace_images(before, images)
        services = tuple(images)
        self._progress(phase="pulling")
        await self.compose("pull", *services, overrides={IMAGE_KEYS[key]: value for key, value in images.items()}, timeout=900)
        write_private(env_file, after)
        try:
            self._progress(phase="starting")
            output = await self.compose("up", "-d", "--wait", "--wait-timeout", "180", *services)
        except BaseException as exc:
            write_private(env_file, before)
            if isinstance(exc, asyncio.CancelledError):
                raise
            self._progress(phase="recovering")
            try:
                await self.compose("up", "-d", "--wait", "--wait-timeout", "180", *services)
            except Exception as recovery:
                raise RuntimeError(f"更新失败: {exc}；已恢复版本配置，但容器恢复也失败: {recovery}") from exc
            raise RuntimeError(f"更新失败，已恢复之前版本: {exc}") from exc
        if action == "update":
            write_private(previous, before)
        self._progress(log=output)

    async def auto_start(self) -> None:
        """默认后台启动已安装的本地服务；环境缺失不阻塞其他模块。"""
        if not get_config("retrieval_local_autostart", True):
            return
        try:
            self.start("up")
        except (ValueError, RuntimeError) as exc:
            self.notice = f"本地检索服务未启动: {exc}。可用 retrieval_services(action='status') 获取环境与装配指引。"

    async def close(self) -> None:
        """取消未完成的命令并回收进程，已启动的服务容器继续独立运行。"""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


def get_deployment() -> RetrievalDeployment:
    """取得由 Lifecycle 持有的本地检索部署管理器。"""
    instance = Lifecycle.get("retrieval_deployment")
    if not isinstance(instance, RetrievalDeployment):
        instance = RetrievalDeployment(Path(project_root()) / "deploy" / "retrieval")
        Lifecycle.register("retrieval_deployment", instance, cleanup=instance.close, on_start=instance.auto_start)
    return instance
