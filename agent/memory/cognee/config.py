"""Cognee 可选记忆后端配置。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from core.log import log
from core.path import ConfigPaths, project_root

# 模型来源：auto=自动映射 LLMManager / model=指定 LLMManager 模型 / custom=完全自定义
MODEL_SOURCE_AUTO = "auto"
MODEL_SOURCE_MODEL = "model"
MODEL_SOURCE_CUSTOM = "custom"
MODEL_SOURCES = (MODEL_SOURCE_AUTO, MODEL_SOURCE_MODEL, MODEL_SOURCE_CUSTOM)


@dataclass(slots=True)
class CogneeChatModelConfig:
    """Cognee 结构化抽取 LLM 配置（独立于主对话模型）。"""

    source: str = MODEL_SOURCE_AUTO
    # source=model：LLMManager 中的 chat 模型 id
    model_id: str = ""
    # source=custom：cognee provider（openai/anthropic/gemini/ollama/custom/azure/mistral/bedrock）
    provider: str = "openai"
    model: str = ""
    api_key: str = ""
    endpoint: str = ""
    api_version: str = ""
    # instructor 结构化输出模式覆盖；thinking 端点用 json_mode 规避 tool_choice 限制
    instructor_mode: str = ""
    max_completion_tokens: int = 0
    extra_args: dict[str, Any] = field(default_factory=dict)

    def normalized(self) -> "CogneeChatModelConfig":
        if self.source not in MODEL_SOURCES:
            self.source = MODEL_SOURCE_AUTO
        self.model_id = self.model_id.strip()
        self.provider = self.provider.strip().lower() or "openai"
        self.model = self.model.strip()
        self.endpoint = self.endpoint.strip()
        self.api_version = self.api_version.strip()
        self.instructor_mode = self.instructor_mode.strip()
        self.max_completion_tokens = max(0, int(self.max_completion_tokens))
        if not isinstance(self.extra_args, dict):
            self.extra_args = {}
        return self


@dataclass(slots=True)
class CogneeEmbeddingModelConfig:
    """Cognee 向量化模型配置（独立于主 Embedding 客户端）。"""

    source: str = MODEL_SOURCE_AUTO
    model_id: str = ""
    provider: str = ""
    model: str = ""
    api_key: str = ""
    endpoint: str = ""
    dimensions: int = 0

    def normalized(self) -> "CogneeEmbeddingModelConfig":
        if self.source not in MODEL_SOURCES:
            self.source = MODEL_SOURCE_AUTO
        self.model_id = self.model_id.strip()
        self.provider = self.provider.strip().lower()
        self.model = self.model.strip()
        self.endpoint = self.endpoint.strip()
        self.dimensions = max(0, int(self.dimensions))
        return self


@dataclass(slots=True)
class CogneeConfig:
    """Cognee 投影与联邦召回配置。"""

    enabled: bool = False
    sync_enabled: bool = True
    recall_enabled: bool = True
    data_root: str = ConfigPaths.COGNEE_DATA_DIR
    dataset_prefix: str = "anelf"
    timeout_seconds: float = 30.0
    pipeline_timeout_seconds: float = 300.0
    improve_interval_seconds: float = 600.0
    sync_interval_seconds: float = 5.0
    sync_batch_size: int = 20
    max_retries: int = 5
    native_weight: float = 1.0
    cognee_weight: float = 0.8
    rrf_k: int = 60
    recall_pool_multiplier: int = 3
    search_types: list[str] = field(
        default_factory=lambda: ["CHUNKS", "CHUNKS_LEXICAL"],
    )
    chat: CogneeChatModelConfig = field(default_factory=CogneeChatModelConfig)
    embedding: CogneeEmbeddingModelConfig = field(default_factory=CogneeEmbeddingModelConfig)

    @property
    def absolute_data_root(self) -> str:
        path = Path(self.data_root)
        if not path.is_absolute():
            path = Path(project_root()) / path
        return str(path.resolve())

    def normalized(self) -> "CogneeConfig":
        self.timeout_seconds = max(1.0, float(self.timeout_seconds))
        self.pipeline_timeout_seconds = max(self.timeout_seconds, float(self.pipeline_timeout_seconds))
        self.improve_interval_seconds = float(self.improve_interval_seconds)
        self.sync_interval_seconds = max(0.5, float(self.sync_interval_seconds))
        self.sync_batch_size = max(1, int(self.sync_batch_size))
        self.max_retries = max(1, int(self.max_retries))
        self.native_weight = max(0.0, float(self.native_weight))
        self.cognee_weight = max(0.0, float(self.cognee_weight))
        self.rrf_k = max(1, int(self.rrf_k))
        self.recall_pool_multiplier = max(1, int(self.recall_pool_multiplier))
        self.dataset_prefix = self.dataset_prefix.strip() or "anelf"
        self.search_types = [str(item).strip().upper() for item in self.search_types if str(item).strip()]
        if not isinstance(self.chat, CogneeChatModelConfig):
            self.chat = _build_nested(CogneeChatModelConfig, self.chat)
        if not isinstance(self.embedding, CogneeEmbeddingModelConfig):
            self.embedding = _build_nested(CogneeEmbeddingModelConfig, self.embedding)
        self.chat.normalized()
        self.embedding.normalized()
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_cognee_config() -> CogneeConfig:
    """读取 Cognee 配置；缺失或损坏时返回安全默认值。"""
    path = Path(ConfigPaths.COGNEE_CONFIG)
    if not path.is_absolute():
        path = Path(project_root()) / path
    if not path.exists():
        return CogneeConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        allowed = CogneeConfig.__dataclass_fields__.keys()
        values = {key: value for key, value in raw.items() if key in allowed}
        values["chat"] = _build_nested(CogneeChatModelConfig, values.get("chat"))
        values["embedding"] = _build_nested(CogneeEmbeddingModelConfig, values.get("embedding"))
        return CogneeConfig(**values).normalized()
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        log(f"Cognee 配置加载失败，使用默认值: {exc}", "WARNING")
        return CogneeConfig()


def _build_nested(cls: type, raw: Any) -> Any:
    """从 dict 构造嵌套配置 dataclass，过滤未知字段。"""
    if not isinstance(raw, dict):
        return cls()
    allowed = cls.__dataclass_fields__.keys()
    return cls(**{key: value for key, value in raw.items() if key in allowed})


def save_cognee_config(config: CogneeConfig) -> None:
    """持久化 Cognee 配置。"""
    path = Path(ConfigPaths.COGNEE_CONFIG)
    if not path.is_absolute():
        path = Path(project_root()) / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(config.normalized().to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
