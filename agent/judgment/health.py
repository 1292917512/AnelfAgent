"""判断模型可用性与后台复查，不在前台重复等待离线服务。

模型看到：调用失败或冷却中的明确错误；后台预判保留原流程。
Token：健康检查只发送无正文 HEAD，零推理调用；正常模型不做周期检查。
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

from agent.judgment.client import SystemOneClient
from agent.judgment.types import JudgmentError
from core.config import get_config_float
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
    cause: str = ""
    check_kind: Literal["inference", "connectivity"] = "inference"


class JudgmentHealth:
    """有界可用性台账与单一复查宿主，配置变更自动失效旧状态。"""

    def __init__(self) -> None:
        self._states: OrderedDict[str, ModelHealth] = OrderedDict()
        self._changed = asyncio.Event()
        self._runner: asyncio.Task[None] | None = None

    @staticmethod
    def _signature(cfg: JudgmentConfig) -> str:
        return hashlib.sha256(f"{cfg.base_url}\0{cfg.api_key}\0{cfg.model}\0{cfg.use_proxy}".encode()).hexdigest()

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
            return "判断模型暂不可用，已跳过；等待后台复查或检查配置"
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

    def success(self, cfg: JudgmentConfig, elapsed: float, *, connectivity: bool = False) -> None:
        state = self._result_state(cfg)
        if state is None:
            return
        state.status, state.checked_at, state.retry_at = "ready", time.time(), 0
        state.error, state.latency_ms = "", max(0, round(elapsed * 1000))
        state.cause = ""
        state.check_kind = "connectivity" if connectivity else "inference"

    def failure(self, cfg: JudgmentConfig, error: JudgmentError, elapsed: float, *, connectivity: bool = False) -> None:
        state = self._result_state(cfg)
        if state is None:
            return
        if error.cause not in {ErrorCause.NETWORK, ErrorCause.TIMEOUT, ErrorCause.CONFIG, ErrorCause.INTERNAL}:
            if state.status == "checking":
                state.status = "unknown"
            return
        state.status, state.checked_at = "offline", time.time()
        state.retry_at = (0 if error.cause == ErrorCause.CONFIG else
                          state.checked_at + max(30, get_config_float("judgment_recheck_seconds", 300)))
        state.error, state.latency_ms = sanitize_text(str(error))[:300], max(0, round(elapsed * 1000))
        state.cause = error.cause.value
        state.check_kind = "connectivity" if connectivity else "inference"
        self._changed.set()

    def cancel(self, cfg: JudgmentConfig) -> None:
        state = self._result_state(cfg)
        if state is None:
            return
        if state.status == "checking":
            state.status = "offline" if state.error else "unknown"

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
                      "latency_ms": value.latency_ms, "error": value.error,
                      "cause": value.cause, "check_kind": value.check_kind}
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
            due = [(mid, state) for mid, state in self._states.items() if state.status == "offline" and state.retry_at]
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
        from agent.judgment.engine import load_config
        try:
            cfg = load_config(model_id)
            if not cfg.enabled:
                state.retry_at = time.time() + max(30, get_config_float("judgment_recheck_seconds", 300))
                return
            if self._signature(cfg) != state.signature:
                self._states.pop(model_id, None)
                return
            if self._result_state(cfg) is not state or state.status != "offline":
                return
        except JudgmentError:
            self._states.pop(model_id, None)
            return
        try:
            await self.check_connectivity(cfg)
        except JudgmentError:
            return

    async def check_connectivity(self, cfg: JudgmentConfig) -> None:
        """强制检查连接，保留凭据故障；成功只表示服务可达，不代表推理已验证。"""
        self.begin(cfg, probe=True)
        previous = self._state(cfg)
        config_error = previous.cause == ErrorCause.CONFIG.value and previous.check_kind == "inference"
        started = time.monotonic()
        attempt_timeout = max(0.1, get_config_float("judgment_probe_timeout", 5.0))
        try:
            async with asyncio.timeout(attempt_timeout):
                await SystemOneClient(
                    base_url=cfg.base_url, api_key="", timeout=attempt_timeout, use_proxy=cfg.use_proxy,
                ).check_connectivity()
        except asyncio.CancelledError:
            self.cancel(cfg)
            raise
        except Exception as exc:
            error = exc if isinstance(exc, JudgmentError) else JudgmentError(
                "判断服务连接检查超时" if isinstance(exc, TimeoutError) else sanitize_text(str(exc)) or type(exc).__name__,
                cause=(ErrorCause.TIMEOUT if isinstance(exc, TimeoutError)
                       else ErrorCause.CONFIG if isinstance(exc, ValueError) else ErrorCause.NETWORK),
                retryable=not isinstance(exc, ValueError),
            )
            latest = self._result_state(cfg)
            if latest is None or latest.status != "checking":
                raise error from exc
            if config_error:
                self.cancel(cfg)
            else:
                self.failure(cfg, error, time.monotonic() - started, connectivity=True)
            raise error from exc
        state = self._result_state(cfg)
        if state is None or state.status != "checking":
            return
        if config_error:
            state.status = "offline"
            raise JudgmentError(state.error, cause=ErrorCause.CONFIG, retryable=False)
        self.success(cfg, time.monotonic() - started, connectivity=True)


judgment_health = JudgmentHealth()
