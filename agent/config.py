"""
BotConfigProvider：AnelfAgent 集中配置访问层。

LLM 客户端配置由 LLMManager 管理（config/llm_clients.json），
此模块保留兼容性运行参数、人设配置、MCP 配置等全局设置的读写。

支持环境变量覆盖：``ANELF_<KEY>`` 格式的环境变量会覆盖对应配置。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.log import log
from core.path import ConfigPaths

_ENV_PREFIX = "ANELF_"

_LLM_CONFIGS: Dict[str, Dict[str, Dict[str, Any]]] = {}

_MIND_CONFIGS = {
    "AnelfAgent/Mind": {
        "heartbeat_interval": {
            "description": "心跳间隔（秒）",
            "default": 300.0,
        },
        "meta_decision_temperature": {
            "description": "元决策温度",
            "default": 0.3,
        },
        "conversation_analysis_threshold": {
            "description": "对话分析阈值（消息数）",
            "default": 5,
        },
        "max_tool_iterations": {
            "description": "最大工具调用轮次",
            "default": 8,
        },
        "force_tool_use": {
            "description": "纯工具模式：思维循环中 LLM 调用强制工具选择（tool_choice=required），杜绝纯文本输出",
            "default": True,
        },
        "log_ai_output": {
            "description": "是否记录 AI 输出日志",
            "default": True,
        },
        "send_interim_text": {
            "description": "是否发送中间文本",
            "default": False,
        },
    },
    "AnelfAgent/Mind/Memory": {
        "vector_search_batch_size": {
            "description": "向量搜索批量大小",
            "default": 500,
        },
        "memory_recall_top_k": {
            "description": "记忆召回 Top-K",
            "default": 5,
        },
        "memory_recall_min_score": {
            "description": "记忆召回最低分数",
            "default": 0.1,
        },
        "memory_time_decay_days": {
            "description": "记忆时间衰减天数",
            "default": 30,
        },
        "memory_warn_threshold": {
            "description": "记忆数量警告阈值",
            "default": 200,
        },
        "memory_max_per_type": {
            "description": "每类记忆最大数量",
            "default": 500,
        },
        "heartbeat_max_entries": {
            "description": "心跳便签最大条目数",
            "default": 50,
        },
        "auto_consolidate_enabled": {
            "description": "是否自动整理便签",
            "default": True,
        },
        "short_term_memory_size": {
            "description": "短期记忆容量",
            "default": 10,
        },
        "tool_recall_top_n": {
            "description": "工具召回保留的 Top-N 数量",
            "default": 10,
        },
        "llm_timeout": {
            "description": "LLM 调用超时时间（秒）",
            "default": 120.0,
        },
        "llm_max_retries": {
            "description": "LLM 调用最大重试次数",
            "default": 2,
        },
        "conv_recall_scan_limit": {
            "description": "深度对话检索：向量扫描的最大历史消息条数",
            "default": 500,
        },
        "conv_recall_backfill_batch": {
            "description": "深度对话检索：每次工具调用的 embedding 回填批次大小",
            "default": 30,
        },
        "conv_recall_min_score": {
            "description": "深度对话检索：向量相似度最低分（低于此值的结果被丢弃）",
            "default": 0.25,
        },
        "conv_recall_max_results": {
            "description": "深度对话检索：recall_conversation 工具最大返回条数",
            "default": 10,
        },
        "cross_channel_enabled": {
            "description": "跨频道感知：是否启用跨频道对话关联",
            "default": True,
        },
        "cross_channel_window_minutes": {
            "description": "跨频道感知：活动感知时间窗口（分钟）",
            "default": 30,
        },
        "cross_channel_recall_min_score": {
            "description": "跨频道感知：语义召回最低相似度（0~1）",
            "default": 0.45,
        },
        "cross_channel_recall_max_results": {
            "description": "跨频道感知：语义召回最大条数",
            "default": 3,
        },
        "cross_channel_recall_scan_limit": {
            "description": "跨频道感知：每 scope 扫描消息数",
            "default": 50,
        },
        "cross_channel_narrative_max_items": {
            "description": "跨频道感知：近况叙事最大条数",
            "default": 3,
        },
    },
}

_MIND_SYNC_FIELDS: tuple[str, ...] = (
    "heartbeat_interval", "meta_decision_temperature",
    "conversation_analysis_threshold", "max_tool_iterations",
    "log_ai_output", "send_interim_text", "force_tool_use",
    "vector_search_batch_size", "memory_recall_top_k",
    "memory_recall_min_score", "memory_time_decay_days",
    "memory_warn_threshold", "memory_max_per_type",
    "heartbeat_max_entries", "auto_consolidate_enabled",
    "short_term_memory_size", "tool_recall_top_n",
    "llm_timeout", "llm_max_retries",
    "conv_recall_scan_limit", "conv_recall_backfill_batch",
    "conv_recall_min_score", "conv_recall_max_results",
    "cross_channel_enabled", "cross_channel_window_minutes",
    "cross_channel_recall_min_score", "cross_channel_recall_max_results",
    "cross_channel_recall_scan_limit", "cross_channel_narrative_max_items",
    "reasoning_effort",
)

_ENV_MAPPING: Dict[str, str] = {
    "ANELF_LLM_STREAM_ENABLED": "llm_stream_enabled",
    "ANELF_PERSONAS_DIR": "personas_dir",
    "ANELF_PERSONAS_CONFIG_PATH": "personas_config_path",
    "ANELF_MCP_CONFIG_PATH": "mcp_config_path",
    "ANELF_SQLITE_PATH": "sqlite_path",
    "ANELF_MAX_CONVERSATION_SIZE": "max_conversation_size",
    "ANELF_MAX_TOOL_ITERATIONS": "max_tool_iterations",
}


def _parse_env_value(value: str) -> Any:
    """将环境变量字符串解析为合适的 Python 类型。"""
    low = value.lower()
    if low in ("true", "1", "yes"):
        return True
    if low in ("false", "0", "no"):
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


@dataclass
class LLMConfig:
    """LLM 运行时参数（仅保留与客户端无关的全局开关）。"""
    stream_enabled: bool = True


@dataclass
class MindConfig:
    """Mind 可调参数（持久化到 config/mind_config.json）。

    反思相关配置已迁移到 config/introspection.json，
    由 IntrospectionConfig 管理。
    """
    heartbeat_interval: float = 300.0
    meta_decision_temperature: float = 0.3
    conversation_analysis_threshold: int = 5
    max_tool_iterations: int = 8
    # 纯工具模式：思维循环中 LLM 调用强制工具选择（tool_choice=required）
    force_tool_use: bool = True
    log_ai_output: bool = True
    send_interim_text: bool = False
    # 记忆搜索配置
    vector_search_batch_size: int = 500
    memory_recall_top_k: int = 5
    memory_recall_min_score: float = 0.1
    memory_time_decay_days: int = 30
    # 记忆管理配置
    memory_warn_threshold: int = 200
    memory_max_per_type: int = 500
    # 便签整理配置
    heartbeat_max_entries: int = 50
    auto_consolidate_enabled: bool = True
    # 短期记忆 & 工具召回
    short_term_memory_size: int = 10
    tool_recall_top_n: int = 10
    # LLM 调用参数
    llm_timeout: float = 120.0
    llm_max_retries: int = 2
    # 深度对话历史检索配置
    conv_recall_scan_limit: int = 500
    conv_recall_backfill_batch: int = 30
    conv_recall_min_score: float = 0.25
    conv_recall_max_results: int = 10
    # 跨频道感知配置
    cross_channel_enabled: bool = True
    cross_channel_window_minutes: int = 30
    cross_channel_recall_min_score: float = 0.45
    cross_channel_recall_max_results: int = 3
    cross_channel_recall_scan_limit: int = 50
    cross_channel_narrative_max_items: int = 3
    # 全局思考等级：low / medium / high / max（空=不设置）
    reasoning_effort: str = ""
    # 工具系统提示规则（每条一行，注入到 LLM system prompt）；实际内容由 mind_config.json 提供
    tool_system_rules: List[str] = field(default_factory=list)


@dataclass
class BotConfig:
    """AnelfAgent 全局配置聚合。"""
    llm: LLMConfig = field(default_factory=LLMConfig)
    mind: MindConfig = field(default_factory=MindConfig)
    personas_dir: str = ConfigPaths.PERSONAS_DIR
    personas_config_path: str = ConfigPaths.PERSONAS_INDEX
    mcp_config_path: str = ConfigPaths.MCP_SERVERS
    sqlite_path: str = ConfigPaths.SQLITE_DB
    max_conversation_size: int = 30
    max_tool_iterations: int = 3


class BotConfigProvider:
    """
    集中配置提供器。

    加载优先级（后者覆盖前者）：
    1. BotConfig 默认值
    2. ConfigManager 持久化值
    3. 环境变量覆盖（``ANELF_`` 前缀）
    """

    def __init__(self) -> None:
        self._config = BotConfig()
        self._cm_available = False
        self._register_configs()
        self._load_from_cm()
        self._load_mind_config()
        self._apply_env_overrides()

    @staticmethod
    def _register_configs() -> None:
        try:
            from core.config import register_configs
            register_configs(_LLM_CONFIGS)
            register_configs(_MIND_CONFIGS)
        except Exception:
            log("配置注册跳过（ConfigManager 不可用）", "DEBUG")

    def _load_from_cm(self) -> None:
        try:
            from core.config import ConfigManager
            self._cm_available = True
        except ImportError:
            log("core.config 不可用，使用默认配置", "DEBUG")
            return

        self._config.llm.stream_enabled = bool(
            ConfigManager.get("llm_stream_enabled", self._config.llm.stream_enabled)
        )

        for attr in ("personas_dir", "personas_config_path", "mcp_config_path", "sqlite_path", "max_conversation_size"):
            val = ConfigManager.get(attr)
            if val is not None:
                current = getattr(self._config, attr)
                setattr(self._config, attr, type(current)(val))

        mc = self._config.mind
        for attr in _MIND_SYNC_FIELDS:
            val = ConfigManager.get(attr)
            if val is not None and hasattr(mc, attr):
                current = getattr(mc, attr)
                try:
                    setattr(mc, attr, type(current)(val))
                except (TypeError, ValueError):
                    pass

    @property
    def mind_config_path(self) -> str:
        if self._cm_available:
            from core.config import ConfigManager
            return str(ConfigManager.get("mind_config_path", ConfigPaths.MIND_CONFIG))
        return ConfigPaths.MIND_CONFIG

    def _apply_env_overrides(self) -> None:
        """从环境变量覆盖配置。"""
        overridden: list[str] = []

        for env_key, config_attr in _ENV_MAPPING.items():
            env_val = os.environ.get(env_key)
            if env_val is None:
                continue

            parsed = _parse_env_value(env_val)

            if config_attr == "llm_stream_enabled":
                self._config.llm.stream_enabled = bool(parsed)
            elif hasattr(self._config, config_attr):
                setattr(self._config, config_attr, parsed)

            overridden.append(f"{env_key}={parsed}")

        if overridden:
            log(f"环境变量覆盖配置: {', '.join(overridden)}")

    def _load_mind_config(self) -> None:
        p = Path(self.mind_config_path)
        if not p.exists():
            return
        try:
            data = json.loads(p.read_text("utf-8"))
            mc = self._config.mind
            for k in (*_MIND_SYNC_FIELDS, "tool_system_rules"):
                if k in data:
                    val = data[k]
                    current = getattr(mc, k)
                    if isinstance(current, (list, dict)):
                        setattr(mc, k, val)
                    else:
                        setattr(mc, k, type(current)(val))

            self._sync_mind_to_config_manager()
        except Exception as exc:
            log(f"Mind 配置加载失败: {exc}", "WARNING")

    def _sync_mind_to_config_manager(self) -> None:
        """将 MindConfig 值同步到 ConfigManager，供 ConfigRegistry 查询。"""
        if not self._cm_available:
            return
        try:
            from core.config import ConfigManager
            mc = self._config.mind
            for k in _MIND_SYNC_FIELDS:
                if hasattr(mc, k):
                    ConfigManager.set(k, getattr(mc, k))
        except Exception as e:
            log(f"Mind 配置同步到 ConfigManager 失败: {e}", "DEBUG")

    def save_mind_config(self, **overrides: Any) -> None:
        """保存 Mind 配置。"""
        mc = self._config.mind
        for k, v in overrides.items():
            if hasattr(mc, k):
                current = getattr(mc, k)
                if isinstance(current, (list, dict)):
                    setattr(mc, k, v)
                elif isinstance(current, str):
                    setattr(mc, k, str(v))
                else:
                    setattr(mc, k, type(current)(v))
        p = Path(self.mind_config_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {key: getattr(mc, key) for key in _MIND_SYNC_FIELDS}
        data["tool_system_rules"] = mc.tool_system_rules
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self._sync_mind_to_config_manager()
        log(f"Mind 配置已保存: {p}")

    def reload(self) -> None:
        self._load_from_cm()
        self._load_mind_config()
        self._apply_env_overrides()

    @property
    def llm(self) -> LLMConfig:
        return self._config.llm

    @property
    def mind(self) -> MindConfig:
        return self._config.mind

    @property
    def config(self) -> BotConfig:
        return self._config

    # ------------------------------------------------------------------
    # LLM 客户端配置（委托给 LLMManager）
    # ------------------------------------------------------------------

    def save_llm_config(self, **kwargs: Any) -> None:
        """保存 LLM 配置。

        stream_enabled 保存到 ConfigManager，
        其余客户端参数（model, base_url, api_key 等）同步到 LLMManager 默认客户端。
        """
        if "stream_enabled" in kwargs:
            self._config.llm.stream_enabled = bool(kwargs.pop("stream_enabled"))
            if self._cm_available:
                from core.config import ConfigManager
                ConfigManager.set("llm_stream_enabled", self._config.llm.stream_enabled)
                ConfigManager.save()

        kwargs.pop("mode", None)
        kwargs.pop("ollama_host", None)

        if kwargs:
            self._update_llm_client(**kwargs)

        from core.event_bus import event_bus, EVENT_CONFIG_CHANGED
        try:
            import asyncio
            loop = asyncio.get_running_loop()
            loop.create_task(event_bus.emit(EVENT_CONFIG_CHANGED, {"keys": list(kwargs.keys())}))
        except RuntimeError:
            log("无事件循环，config_changed 事件延迟发射", "DEBUG")

    @staticmethod
    def _update_llm_client(**kwargs: Any) -> None:
        """将客户端参数同步到 LLMManager 默认客户端。"""
        try:
            from agent.llm import get_llm_manager
            manager = get_llm_manager()
            default_name = manager.default_name
            if not default_name:
                log("无默认 LLM 客户端，跳过配置同步", "DEBUG")
                return
            manager.update_model(default_name, **kwargs)
        except Exception as exc:
            log(f"同步 LLM 客户端配置失败: {exc}", "WARNING")

    # ------------------------------------------------------------------
    # 人设管理
    # ------------------------------------------------------------------

    def _personas_dir(self) -> Path:
        return Path(self._config.personas_dir)

    def _personas_index_path(self) -> Path:
        return Path(self._config.personas_config_path)

    def list_personas(self) -> List[Dict[str, Any]]:
        """列出所有可用人设（名称、描述、是否活跃）。"""
        active = self.get_active_persona_name()
        d = self._personas_dir()
        result: List[Dict[str, Any]] = []
        if not d.is_dir():
            return result
        for f in sorted(d.iterdir()):
            if f.suffix != ".json" or f.name == "index.json":
                continue
            try:
                data = json.loads(f.read_text("utf-8"))
                key = f.stem
                result.append({
                    "key": key,
                    "name": data.get("name", key),
                    "description": data.get("description", ""),
                    "active": key == active,
                })
            except Exception:
                log(f"读取人设失败: {f}", "WARNING")
        return result

    def get_active_persona_name(self) -> str:
        """获取当前活跃人设的 key（文件名去掉 .json）。"""
        p = self._personas_index_path()
        if p.exists():
            try:
                data = json.loads(p.read_text("utf-8"))
                return str(data.get("active", ""))
            except Exception:
                log(f"读取人设索引失败: {p}", "WARNING")
        return ""

    def set_active_persona(self, key: str) -> bool:
        """设置活跃人设。"""
        persona_file = self._personas_dir() / f"{key}.json"
        if not persona_file.exists():
            log(f"人设文件不存在: {persona_file}", "ERROR")
            return False
        p = self._personas_index_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"active": key}, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"活跃人设已切换: {key}")
        return True

    def get_persona_config(self, key: Optional[str] = None) -> Dict[str, Any]:
        """获取指定人设配置（默认获取活跃人设）。"""
        key = key or self.get_active_persona_name()
        if not key:
            return {"name": "", "description": "", "personality": []}
        p = self._personas_dir() / f"{key}.json"
        if p.exists():
            try:
                return json.loads(p.read_text("utf-8"))
            except Exception:
                log(f"读取人设配置失败: {p}", "ERROR")
        return {"name": key, "description": "", "personality": []}

    def save_persona_config(self, key: str, data: Dict[str, Any]) -> None:
        """保存人设配置。"""
        d = self._personas_dir()
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{key}.json"
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"人设配置已保存: {key}")

    def delete_persona(self, key: str) -> bool:
        """删除人设。"""
        if key == self.get_active_persona_name():
            log("不能删除当前活跃人设", "WARNING")
            return False
        p = self._personas_dir() / f"{key}.json"
        if p.exists():
            p.unlink()
            log(f"人设已删除: {key}")
            return True
        return False

    # ------------------------------------------------------------------
    # MCP 配置
    # ------------------------------------------------------------------

    def get_mcp_config(self) -> Dict[str, Any]:
        p = Path(self._config.mcp_config_path)
        if p.exists():
            try:
                return json.loads(p.read_text("utf-8"))
            except Exception:
                log(f"读取 MCP 配置失败: {p}", "ERROR")
        return {"servers": []}

    def save_mcp_config(self, data: Dict[str, Any]) -> None:
        p = Path(self._config.mcp_config_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# 全局单例
_provider: Optional[BotConfigProvider] = None


def get_config_provider() -> BotConfigProvider:
    global _provider
    if _provider is None:
        _provider = BotConfigProvider()
    return _provider


def get_mind_config() -> MindConfig:
    """获取 Mind 配置（安全兜底，永不抛异常）。"""
    try:
        return get_config_provider().mind
    except Exception:
        return MindConfig()
