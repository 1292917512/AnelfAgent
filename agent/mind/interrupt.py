"""中断注册表 — 自主循环的协作式"刹车"。

自主性三定律第 2 条：每个循环必须可中断。

设计要点：
- 协作式而非抢占式：think_loop 在每轮 LLM 调用前检查一次信号，
  命中则安全收束（不发半截消息、不写残缺工具链、历史留中断元消息），
  而不是强行 cancel 协程留下不一致状态。
- scope 粒度：中断只影响目标对话，其他 scope 的回复/反思不受影响。
- 触发源：
  1. 用户指令：accept_feel 识别到精确匹配的中断关键词（"停止"/"stop" 等，
     可配置）且该 scope 正在回复 → 请求中断而非入队新任务；
  2. 守卫层：GuardrailController 判定失控时（预留）；
  3. 代码级：Mind.interrupt(scope) 供任何子系统调用。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from core.log import log


@dataclass
class InterruptRequest:
    """一条中断请求。"""

    reason: str
    requested_at: float


class InterruptRegistry:
    """scope 级中断信号注册表（进程内，无持久化需求）。"""

    def __init__(self) -> None:
        self._requests: Dict[str, InterruptRequest] = {}

    def request(self, scope: str, reason: str = "") -> None:
        """请求中断指定 scope 的进行中会话（幂等：重复请求只更新时间）。"""
        if not scope:
            return
        self._requests[scope] = InterruptRequest(reason=reason, requested_at=time.time())
        log(f"中断请求已登记: scope={scope} reason={reason or '未说明'}", tag="中断")

    def is_requested(self, scope: str) -> bool:
        """该 scope 是否存在未消费的中断请求。"""
        return scope in self._requests

    def consume(self, scope: str) -> Optional[str]:
        """消费中断请求（收束时调用），返回原因；无请求返回 None。"""
        req = self._requests.pop(scope, None)
        return req.reason if req else None

    def clear(self, scope: str) -> None:
        """会话开始时清理历史请求，避免旧信号误杀新会话。"""
        self._requests.pop(scope, None)

    def pending_scopes(self) -> List[str]:
        """当前存在未消费请求的 scope 列表（可观测性用）。"""
        return list(self._requests)


# ------------------------------------------------------------------
# 中断关键词识别（accept_feel 入口用）
# ------------------------------------------------------------------

_DEFAULT_KEYWORDS = ("停止", "停下", "别说了", "取消", "stop", "cancel")


def get_interrupt_keywords() -> tuple[str, ...]:
    """中断关键词列表（可配置，精确匹配，英文不区分大小写）。"""
    try:
        from core.config import ConfigManager
        raw = ConfigManager.get("interrupt_keywords", None)
        if isinstance(raw, list) and raw:
            return tuple(str(k).strip() for k in raw if str(k).strip())
    except Exception:
        pass
    return _DEFAULT_KEYWORDS


def match_interrupt_keyword(text: str) -> bool:
    """消息文本是否精确匹配中断关键词。

    只接受"整条消息就是中断指令"（去除空白与常见语气标点后的精确匹配），
    不接受包含式匹配 —— "请帮我分析停止损失的原因"绝不能触发中断。
    """
    if not text:
        return False
    cleaned = text.strip().strip("。!！?？~～.,").strip()
    if not cleaned or len(cleaned) > 12:
        return False
    lowered = cleaned.lower()
    return any(lowered == kw.lower() for kw in get_interrupt_keywords())


# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

_INTERRUPT_CONFIGS = {
    "中断控制": {
        "interrupt_enabled": {
            "description": "是否启用会话中断（用户发送中断关键词可停止进行中的回复）",
            "default": True,
        },
        "interrupt_keywords": {
            "description": "中断关键词列表（整条消息精确匹配才触发）",
            "default": list(_DEFAULT_KEYWORDS),
        },
    },
}

from core.config import register_configs_safe  # noqa: E402

register_configs_safe(_INTERRUPT_CONFIGS)


def is_interrupt_enabled() -> bool:
    """中断功能总开关。"""
    from core.config import get_config_bool
    return get_config_bool("interrupt_enabled", True)
