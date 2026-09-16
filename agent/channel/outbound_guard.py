"""出站哨兵：同一会话的跨思维周期出站互斥（防「双答同一问题」）。

背景（2026-09-14 两次双发事故）：思维周期的消息链在启动时刻冻结（对话历史
周期内不重读——前缀缓存纪律），回复周期与任务/反思周期并发时互相看不到
对方的出站。快照时间错位让双方各自认为「还没回复」：反思任务按「主人已
等 6 分钟」的过期快照代答，慢速回复周期调查完毕后又交出自己的答案；或
闹钟回复刚交付、反思任务按几分钟前读到的待办再发一遍。

防线分两层，本模块是出站层：
- 调度层（agent/heartbeat/engine.py）：回复进行中，tick 不启动新任务；
- 出站层（本模块，挂在 output_tools.execute_send_action）：反思/任务上下文
  （think scope 以 ``reflect:`` 开头，见 mind.reflect 的会话命名约定）的出站，
  命中以下任一即拒绝并给出依据，由模型自适应（跳过/稍后/换目标）——
  1. 目标会话正有回复周期在飞（大概率正在处理同一事项，代答即重复）；
  2. 目标会话近期窗口内已有其他思维链出站（刚交付的事实，本周期快照不含）。
  回复周期自身与系统路径（无 think scope）不受限：多段回复是合法形态，
  回复周期就是该会话的当前所有者；同一思维链的连续出站同理放行。
"""

from __future__ import annotations

import time
from collections import deque
from typing import Callable, NamedTuple, Optional

from core.config import ConfigValueType, get_config_bool, get_config_int, register_configs_safe
from core.log import log
from core.tool_errors import ErrorCause

#: 反思/任务会话的 scope 前缀（与 mind.reflect 的会话命名约定一致）
_REFLECT_SCOPE_PREFIX = "reflect:"

#: 单会话出站记录上限（防长会话无界增长；窗口外的记录在检查时自然失效）
_MAX_RECORDS_PER_SCOPE = 64
#: 追踪的会话数上限（超出时清理已过期会话的记录）
_MAX_SCOPES = 4096

_CONFIGS = {
    "channel/outbound": {
        "outbound_guard_enabled": {
            "description": (
                "出站哨兵：反思/任务上下文向「正被回复的会话」或「近期已有其他"
                "思维链出站的会话」发送时拒绝，防止并发思维周期双答同一问题"
            ),
            "default": True,
            "value_type": ConfigValueType.BOOLEAN,
        },
        "outbound_guard_recent_seconds": {
            "description": "出站哨兵近期窗口（秒）：窗口内其他思维链已出站的会话拒绝代发，0 = 关闭该判定",
            "default": 180,
            "value_type": ConfigValueType.RANGE,
            "min": 0,
            "max": 3600,
            "step": 30,
            "unit": "秒",
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


def active_reply_scopes() -> frozenset[str]:
    """在途回复会话集合（Mind._active_scopes 的统一读取面，未施绑为空）。

    出站哨兵的拦截判定与思维层会话通知的「处理中」标注共用同一事实源。
    """
    if _reply_scopes_provider is None:
        return frozenset()
    try:
        return _reply_scopes_provider()
    except Exception:
        return frozenset()


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

    log(f"出站哨兵拦截 [{guard}]: {error}", tag="通道")
    return json.dumps({
        "success": False,
        "error": error,
        "cause": ErrorCause.STATE.value,
        "guard": guard,
        "retryable": False,
        "hint": hint,
        **extra,
    }, ensure_ascii=False)


def guard_outbound(target_scope: str, thinker: str) -> str:
    """出站前检查：返回拒绝原因 JSON，空串表示放行。

    thinker 为当前思维会话 scope：回复会话传会话 scope（user_/group_ 前缀）、
    反思/任务会话传 ``reflect:`` 前缀的唯一 scope、思维会话外的系统路径传
    ``_global``/空串——后两者不在拦截范围。
    """
    if not get_config_bool("outbound_guard_enabled", True):
        return ""
    if not thinker.startswith(_REFLECT_SCOPE_PREFIX):
        return ""
    if _reply_scope_active(target_scope):
        return _reject(
            "reply_active",
            f"目标会话 {target_scope} 的回复周期正在进行中，该会话的事项大概率正被处理",
            "请勿代答：等待回复收尾后再跟进，或改记待办/目标稍后处理",
            target_scope=target_scope,
        )
    window = _recent_window()
    if window > 0:
        now = time.time()
        for rec in reversed(_recent.get(target_scope, ())):
            if now - rec.ts > window:
                break
            if rec.thinker != thinker:
                age = int(now - rec.ts)
                return _reject(
                    "recent_outbound",
                    f"目标会话 {target_scope} 在 {age} 秒前已由其他思维周期发出消息，"
                    "当前周期的上下文快照早于该出站，可能不知情",
                    "若属同一事项请勿重复发送；确属不同事项请稍后再发或改用待办",
                    target_scope=target_scope,
                    recent_age_seconds=age,
                    recent_preview=rec.preview,
                )
    return ""


def note_outbound(target_scope: str, thinker: str, preview: str) -> None:
    """登记一条成功出站（供后续周期的近期窗口判定）。"""
    queue = _recent.get(target_scope)
    if queue is None:
        if len(_recent) >= _MAX_SCOPES:
            _prune_stale_scopes()
        queue = _recent.setdefault(target_scope, deque(maxlen=_MAX_RECORDS_PER_SCOPE))
    queue.append(_RecentSend(time.time(), thinker, preview.strip()))


def _prune_stale_scopes() -> None:
    """会话数超限时清理窗口外无新记录的会话（近似 LRU，足够用）。"""
    horizon = _recent_window() * 4
    now = time.time()
    for scope in [
        s for s, q in _recent.items()
        if not q or now - q[-1].ts > horizon
    ]:
        _recent.pop(scope, None)
