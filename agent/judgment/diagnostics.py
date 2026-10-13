"""判断服务诊断：默认仅检查连接，显式推理测试才提交三题型样例。"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict

from agent.judgment.engine import get_judgment_engine, load_config
from agent.judgment.health import judgment_health
from agent.judgment.latency import slow_threshold_ms
from agent.judgment.types import JudgmentError, parse_questions
from core.config import get_config_float
from core.sanitizer import sanitize_text
from core.tool_errors import ErrorCause

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


async def check_connection(model_id: str = "") -> dict[str, Any]:
    """检查指定或首选服务是否可达，不发送密钥、会话内容或推理问题。"""
    started = time.monotonic()
    result: dict[str, Any] = {"kind": "connectivity", "slow": False, "slow_after_ms": 0, "gate_budget_ms": 0}
    try:
        cfg = load_config(model_id) if model_id else load_config()
        if not cfg.native_configured:
            raise JudgmentError("未选择判断服务", cause=ErrorCause.CONFIG, retryable=False)
        result.update(model=cfg.model, model_id=cfg.model_id)
        await judgment_health.check_connectivity(cfg)
        result["ok"] = True
    except JudgmentError as exc:
        result.update(ok=False, error=sanitize_text(str(exc)), cause=exc.cause.value, retryable=exc.retryable)
    result["latency_ms"] = max(0, round((time.monotonic() - started) * 1000))
    return result


async def test_inference(model_id: str = "") -> Dict[str, Any]:
    """测试当前通道与端到端延迟；已配置原生通道时不以回退成功掩盖故障。"""
    try:
        cfg = load_config(model_id) if model_id else load_config()
    except JudgmentError as exc:
        return {"ok": False, "error": sanitize_text(str(exc)), "cause": exc.cause.value,
                "retryable": False, "source": "unavailable", "latency_ms": 0,
                "slow": False, "slow_after_ms": 0, "gate_budget_ms": 0}
    budget = max(0.1, min(cfg.timeout, get_config_float("judgment_gate_timeout", 5.0)))
    slow_after_ms = slow_threshold_ms(budget)
    started = time.monotonic()
    try:
        async with asyncio.timeout(cfg.timeout):
            report = await get_judgment_engine().judge(
                _TEST_STATE, parse_questions(_TEST_QUESTIONS), native_only=cfg.native_configured,
                probe=True,
                model_id=cfg.model_id or model_id,
            )
    except (JudgmentError, TimeoutError) as exc:
        if isinstance(exc, TimeoutError):
            judgment_health.failure(cfg, JudgmentError("判断服务请求超时", cause=ErrorCause.TIMEOUT, retryable=True), time.monotonic() - started)
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
    result.setdefault("source", cfg.channel)
    result.update(kind="inference", latency_ms=elapsed, slow=cfg.native_configured and elapsed >= slow_after_ms,
                  slow_after_ms=slow_after_ms, gate_budget_ms=round(budget * 1000))
    return result
