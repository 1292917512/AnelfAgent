"""FTS 分词器：jieba 词级分词（索引与查询两侧共用同一实现，保证 token 一致）。

背景：unicode61 把连续中文视为单个 token，旧 bigram 查询只能命中标点分隔的
整句子句，中文 FTS 实质失效。本模块将索引与查询统一为 jieba 词级 token：

- 索引侧 `tokenize_for_index`：cut_for_search 细粒度切分（长词与其子词同时
  入索引，兼顾召回率），空格连接后写入 FTS 表；
- 查询侧 `tokenize_for_query`：精确模式 cut（长词保持完整，配合索引侧的
  全词覆盖保证命中）；
- 自定义词典：实体名/图谱节点/高频 topic 标签经 `add_words` 动态注册，
  昵称、梗、专有名词不被切碎。

jieba 导入失败时整体降级为 bigram 切分（与改造前行为一致），降级会显式
记录 WARNING 日志。分词器版本变更（`FTS_TOKENIZER_VERSION`）触发连接层
全量重建 FTS 索引。
"""

from __future__ import annotations

import re
import threading
from typing import Iterable, List

from core.log import log

# 分词器版本：索引模式/词典策略变更时递增，触发 FTS 全量重建
FTS_TOKENIZER_VERSION = "jieba-search-v1"

_jieba = None
_jieba_ready = False
_init_lock = threading.Lock()

# 自定义词典（add_words 注册，初始化时一次性并入 jieba）
_pending_words: set[str] = set()

# 至少含一个 CJK/字母/数字字符的 token 才有索引价值（过滤纯标点）
_USEFUL_TOKEN_RE = re.compile(r"[一-鿿A-Za-z0-9]")

_CJK_RUN_RE = re.compile(r"[一-鿿]+")


def _cjk_bigrams(text: str) -> List[str]:
    """连续中文串的重叠 bigram（兜底召回：jieba 切碎/未登录词也能命中）。

    索引侧与查询侧都附带 bigram，词级 token 由 BM25 的 IDF 天然排前，
    bigram 只在没有词级命中时发挥兜底作用。
    """
    out: List[str] = []
    for run in _CJK_RUN_RE.findall(text):
        out.extend(run[i:i + 2] for i in range(len(run) - 1))
    return out


def _load_jieba():
    """加载 jieba 并应用待注册词典（线程安全，仅首个调用真正执行）。"""
    global _jieba, _jieba_ready
    with _init_lock:
        if _jieba_ready:
            return _jieba
        try:
            import jieba as _mod

            for word in sorted(_pending_words):
                if word:
                    _mod.add_word(word)
            _jieba = _mod
        except ImportError as exc:
            log(
                f"jieba 不可用，FTS 分词降级为 bigram（中文检索质量显著下降，"
                f"请安装依赖）: {exc}",
                "WARNING",
            )
            _jieba = None
        _jieba_ready = True
        return _jieba


def add_words(words: Iterable[str]) -> None:
    """注册自定义词（实体名/图谱节点/高频标签等），初始化前后的调用都生效。"""
    cleaned = {w.strip() for w in words if w and len(w.strip()) >= 2}
    if not cleaned:
        return
    if _jieba_ready:
        mod = _jieba
        if mod is not None:
            for word in sorted(cleaned):
                mod.add_word(word)
        return
    _pending_words.update(cleaned)


def available() -> bool:
    """jieba 是否可用（首次调用触发初始化）。"""
    return _load_jieba() is not None


def _bigram_tokens(text: str) -> List[str]:
    """bigram 降级切分：中文 bigram（跳步=2）+ 英文原词。"""
    tokens: List[str] = []
    for word in text.split():
        cjk = [ch for ch in word if "一" <= ch <= "鿿"]
        if len(cjk) >= 2:
            for i in range(0, len(cjk) - 1, 2):
                tokens.append("".join(cjk[i:i + 2]))
            if len(cjk) > 2 and len(cjk) % 2 == 1:
                tokens.append("".join(cjk[-2:]))
        elif len(word) >= 2:
            tokens.append(word)
    return tokens


def _dedup_keep_order(tokens: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def tokenize_for_index(text: str) -> str:
    """索引侧分词：jieba 细粒度词 + CJK bigram 兜底，空格连接后写入 FTS。"""
    text = (text or "").strip()
    if not text:
        return ""
    mod = _load_jieba()
    if mod is None:
        return " ".join(_dedup_keep_order(_bigram_tokens(text)))
    tokens = [
        t.strip() for t in mod.cut_for_search(text)
        if _USEFUL_TOKEN_RE.search(t)
    ]
    tokens += _cjk_bigrams(text)
    return " ".join(_dedup_keep_order(tokens))


def tokenize_for_query(text: str) -> List[str]:
    """查询侧分词：jieba 双模式词表并集 + CJK bigram 兜底（构建 FTS MATCH 用）。

    单用精确模式时，jieba 可能把复合词进一步切碎（"搬到"单独出现时→搬/到），
    导致长词查询丢召回；取 cut 与 cut_for_search 并集（过滤单字与标点），
    再附加重叠 bigram 兜底未登录词，噪声由 BM25 排序天然抑制。
    """
    text = (text or "").strip()
    if not text:
        return []
    mod = _load_jieba()
    if mod is None:
        return _dedup_keep_order(_bigram_tokens(text))
    tokens = _dedup_keep_order(
        t.strip()
        for t in list(mod.cut(text)) + list(mod.cut_for_search(text))
        if _USEFUL_TOKEN_RE.search(t) and len(t.strip()) >= 2
    )
    tokens = _dedup_keep_order(list(tokens) + _cjk_bigrams(text))
    if not tokens:
        # 查询过短（如单字昵称）：放宽到单字 token，避免 FTS 完全无查询词
        tokens = _dedup_keep_order(
            t.strip() for t in mod.cut(text) if _USEFUL_TOKEN_RE.search(t)
        )
    return tokens
