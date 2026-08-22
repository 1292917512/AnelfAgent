"""Guardian 自动审批 — ask 触发时的 LLM 自动评审员。

规则引擎判定 ask 时先由本模块评审：安全即放行，危险才升级人工；
无频道上下文（reflect/心跳/子代理）时 guardian 是唯一评审者。
评审不可用/超时/熔断时返回 None，放行与否由 gate 按路径决定。

guardian 批准使用独立 outcome（guardian_approved），不计入
trust_after_n_approvals 信任计数，避免自动放行自我强化信任。
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from core.config import get_config_bool, get_config_float, register_configs_safe
from core.log import log

_GUARDIAN_CONFIGS = {
    "approval/guardian": {
        "approval_guardian_enabled": {
            "description": "是否启用 Guardian 自动审批（ask 规则触发时先由 LLM 自动评审，安全即放行）",
            "default": True,
        },
        "approval_guardian_timeout": {
            "description": "Guardian 单次评审的超时时间（秒），超时按不可用处理（fail-open 放行）",
            "default": 15.0,
        },
        "approval_guardian_breaker_cooldown": {
            "description": "Guardian 连续失败熔断后的冷却时间（秒），冷却期内跳过评审",
            "default": 300.0,
        },
    },
}

register_configs_safe(_GUARDIAN_CONFIGS)

# 连续失败达到该次数即熔断（冷却期内不再尝试评审）
_BREAKER_THRESHOLD = 3


@dataclass(frozen=True)
class GuardianVerdict:
    """Guardian 评审结论。"""

    approved: bool
    risk: str = ""
    rationale: str = ""


_SYSTEM_PROMPT = """你是 AI 智能体的安全评审员。智能体请求执行一个工具调用，请判定是否放行。

放行准则（默认放行，仅对明确危险说不）：
- 放行：常规文件读写、搜索、查询、计算、用户明确要求的操作、可逆的本地修改。
- 拒绝：不可逆的破坏性操作（批量删除、格式化、清库）、向外部发送隐私数据或凭据、
  窃取/外泄密钥令牌、建立持久化后门（改启动项/authorized_keys/crontab）、
  明显超出用户请求范围的越权行为。
- 拿不准时放行——用户希望自己决定是否限制，误拦比误放的代价更高。

只输出 JSON，不要输出任何其他内容：
{"approve": true/false, "risk": "low/medium/high", "rationale": "一句话理由"}"""


class ApprovalGuardian:
    """Guardian 评审器（进程级单例，携带熔断状态）。"""

    def __init__(self) -> None:
        self._consecutive_failures = 0
        self._breaker_open_until = 0.0

    async def review(
            self,
            *,
            tool_name: str,
            tool_args: Dict[str, Any],
            reason: str,
            risk_level: str,
            channel_id: str = "",
            user_id: str = "",
    ) -> Optional[GuardianVerdict]:
        """评审一次工具调用；配置关闭/熔断中/失败/超时/输出不可解析时返回 None。"""
        if not get_config_bool("approval_guardian_enabled", True):
            return None
        now = time.monotonic()
        if now < self._breaker_open_until:
            return None

        try:
            verdict = await asyncio.wait_for(
                self._review_llm(
                    tool_name=tool_name, tool_args=tool_args, reason=reason,
                    risk_level=risk_level, channel_id=channel_id, user_id=user_id,
                ),
                timeout=get_config_float("approval_guardian_timeout", 15.0),
            )
        except Exception as exc:
            self._record_failure()
            log(f"Guardian 评审失败（按不可用处理）: {type(exc).__name__}: {exc}",
                "DEBUG", tag="权限")
            return None

        if verdict is None:
            self._record_failure()
            return None
        self._consecutive_failures = 0
        log(
            f"Guardian 评审: {tool_name} -> {'放行' if verdict.approved else '拒绝'} "
            f"(risk={verdict.risk}, {verdict.rationale[:60]})",
            tag="权限",
        )
        return verdict

    async def _review_llm(
            self,
            *,
            tool_name: str,
            tool_args: Dict[str, Any],
            reason: str,
            risk_level: str,
            channel_id: str,
            user_id: str,
    ) -> Optional[GuardianVerdict]:
        from agent.llm import get_llm_manager

        args_text = json.dumps(tool_args, ensure_ascii=False, default=str)
        if len(args_text) > 2000:
            args_text = args_text[:2000] + "…(截断)"
        user_msg = (
            f"工具: {tool_name}\n"
            f"参数: {args_text}\n"
            f"触发原因: {reason}\n"
            f"规则风险等级: {risk_level}\n"
            f"来源: 频道={channel_id or '内部'} 用户={user_id or 'agent'}"
        )
        result = await get_llm_manager().chat_with_fallback(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            max_retries=0,
            timeout=15.0,
            purpose="guardian",
        )
        return self._parse_verdict(result.content or "")

    @staticmethod
    def _parse_verdict(text: str) -> Optional[GuardianVerdict]:
        """解析严格 JSON 输出；容忍首尾杂质文本，解析失败返回 None。"""
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start:end + 1])
        except (json.JSONDecodeError, ValueError):
            return None
        approve = data.get("approve")
        if not isinstance(approve, bool):
            return None
        return GuardianVerdict(
            approved=approve,
            risk=str(data.get("risk", "") or ""),
            rationale=str(data.get("rationale", "") or ""),
        )

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= _BREAKER_THRESHOLD:
            cooldown = get_config_float("approval_guardian_breaker_cooldown", 300.0)
            self._breaker_open_until = time.monotonic() + cooldown
            log(
                f"Guardian 连续 {self._consecutive_failures} 次失败，熔断 {cooldown:.0f}s",
                "WARNING", tag="权限",
            )


_guardian: Optional[ApprovalGuardian] = None


def get_approval_guardian() -> ApprovalGuardian:
    """获取全局 Guardian 评审器。"""
    global _guardian
    if _guardian is None:
        _guardian = ApprovalGuardian()
    return _guardian
