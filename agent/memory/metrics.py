"""记忆系统运行指标：进程内累计计数器，供心跳 memory-status 区块展示。

设计目标：写入路径零成本（字典自增）、单事件循环内天然线程安全、
无外部依赖。计数自进程启动累计，重启归零——用于观察趋势而非精确审计。
"""

from __future__ import annotations

import time
from typing import Dict

_counters: Dict[str, int] = {}
_started_at: float = time.time()


def incr(key: str, n: int = 1) -> None:
    """计数器自增（key 用 "." 分层，如 recall.fts_hits）。"""
    _counters[key] = _counters.get(key, 0) + n


def snapshot() -> Dict[str, int]:
    return dict(_counters)


def render_status_lines() -> list[str]:
    """渲染为 memory-status 区块的指标行（无数据时不产生行）。"""
    if not _counters:
        return []
    lines: list[str] = []

    requests = _counters.get("recall.requests", 0)
    if requests:
        vec = _counters.get("recall.vec_hits", 0)
        fts = _counters.get("recall.fts_hits", 0)
        tag = _counters.get("recall.tag_hits", 0)
        fts_empty = _counters.get("recall.fts_empty", 0)
        latency_total = _counters.get("recall.latency_ms_total", 0)
        latency_avg = latency_total // max(1, requests)
        lines.append(
            f"- 召回通道（本次启动累计 {requests} 轮）："
            f"向量命中 {vec} · 关键词命中 {fts} · 标签命中 {tag} · "
            f"关键词空结果 {fts_empty} 次 · 平均耗时 {latency_avg}ms"
        )

    rule_skip = _counters.get("write.dedup_rule_skip", 0)
    llm_store = _counters.get("write.dedup_llm_store", 0)
    llm_skip = _counters.get("write.dedup_llm_skip", 0)
    llm_update = _counters.get("write.dedup_llm_update", 0)
    llm_merge = _counters.get("write.dedup_llm_merge", 0)
    if rule_skip or llm_store or llm_skip or llm_update or llm_merge:
        parts = []
        if rule_skip:
            parts.append(f"规则拦截 {rule_skip}")
        if llm_store or llm_skip or llm_update or llm_merge:
            parts.append(
                f"LLM 判定 存 {llm_store}/跳 {llm_skip}/改 {llm_update}/并 {llm_merge}"
            )
        lines.append(f"- 写入去重：{' · '.join(parts)}")

    batches = _counters.get("capture.batches", 0)
    if batches:
        extracted = _counters.get("capture.extracted", 0)
        skipped = _counters.get("capture.skipped_low_quality", 0)
        failed = _counters.get("capture.failed", 0)
        relations = _counters.get("capture.relations", 0)
        rel_part = f" · 图谱关系 +{relations}" if relations else ""
        lines.append(
            f"- 自动捕获：{batches} 批 → 提取 {extracted} 条"
            f"（低质跳过 {skipped} · 失败 {failed}）{rel_part}"
        )
    return lines
