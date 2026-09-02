"""SillyTavern 进程生命周期管理。

设计：
- 由本实体直接 Popen 拉起 `node server.js`（独立进程组 + 日志落盘），
  state.json 持久化 pid/端口/日志路径——AnelfAgent 重启后仍能接管管理。
- 运行探测以 GET /version（免 CSRF）为准：即使酒馆是被外部拉起的，
  只要端口在响应，一样可被识别与管理（停止时用 lsof 定位 pid 兜底）。
- SIGTERM 优雅停止（酒馆 server-main.js 注册了信号处理做落盘清理），
  宽限后 SIGKILL 兜底。
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
import time
from typing import Any, Dict, Optional

from core.log import log

from . import config as st_config
from .st_client import STError, get_st_client

_DIR = os.path.dirname(os.path.abspath(__file__))
_STATE_FILE = os.path.join(_DIR, "state.json")
_LOG_DIR = os.path.join(_DIR, "logs")

_lock = threading.Lock()
_proc: Optional[subprocess.Popen] = None  # 本进程内拉起的句柄（可选）

# 上下文注入用的最新探测快照（零 I/O 读取，由 _probe 线程/调用方更新）
_probe_cache: Dict[str, Any] = {"checked_at": 0.0, "result": None}


# ------------------------------------------------------------------
# 状态持久化
# ------------------------------------------------------------------

def _load_state() -> Dict[str, Any]:
    try:
        with open(_STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(data: Dict[str, Any]) -> None:
    tmp = _STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _STATE_FILE)


def _clear_state() -> None:
    try:
        os.remove(_STATE_FILE)
    except FileNotFoundError:
        pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _lan_ip() -> str:
    """探测本机局域网 IP（UDP 连公共地址不实际发包，只为选网卡）。"""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return str(s.getsockname()[0])
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"


def _access_url(cfg: Dict[str, Any]) -> str:
    """酒馆访问地址：listen=True 用局域网 IP，否则回环。"""
    port = cfg["port"]
    if cfg.get("listen"):
        return f"http://{_lan_ip()}:{port}"
    return f"http://127.0.0.1:{port}"


def _find_pid_on_port(port: int) -> Optional[int]:
    """lsof 定位占用端口的进程 pid（管理外部拉起的酒馆实例）。"""
    try:
        out = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
            capture_output=True, text=True, timeout=5,
        )
        pids = [int(x) for x in out.stdout.split() if x.isdigit()]
        return pids[0] if pids else None
    except Exception:
        return None


# ------------------------------------------------------------------
# 健康探测
# ------------------------------------------------------------------

def _probe(timeout: float = 3.0) -> Optional[Dict[str, Any]]:
    """GET /version 探测；运行中返回版本信息，否则 None。"""
    base = st_config.base_url()
    try:
        return get_st_client().version(base, timeout=timeout)
    except STError:
        return None


def is_running() -> bool:
    return _probe() is not None


def status() -> Dict[str, Any]:
    cfg = st_config.load_config()
    state = _load_state()
    version = _probe()
    running = version is not None
    pid = state.get("pid")
    pid_alive = pid is not None and _pid_alive(int(pid))
    info: Dict[str, Any] = {
        "running": running,
        "managed": running and pid_alive,
        "url": _access_url(cfg),
        "port": cfg["port"],
        "listen": cfg.get("listen", False),
        "pid": pid if running else None,
        "started_at": state.get("started_at"),
        "version": (version or {}).get("pkgVersion") if running else None,
        "commit": (version or {}).get("gitRevision") if running else None,
        "log_file": state.get("log_file"),
        "auto_start": cfg.get("auto_start", False),
    }
    if running and state.get("started_at"):
        info["uptime_sec"] = int(time.time() - float(state["started_at"]))
    return info


def refresh_probe_cache() -> None:
    """后台轮询入口：更新供上下文注入读取的快照。"""
    with _lock:
        _probe_cache["checked_at"] = time.time()
        _probe_cache["result"] = status()


def cached_status(max_age: float = 60.0) -> Dict[str, Any]:
    """读取探测快照（超龄则同步补一次，供 provide() 之外的展示场景）。"""
    with _lock:
        age = time.time() - _probe_cache["checked_at"]
        result = _probe_cache["result"]
    if result is None or age > max_age:
        refresh_probe_cache()
        with _lock:
            result = _probe_cache["result"]
    return result or {}


# ------------------------------------------------------------------
# 启动 / 停止 / 重启
# ------------------------------------------------------------------

def _ensure_bridge_plugin(st_dir: str) -> None:
    """确保 anelf-bridge 插件存在且 config.yaml 开启了 server plugins。

    插件源码随实体仓库维护在 <st_dir>/plugins/anelf-bridge/；启动前把
    enableServerPlugins 置为 true，让酒馆加载该桥接端点。
    """
    plugin_dir = os.path.join(st_dir, "plugins", "anelf-bridge")
    index = os.path.join(plugin_dir, "index.js")
    if not os.path.isfile(index):
        log(f"anelf-bridge 插件缺失: {index}", "WARNING", tag="酒馆")
        return
    config_yaml = os.path.join(st_dir, "config.yaml")
    if not os.path.isfile(config_yaml):
        return
    try:
        with open(config_yaml, encoding="utf-8") as f:
            content = f.read()
        if "enableServerPlugins: true" in content:
            return
        if "enableServerPlugins: false" in content:
            content = content.replace("enableServerPlugins: false",
                                      "enableServerPlugins: true")
        else:
            content += "\nenableServerPlugins: true\n"
        with open(config_yaml, "w", encoding="utf-8") as f:
            f.write(content)
        log("已在酒馆 config.yaml 开启 enableServerPlugins（anelf-bridge）", tag="酒馆")
    except Exception as e:
        log(f"开启 server plugins 失败: {e}", "ERROR", tag="酒馆")


def _ensure_dependencies(st_dir: str) -> Optional[str]:
    """首次启动时安装酒馆依赖（node_modules 缺失时）。"""
    if os.path.isdir(os.path.join(st_dir, "node_modules")):
        return None
    log("SillyTavern 首次启动，安装依赖（npm install）…", tag="酒馆")
    result = subprocess.run(
        ["npm", "install", "--no-audit", "--no-fund"],
        cwd=st_dir, capture_output=True, text=True, timeout=900,
    )
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "")[-800:]
        raise RuntimeError(f"npm install 失败（退出码 {result.returncode}）: {tail}")
    return "deps_installed"


def start(wait_ready: bool = True) -> Dict[str, Any]:
    global _proc
    cfg = st_config.load_config()
    st_dir = cfg["st_dir"]
    server_js = os.path.join(st_dir, "server.js")
    if not os.path.isfile(server_js):
        raise RuntimeError(f"未找到酒馆源码: {server_js}（请检查配置 st_dir）")
    if _probe():
        return {"ok": True, "already_running": True, **status()}

    with _lock:
        if _probe():
            return {"ok": True, "already_running": True, **status()}
        installed = _ensure_dependencies(st_dir)
        if cfg.get("enable_bridge_plugin", True):
            _ensure_bridge_plugin(st_dir)

        node = shutil.which("node")
        if not node:
            raise RuntimeError("未找到 node 可执行文件，请先安装 Node.js")

        args = [node, "server.js", "--port", str(cfg["port"])]
        if cfg.get("disable_csrf", True):
            args.append("--disableCsrf")
        if cfg.get("listen"):
            args.append("--listen")
        args.extend(str(a) for a in cfg.get("extra_args", []))

        os.makedirs(_LOG_DIR, exist_ok=True)
        log_file = os.path.join(_LOG_DIR, f"st-{time.strftime('%Y%m%d-%H%M%S')}.log")
        out_fp = open(log_file, "w", encoding="utf-8", errors="replace")
        popen_kwargs: Dict[str, Any] = {
            "stdout": out_fp,
            "stderr": subprocess.STDOUT,
            "cwd": st_dir,
            "text": True,
        }
        if os.name != "nt":
            popen_kwargs["start_new_session"] = True
        try:
            proc = subprocess.Popen(args, **popen_kwargs)
        except Exception as e:
            out_fp.close()
            raise RuntimeError(f"启动酒馆进程失败: {e}") from e

        _proc = proc
        _save_state({
            "pid": proc.pid,
            "port": cfg["port"],
            "started_at": time.time(),
            "log_file": log_file,
            "cmd": " ".join(args),
        })
        get_st_client().reset()
        log(f"酒馆已启动: pid={proc.pid} port={cfg['port']} log={log_file}", tag="酒馆")

    # 就绪等待：node 进程存活 + /version 响应
    deadline = time.time() + cfg.get("startup_timeout", 120)
    while wait_ready and time.time() < deadline:
        if _probe():
            refresh_probe_cache()
            return {"ok": True, "pid": proc.pid, "port": cfg["port"],
                    "url": _access_url(cfg), "log_file": log_file,
                    "deps_installed": installed == "deps_installed",
                    "version": (_probe() or {}).get("pkgVersion")}
        if proc.poll() is not None:
            raise RuntimeError(
                f"酒馆进程提前退出（退出码 {proc.returncode}），日志尾部:\n{tail_log(2000)}")
        time.sleep(1.0)
    if wait_ready:
        raise TimeoutError(
            f"酒馆启动超时（{cfg.get('startup_timeout', 120)}s 内未就绪），日志尾部:\n{tail_log(2000)}")
    return {"ok": True, "pid": proc.pid, "port": cfg["port"],
            "url": _access_url(cfg), "log_file": log_file,
            "starting": True}


def stop(grace_seconds: float = 8.0) -> Dict[str, Any]:
    global _proc
    state = _load_state()
    if not _probe():
        _clear_state()
        _proc = None
        return {"ok": True, "was_running": False}

    pid = state.get("pid")
    if not pid or not _pid_alive(int(pid)):
        pid = _find_pid_on_port(st_config.load_config()["port"])
    if not pid:
        raise RuntimeError("酒馆在运行但无法定位其进程（state 缺失且 lsof 未命中）")

    from core.command import terminate_process_group
    proc = _proc if (_proc and _proc.pid == pid) else None
    if proc is not None:
        terminate_process_group(proc, grace_seconds=grace_seconds)
    else:
        # 外部拉起的实例：手动进程组终止（start_new_session 语义）
        try:
            os.killpg(os.getpgid(int(pid)), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            os.kill(int(pid), signal.SIGTERM)
        deadline = time.time() + grace_seconds
        while time.time() < deadline and _pid_alive(int(pid)):
            time.sleep(0.3)
        if _pid_alive(int(pid)):
            try:
                os.killpg(os.getpgid(int(pid)), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                os.kill(int(pid), signal.SIGKILL)

    stopped = _probe() is None
    _clear_state()
    _proc = None
    get_st_client().reset()
    refresh_probe_cache()
    log(f"酒馆已停止 (pid={pid})", tag="酒馆")
    return {"ok": stopped, "was_running": True, "pid": pid}


def restart() -> Dict[str, Any]:
    was_running = _probe() is not None
    if was_running:
        stop()
    result = start()
    result["was_running"] = was_running
    return result


# ------------------------------------------------------------------
# 日志
# ------------------------------------------------------------------

def tail_log(max_chars: int = 4000) -> str:
    state = _load_state()
    path = state.get("log_file")
    if not path or not os.path.isfile(path):
        # 兜底：取 logs 目录里最新的
        try:
            files = sorted(
                (f for f in os.listdir(_LOG_DIR) if f.endswith(".log")),
                reverse=True)
            if files:
                path = os.path.join(_LOG_DIR, files[0])
        except OSError:
            return ""
    if not path or not os.path.isfile(path):
        return ""
    try:
        size = os.path.getsize(path)
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            if size > max_chars:
                f.seek(size - max_chars)
            return f.read().strip()
    except OSError:
        return ""


def autostart_if_enabled() -> None:
    """AnelfAgent 启动钩子：auto_start 开启时后台拉起酒馆。"""
    cfg = st_config.load_config()
    if not cfg.get("auto_start"):
        return
    if _probe():
        return

    def _bg() -> None:
        try:
            start()
        except Exception as e:
            log(f"酒馆自动启动失败: {e}", "ERROR", tag="酒馆")

    threading.Thread(target=_bg, name="st-autostart", daemon=True).start()
