"""记忆数据结构定义。"""

from __future__ import annotations

import re
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
    version: int = 1
    """内容修订版本号：update/合并演进时 +1，审计与冲突排查用。"""
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
    source: Literal["file", "memory", "cognee_graph", "cognee_chunk"] = "memory"
    """来源类型：本地记忆、文件块或 Cognee 图/向量结果。"""
    memory_type: Optional[str] = None
    """原始 MemoryType 值（仅 memory 来源有值）。"""
    tags: List[str] = Field(default_factory=list)
    timestamp: float = 0.0
    """记忆写入时间（Unix 秒，仅 memory 来源有值）。"""
    sensitivity: str = "normal"
    """私密度：normal / private / secret（仅 memory 来源有值，注入侧标注用）。"""
    dataset_id: str = ""
    dataset_name: str = ""
    provenance: Dict[str, Any] = Field(default_factory=dict)


class RetrievalPlan(BaseModel):
    """检索规划：轻量 LLM 对"该查什么"的结构化决策（被动召回的驱动计划）。

    由 MemoryRetriever 产出、异步深探消费——放在类型层供双方共享，
    规划逻辑归 retriever，执行归 probe。
    """

    queries: List[str] = Field(default_factory=list)
    """互补检索查询（1-3 条），首条为主查询。"""

    entities: List[str] = Field(default_factory=list)
    """对话中提及的实体名（人名/称呼/项目/话题），用于图谱解析与定向检索。"""

    deep_needed: bool = False
    """是否需要异步深度检索（关系类问题 / 多实体交叉 / 明确回忆过去）。"""

    rationale: str = ""
    """规划依据（审计用，不注入上下文）。"""

    node_keys: List[str] = Field(default_factory=list)
    """entities 解析到的图谱节点 key（运行期产物，非 LLM 输出）。"""

    node_labels: List[str] = Field(default_factory=list)
    """对应节点 label（cognee node_name 定向检索入参）。"""


_WHITESPACE_RE = re.compile(r"\s+")


def normalized_content_key(snippet: str, *, max_chars: int = 120) -> str:
    """跨来源内容去重键：去空白后的前缀（同名事实不同 id 命中时的兜底判定）。

    召回注入格式化与召回账本共用的权威实现。
    """
    return _WHITESPACE_RE.sub("", snippet or "")[:max_chars]
