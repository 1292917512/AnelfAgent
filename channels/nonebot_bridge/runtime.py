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
import re
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


def normalize_install_spec(source: str) -> str:
    """安装源规范化（纯函数，可测试）。

    - ``git+https://...`` 直通（支持 ``@branch`` / ``#subpath``）；
    - ``https://....git`` → 自动补 ``git+`` 前缀；
    - ``user/repo`` 简写 → ``git+https://github.com/user/repo``；
    - 本地路径（绝对或已存在的相对路径）直通；
    - 其余按 PyPI 包名直通。
    """
    source = source.strip()
    if not source:
        return ""
    if source.startswith(("git+", "git@", "ssh://", "file://", "http://", "https://", "/")):
        if source.startswith(("http://", "https://")) and source.endswith(".git"):
            return f"git+{source}"
        return source
    if re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", source):
        return f"git+https://github.com/{source}"
    if re.match(r"^[./~]", source):
        return str(Path(source).resolve())
    return source


def derive_package_name(spec: str) -> str:
    """从安装源推导分发名（git URL 取仓库名去 .git；路径取末段；PyPI 名原样）。"""
    spec = spec.strip()
    if not spec:
        return ""
    tail = spec.split("#")[0].split("@")[0].rstrip("/")
    name = tail.rsplit("/", 1)[-1]
    return name[:-4] if name.endswith(".git") else name


def parse_git_spec(spec: str) -> Optional[tuple]:
    """解析 git 安装源为 (url, ref)（纯函数，可测试）。

    支持 ``git+https://host/repo.git@dev`` / ``https://host/repo.git`` /
    ``git+file:///path/repo``；非 git 源返回 None。#subpath 后缀剥离保留。
    """
    spec = spec.strip()
    subpath = ""
    if "#" in spec:
        spec, subpath = spec.split("#", 1)
    if spec.startswith("git+"):
        url = spec[4:]
    elif spec.startswith(("https://", "http://")) and spec.endswith(".git"):
        url = spec
    else:
        return None

    ref = ""
    # ref 取最后一个 @（且不在 :// 协议分隔符内）
    at = url.rfind("@")
    if at > url.find("://") + 2:
        url, ref = url[:at], url[at + 1:]
    url = url.rstrip("/")
    if subpath:
        url = f"{url}#{subpath}"
    return url, ref


def sources_dir(override: str = "") -> Path:
    """git 源本地检出目录（纯函数）。

    默认 ``channels/nonebot_bridge/sources/``（频道目录下，已被 .gitignore
    忽略、ruff/mypy 排除 —— 方便直接浏览修改源码做二次开发）；
    可经配置 ``sources_dir`` 覆盖为任意路径（如数据目录或外部工作区）。
    """
    if override.strip():
        return Path(override.strip()).resolve()
    return Path(__file__).parent / "sources"


def build_install_command(
    uv_exec: Optional[str],
    python: Path,
    packages: List[str],
    index_url: str = "",
    editable: bool = False,
    proxy: str = "",
    refresh: bool = False,
) -> List[str]:
    """构造包安装命令（uv 优先；自定义源 / 本地可编辑 / 代理 / 强制刷新）。

    refresh 强制重取（uv ``--refresh`` / pip ``--force-reinstall``），
    git 源更新后重装必用（否则判定已安装而跳过）。
    """
    if uv_exec:
        # 代理不进命令行：uv 不支持 --proxy 旗标，经 install_subprocess_env 注入
        cmd = [uv_exec, "pip", "install", "--python", str(python)]
        if refresh:
            cmd.append("--refresh")
    else:
        cmd = [str(python), "-m", "pip", "install"]
        if refresh:
            cmd.append("--force-reinstall")
    if index_url:
        cmd += ["--index-url", index_url]
    if not uv_exec and proxy:
        cmd += ["--proxy", proxy]
    if editable:
        cmd.append("-e")
    return [*cmd, *packages]


def build_uninstall_command(
    uv_exec: Optional[str], python: Path, packages: List[str]
) -> List[str]:
    """构造包卸载命令。"""
    if uv_exec:
        return [uv_exec, "pip", "uninstall", "--python", str(python), "-y", *packages]
    return [str(python), "-m", "pip", "uninstall", "-y", *packages]


def build_list_command(uv_exec: Optional[str], python: Path) -> List[str]:
    """构造已安装包列表命令。"""
    if uv_exec:
        return [uv_exec, "pip", "list", "--python", str(python)]
    return [str(python), "-m", "pip", "list"]


def build_upgrade_command(
    uv_exec: Optional[str],
    python: Path,
    packages: List[str],
    index_url: str = "",
    proxy: str = "",
) -> List[str]:
    """构造包升级命令（-U 尽量升级到最新兼容版本）。"""
    if uv_exec:
        cmd = [uv_exec, "pip", "install", "--python", str(python), "-U"]
    else:
        cmd = [str(python), "-m", "pip", "install", "-U"]
    if index_url:
        cmd += ["--index-url", index_url]
    if not uv_exec and proxy:
        cmd += ["--proxy", proxy]
    return [*cmd, *packages]


_PROXY_ENV_KEYS = (
    "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
    "all_proxy", "ALL_PROXY",
)


def install_subprocess_env(proxy: str = "") -> Dict[str, str]:
    """安装子进程环境（纯函数，可测试）。

    pip_proxy 语义：空 = 继承系统；``off``/``none`` = 强制直连（剥离代理环境
    变量并用 GIT_CONFIG_* 覆盖 git 全局配置里的坏代理）；其余值 = 使用该代理
    （注入环境变量 + GIT_CONFIG_* 同步，git 优先级最高）。
    """
    env = dict(os.environ)
    value = proxy.strip()
    if not value:
        return env
    if value.lower() in ("off", "none"):
        for key in _PROXY_ENV_KEYS:
            env.pop(key, None)
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "http.proxy"
        env["GIT_CONFIG_VALUE_0"] = ""
        return env
    for key in _PROXY_ENV_KEYS:
        env[key] = value
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = "http.proxy"
    env["GIT_CONFIG_VALUE_0"] = value
    return env


def parse_package_list(output: str) -> List[Dict[str, str]]:
    """解析 pip list / uv pip list 的表格输出为 [{name, version}]。

    纯函数：跳过表头与分隔线，按空白切分取前两列。
    """
    packages: List[Dict[str, str]] = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[0] in ("Package", "-----") or set(parts[0]) == {"-"}:
            continue
        packages.append({"name": parts[0], "version": parts[1]})
    return packages


def remove_venv_dir() -> bool:
    """删除 venv 目录（uv 创建的目录为只执行权限，先放权再删）。"""
    target = venv_dir()
    if not target.exists():
        return True
    try:
        for path in [target, *target.rglob("*")]:
            try:
                path.chmod(0o700)
            except OSError:
                pass
        shutil.rmtree(target)
        return True
    except OSError as exc:
        log(f"NoneBot Bridge: venv 删除失败 - {exc}", "ERROR")
        return False


def format_env_value(value: Any) -> str:
    """把配置值格式化为 dotenv 行值（纯函数，可测试）。

    - list/dict → JSON 字面量（json.dumps，合法且稳定）；
    - str 含特殊字符（``#``/引号/空白/``=``/换行）→ 双引号包裹 + 转义
      （python-dotenv 支持双引号与 ``\\n`` 展开）；
    - 其余 str 原样（保持 .env 可读）。
    """
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    text = str(value)
    if not text:
        return '""'
    if any(ch in text for ch in '#\'"\n=') or text != text.strip():
        return json.dumps(text, ensure_ascii=False)
    return text


def adapter_entry_key(entry: Any) -> str:
    """从 adapters 条目提取 key（纯函数；string 直取，dict 取 key 字段）。"""
    if isinstance(entry, dict):
        return str(entry.get("key", "") or "")
    return str(entry)


def normalize_adapter_entries(entries: List[Any]) -> List[Dict[str, str]]:
    """adapters 条目归一化（纯函数，可测试）。

    兼容两种写法（外部工具/AI 可能直接写 worker 格式进频道配置）：
    - 字符串 key：经 KNOWN_ADAPTERS 解析；``nonebot.adapters.*`` 视为模块路径；
    - dict ``{key, import, class}``：完整声明直通（fork/自研适配器用）。
    未知项跳过并告警。
    """
    resolved: List[Dict[str, str]] = []
    for entry in entries or []:
        if isinstance(entry, dict):
            import_path = str(entry.get("import", "") or "")
            key = str(entry.get("key", "") or import_path.rsplit(".", 1)[-1])
            if import_path:
                resolved.append({
                    "key": key,
                    "import": import_path,
                    "class": str(entry.get("class", "") or "Adapter"),
                })
            else:
                log(f"NoneBot Bridge: 适配器 dict 条目缺少 import，已跳过: {entry}", "WARNING")
            continue

        key = str(entry)
        info = KNOWN_ADAPTERS.get(key)
        if info is not None:
            resolved.append({"key": key, "import": info["import"], "class": info["class"]})
        elif key.startswith("nonebot.adapters."):
            # 注册表动态适配器：模块路径即导入目标
            resolved.append({"key": key, "import": key, "class": "Adapter"})
        else:
            log(f"NoneBot Bridge: 未知适配器 key '{key}'，已跳过", "WARNING")
    return resolved


def build_worker_files(cfg: Dict[str, Any]) -> Dict[str, str]:
    """根据频道配置生成 worker 文件内容（.env / config.json）。

    Args:
        cfg: 频道配置字典（adapters / plugins / nonebot_env / intercept_all /
            worker_host / worker_port 等）；adapters 条目兼容字符串 key 与
            ``{key, import, class}`` dict（见 normalize_adapter_entries）

    Returns:
        ``{".env": 文本, "config.json": 文本}``
    """
    adapter_entries: List[Dict[str, str]] = normalize_adapter_entries(
        list(cfg.get("adapters") or [])
    )

    # 保留字（DRIVER/HOST/PORT/LOG_LEVEL）：用户显式配置覆盖默认值，而非重复写行
    env_pairs: Dict[str, str] = {
        "DRIVER": "~fastapi+~aiohttp",
        "HOST": str(cfg.get("worker_host", "127.0.0.1")),
        "PORT": str(int(cfg.get("worker_port", 8198))),
        "LOG_LEVEL": "INFO",
    }
    for env_key, env_value in (cfg.get("nonebot_env") or {}).items():
        env_pairs[str(env_key)] = format_env_value(env_value)
    env_lines: List[str] = ["# 由 AnelfAgent NoneBot Bridge 自动生成，勿手工编辑"]
    env_lines.extend(f"{key}={value}" for key, value in env_pairs.items())

    from .worker.protocol import WIRE_VERSION

    worker_config = {
        "wire_version": WIRE_VERSION,
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
        self._uv_version_cache: str = ""

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

    async def ensure_venv(self, python_exec: str = "", uv_exec: str = "", proxy: str = "") -> None:
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
        await self._run_setup_command(create_cmd, "venv 创建", proxy=proxy)

        self._log(f"安装基线包: {', '.join(_BASELINE_PACKAGES)}")
        install_cmd = build_install_command(uv, venv_python(), list(_BASELINE_PACKAGES))
        await self._run_setup_command(install_cmd, "基线包安装", proxy=proxy)

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

    async def install_packages(
        self,
        packages: List[str],
        uv_exec: str = "",
        index_url: str = "",
        editable: bool = False,
        proxy: str = "",
        refresh: bool = False,
    ) -> Dict[str, Any]:
        """向 worker venv 安装包（串行化，状态可轮询）。

        packages 支持 PyPI 包名 / git 源（git+URL[@branch]）/ 本地路径；
        editable 仅对本地路径有意义（可编辑安装，仓库代码改动即时生效）；
        proxy：空=继承系统，off/none=强制直连，其余值=使用该代理。
        """
        async with self._install_lock:
            self._install_state = {
                "running": True, "packages": list(packages), "logs": [], "error": "", "ok": None,
            }
            try:
                uv = self._resolve_uv(uv_exec)
                cmd = build_install_command(
                    uv, venv_python(), packages,
                    index_url=index_url, editable=editable, proxy=proxy,
                    refresh=refresh,
                )
                result = await self._run_install_command(cmd, proxy=proxy)
                self._install_state.update(
                    ok=result, running=False, finished_at=time.time(),
                    error="" if result else "安装命令失败（详见 logs 尾部）",
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
                self._install_state.update(
                    ok=result, running=False, finished_at=time.time(),
                    error="" if result else "卸载命令失败（详见 logs 尾部）",
                )
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
    # 环境探测与升级（uv 管理）
    # ------------------------------------------------------------------

    def get_uv_version(self) -> str:
        """探测 uv 版本（缓存，如 "uv 0.8.4"）。"""
        uv = self._resolve_uv()
        if not uv:
            return ""
        if self._uv_version_cache:
            return self._uv_version_cache
        import subprocess

        try:
            proc = subprocess.run(
                [uv, "--version"], capture_output=True, text=True, timeout=15
            )
            self._uv_version_cache = proc.stdout.strip() if proc.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            self._uv_version_cache = ""
        return self._uv_version_cache

    async def get_python_version(self) -> str:
        """worker venv Python 版本（venv 未就绪返回空串）。"""
        if not venv_python().exists():
            return ""
        try:
            proc = await asyncio.create_subprocess_exec(
                str(venv_python()), "--version",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            return out.decode("utf-8", "replace").strip()
        except (OSError, asyncio.TimeoutError):
            return ""

    async def list_installed_packages(self, uv_exec: str = "") -> List[Dict[str, str]]:
        """列出 worker venv 已安装包（名称 + 版本）。"""
        if not self.is_venv_ready():
            return []
        uv = self._resolve_uv(uv_exec)
        output = await self._run_capture(build_list_command(uv, venv_python()))
        return parse_package_list(output) if output is not None else []

    async def sync_git_source(
        self, spec: str, proxy: str = "", sources_override: str = ""
    ) -> Dict[str, Any]:
        """把 git 源克隆/更新到本地仓库目录（sources_dir/<仓库名>）。

        - 首次：``git clone [--branch ref] <url> <dir>``；
        - 已存在：固定 ref 时 checkout，随后 ``pull --ff-only``
          （快进合并，本地有提交/改动时失败而非强推 —— 保护用户对源码的修改）。
        返回 ``{"success", "path", "cloned", "detail"}``，本地路径供安装使用。
        """
        parsed = parse_git_spec(spec)
        if parsed is None:
            return {"success": False, "error": f"非 git 安装源: {spec}"}
        url, ref = parsed
        name = derive_package_name(spec)
        target = sources_dir(sources_override) / name
        env = install_subprocess_env(proxy)
        cloned = not target.exists()

        try:
            if cloned:
                target.parent.mkdir(parents=True, exist_ok=True)
                cmd = ["git", "clone"]
                if ref:
                    cmd += ["--branch", ref]
                cmd += [url, str(target)]
                await self._run_git(cmd, env)
            else:
                if ref:
                    await self._run_git(["git", "-C", str(target), "fetch", "origin", ref], env)
                    await self._run_git(["git", "-C", str(target), "checkout", ref], env)
                else:
                    await self._run_git(["git", "-C", str(target), "fetch", "origin"], env)
                    await self._run_git(["git", "-C", str(target), "pull", "--ff-only"], env)
        except RuntimeError as exc:
            return {"success": False, "error": str(exc), "path": str(target)}

        self._log(f"git 源{'克隆' if cloned else '更新'}完成: {name} -> {target}")
        return {"success": True, "path": str(target), "cloned": cloned, "ref": ref}

    async def _run_git(self, cmd: List[str], env: Dict[str, str]) -> None:
        """执行 git 命令，非零退出抛 RuntimeError（输出进日志环）。"""
        self._log("$ " + " ".join(cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=300.0)
        for line in (out or b"").decode("utf-8", "replace").splitlines():
            self._log(line)
        if proc.returncode != 0:
            raise RuntimeError(f"git 命令失败 (exit={proc.returncode})")

    async def upgrade_packages(
        self, packages: List[str], uv_exec: str = "", index_url: str = "", proxy: str = ""
    ) -> Dict[str, Any]:
        """升级包（NoneBot 本体升级 = 升级 _BASELINE_PACKAGES）。"""
        async with self._install_lock:
            self._install_state = {
                "running": True, "packages": list(packages), "logs": [],
                "error": "", "ok": None, "upgrade": True,
            }
            try:
                uv = self._resolve_uv(uv_exec)
                cmd = build_upgrade_command(
                    uv, venv_python(), packages, index_url=index_url, proxy=proxy
                )
                result = await self._run_install_command(cmd, proxy=proxy)
                self._install_state.update(
                    ok=result, running=False, finished_at=time.time(),
                    error="" if result else "升级命令失败（详见 logs 尾部）",
                )
                return {"success": result, **self._install_state_snapshot()}
            except Exception as exc:
                self._install_state.update(ok=False, running=False, error=str(exc))
                raise

    async def rebuild_venv(self, python_exec: str = "", uv_exec: str = "", proxy: str = "") -> None:
        """删除并重建 worker venv（调用方须先停 worker）。"""
        if self.is_process_alive():
            raise RuntimeError("worker 正在运行，请先停止后再重建 venv")
        if not remove_venv_dir():
            raise RuntimeError("venv 目录删除失败，请检查文件权限")
        marker = _baseline_marker_path()
        try:
            marker.unlink()
        except OSError:
            pass
        await self.ensure_venv(python_exec=python_exec, uv_exec=uv_exec, proxy=proxy)

    async def _run_capture(self, cmd: List[str], timeout: float = 60.0) -> Optional[str]:
        """执行命令并捕获 stdout（失败返回 None）。"""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(runtime_dir()),
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            if proc.returncode != 0:
                return None
            return out.decode("utf-8", "replace")
        except (OSError, asyncio.TimeoutError) as exc:
            log(f"NoneBot Bridge: 命令执行失败 ({cmd[0]}) - {exc}", "WARNING")
            return None

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

    async def _run_setup_command(self, cmd: List[str], label: str, proxy: str = "") -> None:
        """执行 venv 引导命令，失败抛 RuntimeError。"""
        self._log(f"$ {' '.join(cmd)}")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(runtime_dir()),
            env=install_subprocess_env(proxy),
        )
        output, _ = await proc.communicate()
        for line in (output or b"").decode("utf-8", "replace").splitlines():
            self._log(line)
        if proc.returncode != 0:
            raise RuntimeError(f"{label}失败 (exit={proc.returncode})")

    async def _run_install_command(self, cmd: List[str], proxy: str = "") -> bool:
        """执行安装命令并流式记录输出，返回是否成功。"""
        self._install_state["logs"].append("$ " + " ".join(cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(runtime_dir()),
            env=install_subprocess_env(proxy),
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
