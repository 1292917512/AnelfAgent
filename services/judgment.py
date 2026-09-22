"""判断能力服务 — Web 侧薄门面（通道状态观测与连通性测试）。

配置读写走统一配置面（/api/config/meta，judgment/core 组），本服务
只提供状态观测与测试执行，不另设写路径。
"""
from __future__ import annotations

import time
from typing import Any, Dict

from agent.judgment import get_judgment_engine
from agent.judgment.engine import load_config
from agent.judgment.types import JudgmentError, parse_questions

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
        """用固定样例经引擎跑一次完整判断，验证当前通道可用性。"""
        started = time.monotonic()
        try:
            report = await get_judgment_engine().judge(
                _TEST_STATE, parse_questions(_TEST_QUESTIONS)
            )
        except JudgmentError as exc:
            return {
                "ok": False,
                "error": str(exc),
                "cause": exc.cause.value,
                "retryable": exc.retryable,
                "latency_ms": round((time.monotonic() - started) * 1000),
            }
        return {
            "ok": True,
            "source": report.source.value,
            "model": report.model,
            "latency_ms": round((time.monotonic() - started) * 1000),
            "answers": {qid: answer.model_dump() for qid, answer in report.answers.items()},
            "missing": report.missing,
            "usage": report.usage.model_dump(),
        }


judgment_service = JudgmentService()
