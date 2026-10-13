"""出站事实面：近期出站登记 + 上下文注入事实源 + 执行期安全边界。

设计定位（对齐「事实归系统、决策归 AI」）：本模块是出站事实的单一权威，
角色是**上下文原料**而非拦截弹药。三通道分工（无冗余）：

1. **历史固化**（本会话事实，持久面）：发送成功经 execute_send_action 的
   record_content 以 assistant 角色写入对话历史——该会话的后续回复周期
   拉历史即见「已送达」，无需再注入。
2. **动态注入**（跨会话事实，决策面）：每次成功出站经 note_outbound 登记
   （thinker/时间/内容预览），context provider「outbound_facts」每轮把
   **其他会话**的近期出站渲染进 volatile 层——对话隔离只管历史分桶，
   执行态势全局共享：反思链向群110投的内容，主人私聊的回复周期也能看到，
   自行决定是否还需自己出手。一次性呈现（本轮快照、下一轮由新事实替换、
   无登记零注入）。
3. **执行期安全边界**（兜底）：guard_outbound 仅拦「目标会话回复周期在飞」
   （反射拿着启动时刻的冻结快照，注入再新鲜也可能差几秒，代答必须拦）；
   空会话拦截 guard_empty_conversation 独立保留。

thinker 为当前思维会话 scope：回复会话传会话 scope（user_/group_ 前缀）、
反思/任务会话传 ``reflect:`` 前缀的唯一 scope、系统路径传 ``_global``/空串。
"""

from __future__ import annotations

import time
from collections import deque
from typing import Callable, NamedTuple, Optional

from agent.messages import build_scope_id, parse_entity_scope
from core.config import ConfigValueType, get_config_bool, get_config_int, register_configs_safe
from core.log import log
from core.tool_errors import ErrorCause

#: 反思/任务会话的 scope 前缀（与 mind.reflect 的会话命名约定一致）
_REFLECT_SCOPE_PREFIX = "reflect:"

#: 单会话出站记录上限（防长会话无界增长；窗口外的记录在检查时自然失效）
_MAX_RECORDS_PER_SCOPE = 64
#: 追踪的会话数上限（超出时清理已过期会话的记录）
_MAX_SCOPES = 4096
#: 注入块的单条预览截断长度（字符）
_FACT_PREVIEW_CHARS = 60

_CONFIGS = {
    "channel/outbound": {
        "outbound_guard_enabled": {
            "description": (
                "出站事实面：登记每次成功出站并动态注入各思维周期的上下文"
                "（其他链刚向哪个会话投了什么），AI 据此自行决策跳过/改写/"
                "转待办；执行期保留「回复在飞代答」硬拦截"
            ),
            "default": True,
            "value_type": ConfigValueType.BOOLEAN,
        },
        "outbound_guard_recent_seconds": {
            "description": "近期出站判定窗口（秒）：窗口内的出站事实参与注入与判定，0 = 关闭",
            "default": 180,
            "value_type": ConfigValueType.RANGE,
            "min": 0,
            "max": 3600,
            "step": 30,
            "unit": "秒",
        },
        "outbound_facts_inject": {
            "description": "出站事实注入：把其他思维链的近期出站动态注入当前思考周期的上下文",
            "default": True,
            "value_type": ConfigValueType.BOOLEAN,
        },
        "outbound_guard_empty_enabled": {
            "description": (
                "空会话拦截：AI 思维上下文向从未收到过对方消息的会话发送时拒绝"
                "（防对陌生/空会话主动发起对话）；系统路径不受限"
            ),
            "default": True,
            "value_type": ConfigValueType.BOOLEAN,
        },
    },
}

register_configs_safe(_CONFIGS)


class _RecentSend(NamedTuple):
    """一条近期出站记录（按目标会话归档）。"""

    ts: float
    thinker: str
    preview: str


_recent: dict[str, deque[_RecentSend]] = {}
_reply_scopes_provider: Optional[Callable[[], frozenset[str]]] = None


def bind_reply_scopes(provider: Callable[[], frozenset[str]]) -> None:
    """施绑在途回复 scope 的读取器（agent.runtime.wiring 统一接线）。

    Mind._active_scopes 是单一事实源，此处只持读取器不复制状态；
    未施绑时视为「无在途回复」（放行，guard 是行为护栏而非安全边界）。
    """
    global _reply_scopes_provider
    _reply_scopes_provider = provider


def _reply_scope_active(scope: str) -> bool:
    if _reply_scopes_provider is None:
        return False
    try:
        return scope in _reply_scopes_provider()
    except Exception:
        return False


def _recent_window() -> float:
    return float(get_config_int("outbound_guard_recent_seconds", 180))


def _reject(guard: str, error: str, hint: str, **extra: object) -> str:
    import json

    log(f"出站安全边界拦截 [{guard}]: {error}", tag="通道")
    return json.dumps({
        "success": False,
        "error": error,
        "cause": ErrorCause.STATE.value,
        "guard": guard,
        "retryable": False,
        "hint": hint,
        **extra,
    }, ensure_ascii=False)


def _is_owner_thinker(thinker: str, target_scope: str) -> bool:
    """本会话回复豁免：回复/系统上下文向**自身会话**出站时放行。

    reflect: 前缀的反思/任务链不在豁免范围（可能代答他会话，需守门）；
    其余 thinker（会话 entity scope 或 _global）仅当其就是目标会话本身时
    豁免——回复周期是该会话的当前所有者，多段回复合法。
    """
    if thinker.startswith(_REFLECT_SCOPE_PREFIX):
        return False
    return thinker == target_scope


def guard_outbound(target_scope: str, thinker: str) -> str:
    """出站前安全边界：仅「目标会话回复周期在飞」硬拦截，空串表示放行。

    决策面（跨会话出站事实）已由 context provider 动态注入覆盖；本函数
    只做执行期最后守门——反射拿着启动时刻的冻结快照，注入再新鲜也可能
    差几秒，回复在飞时代答必须在此拦下。
    """
    if not get_config_bool("outbound_guard_enabled", True):
        return ""
    if _is_owner_thinker(thinker, target_scope):
        return ""
    if _reply_scope_active(target_scope):
        return _reject(
            "reply_active",
            f"目标会话 {target_scope} 的回复周期正在进行中，该会话的事项大概率正被处理",
            "请勿代答：等待回复收尾后再跟进，或改记待办/目标稍后处理",
            target_scope=target_scope,
        )
    return ""


def render_outbound_facts(thinker: str, *, now: float | None = None) -> str:
    """渲染近期出站事实块（context provider 消费）；无事实返回空串。

    呈现**其他会话**的近期出站——本会话（target_scope == thinker）的投递
    已固化进对话历史（record_content），回复周期拉历史即见，注入是冗余；
    thinker 自身发出的行同样跳过。目标会话正有回复在飞时附在途提示。
    一次性呈现：窗口由 outbound_guard_recent_seconds 限定，过期记录自然消失。
    """
    if not get_config_bool("outbound_facts_inject", True):
        return ""
    window = _recent_window()
    ts_now = now if now is not None else time.time()
    lines: list[str] = []
    if window > 0 and _recent:
        for target_scope, queue in _recent.items():
            if target_scope == thinker:
                continue
            for rec in queue:
                age = ts_now - rec.ts
                if age > window:
                    continue
                if rec.thinker == thinker:
                    continue
                preview = rec.preview if len(rec.preview) <= _FACT_PREVIEW_CHARS else rec.preview[:_FACT_PREVIEW_CHARS] + "…"
                lines.append(f"- {target_scope} · {int(age)}秒前：{preview}")
    active: list[str] = []
    if _reply_scopes_provider is not None:
        try:
            active = sorted(s for s in _reply_scopes_provider() if s != thinker)[:5]
        except Exception:
            active = []
    if not lines and not active:
        return ""
    header = "[出站事实] 其他思维周期近期已向会话投递（本轮快照，据此处决策是否还需自己出手）："
    if active:
        header += f"\n[回复在飞] {', '.join(active)}——这些会话正被回复周期处理，勿代答"
    if lines:
        header += "\n" + "\n".join(lines)
    return header


async def target_has_interaction(entity_scope: str) -> bool | None:
    """目标会话是否有过用户侧消息；无法判定（runtime 未就绪/查询失败）返回 None。"""
    scope_type, adapter, base_id, session_id = parse_entity_scope(entity_scope)
    if not scope_type or not base_id:
        return None
    suffix = f"#{session_id}" if session_id and session_id != base_id else ""
    try:
        from agent.runtime.singleton import require_runtime

        sqlite = require_runtime().data_center.sqlite
        return await sqlite.conversation_has_user_message(
            scope_type=scope_type, scope_id=build_scope_id(adapter, base_id, suffix),
        )
    except Exception as exc:
        log(f"空会话判定查询失败 [{entity_scope}]（放行）: {exc}", "DEBUG", tag="通道")
        return None


async def guard_empty_conversation(target_scope: str, thinker: str) -> str:
    """空会话拦截：AI 思维上下文向从未有过用户消息的会话发送时拒绝。

    正常回复周期由真实用户消息触发、必有历史；命中空会话即说明是主动
    搭话（PROACTIVE/心跳任务）或对陌生会话的代发。系统路径（无思维会话，
    thinker 为 ``_global``/空串）不受限；历史查询失败放行（行为护栏）。
    """
    if not get_config_bool("outbound_guard_empty_enabled", True):
        return ""
    if thinker in ("", "_global"):
        return ""
    if await target_has_interaction(target_scope) is not False:
        return ""
    return _reject(
        "empty_conversation",
        f"目标会话 {target_scope} 从未收到过对方消息（空会话），不能向其发起对话",
        "对方从未在该会话中说过话，主动发消息会非常突兀；请等待对方先开口，"
        "或改记待办/目标，待对方主动联系时再处理",
        target_scope=target_scope,
    )


def note_outbound(target_scope: str, thinker: str, preview: str) -> None:
    """登记一条成功出站（供注入与后续周期的事实呈现）。"""
    queue = _recent.get(target_scope)
    if queue is None:
        if len(_recent) >= _MAX_SCOPES:
            _prune_stale_scopes()
        queue = _recent.setdefault(target_scope, deque(maxlen=_MAX_RECORDS_PER_SCOPE))
    text = preview.strip()
    queue.append(_RecentSend(time.time(), thinker, text))


def _prune_stale_scopes() -> None:
    """会话数超限时清理窗口外无新记录的会话（近似 LRU，足够用）。"""
    horizon = _recent_window() * 4
    now = time.time()
    for scope in [
        s for s, q in _recent.items()
        if not q or now - q[-1].ts > horizon
    ]:
        _recent.pop(scope, None)


# 出站事实注入（priority 32 会话操作态势档：随各链出站实时变化，内容仅
# 在真实出站事件发生时漂移，稳态零注入；group=None 全局常驻——回复与反思
# 周期都需要这份事实，不挂工具分组启停）。
def _register_provider() -> None:
    from core.context_provider import ContextProviderRegistry, ProviderMeta

    ContextProviderRegistry.register(ProviderMeta(
        name="outbound_facts",
        priority=32,
        max_tokens=300,
        inject_key="outbound_facts_inject",
        provide_fn=render_outbound_facts,
        description="出站事实：其他思维周期近期已向会话投递的内容（一次性呈现）",
    ))


_register_provider()
