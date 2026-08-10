"""MemoryStore 共用纯函数助手：评分衰减、行转换、FTS/LIKE 查询构建。

本模块不依赖连接与子模块，仅供 connection / cognee_queue / file_index /
search / memory_store 门面共享，避免代码重复。
"""

from __future__ import annotations

import json
import math
import re
import time
from typing import Any, Dict, Optional

from ..memory_types import MemoryEntry, MemoryType
from ..memory_utils import unpack_embedding

# memories 表显式列名（避免 SELECT * 对顺序的依赖）
MEM_COLUMNS = (
    "id, type, content, source, importance, ts_ns, "
    "metadata_json, embedding_blob, tags_json, access_count, last_accessed_ns, "
    "migrated, version"
)

# 无向量列清单：评分/投影/遗忘扫描等不需要向量的路径使用，
# 避免逐行 unpack_embedding 的无效 IO 与 CPU
MEM_COLUMNS_NO_EMB = (
    "id, type, content, source, importance, ts_ns, "
    "metadata_json, tags_json, access_count, last_accessed_ns, "
    "migrated, version"
)


def get_memory_config_value(field: str, default: Any = None) -> Any:
    """从 MindConfig 安全读取配置值。"""
    try:
        from agent.config import get_config_provider
        return getattr(get_config_provider().mind, field, default)
    except Exception:
        return default


def time_decay(
    ts: float,
    half_life_hours: Optional[float] = None,
    now: Optional[float] = None,
) -> float:
    """基于时间的衰减因子，越新越接近 1（艾宾浩斯式半衰期指数衰减）。"""
    if half_life_hours is None:
        days = float(get_memory_config_value("memory_time_decay_days", 30))
        half_life_hours = max(1.0, days * 24)
    age_hours = ((now or time.time()) - ts) / 3600.0
    return 0.5 ** (max(0.0, age_hours) / half_life_hours)


def idf_tag_score(
    query_tags: list[str],
    memory_tags: list[str],
    tag_df: Dict[str, int],
    total_docs: int,
) -> float:
    """IDF 加权标签匹配分：命中标签的 IDF 和 / 查询标签的 IDF 和（0~1）。

    高频标签（如全局 user: 标签）区分度低、权重小；稀有标签（如某次事件的
    topic:）信息量高、权重大。查询标签不在统计中时按 0 文档计（最高权重）。
    """
    if not query_tags or not memory_tags:
        return 0.0
    mem_set = set(memory_tags)
    total = 0.0
    hits = 0.0
    for tag in query_tags:
        weight = math.log(1.0 + total_docs / (1.0 + tag_df.get(tag, 0)))
        total += weight
        if tag in mem_set:
            hits += weight
    return hits / total if total > 0 else 0.0


def frequency_boost(access_count: int, max_access: int) -> float:
    """访问频率归一化得分。"""
    if max_access <= 0:
        return 0.0
    return math.log(1 + access_count) / math.log(1 + max_access)


_DATED_PATH_RE = re.compile(r"(?:^|/)memory/(?:events/)?(\d{4})-(\d{2})-(\d{2})\.md$")
_HALF_LIFE_DAYS = 30


def file_temporal_decay(path: str) -> float:
    """文件级时间衰减：常青文件不衰减，memory/events/YYYY-MM-DD.md 按日期衰减。"""
    normalized = path.replace("\\", "/").lstrip("./")
    if normalized in ("MEMORY.md", "memory.md"):
        return 1.0
    if normalized.startswith("memory/") and not _DATED_PATH_RE.search(normalized):
        return 1.0
    m = _DATED_PATH_RE.search(normalized)
    if m:
        try:
            from datetime import datetime, timezone
            file_date = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
            age_days = (datetime.now(tz=timezone.utc) - file_date).total_seconds() / 86400
            return 0.5 ** (age_days / _HALF_LIFE_DAYS)
        except (ValueError, OverflowError):
            pass
    return 1.0


def compute_effective_score(entry: MemoryEntry, now: Optional[float] = None) -> float:
    """计算记忆的有效分：importance × 时间衰减 × 访问强化。

    有效分模拟人脑遗忘曲线：重要性是基础，时间推移衰减，
    频繁访问的记忆获得强化抵抗遗忘。permanent 永远返回 1.0（不遗忘）。
    时间衰减项与检索评分共用 time_decay（同一半衰期配置，单一实现）。
    """
    if entry.memory_type == MemoryType.PERMANENT:
        return 1.0
    now = now or time.time()
    decay = time_decay(entry.timestamp, now=now)
    reinforcement = 1.0 + math.log1p(entry.access_count) * 0.15
    return entry.importance * decay * reinforcement


def row_to_entry(row: Any, *, with_embedding: bool = True) -> MemoryEntry:
    """将数据库行转换为 MemoryEntry（按列名访问，不依赖列顺序）。

    with_embedding=False 时跳过向量解包（配合 MEM_COLUMNS_NO_EMB 查询）。
    """
    embedding = None
    if with_embedding:
        embedding = unpack_embedding(row["embedding_blob"]) if row["embedding_blob"] else None
    tags: list[str] = []
    access_count = 0
    last_accessed = 0.0
    try:
        tags = json.loads(row["tags_json"]) if row["tags_json"] else []
    except (json.JSONDecodeError, TypeError, KeyError):
        pass
    try:
        access_count = row["access_count"] or 0
    except (TypeError, KeyError):
        pass
    try:
        last_accessed = (row["last_accessed_ns"] or 0) / 1e9
    except (TypeError, KeyError):
        pass
    version = 1
    try:
        version = int(row["version"] or 1)
    except (TypeError, KeyError, ValueError):
        pass

    return MemoryEntry(
        id=row["id"],
        memory_type=MemoryType(row["type"]),
        content=row["content"],
        source=row["source"],
        importance=row["importance"],
        timestamp=row["ts_ns"] / 1e9,
        metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else {},
        embedding=embedding,
        tags=tags,
        access_count=access_count,
        last_accessed=last_accessed,
        version=version,
    )


def entry_projection_payload(entry: MemoryEntry, memory_id: int) -> Dict[str, Any]:
    """构造 Cognee 投影负载（记忆字段快照）。"""
    return {
        "memory_id": memory_id,
        "type": entry.memory_type.value,
        "content": entry.content,
        "source": entry.source,
        "importance": entry.importance,
        "timestamp": entry.timestamp,
        "metadata": entry.metadata,
        "tags": entry.tags,
    }


def build_fts_query(raw: str) -> Optional[str]:
    """构建 FTS5 查询：分词（jieba，降级 bigram）+ 短语引用，OR 组合。"""
    from .tokenizer import tokenize_for_query

    tokens = tokenize_for_query(raw)
    if not tokens:
        return None
    return " OR ".join(f'"{t}"' for t in tokens)


def escape_like(value: str) -> str:
    """转义 LIKE 模式中的通配符（%/_），防止用户输入被当作模式元字符。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def extract_like_keywords(query: str) -> list[str]:
    """从查询中提取 LIKE 搜索关键词（中文按 2-4 字滑窗，英文按空格分词）。"""
    keywords: list[str] = []
    for word in query.split():
        cjk_chars = [ch for ch in word if '\u4e00' <= ch <= '\u9fff']
        if len(cjk_chars) >= 2:
            # 2-4 字滑窗，优先长片段
            step = 2 if len(cjk_chars) <= 4 else 3
            for i in range(0, len(cjk_chars) - step + 1, step):
                kw = "".join(cjk_chars[i:i + min(step + 1, len(cjk_chars) - i)])
                keywords.append(kw)
        elif len(word) >= 2:
            keywords.append(word)
    return keywords[:10]


def bigram_similarity(a: str, b: str) -> float:
    """bigram Jaccard 相似度（去重判重用）。"""
    if len(a) < 2 or len(b) < 2:
        return 0.0
    bigrams_a = {a[i:i + 2] for i in range(len(a) - 1)}
    bigrams_b = {b[i:i + 2] for i in range(len(b) - 1)}
    intersection = bigrams_a & bigrams_b
    union = bigrams_a | bigrams_b
    return len(intersection) / len(union) if union else 0.0
