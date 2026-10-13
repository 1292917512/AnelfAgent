"""判断引擎 — System One 服务与普通模型回退的统一编排。

模型看到：云端与自部署服务接收同一组 state 和类型化问题，密钥可选。
Token：复用原生批量评判，不增加聊天调用；失败按调用方策略回退。
缓存：配置逐次现读，仅影响判断请求，不改变主模型上下文或缓存前缀。
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, replace
from typing import Dict, Optional

from agent.judgment.bridge import judge_via_llm
from agent.judgment.catalog import load_catalog, select_model
from agent.judgment.client import SystemOneClient
from agent.judgment.health import judgment_health
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
    model_id: str = ""
    provider_id: str = ""
    fallback_models: tuple[str, ...] = ()
    use_proxy: bool = False

    @property
    def native_configured(self) -> bool:
        """已选择服务连接，不以是否需要认证决定通道可用性。"""
        return bool(self.base_url)

    @property
    def channel(self) -> str:
        """当前生效通道标识（disabled / typesafe / llm_fallback）。"""
        if not self.enabled:
            return "disabled"
        if self.native_configured:
            return JudgmentSource.TYPESAFE.value
        if self.fallback_enabled:
            return JudgmentSource.LLM_FALLBACK.value
        return "unavailable"


def load_config(model_id: str = "") -> JudgmentConfig:
    """从模型目录与统一策略配置构建调用快照。"""
    try:
        catalog = load_catalog()
        selected = select_model(model_id, catalog=catalog)
        fallback_models = tuple(catalog.fallback_models) if not model_id else ()
    except (ValueError, OSError) as exc:
        raise JudgmentError(str(exc), cause=ErrorCause.CONFIG, retryable=False) from exc
    mid, pid, provider, model = selected if selected else ("", "", None, None)
    return JudgmentConfig(
        enabled=get_config_bool("judgment_enabled", True),
        api_key=provider.api_key if provider else "",
        base_url=provider.base_url if provider else "",
        use_proxy=provider.use_proxy if provider else False,
        model=model.model if model else "",
        timeout=get_config_float("judgment_timeout", 30.0),
        fallback_enabled=get_config_bool("judgment_fallback_enabled", True),
        fallback_model=str(get_config("judgment_fallback_model", "") or "").strip(),
        fallback_effort=str(get_config("judgment_fallback_effort", "low") or "low").strip(),
        model_id=mid, provider_id=pid,
        fallback_models=fallback_models,
    )


class JudgmentEngine:
    """在总等待预算内按优先级选择原生服务，普通模型回退独立受开关控制。"""

    async def judge(
        self,
        state: JsonValue,
        questions: Dict[str, Question],
        *,
        native_only: bool = False,
        timeout: float | None = None,
        model_id: str = "",
        probe: bool = False,
    ) -> JudgmentReport:
        """批量评判；显式模型不切换其他原生模型，有效答案不因判断结果而重试。"""
        cfg = load_config(model_id) if model_id else load_config()
        if timeout is not None:
            cfg = replace(cfg, timeout=min(cfg.timeout, timeout))
        if not cfg.enabled:
            raise JudgmentError(
                "判断能力已停用（judgment_enabled=false）",
                cause=ErrorCause.STATE,
                retryable=False,
            )
        if not questions:
            raise JudgmentError("questions 不能为空", cause=ErrorCause.PARAM, retryable=False)

        deadline = time.monotonic() + cfg.timeout
        last_error: JudgmentError | None = None
        candidates = [cfg, *(load_config(mid) for mid in cfg.fallback_models)] if cfg.native_configured else []
        for candidate in candidates:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            attempt = replace(candidate, timeout=min(candidate.timeout, remaining))
            exhausted = False
            try:
                judgment_health.begin(attempt, probe=probe)
                started = time.monotonic()
                try:
                    async with asyncio.timeout(attempt.timeout):
                        report = await self._judge_native(attempt, state, questions)
                except TimeoutError as exc:
                    exhausted = True
                    error = JudgmentError("判断服务请求超时", cause=ErrorCause.TIMEOUT, retryable=True)
                    judgment_health.failure(attempt, error, time.monotonic() - started)
                    raise error from exc
                except JudgmentError as exc:
                    judgment_health.failure(attempt, exc, time.monotonic() - started)
                    raise
                except asyncio.CancelledError:
                    judgment_health.cancel(attempt)
                    raise
                except Exception:
                    judgment_health.cancel(attempt)
                    raise
                judgment_health.success(attempt, time.monotonic() - started)
                return report
            except JudgmentError as exc:
                if exc.cause == ErrorCause.PARAM:
                    raise
                last_error = exc
                if exhausted:
                    raise

        if native_only or not cfg.fallback_enabled:
            raise last_error or JudgmentError(
                "未选择可用判断模型，当前请求不允许普通模型回退",
                cause=ErrorCause.CONFIG, retryable=False,
            )
        if last_error is not None:
            log(f"判断候选均不可用，降级普通模型通道: {last_error}", "WARNING", tag="判断")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise last_error or JudgmentError("判断等待预算已耗尽", cause=ErrorCause.TIMEOUT, retryable=True)

        return await self._judge_fallback(replace(cfg, timeout=remaining), state, questions)

    async def _judge_native(
        self, cfg: JudgmentConfig, state: JsonValue, questions: Dict[str, Question]
    ) -> JudgmentReport:
        client = SystemOneClient(
            base_url=cfg.base_url, api_key=cfg.api_key, timeout=cfg.timeout, use_proxy=cfg.use_proxy,
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
