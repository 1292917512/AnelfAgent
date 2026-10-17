"""延迟回复调度与持久化定时提醒。

- schedule_reply：短延迟（≤600s）内存版延迟回复，进程重启即失效。
- schedule_reminder：持久化定时提醒（支持绝对时间/长延迟），存储在
  config/reminders.json，由心跳 tick 检查到期并触发一轮完整 REPLY。

两者共用 ``_resolve_target`` 目标解析：显式 scope 参数优先（非法值报
PARAM 错误，不静默回退），缺省走当前会话上下文推断；任务/反思等无会话
上下文中设定提醒必须显式指定目标，否则明确报错而非碰运气。

通过 deferred_tool 模式注册（group="thinking"），bootstrap 阶段激活。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from core.async_helper import spawn
from core.log import log
from core.path import ConfigPaths
from core.tool_errors import ErrorCause, tool_error
from core.tool_registry import deferred_tool

from .ports import mind_port

if TYPE_CHECKING:
    from agent.mind.prefrontal_cortex import PrefrontalCortex

_MAX_DELAY = 600


def _system_not_ready() -> str:
    """调度系统未就绪的统一错误。"""
    return tool_error(
        "系统未就绪",
        cause=ErrorCause.STATE, retryable=True,
        hint="系统组件尚未完成初始化，请稍后重试",
    )


def _no_reply_target() -> str:
    """无法确定回复目标的统一错误。"""
    return tool_error(
        "无法确定回复目标",
        cause=ErrorCause.STATE, retryable=False,
        hint=(
            "当前上下文（任务/反思/子代理）推不出用户或群组会话，"
            "请显式传 scope（如 user_qq:123 / group_qq:456）后重试"
        ),
    )


def _invalid_scope(scope: str) -> str:
    """显式 scope 非法的统一错误（显式参数必须被采纳或明确拒绝，不静默回退）。"""
    return tool_error(
        f"scope 非法: {scope!r}",
        cause=ErrorCause.PARAM, retryable=False,
        hint="需为可路由的会话 scope，如 user_qq:123 / group_qq:456（qq 为频道适配器）",
    )


def _channel_for_scope(scope: str, pfc: PrefrontalCortex) -> str:
    """scope 的投递频道：PFC 登记表优先（活跃会话可能携带更精确路由），
    无登记时从 scope 的 adapter 段派生——显式指定目标的提醒在触发时
    无活跃会话可查，投递路由必须自足。"""
    registered = pfc.get_adapter_key(scope)
    if registered:
        return registered
    from agent.messages.everything import parse_entity_scope
    _, adapter, _, _ = parse_entity_scope(scope)
    return adapter


def _resolve_target(explicit: str) -> tuple[str, str]:
    """解析回复目标，两个工具共用的统一入口。

    显式 scope 优先（PARAM 错误拒绝非法值，不静默回退）；缺省走上下文
    推断（仅当前执行的 ContextVar）——任务/反思上下文
    推不出目标时明确报错，而不是碰运气。

    Returns:
        (scope, "") 成功；("" , error_json) 失败（调用方直接返回）。
    """
    explicit = explicit.strip()
    if explicit:
        from agent.messages import is_conversation_scope
        if not is_conversation_scope(explicit):
            return "", _invalid_scope(explicit)
        return explicit, ""
    scope = _current_scope()
    if not scope:
        return "", _no_reply_target()
    return scope, ""


@deferred_tool(
    group="thinking", tags=["core"], source="mind.scheduler",
    description=(
        "延迟指定秒数后自动触发一轮新的对话回复，适用于需要等一会儿再主动联系用户的场景。"
        "在任务/反思等无会话上下文中使用时需显式传 scope 指定目标。"
    ),
)
async def schedule_reply(
    delay_seconds: int = 30, reason: str = "", scope: str = "",
) -> str:
    """延迟指定秒数后自动触发一轮新的对话回复。

    Args:
        delay_seconds: 延迟秒数（1-600），默认30秒
        reason: 延迟原因，会作为提示注入下一轮上下文
        scope: 回复目标会话（如 user_qq:123 / group_qq:456）；
            缺省取当前会话上下文，非会话上下文必须显式指定
    """
    if not mind_port.bound:
        return _system_not_ready()
    mind = mind_port.get()

    delay = max(1, min(delay_seconds, _MAX_DELAY))

    target, err = _resolve_target(scope)
    if err:
        return err

    reply_channel = _channel_for_scope(target, mind.pfc)
    log(f"计划 {delay}s 后触发回复: scope={target} reason={reason}", tag="调度")
    spawn(_delayed_reply(delay, reply_channel, target, reason), name="mind.delayed_reply")

    return json.dumps({
        "ok": True,
        "delay_seconds": delay,
        "scope": target,
        "channel": reply_channel,
        "hint": f"{delay}秒后系统将自动触发一轮新的对话回复",
    }, ensure_ascii=False)


async def _delayed_reply(delay: int, channel: str, scope: str, reason: str) -> None:
    """等待指定秒数后触发一轮 REPLY。"""
    await asyncio.sleep(delay)

    if not mind_port.bound:
        return

    prompt = f"[定时提醒] 你 {delay} 秒前设定了一个延迟回复"
    if reason:
        prompt += f"，原因：{reason}"
    prompt += "。请决定下一步操作。"

    await mind_port.get().pfc.enqueue_reply(scope, channel, f"定时回复: {reason or '延迟触发'}", prompt)
    log(f"延迟 {delay}s 到期，触发回复: scope={scope}", tag="调度")
    spawn(mind_port.get().try_execute_mind(), name="mind.wake")


# ======================================================================
# 持久化定时提醒（schedule_reminder）
# ======================================================================

def _reminders_path() -> Path:
    return Path(ConfigPaths.REMINDERS)


# 提醒文件读-改-写串行锁：心跳 tick 的 check_due 与回复周期的
# schedule/cancel 可能并发，无锁的 load→改→save 会互相覆盖丢更新
_reminders_lock = asyncio.Lock()


def _load_reminders() -> List[Dict[str, Any]]:
    p = _reminders_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text("utf-8"))
        return list(data.get("reminders", []))
    except Exception as exc:
        log(f"提醒列表加载失败: {exc}", "WARNING", tag="调度")
        return []


def _save_reminders(reminders: List[Dict[str, Any]]) -> None:
    p = _reminders_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"reminders": reminders}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        log(f"提醒列表保存失败: {exc}", "ERROR", tag="调度")


def _current_scope() -> str:
    """只读取当前执行绑定的会话，内部空间不得借用其他正在回复的会话。"""
    from agent.messages import is_conversation_scope
    from agent.mind.tool_activation import ToolActivationManager
    scope = ToolActivationManager.current_scope()
    return scope if is_conversation_scope(scope) else ""


async def add_reminder(
    note: str, run_at_ts: float, scope: str, channel: str = "",
) -> Dict[str, Any]:
    """写入一条持久化提醒（模块级复用入口：工具与 entities 日历共用）。

    到期由心跳 ``check_due_reminders`` 触发一轮完整 REPLY，重启不丢失。
    scope 必须是可路由的会话 scope（user_/group_ 且含频道前缀可解析）——
    到期要靠 REPLY 决策投递，非会话 scope 无处投递还可能让自主循环空转，
    抛 ValueError 拒绝。返回提醒记录（含 id）。
    """
    from agent.messages import is_conversation_scope
    if not is_conversation_scope(scope):
        raise ValueError(
            f"定时提醒需要可路由的会话 scope（如 user_qq:123，含频道前缀），收到: {scope!r}"
        )
    reminder = {
        "id": uuid.uuid4().hex[:8],
        "note": note.strip(),
        "run_at_ts": run_at_ts,
        "scope": scope,
        "channel": channel,
        "created_ts": time.time(),
    }
    async with _reminders_lock:
        reminders = await asyncio.to_thread(_load_reminders)
        reminders.append(reminder)
        await asyncio.to_thread(_save_reminders, reminders)
    return reminder


async def remove_reminder(reminder_id: str) -> bool:
    """按 id 删除一条未触发提醒（模块级复用入口），返回是否存在。"""
    async with _reminders_lock:
        reminders = await asyncio.to_thread(_load_reminders)
        kept = [r for r in reminders if r["id"] != reminder_id]
        if len(kept) == len(reminders):
            return False
        await asyncio.to_thread(_save_reminders, kept)
    return True


async def remove_scope_reminders(scopes: set[str]) -> int:
    """取消指定会话尚未触发的提醒，读写与心跳检查共用同一把锁。"""
    if not scopes:
        return 0
    async with _reminders_lock:
        reminders = await asyncio.to_thread(_load_reminders)
        kept = [r for r in reminders if r.get("scope") not in scopes]
        removed = len(reminders) - len(kept)
        if removed:
            await asyncio.to_thread(_save_reminders, kept)
    return removed


def _parse_run_at(run_at: str) -> Optional[float]:
    """解析绝对时间字符串为时间戳，支持多种常见格式，无法解析返回 None。"""
    text = run_at.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(text, fmt).timestamp()
        except ValueError:
            continue
    # 仅 "HH:MM"：今天该时刻，已过则明天
    try:
        hm = datetime.strptime(text, "%H:%M")
        now = datetime.now()
        candidate = now.replace(hour=hm.hour, minute=hm.minute, second=0, microsecond=0)
        if candidate.timestamp() <= time.time():
            candidate += timedelta(days=1)
        return candidate.timestamp()
    except ValueError:
        return None


@deferred_tool(
    group="thinking", tags=["core"], source="mind.scheduler",
    description=(
        "设定一个持久化的定时提醒：到时间后自动触发一轮对话（可搜索、可发消息）。"
        "支持绝对时间（如 2026-07-21 08:00）或长延迟（可超过10分钟），重启不丢失。"
        "适用于'明天早上告诉我比分'、'两小时后提醒我'等场景；"
        "在任务/反思等无会话上下文中设定时需显式传 scope 指定提醒对象。"
    ),
)
async def schedule_reminder(
    note: str, run_at: str = "", delay_seconds: int = 0, scope: str = "",
) -> str:
    """设定持久化定时提醒，到期自动触发一轮完整对话回复。

    Args:
        note: 提醒内容（到时要做什么，如"搜索世界杯决赛比分并告诉主人"）
        run_at: 绝对触发时间，格式 "YYYY-MM-DD HH:MM" 或 "HH:MM"（与 delay_seconds 二选一）
        delay_seconds: 相对延迟秒数（可超过600，与 run_at 二选一）
        scope: 提醒目标会话（如 user_qq:123 / group_qq:456）；
            缺省取当前会话上下文，非会话上下文（任务/反思/子代理）必须显式指定
    """
    if not mind_port.bound:
        return _system_not_ready()
    mind = mind_port.get()
    if not note.strip():
        return tool_error("提醒内容 note 不能为空", cause=ErrorCause.PARAM, retryable=False)

    if run_at.strip():
        run_at_ts = _parse_run_at(run_at)
        if run_at_ts is None:
            return tool_error(
                f"无法解析时间: {run_at!r}，请使用 'YYYY-MM-DD HH:MM' 或 'HH:MM' 格式",
                cause=ErrorCause.PARAM, retryable=False,
            )
    elif delay_seconds > 0:
        run_at_ts = time.time() + delay_seconds
    else:
        return tool_error(
            "请提供 run_at（绝对时间）或 delay_seconds（相对延迟）之一",
            cause=ErrorCause.PARAM, retryable=False,
        )

    if run_at_ts <= time.time():
        return tool_error("触发时间必须晚于当前时间", cause=ErrorCause.PARAM, retryable=False)

    target, err = _resolve_target(scope)
    if err:
        return err

    reminder = await add_reminder(
        note, run_at_ts, target, _channel_for_scope(target, mind.pfc),
    )

    run_at_str = datetime.fromtimestamp(run_at_ts).strftime("%Y-%m-%d %H:%M:%S")
    log(f"定时提醒已创建: id={reminder['id']} run_at={run_at_str} scope={target} note={note[:50]}", tag="调度")
    return json.dumps({
        "ok": True,
        "reminder_id": reminder["id"],
        "run_at": run_at_str,
        "scope": target,
        "channel": reminder["channel"],
        "note": reminder["note"],
        "hint": "提醒已持久化，到时间后系统会自动触发一轮对话（重启不丢失）",
    }, ensure_ascii=False)


@deferred_tool(
    group="thinking", tags=["core"], source="mind.scheduler",
    description="列出所有未触发的定时提醒。",
)
async def list_reminders() -> str:
    """列出所有未触发的定时提醒（含提醒 ID、触发时间、内容）。"""
    reminders = await asyncio.to_thread(_load_reminders)
    items = [{
        "id": r["id"],
        "note": r["note"],
        "run_at": datetime.fromtimestamp(r["run_at_ts"]).strftime("%Y-%m-%d %H:%M:%S"),
        "scope": r.get("scope", ""),
    } for r in sorted(reminders, key=lambda r: r["run_at_ts"])]
    return json.dumps({"reminders": items, "total": len(items)}, ensure_ascii=False)


@deferred_tool(
    group="thinking", tags=["core"], source="mind.scheduler",
    description="取消一个未触发的定时提醒。",
)
async def cancel_reminder(reminder_id: str) -> str:
    """取消指定的定时提醒。

    Args:
        reminder_id: 提醒 ID（通过 list_reminders 获取）
    """
    if not await remove_reminder(reminder_id):
        return tool_error(f"提醒不存在: {reminder_id}", cause=ErrorCause.NOT_FOUND, retryable=False)
    log(f"定时提醒已取消: id={reminder_id}", tag="调度")
    return json.dumps({"ok": True, "message": f"提醒 {reminder_id} 已取消"}, ensure_ascii=False)


async def check_due_reminders() -> int:
    """触发所有到期的持久化提醒（由心跳 tick 调用），返回触发数量。

    停机期间错过的提醒会在重启后首个心跳补触发。
    """
    if not mind_port.bound:
        return 0

    now = time.time()
    async with _reminders_lock:
        reminders = await asyncio.to_thread(_load_reminders)
        due = [r for r in reminders if r.get("run_at_ts", 0) <= now]
        if not due:
            return 0
        kept = [r for r in reminders if r.get("run_at_ts", 0) > now]
        await asyncio.to_thread(_save_reminders, kept)

    for r in due:
        scope = r.get("scope", "")
        if not scope:
            continue
        prompt = (
            f"[定时提醒] 你之前设定的定时提醒到期：{r.get('note', '')}"
            "。请执行提醒内容并告知用户（如需最新信息请先搜索）。"
        )
        await mind_port.get().pfc.enqueue_reply(
            scope, r.get("channel", ""), f"定时提醒: {r.get('note', '')[:80]}", prompt,
        )
        log(f"定时提醒到期触发: id={r.get('id')} scope={scope} note={r.get('note', '')[:50]}", tag="调度")

    spawn(mind_port.get().try_execute_mind(), name="mind.wake")
    return len(due)
