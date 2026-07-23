"""记忆整理器 — 心跳时的自动记忆维护（人脑"睡眠整理"）。

每次心跳维护周期执行：
1. 遗忘：清理低有效分的非永久记忆（importance × 时间衰减 × 访问强化）
2. 上限：每类记忆超限时删除最低分条目
3. 合并：向量相似度 > 阈值的高相似记忆自动合并
4. 清理：过期 embedding 缓存
5. 归档：物理删除超过保留期的归档记忆（防归档表无限增长）
6. 同步：cognee 队列积压检查与唤醒

全部确定性操作（无 LLM 调用），报告写入心跳日志。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.config import get_config_bool, get_config_float, get_config_int, register_configs_safe
from core.log import log

from .memory_store import MemoryStore


@dataclass
class ConsolidationReport:
    """一次记忆整理的执行报告。"""

    forgotten_count: int = 0
    forgotten_previews: List[str] = field(default_factory=list)
    limit_removed: Dict[str, int] = field(default_factory=dict)
    merged_count: int = 0
    cache_cleaned: int = 0
    archive_purged: int = 0
    cognee_pending: int = 0
    errors: List[str] = field(default_factory=list)

    def to_log_lines(self) -> List[str]:
        """格式化为心跳日志行。"""
        lines: List[str] = []
        if self.forgotten_count:
            lines.append(f"遗忘 {self.forgotten_count} 条低价值记忆")
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

        # 2. 类型上限强制
        try:
            report.limit_removed = await self._store.enforce_type_limits()
        except Exception as exc:
            report.errors.append(f"上限清理失败: {exc}")
            log(f"记忆上限清理失败: {exc}", "WARNING", tag="记忆")

        # 3. 高相似记忆自动合并
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

        # 4. embedding 缓存清理
        try:
            report.cache_cleaned = await self._store.clean_embedding_cache()
        except Exception as exc:
            report.errors.append(f"缓存清理失败: {exc}")

        # 5. 超期归档物理删除（0 = 永久保留归档）
        try:
            report.archive_purged = await self._store.purge_archived_memories(
                get_config_int("memory_archive_retention_days", 90),
            )
        except Exception as exc:
            report.errors.append(f"归档清理失败: {exc}")

        # 6. cognee 同步队列检查与唤醒
        try:
            report.cognee_pending = await self._check_cognee_sync()
        except Exception as exc:
            report.errors.append(f"cognee 检查失败: {exc}")

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
        "memory_merge_similarity": {
            "description": "高相似记忆自动合并的向量相似度阈值",
            "default": 0.92,
        },
        "memory_archive_retention_days": {
            "description": "归档记忆保留天数（超期物理删除，0 = 永久保留）",
            "default": 90,
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
