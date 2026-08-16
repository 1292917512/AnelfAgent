"""运维核心逻辑 — 应用重启、崩溃信息、前端构建、项目代码更新（单一实现主体）。

重启依赖外层启动脚本（start.sh / start.bat）的退出码 42 重启循环：
本模块仅标记重启意图并触发优雅关闭（core.lifecycle.Lifecycle.request_shutdown，
线程安全），完整清理由 launch.py 关停流程执行，进程以 42 退出后由脚本重新拉起。
若进程并非由启动脚本拉起，则等同于普通关闭。

AI 工具（tools.py，同步工作线程）与 HTTP 路由（router.py，主事件循环）
统一走本模块，杜绝平行实现。
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.command import run_command
from core.lifecycle import Lifecycle
from core.log import log

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
FRONTEND_DIR = PROJECT_ROOT / "web" / "frontend"

# 延迟触发关闭，确保触发重启的 HTTP 响应/工具结果先行返回
_SHUTDOWN_DELAY = 1.0
# 前端构建超时（秒）
_BUILD_TIMEOUT = 300
# 构建日志仅保留尾部，避免状态接口返回过大
_LOG_TAIL_LIMIT = 4000

_build_state: Dict[str, Any] = {"building": False, "last": None}
_build_lock = threading.Lock()


# ── 重启 ─────────────────────────────────────────────────────────────


def schedule_restart(delay: float = _SHUTDOWN_DELAY) -> None:
    """延迟触发优雅关闭并标记重启意图（任意线程可调）。"""
    threading.Timer(delay, Lifecycle.request_shutdown, args=(True,)).start()


def request_restart() -> Dict[str, Any]:
    """请求优雅重启（由外层启动脚本按退出码 42 重新拉起）。"""
    log("收到重启请求，即将优雅关闭并重启", tag="运维")
    schedule_restart()
    return {"ok": True, "restarting": True}


# ── 崩溃信息 ─────────────────────────────────────────────────────────


def get_crash_info() -> Dict[str, Any]:
    """查询最近一次进程崩溃信息（只读，不消费崩溃状态）。

    数据源：启动脚本守护循环写入的 logs/crash_state.json（崩溃退出码
    自动拉起时落盘）+ macOS 系统崩溃报告（DiagnosticReports .ips）关联。
    """
    from core import crash_report

    state = crash_report.read_crash_state()
    if state is None:
        return {"ok": True, "has_crash": False}
    crash = dict(state)
    if not crash.get("ips"):
        crash["ips"] = crash_report.find_related_ips(str(crash.get("crashed_at") or ""))
    return {
        "ok": True,
        "has_crash": True,
        "crash": crash,
        "summary": crash_report.format_crash_summary(crash),
    }


# ── 前端构建 ─────────────────────────────────────────────────────────


def get_build_state() -> Dict[str, Any]:
    """查询前端构建状态（building / 最近一次构建结果）。"""
    return _build_state


def try_begin_build() -> Optional[Dict[str, Any]]:
    """抢占构建名额；前端目录不存在或已有构建进行时返回错误字典，否则返回 None。"""
    if not (FRONTEND_DIR / "package.json").exists():
        return {"ok": False, "error": "frontend_not_found"}
    with _build_lock:
        if _build_state["building"]:
            return {"ok": False, "error": "build_in_progress"}
        _build_state["building"] = True
    return None


def _finish_build(ok: bool, started: float, log_tail: str) -> None:
    _build_state["last"] = {
        "ok": ok,
        "duration": round(time.time() - started, 1),
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "log_tail": log_tail[-_LOG_TAIL_LIMIT:],
    }
    _build_state["building"] = False


async def _run_build() -> None:
    """执行前端构建（npm run build）并记录结果。"""
    started = time.time()
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    log("开始构建前端（npm run build）...", tag="运维")
    try:
        proc = await asyncio.create_subprocess_exec(
            npm, "run", "build",
            cwd=str(FRONTEND_DIR),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=_BUILD_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            _finish_build(False, started, f"构建超时（{_BUILD_TIMEOUT}s）")
            log("前端构建超时", "ERROR", tag="运维")
            return
        text = out.decode("utf-8", errors="replace") if out else ""
        ok = proc.returncode == 0
        _finish_build(ok, started, text)
        if ok:
            log(f"前端构建成功（{_build_state['last']['duration']}s）", tag="运维")
        else:
            log(f"前端构建失败（exit {proc.returncode}）", "ERROR", tag="运维")
    except FileNotFoundError:
        _finish_build(False, started, "npm 未安装或不在 PATH 中")
        log("前端构建失败：未找到 npm", "ERROR", tag="运维")
    except Exception as exc:
        _finish_build(False, started, f"构建异常: {exc}")
        log(f"前端构建异常: {exc}", "ERROR", tag="运维")


async def build_and_restart() -> Dict[str, Any]:
    """构建前端，成功后调度重启；构建失败则取消重启并返回错误。"""
    error = try_begin_build()
    if error:
        return error
    await _run_build()
    last = _build_state["last"]
    if last and last["ok"]:
        log("构建成功，即将重启", tag="运维")
        schedule_restart()
        return {"ok": True, "restarting": True, "build": last}
    return {"ok": False, "error": "build_failed", "build": last}


def build_and_restart_blocking() -> Dict[str, Any]:
    """build_and_restart 的同步包装（供工具工作线程使用，私有循环内执行子进程）。"""
    return asyncio.run(build_and_restart())


_background_tasks: set[asyncio.Task[Dict[str, Any]]] = set()


def start_build_and_restart() -> bool:
    """在当前事件循环上后台执行构建并重启（供 HTTP 路由使用）。

    持有任务引用避免被 GC 提前回收；已有构建进行时返回 False。
    """
    if _build_state["building"]:
        return False
    task = asyncio.create_task(build_and_restart(), name="devops.build_restart")
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return True


# ── 项目代码更新 ──────────────────────────────────────────────────────


def _git(args: List[str], timeout: int = 60) -> Dict[str, Any]:
    """执行 git 命令并返回结构化结果。"""
    result = run_command(["git", *args], timeout_sec=timeout, cwd=str(PROJECT_ROOT))
    return {
        "ok": result.ok,
        "stdout": (result.stdout or "").strip()[:3000],
        "stderr": (result.stderr or "").strip()[:1000],
    }


def _has_conflict(output: str) -> bool:
    """检测 git pull 输出是否包含冲突标志。"""
    conflict_markers = ("CONFLICT", "Automatic merge failed", "fix conflicts")
    return any(m in output for m in conflict_markers)


def git_pull() -> Dict[str, Any]:
    """从远程仓库拉取最新代码（git pull --ff-only）。

    工作区有未提交修改或拉取冲突时返回 ok=False，调用方不得继续重启流程。
    """
    status = _git(["status", "--porcelain"], timeout=15)
    if status["ok"] and status["stdout"]:
        return {
            "ok": False,
            "error": "dirty_workspace",
            "message": "工作区有未提交的修改，请先提交或暂存后再更新",
            "dirty_files": status["stdout"][:500],
        }

    pull = _git(["pull", "--ff-only"], timeout=120)
    if not pull["ok"] or _has_conflict(pull["stdout"] + pull["stderr"]):
        return {
            "ok": False,
            "error": "pull_conflict",
            "conflict": True,
            "message": "代码更新遇到冲突，请主动联系主人解决，不要尝试自动处理！",
            "detail": pull["stdout"][:500],
        }
    return {"ok": True, "pull_result": pull["stdout"][:300]}
