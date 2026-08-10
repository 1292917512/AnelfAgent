"""记忆整理器 — 心跳时的自动记忆维护（人脑"睡眠整理"）。

每次心跳维护周期执行：
1. 遗忘：清理低有效分的非永久记忆（importance × 时间衰减 × 访问强化）
2. 松弛：长期未访问记忆的 importance 向基线回归（对称化召回强化，防趋同）
3. 上限：每类记忆超限时归档最低分条目
4. 合并：向量相似度 > 阈值的高相似记忆自动合并
5. 清理：过期 embedding 缓存
6. 归档：物理删除超过保留期的归档记忆（防归档表无限增长）
7. 同步：cognee 队列积压检查与唤醒

全部确定性操作（无 LLM 调用），报告写入心跳日志。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from core.config import get_config_bool, get_config_float, get_config_int, register_configs_safe
from core.log import log

from .memory_store import MemoryStore


@dataclass
class ConsolidationReport:
    """一次记忆整理的执行报告。"""

    forgotten_count: int = 0
    forgotten_previews: List[str] = field(default_factory=list)
    relaxed_count: int = 0
    limit_removed: Dict[str, int] = field(default_factory=dict)
    merged_count: int = 0
    cache_cleaned: int = 0
    archive_purged: int = 0
    cognee_pending: int = 0
    vocab_refreshed: int = 0
    errors: List[str] = field(default_factory=list)

    def to_log_lines(self) -> List[str]:
        """格式化为心跳日志行。"""
        lines: List[str] = []
        if self.forgotten_count:
            lines.append(f"遗忘 {self.forgotten_count} 条低价值记忆")
        if self.relaxed_count:
            lines.append(f"重要性松弛 {self.relaxed_count} 条（向基线回归）")
        if self.limit_removed:
            detail = ", ".join(f"{t}:{n}" for t, n in self.limit_removed.items())
            lines.append(f"类型上限清理 {detail}")
        if self.merged_count:
            lines.append(f"合并 {self.merged_count} 对高相似记忆")
        if self.cache_cleaned:
            lines.append(f"清理 {self.cache_cleaned} 条过期 embedding 缓存")
        if self.archive_purged:
            lines.append(f"物理删除 {self.archive_purged} 条超期归档记忆")
        if self.cognee_pending:
            lines.append(f"cognee 同步积压 {self.cognee_pending} 条（已唤醒）")
        if self.vocab_refreshed:
            lines.append(f"FTS 词典刷新 {self.vocab_refreshed} 词")
        if self.errors:
            lines.append(f"异常 {len(self.errors)} 项: {'; '.join(self.errors[:3])}")
        return lines


class MemoryConsolidator:
    """记忆整理器：心跳维护时执行遗忘/上限/合并/清理/同步。"""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def consolidate(self) -> ConsolidationReport:
        """执行一轮完整记忆整理（幂等，可安全重复调用）。"""
        report = ConsolidationReport()

        if not get_config_bool("memory_forget_enabled", True):
            return report

        # 1. 遗忘低价值记忆
        try:
            forget_result = await self._store.forget_weak_memories(
                min_age_days=get_config_int("memory_forget_min_age_days", 30),
                score_threshold=get_config_float("memory_forget_score_threshold", 0.08),
            )
            report.forgotten_count = forget_result["count"]
            report.forgotten_previews = [
                f"[{f['type']}] {f['preview']}" for f in forget_result["forgotten"][:5]
            ]
        except Exception as exc:
            report.errors.append(f"遗忘执行失败: {exc}")
            log(f"记忆遗忘执行失败: {exc}", "WARNING", tag="记忆")

        # 2. 重要性松弛（对称化 record_access 的单向强化，防止趋同 1.0）
        try:
            report.relaxed_count = await self._store.relax_importance(
                stale_days=get_config_int("memory_importance_relax_days", 14),
                rate=get_config_float("memory_importance_relax_rate", 0.05),
            )
        except Exception as exc:
            report.errors.append(f"重要性松弛失败: {exc}")
            log(f"记忆重要性松弛失败: {exc}", "WARNING", tag="记忆")

        # 3. 类型上限强制
        try:
            report.limit_removed = await self._store.enforce_type_limits()
        except Exception as exc:
            report.errors.append(f"上限清理失败: {exc}")
            log(f"记忆上限清理失败: {exc}", "WARNING", tag="记忆")

        # 4. 高相似记忆自动合并
        try:
            threshold = get_config_float("memory_merge_similarity", 0.92)
            pairs = await self._store.find_similar_memories(threshold)
            for a, b, sim in pairs:
                # 保留有效分较高者，合并另一条的 tags/访问次数
                keep, drop = (a, b) if (
                    self._store.compute_effective_score(a)
                    >= self._store.compute_effective_score(b)
                ) else (b, a)
                if keep.id is not None and drop.id is not None:
                    if await self._store.merge_pair(keep.id, drop.id):
                        report.merged_count += 1
                        log(f"记忆合并: #{drop.id} -> #{keep.id} (相似度 {sim:.2f})", "DEBUG", tag="记忆")
        except Exception as exc:
            report.errors.append(f"相似合并失败: {exc}")
            log(f"记忆相似合并失败: {exc}", "WARNING", tag="记忆")

        # 5. embedding 缓存清理
        try:
            report.cache_cleaned = await self._store.clean_embedding_cache()
        except Exception as exc:
            report.errors.append(f"缓存清理失败: {exc}")

        # 6. 超期归档物理删除（0 = 永久保留归档）
        try:
            report.archive_purged = await self._store.purge_archived_memories(
                get_config_int("memory_archive_retention_days", 90),
            )
        except Exception as exc:
            report.errors.append(f"归档清理失败: {exc}")

        # 6.5 审计日志保留期清理（表只追加，不清理会随运行时间线性膨胀）
        try:
            await self._store.purge_audit_log(
                get_config_int("memory_audit_retention_days", 30),
            )
        except Exception as exc:
            report.errors.append(f"审计日志清理失败: {exc}")

        # 7. cognee 同步队列检查与唤醒
        try:
            report.cognee_pending = await self._check_cognee_sync()
        except Exception as exc:
            report.errors.append(f"cognee 检查失败: {exc}")

        # 8. jieba 自定义词典刷新：图谱节点称呼 + 高频标签喂给分词器，
        #    新实体/新话题的专名不被切碎（作用于后续写入与查询两侧）
        try:
            report.vocab_refreshed = await self._refresh_fts_vocab()
        except Exception as exc:
            report.errors.append(f"词典刷新失败: {exc}")

        if report.forgotten_count or report.merged_count or report.limit_removed:
            log(
                f"记忆整理完成: 遗忘 {report.forgotten_count}, 合并 {report.merged_count}, "
                f"上限清理 {sum(report.limit_removed.values())}",
                tag="记忆",
            )
        return report

    @staticmethod
    async def _check_cognee_sync() -> int:
        """检查 cognee 同步队列积压并唤醒 coordinator，返回积压数。"""
        try:
            from .cognee.runtime import get_cognee_coordinator
            coordinator = get_cognee_coordinator()
            if coordinator is None:
                return 0
            status = await coordinator.status()
            pending = getattr(status, "pending", 0) or 0
            if pending > 0:
                coordinator.wake()
                log(f"cognee 同步积压 {pending} 条，已唤醒处理", "DEBUG", tag="记忆")
            return pending
        except Exception:
            return 0

    async def _refresh_fts_vocab(self) -> int:
        """刷新 FTS 自定义词典（图谱节点称呼 + 高频标签词），返回词条数。"""
        from .store.tokenizer import add_words

        words: set[str] = set()
        try:
            tag_df = await self._store.list_tags()
            for tag, count in tag_df.items():
                if count >= 2 and ":" in tag:
                    value = tag.split(":", 1)[1].strip()
                    if len(value) >= 2:
                        words.add(value)
        except Exception:
            pass
        try:
            db = await self._store._get_db()
            cursor = await db.execute(
                "SELECT label FROM graph_nodes WHERE label != '' AND archived = 0"
            )
            words.update(
                str(r["label"]).strip() for r in await cursor.fetchall()
                if len(str(r["label"]).strip()) >= 2
            )
        except Exception:
            pass
        if words:
            add_words(words)
        return len(words)


# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

_CONSOLIDATOR_CONFIGS = {
    "记忆": {
        "memory_forget_enabled": {
            "description": "是否启用自动遗忘（心跳时清理低价值记忆）",
            "default": True,
        },
        "memory_consolidate_every_n_ticks": {
            "description": "记忆整理执行间隔（每 N 次心跳执行一次全量整理）",
            "default": 12,
        },
        "memory_forget_min_age_days": {
            "description": "记忆最小保留天数（早于此年龄的记忆不遗忘）",
            "default": 30,
        },
        "memory_forget_score_threshold": {
            "description": "遗忘有效分阈值（低于此分且超过最小年龄的记忆被清理）",
            "default": 0.08,
        },
        "memory_forget_min_keep_per_type": {
            "description": "遗忘最小保留：每类活跃记忆低于该数量后不再遗忘（护栏）",
            "default": 20,
        },
        "memory_importance_relax_days": {
            "description": "重要性松弛：超过 N 天未被访问的记忆 importance 开始向基线 0.5 回归",
            "default": 14,
        },
        "memory_importance_relax_rate": {
            "description": "重要性松弛速率（每次整理向基线回归的比例，0-1，0 = 关闭）",
            "default": 0.05,
        },
        "memory_merge_similarity": {
            "description": "高相似记忆自动合并的向量相似度阈值",
            "default": 0.92,
        },
        "memory_archive_retention_days": {
            "description": "归档记忆保留天数（超期物理删除，0 = 永久保留）",
            "default": 90,
        },
        "memory_recall_timeout_seconds": {
            "description": "被动召回检索整体超时（秒），超时回退近期记忆不阻塞对话",
            "default": 5.0,
        },
        "memory_recall_permanent_pin": {
            "description": "永久记忆置顶注入条数（0 = 关闭；主人教导/规则类每轮固定注入）",
            "default": 3,
        },
        "memory_query_rewrite_enabled": {
            "description": "被动召回前是否用轻量 LLM 改写检索查询（口语上下文→检索友好形式）",
            "default": True,
        },
        "memory_recall_skip_trivial": {
            "description": "平凡消息（≤6 字符的纯客套/短回复）跳过记忆检索与查询改写",
            "default": True,
        },
        "notes_inject_max_chars": {
            "description": "主便签注入上下文的最大字符数（超出按章节优先级裁剪）",
            "default": 6000,
        },
        "cognee_sync_stale_seconds": {
            "description": "Cognee 投影任务卡死判定时长（秒），超过后自动重新入队",
            "default": 900.0,
        },
    },
}

register_configs_safe(_CONSOLIDATOR_CONFIGS)
