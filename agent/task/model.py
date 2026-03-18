"""任务数据模型：TaskDefinition 定义任务内容，TaskResult 封装执行产出。"""

from __future__ import annotations

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
            tool_tags=list(data.get("tool_tags", [])),
            prompt=data.get("prompt", ""),
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
        }
        if self.tool_tags:
            result["tool_tags"] = self.tool_tags
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
