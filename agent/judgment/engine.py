"""判断引擎 — 通道选择（TypeSafe 原生 / 普通模型回退）与编排。

配置每次调用现读（ConfigManager），天然热更；原生通道仅在有 API Key 时
启用，任何失败在回退开启时降级到普通模型通道（TypeSafe 官方语义：置信度
与概率分布由答案携带，双通道输出同构，调用方无需关心来源）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, Optional

from agent.judgment.bridge import judge_via_llm
from agent.judgment.client import TypeSafeClient
from agent.judgment.types import (
    JsonValue,
    JudgmentError,
    JudgmentReport,
    JudgmentSource,
    Question,
)
from core.config import get_config, get_config_bool, get_config_float
from core.log import log
from core.tool_errors import ErrorCause


@dataclass(frozen=True)
class JudgmentConfig:
    """判断配置快照（每次调用现读，热更即时生效）。"""

    enabled: bool
    api_key: str
    base_url: str
    model: str
    timeout: float
    fallback_enabled: bool
    fallback_model: str
    fallback_effort: str

    @property
    def channel(self) -> str:
        """当前生效通道标识（disabled / typesafe / llm_fallback）。"""
        if not self.enabled:
            return "disabled"
        if self.api_key:
            return JudgmentSource.TYPESAFE.value
        if self.fallback_enabled:
            return JudgmentSource.LLM_FALLBACK.value
        return "unavailable"


def load_config() -> JudgmentConfig:
    """从统一配置面读取当前判断配置。"""
    return JudgmentConfig(
        enabled=get_config_bool("judgment_enabled", True),
        api_key=str(get_config("judgment_api_key", "") or "").strip(),
        base_url=str(get_config("judgment_base_url", "https://api.typesafe.ai")
                     or "https://api.typesafe.ai").strip(),
        model=str(get_config("judgment_model", "jev-latest") or "jev-latest").strip(),
        timeout=get_config_float("judgment_timeout", 30.0),
        fallback_enabled=get_config_bool("judgment_fallback_enabled", True),
        fallback_model=str(get_config("judgment_fallback_model", "") or "").strip(),
        fallback_effort=str(get_config("judgment_fallback_effort", "low") or "low").strip(),
    )


class JudgmentEngine:
    """判断编排器：原生优先，失败按回退开关降级到普通模型通道。"""

    async def judge(
        self,
        state: JsonValue,
        questions: Dict[str, Question],
    ) -> JudgmentReport:
        """批量评判问题。失败抛 JudgmentError（归因齐备，供工具层/调用方决策）。"""
        cfg = load_config()
        if not cfg.enabled:
            raise JudgmentError(
                "判断能力已停用（judgment_enabled=false）",
                cause=ErrorCause.STATE,
                retryable=False,
            )
        if not questions:
            raise JudgmentError("questions 不能为空", cause=ErrorCause.PARAM, retryable=False)

        if cfg.api_key:
            try:
                return await self._judge_native(cfg, state, questions)
            except JudgmentError as exc:
                if not cfg.fallback_enabled:
                    raise
                log(
                    f"TypeSafe 原生通道失败，降级普通模型通道: {exc}",
                    "WARNING", tag="判断",
                )
        elif not cfg.fallback_enabled:
            raise JudgmentError(
                "未配置 TypeSafe API Key 且回退通道已关闭",
                cause=ErrorCause.CONFIG,
                retryable=False,
            )

        return await self._judge_fallback(cfg, state, questions)

    async def _judge_native(
        self, cfg: JudgmentConfig, state: JsonValue, questions: Dict[str, Question]
    ) -> JudgmentReport:
        client = TypeSafeClient(
            base_url=cfg.base_url, api_key=cfg.api_key, timeout=cfg.timeout
        )
        started = time.monotonic()
        answers, model, usage = await client.evaluate(
            state=state, model=cfg.model, questions=questions
        )
        log(
            f"判断完成 [typesafe/{model}] {len(answers)} 题 "
            f"{time.monotonic() - started:.2f}s",
            "DEBUG", tag="判断",
        )
        return JudgmentReport(
            answers=answers, source=JudgmentSource.TYPESAFE, model=model, usage=usage
        )

    async def _judge_fallback(
        self, cfg: JudgmentConfig, state: JsonValue, questions: Dict[str, Question]
    ) -> JudgmentReport:
        started = time.monotonic()
        answers, missing, model, usage = await judge_via_llm(
            state=state,
            questions=questions,
            model_id=cfg.fallback_model,
            effort=cfg.fallback_effort,
            timeout=cfg.timeout,
        )
        log(
            f"判断完成 [llm_fallback/{model or 'default'}] {len(answers)} 题"
            f"{'（缺失: ' + ','.join(missing) + '）' if missing else ''} "
            f"{time.monotonic() - started:.2f}s",
            "DEBUG", tag="判断",
        )
        return JudgmentReport(
            answers=answers,
            source=JudgmentSource.LLM_FALLBACK,
            model=model,
            usage=usage,
            missing=missing,
        )


_engine: Optional[JudgmentEngine] = None


def get_judgment_engine() -> JudgmentEngine:
    """进程级单例（引擎无内部状态，配置调用时现读）。"""
    global _engine
    if _engine is None:
        _engine = JudgmentEngine()
    return _engine
