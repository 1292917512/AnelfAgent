"""Plan 状态追踪器 — 计划模式程序级进度的统一实现。

设计原则（参考 hermes 的 progress callback / Claude Code 的
updateProgressFromMessage）：进度由程序从执行流自动推断，AI 不需要
主动汇报；AI 调 update_goal 只是"可选的精确标记"，不是必要条件。

本模块是 plan 状态机与事件发射的**唯一入口**，消费者：
- ``agent/planning/tools.py``：present_plan / update_goal 工具
- ``agent/mind/tools/think_loop.py``：每轮自动推进 + 会话结束收敛 + 纯文本守卫
- ``web/routers/chat.py``：cancel-plan 路由

三层进度机制：
1. ``submit_plan``：present_plan 工具内调用，公告计划 + 首步 in_progress
2. ``advance_plan_step``：每轮工具批次后推进当前步骤（粗粒度兜底）
3. ``finalize_plan``：think_loop 全退出路径的唯一收敛入口（诚实语义：
   正常结束 in_progress → completed；中断/取消 in_progress → skipped；
   pending 一律 → skipped，不假装完成）

守卫：``guard_feedback_for_text_only`` —— AI 有未完成 plan 却输出纯文本
（无工具调用）时，think_loop 在"无工具正文 = 最终回复"终态前调用本函数
注入提醒，防止"计划公告了但一步没做就结束"。
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from agent.memory.memory_store import MemoryStore
from agent.memory.memory_types import MemoryEntry, MemoryType
from core.event_bus import (
    EVENT_PLAN_CANCELLED,
    EVENT_PLAN_STATUS_CHANGED,
    EVENT_PLAN_STEP_UPDATED,
    EVENT_PLAN_SUBMITTED,
    event_bus,
)
from core.log import log

GOAL_SOURCE = "goal"

# 计划管理工具：调用它们不算"执行了一步"，不触发自动推进。
# 否则 present_plan 当轮 step 0 就被误标完成（进度超前 bug）。
PLAN_MANAGEMENT_TOOL_NAMES = frozenset({
    "present_plan", "update_goal", "create_goal", "list_goals", "get_goal", "delete_goal",
})

_store: Optional[MemoryStore] = None


def bind_store(store: MemoryStore) -> None:
    """注入 MemoryStore（由 planning.tools.register_planning_tools 调用）。"""
    global _store
    _store = store


# ------------------------------------------------------------------
# scope 工具（delegation / plan / webui 共享）
# ------------------------------------------------------------------

def current_scope() -> str:
    """读取当前对话 scope（ContextVar 绑定，think_loop 会话期间有效）。"""
    try:
        from agent.mind.tool_activation import ToolActivationManager
        return ToolActivationManager.current_scope()
    except Exception:
        return "_global"


def parse_scope_chat_id(scope: str) -> Tuple[str, str]:
    """从 scope 提取 (base_scope, chat_id)。

    scope 形如 ``user_web_user`` 或 ``user_web_user#abc123``。
    """
    if "#" in scope:
        base, chat_id = scope.split("#", 1)
        return base, chat_id
    return scope, ""


def make_scope(user_id: str, chat_id: str = "") -> str:
    """由 user_id / chat_id 构造 scope（cancel-plan 路由等外部调用方用）。

    user_id 为含 adapter 前缀的 scope 基 id（如 ``webui:web_user``），
    产出与 entity_scope 同格式（``user_webui:web_user[#chat_id]``）。
    """
    base = f"user_{user_id}"
    return f"{base}#{chat_id}" if chat_id else base


# ------------------------------------------------------------------
# 内部：active plan 查询（三个消费者共享，消除三份重复遍历）
# ------------------------------------------------------------------

def _goal_scope_matches(entry: MemoryEntry, scope: str) -> bool:
    """plan 的 metadata.scope 与当前 scope 匹配（空 scope 视为全局，兼容旧数据）。"""
    plan_scope = (entry.metadata or {}).get("scope", "")
    return not plan_scope or plan_scope == scope


async def _find_active_plan(
    scope: str,
) -> Tuple[Optional[MemoryEntry], Optional[Dict[str, Any]]]:
    """查找当前 scope 的 active plan（取最近一条）。

    Returns:
        (entry, goal) 或 (None, None)。
    """
    if _store is None:
        return None, None
    try:
        entries = await _store.list_recent(
            limit=20, memory_type=MemoryType.SEMANTIC, source=GOAL_SOURCE,
        )
    except Exception as exc:
        log(f"active plan 查询失败: {exc}", "DEBUG", tag="规划")
        return None, None
    for entry in entries:
        try:
            goal = json.loads(entry.content)
        except (json.JSONDecodeError, AttributeError):
            continue
        if goal.get("status") != "active":
            continue
        if not _goal_scope_matches(entry, scope):
            continue
        return entry, goal
    return None, None


async def get_active_plan(scope: str) -> Optional[Dict[str, Any]]:
    """公开查询：当前 scope 的 active plan（goal dict），无则 None。

    present_plan 工具据此复用已有计划（防止 AI 重复规划）。
    """
    _, goal = await _find_active_plan(scope)
    return goal


async def _persist(entry: MemoryEntry, goal: Dict[str, Any]) -> None:
    """写回 MemoryStore（不清 embedding，避免后台 worker 频繁重建）。"""
    goal["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    entry.content = json.dumps(goal, ensure_ascii=False)
    await _store.update(entry, clear_embedding=False)


# ------------------------------------------------------------------
# 内部：事件发射（统一 payload 构造 + scope/chat_id 解析）
# ------------------------------------------------------------------

async def _emit_step(
    scope: str, plan_id: str, step_index: int, status: str, note: str = "",
) -> None:
    _, chat_id = parse_scope_chat_id(scope)
    try:
        await event_bus.emit(EVENT_PLAN_STEP_UPDATED, {
            "scope": scope, "chat_id": chat_id,
            "plan_id": plan_id, "step_index": step_index,
            "step_status": status, "note": note, "ts": time.time(),
        })
    except Exception as exc:
        log(f"plan 步骤事件发射失败: {exc}", "DEBUG", tag="规划")


async def _emit_status(scope: str, plan_id: str, status: str) -> None:
    _, chat_id = parse_scope_chat_id(scope)
    try:
        await event_bus.emit(EVENT_PLAN_STATUS_CHANGED, {
            "scope": scope, "chat_id": chat_id,
            "plan_id": plan_id, "goal_status": status, "ts": time.time(),
        })
    except Exception as exc:
        log(f"plan 状态事件发射失败: {exc}", "DEBUG", tag="规划")


# ------------------------------------------------------------------
# 公共 API
# ------------------------------------------------------------------

def parse_steps(steps_text: str) -> List[Dict[str, Any]]:
    """解析步骤文本为步骤对象列表（兼容 \\n 与 | 分隔），首步 in_progress。"""
    raw = [
        s.strip()
        for s in (steps_text.replace("|", "\n").split("\n") if steps_text else [])
        if s.strip()
    ]
    steps = [
        {"index": i, "content": s, "status": "pending", "note": ""}
        for i, s in enumerate(raw)
    ]
    if steps:
        steps[0]["status"] = "in_progress"
    return steps


async def submit_plan(
    scope: str, goal: str, steps: List[Dict[str, Any]],
    files: str = "", risks: str = "",
) -> str:
    """公告计划：持久化 + 发射 plan_submitted / 首步 in_progress 事件。

    Returns:
        plan_id
    """
    plan_id = uuid.uuid4().hex[:8]
    _, chat_id = parse_scope_chat_id(scope)

    if _store is not None:
        try:
            goal_doc = {
                "goal_id": plan_id,
                "title": goal,
                "description": "",
                "status": "active",
                "recurring": False,
                "steps": steps,
                "files": files,
                "risks": risks,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            entry = MemoryEntry(
                memory_type=MemoryType.SEMANTIC,
                content=json.dumps(goal_doc, ensure_ascii=False),
                source=GOAL_SOURCE,
                importance=0.8,
                metadata={
                    "goal_id": plan_id, "status": "active",
                    "kind": "present_plan", "scope": scope,
                },
            )
            await _store.add(entry)
        except Exception as exc:
            log(f"plan 持久化失败（不影响执行）: {exc}", "WARNING", tag="规划")

    try:
        await event_bus.emit(EVENT_PLAN_SUBMITTED, {
            "scope": scope, "chat_id": chat_id, "plan_id": plan_id,
            "goal": goal, "steps": steps, "files": files, "risks": risks,
            "ts": time.time(),
        })
    except Exception as exc:
        log(f"plan_submitted 事件发射失败（不影响执行）: {exc}", "DEBUG", tag="规划")

    if steps:
        await _emit_step(scope, plan_id, 0, "in_progress", note="自动推进")
    return plan_id


async def advance_plan_step(scope: str) -> None:
    """每轮实际工具批次后推进当前步骤（粗粒度兜底）。

    把当前 in_progress 步骤标记 completed，推进下一 pending 为 in_progress。
    仅处理当前 scope 的 active plan；无 active plan 时快速返回。
    """
    if _store is None or current_scope() != scope:
        return
    try:
        entry, goal = await _find_active_plan(scope)
        if goal is None:
            return
        steps = goal.get("steps", [])
        for i, s in enumerate(steps):
            if s.get("status") != "in_progress":
                continue
            s["status"] = "completed"
            next_idx = i + 1
            has_next = (
                next_idx < len(steps)
                and steps[next_idx].get("status") == "pending"
            )
            if has_next:
                steps[next_idx]["status"] = "in_progress"
            await _persist(entry, goal)
            plan_id = goal.get("goal_id", "")
            await _emit_step(scope, plan_id, i, "completed", note="自动推进")
            if has_next:
                await _emit_step(scope, plan_id, next_idx, "in_progress", note="自动推进")
            return
    except Exception:
        pass  # 自动推进失败不影响主流程


async def _converge_steps(
    scope: str, plan_id: str, steps: List[Dict[str, Any]], outcome: str,
) -> bool:
    """把未完成步骤按 outcome 收敛到终态并逐步发射事件。

    - completed：in_progress → completed（当前正在做的视为做完），pending → skipped
    - cancelled：in_progress / pending 统一 → skipped（中断的步骤不假装完成）

    Returns:
        是否有步骤变更。
    """
    note = "会话结束自动收束" if outcome == "completed" else "会话中断，步骤未执行"
    changed = False
    for s in steps:
        st = s.get("status")
        if st == "in_progress":
            s["status"] = "completed" if outcome == "completed" else "skipped"
            changed = True
            await _emit_step(scope, plan_id, s.get("index", 0), s["status"], note=note)
        elif st == "pending":
            s["status"] = "skipped"
            changed = True
            await _emit_step(scope, plan_id, s.get("index", 0), "skipped", note=note)
    return changed


async def finalize_plan(scope: str, outcome: str = "completed") -> None:
    """会话结束时收敛 active plan 到终态（全退出路径唯一收敛入口）。

    - outcome="completed"（正常结束）：in_progress → completed，pending → skipped，
      plan → completed
    - outcome="cancelled"（中断 / 安全上限等异常结束）：in_progress / pending →
      skipped，plan → cancelled
    无 active plan 时零成本返回；状态机幂等，可在 finally 中安全调用。
    """
    if _store is None:
        return
    try:
        entry, goal = await _find_active_plan(scope)
        if goal is None:
            return
        plan_id = goal.get("goal_id", "")
        changed = await _converge_steps(scope, plan_id, goal.get("steps", []), outcome)
        if changed or goal.get("status") != outcome:
            goal["status"] = outcome
            await _persist(entry, goal)
            await _emit_status(scope, plan_id, outcome)
    except Exception as exc:
        log(f"plan 收敛失败（不影响主流程）: {exc}", "DEBUG", tag="规划")


async def cancel_plan(scope: str, plan_id: str, reason: str = "用户取消") -> bool:
    """取消计划：标记 cancelled + 发射事件 + interrupt scope。

    Returns:
        是否找到并取消了该 plan。
    """
    if _store is None:
        return False
    try:
        entry, goal = await _find_active_plan(scope)
        if goal is None or goal.get("goal_id") != plan_id:
            return False
        # 步骤同步收敛到 skipped，避免前端残留 in_progress 转圈
        await _converge_steps(scope, plan_id, goal.get("steps", []), "cancelled")
        goal["status"] = "cancelled"
        await _persist(entry, goal)
        _, chat_id = parse_scope_chat_id(scope)
        try:
            await event_bus.emit(EVENT_PLAN_CANCELLED, {
                "scope": scope, "chat_id": chat_id,
                "plan_id": plan_id, "reason": reason,
            })
        except Exception:
            log("cancel_plan 异常已忽略", "DEBUG")
        # 协作式中断当前 think_loop（Agent 下轮检查点停止）
        try:
            from services._runtime import get_runtime
            rt = get_runtime()
            mind = getattr(rt, "mind", None) if rt is not None else None
            if mind is not None and hasattr(mind, "interrupt"):
                mind.interrupt(scope, reason=reason)
        except Exception:
            log("cancel_plan 异常已忽略", "DEBUG")
        return True
    except Exception:
        return False


# ------------------------------------------------------------------
# 纯文本守卫
# ------------------------------------------------------------------

_PROMPT_PLAN_NOT_EXECUTED = (
    "[系统提示] 检测到你有一个正在进行中的计划，但尚未执行任何步骤：\n"
    "- 计划：{goal} (plan_id={plan_id})\n"
    "- 当前步骤：步骤 {step_no}: {step_content}\n\n"
    "你刚才输出的是纯文本（无工具调用），系统会把它当作最终回复并结束本轮——"
    "这将导致计划被放弃。\n"
    "**请立即调用执行当前步骤所需的工具**（不要只输出文字说明）；"
    "若任务确实已完成，请调用 end_reply 明确结束。"
)


async def guard_feedback_for_text_only(scope: str) -> str:
    """当前 scope 有未完成 plan 时返回守卫提醒文本；无则返回空串。

    think_loop 在"无工具正文 = 最终回复"终态前调用，防止
    "计划公告了但一步没做就结束"。
    """
    entry, goal = await _find_active_plan(scope)
    if goal is None:
        return ""
    steps = goal.get("steps", [])
    current = next(
        (s for s in steps if s.get("status") == "in_progress"), None,
    ) or next(
        (s for s in steps if s.get("status") == "pending"), None,
    )
    if current is None:
        return ""
    return _PROMPT_PLAN_NOT_EXECUTED.format(
        goal=goal.get("title", ""),
        plan_id=goal.get("goal_id", ""),
        step_no=current.get("index", 0) + 1,
        step_content=current.get("content", ""),
    )


async def find_goal_by_id(
    goal_id: str,
) -> Tuple[Optional[MemoryEntry], Optional[Dict[str, Any]]]:
    """按 goal_id 定位记忆条目与目标数据（分页扫描）。"""
    if _store is None:
        return None, None
    page_size = 100
    offset = 0
    while True:
        entries = await _store.list_by_source(
            GOAL_SOURCE, memory_type=MemoryType.SEMANTIC,
            limit=page_size, offset=offset,
        )
        if not entries:
            break
        for entry in entries:
            try:
                goal = json.loads(entry.content)
            except (json.JSONDecodeError, AttributeError):
                continue
            if goal.get("goal_id") == goal_id:
                return entry, goal
        if len(entries) < page_size:
            break
        offset += page_size
    return None, None
