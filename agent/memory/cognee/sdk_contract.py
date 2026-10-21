"""隔离依赖私有实现的安装与资源释放契约；升级时先验证再启用后端。"""

from __future__ import annotations

import asyncio
import importlib
import inspect
from typing import Any


def require_parameters(target: Any, names: set[str], label: str) -> None:
    """拒绝静默丢失的调用参数，包括装饰器保留的原始签名。"""
    parameters = inspect.signature(target).parameters
    if not names.issubset(parameters):
        raise RuntimeError(f"Cognee 接口契约变化: {label} 缺少 {sorted(names - parameters.keys())}")


def validate_sdk(module: Any) -> None:
    """在安装补丁或打开数据库前校验实际依赖，不按版本号猜测兼容性。"""
    require_parameters(module.add, {"data", "dataset_name", "incremental_loading"}, "add")
    require_parameters(module.cognify, {"datasets", "incremental_loading", "raise_on_error"}, "cognify")
    require_parameters(module.recall, {"query_text", "query_type", "datasets", "retriever_specific_config"}, "recall")
    require_parameters(module.improve, {"dataset", "build_global_context_index", "review_conflicts"}, "improve")
    points: dict[str, dict[str, tuple[str, ...]]] = {
        "cognee.infrastructure.databases.graph.ladybug.adapter": {
            "LadybugAdapter": ("_submit_to_executor_locked", "_drop_native_resources", "close", "delete_graph"),
        },
        "cognee.infrastructure.databases.graph.get_graph_engine": {
            "_create_graph_engine": ("cache_clear", "cache_await_closed"),
        },
        "cognee.infrastructure.databases.vector.create_vector_engine": {
            "_create_vector_engine": ("cache_clear", "cache_await_closed"),
        },
        "cognee.infrastructure.databases.relational.create_relational_engine": {
            "create_relational_engine": ("cache_clear", "cache_info"),
        },
        "cognee.infrastructure.databases.vector.embeddings.get_embedding_engine": {
            "create_embedding_engine": ("cache_clear", "cache_info", "cache_parameters"),
        },
    }
    for path, targets in points.items():
        loaded = importlib.import_module(path)
        for name, methods in targets.items():
            target = getattr(loaded, name, None)
            if any(not callable(getattr(target, method, None)) for method in methods):
                raise RuntimeError(f"Cognee 资源或并发契约变化: {path}.{name}")


async def close_engines() -> None:
    """宿主唯一客户端停止工作后释放进程缓存；不删除数据库。"""
    factories = [getattr(importlib.import_module(module), factory) for module, factory in (
        ("cognee.infrastructure.databases.graph.get_graph_engine", "_create_graph_engine"),
        ("cognee.infrastructure.databases.vector.create_vector_engine", "_create_vector_engine"),
    )]
    for factory in factories:
        factory.cache_clear()
    results = await asyncio.gather(*(factory.cache_await_closed() for factory in factories), return_exceptions=True)
    relational = importlib.import_module("cognee.infrastructure.databases.relational")
    factory = importlib.import_module(
        "cognee.infrastructure.databases.relational.create_relational_engine",
    ).create_relational_engine
    if factory.cache_info().currsize:
        await relational.get_relational_engine().engine.dispose()
        factory.cache_clear()
    for result in results:
        if isinstance(result, BaseException):
            raise result
