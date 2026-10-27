"""标签智能：共现图谱与查询提及识别（记忆联想的驱动层）。

单趟扫描 tags_json + graph_nodes 构建三份派生数据，TTL 到期按标签修订号复核：
- df（标签文档频率，IDF 评分与提及词表共用）
- 共现对（同一条记忆中关联前缀标签两两共现计数）
- 提及词表（高频 topic 名 + 图谱节点 label → 标签的映射）

关联前缀之外的标签（type:/date:/channel: 等结构性标签）不参与共现与提及，
避免"共现于 type:fact"这类无意义关联。

模型可见：仅将无歧义的实体/话题提及作为召回种子，不改显式标签。
Token 影响：不新增注入，减少错误身份和词内误匹配带来的候选。
缓存影响：派生词表在内存复用，召回仍进入动态区，不触碰稳定前缀。
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from typing import Any, Dict, List

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
        self._revision: int = -1
        self._df: Dict[str, int] = {}
        self._total: int = 0
        self._cooc: Dict[tuple[str, str], int] = {}
        self._vocab: Dict[str, str] = {}  # 提及词（小写）→ 标签
        # 重建互斥锁：TTL 刚好过期时并发调用方各自全表重建（cache stampede）
        self._rebuild_lock = asyncio.Lock()

    def _is_assoc_tag(self, tag: str) -> bool:
        return tag.startswith(_COOC_PREFIX_FILTER)

    def _compute(
        self,
        tag_rows: List[str],
        node_rows: List[tuple[str, str]],
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
            unique_tags = {tag for tag in tags if isinstance(tag, str)}
            for tag in sorted(unique_tags):
                df[tag] = df.get(tag, 0) + 1
            assoc = sorted(t for t in unique_tags if self._is_assoc_tag(t))
            for i in range(len(assoc)):
                for j in range(i + 1, len(assoc)):
                    pair = (assoc[i], assoc[j])
                    cooc[pair] = cooc.get(pair, 0) + 1

        # 提及词表：高频 topic 名（df>=2，长度>=2 防噪声）
        mentions: Dict[str, set[str]] = {}
        for tag, count in df.items():
            if tag.startswith("topic:") and count >= 2:
                name = tag[len("topic:") :]
                if len(name) >= 2:
                    mentions.setdefault(name.casefold(), set()).add(tag)
        # 图谱节点 label/key 词表（user/group/topic 节点 → 同名记忆标签）
        for key, label in node_rows:
            if not key.startswith(ASSOC_PREFIXES):
                continue
            label = label.strip()
            if len(label) >= 2:
                mentions.setdefault(label.casefold(), set()).add(key)
            # key 的话题名段也可被提及（如 topic:火锅 的"火锅"）
            name = key.split(":", 1)[1]
            if key.startswith("topic:") and len(name) >= 2:
                mentions.setdefault(name.casefold(), set()).add(key)
        vocab = {word: next(iter(tags)) for word, tags in mentions.items() if len(tags) == 1}
        return df, cooc, vocab

    async def _ensure_fresh(self, *, fresh: bool = False) -> None:
        """召回复用 TTL；写入治理按标签修订号复核，读不到未提交的标签。"""
        # 空表也记录构建时间：无记忆时不至于每次调用都全表重建
        if not fresh and time.monotonic() - self._built_at < _TTL_SECONDS:
            return
        async with self._rebuild_lock:
            # 双检：等待锁期间可能已有并发重建完成
            now = time.monotonic()
            if not fresh and now - self._built_at < _TTL_SECONDS:
                return
            db = await self._conn.get_db()
            async with self._conn.write_lock:
                cursor = await db.execute("SELECT value FROM schema_meta WHERE key='tag_revision'")
                row = await cursor.fetchone()
                revision = int(row["value"]) if row else -1
                if revision >= 0 and revision == self._revision:
                    self._built_at = now
                    return
                cursor = await db.execute("SELECT tags_json FROM memories WHERE importance > 0")
                tag_rows = [str(r["tags_json"]) for r in await cursor.fetchall()]
                cursor = await db.execute("SELECT node_key, label FROM graph_nodes WHERE archived=0")
                node_rows = [(str(r["node_key"]), str(r["label"] or "")) for r in await cursor.fetchall()]

            # 逐行 JSON 解析 + 共现组合在 worker 线程执行，避免阻塞事件循环
            df, cooc, vocab = await asyncio.to_thread(self._compute, tag_rows, node_rows)
            self._df = df
            self._cooc = cooc
            self._vocab = vocab
            self._total = len(tag_rows)
            self._built_at = now
            self._revision = revision

    async def tag_stats(self, *, fresh: bool = False) -> tuple[Dict[str, int], int]:
        """(标签文档频率, 活跃记忆总数)。"""
        await self._ensure_fresh(fresh=fresh)
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
        scores = {tag: (count / self._df.get(tag, 1)) * self._idf(tag) for tag, count in cooc_count.items()}
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        return ranked[:limit]

    async def extract_mentions(self, query: str, *, limit: int = 3) -> List[str]:
        """从查询文本识别已知实体/话题提及，返回对应标签（关联前缀）。

        按词长降序匹配（长词优先，避免「火锅城」先被「火锅」截胡），
        同一标签只取一次；同名歧义不推断身份，拉丁词须匹配完整词。
        话题来自 df>=2 的标签，实体来自未归档图谱节点。
        """
        text = (query or "").casefold()
        if not text or limit <= 0:
            return []
        await self._ensure_fresh()
        hits: List[str] = []
        for word in sorted(self._vocab, key=lambda value: (-len(value), value)):
            if word in text and (
                not word.isascii() or re.search(rf"(?<![a-z0-9_]){re.escape(word)}(?![a-z0-9_])", text)
            ):
                tag = self._vocab[word]
                if tag not in hits:
                    hits.append(tag)
                if len(hits) >= limit:
                    break
        return hits

    async def merge_candidates(self, *, limit: int | None = 20) -> List[Dict[str, Any]]:
        """标签归并候选（确定性事实生产；归并决策归 AI，经 update_memory 执行）。

        两类高置信候选，宁缺毋滥（kind 供治理豁免签名用）：
        - form_variant 写法变体：NFKC/小写/空白归一后相同的 topic:/goal:
          标签组（如全角字符或多余空格产生的孪生标签），归并到高频形态；
        - containment 包含关系对：topic: 名互为子串且双方 df>=2（与写入
          闸门的包含提示同口径，作用于存量），归并到高频一方。
        """
        if limit is not None and limit <= 0:
            return []
        await self._ensure_fresh(fresh=True)
        out: List[Dict[str, Any]] = []
        seen: set[frozenset[str]] = set()

        groups: Dict[str, List[str]] = {}
        for tag in self._df:
            if tag.startswith(("topic:", "goal:")):
                groups.setdefault(normalize_tag_form(tag), []).append(tag)
        for tags in groups.values():
            if len(tags) < 2:
                continue
            canonical = max(tags, key=lambda t: self._df.get(t, 0))
            for t in tags:
                if t == canonical:
                    continue
                key = frozenset((t, canonical))
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    {
                        "kind": "form_variant",
                        "from": t,
                        "into": canonical,
                        "reason": "写法变体",
                        "from_count": self._df.get(t, 0),
                        "into_count": self._df.get(canonical, 0),
                    }
                )

        topics = sorted(
            (t for t in self._df if t.startswith("topic:") and self._df.get(t, 0) >= 2),
            key=lambda t: -self._df.get(t, 0),
        )
        for i, a in enumerate(topics):
            na = a[len("topic:") :]
            for b in topics[i + 1 :]:
                nb = b[len("topic:") :]
                if not na or not nb or (na not in nb and nb not in na):
                    continue
                into, from_ = (a, b) if self._df.get(a, 0) >= self._df.get(b, 0) else (b, a)
                key = frozenset((from_, into))
                if key in seen or from_ == into:
                    continue
                seen.add(key)
                out.append(
                    {
                        "kind": "containment",
                        "from": from_,
                        "into": into,
                        "reason": "名称包含关系",
                        "from_count": self._df.get(from_, 0),
                        "into_count": self._df.get(into, 0),
                    }
                )
        out.sort(key=lambda c: -(c["from_count"] + c["into_count"]))
        return out[:limit]


def normalize_tag_form(tag: str, *, preserve_word_boundaries: bool = False) -> str:
    """形态归一；写入保留词间空格，治理候选及持久豁免签名沿用去空白口径。"""
    import re
    import unicodedata

    normalized = unicodedata.normalize("NFKC", tag).casefold()
    if preserve_word_boundaries:
        return re.sub(r"\s+", " ", normalized).strip()
    return re.sub(r"\s+", "", normalized)
