"""运维核心逻辑 — 应用重启、崩溃信息、前端构建、项目代码更新（单一实现主体）。

重启依赖外层启动脚本（start.sh / start.bat）的退出码 42 重启循环：
本模块仅标记重启意图并触发优雅关闭（core.lifecycle.Lifecycle.request_shutdown，
线程安全），完整清理由 launch.py 关停流程执行，进程以 42 退出后由脚本重新拉起。
若进程并非由启动脚本拉起，则等同于普通关闭。

AI 工具发起的重启走"重启交接"闭环：调用时把目标会话 scope / 回复路由 /
AI 留言落盘（<data_dir>/restart_handoff.json），随后等待思维空闲再关停
（当前回复轮自然收尾，不产生"被意外中断"残留检查点）；进程重新拉起后由
tools.py 的 RestartHandoffWatcher 消费交接，向原会话推送"重启成功 + 留言"
一次性通知并唤醒思维。Web/API 路径不等待、不写交接，行为与历史一致。

AI 工具（tools.py，同步工作线程）与 HTTP 路由（router.py，主事件循环）
统一走本模块，杜绝平行实现。
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.command import run_command
from core.lifecycle import Lifecycle
from core.log import log
from core.path import data_dir
from entities._sdk import is_mind_busy

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
FRONTEND_DIR = PROJECT_ROOT / "web" / "frontend"

# 延迟触发关闭，确保触发重启的 HTTP 响应/工具结果先行返回
_SHUTDOWN_DELAY = 1.0
# 重启看门狗：排定重启后超过该时长进程仍存活（优雅关停卡死），
# 无条件 os._exit(42) 保底，确保守护脚本必然接管。
# 取值需覆盖正常关停耗时（记忆兜底 30s 上限 + 各组件 per_timeout 清理）
_RESTART_WATCHDOG_SECONDS = 90.0
# 前端构建超时（秒）
_BUILD_TIMEOUT = 300
# 构建日志仅保留尾部，避免状态接口返回过大
_LOG_TAIL_LIMIT = 4000
# 等待思维空闲的轮询间隔 / 上限（超时强制关停，防死等）
_IDLE_POLL_INTERVAL = 0.5
_IDLE_WAIT_CAP = 120.0
# 交接留言长度上限（对齐推送通道单条内容上限）
_HANDOFF_MESSAGE_LIMIT = 2000
# 交接有效期限：正常重启秒级完成即被消费，超时残留的一律视为陈旧，
# 只清理不投递（防止进程被 kill 后数小时才手动启动时投递过期留言）
_HANDOFF_TTL_SECONDS = 3600.0

_runtime_id = uuid.uuid4().hex
_build_state: Dict[str, Any] = {"building": False, "last": None, "phase": "idle", "result": None, "operation_id": None}
_operation_lock = threading.Lock()


# ── 重启 ─────────────────────────────────────────────────────────────

_restart_pending = False


def _is_supervised() -> bool:
    """当前进程是否由 start.sh / start.bat 守护循环拉起。

    重启依赖外层脚本按退出码 42 重新拉起；直接 ``python launch.py``
    启动的进程没有守护，"重启"会等同关机且不会自动拉起。
    """
    try:
        import psutil
        for parent in psutil.Process().parents():
            try:
                cmdline = " ".join(parent.cmdline())
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if "start.sh" in cmdline or "start.bat" in cmdline:
                return True
    except Exception as exc:
        log(f"守护进程检测失败: {exc}", "DEBUG", tag="运维")
    return False


def _restart_watchdog() -> None:
    """重启保底：优雅关停卡死时直接以退出码 42 退出，交由守护脚本拉起。"""
    import os

    from core.lifecycle import RESTART_EXIT_CODE

    time.sleep(_RESTART_WATCHDOG_SECONDS)
    log(
        f"优雅关停超过 {_RESTART_WATCHDOG_SECONDS:.0f}s 未完成，看门狗强制退出（退出码 {RESTART_EXIT_CODE}）",
        "WARNING", tag="运维",
    )
    os._exit(RESTART_EXIT_CODE)


def _arm_restart_watchdog() -> None:
    threading.Thread(target=_restart_watchdog, daemon=True, name="devops.restart_watchdog").start()


def schedule_restart(delay: float = _SHUTDOWN_DELAY) -> None:
    """延迟触发优雅关闭并标记重启意图（任意线程可调）。"""
    _arm_restart_watchdog()
    threading.Timer(delay, Lifecycle.request_shutdown, args=(True,)).start()


def _wait_idle_then_shutdown() -> None:
    """等待思维空闲（无进行中回复/反思）后触发优雅关闭，超上限强制关停。"""
    deadline = time.monotonic() + _IDLE_WAIT_CAP
    while is_mind_busy() and time.monotonic() < deadline:
        time.sleep(_IDLE_POLL_INTERVAL)
    Lifecycle.request_shutdown(True)
    _arm_restart_watchdog()  # 关停请求发出后才布防，避免看门狗在等空闲期间误触发


def schedule_restart_when_idle(delay: float = _SHUTDOWN_DELAY) -> None:
    """宽限 delay 后等思维空闲再触发优雅关闭（任意线程可调）。

    AI 工具发起的重启走此路径：当前回复轮自然收尾（检查点正常清除，
    重启后不会产生"被意外中断"元消息），随后才进入关停流程。
    """
    threading.Timer(delay, _wait_idle_then_shutdown).start()


# ── 重启交接 ─────────────────────────────────────────────────────────


def _handoff_path() -> Path:
    """重启交接状态文件路径（随数据目录配置搬迁）。"""
    return Path(data_dir()) / "restart_handoff.json"


def write_handoff(scope: str, channel: str, message: str, source: str) -> None:
    """持久化重启交接（重启后由原会话接收"重启成功"通知与 AI 留言）。

    写失败仅记日志（fail-open）：交接丢失只退化为无通知重启，不阻断流程。
    """
    payload = {
        "scope": scope,
        "channel": channel,
        "message": message[:_HANDOFF_MESSAGE_LIMIT],
        "source": source,
        "ts": time.time(),
    }
    try:
        _handoff_path().write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        log(f"重启交接写入失败（已忽略）: {exc}", "WARNING", tag="运维")


def consume_handoff() -> Optional[Dict[str, Any]]:
    """读取并删除重启交接（不存在/损坏/陈旧返回 None；保证只消费一次）。

    文件先删后判：无论内容是否可用都只处理一次，绝不在后续启动重复投递。
    超过 _HANDOFF_TTL_SECONDS 的残留交接视为陈旧，仅清理不返回。
    """
    path = _handoff_path()
    data: Any = None
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log(f"重启交接读取失败（按无交接处理）: {exc}", "DEBUG", tag="运维")
    try:
        path.unlink(missing_ok=True)
    except Exception as exc:
        log(f"重启交接清理失败（已忽略）: {exc}", "DEBUG", tag="运维")
    if not isinstance(data, dict):
        return None
    try:
        age = time.time() - float(data.get("ts") or 0)
    except (TypeError, ValueError):
        age = _HANDOFF_TTL_SECONDS + 1
    if age > _HANDOFF_TTL_SECONDS:
        log(f"重启交接已陈旧（{age:.0f}s 前），仅清理不投递", "WARNING", tag="运维")
        return None
    return data


def request_restart(
    source: str = "api",
    delay: float = _SHUTDOWN_DELAY,
    wait_idle: bool = False,
    handoff: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """请求优雅重启（由外层启动脚本按退出码 42 重新拉起）。

    重复请求去重（多标签页/多渠道重复触发只生效一次，已排定时允许用
    handoff 补充/更新交接留言）；
    进程非守护脚本拉起时拒绝，避免"重启变关机"。

    Args:
        wait_idle: True 时等思维空闲再关停（AI 工具路径，让当前回复轮收尾）
        handoff: 重启交接（scope/channel/message），仅在重启确认排定后落盘，
            重启完成重新拉起后由原会话消费
    """
    global _restart_pending
    with _operation_lock:
        if _restart_pending:
            # 已排定：仅在新留言非空时更新交接，避免空留言覆盖已写内容
            if handoff and handoff.get("message", "").strip():
                write_handoff(handoff.get("scope", ""), handoff.get("channel", ""),
                              handoff.get("message", ""), source)
            return {"ok": True, "restarting": True, "already_pending": True, "runtime_id": _runtime_id}
        if _build_state["building"] and source != "build_and_restart":
            return {"ok": False, "error": "build_in_progress", "message": "更新或构建正在进行，请等待完成后再重启"}
        if not _is_supervised():
            log(
                f"拒绝重启请求（来源 {source}）：进程非 start.sh 守护拉起，重启将等同关机",
                "WARNING", tag="运维",
            )
            return {
                "ok": False,
                "error": "no_supervisor",
                "message": "当前进程不是由启动脚本（start.sh）守护拉起的，重启等同关机且不会自动拉起；"
                           "请改用 restart.sh 重启，或先通过 start.sh 启动后再使用本功能",
            }
        _restart_pending = True
    if handoff:
        write_handoff(handoff.get("scope", ""), handoff.get("channel", ""),
                      handoff.get("message", ""), source)
    log(f"收到重启请求（来源 {source}），即将优雅关闭并重启", tag="运维")
    if wait_idle:
        schedule_restart_when_idle(delay)
    else:
        schedule_restart(delay)
    return {"ok": True, "restarting": True, "runtime_id": _runtime_id}


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
    with _operation_lock:
        return {**_build_state, "last": dict(_build_state["last"]) if _build_state["last"] else None,
                "result": dict(_build_state["result"]) if _build_state["result"] else None,
                "runtime_id": _runtime_id, "restarting": _restart_pending}


def _begin_operation(phase: str, require_frontend: bool = True) -> Optional[Dict[str, Any]]:
    """原子占用运维操作名额，防止更新、构建与重启并发。"""
    if require_frontend and not (FRONTEND_DIR / "package.json").exists():
        return {"ok": False, "error": "frontend_not_found"}
    with _operation_lock:
        if _build_state["building"] or _restart_pending:
            return {"ok": False, "error": "build_in_progress"}
        _build_state.update(building=True, phase=phase, result=None, operation_id=uuid.uuid4().hex)
    return None


def _finish_build(ok: bool, started: float, log_tail: str) -> None:
    _build_state["last"] = {
        "ok": ok,
        "duration": round(time.time() - started, 1),
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "log_tail": log_tail[-_LOG_TAIL_LIMIT:],
    }


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
        except asyncio.CancelledError:
            proc.kill()
            await proc.wait()
            _finish_build(False, started, "构建已取消")
            raise
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


async def build_and_restart(
    wait_idle: bool = False,
    handoff: Optional[Dict[str, str]] = None,
    update: bool = False,
) -> Dict[str, Any]:
    """构建前端，成功后调度重启；构建失败则取消重启并返回错误。"""
    error = _begin_operation("pulling" if update else "building")
    if error:
        return error
    return await _complete_build(wait_idle=wait_idle, handoff=handoff, update=update)


async def _complete_build(
    wait_idle: bool = False, handoff: Optional[Dict[str, str]] = None, update: bool = False,
) -> Dict[str, Any]:
    """执行已占用构建名额的更新、构建、重启流程，并保留完整终态。"""
    result: Dict[str, Any] = {"ok": False, "error": "cancelled", "message": "操作已取消"}
    pull_result = ""
    try:
        if update:
            _build_state["phase"] = "pulling"
            pull_task = asyncio.create_task(asyncio.to_thread(_git_pull))
            try:
                pull = await asyncio.shield(pull_task)
            except asyncio.CancelledError:
                try:
                    await pull_task
                finally:
                    raise
            if not pull["ok"]:
                result = pull
                return result
            pull_result = pull.get("pull_result", "")
        _build_state["phase"] = "building"
        await _run_build()
        last = _build_state["last"]
        if not last or not last["ok"]:
            result = {"ok": False, "error": "build_failed", "build": last}
        else:
            restart = request_restart(source="build_and_restart", wait_idle=wait_idle, handoff=handoff)
            result = {**restart, "build": last}
        if pull_result:
            result["pull_result"] = pull_result
        return result
    except Exception as exc:
        result = {"ok": False, "error": "operation_failed", "message": str(exc)}
        log(f"运维操作失败: {exc}", "ERROR", tag="运维")
        return result
    finally:
        with _operation_lock:
            _build_state.update(building=False, result=result, phase="restarting" if result.get("ok") else "failed")


def build_and_restart_blocking(
    wait_idle: bool = False,
    handoff: Optional[Dict[str, str]] = None,
    update: bool = False,
) -> Dict[str, Any]:
    """build_and_restart 的同步包装（供工具工作线程使用，私有循环内执行子进程）。"""
    return asyncio.run(build_and_restart(wait_idle=wait_idle, handoff=handoff, update=update))


_background_tasks: set[asyncio.Task[Dict[str, Any]]] = set()


def start_build_and_restart(update: bool = False) -> Dict[str, Any]:
    """在当前事件循环上后台执行构建并重启（供 HTTP 路由使用）。

    提交任务前同步占用名额，拒绝重复请求；结果由 build-state 查询。
    """
    asyncio.get_running_loop()
    error = _begin_operation("pulling" if update else "building")
    if error:
        return error
    task = asyncio.create_task(_complete_build(update=update), name="devops.build_restart")
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"ok": True, "building": True, "runtime_id": _runtime_id, "operation_id": _build_state["operation_id"]}


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


def _git_pull() -> Dict[str, Any]:
    """从远程仓库拉取最新代码（git pull --ff-only）。

    工作区有未提交修改或拉取冲突时返回 ok=False，调用方不得继续重启流程。
    """
    status = _git(["status", "--porcelain"], timeout=15)
    if not status["ok"]:
        return {"ok": False, "error": "status_failed", "message": status["stderr"] or "无法检查工作区状态"}
    if status["stdout"]:
        return {
            "ok": False,
            "error": "dirty_workspace",
            "message": "工作区有未提交的修改，请先提交或暂存后再更新",
            "dirty_files": status["stdout"][:500],
        }

    pull = _git(["pull", "--ff-only"], timeout=120)
    conflict = _has_conflict(pull["stdout"] + pull["stderr"])
    if not pull["ok"] or conflict:
        return {
            "ok": False,
            "error": "pull_conflict" if conflict else "pull_failed",
            "conflict": conflict,
            "message": "代码更新失败，未继续构建或重启，请检查仓库状态与网络连接",
            "detail": (pull["stderr"] or pull["stdout"])[:500],
        }
    return {"ok": True, "pull_result": pull["stdout"][:300]}


def git_pull() -> Dict[str, Any]:
    """独占运维操作期间拉取代码，并记录结果供各界面查询。"""
    error = _begin_operation("pulling", require_frontend=False)
    if error:
        return error
    result: Dict[str, Any] = {"ok": False, "error": "pull_failed"}
    try:
        result = _git_pull()
        return result
    finally:
        with _operation_lock:
            _build_state.update(building=False, result=result, phase="updated" if result.get("ok") else "failed")
