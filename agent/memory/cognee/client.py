"""Cognee v1.3 稳定公共 API 的懒加载门面。"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import os
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

from core.log import log

from .config import CogneeConfig
from .types import CogneeAvailability, CogneeRecallItem

# cognee 支持且走 litellm 路由的 provider（无需额外原生 SDK）
_COGNEE_SUPPORTED_PROVIDERS = frozenset({"openai", "ollama", "azure"})


class CogneeClient:
    """隔离 Cognee 导入、配置和返回类型的异步门面。"""

    def __init__(self, config: CogneeConfig) -> None:
        self.config = config.normalized()
        self._module: Optional[Any] = None
        self._configured = False
        self._import_error = ""

    @property
    def installed(self) -> bool:
        try:
            return importlib.util.find_spec("cognee") is not None
        except (ImportError, ValueError):
            return False

    async def initialize(self) -> CogneeAvailability:
        if not self.config.enabled:
            return self.availability(reason="未启用")
        if not self.installed:
            return self.availability(reason="未安装可选依赖 cognee")
        try:
            module = await asyncio.to_thread(self._import_cognee)
            await self._configure(module)
            return self.availability()
        except Exception as exc:
            self._import_error = str(exc)
            log(f"Cognee 初始化失败，保持原记忆后端: {exc}", "WARNING")
            return self.availability(reason=self._import_error)

    def availability(self, reason: str = "") -> CogneeAvailability:
        version = str(getattr(self._module, "__version__", "")) if self._module else ""
        ready = bool(self.config.enabled and self._module is not None and self._configured)
        return CogneeAvailability(
            installed=self.installed,
            enabled=self.config.enabled,
            ready=ready,
            version=version,
            reason=reason or ("" if ready else self._import_error),
        )

    def _import_cognee(self) -> Any:
        if self._module is not None:
            return self._module
        # Cognee 导入时会 dotenv.load_dotenv(override=True)。恢复环境，避免污染宿主进程。
        original_env = dict(os.environ)
        try:
            # 抑制 cognee 的 structlog 喧哗：只输出 WARNING 及以上
            os.environ.setdefault("LOG_LEVEL", "WARNING")
            os.environ.setdefault("COGNEE_LOG_FILE", "false")
            self._module = importlib.import_module("cognee")
        finally:
            os.environ.clear()
            os.environ.update(original_env)
        # cognee 的 setup_logging() 接管了 stdlib root logger，
        # 限制其 handler 级别以避免 pipeline 状态持续刷屏
        _quieten_cognee_logger()
        return self._module

    async def _configure(self, module: Any) -> None:
        if self._configured:
            return
        root = Path(self.config.absolute_data_root)
        root.mkdir(parents=True, exist_ok=True)
        module.config.system_root_directory(str(root / "system"))
        module.config.data_root_directory(str(root / "data"))

        try:
            from agent.llm import get_llm_manager

            manager = get_llm_manager()
            chat = _pick_openai_chat(manager)
            embedding = manager.get_embedding_client()
            if chat:
                module.config.set_llm_config({
                    "llm_provider": "openai",
                    "llm_model": chat.config.litellm_model,
                    "llm_api_key": chat.config.api_key,
                    "llm_endpoint": chat.config.base_url,
                    "llm_temperature": 0.0,
                })
            if embedding:
                embed_provider = _provider_name(embedding.config.api_type)
                embed_model = embedding.config.litellm_embed_model
                if embed_provider not in _COGNEE_SUPPORTED_PROVIDERS:
                    embed_provider = "openai"
                embedding_cfg: dict[str, Any] = {
                    "embedding_provider": embed_provider,
                    "embedding_model": embed_model,
                    "embedding_api_key": embedding.config.api_key,
                    "embedding_endpoint": embedding.config.base_url,
                }
                dimensions = getattr(embedding, "dimensions", None)
                if isinstance(dimensions, int) and dimensions > 0:
                    embedding_cfg["embedding_dimensions"] = dimensions
                module.config.set_embedding_config(embedding_cfg)
        except Exception as exc:
            raise RuntimeError(f"无法映射 AnelfAgent 模型配置: {exc}") from exc
        self._configured = True

    async def _call(
        self,
        dotted_name: str,
        *args: Any,
        timeout: Optional[float] = None,
        **kwargs: Any,
    ) -> Any:
        if self._module is None or not self._configured:
            availability = await self.initialize()
            if not availability.ready:
                raise RuntimeError(availability.reason or "Cognee 未就绪")
        target = self._module
        for part in dotted_name.split("."):
            target = getattr(target, part)
        result = target(*args, **kwargs)
        if hasattr(result, "__await__"):
            limit = timeout if timeout is not None else self.config.timeout_seconds
            return await asyncio.wait_for(result, timeout=limit)
        return result

    # v2 memory-oriented API
    async def remember(self, data: Any, **kwargs: Any) -> Any:
        return await self._call("remember", data, **kwargs)

    async def recall(self, query_text: str, **kwargs: Any) -> list[CogneeRecallItem]:
        raw = await self._call("recall", query_text, **kwargs)
        return _normalize_recall(raw)

    async def improve(self, dataset: str = "main_dataset", **kwargs: Any) -> Any:
        return await self._call(
            "improve",
            dataset=dataset,
            timeout=self.config.pipeline_timeout_seconds,
            **kwargs,
        )

    async def forget(self, **kwargs: Any) -> Any:
        return await self._call("forget", **kwargs)

    async def serve(self, **kwargs: Any) -> Any:
        return await self._call("serve", **kwargs)

    async def disconnect(self) -> Any:
        return await self._call("disconnect")

    async def push(self, **kwargs: Any) -> Any:
        return await self._call("push", **kwargs)

    async def export(self, **kwargs: Any) -> Any:
        return await self._call("export", **kwargs)

    # v1/lower-level public API
    async def add(self, data: Any, **kwargs: Any) -> Any:
        return await self._call(
            "add",
            data,
            timeout=self.config.pipeline_timeout_seconds,
            **kwargs,
        )

    async def make_data_item(
        self,
        data: str,
        *,
        label: str,
        external_metadata: dict[str, Any],
    ) -> Any:
        """构造 Cognee 文档化公开输入类型 DataItem。"""
        if self._module is None or not self._configured:
            availability = await self.initialize()
            if not availability.ready:
                raise RuntimeError(availability.reason or "Cognee 未就绪")
        data_item_module = importlib.import_module("cognee.tasks.ingestion.data_item")
        return data_item_module.DataItem(
            data,
            label=label,
            external_metadata=external_metadata,
        )

    async def cognify(self, **kwargs: Any) -> Any:
        return await self._call(
            "cognify",
            timeout=self.config.pipeline_timeout_seconds,
            **kwargs,
        )

    async def search(self, query_text: str, **kwargs: Any) -> list[CogneeRecallItem]:
        raw = await self._call("search", query_text, **kwargs)
        return _normalize_recall(raw)

    async def memify(self, **kwargs: Any) -> Any:
        return await self._call(
            "memify",
            timeout=self.config.pipeline_timeout_seconds,
            **kwargs,
        )

    async def update(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call("update", *args, **kwargs)

    async def run_custom_pipeline(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call("run_custom_pipeline", *args, **kwargs)

    async def run_migrations(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call("run_migrations", *args, **kwargs)

    # Dataset namespace
    async def list_datasets(self, **kwargs: Any) -> Any:
        return await self._call("datasets.list_datasets", **kwargs)

    async def discover_datasets(self, directory_path: str) -> Any:
        return await self._call("datasets.discover_datasets", directory_path)

    async def list_data(self, dataset_id: Any, **kwargs: Any) -> Any:
        return await self._call("datasets.list_data", _to_uuid(dataset_id), **kwargs)

    async def has_data(self, dataset_id: Any, **kwargs: Any) -> bool:
        return bool(await self._call("datasets.has_data", _to_uuid(dataset_id), **kwargs))

    async def get_dataset_status(self, dataset_ids: list[Any], **kwargs: Any) -> Any:
        return await self._call(
            "datasets.get_status",
            [_to_uuid(dataset_id) for dataset_id in dataset_ids],
            **kwargs,
        )

    async def empty_dataset(self, dataset_id: Any, **kwargs: Any) -> Any:
        return await self._call("datasets.empty_dataset", _to_uuid(dataset_id), **kwargs)

    async def delete_data(self, dataset_id: Any, data_id: Any, **kwargs: Any) -> Any:
        return await self._call(
            "datasets.delete_data",
            _to_uuid(dataset_id),
            _to_uuid(data_id),
            **kwargs,
        )

    async def delete_all(self, **kwargs: Any) -> Any:
        return await self._call("datasets.delete_all", **kwargs)

    # Maintenance / diagnostics / visualization
    async def prune_data(self) -> Any:
        return await self._call("prune.prune_data")

    async def prune_system(self, **kwargs: Any) -> Any:
        return await self._call("prune.prune_system", **kwargs)

    async def visualize(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call("visualize", *args, **kwargs)

    async def visualize_graph(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call("visualize_graph", *args, **kwargs)

    async def get_schema_inventory(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call("get_schema_inventory", *args, **kwargs)

    async def get_memory_provenance_graph(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call("get_memory_provenance_graph", *args, **kwargs)

    async def visualize_memory_provenance(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call("visualize_memory_provenance", *args, **kwargs)

    async def enable_tracing(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call("enable_tracing", *args, **kwargs)

    async def disable_tracing(self) -> Any:
        return await self._call("disable_tracing")

    async def get_last_trace(self) -> Any:
        return await self._call("get_last_trace")

    async def get_all_traces(self) -> Any:
        return await self._call("get_all_traces")

    async def clear_traces(self) -> Any:
        return await self._call("clear_traces")

    def search_type(self, name: str) -> Any:
        if self._module is None:
            raise RuntimeError("Cognee 未初始化")
        return getattr(self._module.SearchType, name.upper())

    def public_namespace(self, name: str) -> Any:
        """获取 agents/session/migration/agent_memory 等公开命名空间。"""
        if self._module is None or not self._configured:
            raise RuntimeError("Cognee 未初始化")
        if name not in {"agents", "session", "migration", "agent_memory", "config", "pipelines", "Drop"}:
            raise ValueError(f"不允许访问未承诺的 Cognee 命名空间: {name}")
        return getattr(self._module, name)


def _to_uuid(value: Any) -> Any:
    """将字符串形式的 UUID 还原为 UUID 对象。

    cognee 的授权检查把 str 类型 dataset_id 当作数据集「名称」解析，
    直接传字符串 UUID 会被误判为无权访问（401），因此必须在边界处转换。
    """
    if isinstance(value, str):
        text = value.strip()
        if text:
            try:
                return UUID(text)
            except ValueError:
                return value
    return value


def _quieten_cognee_logger() -> None:
    """cognee 的 setup_logging() 接管了 stdlib root logger，限制其输出级别。"""
    import logging as _logging

    root = _logging.getLogger()
    for handler in root.handlers:
        if handler.level < _logging.WARNING:
            handler.setLevel(_logging.WARNING)
    # cognee 子 logger 也限制
    _logging.getLogger("cognee").setLevel(_logging.WARNING)


def _pick_openai_chat(manager: Any) -> Any:
    """选取 api_type=openai 的 chat 客户端供 cognee 使用。

    cognee 的 OpenAIAdapter 走 litellm.acompletion，可兼容任何 OpenAI 兼容端点；
    而 AnthropicAdapter 依赖原生 anthropic SDK，项目未安装且 MiniMax 兼容端点不一定契合。
    因此强制为 cognee 选 openai 类型客户端，默认 chat 如非 openai 则回退。
    """
    from agent.llm.llm_client import API_TYPE_OPENAI

    default = manager.get_default()
    if default and default.config.api_type == API_TYPE_OPENAI:
        return default
    # 回退：遍历 chat 优先级列表，选第一个 openai 兼容的
    for mid in manager._type_priorities.get("chat", []):
        client = manager._clients.get(mid)
        if client and client.config.api_type == API_TYPE_OPENAI:
            log(
                f"Cognee: 默认 chat 模型 '{default.config.name}' 为 "
                f"api_type={default.config.api_type}，回退到 '{client.config.name}'",
                "DEBUG",
            )
            return client
    # 全部都不兼容时仍返回默认（让 cognee 自己报错，不吞异常）
    return default


def _provider_name(api_type: str) -> str:
    value = (api_type or "openai").strip().lower()
    aliases = {
        "openai_compatible": "openai",
        "azure_openai": "azure",
    }
    return aliases.get(value, value)


def _normalize_recall(raw_results: Any) -> list[CogneeRecallItem]:
    if raw_results is None:
        return []
    values = raw_results if isinstance(raw_results, list) else [raw_results]
    normalized: list[CogneeRecallItem] = []
    for index, item in enumerate(values):
        if hasattr(item, "model_dump"):
            data = item.model_dump(mode="python")
        elif isinstance(item, dict):
            data = dict(item)
        else:
            data = {"text": str(item)}

        source_value = str(data.get("source", "graph"))
        content = (
            data.get("text")
            or data.get("content")
            or data.get("answer")
            or data.get("context")
            or data.get("result")
            or ""
        )
        if not isinstance(content, str):
            content = str(content)
        if not content.strip():
            continue
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        raw_id = (
            metadata.get("chunk_id")
            or data.get("id")
            or data.get("qa_id")
            or f"result-{index}"
        )
        score_value = data.get("score", 0.0)
        score = float(score_value) if isinstance(score_value, (int, float)) else 0.0
        normalized.append(CogneeRecallItem(
            id=f"cognee:{raw_id}",
            content=content,
            score=score,
            source="cognee_chunk" if "chunk" in source_value.lower() or metadata.get("chunk_id") else "cognee_graph",
            dataset_id=str(data.get("dataset_id", "")),
            dataset_name=str(data.get("dataset_name", "")),
            metadata=metadata,
            raw=data,
        ))
    return normalized
