"""判断模型可用性与后台复查，不在前台重复等待离线服务。

模型看到：调用失败或冷却中的明确错误；后台预判保留原流程。
Token：冷却期零前台判断调用，后台间隔发送一题最小连通性探测。
缓存：状态只供管理面与工具结果使用，不改动主模型稳定前缀。
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from collections import OrderedDict
from contextvars import Context
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from agent.judgment.types import JudgmentError, NoulQuestion
from core.config import get_config_float
from core.log import log
from core.sanitizer import sanitize_text
from core.tool_errors import ErrorCause

if TYPE_CHECKING:
    from agent.judgment.engine import JudgmentConfig


@dataclass
class ModelHealth:
    signature: str
    status: Literal["unknown", "ready", "offline", "checking"] = "unknown"
    checked_at: float = 0
    retry_at: float = 0
    latency_ms: int = 0
    error: str = ""


class JudgmentHealth:
    """有界可用性台账与单一复查宿主，配置变更自动失效旧状态。"""

    def __init__(self) -> None:
        self._states: OrderedDict[str, ModelHealth] = OrderedDict()
        self._changed = asyncio.Event()
        self._runner: asyncio.Task[None] | None = None

    @staticmethod
    def _signature(cfg: JudgmentConfig) -> str:
        return hashlib.sha256(f"{cfg.base_url}\0{cfg.api_key}\0{cfg.model}".encode()).hexdigest()

    def _state(self, cfg: JudgmentConfig) -> ModelHealth:
        signature = self._signature(cfg)
        key = cfg.model_id or signature
        state = self._states.get(key)
        if state is None or state.signature != signature:
            state = ModelHealth(signature)
            self._states[key] = state
        self._states.move_to_end(key)
        while len(self._states) > 128:
            self._states.popitem(last=False)
        return state

    def unavailable_reason(self, cfg: JudgmentConfig) -> str:
        state = self._state(cfg)
        if state.status == "offline":
            return "判断模型暂不可用，已跳过；等待后台复查或手动测试"
        if state.status == "checking":
            return "判断模型正在检查连通性，已跳过重复等待"
        return ""

    def begin(self, cfg: JudgmentConfig, *, probe: bool) -> None:
        state = self._state(cfg)
        reason = self.unavailable_reason(cfg)
        if state.status == "checking" or (reason and not probe):
            raise JudgmentError(reason, cause=ErrorCause.STATE, retryable=False)
        if probe or state.status == "unknown":
            state.status = "checking"

    def success(self, cfg: JudgmentConfig, elapsed: float) -> None:
        state = self._result_state(cfg)
        if state is None:
            return
        state.status, state.checked_at, state.retry_at = "ready", time.time(), 0
        state.error, state.latency_ms = "", max(0, round(elapsed * 1000))

    def failure(self, cfg: JudgmentConfig, error: JudgmentError, elapsed: float) -> None:
        state = self._result_state(cfg)
        if state is None:
            return
        if error.cause not in {ErrorCause.NETWORK, ErrorCause.TIMEOUT, ErrorCause.CONFIG, ErrorCause.INTERNAL}:
            if state.status == "checking":
                state.status = "unknown"
            return
        state.status, state.checked_at = "offline", time.time()
        state.retry_at = state.checked_at + max(30, get_config_float("judgment_recheck_seconds", 300))
        state.error, state.latency_ms = sanitize_text(str(error))[:300], max(0, round(elapsed * 1000))
        self._changed.set()

    def cancel(self, cfg: JudgmentConfig) -> None:
        state = self._result_state(cfg)
        if state is None:
            return
        if state.status == "checking":
            state.status = "offline" if state.retry_at else "unknown"

    def _result_state(self, cfg: JudgmentConfig) -> ModelHealth | None:
        signature = self._signature(cfg)
        state = self._states.get(cfg.model_id or signature)
        return state if state is not None and state.signature == signature else None

    def invalidate(self, model_ids: set[str]) -> None:
        for model_id in model_ids:
            self._states.pop(model_id, None)
        self._changed.set()

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {key: {"status": value.status, "checked_at": value.checked_at, "retry_at": value.retry_at,
                      "latency_ms": value.latency_ms, "error": value.error}
                for key, value in self._states.items()}

    def start(self) -> None:
        if self._runner is None or self._runner.done():
            self._runner = asyncio.create_task(self._run(), name="judgment-health", context=Context())

    async def close(self) -> None:
        if self._runner is not None:
            self._runner.cancel()
            await asyncio.gather(self._runner, return_exceptions=True)
            self._runner = None
        self._states.clear()
        self._changed = asyncio.Event()

    async def _run(self) -> None:
        while True:
            self._changed.clear()
            due = [(mid, state) for mid, state in self._states.items() if state.status == "offline"]
            now = time.time()
            ready = [(mid, state) for mid, state in due if state.retry_at <= now]
            if ready:
                # 每批最多四个轻量探测，其他服务不占用前台执行队列。
                await asyncio.gather(*(self._recheck(mid, state) for mid, state in ready[:4]))
                continue
            wait = max(0.1, min((state.retry_at - now for _, state in due), default=3600))
            try:
                await asyncio.wait_for(self._changed.wait(), timeout=wait)
            except TimeoutError:
                continue

    async def _recheck(self, model_id: str, state: ModelHealth) -> None:
        from agent.judgment.engine import get_judgment_engine, load_config
        try:
            cfg = load_config(model_id)
            if not cfg.enabled:
                state.retry_at = time.time() + max(30, get_config_float("judgment_recheck_seconds", 300))
                return
            if self._signature(cfg) != state.signature:
                self._states.pop(model_id, None)
                return
            await get_judgment_engine().judge(
                "connection check", {"ready": NoulQuestion(instructions="Is this a connection check?")},
                model_id=model_id, native_only=True, probe=True,
                timeout=max(0.1, min(cfg.timeout, get_config_float("judgment_gate_timeout", 2))),
            )
        except JudgmentError as exc:
            if exc.cause == ErrorCause.CONFIG and state.status == "offline" and state.retry_at <= time.time():
                self._states.pop(model_id, None)
            elif state.status == "offline" and state.retry_at <= time.time():
                state.retry_at = time.time() + max(30, get_config_float("judgment_recheck_seconds", 300))
        except Exception as exc:
            state.status, state.retry_at = "offline", time.time() + max(30, get_config_float("judgment_recheck_seconds", 300))
            state.error = sanitize_text(str(exc))[:300] or type(exc).__name__
            log(f"判断后台复查失败：{model_id} {state.error}", "WARNING", tag="判断")


judgment_health = JudgmentHealth()
