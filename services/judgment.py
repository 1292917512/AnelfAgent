"""判断能力服务 — Web 侧薄门面（通道状态观测与连通性测试）。

配置读写走统一配置面（/api/config/meta，judgment/core 组），本服务
只提供状态观测与测试执行，不另设写路径。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict

from agent.judgment import get_judgment_engine
from agent.judgment.engine import load_config
from agent.judgment.latency import slow_threshold_ms
from agent.judgment.types import JudgmentError, parse_questions
from core.config import get_config_float
from core.sanitizer import sanitize_text

# 通道自检样例（三题型各一，覆盖完整输出结构）
_TEST_STATE = "用户反馈：导出按钮点击后一直转圈，换了浏览器也一样，这已经是我第三次报这个问题了。"

_TEST_QUESTIONS = [
    {
        "id": "severity",
        "type": "score",
        "instructions": "这个问题的严重程度？",
        "criteria": ["表面问题，不影响使用", "功能受损但有替代方案", "阻塞问题，无法继续使用"],
    },
    {
        "id": "is_repeat",
        "type": "noul",
        "instructions": "用户是否在重复反馈同一个问题？",
    },
    {
        "id": "team",
        "type": "choice",
        "instructions": "应该由哪个团队处理？",
        "criteria": {"engineering": "缺陷与故障排查", "support": "使用咨询与安抚", "billing": "费用与订单"},
    },
]


class JudgmentService:
    """判断能力观测与测试门面。"""

    @staticmethod
    def status() -> Dict[str, Any]:
        """通道状态：启用态 / 当前通道 / 模型与回退配置（密钥只报已配与否，不出值）。"""
        cfg = load_config()
        return {
            "enabled": cfg.enabled,
            "channel": cfg.channel,
            "api_key_configured": bool(cfg.api_key),
            "base_url": cfg.base_url,
            "model": cfg.model,
            "timeout": cfg.timeout,
            "fallback_enabled": cfg.fallback_enabled,
            "fallback_model": cfg.fallback_model,
            "fallback_effort": cfg.fallback_effort,
        }

    @staticmethod
    async def test() -> Dict[str, Any]:
        """测试当前通道与端到端延迟；已配置原生通道时不以回退成功掩盖故障。"""
        cfg = load_config()
        budget = max(0.1, min(cfg.timeout, get_config_float("judgment_gate_timeout", 2.0)))
        slow_after_ms = slow_threshold_ms(budget)
        started = time.monotonic()
        try:
            async with asyncio.timeout(cfg.timeout):
                report = await get_judgment_engine().judge(
                    _TEST_STATE, parse_questions(_TEST_QUESTIONS), native_only=bool(cfg.api_key),
                )
        except (JudgmentError, TimeoutError) as exc:
            result: Dict[str, Any] = {
                "ok": False,
                "error": sanitize_text(str(exc)) or "判断请求超过等待预算",
                "cause": exc.cause.value if isinstance(exc, JudgmentError) else "timeout",
                "retryable": exc.retryable if isinstance(exc, JudgmentError) else True,
            }
        else:
            result = {
                "ok": True, "source": report.source.value, "model": report.model,
                "answers": {qid: answer.model_dump() for qid, answer in report.answers.items()},
                "missing": report.missing, "usage": report.usage.model_dump(),
            }
        elapsed = max(0, round((time.monotonic() - started) * 1000))
        result.setdefault("source", "typesafe" if cfg.api_key else "llm_fallback")
        result.update(latency_ms=elapsed, slow=bool(cfg.api_key) and elapsed >= slow_after_ms,
                      slow_after_ms=slow_after_ms, gate_budget_ms=round(budget * 1000))
        return result


judgment_service = JudgmentService()
