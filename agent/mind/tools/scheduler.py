"""延迟回复调度与持久化定时提醒。

- schedule_reply：短延迟（≤600s）内存版延迟回复，进程重启即失效。
- schedule_reminder：持久化定时提醒（支持绝对时间/长延迟），存储在
  config/reminders.json，由心跳 tick 检查到期并触发一轮完整 REPLY。

通过 deferred_tool 模式注册（group="thinking"），bootstrap 阶段激活。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from entities._sdk import deferred_tool
from core.log import log
from core.path import ConfigPaths

# ── 运行时引用（bootstrap 组装后通过 set_mind 注入）──

_pfc_ref: Any = None
_mind_ref: Any = None

_MAX_DELAY = 600


def set_mind(mind: Any) -> None:
    """延迟注入 Mind 引用（bootstrap 组装完成后调用），同时获取 PFC。"""
    global _mind_ref, _pfc_ref
    _mind_ref = mind
    _pfc_ref = mind.pfc if mind is not None else None


@deferred_tool(
    group="thinking", tags=["core"], source="mind.scheduler",
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
    scope = _current_scope()
    if not scope:
        return json.dumps({"error": "无法确定回复目标"}, ensure_ascii=False)

    log(f"计划 {delay}s 后触发回复: scope={scope} reason={reason}", tag="调度")
    asyncio.create_task(_delayed_reply(delay, reply_channel, scope, reason))

    return json.dumps({
        "ok": True,
        "delay_seconds": delay,
        "scope": scope,
        "channel": reply_channel,
        "hint": f"{delay}秒后系统将自动触发一轮新的对话回复",
    }, ensure_ascii=False)


async def _delayed_reply(delay: int, channel: str, scope: str, reason: str) -> None:
    """等待指定秒数后触发一轮 REPLY。"""
    await asyncio.sleep(delay)

    if not _pfc_ref or not _mind_ref:
        return

    prompt = f"[定时提醒] 你 {delay} 秒前设定了一个延迟回复"
    if reason:
        prompt += f"，原因：{reason}"
    prompt += "。请决定下一步操作。"

    _enqueue_reply(scope, channel, f"定时回复: {reason or '延迟触发'}", prompt)
    log(f"延迟 {delay}s 到期，触发回复: {scope}", tag="调度")
    asyncio.create_task(_mind_ref.try_execute_mind())


# ======================================================================
# 持久化定时提醒（schedule_reminder）
# ======================================================================

def _reminders_path() -> Path:
    return Path(ConfigPaths.REMINDERS)


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
    """从 Mind 当前活跃 scope 中提取回复目标（user_xxx / group_xxx）。"""
    for scope in getattr(_mind_ref, "_active_scopes", set()) or set():
        if scope.startswith(("user_", "group_")):
            return scope
    return ""


def enqueue_scope_reply(pfc: Any, scope: str, channel: str, preview: str, prompt: str) -> None:
    """注入系统提示并将目标 scope 排入回复队列（"完成即新 turn"的统一入口）。

    供延迟回复、定时提醒、后台任务完成通知等场景复用：
    提示写入短期记忆，scope 入待处理队列，由调用方触发 try_execute_mind。
    """
    pfc.add_temporary({"role": "system", "content": prompt})
    target = scope.split("_", 1)[1]
    if scope.startswith("group_"):
        pfc.pending_group.append(target)
    else:
        pfc.pending_user.append(target)
    pfc._message_previews[scope] = preview
    if channel:
        pfc._task_adapter_keys[scope] = channel


def _enqueue_reply(scope: str, channel: str, preview: str, prompt: str) -> None:
    """基于模块级 PFC 引用的 enqueue_scope_reply 便捷封装。"""
    enqueue_scope_reply(_pfc_ref, scope, channel, preview, prompt)


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
        "适用于'明天早上告诉我比分'、'两小时后提醒我'等场景。"
    ),
)
async def schedule_reminder(note: str, run_at: str = "", delay_seconds: int = 0) -> str:
    """设定持久化定时提醒，到期自动触发一轮完整对话回复。

    Args:
        note: 提醒内容（到时要做什么，如"搜索世界杯决赛比分并告诉主人"）
        run_at: 绝对触发时间，格式 "YYYY-MM-DD HH:MM" 或 "HH:MM"（与 delay_seconds 二选一）
        delay_seconds: 相对延迟秒数（可超过600，与 run_at 二选一）
    """
    if not _pfc_ref or not _mind_ref:
        return json.dumps({"error": "系统未就绪"}, ensure_ascii=False)
    if not note.strip():
        return json.dumps({"error": "提醒内容 note 不能为空"}, ensure_ascii=False)

    if run_at.strip():
        run_at_ts = _parse_run_at(run_at)
        if run_at_ts is None:
            return json.dumps({
                "error": f"无法解析时间: {run_at!r}，请使用 'YYYY-MM-DD HH:MM' 或 'HH:MM' 格式",
            }, ensure_ascii=False)
    elif delay_seconds > 0:
        run_at_ts = time.time() + delay_seconds
    else:
        return json.dumps({
            "error": "请提供 run_at（绝对时间）或 delay_seconds（相对延迟）之一",
        }, ensure_ascii=False)

    if run_at_ts <= time.time():
        return json.dumps({"error": "触发时间必须晚于当前时间"}, ensure_ascii=False)

    scope = _current_scope()
    if not scope:
        return json.dumps({"error": "无法确定回复目标"}, ensure_ascii=False)

    reminder = {
        "id": uuid.uuid4().hex[:8],
        "note": note.strip(),
        "run_at_ts": run_at_ts,
        "scope": scope,
        "channel": getattr(_mind_ref, "_reply_adapter_key", "") or "",
        "created_ts": time.time(),
    }
    reminders = _load_reminders()
    reminders.append(reminder)
    _save_reminders(reminders)

    run_at_str = datetime.fromtimestamp(run_at_ts).strftime("%Y-%m-%d %H:%M:%S")
    log(f"定时提醒已创建: id={reminder['id']} run_at={run_at_str} scope={scope} note={note[:50]}", tag="调度")
    return json.dumps({
        "ok": True,
        "reminder_id": reminder["id"],
        "run_at": run_at_str,
        "scope": scope,
        "note": reminder["note"],
        "hint": "提醒已持久化，到时间后系统会自动触发一轮对话（重启不丢失）",
    }, ensure_ascii=False)


@deferred_tool(
    group="thinking", tags=["core"], source="mind.scheduler",
    description="列出所有未触发的定时提醒。",
)
async def list_reminders() -> str:
    """列出所有未触发的定时提醒（含提醒 ID、触发时间、内容）。"""
    reminders = _load_reminders()
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
    reminders = _load_reminders()
    kept = [r for r in reminders if r["id"] != reminder_id]
    if len(kept) == len(reminders):
        return json.dumps({"error": f"提醒不存在: {reminder_id}"}, ensure_ascii=False)
    _save_reminders(kept)
    log(f"定时提醒已取消: id={reminder_id}", tag="调度")
    return json.dumps({"ok": True, "message": f"提醒 {reminder_id} 已取消"}, ensure_ascii=False)


async def check_due_reminders() -> int:
    """触发所有到期的持久化提醒（由心跳 tick 调用），返回触发数量。

    停机期间错过的提醒会在重启后首个心跳补触发。
    """
    if not _pfc_ref or not _mind_ref:
        return 0

    now = time.time()
    reminders = _load_reminders()
    due = [r for r in reminders if r.get("run_at_ts", 0) <= now]
    if not due:
        return 0

    kept = [r for r in reminders if r.get("run_at_ts", 0) > now]
    _save_reminders(kept)

    for r in due:
        scope = r.get("scope", "")
        if not scope:
            continue
        prompt = (
            f"[定时提醒] 你之前设定的定时提醒到期：{r.get('note', '')}"
            "。请执行提醒内容并告知用户（如需最新信息请先搜索）。"
        )
        _enqueue_reply(scope, r.get("channel", ""), f"定时提醒: {r.get('note', '')[:80]}", prompt)
        log(f"定时提醒到期触发: id={r.get('id')} scope={scope} note={r.get('note', '')[:50]}", tag="调度")

    asyncio.create_task(_mind_ref.try_execute_mind())
    return len(due)
