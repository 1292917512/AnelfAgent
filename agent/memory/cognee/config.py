"""Cognee 记忆后端配置：默认启用，模型未配置时等待 Web 配置。

模型看到什么：就绪后沿用既有投影与联邦召回通道提供记忆，不新增提示词。
token 影响：图谱抽取与向量化随新增记忆量产生调用；待配置时不调用模型。
缓存影响：召回沿用动态上下文，不修改稳定前缀。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Optional

from agent.llm.reasoning import CANONICAL_EFFORTS
from core.file_utils import atomic_write_text
from core.log import log
from core.path import ConfigPaths, project_root

# 模型来源：auto=自动映射 LLMManager / model=指定 LLMManager 模型 / custom=完全自定义
MODEL_SOURCE_AUTO = "auto"
MODEL_SOURCE_MODEL = "model"
MODEL_SOURCE_CUSTOM = "custom"
MODEL_SOURCES = (MODEL_SOURCE_AUTO, MODEL_SOURCE_MODEL, MODEL_SOURCE_CUSTOM)

# 思考等级：与主 LLM 系统 reasoning_effort 对齐（agent.llm.reasoning 的 7 级规范词汇）；
# ""=auto 继承模型设置；显式等级按模型思考契约或 LiteLLM 标准参数下发
REASONING_EFFORTS = ("",) + CANONICAL_EFFORTS

# cognee 库支持的 provider 词汇（custom = 自填 endpoint 的 OpenAI 兼容端点）
COGNEE_CHAT_PROVIDERS = ("openai", "anthropic", "gemini", "ollama", "custom", "azure", "mistral", "bedrock")
COGNEE_EMBED_PROVIDERS = ("openai", "ollama", "azure", "fastembed")
# instructor 结构化输出模式（"" = 跟随库默认）
COGNEE_INSTRUCTOR_MODES = ("", "json_mode", "json_schema_mode", "tools", "anthropic_tools", "mistral_tools")


@dataclass(slots=True)
class CogneeChatModelConfig:
    """Cognee 结构化抽取 LLM 配置（独立于主对话模型）。"""

    source: str = MODEL_SOURCE_AUTO
    # source=model：LLMManager 中的 chat 模型 id
    model_id: str = ""
    # source=custom：cognee provider（词汇见 COGNEE_CHAT_PROVIDERS）
    provider: str = "openai"
    model: str = ""
    api_key: str = ""
    endpoint: str = ""
    api_version: str = ""
    # instructor 结构化输出模式覆盖；thinking 端点用 json_mode 规避 tool_choice 限制
    instructor_mode: str = ""
    max_completion_tokens: int = 0
    # 思考等级独立覆盖抽取调用，关闭与档位支持范围由模型契约决定
    reasoning_effort: str = ""
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
        self.reasoning_effort = self.reasoning_effort.strip().lower()
        if self.reasoning_effort not in REASONING_EFFORTS:
            self.reasoning_effort = ""
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

    enabled: bool = field(default=True, metadata={"section": "models"})
    sync_enabled: bool = field(default=True, metadata={"section": "sync"})
    recall_enabled: bool = field(default=True, metadata={"section": "recall"})
    # 投影开关：memory 投影与主向量库内容同源（重复嵌入一份），
    # 召回经 RRF 按 memory id 去重后增益主要在 cognee 图谱抽取；
    # graph 投影（关系邻域文档）是原生检索没有的增量信息
    project_memories_enabled: bool = field(default=True, metadata={"section": "sync", "advanced": True})
    project_graph_enabled: bool = field(default=True, metadata={"section": "sync", "advanced": True})
    data_root: str = field(default=ConfigPaths.COGNEE_DATA_DIR, metadata={"section": "storage", "advanced": True, "read_only": True})
    dataset_prefix: str = field(default="anelf", metadata={"section": "sync", "advanced": True})
    timeout_seconds: float = field(default=30.0, metadata={"section": "recall", "min": 1})
    # 流水线超时需覆盖整批次的 add/cognify/improve：
    # thinking 模型下单批 20 条记忆的图谱抽取约需 15 分钟
    pipeline_timeout_seconds: float = field(default=1800.0, metadata={"section": "sync", "min": 1})
    # 全局上下文摘要需要额外模型调用，CHUNKS 不依赖它；默认手动维护。
    improve_interval_seconds: float = field(default=0.0, metadata={"section": "sync", "advanced": True})
    sync_interval_seconds: float = field(default=5.0, metadata={"section": "sync", "advanced": True, "min": 0.5})
    sync_batch_size: int = field(default=20, metadata={"section": "sync", "min": 1})
    max_retries: int = field(default=5, metadata={"section": "sync", "min": 1})
    # LanceDB 物理压缩：同步队列空闲时按共享预算轮换维护向量表，
    # 回收删除/更新遗留的历史版本（最新版本永远保留，逻辑数据不受影响）。
    # 保留期只是并发读保险：cognee 从不时间旅行，而高频重抽取每天产生
    # 上千个版本清单（单表 7 天可累积 ~7G manifests），窗口宁短勿长
    compact_enabled: bool = field(default=True, metadata={"section": "storage"})
    compact_interval_seconds: float = field(default=86400.0, metadata={"section": "storage", "advanced": True, "min": 600})
    compact_cleanup_interval_seconds: float = field(default=600.0, metadata={"section": "storage", "advanced": True, "min": 60})
    compact_retention_days: float = field(default=1.0, metadata={"section": "storage", "min": 0})
    # 写盘熔断：滑动窗口内进程自身写入速率超阈值时暂停投影认领与
    # 自动压缩（冷却期后重评），防止 Kùzu checkpoint 风暴撞爆磁盘配额
    write_breaker_enabled: bool = field(default=True, metadata={"section": "storage", "advanced": True})
    write_breaker_threshold_mb: float = field(default=500.0, metadata={"section": "storage", "advanced": True, "min": 1})
    write_breaker_window_seconds: float = field(default=300.0, metadata={"section": "storage", "advanced": True, "min": 10})
    write_breaker_cooldown_seconds: float = field(default=1800.0, metadata={"section": "storage", "advanced": True, "min": 60})
    # 失控看门狗：门闸被持有超阈值（native 查询失控的唯一可靠信号）即请求
    # 守护重启——ladybug 的 timeout/interrupt 中止路径对卡死扫描不可靠
    # （会升级为 SIGSEGV），进程级重启是唯一安全语义
    native_watchdog_enabled: bool = field(default=True, metadata={"section": "storage", "advanced": True})
    native_watchdog_restart_seconds: float = field(default=600.0, metadata={"section": "storage", "advanced": True, "min": 120})
    native_weight: float = field(default=1.0, metadata={"section": "recall", "advanced": True, "min": 0})
    # cognee 与原生同权：融合去重时来源优先级已保证原生结果胜出（memory >
    # file > cognee_chunk > cognee_graph），权重不再额外压制——图谱投影
    # 的净增量事实（跨记忆实体关联）值得与原生结果同台竞争
    cognee_weight: float = field(default=1.0, metadata={"section": "recall", "advanced": True, "min": 0})
    rrf_k: int = field(default=60, metadata={"section": "recall", "advanced": True, "min": 1})
    recall_pool_multiplier: int = field(default=3, metadata={"section": "recall", "advanced": True, "min": 1})
    search_types: list[str] = field(
        # 原生 FTS 已覆盖同步词法召回；Cognee BM25 会全量加载图库。
        default_factory=lambda: ["CHUNKS"],
        metadata={"section": "recall", "advanced": True},
    )
    # 显式 recall depth=deep 的搜索类型；自动补充独立选择非生成式通道。
    # only_context 仅跳过最终回答生成，CONTEXT_EXTENSION 的迭代检索仍调用 LLM。
    deep_search_types: list[str] = field(
        default_factory=lambda: [
            "CHUNKS",
            "CHUNKS_LEXICAL",
            "GRAPH_COMPLETION",
            "GRAPH_COMPLETION_CONTEXT_EXTENSION",
            "SUMMARIES",
        ],
        metadata={"section": "recall", "advanced": True},
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
        self.compact_interval_seconds = max(600.0, float(self.compact_interval_seconds))
        self.compact_cleanup_interval_seconds = max(60.0, float(self.compact_cleanup_interval_seconds))
        self.compact_retention_days = max(0.0, float(self.compact_retention_days))
        self.write_breaker_threshold_mb = max(1.0, float(self.write_breaker_threshold_mb))
        self.write_breaker_window_seconds = max(10.0, float(self.write_breaker_window_seconds))
        self.write_breaker_cooldown_seconds = max(60.0, float(self.write_breaker_cooldown_seconds))
        self.native_watchdog_restart_seconds = max(120.0, float(self.native_watchdog_restart_seconds))
        self.native_weight = max(0.0, float(self.native_weight))
        self.cognee_weight = max(0.0, float(self.cognee_weight))
        self.rrf_k = max(1, int(self.rrf_k))
        self.recall_pool_multiplier = max(1, int(self.recall_pool_multiplier))
        self.dataset_prefix = re.sub(r"[^a-zA-Z0-9_-]+", "_", self.dataset_prefix.strip()) or "anelf"
        self.search_types = [str(item).strip().upper() for item in self.search_types if str(item).strip()]
        self.deep_search_types = [str(item).strip().upper() for item in self.deep_search_types if str(item).strip()]
        if not isinstance(self.chat, CogneeChatModelConfig):
            self.chat = _build_nested(CogneeChatModelConfig, self.chat)
        if not isinstance(self.embedding, CogneeEmbeddingModelConfig):
            self.embedding = _build_nested(CogneeEmbeddingModelConfig, self.embedding)
        self.chat.normalized()
        self.embedding.normalized()
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def settings_schema(self) -> list[dict[str, Any]]:
        """界面字段从配置声明派生；模型配置由专用编辑器管理。"""
        result: list[dict[str, Any]] = []
        for item in fields(self):
            if "section" not in item.metadata:
                continue
            value = getattr(self, item.name)
            kind = ("bool" if isinstance(value, bool) else "int" if isinstance(value, int)
                    else "float" if isinstance(value, float) else "string_list" if isinstance(value, list)
                    else "string")
            result.append({"key": item.name, "type": kind, **item.metadata})
        return result


#: 旧版深检索默认值（用于识别"从未自定义"的存量配置，升级到新默认）
_LEGACY_DEEP_TYPES = ["CHUNKS", "CHUNKS_LEXICAL", "GRAPH_COMPLETION"]


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
        if values.get("search_types") == ["CHUNKS", "CHUNKS_LEXICAL"]:
            values["search_types"] = ["CHUNKS"]
        # 一次性默认值迁移：deep_search_types 仍是旧默认（用户从未自定义）时
        # 升级到新默认（含 CONTEXT_EXTENSION/SUMMARIES）；自定义过的列表原样保留
        if values.get("deep_search_types") == _LEGACY_DEEP_TYPES:
            values.pop("deep_search_types")
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
    atomic_write_text(
        path,
        json.dumps(config.normalized().to_dict(), ensure_ascii=False, indent=2),
    )


# ----------------------------------------------------------------------
# 存储卷登记（cognee 数据树；位置权威保留在 cognee.json 的 data_root）
# ----------------------------------------------------------------------


def _config_file_path() -> Path:
    path = Path(ConfigPaths.COGNEE_CONFIG)
    if not path.is_absolute():
        path = Path(project_root()) / path
    return path


def _absolute(path_str: str) -> str:
    path = Path(path_str)
    if not path.is_absolute():
        path = Path(project_root()) / path
    return str(path.resolve())


def _default_cognee_data_root() -> str:
    return _absolute(ConfigPaths.COGNEE_DATA_DIR)


def _cognee_location_reader():
    """卷位置读取：cognee.json 显式声明 data_root 才算有指派。"""
    from core.storage_volume import VolumeLocation

    path = _config_file_path()
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    data_root = str(raw.get("data_root", "") or "").strip() if isinstance(raw, dict) else ""
    if not data_root:
        return None
    return VolumeLocation(path=_absolute(data_root))


def _cognee_location_writer(path: Optional[str]) -> None:
    """卷位置写入：转发 cognee.json data_root（单一权威，不进中央指派文件）。"""
    config = load_cognee_config()
    config.data_root = (path or "").strip() or ConfigPaths.COGNEE_DATA_DIR
    save_cognee_config(config)


def _register_volume() -> None:
    from core.storage_volume import VolumeDescriptor, VolumeKind, register_volume

    from .databases import DATABASE_COMPONENTS
    from .volume import backup_members, observe_size, run_snapshot

    register_volume(
        VolumeDescriptor(
            volume_id="cognee",
            name="Cognee",
            description="知识图谱投影完整数据卷（Ladybug / LanceDB / SQLite / 原文 / 保护快照）",
            kind=VolumeKind.COGNEE_TREE,
            default_path=_default_cognee_data_root,
            location_reader=_cognee_location_reader,
            location_writer=_cognee_location_writer,
            databases=DATABASE_COMPONENTS,
            observe_size=observe_size,
            backup_members=backup_members,
            snapshot=run_snapshot,
        )
    )


_register_volume()
