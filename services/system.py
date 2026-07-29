"""系统运行时控制服务 -- 优雅重启与前端构建重启。

重启依赖外层启动脚本（start.sh / start.bat）的退出码 42 重启循环：
服务标记重启意图并触发优雅关闭，进程以 42 退出后由脚本重新拉起。
若进程并非由启动脚本拉起，则等同于普通关闭。
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from core.lifecycle import Lifecycle
from core.log import log

_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "web" / "frontend"

# 延迟触发关闭，确保触发重启的 HTTP 响应先行返回
_SHUTDOWN_DELAY = 0.5
# 构建日志仅保留尾部，避免状态接口返回过大
_LOG_TAIL_LIMIT = 4000

_build_state: Dict[str, Any] = {"building": False, "last": None}
_build_task: Optional[asyncio.Task[None]] = None


def _schedule_shutdown() -> None:
    """延迟触发优雅关闭并标记重启意图。"""
    asyncio.get_running_loop().call_later(
        _SHUTDOWN_DELAY, Lifecycle.request_shutdown, True,
    )


def request_restart() -> Dict[str, Any]:
    """请求优雅重启（由外层启动脚本按退出码 42 重新拉起）。"""
    log("收到 WebUI 重启请求，即将优雅关闭并重启", tag="重启")
    _schedule_shutdown()
    return {"ok": True, "restarting": True}


def get_build_state() -> Dict[str, Any]:
    """查询前端构建状态（building / 最近一次构建结果）。"""
    return _build_state


def request_build_and_restart() -> Dict[str, Any]:
    """请求构建前端并重启：后台执行 npm run build，成功后自动重启，失败则取消。"""
    global _build_task
    if _build_state["building"]:
        return {"ok": False, "building": True, "error": "build_in_progress"}
    if not (_FRONTEND_DIR / "package.json").exists():
        return {"ok": False, "building": False, "error": "frontend_not_found"}
    _build_state["building"] = True
    _build_task = asyncio.create_task(_build_then_restart(), name="web.frontend_build")
    return {"ok": True, "building": True}


def _record_build_result(ok: bool, started: float, log_tail: str) -> None:
    _build_state["last"] = {
        "ok": ok,
        "duration": round(time.time() - started, 1),
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "log_tail": log_tail[-_LOG_TAIL_LIMIT:],
    }


async def _build_then_restart() -> None:
    """执行前端构建，成功后触发重启，失败仅记录结果。"""
    started = time.time()
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    log("开始构建前端（npm run build）...", tag="重启")
    try:
        proc = await asyncio.create_subprocess_exec(
            npm, "run", "build",
            cwd=str(_FRONTEND_DIR),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        text = out.decode("utf-8", errors="replace") if out else ""
        ok = proc.returncode == 0
        _record_build_result(ok, started, text)
        if ok:
            log(f"前端构建成功（{_build_state['last']['duration']}s），即将重启", tag="重启")
            _schedule_shutdown()
        else:
            log(f"前端构建失败（exit {proc.returncode}），已取消重启", "ERROR", tag="重启")
    except FileNotFoundError:
        _record_build_result(False, started, "npm 未安装或不在 PATH 中")
        log("前端构建失败：未找到 npm，已取消重启", "ERROR", tag="重启")
    except Exception as exc:
        _record_build_result(False, started, f"构建异常: {exc}")
        log(f"前端构建异常: {exc}，已取消重启", "ERROR", tag="重启")
    finally:
        _build_state["building"] = False
