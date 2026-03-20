"""一次性数据迁移：将旧 memories 表中的记忆按类型导出为 MD 文件。

迁移后原记录标记 migrated=1 但不删除，可随时回退。
只在检测到未迁移记录时执行，幂等安全。
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import aiosqlite

from core.log import log
from .memory_types import MemoryType


async def needs_migration(db_path: str) -> bool:
    """检查是否存在需要迁移的记忆。"""
    try:
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM memories WHERE migrated = 0"
            )
            row = await cursor.fetchone()
            return (row[0] if row else 0) > 0
    except Exception:
        return False


async def migrate_memories_to_md(db_path: str, workspace_dir: Path) -> int:
    """将 memories 表中未迁移的记忆导出为 MD 文件。

    按 MemoryType 分类：
      - PERMANENT → memory.md（常青知识）
      - ENTITY    → memory/entities.md
      - REFLECTION → memory/reflections.md
      - EPISODIC  → memory/YYYY-MM-DD.md（按日期）
      - SEMANTIC  → memory/knowledge.md

    返回迁移的记忆条数。
    """
    memory_dir = workspace_dir / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "SELECT id, type, content, source, importance, ts_ns, tags_json "
            "FROM memories WHERE migrated = 0 ORDER BY ts_ns ASC"
        )
        rows = await cursor.fetchall()

    if not rows:
        return 0

    log(f"📦 记忆迁移: 发现 {len(rows)} 条未迁移记忆", tag="思维")

    # 按类型分组
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        mem_id, mem_type, content, source, importance, ts_ns, tags_raw = row
        tags: list[str] = []
        try:
            tags = json.loads(tags_raw) if tags_raw else []
        except (json.JSONDecodeError, TypeError):
            pass

        grouped[mem_type].append({
            "id": mem_id,
            "content": content,
            "source": source,
            "importance": importance,
            "ts_ns": ts_ns,
            "tags": tags,
        })

    migrated_ids: list[int] = []

    # PERMANENT → memory/memory.md
    if MemoryType.PERMANENT.value in grouped:
        entries = grouped[MemoryType.PERMANENT.value]
        _append_to_file(
            memory_dir / "memory.md",
            _format_section("永久记忆", entries),
        )
        migrated_ids.extend(e["id"] for e in entries)

    # ENTITY → memory/entities.md
    if MemoryType.ENTITY.value in grouped:
        entries = grouped[MemoryType.ENTITY.value]
        _append_to_file(
            memory_dir / "entities.md",
            _format_section("实体画像", entries),
        )
        migrated_ids.extend(e["id"] for e in entries)

    # REFLECTION → memory/reflections.md
    if MemoryType.REFLECTION.value in grouped:
        entries = grouped[MemoryType.REFLECTION.value]
        _append_to_file(
            memory_dir / "reflections.md",
            _format_section("反思记忆", entries),
        )
        migrated_ids.extend(e["id"] for e in entries)

    # SEMANTIC → memory/knowledge.md
    if MemoryType.SEMANTIC.value in grouped:
        entries = grouped[MemoryType.SEMANTIC.value]
        _append_to_file(
            memory_dir / "knowledge.md",
            _format_section("语义知识", entries),
        )
        migrated_ids.extend(e["id"] for e in entries)

    # EPISODIC → memory/YYYY-MM-DD.md（按日期归档）
    if MemoryType.EPISODIC.value in grouped:
        entries = grouped[MemoryType.EPISODIC.value]
        by_date: Dict[str, list[Dict[str, Any]]] = defaultdict(list)
        for e in entries:
            ts = e["ts_ns"] / 1e9
            date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            by_date[date_str].append(e)

        for date_str, day_entries in by_date.items():
            _append_to_file(
                memory_dir / f"{date_str}.md",
                _format_section(f"事件记忆 ({date_str})", day_entries),
            )
        migrated_ids.extend(e["id"] for e in entries)

    # 标记已迁移
    if migrated_ids:
        async with aiosqlite.connect(db_path) as db:
            placeholders = ",".join("?" for _ in migrated_ids)
            await db.execute(
                f"UPDATE memories SET migrated = 1 WHERE id IN ({placeholders})",
                migrated_ids,
            )
            await db.commit()

    log(f"✅ 记忆迁移完成: {len(migrated_ids)} 条记忆已导出为 MD 文件", tag="思维")
    return len(migrated_ids)


def _clean_content(text: str) -> str:
    """清洗 AI 原始输出：移除思维链标签和模型特定 XML 标签。"""
    import re
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"</?(?:minimax|invoke|parameter)[^>]*>", "", text)
    text = text.strip()
    if not text:
        return ""
    lines = [line for line in text.split("\n") if line.strip()]
    return "\n".join(lines)


def _format_section(title: str, entries: list[Dict[str, Any]]) -> str:
    """将一组记忆格式化为 Markdown 段落。"""
    lines: list[str] = [f"## {title}\n"]
    for e in entries:
        ts = e["ts_ns"] / 1e9
        time_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")

        content = _clean_content(e["content"])
        if not content:
            continue

        tag_str = f" `{', '.join(e['tags'])}`" if e["tags"] else ""
        source_str = f" (source: {e['source']})" if e["source"] else ""

        lines.append(f"- [{time_str}]{tag_str}{source_str} {content}")

    lines.append("")
    return "\n".join(lines)


def _append_to_file(path: Path, content: str) -> None:
    """追加内容到文件末尾，文件不存在时创建。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = ""
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing and not existing.endswith("\n"):
            existing += "\n"

    # 如果文件为空，添加标题
    if not existing.strip():
        header = f"# {path.stem}\n\n"
        path.write_text(header + content, encoding="utf-8")
    else:
        path.write_text(existing + content, encoding="utf-8")
