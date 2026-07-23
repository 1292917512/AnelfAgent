"""
统一实体注册系统 —— 项目的中央注册枢纽。

所有模块（工具、MCP 服务器、存储、LLM 模型等）统一以实体方式注册，
通过 EntityRegistry 发现、查询和调用。

实体分组（group）支持注册描述信息，供 AI 进行两级能力发现：
  Level 1: 实体目录（分组名 + 描述 + 工具数）
  Level 2: 查看具体分组的方法列表
"""
from __future__ import annotations

import asyncio
import inspect
import json
from abc import ABC
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Type

from core.event_bus import event_bus, EVENT_TRACE_CALL_START, EVENT_TRACE_CALL_END
from core.exceptions import catch_exceptions
from core.log import log


# ======================================================================
# JSON 容错修复
# ======================================================================

import re as _re

# 非法控制字符（\x00-\x1f 中排除 \t \n \r）
_CTRL_CHAR_RE = _re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def repair_json_arguments(arguments: str) -> str:
    """对 LLM 生成的 JSON 参数做常见容错修复。

    处理 LLM function calling 输出的高频格式错误：
    - 移除 } / ] 前的尾部逗号
    - 移除 // 单行注释
    - 移除非法控制字符

    注释与尾逗号清理仅在字符串字面量之外进行，
    避免破坏字符串值内容（如 URL 中的 //）。
    """
    if not arguments:
        return arguments

    out: List[str] = []
    i, n = 0, len(arguments)
    in_string = False
    while i < n:
        ch = arguments[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(arguments[i + 1])
                i += 1
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
            out.append(ch)
        elif ch == "/" and arguments[i + 1:i + 2] == "/":
            # 字符串外的 // 注释：跳过至行尾
            while i < n and arguments[i] not in "\r\n":
                i += 1
            continue
        elif ch == ",":
            # 字符串外的尾部逗号：下一个非空白字符是 } 或 ] 时丢弃
            j = i + 1
            while j < n and arguments[j] in " \t\r\n":
                j += 1
            if j < n and arguments[j] in "}]":
                i += 1
                continue
            out.append(ch)
        else:
            out.append(ch)
        i += 1

    return _CTRL_CHAR_RE.sub("", "".join(out))


# ======================================================================
# 类型定义
# ======================================================================


class EntityType(Enum):
    """实体类型"""
    SERVICE = "service"
    COMPONENT = "component"
    TOOL = "tool"
    MCP_SERVER = "mcp_server"
    STORAGE = "storage"
    MODEL = "model"
    PLUGIN = "plugin"
    DATABASE = "database"
    ADAPTER = "adapter"
    CUSTOM = "custom"


@dataclass
class ToolParam:
    """工具参数描述"""
    name: str
    description: str = ""
    type: str = "string"
    required: bool = True
    enum: Optional[List[str]] = None
    default: Any = inspect.Parameter.empty
    schema_extra: Optional[Dict[str, Any]] = None


@dataclass
class EntityMetadata:
    """实体元数据

    同时支持完整实体（BaseEntity 子类实例）和轻量级实体（工具函数等）。
    ``meta`` 字典按实体类型存储专属数据：
    - TOOL:       {"params": [ToolParam(...)]}
    - MCP_SERVER: {"transport": "stdio|sse", "command": "...", "connected": bool}
    - MODEL:      {"mode": "web|local", "model_name": "...", "provider": "..."}
    - STORAGE:    {"backend": "sqlite|mongo|memory", "domains": [...]}
    """
    name: str
    entity_type: EntityType
    description: str = ""
    enabled: bool = True
    group: str = ""
    source: str = "builtin"
    tags: List[str] = field(default_factory=list)

    instance: Any = None
    entity_class: Optional[Type] = None

    func: Optional[Callable] = None
    is_async: bool = False

    meta: Dict[str, Any] = field(default_factory=dict)

    config_group: str = ""

    @property
    def check_fn(self) -> Optional[Callable]:
        """工具门控前置检查函数（meta["check_fn"]），None 表示无门控。"""
        return self.meta.get("check_fn")

    @property
    def allow_sleep(self) -> bool:
        """工具是否允许沉睡（meta["allow_sleep"]）。"""
        return bool(self.meta.get("allow_sleep"))

    @property
    def sleep_brief(self) -> str:
        """工具沉睡时展示的简短描述（meta["sleep_brief"]）。"""
        return str(self.meta.get("sleep_brief") or "")

    def get_registered_apis(self) -> List[str]:
        """获取实体对外暴露的接口列表"""
        if self.instance and hasattr(self.instance, '_registered_apis'):
            apis = self.instance._registered_apis
            if apis:
                return apis.copy()
        if self.entity_type == EntityType.TOOL and self.func is not None:
            return [self.name]
        return []

    def get_config(self, key: str, default: Any = None) -> Any:
        """获取实体绑定的配置值"""
        from core.config import ConfigManager
        return ConfigManager.get(key, default)

    def set_config(self, key: str, value: Any) -> None:
        """设置实体绑定的配置值"""
        from core.config import ConfigManager
        ConfigManager.set(key, value)

    def get_all_configs(self) -> Dict[str, Any]:
        """获取实体所属配置分组的所有配置项及其当前值"""
        if not self.config_group:
            return {}
        from core.config import ConfigRegistry, ConfigManager
        items = ConfigRegistry.get_group_items(self.config_group)
        return {
            item.key: ConfigManager.get(item.key, item.default_value)
            for item in items
        }

    def get_config_items(self) -> List[Dict[str, Any]]:
        """获取实体配置项描述列表（含类型、默认值等元信息）"""
        if not self.config_group:
            return []
        from core.config import ConfigRegistry, ConfigManager
        items = ConfigRegistry.get_group_items(self.config_group)
        return [
            {
                "key": item.key,
                "description": item.description,
                "value": ConfigManager.get(item.key, item.default_value),
                "default": item.default_value,
                "type": item.value_type.value if hasattr(item.value_type, 'value') else str(item.value_type),
                "editable": item.editable,
                "required": item.required,
            }
            for item in items
        ]


# ======================================================================
# 参数类型矫正
# ======================================================================


def _coerce_param_value(value: Any, declared: str) -> Any:
    """按 schema 声明类型矫正单个参数值，无法明确转换时返回原值。

    仅做无损明确转换：string←number/bool、integer←数字字符串/整值浮点、
    number←数字字符串、boolean←"true"/"false"/0/1。
    注意 bool 是 int 子类，数值分支需先排除 bool。
    """
    if declared == "string":
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        if isinstance(value, (int, float)):
            return str(value)
        return value
    if declared == "integer":
        if isinstance(value, bool):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str):
            try:
                return int(value.strip())
            except ValueError:
                return value
        return value
    if declared == "number":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                return value
        return value
    if declared == "boolean":
        if isinstance(value, str):
            lower = value.strip().lower()
            if lower in ("true", "1"):
                return True
            if lower in ("false", "0"):
                return False
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        return value
    return value


def _validate_param_types(params: List[ToolParam], kwargs: Dict[str, Any]) -> str:
    """校验矫正后的参数类型，返回错误描述（空串表示通过）。

    矫正失败的非法值（如 integer 参数收到 "abc"）在此拦截，
    给 AI 清晰可行动的反馈，而非让其在工具内部崩溃成 TypeError。
    """
    if not params or not kwargs:
        return ""
    type_map = {p.name: p.type for p in params}
    for key, value in kwargs.items():
        declared = type_map.get(key)
        if declared == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                return f"参数 {key} 需要整数，收到: {value!r}"
        elif declared == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return f"参数 {key} 需要数字，收到: {value!r}"
        elif declared == "boolean":
            if not isinstance(value, bool):
                return f"参数 {key} 需要布尔值，收到: {value!r}"
    return ""


def _coerce_kwargs_types(params: List[ToolParam], kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """按工具 schema 声明类型矫正参数值（LLM 可能传错 JSON 类型）。

    例如纯数字 ID 被按 JSON number 传递、布尔值被按字符串传递时，
    在调用工具函数前统一矫正；schema 未声明的参数原样保留。
    """
    if not params or not kwargs:
        return kwargs
    type_map = {p.name: p.type for p in params}
    coerced: Dict[str, Any] = {}
    for key, value in kwargs.items():
        declared = type_map.get(key)
        if declared is None:
            coerced[key] = value
            continue
        new_value = _coerce_param_value(value, declared)
        if new_value is not value:
            log(
                f"参数类型矫正: {key} ({type(value).__name__} -> {declared})",
                "DEBUG", tag="实体",
            )
        coerced[key] = new_value
    return coerced


def _func_accepts_var_kwargs(func: Callable) -> bool:
    """判断工具函数是否声明了 **kwargs（声明则未知名参属于合法透传）。"""
    try:
        return any(
            p.kind == inspect.Parameter.VAR_KEYWORD
            for p in inspect.signature(func).parameters.values()
        )
    except (TypeError, ValueError):
        return True


def _unwrap_nested_arguments(params: List[ToolParam], kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """解开 LLM 偶发的参数嵌套包装（如 {"tool_args": "{\\"image_path\\": ...}"}）。

    部分模型按训练先验把真实参数包进单个包装键（tool_args/arguments 等），
    字符串化后作为唯一参数传入。当没有任何键命中声明参数、且恰好一个
    包装键的值可解析为非空 dict 时，以解包结果为准；与声明参数混传时
    不做猜测，保持原样交由后续校验反馈。
    """
    if not kwargs:
        return kwargs
    declared = {p.name for p in params}
    if any(k in declared for k in kwargs):
        return kwargs
    wrappers = [k for k in kwargs if k != "_timeout"]
    if len(wrappers) != 1:
        return kwargs
    inner: Any = kwargs[wrappers[0]]
    if isinstance(inner, str):
        text = inner.strip()
        if not text.startswith("{"):
            return kwargs
        try:
            inner = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return kwargs
    if isinstance(inner, dict) and inner:
        log(
            f"参数嵌套包装已解包: {wrappers[0]} -> {list(inner)}",
            "WARNING", tag="实体",
        )
        if "_timeout" in kwargs and "_timeout" not in inner:
            inner["_timeout"] = kwargs["_timeout"]
        return inner
    return kwargs


# ======================================================================
# ======================================================================


class BaseEntity(ABC):
    """实体基类 - 实例化时自动注册到 EntityRegistry"""

    def __init__(self) -> None:
        super().__init__()
        self._registered_apis: List[str] = []
        self._register_instance()

    @catch_exceptions()
    def _register_instance(self) -> None:
        cls = self.__class__
        instance_name = f"{cls.__module__}.{cls.__name__}_{id(self)}"
        self._register_entity_configs()

        entity_configs = getattr(cls, '_entity_configs', None)
        cfg_group = ""
        if entity_configs:
            cfg_group = next(iter(entity_configs), "")

        metadata = EntityMetadata(
            name=instance_name,
            entity_type=getattr(cls, '_entity_type', EntityType.CUSTOM),
            entity_class=cls,
            description=(
                getattr(cls, '_entity_description', '')
                or cls.__doc__
                or f"{cls.__name__}实例"
            ),
            enabled=True,
            instance=self,
            config_group=cfg_group,
        )
        EntityRegistry.register(metadata)
        EntityRegistry.activate_entity(instance_name)

    def get_entity_name(self) -> str:
        cls = self.__class__
        return f"{cls.__module__}.{cls.__name__}_{id(self)}"

    @catch_exceptions()
    def _register_entity_configs(self) -> None:
        entity_configs = getattr(self.__class__, '_entity_configs', None)
        if entity_configs:
            from core.config import register_configs
            register_configs(entity_configs)

    def get_entity_config(self, key: str, default: Any = None) -> Any:
        from core.config import ConfigManager
        return ConfigManager.get(key, default)

    def register_api(self, name: Optional[str] = None, description: str = ""):
        """装饰器：将实体方法注册为可发现的 API"""
        @catch_exceptions(reraise=False, tag="entity")
        def decorator(method):  # type: ignore[no-untyped-def]
            api_name = name or f"{self.get_entity_name()}.{method.__name__}"
            des = description or f"{self.__class__.__name__}的{method.__name__}方法"
            self._registered_apis.append(api_name)
            log(f"✅ 实体API注册: {api_name}", "DEBUG")
            return method
        return decorator

    def get_registered_apis(self) -> List[str]:
        return self._registered_apis.copy()


# ======================================================================
# 常量
# ======================================================================

_DEFAULT_TOOL_TIMEOUT = 60.0
_SKIP_ENTITY_METHODS = frozenset({
    'get_entity_name', 'get_entity_config',
    'register_api', 'get_registered_apis',
})


# ======================================================================
# EntityRegistry —— 中央注册枢纽
# ======================================================================


class EntityRegistry:
    """中央实体注册表

    所有模块在初始化时注册为实体，所有能力通过本注册表发现和调用。
    """

    _entities: Dict[str, EntityMetadata] = {}
    _types: Dict[EntityType, List[str]] = {}
    _groups: Dict[str, List[str]] = {}
    _group_descriptions: Dict[str, str] = {}
    # 工具名候选缓存（未知工具名纠错建议用），注册/注销时失效
    _names_cache: Optional[List[str]] = None

    # ------------------------------------------------------------------
    # 通用注册 / 注销
    # ------------------------------------------------------------------

    @classmethod
    @catch_exceptions(reraise=False, default_value=False, tag="entity")
    def register(cls, metadata: EntityMetadata) -> bool:
        """注册实体"""
        if metadata.name in cls._entities:
            existing = cls._entities[metadata.name]
            if (existing.entity_class and metadata.entity_class
                    and existing.entity_class != metadata.entity_class):
                log(f"⚠️ 实体名称冲突: {metadata.name}", "WARNING")
                return False
            if existing.source != metadata.source:
                log(
                    f"⚠️ 实体名称冲突，已覆盖: {metadata.name} "
                    f"({existing.source} → {metadata.source})",
                    "WARNING",
                )
            cls._remove_from_indexes(metadata.name)

        cls._entities[metadata.name] = metadata
        cls._types.setdefault(metadata.entity_type, []).append(metadata.name)
        if metadata.group:
            cls._groups.setdefault(metadata.group, []).append(metadata.name)
        cls._names_cache = None

        log(f"✅ 实体注册: {metadata.name} [{metadata.entity_type.value}]", "DEBUG")
        return True

    @classmethod
    def _remove_from_indexes(cls, name: str) -> None:
        old = cls._entities.get(name)
        if not old:
            return
        if old.entity_type in cls._types:
            try:
                cls._types[old.entity_type].remove(name)
            except ValueError:
                pass
        if old.group and old.group in cls._groups:
            try:
                cls._groups[old.group].remove(name)
            except ValueError:
                pass

    @classmethod
    @catch_exceptions(reraise=False, default_value=False, tag="entity")
    def unregister(cls, name: str) -> bool:
        """注销实体"""
        if name not in cls._entities:
            return False
        metadata = cls._entities[name]
        if metadata.instance:
            cls.deactivate_entity(name)
        cls._remove_from_indexes(name)
        del cls._entities[name]
        cls._names_cache = None
        log(f"✅ 实体注销: {name}")
        return True

    # ------------------------------------------------------------------
    # 便捷注册方法
    # ------------------------------------------------------------------

    @classmethod
    def register_tool(
        cls,
        name: str,
        func: Callable,
        description: str = "",
        group: str = "default",
        params: Optional[List[ToolParam]] = None,
        tags: Optional[List[str]] = None,
        source: str = "internal",
        cacheable: bool = False,
        meta: Optional[Dict[str, Any]] = None,
        check_fn: Optional[Callable] = None,
        allow_sleep: bool = False,
        sleep_brief: str = "",
    ) -> bool:
        """注册工具实体。

        Args:
            check_fn: 工具门控前置检查（零参数 callable，返回 bool 或 Awaitable[bool]），
                检查不通过时工具不出现在 LLM schema 中。
            allow_sleep: 是否允许沉睡（沉睡时仅展示 sleep_brief，激活后恢复完整 schema）。
            sleep_brief: 沉睡状态下展示给 AI 的简短描述。
        """
        tool_meta = {"params": params or []}
        if meta:
            tool_meta.update(meta)
        if check_fn is not None:
            tool_meta["check_fn"] = check_fn
        if allow_sleep:
            tool_meta["allow_sleep"] = True
        if sleep_brief:
            tool_meta["sleep_brief"] = sleep_brief
        
        return cls.register(EntityMetadata(
            name=name,
            entity_type=EntityType.TOOL,
            description=description or name,
            enabled=True,
            group=group,
            source=source,
            tags=tags or [],
            func=func,
            is_async=inspect.iscoroutinefunction(func),
            meta=tool_meta,
        ))

    @classmethod
    def register_group(cls, group: str, description: str) -> None:
        """注册实体分组描述，供 AI 进行实体目录发现。"""
        cls._group_descriptions[group] = description
        log(f"✅ 实体分组注册: {group}", "DEBUG")

    @classmethod
    def get_group_description(cls, group: str) -> str:
        """获取分组描述"""
        return cls._group_descriptions.get(group, "")

    @classmethod
    def list_groups(cls) -> List[str]:
        """返回所有已注册的分组名。"""
        return list(cls._groups.keys())

    _CATALOG_ORDER: Dict[str, int] = {
        "output": 0, "memory": 1, "notes": 2, "thinking": 3, "planning": 4,
        "web": 5, "media": 6, "minimax": 7, "os": 8, "environment": 9,
        "model_control": 10, "ollama": 11, "logs": 12, "channel_ops": 13,
        "entity": 14, "mcp_manage": 15, "devops": 16,
        "skills": 17, "delegation": 18, "ui": 19,
    }

    @classmethod
    def get_entity_catalog(cls) -> List[Dict[str, Any]]:
        """生成实体目录（两级发现的第一级）。

        只返回分组名、描述和工具数量，不含具体工具名。
        具体工具通过 list_entity_methods 按需查询。
        按预定义顺序排列，mcp:* 动态分组排在末尾。
        """
        catalog: List[Dict[str, Any]] = []
        seen_groups: set[str] = set()

        for group, names in cls._groups.items():
            if group in seen_groups:
                continue
            seen_groups.add(group)
            tool_count = sum(
                1 for n in names
                if n in cls._entities
                and cls._entities[n].entity_type == EntityType.TOOL
                and cls._entities[n].enabled
            )
            if not tool_count:
                continue
            catalog.append({
                "group": group,
                "description": cls._group_descriptions.get(group, ""),
                "tool_count": tool_count,
            })

        def _sort_key(entry: Dict[str, Any]) -> tuple:
            g = entry["group"]
            if g.startswith("mcp:"):
                return (200, g)
            return (cls._CATALOG_ORDER.get(g, 100), g)

        catalog.sort(key=_sort_key)
        return catalog

    @classmethod
    def import_from_api_registry(cls) -> int:
        """从 APIRegistry 批量导入 PUBLIC API 作为工具实体。

        自 Phase 5 起，PUBLIC API 应直接通过 EntityRegistry.register_tool() 注册。
        """
        try:
            from core.api import APIRegistry, ApiScope
        except ImportError:
            return 0

        count = 0
        for api_meta in APIRegistry.get_by_scope(ApiScope.PUBLIC):
            if not api_meta.enabled or cls.exists(api_meta.name):
                continue
            params: List[ToolParam] = [
                ToolParam(
                    name=p.name,
                    type=_python_type_to_json_type(p.param_type),
                    required=p.required,
                    default=p.default_value,
                )
                for p in api_meta.parameters
            ]
            cls.register_tool(
                name=api_meta.name,
                func=api_meta.func,
                description=api_meta.description,
                group=api_meta.group,
                params=params,
                tags=api_meta.tags,
                source="api_registry",
            )
            count += 1
        if count:
            log(f"从 APIRegistry 导入 {count} 个工具实体")
        return count

    # ------------------------------------------------------------------
    # 查询方法
    # ------------------------------------------------------------------

    @classmethod
    def get(cls, name: str) -> Optional[EntityMetadata]:
        return cls._entities.get(name)

    @classmethod
    def get_instance(cls, name: str) -> Optional[Any]:
        metadata = cls._entities.get(name)
        if metadata and metadata.enabled and metadata.instance:
            return metadata.instance
        return None

    @classmethod
    def get_by_type(cls, entity_type: EntityType) -> List[EntityMetadata]:
        names = cls._types.get(entity_type, [])
        return [cls._entities[n] for n in names if n in cls._entities]

    @classmethod
    def get_by_group(cls, group: str) -> List[EntityMetadata]:
        names = cls._groups.get(group, [])
        return [cls._entities[n] for n in names if n in cls._entities]

    @classmethod
    def get_all(cls) -> List[EntityMetadata]:
        return list(cls._entities.values())

    @classmethod
    def get_all_names(cls) -> List[str]:
        return list(cls._entities.keys())

    @classmethod
    def _get_all_names_cached(cls) -> List[str]:
        """实体名候选列表（带缓存，供未知工具名纠错建议复用）。"""
        if cls._names_cache is None:
            cls._names_cache = list(cls._entities.keys())
        return cls._names_cache

    @classmethod
    def exists(cls, name: str) -> bool:
        return name in cls._entities

    @classmethod
    def search(cls, keyword: str) -> List[EntityMetadata]:
        """按关键词搜索实体（名称/描述/分组/标签）"""
        kw = keyword.lower()
        return [
            e for e in cls._entities.values()
            if (kw in e.name.lower()
                or kw in e.description.lower()
                or kw in e.group.lower()
                or any(kw in t.lower() for t in e.tags))
        ]

    @classmethod
    def get_by_tag(cls, tag: str) -> List[EntityMetadata]:
        """按标签精确匹配查询实体。"""
        tag_lower = tag.lower()
        return [
            e for e in cls._entities.values()
            if any(tag_lower == t.lower() for t in e.tags)
        ]

    # ------------------------------------------------------------------
    # 工具系统方法
    # ------------------------------------------------------------------

    @classmethod
    def get_tool_schemas(cls, *, enabled_only: bool = True) -> List[Dict[str, Any]]:
        """生成所有 TOOL 实体的 OpenAI function-calling schema"""
        return [
            cls._build_tool_schema(e)
            for e in cls.get_by_type(EntityType.TOOL)
            if not enabled_only or e.enabled
        ]

    @classmethod
    def get_tool_schema_by_names(cls, names: List[str]) -> List[Dict[str, Any]]:
        """按工具名列表构建子集 schema"""
        return [
            cls._build_tool_schema(e)
            for n in names
            if (e := cls.get(n)) and e.entity_type == EntityType.TOOL and e.enabled
        ]

    @classmethod
    def get_tool_schemas_by_group(cls, group: str) -> List[Dict[str, Any]]:
        """按分组构建工具 schema"""
        return [
            cls._build_tool_schema(e)
            for e in cls.get_by_group(group)
            if e.entity_type == EntityType.TOOL and e.enabled
        ]

    @classmethod
    def get_tool_schema_by_tags(cls, tags: List[str]) -> List[Dict[str, Any]]:
        """返回包含任一指定 tag 的已启用工具的 schema。"""
        tags_lower = {t.lower() for t in tags}
        seen: set[str] = set()
        result: List[Dict[str, Any]] = []
        for e in cls._entities.values():
            if e.entity_type != EntityType.TOOL or not e.enabled or not e.func:
                continue
            if e.name in seen:
                continue
            if any(t.lower() in tags_lower for t in e.tags):
                seen.add(e.name)
                result.append(cls._build_tool_schema(e))
        return result

    @classmethod
    async def get_active_tools(cls, names: List[str]) -> List[EntityMetadata]:
        """按名称返回通过门控检查的工具实体（check_fn 不通过的被过滤）。

        门控结果带 TTL 缓存（见 core.tool_gate.ToolGate），
        门控总开关关闭时不过滤直接返回。
        """
        entities = [
            e for n in names
            if (e := cls.get(n)) and e.entity_type == EntityType.TOOL and e.enabled and e.func
        ]
        try:
            from core.tool_gate import tool_gate, is_gate_enabled
        except ImportError:
            return entities
        if not is_gate_enabled():
            return entities
        verdicts = await tool_gate.filter_names({e.name: e.check_fn for e in entities})
        filtered = [e for e in entities if verdicts.get(e.name, True)]
        blocked = [e.name for e in entities if not verdicts.get(e.name, True)]
        if blocked:
            log(f"工具门控过滤: {', '.join(blocked)} (check_fn 未通过)", "DEBUG", tag="门控")
        return filtered

    @classmethod
    async def get_active_tool_schema_by_names(cls, names: List[str]) -> List[Dict[str, Any]]:
        """按名称构建工具 schema（经门控过滤）。"""
        return [cls._build_tool_schema(e) for e in await cls.get_active_tools(names)]

    @classmethod
    def get_sleepable_groups(cls) -> Dict[str, Dict[str, Any]]:
        """返回可沉睡的工具分组信息 {group: {"brief": ..., "tool_count": ...}}。

        分组内任一工具声明 allow_sleep=True 且带 sleep_brief 时，
        该分组视为可沉睡分组，沉睡期间以 brief 代替完整 schema 展示。
        """
        result: Dict[str, Dict[str, Any]] = {}
        for e in cls._entities.values():
            if e.entity_type != EntityType.TOOL or not e.enabled or not e.func:
                continue
            if not (e.allow_sleep and e.sleep_brief):
                continue
            entry = result.setdefault(e.group, {"brief": e.sleep_brief, "tool_count": 0})
            entry["tool_count"] += 1
        return result

    @staticmethod
    def _build_tool_schema(entity: EntityMetadata) -> Dict[str, Any]:
        """单个 TOOL 实体 -> OpenAI function schema"""
        properties: Dict[str, Any] = {}
        required: List[str] = []

        for p in entity.meta.get("params", []):
            prop: Dict[str, Any] = {"type": p.type}
            if p.description:
                prop["description"] = p.description
            if p.enum:
                prop["enum"] = p.enum
            if p.schema_extra:
                prop.update(p.schema_extra)
            properties[p.name] = prop
            if p.required:
                required.append(p.name)

        # AI 可动态指定超时时间
        properties["_timeout"] = {
            "type": "number",
            "description": "执行超时时间（秒），不传则使用默认值",
        }

        params_schema: Dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            params_schema["required"] = required

        return {
            "type": "function",
            "function": {
                "name": entity.name,
                "description": entity.description or entity.name,
                "parameters": params_schema,
            },
        }

    @classmethod
    async def execute_tool(
        cls,
        name: str,
        arguments: str = "",
        *,
        timeout: float = _DEFAULT_TOOL_TIMEOUT,
    ) -> str:
        """执行工具实体（带超时保护）。"""
        entity = cls.get(name)
        if entity is None:
            import difflib
            catalog = cls.get_entity_catalog()
            groups = [
                f"{e['group']}({e['tool_count']}个方法)"
                for e in catalog
            ]
            all_tool_names = cls._get_all_names_cached()
            suggested = difflib.get_close_matches(name, all_tool_names, n=8, cutoff=0.35)
            return json.dumps({
                "error": f"工具 '{name}' 不存在或当前不可用，请勿猜测工具名。",
                "hint": '请先调用 list_entity_methods({"group": "分组名"}) 查看该实体的具体方法名和参数。'
                        '部分工具可能因门控检查未通过或处于沉睡状态而暂时隐藏；'
                        '被管理员禁用或被权限规则拒绝的工具会在调用时收到明确的原因说明。',
                "available_groups": groups,
                "suggested_tools": suggested,
            }, ensure_ascii=False)
        if not entity.enabled:
            return json.dumps({"error": f"工具已禁用: {name}"}, ensure_ascii=False)
        if entity.func is None:
            return json.dumps({"error": f"工具无执行函数: {name}"}, ensure_ascii=False)

        try:
            repaired = repair_json_arguments(arguments) if arguments else "{}"
            kwargs = json.loads(repaired) if arguments else {}
        except json.JSONDecodeError as e:
            preview = arguments[:200] if arguments else ""
            return json.dumps(
                {"error": f"参数 JSON 解析失败: {e}", "args_preview": preview},
                ensure_ascii=False,
            )

        # 按 schema 声明类型矫正参数（LLM 可能传错 JSON 类型，如数字 ID 按 number 传递）
        if isinstance(kwargs, dict):
            # 解开模型先验产生的嵌套包装（{"tool_args": "{...}"}）
            kwargs = _unwrap_nested_arguments(entity.meta.get("params") or [], kwargs)
            kwargs = _coerce_kwargs_types(entity.meta.get("params") or [], kwargs)
            # 矫正失败的非法值在此拦截，给 AI 清晰反馈而非工具内部 TypeError
            type_error = _validate_param_types(entity.meta.get("params") or [], kwargs)
            if type_error:
                return json.dumps({
                    "error": f"参数类型错误: {type_error}",
                    "hint": "请按工具 schema 声明的类型传参后重试",
                }, ensure_ascii=False)
            # schema 未声明的参数对不接收 **kwargs 的工具必然崩溃成 TypeError，
            # 提前拦截并返回正确参数列表，避免 AI 盲猜参数名反复失败
            if not _func_accepts_var_kwargs(entity.func):
                declared = {p.name for p in entity.meta.get("params") or []}
                declared.add("_timeout")
                unknown = sorted(k for k in kwargs if k not in declared)
                if unknown:
                    log(
                        f"工具参数拦截: {name} 收到未知参数 {unknown}",
                        "WARNING", tag="实体",
                    )
                    return json.dumps({
                        "error": f"参数错误: 工具 {name} 不接受参数 {unknown}",
                        "valid_params": sorted(declared - {"_timeout"}),
                        "hint": "请仅使用 valid_params 列出的参数名修正后重试，不要猜测其他参数名",
                    }, ensure_ascii=False)

        # 优先级: AI 传入 > 装饰器定义 > 全局默认
        ai_timeout = kwargs.pop("_timeout", None)
        meta_timeout = entity.meta.get("timeout") if entity.meta else None

        if isinstance(ai_timeout, (int, float)) and ai_timeout > 0:
            tool_timeout = float(ai_timeout)
        elif meta_timeout is not None:
            tool_timeout = float(meta_timeout)
        else:
            tool_timeout = timeout

        import uuid as _uuid
        call_id = _uuid.uuid4().hex[:8]
        t0 = asyncio.get_running_loop().time()

        log(f"▶ 执行工具: {name}({arguments[:120] if arguments else ''})", "DEBUG", tag="实体")
        await event_bus.emit(EVENT_TRACE_CALL_START, {
            "call_id": call_id,
            "name": name,
            "group": entity.group,
            "entity_type": entity.entity_type.value,
            "arguments_preview": arguments[:200] if arguments else "",
        })

        try:
            if entity.is_async:
                coro = entity.func(**kwargs)
            else:
                coro = asyncio.to_thread(entity.func, **kwargs)
            result = await asyncio.wait_for(coro, timeout=tool_timeout)
        except asyncio.TimeoutError:
            dur = round((asyncio.get_running_loop().time() - t0) * 1000)
            log(f"工具执行超时 ({tool_timeout}s): {name}", "WARNING", tag="实体")
            await event_bus.emit(EVENT_TRACE_CALL_END, {
                "call_id": call_id,
                "name": name,
                "duration_ms": dur,
                "success": False,
                "error": f"执行超时 ({tool_timeout}s)",
            })
            return json.dumps(
                {"error": f"工具执行超时 ({tool_timeout}s): {name}"},
                ensure_ascii=False,
            )
        except Exception as exc:
            dur = round((asyncio.get_running_loop().time() - t0) * 1000)
            log(f"工具执行异常: {name} - {exc}", "ERROR", tag="实体")
            await event_bus.emit(EVENT_TRACE_CALL_END, {
                "call_id": call_id,
                "name": name,
                "duration_ms": dur,
                "success": False,
                "error": str(exc),
            })
            return json.dumps({"error": str(exc)}, ensure_ascii=False)

        dur = round((asyncio.get_running_loop().time() - t0) * 1000)
        result_str = _serialize_result(result)
        log(f"◀ 工具完成: {name} → {result_str[:150]}", "DEBUG", tag="实体")
        await event_bus.emit(EVENT_TRACE_CALL_END, {
            "call_id": call_id,
            "name": name,
            "duration_ms": dur,
            "success": True,
            "result_preview": result_str[:200],
        })
        return result_str

    @classmethod
    async def execute_tool_call(cls, tool_call: Any) -> str:
        """执行 LLM 返回的 ToolCall（duck-typed: 需有 .name 和 .arguments）"""
        return await cls.execute_tool(tool_call.name, tool_call.arguments or "")

    # ------------------------------------------------------------------
    # 状态管理
    # ------------------------------------------------------------------

    @classmethod
    def enable(cls, name: str) -> bool:
        if name in cls._entities:
            cls._entities[name].enabled = True
            log(f"✅ 实体已启用: {name}")
            return True
        return False

    @classmethod
    def disable(cls, name: str) -> bool:
        if name in cls._entities:
            cls._entities[name].enabled = False
            log(f"⚠️ 实体已禁用: {name}")
            return True
        return False

    @classmethod
    def enable_group(cls, group: str) -> int:
        """Enable all entities in a group. Returns count affected."""
        names = cls._groups.get(group, [])
        count = 0
        for n in names:
            if n in cls._entities and not cls._entities[n].enabled:
                cls._entities[n].enabled = True
                count += 1
        if count:
            log(f"✅ group enabled: {group} ({count} entities)")
        return count

    @classmethod
    def disable_group(cls, group: str) -> int:
        """Disable all entities in a group. Returns count affected."""
        names = cls._groups.get(group, [])
        count = 0
        for n in names:
            if n in cls._entities and cls._entities[n].enabled:
                cls._entities[n].enabled = False
                count += 1
        if count:
            log(f"⚠️ group disabled: {group} ({count} entities)")
        return count

    @classmethod
    def unregister_group(cls, group: str) -> int:
        """Unregister all entities in a group. Returns count removed."""
        names = list(cls._groups.get(group, []))
        count = 0
        for n in names:
            if cls.unregister(n):
                count += 1
        cls._groups.pop(group, None)
        cls._group_descriptions.pop(group, None)
        if count:
            log(f"🧹 group unregistered: {group} ({count} entities)")
        return count

    @classmethod
    def is_group_enabled(cls, group: str) -> bool:
        """Check if all TOOL entities in a group are enabled."""
        names = cls._groups.get(group, [])
        tools = [
            cls._entities[n] for n in names
            if n in cls._entities and cls._entities[n].entity_type == EntityType.TOOL
        ]
        return bool(tools) and all(t.enabled for t in tools)

    @classmethod
    @catch_exceptions(reraise=False, default_value=False, tag="entity")
    def activate_entity(cls, name: str) -> bool:
        """激活实体并注册其公共方法为 API（仅 BaseEntity 实例）"""
        metadata = cls.get(name)
        if not metadata or not metadata.instance:
            log(f"❌ 实体不存在或无实例: {name}", "ERROR")
            return False

        metadata.enabled = True
        entity_type = getattr(
            metadata.entity_class, '_entity_type', EntityType.CUSTOM,
        ).value
        entity_name = metadata.entity_class.__name__  # type: ignore[union-attr]
        api_group = f"{entity_type}/{entity_name}"
        cls._auto_register_entity_apis(metadata, api_group)
        log(f"✅ 实体已激活: {name}")
        return True

    @classmethod
    @catch_exceptions(reraise=False, default_value=False, tag="entity")
    def deactivate_entity(cls, name: str) -> bool:
        """失活实体并清理其已注册 API 跟踪记录"""
        metadata = cls.get(name)
        if not metadata:
            return False

        metadata.enabled = False
        if metadata.instance and hasattr(metadata.instance, '_registered_apis'):
            metadata.instance._registered_apis.clear()

        log(f"✅ 实体已失活: {name}")
        return True

    @classmethod
    @catch_exceptions(reraise=False, tag="entity")
    def _auto_register_entity_apis(
        cls, metadata: EntityMetadata, api_group: str,
    ) -> None:
        instance = metadata.instance
        if not instance:
            return
        for method_name in dir(instance):
            if (not method_name.startswith('_')
                    and callable(getattr(instance, method_name))
                    and method_name not in _SKIP_ENTITY_METHODS):
                api_name = f"{metadata.name}.{method_name}"
                if hasattr(instance, '_registered_apis'):
                    if api_name not in instance._registered_apis:
                        instance._registered_apis.append(api_name)

    # ------------------------------------------------------------------
    # 清理 / 统计
    # ------------------------------------------------------------------

    @classmethod
    def clear(cls) -> None:
        """清空所有实体"""
        for name in list(cls._entities.keys()):
            metadata = cls._entities.get(name)
            if metadata and metadata.instance:
                cls.deactivate_entity(name)
        cls._entities.clear()
        cls._types.clear()
        cls._groups.clear()
        cls._group_descriptions.clear()
        cls._names_cache = None
        log("🧹 实体注册表已清空")

    @classmethod
    def get_statistics(cls) -> Dict[str, Any]:
        """获取统计摘要"""
        return {
            'total_entities': len(cls._entities),
            'enabled_entities': sum(1 for e in cls._entities.values() if e.enabled),
            'types': {
                t.value: len(names)
                for t, names in cls._types.items() if names
            },
            'groups': {
                g: len(names)
                for g, names in cls._groups.items() if names
            },
        }

    @classmethod
    def get_summary(cls) -> Dict[str, Any]:
        """生成实体概览（供 AI query_entities 工具使用）"""
        summary: Dict[str, Any] = {
            "total": len(cls._entities),
            "enabled": sum(1 for e in cls._entities.values() if e.enabled),
            "types": {},
        }
        for etype in EntityType:
            entities = cls.get_by_type(etype)
            if not entities:
                continue
            summary["types"][etype.value] = {
                "count": len(entities),
                "enabled": sum(1 for e in entities if e.enabled),
                "items": [
                    {
                        "name": e.name,
                        "description": e.description,
                        "enabled": e.enabled,
                        "group": e.group,
                        "config_group": e.config_group,
                    }
                    for e in entities
                ],
            }
        return summary


# ======================================================================
# Helpers
# ======================================================================

_PYTHON_TYPE_MAP = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "list": "array",
    "dict": "object",
}


def _python_type_to_json_type(py_type: str) -> str:
    low = py_type.lower().strip("<>\"'class ")
    # 先精确匹配（如 "str"、"list"）
    if low in _PYTHON_TYPE_MAP:
        return _PYTHON_TYPE_MAP[low]
    # 子串匹配时容器类型优先（"List[str]" 应映射 array 而非 string）
    for container in ("list", "dict"):
        if container in low:
            return _PYTHON_TYPE_MAP[container]
    for k, v in _PYTHON_TYPE_MAP.items():
        if k in low:
            return v
    return "string"


def _serialize_result(result: Any) -> str:
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(result)
