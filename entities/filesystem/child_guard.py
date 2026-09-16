"""后台 shell 子进程看护 — owner-death 守卫与关停清扫。

后台 shell 以独立进程组运行（``start_new_session``，宿主崩溃不波及），
代价是宿主退出/崩溃后子进程成为孤儿继续跑。本模块用登记文件补上这层：

- **登记**：启动时把进程组号追加到 ``logs/shell_children.json``，等待
  线程结束（自然/被终止）后移除；
- **启动清扫**：进程起来时扫描登记文件——仍存活且不是当前进程子进程
  的组，就是上次实例的孤儿：SIGTERM → 5s 宽限 → SIGKILL（整组）；
- **关停清扫**：优雅退出时终止全部在册子进程（AI 明确信任的长任务
  语义是"宿主死后继续"，但那是崩溃场景；正常退出不该留孤儿）。

POSIX 用 killpg 整组击杀；Windows 无进程组语义，退化为 taskkill /T
（登记照常，清扫尽力而为）。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict

from core.log import log

_REGISTRY_LOCK = threading.Lock()
"""登记文件读-改-写锁：启动/结束/清扫可能来自不同线程（等待线程）。"""


def _registry_path() -> Path:
    return Path(__file__).resolve().parents[2] / "logs" / "shell_children.json"


def register_child(pgid: int, description: str) -> None:
    """登记一个在册子进程组（等待线程结束时调 unregister_child 移除）。"""
    with _REGISTRY_LOCK:
        data = _load()
        data[str(pgid)] = {
            "pid": pgid, "desc": description[:60],
            "started_at": time.time(), "boot_id": os.getpid(),
        }
        _save(data)


def unregister_child(pgid: int) -> None:
    """移除登记（进程已结束——自然退出或被终止）。"""
    with _REGISTRY_LOCK:
        data = _load()
        if data.pop(str(pgid), None) is not None:
            _save(data)


def _load() -> Dict[str, Any]:
    p = _registry_path()
    try:
        return json.loads(p.read_text("utf-8"))
    except Exception:
        return {}


def _save(data: Dict[str, Any]) -> None:
    p = _registry_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
        tmp.replace(p)
    except Exception as exc:
        log(f"后台子进程登记写盘失败: {exc}", "DEBUG", tag="后台")


def _alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 组存在但无权发信号——按存活处理（不误清登记）
    except OSError:
        return False


def _terminate_group(pgid: int, *, grace_seconds: float = 5.0) -> None:
    """终止一个进程组：SIGTERM → 宽限 → SIGKILL（尽力而为，失败只记日志）。"""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pgid), "/T", "/F"],
            capture_output=True, timeout=10, check=False,
        )
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        return
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if not _alive(pgid):
            return
        time.sleep(0.2)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def sweep_stale_children() -> int:
    """启动清扫：清掉上次实例遗留的孤儿子进程组（返回清除数）。

    判据：组仍存活 且 登记的 boot_id ≠ 当前进程 pid（自己登记的由等待
    线程照常管理，不在此处干预）。登记文件里已死的组直接清账。
    """
    current_boot = os.getpid()
    swept = 0
    with _REGISTRY_LOCK:
        data = _load()
        stale: Dict[str, Any] = {}
        mutated = False
        for key, entry in list(data.items()):
            pgid = int(entry.get("pid", 0) or 0)
            if pgid <= 0:
                data.pop(key)
                mutated = True
                continue
            if not _alive(pgid):
                data.pop(key)
                mutated = True
                continue
            if int(entry.get("boot_id", 0) or 0) == current_boot:
                continue  # 本次启动内登记的，等待线程在管
            stale[key] = entry
        if mutated:
            _save(data)
    for key, entry in stale.items():
        pgid = int(entry["pid"])
        log(f"清扫上次实例遗留的后台子进程组 {pgid}: {entry.get('desc', '')}", "INFO", tag="后台")
        _terminate_group(pgid)
        with _REGISTRY_LOCK:
            data = _load()
            data.pop(key, None)
            _save(data)
        swept += 1
    return swept


def terminate_all_children() -> int:
    """关停清扫：终止全部在册子进程（优雅退出不留孤儿）。"""
    with _REGISTRY_LOCK:
        data = _load()
        live = {k: e for k, e in data.items()
                if int(e.get("pid", 0) or 0) > 0 and _alive(int(e["pid"]))}
    for _key, entry in live.items():
        _terminate_group(int(entry["pid"]))
    if live:
        with _REGISTRY_LOCK:
            _save({})
        log(f"关停清扫后台子进程: {len(live)} 个", "INFO", tag="后台")
    return len(live)
