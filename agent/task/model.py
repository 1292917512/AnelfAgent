"""任务数据模型：TaskDefinition 定义任务内容，TaskResult 封装执行产出。"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from agent.memory.memory_types import MemoryType


class TaskScope(str, Enum):
    """任务的作用域。"""

    ENTITY = "entity"
    GLOBAL = "global"
    ANY = "any"


_SCOPE_MAP: Dict[str, TaskScope] = {
    "global": TaskScope.GLOBAL,
    "entity": TaskScope.ENTITY,
    "any": TaskScope.ANY,
}

_MEMORY_TYPE_MAP: Dict[str, MemoryType] = {
    "reflection": MemoryType.REFLECTION,
    "semantic": MemoryType.SEMANTIC,
    "entity": MemoryType.ENTITY,
}
_REASONING_EFFORT_VALUES = frozenset({"low", "medium", "high", "max"})


def _to_bool(value: Any) -> bool:
    """兼容字符串/数字的布尔值解析。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if value is None:
        return False
    return bool(value)


def _normalize_reasoning_effort(value: Any) -> Optional[str]:
    """标准化 reasoning_effort：仅接受 low/medium/high/max。"""
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if not normalized:
        return None
    if normalized in _REASONING_EFFORT_VALUES:
        return normalized
    return None


def _normalize_tool_tags(value: Any) -> List[str]:
    """标准化 tool_tags：兼容数组、逗号字符串和中文逗号写法。"""
    if value is None:
        return []

    raw_items: List[str]
    if isinstance(value, str):
        raw_items = [value]
    elif isinstance(value, list):
        raw_items = [str(v) for v in value if v is not None]
    else:
        raw_items = [str(value)]

    result: List[str] = []
    seen: set[str] = set()
    for item in raw_items:
        for part in re.split(r"[,\uFF0C]", item):
            tag = part.strip()
            if not tag:
                continue
            key = tag.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(tag)
    return result


class TaskDefinition(BaseModel):
    """一个可执行任务的完整定义。"""

    name: str
    display_name: str = ""
    description: str = ""
    scope: TaskScope = TaskScope.GLOBAL
    enabled: bool = True
    memory_type: MemoryType = MemoryType.REFLECTION
    importance: float = 0.5
    tags: List[str] = Field(default_factory=list)
    source: str = ""
    null_keywords: List[str] = Field(default_factory=list)
    tool_tags: List[str] = Field(default_factory=list)
    prompt: str = ""
    allow_output_tools: bool = False
    """是否允许该任务在 reflect 阶段使用 send_* 外发工具。"""
    save_result_to_memory: bool = True
    """任务执行产出是否写入记忆。"""
    model_id: Optional[str] = None
    """指定执行该任务的模型 ID，为空时使用默认模型。"""
    reasoning_effort: Optional[str] = None
    """任务级思考等级覆盖（low/medium/high/max），为空时使用全局设置。"""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TaskDefinition:
        scope = _SCOPE_MAP.get(data.get("scope", "global"), TaskScope.GLOBAL)
        memory_type = _MEMORY_TYPE_MAP.get(
            data.get("memory_type", "reflection"), MemoryType.REFLECTION,
        )
        return cls(
            name=data["name"],
            display_name=data.get("display_name", data["name"]),
            description=data.get("description", ""),
            scope=scope,
            enabled=data.get("enabled", True),
            memory_type=memory_type,
            importance=float(data.get("importance", 0.5)),
            tags=list(data.get("tags", [])),
            source=data.get("source", data["name"]),
            null_keywords=list(data.get("null_keywords", [])),
            tool_tags=_normalize_tool_tags(data.get("tool_tags", [])),
            prompt=data.get("prompt", ""),
            allow_output_tools=_to_bool(data.get("allow_output_tools", False)),
            save_result_to_memory=_to_bool(data.get("save_result_to_memory", True)),
            model_id=data.get("model_id") or None,
            reasoning_effort=_normalize_reasoning_effort(data.get("reasoning_effort")),
        )

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "scope": self.scope.value,
            "enabled": self.enabled,
            "memory_type": self.memory_type.value,
            "importance": self.importance,
            "tags": self.tags,
            "source": self.source or self.name,
            "null_keywords": self.null_keywords,
            "prompt": self.prompt,
            "allow_output_tools": self.allow_output_tools,
            "save_result_to_memory": self.save_result_to_memory,
        }
        if self.tool_tags:
            result["tool_tags"] = self.tool_tags
        if self.model_id:
            result["model_id"] = self.model_id
        normalized_effort = _normalize_reasoning_effort(self.reasoning_effort)
        if normalized_effort:
            result["reasoning_effort"] = normalized_effort
        return result

    def should_run_for_entity(self, has_entity: bool) -> bool:
        """判断当前上下文（有/无实体）是否适合执行该任务。"""
        if self.scope == TaskScope.ENTITY and not has_entity:
            return False
        if self.scope == TaskScope.GLOBAL and has_entity:
            return False
        return True


class TaskResult(BaseModel):
    """单个任务的执行产出。"""

    task_name: str
    content: str
    memory_type: MemoryType
    source: str
    tags: List[str] = Field(default_factory=list)
    importance: float = 0.7
