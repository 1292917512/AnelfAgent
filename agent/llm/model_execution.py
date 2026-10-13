"""模型能力调用：按类型选择候选，聚合失败归因并保留实际模型身份。"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from agent.llm.config import ModelType
from agent.llm.llm_client import LLMClient, LLMNotConfiguredError
from agent.llm.llm_manager import LLMManager
from agent.llm.media_client import MediaClient
from agent.llm.resilience import ErrorCategory, classify_llm_error
from core.log import log

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ModelResult(Generic[T]):
    """调用产物及实际使用的配置 ID、供应商模型名。"""

    value: T
    model_id: str
    model: str


class ModelCallError(RuntimeError):
    """候选链失败；errors 按尝试顺序保存脱敏的模型错误。"""

    def __init__(self, model_type: ModelType, errors: dict[str, str], *, content_policy: bool = False) -> None:
        self.errors = errors
        reason = "均因内容审核拒绝" if content_policy else "均调用失败"
        super().__init__(f"{model_type.value} 模型{reason}: " + "; ".join(
            f"{name}: {detail}" for name, detail in errors.items()
        ))


def select_candidates(
    manager: LLMManager,
    model_type: ModelType,
    *,
    model_id: str = "",
    accepts: Callable[[LLMClient], bool] | None = None,
) -> list[LLMClient]:
    """未指定模型时按类型排序；显式 ID 只允许匹配类型的已启用模型。"""
    if model_id:
        client = manager.get_client_by_id(model_id)
        candidates = [client] if client and model_type.value in client.config.model_types else []
    else:
        candidates = manager.get_all_by_type(model_type)
    if accepts is not None:
        candidates = [client for client in candidates if accepts(client)]
    if not candidates:
        target = f" {model_id!r}" if model_id else ""
        raise LLMNotConfiguredError(f"没有符合请求能力的已启用 {model_type.value} 模型{target}")
    return candidates


def validate_timeout(timeout: float | None) -> None:
    """拒绝无限、非数值与非正调用时限。"""
    if timeout is not None and (not math.isfinite(timeout) or timeout <= 0):
        raise ValueError("timeout 必须为有限正数")


async def execute_candidates(
    manager: LLMManager,
    model_type: ModelType,
    invoke: Callable[[LLMClient], Awaitable[T]],
    *,
    model_id: str = "",
    timeout: float | None = None,
    accepts: Callable[[LLMClient], bool] | None = None,
) -> ModelResult[T]:
    """每个候选调用一次；取消立即传播，显式时限约束单个候选的完整操作。"""
    validate_timeout(timeout)
    candidates = select_candidates(manager, model_type, model_id=model_id, accepts=accepts)
    errors: dict[str, str] = {}
    policy_rejects = 0
    for client in candidates:
        try:
            async with asyncio.timeout(timeout):
                value = await invoke(client)
            return ModelResult(value, client.config.name, client.config.model)
        except Exception as exc:
            policy_rejects += classify_llm_error(exc).category is ErrorCategory.CONTENT_POLICY
            detail = manager.safe_error(exc, client).strip() or type(exc).__name__
            errors[client.config.name] = f"{type(exc).__name__}: {detail}"[:500]
            log(f"模型 {client.config.name} [{model_type.value}] 调用失败: "
                f"{errors[client.config.name]}", "WARNING", tag="模型")
    raise ModelCallError(model_type, errors, content_policy=policy_rejects == len(errors)) from None


async def execute_media(
    manager: LLMManager,
    model_type: ModelType,
    invoke: Callable[[str, MediaClient], Awaitable[T]],
    *,
    model_id: str = "",
    timeout: float | None = None,
) -> ModelResult[T]:
    """用候选自身的凭据、协议、代理配置执行媒体调用。"""
    async def call(client: LLMClient) -> T:
        return await invoke(client.config.model, MediaClient.from_config(client.config))

    return await execute_candidates(manager, model_type, call, model_id=model_id, timeout=timeout)
