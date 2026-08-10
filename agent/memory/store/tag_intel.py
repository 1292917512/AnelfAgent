"""标签智能：共现图谱与查询提及识别（记忆联想的驱动层）。

单趟扫描 tags_json + graph_nodes 构建三份派生数据，TTL 缓存：
- df（标签文档频率，IDF 评分与提及词表共用）
- 共现对（同一条记忆中关联前缀标签两两共现计数）
- 提及词表（高频 topic 名 + 图谱节点 label → 标签的映射）

关联前缀之外的标签（type:/date:/channel: 等结构性标签）不参与共现与提及，
避免"共现于 type:fact"这类无意义关联。
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from typing import Dict, List

from .connection import MemoryConnectionManager

# 参与联想网络的标签前缀（与 recall 联想消费端一致）
ASSOC_PREFIXES = ("user:", "group:", "topic:", "goal:")

# 实体标签前缀（ASSOC_PREFIXES 子集，图谱邻居/scope 加权用）
ENTITY_PREFIXES = ("user:", "group:")

_TTL_SECONDS = 60.0
_COOC_PREFIX_FILTER = ASSOC_PREFIXES


class TagIntelligence:
    """标签共现图谱 + 查询提及识别（缓存的派生视图，写入侧无维护成本）。"""

    def __init__(self, conn: MemoryConnectionManager) -> None:
        self._conn = conn
        self._built_at: float = 0.0
        self._df: Dict[str, int] = {}
        self._total: int = 0
        self._cooc: Dict[tuple[str, str], int] = {}
        self._vocab: Dict[str, str] = {}  # 提及词（小写）→ 标签
        # 重建互斥锁：TTL 刚好过期时并发调用方各自全表重建（cache stampede）
        self._rebuild_lock = asyncio.Lock()

    def _is_assoc_tag(self, tag: str) -> bool:
        return tag.startswith(_COOC_PREFIX_FILTER)

    def _compute(
        self, tag_rows: List[str], node_rows: List[tuple[str, str]],
    ) -> tuple[Dict[str, int], Dict[tuple[str, str], int], Dict[str, str]]:
        """纯计算部分（worker 线程执行）：df + 共现对 + 提及词表。"""
        df: Dict[str, int] = {}
        cooc: Dict[tuple[str, str], int] = {}
        for tags_json in tag_rows:
            try:
                tags = json.loads(tags_json) if tags_json else []
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(tags, list):
                continue
            for tag in tags:
                if isinstance(tag, str):
                    df[tag] = df.get(tag, 0) + 1
            assoc = sorted({t for t in tags if isinstance(t, str) and self._is_assoc_tag(t)})
            for i in range(len(assoc)):
                for j in range(i + 1, len(assoc)):
                    pair = (assoc[i], assoc[j])
                    cooc[pair] = cooc.get(pair, 0) + 1

        # 提及词表：高频 topic 名（df>=2，长度>=2 防噪声）
        vocab: Dict[str, str] = {}
        for tag, count in df.items():
            if tag.startswith("topic:") and count >= 2:
                name = tag[len("topic:"):]
                if len(name) >= 2:
                    vocab[name.casefold()] = tag
        # 图谱节点 label/key 词表（user/group/topic 节点 → 同名记忆标签）
        for key, label in node_rows:
            if not key.startswith(ASSOC_PREFIXES):
                continue
            label = label.strip()
            if len(label) >= 2:
                vocab.setdefault(label.casefold(), key)
            # key 的话题名段也可被提及（如 topic:火锅 的"火锅"）
            name = key.split(":", 1)[1]
            if key.startswith("topic:") and len(name) >= 2:
                vocab.setdefault(name.casefold(), key)
        return df, cooc, vocab

    async def _ensure_fresh(self) -> None:
        # 空表也记录构建时间：无记忆时不至于每次调用都全表重建
        if time.monotonic() - self._built_at < _TTL_SECONDS:
            return
        async with self._rebuild_lock:
            # 双检：等待锁期间可能已有并发重建完成
            now = time.monotonic()
            if now - self._built_at < _TTL_SECONDS:
                return
            db = await self._conn.get_db()
            cursor = await db.execute(
                "SELECT tags_json FROM memories WHERE importance > 0"
            )
            tag_rows = [str(r["tags_json"]) for r in await cursor.fetchall()]
            try:
                cursor = await db.execute(
                    "SELECT node_key, label FROM graph_nodes WHERE archived=0"
                )
                node_rows = [
                    (str(r["node_key"]), str(r["label"] or ""))
                    for r in await cursor.fetchall()
                ]
            except Exception:
                node_rows = []  # graph_nodes 表尚未创建（旧库）时仅用词表

            # 逐行 JSON 解析 + 共现组合在 worker 线程执行，避免阻塞事件循环
            df, cooc, vocab = await asyncio.to_thread(self._compute, tag_rows, node_rows)
            self._df = df
            self._cooc = cooc
            self._vocab = vocab
            self._total = len(tag_rows)
            self._built_at = now

    async def tag_stats(self) -> tuple[Dict[str, int], int]:
        """(标签文档频率, 活跃记忆总数)。"""
        await self._ensure_fresh()
        return self._df, self._total

    def _idf(self, tag: str) -> float:
        return math.log(1.0 + self._total / (1.0 + self._df.get(tag, 0)))

    async def cooccurring_tags(
        self,
        seed_tags: List[str],
        *,
        limit: int = 3,
    ) -> List[tuple[str, float]]:
        """共现联想：与种子标签同现过的其他关联标签。

        评分 = 共现占比（cooc/df，该标签有多"专属于"种子的语境）× IDF——
        全局高频标签（当前用户 user: 等）天然稀释，稀有且高度专属于种子
        语境的标签（某次具体事件）排名最高。
        """
        if not seed_tags:
            return []
        await self._ensure_fresh()
        seeds = set(seed_tags)
        cooc_count: Dict[str, int] = {}
        for (a, b), count in self._cooc.items():
            if a in seeds and b not in seeds:
                cooc_count[b] = cooc_count.get(b, 0) + count
            elif b in seeds and a not in seeds:
                cooc_count[a] = cooc_count.get(a, 0) + count
        scores = {
            tag: (count / self._df.get(tag, 1)) * self._idf(tag)
            for tag, count in cooc_count.items()
        }
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        return ranked[:limit]

    async def extract_mentions(self, query: str, *, limit: int = 3) -> List[str]:
        """从查询文本识别已知实体/话题提及，返回对应标签（关联前缀）。

        按词长降序匹配（长词优先，避免「火锅城」先被「火锅」截胡），
        同一标签只取一次。仅产出 df>=2 的高频标签，控制噪声。
        """
        text = (query or "").casefold()
        if not text:
            return []
        await self._ensure_fresh()
        hits: List[str] = []
        for word in sorted(self._vocab, key=len, reverse=True):
            if word in text:
                tag = self._vocab[word]
                if tag not in hits:
                    hits.append(tag)
                if len(hits) >= limit:
                    break
        return hits
