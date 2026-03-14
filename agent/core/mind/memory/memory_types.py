"""记忆数据结构定义。"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class MemoryType(str, Enum):
    """记忆类型。"""

    EPISODIC = "episodic"
    """事件记忆：对话中发生的具体事件。"""

    SEMANTIC = "semantic"
    """语义记忆：总结性知识、常识。"""

    ENTITY = "entity"
    """实体记忆：人/群画像。"""

    REFLECTION = "reflection"
    """反思记忆：自我反思产物。"""

    PERMANENT = "permanent"
    """永久记忆：重要知识和关键信息，不会被自动清理。"""


class MemoryEntry(BaseModel):
    """单条记忆。"""

    id: Optional[int] = None
    memory_type: MemoryType
    content: str
    source: str = ""
    tags: List[str] = Field(default_factory=list)
    importance: float = 0.5
    timestamp: float = Field(default_factory=time.time)
    access_count: int = 0
    last_accessed: float = 0.0
    metadata: Dict[str, Any] = Field(default_factory=dict)
    embedding: Optional[List[float]] = None

    def to_message(self) -> Dict[str, str]:
        """转换为 LLM messages 格式。"""
        return {"role": "system", "content": self.content}

    def age_hours(self) -> float:
        """距今经过的小时数。"""
        return (time.time() - self.timestamp) / 3600.0


class MemorySearchResult(BaseModel):
    """统一的搜索结果，兼容文件 chunk 和工具记忆两种来源。"""

    id: str
    """结果标识：chunk id 或 "mem:{memory_id}"。"""
    path: str = ""
    """来源文件路径（仅 file 来源有值）。"""
    start_line: int = 0
    end_line: int = 0
    snippet: str
    """摘要文本（最多 700 字符）。"""
    score: float
    source: Literal["file", "memory"] = "memory"
    """来源类型：file = MD 文件 chunk，memory = memories 表。"""
    memory_type: Optional[str] = None
    """原始 MemoryType 值（仅 memory 来源有值）。"""
    tags: List[str] = Field(default_factory=list)
