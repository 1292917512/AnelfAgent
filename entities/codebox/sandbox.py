"""代码编排沙箱（父进程侧）：子进程生命周期 + JSON 行协议 + 工具桥。

设计要点（对照 pi codemode，按 Anelf 信任模型裁剪，见 AGENTS.md 否决录 #4）：
- 一次性执行环境：每次运行一个新子进程，跑完/超时即杀，失控脚本不污染后续
- 资源上限在子进程内自设（rlimit，见 runner.py）；墙钟超时由本侧强杀
- 工具桥：每次子调用过统一审批门（channel=None 语义，同 workflow 引擎样板）
  + EntityRegistry.execute_tool；输出/编排/交互类工具在脚本内不可用
- 子调用结果截断为头尾保留：脚本可用带 offset/limit 的调用自行分页精读，
  比落盘预览更贴合脚本语义（脚本有变量，不需要文件兜底）
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import get_config_int
from core.log import log
from core.tool_errors import ErrorCause, tool_error

_TAG = "代码编排"

# 脚本内不可用的工具：
# - 输出类（send_message/send_photo/send_voice/send_file/end_reply/schedule_reply）：
#   脚本不能回复用户——结果 print 出来，回复在脚本外组织
# - 编排类（activate/deactivate_tool_group/delegate_task/workflow_*/cancel_*）：
#   脚本是 worker 不是 orchestrator，嵌套编排会让审批与取消语义不可追踪
# - 交互类（ui_ask）：脚本内无人可问，调用即挂起
# - run_python 自身：禁止嵌套子进程
_EXCLUDED_TOOLS = frozenset({
    "send_message", "send_photo", "send_voice", "send_file",
    "end_reply", "schedule_reply",
    "activate_tool_group", "deactivate_tool_group",
    "delegate_task", "cancel_delegation",
    "workflow_start", "workflow_stop", "workflow_resume", "cancel_plan",
    "ui_ask", "run_python",
})

_CHILD_ENV_KEYS = (
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TMPDIR",
    "SYSTEMROOT", "COMSPEC", "PATHEXT",
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _child_env() -> Dict[str, str]:
    """子进程环境：白名单继承（防敏感变量泄露）+ 资源上限参数。"""
    env = {k: os.environ[k] for k in _CHILD_ENV_KEYS if k in os.environ}
    env["PYTHONIOENCODING"] = "utf-8"
    env["ANELF_CODEBOX_MEM_MB"] = str(get_config_int("codebox_memory_limit_mb", 1024))
    return env


async def _dispatch(tool_name: str, args: Dict[str, Any], result_cap: int) -> Dict[str, Any]:
    """校验并执行脚本子调用，完整返回数据或明确报告失败及资源限制。

    返回 {"ok", "result"|"error", "ledger"}；ledger 供运行结束后的调用账目汇总。
    """
    started = time.monotonic()
    ledger: Dict[str, Any] = {"tool": tool_name, "ok": False, "ms": 0}

    def _finish(ok: bool) -> Dict[str, Any]:
        ledger["ok"] = ok
        ledger["ms"] = int((time.monotonic() - started) * 1000)
        return ledger

    if tool_name == "__list__":
        catalog = _tool_catalog()
        _finish(True)
        return {"ok": True, "result": catalog, "ledger": ledger}
    if tool_name in _EXCLUDED_TOOLS:
        _finish(False)
        return {
            "ok": False,
            "error": f"工具 {tool_name} 在脚本内不可用：输出/编排/交互类操作请在脚本外进行"
            "（把结果 print 出来，由你在脚本外组织回复或后续编排）",
            "ledger": ledger,
        }

    try:
        from entities._sdk import check_tool_permission
        permission = await check_tool_permission(tool_name, args, f"代码编排脚本调用 {tool_name}")
        if permission["notice"]:
            ledger["permission_notice"] = permission["notice"]
        if not permission["allowed"]:
            _finish(False)
            return {"ok": False, "error": permission["reason"], "retryable": False, "ledger": ledger}
    except Exception as exc:
        _finish(False)
        log(f"代码编排权限检查异常: {type(exc).__name__}", "ERROR", tag=_TAG)
        return {"ok": False, "error": "权限检查未完成，工具未执行", "ledger": ledger}

    from core.entity import EntityRegistry
    try:
        result = await EntityRegistry.execute_tool(
            tool_name, json.dumps(args, ensure_ascii=False)
        )
    except Exception as exc:  # execute_tool 正常不抛（错误走 JSON），此为防御兜底
        _finish(False)
        return {"ok": False, "error": f"工具执行异常: {exc}"[:500], "ledger": ledger}

    from core.tool_results import extract_error_text

    if extract_error_text(result):
        _finish(False)
        return {"ok": False, "error": result[:2000], "ledger": ledger}

    if len(result) > result_cap:
        _finish(False)
        return {
            "ok": False,
            "error": tool_error(
                f"工具已执行，结果超过脚本数据上限（{len(result)}/{result_cap} 字符）",
                cause=ErrorCause.STATE, retryable=False, code="CODEBOX_RESULT_LIMIT",
                outcome="completed",
                hint="不要重做已执行的写操作；查询类结果请使用分页或更窄的查询范围。",
            ),
            "ledger": ledger,
        }

    _finish(True)
    return {"ok": True, "result": result, "ledger": ledger}


def _tool_catalog() -> str:
    """脚本内可调用工具目录（名称 + 一句话描述；排除清单外全部启用工具）。"""
    from core.entity import EntityRegistry, EntityType

    entries: List[Dict[str, str]] = []
    for group in EntityRegistry.get_entity_catalog():
        for e in EntityRegistry.get_by_group(group["group"]):
            if e.entity_type != EntityType.TOOL or not e.enabled:
                continue
            if e.name in _EXCLUDED_TOOLS:
                continue
            entries.append({"name": e.name, "description": (e.description or "")[:80]})
    entries.sort(key=lambda item: item["name"])
    return json.dumps(entries, ensure_ascii=False)


async def run_script(code: str, *, timeout: int = 0, workspace_root: str = "") -> str:
    """在一次性子进程中运行编排脚本，返回结果文本（成功）或统一错误 JSON。

    成功结果形如「[代码编排] 完成：N 次工具调用（M 次失败），耗时 Xs\\n<打印输出>」；
    失败为 tool_error JSON（顶层 error 键是框架各处的失败判定信号）。
    """
    if not code.strip():
        return tool_error("脚本为空", cause=ErrorCause.PARAM,
                          hint="传入 Python 脚本源码；脚本内用 tools.<工具名>(参数=值) 调用工具")
    default_timeout = get_config_int("codebox_default_timeout", 120)
    max_timeout = get_config_int("codebox_max_timeout", 600)
    try:
        timeout = int(timeout) if timeout else default_timeout
    except (TypeError, ValueError):
        timeout = default_timeout
    timeout = max(1, min(timeout, max_timeout))

    if not workspace_root:
        from entities.filesystem.paths import get_workspace_root
        workspace_root = get_workspace_root()
    work_dir = Path(workspace_root) / ".codebox"
    work_dir.mkdir(parents=True, exist_ok=True)
    script_path = work_dir / f"script-{uuid.uuid4().hex[:8]}.py"
    script_path.write_text(code, encoding="utf-8")

    env = _child_env()
    env["ANELF_CODEBOX_CPU_S"] = str(timeout + 60)
    max_output = get_config_int("codebox_output_chars", 20000)
    max_calls = get_config_int("codebox_max_tool_calls", 100)
    result_cap = max(1024, get_config_int("codebox_max_result_chars", 2_000_000))

    started = time.monotonic()
    deadline = started + timeout
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(_REPO_ROOT / "entities" / "codebox" / "runner.py"),
        str(script_path),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=workspace_root,
        env=env,
        limit=1024 * 1024,
    )
    assert proc.stdout is not None  # stdout=PIPE 必然存在（mypy 收窄）

    output_parts: List[str] = []
    output_chars = 0
    output_truncated = False
    stderr_parts: List[str] = []
    ledger: List[Dict[str, Any]] = []
    done_info: Optional[Dict[str, Any]] = None
    timed_out = False
    pending_tool = ""

    async def _pump_stderr() -> None:
        assert proc.stderr is not None
        async for raw in proc.stderr:
            if sum(len(p) for p in stderr_parts) < 4000:
                stderr_parts.append(raw.decode("utf-8", "replace"))

    stderr_task = asyncio.ensure_future(_pump_stderr())
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            try:
                raw = await asyncio.wait_for(proc.stdout.readline(), remaining)
            except asyncio.TimeoutError:
                timed_out = True
                break
            if not raw:
                break  # 子进程退出（EOF）
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except ValueError:
                # 协议外输出按原文保留（runner 故障或脚本绕过协议直写的线索）
                output_parts.append(line + "\n")
                output_chars += len(line) + 1
                continue
            kind = frame.get("f")
            if kind in ("out", "err"):
                text = str(frame.get("text", ""))
                if kind == "err":
                    text = "[stderr] " + text
                if output_chars < max_output:
                    output_parts.append(text + "\n")
                    output_chars += len(text) + 1
                else:
                    output_truncated = True
            elif kind == "call":
                if len(ledger) >= max_calls:
                    await _reply(proc, {
                        "f": "ret", "id": frame.get("id"), "ok": False,
                        "error": f"单次运行工具调用上限 {max_calls} 次已达；"
                                 "请缩小本次任务范围，剩余工作拆到下一次 run_python",
                    })
                    continue
                try:
                    pending_tool = str(frame.get("tool") or "")
                    result = await asyncio.wait_for(
                        _dispatch(pending_tool, frame.get("args") or {}, result_cap),
                        timeout=max(0.0, deadline - time.monotonic()),
                    )
                except asyncio.TimeoutError:
                    timed_out = True
                    break
                ledger.append(result["ledger"])
                pending_tool = ""
                payload: Dict[str, Any] = {"f": "ret", "id": frame.get("id"), "ok": result["ok"]}
                if result["ok"]:
                    payload["result"] = result["result"]
                else:
                    payload["error"] = result["error"]
                await _reply(proc, payload)
            elif kind == "done":
                done_info = frame
                break
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        stderr_task.cancel()
        await asyncio.gather(stderr_task, return_exceptions=True)
        script_path.unlink(missing_ok=True)

    elapsed = time.monotonic() - started
    output = "".join(output_parts).strip()
    if output_truncated:
        output += f"\n…（打印输出超出 {max_output} 字符，已截断）"
    failed_calls = sum(1 for c in ledger if not c["ok"])

    if timed_out:
        return tool_error(
            f"脚本运行超时（{timeout}s），进程已强制终止",
            cause=ErrorCause.TIMEOUT, retryable=False, outcome="unknown",
            hint="已调用的工具可能生效；先只读核验状态或产物，保留已完成部分，再决定续做范围。",
            output_so_far=output[-4000:], calls=len(ledger) + bool(pending_tool),
            pending_tool=pending_tool, completed_calls=len(ledger),
        )
    if done_info is None:
        diag = "".join(stderr_parts).strip()[:1000]
        return tool_error(
            "脚本进程异常退出（未收到收尾帧）",
            cause=ErrorCause.INTERNAL, retryable=False,
            hint=diag or "子进程崩溃且无诊断输出；请简化脚本后重试",
            output_so_far=output[-2000:], calls=len(ledger),
        )
    if not done_info.get("ok"):
        tb = str(done_info.get("error") or "")
        last_line = tb.strip().splitlines()[-1] if tb.strip() else "未知异常"
        return tool_error(
            f"脚本执行异常: {last_line}",
            cause=ErrorCause.INTERNAL, retryable=False,
            hint="完整 traceback 在 output_so_far 末尾；修复后重新运行",
            output_so_far=(output + "\n" + tb)[-4000:], calls=len(ledger),
        )

    header = f"[代码编排] 完成：{len(ledger)} 次工具调用"
    if failed_calls:
        header += f"（{failed_calls} 次失败，见输出中的 ToolError 处理）"
    header += f"，耗时 {elapsed:.1f}s"
    return f"{header}\n{output or '（脚本无输出）'}"


async def _reply(proc: asyncio.subprocess.Process, payload: Dict[str, Any]) -> None:
    """向子进程写入一帧响应（stdin 是 parent→child 唯一通道）。"""
    assert proc.stdin is not None
    proc.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode())
    await proc.stdin.drain()
