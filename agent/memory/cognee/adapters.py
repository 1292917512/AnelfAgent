"""Cognee 适配器的宿主边界：有限重试、生成并发与单次召回向量复用。

模型看到什么：沿用 Cognee 提示词，不添加消息。
token 影响：永久错误不重试，同一召回内相同查询只向量化一次。
缓存影响：查询缓存仅存活于本次调用，不改变稳定提示词前缀。
"""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Any
from weakref import WeakKeyDictionary

from tenacity import retry_if_exception, stop_after_attempt

_installed = False
_llm_slots: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = WeakKeyDictionary()
_query_cache: ContextVar[dict[tuple[int, tuple[str, ...]], asyncio.Task[list[list[float]]]] | None] = ContextVar(
    "cognee_query_embeddings", default=None,
)
_PERMANENT_ERRORS = frozenset({
    "ValueError", "TypeError", "AuthenticationError", "PermissionDeniedError", "NotFoundError",
    "BadRequestError", "UnprocessableEntityError", "LLMPaymentRequiredError",
    "BudgetExceededError", "ContentPolicyFilterError", "ValidationError",
    "IncompleteOutputException", "InstructorRetryException",
})


def retryable(error: BaseException) -> bool:
    """按异常链分类：鉴权、配额、参数与结构化校验失败需修复后显式重试。"""
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (ValueError, TypeError, asyncio.CancelledError)):
            return False
        error_type = type(current).__name__
        if error_type in _PERMANENT_ERRORS or getattr(current, "error_class", None) in _PERMANENT_ERRORS:
            return False
        status = getattr(current, "status_code", None)
        # Cognee 的流水线包装统一使用 422；重试依据应是它携带的根因。
        pipeline_wrapper = error_type in {"PipelineRunFailedError", "CognifyFailedError"}
        if not pipeline_wrapper and isinstance(status, int) and 400 <= status < 500 and status not in {408, 409, 429}:
            return False
        nested = getattr(current, "first_error", None)
        current = nested if isinstance(nested, BaseException) else current.__cause__ or current.__context__
    return True


@asynccontextmanager
async def query_embeddings() -> AsyncIterator[None]:
    """跨并行数据集共享查询向量，退出时取消并回收仍在途的请求。"""
    cache: dict[tuple[int, tuple[str, ...]], asyncio.Task[list[list[float]]]] = {}
    token = _query_cache.set(cache)
    try:
        yield
    finally:
        _query_cache.reset(token)
        for task in cache.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*cache.values(), return_exceptions=True)


def _wrap_embed(engine: Any) -> None:
    if getattr(engine, "_anelf_query_cache", False):
        return
    original: Callable[[list[str]], Awaitable[list[list[float]]]] = engine.embed_text

    @wraps(original)
    async def embed(text: list[str]) -> list[list[float]]:
        cache = _query_cache.get()
        if cache is None or len(text) != 1:
            return await original(text)
        key = (id(engine), tuple(text))
        task = cache.get(key)
        if task is None:
            async def produce() -> list[list[float]]:
                return await original(text)

            task = asyncio.create_task(produce())
            cache[key] = task
        # 取消一条检索通道不得取消其他通道借用的向量请求。
        return await asyncio.shield(task)

    engine.embed_text = embed
    engine._anelf_query_cache = True


def install() -> None:
    """在客户端懒加载边界安装一次，不改 site-packages 或宿主 LLM 策略。"""
    global _installed
    if _installed:
        return
    from cognee.infrastructure.llm.LLMGateway import LLMGateway
    from cognee.infrastructure.llm.structured_output_framework.litellm_native.native_adapter import NativeLiteLLMAdapter

    def limit_retries(method: Any) -> None:
        policy = getattr(method, "retry", None)
        if policy is not None:
            policy.stop = stop_after_attempt(3)
            policy.retry = retry_if_exception(retryable)

    limit_retries(NativeLiteLLMAdapter.acreate_structured_output)
    legacy: Any = importlib.import_module(
        "cognee.infrastructure.llm.structured_output_framework.litellm_instructor.llm.get_llm_client")
    original_client = legacy.get_llm_client

    @wraps(original_client)
    def get_client(*args: Any, **kwargs: Any) -> Any:
        client = original_client(*args, **kwargs)
        limit_retries(client.acreate_structured_output)
        return client

    legacy.get_llm_client = get_client
    original_completion = LLMGateway.acreate_structured_output

    @wraps(original_completion)
    async def completion(*args: Any, **kwargs: Any) -> Any:
        loop = asyncio.get_running_loop()
        slots = _llm_slots.setdefault(loop, asyncio.Semaphore(4))
        async with slots:
            return await original_completion(*args, **kwargs)

    LLMGateway.acreate_structured_output = staticmethod(completion)
    factory: Any = importlib.import_module("cognee.infrastructure.databases.vector.embeddings.get_embedding_engine")
    original_factory = factory.create_embedding_engine

    @wraps(original_factory)
    def create_engine(*args: Any, **kwargs: Any) -> Any:
        engine = original_factory(*args, **kwargs)
        _wrap_embed(engine)
        return engine

    for name in ("cache_clear", "cache_info", "cache_parameters"):
        setattr(create_engine, name, getattr(original_factory, name))
    factory.create_embedding_engine = create_engine
    # drop_params 仅作用于 Cognee embedding，不能改变宿主所有 LLM 请求。
    embedding_module: Any = importlib.import_module(
        "cognee.infrastructure.databases.vector.embeddings.LiteLLMEmbeddingEngine")
    embedding_module.litellm = _EmbeddingTransport(embedding_module.litellm)
    from cognee.infrastructure.databases.vector.lancedb.LanceDBAdapter import LanceDBAdapter

    # 压缩只由宿主空闲窗口调度，防止构图收尾绕过保留期与写盘熔断。
    LanceDBAdapter._compaction_skip_reason = _host_compaction_reason
    _installed = True


def _host_compaction_reason(_adapter: Any) -> str:
    """库内构图收尾压缩统一交给宿主存储维护。"""
    return "host_managed"


class _EmbeddingTransport:
    """保留 LiteLLM 模块接口，仅给此适配器的向量请求设置兼容参数。"""

    def __init__(self, module: Any) -> None:
        self._module = module

    def __getattr__(self, name: str) -> Any:
        return getattr(self._module, name)

    async def aembedding(self, **kwargs: Any) -> Any:
        kwargs.setdefault("drop_params", True)
        return await self._module.aembedding(**kwargs)
