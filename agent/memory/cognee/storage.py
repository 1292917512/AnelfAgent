"""Cognee 物理存储维护：LanceDB 版本压缩与存储占用统计。

两个职责共享同一条目录遍历设施，收敛在一个模块：

- ``compact_lance_tree``：cognee 的删除/更新只在 Lance 追加 tombstone 新版本，
  历史版本物理数据永不回收（磁盘单调膨胀的根因）。本函数遍历
  ``system/databases/**/*.lance.db`` 逐表 ``optimize(cleanup_older_than)``
  （碎片合并 + 索引优化 + 清理过期版本；最新版本永远保留，逻辑数据零影响）。
- ``StorageStatsTracker``：占用统计（向量/图/元数据/原始文档归类）。
  大库遍历可达数十秒，故请求路径永不遍历——内存 TTL 缓存 → 磁盘快照
  （重启即恢复上次真实值）→ 空统计 三级返回，过期仅调度后台单任务刷新。

并发纪律：所有缓存写入携带单调代际号，``invalidate``/``adopt``/新刷新
都会使在途遍历的过期结果被丢弃（防压缩后数字被旧遍历回写）。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Optional, TypedDict

from core.log import log

STORAGE_STATS_SNAPSHOT_NAME = "storage_stats.json"
STORAGE_STATS_TTL_SECONDS = 600.0


class StorageStatsDict(TypedDict):
    total_bytes: int
    data_bytes: int
    lance_bytes: int
    graph_bytes: int
    metadata_bytes: int
    other_bytes: int


def _empty_stats() -> StorageStatsDict:
    return StorageStatsDict(
        total_bytes=0, data_bytes=0, lance_bytes=0,
        graph_bytes=0, metadata_bytes=0, other_bytes=0,
    )


def compute_storage_stats(root: Path) -> StorageStatsDict:
    """单次遍历统计 cognee 数据目录构成；大库耗时可达数十秒，仅限后台线程调用。"""
    stats = _empty_stats()
    if not root.is_dir():
        return stats
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            full = Path(dirpath) / name
            try:
                size = full.lstat().st_size
            except OSError:
                continue
            stats["total_bytes"] += size
            rel = full.relative_to(root).parts
            if rel[0] == "data":
                stats["data_bytes"] += size
            elif len(rel) >= 3 and rel[0] == "system" and rel[1] == "databases":
                if any(part.endswith(".lance.db") for part in rel):
                    stats["lance_bytes"] += size
                elif name.startswith("cognee_db"):
                    stats["metadata_bytes"] += size
                elif name.endswith((".lbug", ".wal")):
                    stats["graph_bytes"] += size
                else:
                    stats["other_bytes"] += size
            else:
                stats["other_bytes"] += size
    return stats


def compact_lance_tree(databases_root: Path, retention_days: float) -> dict[str, Any]:
    """遍历 LanceDB 库目录逐表 optimize（同步实现，由调用方放入线程执行）。

    单表失败仅记录不中断其余表；须在无并发写入时调用（协调器空闲窗口）。
    返回的 after_stats 是压缩后的实测占用，调用方应直接收录进统计缓存
    （``StorageStatsTracker.adopt``），避免紧接一次重复遍历。
    """
    from datetime import timedelta

    import lancedb

    result: dict[str, Any] = {
        "databases": 0,
        "tables": 0,
        "bytes_before": 0,
        "bytes_after": 0,
        "bytes_reclaimed": 0,
        "errors": [],
        "after_stats": _empty_stats(),
    }
    if not databases_root.is_dir():
        return result
    db_dirs = sorted(databases_root.glob("**/*.lance.db"))
    if not db_dirs:
        return result
    stats_root = databases_root.parent.parent
    result["bytes_before"] = compute_storage_stats(stats_root)["total_bytes"]
    older_than = timedelta(days=max(0.0, float(retention_days)))
    for index, db_dir in enumerate(db_dirs, 1):
        log(
            f"Cognee LanceDB 压缩进度: {index}/{len(db_dirs)} {db_dir.name}",
            "DEBUG",
            tag="记忆",
        )
        result["databases"] += 1
        try:
            connection = lancedb.connect(str(db_dir))
            table_names = list(connection.list_tables().tables)
        except Exception as exc:
            result["errors"].append(f"{db_dir.name}: 打开失败 {exc}")
            continue
        for table_name in table_names:
            try:
                connection.open_table(table_name).optimize(cleanup_older_than=older_than)
                result["tables"] += 1
            except Exception as exc:
                result["errors"].append(f"{db_dir.name}/{table_name}: {exc}")
    after_stats = compute_storage_stats(stats_root)
    result["after_stats"] = after_stats
    result["bytes_after"] = after_stats["total_bytes"]
    result["bytes_reclaimed"] = max(0, result["bytes_before"] - result["bytes_after"])
    return result


class StorageStatsTracker:
    """存储统计的缓存/快照/后台刷新调度（全部状态收拢在本实例内）。"""

    def __init__(self, ttl_seconds: float = STORAGE_STATS_TTL_SECONDS) -> None:
        self._ttl = max(0.0, float(ttl_seconds))
        self._cache: dict[str, tuple[float, StorageStatsDict]] = {}
        self._refreshing: set[str] = set()
        self._generation: dict[str, int] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    async def get(self, data_root: str) -> StorageStatsDict:
        """返回最近已知的占用统计（可能是上周期的值），并保证后台刷新在途。"""
        cached = self._cache.get(data_root)
        if cached and time.monotonic() - cached[0] < self._ttl:
            return StorageStatsDict(**cached[1])
        if cached is None:
            snapshot = self._load_snapshot(data_root)
            if snapshot is not None:
                # 时间戳置 0 视为过期：返回快照值的同时仍会调度后台刷新
                cached = (0.0, snapshot)
                self._cache[data_root] = cached
        self.schedule_refresh(data_root)
        return StorageStatsDict(**cached[1]) if cached else _empty_stats()

    def schedule_refresh(self, data_root: str) -> None:
        """调度一次后台遍历刷新（同路径去重；无运行循环时静默跳过）。"""
        if data_root in self._refreshing:
            return
        self._refreshing.add(data_root)
        generation = self._bump_generation(data_root)

        async def refresh() -> None:
            try:
                stats = await asyncio.to_thread(compute_storage_stats, Path(data_root))
                # 在途期间发生 invalidate/adopt/新刷新 → 本结果已过期，丢弃
                if self._generation.get(data_root) != generation:
                    return
                self._cache[data_root] = (time.monotonic(), stats)
                await asyncio.to_thread(self._save_snapshot, data_root, stats)
            except Exception as exc:
                log(f"cognee 存储统计刷新失败: {exc}", "DEBUG", tag="记忆")
            finally:
                self._refreshing.discard(data_root)

        try:
            task = asyncio.create_task(refresh())
        except RuntimeError as exc:
            self._refreshing.discard(data_root)
            log(f"cognee 存储统计刷新调度失败: {exc}", "DEBUG", tag="记忆")
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def adopt(self, data_root: str, stats: StorageStatsDict) -> None:
        """收录维护动作（如压缩）实测的最新占用，免一次重复遍历。"""
        self._bump_generation(data_root)
        snapshot = StorageStatsDict(**stats)
        self._cache[data_root] = (time.monotonic(), snapshot)

        async def save() -> None:
            await asyncio.to_thread(self._save_snapshot, data_root, snapshot)

        try:
            task = asyncio.create_task(save())
        except RuntimeError as exc:
            log(f"cognee 存储统计快照保存调度失败: {exc}", "DEBUG", tag="记忆")
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def invalidate(self, data_root: Optional[str] = None) -> None:
        """物理占用已失效（清场/重建）：清缓存并删除磁盘快照防回读旧值。"""
        if data_root is None:
            for root in list(self._cache):
                self._bump_generation(root)
            self._cache.clear()
            return
        self._bump_generation(data_root)
        self._cache.pop(data_root, None)
        try:
            self._snapshot_path(data_root).unlink(missing_ok=True)
        except OSError:
            pass

    def _bump_generation(self, data_root: str) -> int:
        generation = self._generation.get(data_root, 0) + 1
        self._generation[data_root] = generation
        return generation

    @staticmethod
    def _snapshot_path(data_root: str) -> Path:
        return Path(data_root) / STORAGE_STATS_SNAPSHOT_NAME

    def _load_snapshot(self, data_root: str) -> Optional[StorageStatsDict]:
        try:
            raw = json.loads(
                self._snapshot_path(data_root).read_text(encoding="utf-8"),
            )
            stats = raw.get("stats")
            if not isinstance(stats, dict):
                return None
            merged = _empty_stats()
            for key in merged:
                value = stats.get(key)
                if isinstance(value, int) and value >= 0:
                    merged[key] = value  # type: ignore[literal-required]
            return merged
        except (OSError, ValueError, AttributeError):
            return None

    def _save_snapshot(self, data_root: str, stats: StorageStatsDict) -> None:
        path = self._snapshot_path(data_root)
        if not path.parent.is_dir():
            return  # 数据目录不存在（未启用）时不产垃圾文件
        tmp = path.with_suffix(".json.tmp")
        try:
            tmp.write_text(
                json.dumps({"computed_at": time.time(), "stats": stats}),
                encoding="utf-8",
            )
            os.replace(tmp, path)
        except OSError as exc:
            log(f"cognee 存储统计快照写入失败: {exc}", "DEBUG", tag="记忆")


cognee_storage_stats = StorageStatsTracker()
