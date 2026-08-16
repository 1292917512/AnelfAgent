"""NoneBot worker 运行时管理 — venv 引导 / 子进程生命周期 / 包安装 / 日志环。

worker 以独立 venv 中的解释器运行 ``worker/bot.py``：
- venv 由 uv 创建（缺省回退 ``python -m venv``），基线包 nonebot2[fastapi,aiohttp]
  + websockets，插件的 pip 安装全部落在该 venv，与主应用依赖完全隔离；
- spawn 时注入 ``ANELF_BRIDGE_WS_URL`` / ``ANELF_BRIDGE_TOKEN``，worker 回连
  频道适配器的桥接 WS 服务；
- 崩溃自动重启（指数退避），手动停止不触发；
- stdout/stderr 泵入内存日志环（与 worker 经 WS 上报的日志行共用一个环）。
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional

from core.lifecycle import Lifecycle
from core.log import log

from .config import KNOWN_ADAPTERS

# worker venv 基线包（spec 变化时重新安装）
_BASELINE_PACKAGES: List[str] = [
    "nonebot2[fastapi,aiohttp]>=2.5.0",
    "websockets>=12.0",
]
# 基线标记放在 venv 目录之外：uv 创建的 venv 目录为只执行权限（防误改），
# 不能向其中写入标记文件
_BASELINE_MARKER = ".venv_baseline"


def _baseline_marker_path() -> Path:
    return runtime_dir() / _BASELINE_MARKER

_STOP_TIMEOUT_SECONDS = 10.0
_RESTART_BACKOFF_BASE = 1.0
_RESTART_BACKOFF_MAX = 60.0
_STABLE_RESET_SECONDS = 300.0

WORKER_SCRIPT = Path(__file__).parent / "worker" / "bot.py"


def _is_windows() -> bool:
    return os.name == "nt"


def runtime_dir() -> Path:
    """worker 运行时目录（venv / .env / config.json / 注册表快照，绝对路径）。"""
    from core.path import ConfigPaths

    return Path(ConfigPaths.NONEBOT_DIR).resolve()


def venv_dir() -> Path:
    return runtime_dir() / "venv"


def venv_python() -> Path:
    """venv 内 Python 解释器路径。"""
    if _is_windows():
        return venv_dir() / "Scripts" / "python.exe"
    return venv_dir() / "bin" / "python"


def find_uv(override: str = "") -> Optional[str]:
    """探测 uv 可执行文件（配置覆盖优先）。"""
    if override:
        return override if Path(override).exists() else None
    return shutil.which("uv")


def build_venv_create_command(uv_exec: Optional[str], python_exec: str, target: Path) -> List[str]:
    """构造 venv 创建命令（uv 存在走 uv venv，否则 python -m venv）。"""
    if uv_exec:
        return [uv_exec, "venv", "--python", python_exec, str(target)]
    return [sys.executable, "-m", "venv", str(target)]


def build_install_command(
    uv_exec: Optional[str], python: Path, packages: List[str]
) -> List[str]:
    """构造包安装命令（uv 存在走 uv pip --python，否则 venv 自带 pip）。"""
    if uv_exec:
        return [uv_exec, "pip", "install", "--python", str(python), *packages]
    return [str(python), "-m", "pip", "install", *packages]


def build_uninstall_command(
    uv_exec: Optional[str], python: Path, packages: List[str]
) -> List[str]:
    """构造包卸载命令。"""
    if uv_exec:
        return [uv_exec, "pip", "uninstall", "--python", str(python), "-y", *packages]
    return [str(python), "-m", "pip", "uninstall", "-y", *packages]


def build_worker_files(cfg: Dict[str, Any]) -> Dict[str, str]:
    """根据频道配置生成 worker 文件内容（.env / config.json）。

    Args:
        cfg: 频道配置字典（adapters / plugins / nonebot_env / intercept_all /
            worker_host / worker_port 等）

    Returns:
        ``{".env": 文本, "config.json": 文本}``
    """
    adapter_keys: List[str] = list(cfg.get("adapters") or [])
    adapter_entries: List[Dict[str, str]] = []
    for key in adapter_keys:
        info = KNOWN_ADAPTERS.get(key)
        if info is not None:
            adapter_entries.append(
                {"key": key, "import": info["import"], "class": info["class"]}
            )
        elif key.startswith("nonebot.adapters."):
            # 注册表动态适配器：模块路径即导入目标
            adapter_entries.append({"key": key, "import": key, "class": "Adapter"})
        else:
            log(f"NoneBot Bridge: 未知适配器 key '{key}'，已跳过", "WARNING")

    env_lines: List[str] = [
        "# 由 AnelfAgent NoneBot Bridge 自动生成，勿手工编辑",
        "DRIVER=~fastapi+~aiohttp",
        f"HOST={cfg.get('worker_host', '127.0.0.1')}",
        f"PORT={int(cfg.get('worker_port', 8198))}",
        "LOG_LEVEL=INFO",
    ]
    for env_key, env_value in (cfg.get("nonebot_env") or {}).items():
        if isinstance(env_value, (list, dict)):
            env_lines.append(f"{env_key}={env_value!r}".replace("'", '"'))
        else:
            env_lines.append(f"{env_key}={env_value}")

    worker_config = {
        "wire_version": 3,
        "adapters": adapter_entries,
        "plugins": list(cfg.get("plugins") or []),
        "intercept_all": bool(cfg.get("intercept_all", False)),
    }

    return {
        ".env": "\n".join(env_lines) + "\n",
        "config.json": json.dumps(worker_config, ensure_ascii=False, indent=2),
    }


class NoneBotRuntime:
    """worker venv 与子进程生命周期管理器（单例，经 get_nonebot_runtime 获取）。"""

    def __init__(self) -> None:
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._watchdog_task: Optional[asyncio.Task[None]] = None
        self._pump_tasks: List[asyncio.Task[None]] = []
        self._want_running: bool = False
        self._auto_restart: bool = True
        self._token: str = ""
        self._started_at: float = 0.0
        self._restart_count: int = 0

        self._install_lock = asyncio.Lock()
        self._install_state: Dict[str, Any] = {
            "running": False, "packages": [], "logs": [], "error": "", "ok": None,
        }
        ring_size = 500
        self._log_ring: Deque[str] = deque(maxlen=ring_size)
        self._log_subscribers: List[asyncio.Queue[str]] = []

        self._uv_exec_cache: Optional[str] = None
        self._uv_probed: bool = False

        # 崩溃自动重启回调（频道适配注入：刷新 worker 文件并重新 spawn）
        self.on_worker_restart: Optional[Callable[[], Awaitable[None]]] = None

    # ------------------------------------------------------------------
    # venv
    # ------------------------------------------------------------------

    def is_venv_ready(self) -> bool:
        """venv 是否已创建且基线包就绪。"""
        marker = _baseline_marker_path()
        if not marker.exists() or not venv_python().exists():
            return False
        try:
            return marker.read_text("utf-8").strip() == "\n".join(_BASELINE_PACKAGES)
        except OSError:
            return False

    async def ensure_venv(self, python_exec: str = "", uv_exec: str = "") -> None:
        """创建/修复 worker venv 并安装基线包（幂等，已就绪时零开销）。"""
        if self.is_venv_ready():
            return

        loop = asyncio.get_running_loop()
        uv = self._resolve_uv(uv_exec)
        python = python_exec or sys.executable
        target = venv_dir()

        self._log("创建 worker venv ...")
        await loop.run_in_executor(
            None, lambda: target.mkdir(parents=True, exist_ok=True)
        )
        create_cmd = build_venv_create_command(uv, python, target)
        await self._run_setup_command(create_cmd, "venv 创建")

        self._log(f"安装基线包: {', '.join(_BASELINE_PACKAGES)}")
        install_cmd = build_install_command(uv, venv_python(), list(_BASELINE_PACKAGES))
        await self._run_setup_command(install_cmd, "基线包安装")

        marker = _baseline_marker_path()
        marker.write_text("\n".join(_BASELINE_PACKAGES), "utf-8")
        self._log("worker venv 就绪")

    # ------------------------------------------------------------------
    # worker 文件与进程
    # ------------------------------------------------------------------

    def write_worker_files(self, cfg: Dict[str, Any]) -> None:
        """把频道配置渲染为 worker 运行时文件（.env / config.json）。"""
        runtime_dir().mkdir(parents=True, exist_ok=True)
        for name, content in build_worker_files(cfg).items():
            (runtime_dir() / name).write_text(content, "utf-8")

    @property
    def token(self) -> str:
        """当前 worker 回连令牌（每次 spawn 轮换）。"""
        return self._token

    def is_process_alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start_worker(
        self,
        bridge_ws_port: int,
        *,
        auto_restart: bool = True,
        python_exec: str = "",
        uv_exec: str = "",
    ) -> bool:
        """启动 worker 子进程（需先 write_worker_files 且 venv 就绪）。"""
        await self.ensure_venv(python_exec=python_exec, uv_exec=uv_exec)

        if self.is_process_alive():
            return True

        self._auto_restart = auto_restart
        self._token = secrets.token_urlsafe(24)
        self._want_running = True
        self._started_at = time.time()

        env = dict(os.environ)
        env["ANELF_BRIDGE_WS_URL"] = f"ws://127.0.0.1:{int(bridge_ws_port)}/bridge"
        env["ANELF_BRIDGE_TOKEN"] = self._token
        env["PYTHONUNBUFFERED"] = "1"

        self._proc = await asyncio.create_subprocess_exec(
            str(venv_python()),
            str(WORKER_SCRIPT),
            cwd=str(runtime_dir()),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=not _is_windows(),
        )
        self._start_pumps()
        self._start_watchdog()
        self._log(f"worker 已启动 (pid={self._proc.pid})")
        return True

    async def stop_worker(self) -> None:
        """停止 worker 子进程（手动停止，不触发自动重启）。"""
        self._want_running = False
        await self._terminate_process()

    async def restart_worker(self) -> None:
        """重启 worker 子进程（经注入的重启回调完成文件刷新与重新 spawn）。"""
        self._log("worker 重启中 ...")
        await self._terminate_process()
        callback = self.on_worker_restart
        if callback is not None:
            await callback()

    def get_process_status(self) -> Dict[str, Any]:
        return {
            "alive": self.is_process_alive(),
            "pid": self._proc.pid if self._proc else None,
            "returncode": self._proc.returncode if self._proc else None,
            "started_at": self._started_at or None,
            "want_running": self._want_running,
            "auto_restart": self._auto_restart,
            "restart_count": self._restart_count,
            "venv_ready": self.is_venv_ready(),
            "uv": self._uv_exec_cache if self._uv_probed else None,
        }

    # ------------------------------------------------------------------
    # 包安装（适配器 / 插件）
    # ------------------------------------------------------------------

    async def install_packages(self, packages: List[str], uv_exec: str = "") -> Dict[str, Any]:
        """向 worker venv 安装包（串行化，状态可轮询）。"""
        async with self._install_lock:
            self._install_state = {
                "running": True, "packages": list(packages), "logs": [], "error": "", "ok": None,
            }
            try:
                uv = self._resolve_uv(uv_exec)
                cmd = build_install_command(uv, venv_python(), packages)
                result = await self._run_install_command(cmd)
                self._install_state.update(
                    ok=result, running=False, finished_at=time.time()
                )
                return {"success": result, **self._install_state_snapshot()}
            except Exception as exc:
                self._install_state.update(ok=False, running=False, error=str(exc))
                raise

    async def uninstall_packages(self, packages: List[str], uv_exec: str = "") -> Dict[str, Any]:
        """从 worker venv 卸载包（串行化，状态可轮询）。"""
        async with self._install_lock:
            self._install_state = {
                "running": True, "packages": list(packages), "logs": [], "error": "", "ok": None,
                "uninstall": True,
            }
            try:
                uv = self._resolve_uv(uv_exec)
                cmd = build_uninstall_command(uv, venv_python(), packages)
                result = await self._run_install_command(cmd)
                self._install_state.update(ok=result, running=False, finished_at=time.time())
                return {"success": result, **self._install_state_snapshot()}
            except Exception as exc:
                self._install_state.update(ok=False, running=False, error=str(exc))
                raise

    def get_install_state(self) -> Dict[str, Any]:
        """获取最近一次安装操作的进度状态。"""
        return self._install_state_snapshot()

    def _install_state_snapshot(self) -> Dict[str, Any]:
        state = dict(self._install_state)
        state["logs"] = list(state.get("logs") or [])[-50:]
        return state

    # ------------------------------------------------------------------
    # 日志环
    # ------------------------------------------------------------------

    def append_log(self, line: str) -> None:
        """追加一行 worker 日志（WS 上报与 stdout/stderr 泵共用）。"""
        self._log_ring.append(line)
        for queue in list(self._log_subscribers):
            try:
                queue.put_nowait(line)
            except asyncio.QueueFull:
                pass

    def tail_logs(self, count: int = 200) -> List[str]:
        """读取日志环尾部若干行。"""
        if count <= 0:
            return []
        return list(self._log_ring)[-count:]

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _resolve_uv(self, override: str = "") -> Optional[str]:
        if override:
            self._uv_exec_cache = override
            self._uv_probed = True
            return override
        if self._uv_probed:
            return self._uv_exec_cache
        self._uv_exec_cache = find_uv()
        self._uv_probed = True
        return self._uv_exec_cache

    def _log(self, message: str) -> None:
        self.append_log(message)
        log(f"NoneBot Bridge: {message}", "DEBUG", tag="通道")

    async def _run_setup_command(self, cmd: List[str], label: str) -> None:
        """执行 venv 引导命令，失败抛 RuntimeError。"""
        self._log(f"$ {' '.join(cmd)}")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(runtime_dir()),
        )
        output, _ = await proc.communicate()
        for line in (output or b"").decode("utf-8", "replace").splitlines():
            self._log(line)
        if proc.returncode != 0:
            raise RuntimeError(f"{label}失败 (exit={proc.returncode})")

    async def _run_install_command(self, cmd: List[str]) -> bool:
        """执行安装命令并流式记录输出，返回是否成功。"""
        self._install_state["logs"].append("$ " + " ".join(cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(runtime_dir()),
        )
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").rstrip()
            self._install_state["logs"].append(line)
        await proc.wait()
        ok = proc.returncode == 0
        self._log(f"包操作{'成功' if ok else '失败'} (exit={proc.returncode})")
        return ok

    def _start_pumps(self) -> None:
        """泵 worker stdout/stderr 到日志环。"""
        proc = self._proc
        if proc is None:
            return
        if proc.stdout is not None:
            self._pump_tasks.append(
                asyncio.create_task(self._pump_stream(proc.stdout), name="nb_stdout")
            )
        if proc.stderr is not None:
            self._pump_tasks.append(
                asyncio.create_task(self._pump_stream(proc.stderr), name="nb_stderr")
            )

    async def _pump_stream(self, stream: asyncio.StreamReader) -> None:
        try:
            async for raw in stream:
                self.append_log(raw.decode("utf-8", "replace").rstrip())
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001 - 泵任务静默退出
            pass

    def _start_watchdog(self) -> None:
        if self._watchdog_task and not self._watchdog_task.done():
            return
        self._watchdog_task = asyncio.create_task(
            self._watchdog_loop(), name="nb_watchdog"
        )

    async def _wait_for_alive_process(self) -> "asyncio.subprocess.Process":
        """阻塞等待一个存活的 worker 进程句柄。"""
        while True:
            proc = self._proc
            if proc is not None and proc.returncode is None:
                return proc
            await asyncio.sleep(0.5)

    async def _watchdog_loop(self) -> None:
        """监控 worker 进程：意外退出且开启自动重启时按指数退避重启。"""
        while True:
            proc = await self._wait_for_alive_process()
            await proc.wait()
            await self._drain_pumps()
            if self._proc is proc:
                self._proc = None
            exit_code = proc.returncode

            if not self._want_running or not self._auto_restart:
                self._log(f"worker 已退出 (exit={exit_code})")
                continue

            # 稳定运行超过窗口则重置退避计数
            if time.time() - self._started_at > _STABLE_RESET_SECONDS:
                self._restart_count = 0
            self._restart_count += 1
            backoff = min(
                _RESTART_BACKOFF_BASE * (2 ** (self._restart_count - 1)),
                _RESTART_BACKOFF_MAX,
            )
            self._log(
                f"worker 意外退出 (exit={exit_code})，"
                f"{backoff:.0f}s 后自动重启（第 {self._restart_count} 次）"
            )
            await asyncio.sleep(backoff)
            # bool() 重读：sleep 期间 stop_worker 可能翻转 _want_running
            if not bool(self._want_running):
                continue
            callback = self.on_worker_restart
            if callback is None:
                continue
            try:
                await callback()
            except Exception as exc:
                self._log(f"worker 自动重启失败: {exc}")

    async def _drain_pumps(self) -> None:
        for task in self._pump_tasks:
            task.cancel()
        if self._pump_tasks:
            await asyncio.gather(*self._pump_tasks, return_exceptions=True)
        self._pump_tasks.clear()

    async def _terminate_process(self) -> None:
        """优雅停止 worker：SIGTERM → 超时 kill。"""
        proc = self._proc
        if proc is None or proc.returncode is not None:
            if self._proc is proc:
                self._proc = None
            await self._drain_pumps()
            return
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=_STOP_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass
        self._log(f"worker 已停止 (exit={proc.returncode})")
        if self._proc is proc:
            self._proc = None
        await self._drain_pumps()

    async def shutdown(self) -> None:
        """生命周期清理（Lifecycle 注册）。"""
        await self.stop_worker()
        if self._watchdog_task:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass
            self._watchdog_task = None


_runtime: Optional[NoneBotRuntime] = None


def get_nonebot_runtime() -> NoneBotRuntime:
    """获取运行时单例（首次调用时注册生命周期清理）。"""
    global _runtime
    if _runtime is None:
        _runtime = NoneBotRuntime()
        Lifecycle.register("nonebot_runtime", _runtime, cleanup=_runtime.shutdown)
    return _runtime
