"""cognee 向量索引一次性清理：重复行去重 + goal 投影退场。

背景：cognee 的 add_data_points/index_graph_edges 每次 cognify 都把当批
实体名/关系文本重新 embedding 并**追加**进 Lance 向量表（无按内容去重），
高频重投影下 EdgeType_relationship_name 曾堆积 21 万+ 重复行（真实关系
文本仅数百种），拖慢检索并浪费磁盘。本脚本：

1. 遍历 cognee 数据目录全部 *.lance.db，对 EdgeType_relationship_name /
   Entity_name / EntityType_name 三张索引表按 text 去重（保留一行）；
2. 向 cognee_sync_queue 注入 delete 操作，清退 source='goal' 的记忆投影
   （新版代码已不再投影 goal 文档，这里处理存量）。

幂等：重复执行无副作用（去重后无重复行、已退场的 goal 无映射可清）。
运行时机：应用运行中可执行（Lance 乐观并发下删除冲突会自动重试；
SQLite 走 WAL 短事务）。删除产生的 tombstone 由 cognee 自动压缩回收。

用法：uv run python scripts/dedupe_cognee_vector_index.py
"""

from __future__ import annotations

import asyncio
import glob
import os
import sqlite3
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.path import ConfigPaths  # noqa: E402


def _memory_db_path() -> str:
    """记忆库路径（经存储卷注册表解析，尊重位置指派）。"""
    from agent.memory.memory_store import default_memory_db_path  # noqa: PLC0415
    from core.storage_volume import get_volume_registry  # noqa: PLC0415

    default_memory_db_path()  # 触发模块导入即完成 memory 卷登记
    return get_volume_registry().resolve_path("memory")

# 按内容去重的索引表（均为"名称/文本 → 向量"的检索索引，重复行纯冗余）
_DEDUP_TABLES = ("EdgeType_relationship_name", "Entity_name", "EntityType_name")
_DELETE_BATCH = 500
_MAX_RETRY = 5


def _cognee_root() -> str:
    """cognee 数据根（cognee.json data_root 为权威，缺省走卷默认）。"""
    config_path = Path(ConfigPaths.COGNEE_CONFIG)
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    try:
        import json

        raw = json.loads(config_path.read_text(encoding="utf-8"))
        root = str(raw.get("data_root", "") or "").strip()
    except (OSError, ValueError):
        root = ""
    if not root:
        root = ConfigPaths.COGNEE_DATA_DIR
    path = Path(root)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return str(path)


async def _dedupe_table(db: "object", table_name: str) -> tuple[int, int]:
    """单表按 text 去重，返回 (去重前行数, 删除行数)。"""
    table = await db.open_table(table_name)  # type: ignore[attr-defined]
    before = await table.count_rows()
    if before <= 1:
        return before, 0
    rows = await table.query().select(["id", "payload"]).to_list()
    seen: dict[str, str] = {}
    duplicate_ids: list[str] = []
    for row in rows:
        payload = row.get("payload") or {}
        text = str(payload.get("text") or "") if isinstance(payload, dict) else ""
        row_id = str(row.get("id") or "")
        if not row_id:
            continue
        if text in seen:
            duplicate_ids.append(row_id)
        else:
            seen[text] = row_id
    for i in range(0, len(duplicate_ids), _DELETE_BATCH):
        batch = duplicate_ids[i : i + _DELETE_BATCH]
        escaped = ",".join("'" + rid.replace("'", "''") + "'" for rid in batch)
        for attempt in range(1, _MAX_RETRY + 1):
            try:
                await table.delete(f"id IN ({escaped})")
                break
            except Exception as exc:
                if attempt >= _MAX_RETRY:
                    raise RuntimeError(f"{table_name} 删除冲突重试耗尽: {exc}") from exc
                await asyncio.sleep(0.5 * attempt)
    return before, len(duplicate_ids)


async def dedupe_vector_indexes() -> None:
    root = _cognee_root()
    pattern = os.path.join(root, "system", "databases", "**", "*.lance.db")
    db_dirs = glob.glob(pattern, recursive=True)
    if not db_dirs:
        print(f"未发现 lance 库: {pattern}")
        return
    import lancedb

    total_removed = 0
    for db_dir in sorted(db_dirs):
        try:
            db = await lancedb.connect_async(db_dir)
            names = await db.table_names()
        except Exception as exc:
            print(f"跳过 {os.path.basename(db_dir)}: 打开失败 {exc}")
            continue
        for table_name in _DEDUP_TABLES:
            if table_name not in names:
                continue
            try:
                before, removed = await _dedupe_table(db, table_name)
            except Exception as exc:
                print(f"  {os.path.basename(db_dir)}/{table_name}: 去重失败 {exc}")
                continue
            if removed:
                total_removed += removed
                print(f"  {os.path.basename(db_dir)}/{table_name}: {before} → {before - removed}（去重 {removed} 行）")
    print(f"向量索引去重完成，共删除 {total_removed} 行")


def retire_goal_projections() -> None:
    """为仍映射在 cognee 的 goal 记忆注入 delete 投影操作（幂等）。"""
    db_path = _memory_db_path()
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        rows = conn.execute(
            "SELECT m.entry_id FROM cognee_entry_map m "
            "JOIN memories mem ON mem.id = m.entry_id "
            "WHERE m.entry_kind='memory' AND mem.source='goal' AND m.data_id != ''"
        ).fetchall()
        if not rows:
            print("goal 投影清理: 无存量映射")
            return
        now_ns = time.time_ns()
        inserted = 0
        with conn:
            conn.execute(
                "DELETE FROM cognee_sync_queue WHERE entry_kind='memory' "
                "AND operation='upsert' AND status IN ('pending','failed') "
                "AND entry_id IN (SELECT m.entry_id FROM cognee_entry_map m "
                "JOIN memories mem ON mem.id = m.entry_id "
                "WHERE m.entry_kind='memory' AND mem.source='goal')"
            )
            for (entry_id,) in rows:
                cursor = conn.execute(
                    "INSERT INTO cognee_sync_queue"
                    "(entry_kind, entry_id, operation, payload_json, status, attempts,"
                    " next_retry_ns, last_error, created_ns, updated_ns) "
                    "SELECT 'memory', ?, 'delete', '{}', 'pending', 0, 0, '', ?, ? "
                    "WHERE NOT EXISTS ("
                    "  SELECT 1 FROM cognee_sync_queue "
                    "  WHERE entry_kind='memory' AND entry_id=? AND operation='delete'"
                    ")",
                    (entry_id, now_ns, now_ns, entry_id),
                )
                inserted += cursor.rowcount or 0
        print(f"goal 投影清理: {len(rows)} 条映射，注入 {inserted} 条 delete（其余已在队列）")
    finally:
        conn.close()


async def main() -> None:
    await dedupe_vector_indexes()
    retire_goal_projections()


if __name__ == "__main__":
    asyncio.run(main())
