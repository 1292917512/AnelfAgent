"""思维工具(编排) — Agent 内部核心编排工具。

通过 deferred_tool 模式注册，在 bootstrap 阶段由 register_multi_tool() 激活。
同步模式：阻塞执行全部任务后返回聚合结果。
后台模式：立即返回任务组 ID，后台执行，完成后主动触发新一轮思考。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from entities._sdk import deferred_tool, activate_group
from core.entity import EntityRegistry, repair_json_arguments
from core.event_bus import event_bus, EVENT_MULTI_TOOL_PROGRESS, EVENT_MULTI_TOOL_COMPLETE
from core.log import log

# ── 运行时引用（bootstrap 组装后通过 set_mind 注入）──

_pfc_ref: Any = None
_mind_ref: Any = None

_MAX_TASKS = 20
_DEFAULT_TASK_TIMEOUT = 30.0
_MAX_BACKGROUND_GROUPS = 10


def register_multi_tool() -> None:
    """批量注册多工具编排工具（PFC/Mind 引用稍后通过 set_mind 注入）。"""
    count = activate_group("thinking", "思维工具 - 对话流程控制与工具编排")
    log(f"思维工具(编排)已注册 ({count} 个)", tag="思维")


def set_mind(mind: Any) -> None:
    """延迟注入 Mind 引用（bootstrap 组装完成后调用），同时获取 PFC。"""
    global _mind_ref, _pfc_ref
    _mind_ref = mind
    _pfc_ref = mind.pfc


# ── 后台任务注册表 ──


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class TaskRecord:
    """单个子任务的运行时记录。"""

    id: str
    tool: str
    step: int
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[str] = None
    error: Optional[str] = None
    duration_ms: int = 0


@dataclass
class TaskGroup:
    """一组后台任务的运行时状态。"""

    group_id: str
    tasks: List[TaskRecord] = field(default_factory=list)
    status: str = "running"
    created_at: float = 0.0
    completed_at: float = 0.0
    reply_channel: str = ""
    reply_target: str = ""


_background_groups: Dict[str, TaskGroup] = {}


# ── 执行引擎 ──


async def _run_task(
    tool_name: str,
    args: Dict[str, Any],
    task_id: str,
    step: int,
    group_id: str = "",
) -> Dict[str, Any]:
    """执行单个子工具并返回结果记录。"""
    t0 = time.time()

    await event_bus.emit(EVENT_MULTI_TOOL_PROGRESS, {
        "group_id": group_id, "task_id": task_id,
        "tool": tool_name, "step": step, "event": "start",
    })

    try:
        args_str = json.dumps(args, ensure_ascii=False) if args else ""
        result = await EntityRegistry.execute_tool(
            tool_name, args_str, timeout=_DEFAULT_TASK_TIMEOUT,
        )
        duration_ms = round((time.time() - t0) * 1000)
        semantic_success, semantic_error = _infer_result_success(result)

        await event_bus.emit(EVENT_MULTI_TOOL_PROGRESS, {
            "group_id": group_id, "task_id": task_id,
            "tool": tool_name, "step": step,
            "event": "done", "success": semantic_success, "duration_ms": duration_ms,
        })

        output: Dict[str, Any] = {
            "id": task_id, "tool": tool_name, "step": step,
            "success": semantic_success, "result": result, "duration_ms": duration_ms,
        }
        if not semantic_success and semantic_error:
            output["error"] = semantic_error
        return output
    except Exception as exc:
        duration_ms = round((time.time() - t0) * 1000)

        await event_bus.emit(EVENT_MULTI_TOOL_PROGRESS, {
            "group_id": group_id, "task_id": task_id,
            "tool": tool_name, "step": step,
            "event": "done", "success": False, "duration_ms": duration_ms,
        })

        return {
            "id": task_id, "tool": tool_name, "step": step,
            "success": False, "error": f"{type(exc).__name__}: {exc}",
            "duration_ms": duration_ms,
        }


def _infer_result_success(result: Any) -> tuple[bool, str]:
    """根据工具返回语义判断任务成功与否（兼容 JSON 字符串返回）。"""
    payload = result
    if isinstance(result, str):
        try:
            payload = json.loads(result)
        except json.JSONDecodeError:
            return True, ""

    if isinstance(payload, dict):
        if payload.get("success") is False:
            return False, str(payload.get("error", "") or "tool returned success=false")
        if payload.get("ok") is False:
            return False, str(payload.get("error", "") or "tool returned ok=false")
        status = str(payload.get("status", "")).lower()
        if status in {"error", "failed", "failure"}:
            return False, str(payload.get("error", "") or f"tool status={status}")
        if payload.get("error"):
            return False, str(payload.get("error"))

    return True, ""


async def _execute_task_graph(
    parsed_tasks: List[Dict[str, Any]],
    group_id: str = "",
) -> List[Dict[str, Any]]:
    """按 step 分组执行任务图：同 step 并行，跨 step 串行。"""
    step_groups: Dict[int, List[Dict[str, Any]]] = {}
    for t in parsed_tasks:
        step_groups.setdefault(t["step"], []).append(t)

    all_results: List[Dict[str, Any]] = []
    for step in sorted(step_groups.keys()):
        group = step_groups[step]
        outputs = await asyncio.gather(*[
            _run_task(t["tool"], t["args"], t["id"], t["step"], group_id)
            for t in group
        ])
        all_results.extend(outputs)

    return all_results


def _build_sync_result(
    parsed_tasks: List[Dict[str, Any]],
    all_results: List[Dict[str, Any]],
) -> str:
    """构建同步执行结果。"""
    completed = sum(1 for r in all_results if r["success"])
    failed = len(all_results) - completed
    has_end_reply = any(t["tool"] == "end_reply" for t in parsed_tasks)

    result: Dict[str, Any] = {
        "success": failed == 0,
        "total": len(all_results),
        "completed": completed,
        "failed": failed,
        "results": all_results,
    }
    if has_end_reply:
        result["_end_reply"] = True

    return json.dumps(result, ensure_ascii=False)


def _parse_and_validate(tasks_input: Any) -> tuple[Optional[str], List[Dict[str, Any]]]:
    """解析并校验 tasks 参数，返回 (error_json | None, parsed_list)。"""
    if isinstance(tasks_input, list):
        task_list = tasks_input
    elif isinstance(tasks_input, str):
        # 先尝试容错修复再解析
        repaired = repair_json_arguments(tasks_input)
        try:
            task_list = json.loads(repaired)
        except json.JSONDecodeError as e:
            log(f"tasks 原始值: {tasks_input[:300]}", "DEBUG", tag="多工具")
            snippet = tasks_input[max(0, e.pos - 40):e.pos + 40] if e.pos else ""
            return json.dumps(
                {
                    "error": (
                        f"tasks JSON 解析失败(位置 {e.pos}): {e.msg}。"
                        f"出错附近: ...{snippet}..."
                        "常见原因: args 中的字符串值包含未转义的引号（中文引号"" 或英文引号）、"
                        "多余尾部逗号、缺少闭合括号。"
                        "请勿再次尝试 multi_tool_invoke——改为逐个单独调用 send_message 和 end_reply 等工具。"
                    ),
                    "fallback_hint": "放弃 multi_tool_invoke，改为直接调用各工具",
                },
                ensure_ascii=False,
            ), []
    else:
        return json.dumps({"error": "tasks 必须是数组"}, ensure_ascii=False), []

    if not isinstance(task_list, list) or not task_list:
        return json.dumps({"error": "tasks 必须是非空 JSON 数组"}, ensure_ascii=False), []

    if len(task_list) > _MAX_TASKS:
        return json.dumps(
            {"error": f"单次最多 {_MAX_TASKS} 个任务，收到 {len(task_list)} 个"},
            ensure_ascii=False,
        ), []

    parsed: List[Dict[str, Any]] = []
    for i, t in enumerate(task_list):
        if not isinstance(t, dict) or "tool" not in t:
            return json.dumps(
                {"error": f"第 {i + 1} 个任务缺少 'tool' 字段"}, ensure_ascii=False,
            ), []
        if t["tool"] in ("multi_tool_invoke", "check_multi_tool_status"):
            return json.dumps(
                {"error": f"不允许递归调用 {t['tool']}"}, ensure_ascii=False,
            ), []
        parsed.append({
            "tool": t["tool"],
            "args": t.get("args", {}),
            "step": int(t.get("step", 1)),
            "id": t.get("id", f"task_{i + 1}"),
        })

    return None, parsed


# ── 后台执行引擎 ──


async def _run_background_group(group_id: str, parsed_tasks: List[Dict[str, Any]]) -> None:
    """后台执行任务组，完成后注入结果并主动触发新一轮思考。"""
    group = _background_groups.get(group_id)
    if not group:
        return

    step_groups: Dict[int, List[Dict[str, Any]]] = {}
    for t in parsed_tasks:
        step_groups.setdefault(t["step"], []).append(t)

    task_map: Dict[str, TaskRecord] = {r.id: r for r in group.tasks}

    for step in sorted(step_groups.keys()):
        step_tasks = step_groups[step]
        for t in step_tasks:
            rec = task_map.get(t["id"])
            if rec:
                rec.status = TaskStatus.RUNNING

        outputs = await asyncio.gather(*[
            _run_task(t["tool"], t["args"], t["id"], t["step"], group_id)
            for t in step_tasks
        ])

        for output in outputs:
            rec = task_map.get(output["id"])
            if rec:
                rec.status = TaskStatus.COMPLETED if output["success"] else TaskStatus.FAILED
                rec.result = output.get("result")
                rec.error = output.get("error")
                rec.duration_ms = output.get("duration_ms", 0)

    group.status = "completed"
    group.completed_at = time.time()

    completed = sum(1 for r in group.tasks if r.status == TaskStatus.COMPLETED)
    failed = sum(1 for r in group.tasks if r.status == TaskStatus.FAILED)

    detail_lines: List[str] = []
    for r in group.tasks:
        if r.status == TaskStatus.COMPLETED:
            detail_lines.append(f"  [{r.id}] {r.tool}: {(r.result or '')[:500]}")
        else:
            detail_lines.append(f"  [{r.id}] {r.tool}: 失败 - {r.error}")

    notification = (
        f"[后台任务完成] group_id={group_id} | "
        f"成功 {completed}，失败 {failed}\n"
        + "\n".join(detail_lines)
    )

    log(f"后台任务组完成: {group_id} ({completed}/{len(group.tasks)})", tag="多工具")

    await event_bus.emit(EVENT_MULTI_TOOL_COMPLETE, {
        "group_id": group_id,
        "total": len(group.tasks),
        "completed": completed,
        "failed": failed,
    })

    # 保留已完成任务组供后续 check_multi_tool_status 查询（超限后统一清理）
    _cleanup_old_groups()

    # 完成后注入结果并主动触发下一轮
    if _mind_ref is None:
        return

    if group.reply_target:
        from agent.messages import MessageToolResult

        result_msg = MessageToolResult(
            uid=group.reply_target,
            adapter_key=group.reply_channel,
        )
        result_msg.set_text_content(notification)

        try:
            await _mind_ref.accept_feel(result_msg)
            log(f"后台任务结果已通过 accept_feel 注入: {group_id}", tag="多工具")
            if not _mind_ref.is_reply:
                asyncio.create_task(_mind_ref.execute_mind())
        except Exception as exc:
            log(f"后台任务结果注入失败: {exc}", "WARNING", tag="多工具")
        return

    # 无 reply_target（如任务 reflect 场景）：注入通用任务并在空闲后主动触发一轮
    if _pfc_ref is None:
        log(f"后台任务完成但未注入（PFC 未就绪）: {group_id}", "WARNING", tag="多工具")
        return
    try:
        from agent.mind.autonomous import MindTask, TaskType

        _pfc_ref.add_temporary({
            "role": "user",
            "content": (
                f"{notification}\n"
                "[系统提示] 后台任务已完成，请基于结果继续当前流程；"
                "若无需继续请调用 end_reply 结束。"
            ),
        })
        _pfc_ref.add_general_task(MindTask(
            task_type=TaskType.SELF_TASK,
            preview=f"后台任务完成: {group_id}",
            metadata={"source": "multi_tool_async", "group_id": group_id},
        ))
        log(f"后台任务结果已注入通用任务: {group_id}", tag="多工具")
        # 复用 Mind 内部 _cycle_lock 串行能力：若当前仍在执行，本任务会排队等待后自动触发。
        asyncio.create_task(_mind_ref.execute_mind())
    except Exception as exc:
        log(f"后台任务通用注入失败: {exc}", "WARNING", tag="多工具")


def _cleanup_old_groups() -> None:
    """保留最近的后台任务组，清理过期条目。"""
    if len(_background_groups) <= _MAX_BACKGROUND_GROUPS:
        return
    sorted_groups = sorted(
        _background_groups.items(), key=lambda x: x[1].created_at,
    )
    for gid, _ in sorted_groups[:-_MAX_BACKGROUND_GROUPS]:
        del _background_groups[gid]


# ── 工具定义 ──


@deferred_tool(
    group="thinking", tags=["always"], source="mind.multi_tool",
    description=(
        "批量执行多个工具调用。同一 step 并行执行、不同 step 按序执行，全部完成后一起返回结果。"
        "async_mode=true 时不阻塞当前对话，任务在后台执行完成后系统自动触发新一轮思考并提供结果。"
        "可通过 auto_end 控制是否立即结束本轮。"
    ),
    timeout=180.0,
)
async def multi_tool_invoke(tasks: list, async_mode: bool = False, auto_end: bool = False) -> str:
    """批量执行多个工具调用，支持并行/顺序编排和异步后台执行。

    Args:
        tasks: 工具调用数组，每个元素必须是对象: {"tool": "工具名", "args": {"参数名": 值}, "step": 阶段号整数默认1, "id": "可选标识"}。同一step并行执行，不同step按序执行。
        async_mode: 为 true 时不阻塞当前对话，任务在后台执行，完成后系统自动触发新一轮思考并提供结果
        auto_end: 仅在 async_mode=true 时生效。True 表示本轮自动结束，False 表示继续当前轮由 AI 自主决定（默认 False）
    """
    error, parsed = _parse_and_validate(tasks)
    if error:
        return error

    if async_mode:
        group_id = f"mt_{uuid.uuid4().hex[:8]}"
        reply_channel = ""
        reply_target = ""
        if _mind_ref is not None:
            reply_channel = getattr(_mind_ref, "_reply_adapter_key", "") or ""
            for scope in getattr(_mind_ref, "_active_scopes", set()):
                if scope.startswith("user_"):
                    reply_target = scope[5:]
                elif scope.startswith("group_"):
                    reply_target = scope[6:]
                break

        group = TaskGroup(
            group_id=group_id,
            tasks=[TaskRecord(id=t["id"], tool=t["tool"], step=t["step"]) for t in parsed],
            created_at=time.time(),
            reply_channel=reply_channel,
            reply_target=reply_target,
        )
        _background_groups[group_id] = group

        step_set = {t["step"] for t in parsed}
        log(f"后台任务组启动: {group_id} ({len(parsed)} 个任务, {len(step_set)} 个阶段)", tag="多工具")
        asyncio.create_task(_run_background_group(group_id, parsed))

        result: Dict[str, Any] = {
            "status": "running",
            "group_id": group_id,
            "total_tasks": len(parsed),
            "total_steps": len(step_set),
        }
        if auto_end:
            result["_end_reply"] = True
            result["hint"] = "任务已在后台执行，本轮将自动结束。完成后系统会自动触发新一轮思考并注入结果"
        else:
            result["hint"] = "任务已在后台执行。你可继续当前轮操作，或调用 end_reply 主动结束"
        return json.dumps(result, ensure_ascii=False)

    # 同步模式
    group_id = f"mt_{uuid.uuid4().hex[:8]}"
    log(f"同步任务组开始: {group_id} ({len(parsed)} 个任务)", tag="多工具")
    all_results = await _execute_task_graph(parsed, group_id)
    return _build_sync_result(parsed, all_results)


multi_tool_invoke._schema_extra = {  # type: ignore[attr-defined]
    "async_mode": {
        "type": "boolean",
        "description": "是否后台异步执行。true 时立即返回任务组信息，后台完成后会主动触发后续思考。",
    },
    "auto_end": {
        "type": "boolean",
        "description": "仅 async_mode=true 时生效。true 自动结束本轮，false 继续当前轮。",
    },
    "tasks": {
        "items": {
            "type": "object",
            "properties": {
                "tool": {"type": "string", "description": "工具名"},
                "args": {"type": "object", "description": "工具参数"},
                "step": {"type": "integer", "description": "阶段号，同 step 并行，不同 step 串行，默认 1"},
                "id": {"type": "string", "description": "可选标识"},
            },
            "required": ["tool", "args"],
        },
    },
}


@deferred_tool(
    group="thinking", tags=["always"], source="mind.multi_tool",
    description="查看后台多工具任务状态。不传 group_id 时列出所有后台任务组概况，传了则查看指定任务组的详细进度和结果。",
)
async def check_multi_tool_status(group_id: str = "") -> str:
    """查看后台多工具任务状态：列出全部任务组或查看指定任务组详情。

    Args:
        group_id: 任务组 ID，为空时列出所有后台任务组概况
    """
    if not group_id:
        return _list_all_groups()

    group = _background_groups.get(group_id)
    if not group:
        return json.dumps({"error": f"任务组 '{group_id}' 不存在，可能已完成（结果已在上下文中）"}, ensure_ascii=False)

    return _build_group_detail(group)


def _list_all_groups() -> str:
    """列出后台任务组概况（运行中 + 已完成保留项）。"""
    if not _background_groups:
        return json.dumps({"running": 0, "total": 0, "hint": "当前无后台任务"}, ensure_ascii=False)

    groups_info: List[Dict[str, Any]] = []
    running_count = 0
    for g in _background_groups.values():
        done = sum(1 for r in g.tasks if r.status in (TaskStatus.COMPLETED, TaskStatus.FAILED))
        if g.status == "running":
            running_count += 1
        info: Dict[str, Any] = {
            "group_id": g.group_id,
            "status": g.status,
            "progress": f"{done}/{len(g.tasks)}",
            "task_count": len(g.tasks),
        }
        running_tools = [r.tool for r in g.tasks if r.status == TaskStatus.RUNNING]
        if running_tools:
            info["running_tools"] = running_tools
        groups_info.append(info)

    return json.dumps({
        "running": running_count,
        "total": len(groups_info),
        "groups": groups_info,
    }, ensure_ascii=False)


def _build_group_detail(group: TaskGroup) -> str:
    """构建单个任务组的详细状态。"""
    done_count = sum(1 for r in group.tasks if r.status in (TaskStatus.COMPLETED, TaskStatus.FAILED))

    result: Dict[str, Any] = {
        "group_id": group.group_id,
        "status": group.status,
        "progress": f"{done_count}/{len(group.tasks)}",
    }

    if group.status == "completed":
        elapsed = group.completed_at - group.created_at
        result["total_duration_ms"] = round(elapsed * 1000)
        result["results"] = [
            {
                "id": r.id, "tool": r.tool, "step": r.step,
                "success": r.status == TaskStatus.COMPLETED,
                **({"result": r.result} if r.result else {}),
                **({"error": r.error} if r.error else {}),
                "duration_ms": r.duration_ms,
            }
            for r in group.tasks
        ]
    else:
        completed_tasks = [r for r in group.tasks if r.status == TaskStatus.COMPLETED]
        failed_tasks = [r for r in group.tasks if r.status == TaskStatus.FAILED]
        running_tasks = [r for r in group.tasks if r.status == TaskStatus.RUNNING]
        pending_tasks = [r for r in group.tasks if r.status == TaskStatus.PENDING]

        if completed_tasks:
            result["completed_tasks"] = [
                {"id": r.id, "tool": r.tool, "duration_ms": r.duration_ms}
                for r in completed_tasks
            ]
        if failed_tasks:
            result["failed_tasks"] = [
                {"id": r.id, "tool": r.tool, "error": r.error, "duration_ms": r.duration_ms}
                for r in failed_tasks
            ]
        if running_tasks:
            result["running_tasks"] = [
                {"id": r.id, "tool": r.tool, "step": r.step} for r in running_tasks
            ]
        if pending_tasks:
            result["pending_tasks"] = [
                {"id": r.id, "tool": r.tool, "step": r.step} for r in pending_tasks
            ]

    return json.dumps(result, ensure_ascii=False)


# ── 延迟触发回复 ──

_MAX_DELAY = 600


@deferred_tool(
    group="thinking", tags=["core"], source="mind.multi_tool",
    description="延迟指定秒数后自动触发一轮新的对话回复，适用于需要等一会儿再主动联系用户的场景。",
)
async def schedule_reply(delay_seconds: int = 30, reason: str = "") -> str:
    """延迟指定秒数后自动触发一轮新的对话回复。

    Args:
        delay_seconds: 延迟秒数（1-600），默认30秒
        reason: 延迟原因，会作为提示注入下一轮上下文
    """
    if not _pfc_ref or not _mind_ref:
        return json.dumps({"error": "系统未就绪"}, ensure_ascii=False)

    delay = max(1, min(delay_seconds, _MAX_DELAY))

    reply_channel = getattr(_mind_ref, "_reply_adapter_key", "") or ""
    reply_target = ""
    for scope in getattr(_mind_ref, "_active_scopes", set()):
        if scope.startswith("user_"):
            reply_target = scope[5:]
        elif scope.startswith("group_"):
            reply_target = scope[6:]
        break

    if not reply_target:
        return json.dumps({"error": "无法确定回复目标"}, ensure_ascii=False)

    log(f"计划 {delay}s 后触发回复: target={reply_target} reason={reason}", tag="多工具")
    asyncio.create_task(_delayed_reply(delay, reply_channel, reply_target, reason))

    return json.dumps({
        "ok": True,
        "delay_seconds": delay,
        "target": reply_target,
        "channel": reply_channel,
        "hint": f"{delay}秒后系统将自动触发一轮新的对话回复",
    }, ensure_ascii=False)


async def _delayed_reply(delay: int, channel: str, target: str, reason: str) -> None:
    """等待指定秒数后触发一轮 REPLY。"""
    await asyncio.sleep(delay)

    if not _pfc_ref or not _mind_ref:
        return

    prompt = f"[定时提醒] 你 {delay} 秒前设定了一个延迟回复"
    if reason:
        prompt += f"，原因：{reason}"
    prompt += "。请决定下一步操作。"

    _pfc_ref.add_temporary({"role": "user", "content": prompt})

    scope = f"user_{target}"
    _pfc_ref.pending_user.append(target)
    _pfc_ref._message_previews[scope] = f"定时回复: {reason or '延迟触发'}"
    if channel:
        _pfc_ref._task_adapter_keys[scope] = channel

    log(f"延迟 {delay}s 到期，触发回复: {scope}", tag="多工具")
    asyncio.create_task(_mind_ref.try_execute_mind())
