"""记忆库清洗脚本：merged 残留归档 + 标签彻底归一（零丢失，全部软操作）。

用法：
    uv run python scripts/memory_cleanup.py --dry-run   # 只输出报告，不写库
    uv run python scripts/memory_cleanup.py --apply     # 实际执行

清洗规则（幂等，可重复执行）：
1. importance=0 的 merged 残留 → 软归档（memories_archive，可恢复）
2. 粘连标签拆分：单标签内含多个 "key:value" 段时拆分为多个标签
3. type: 标签归一：与 memories.type 列对齐（event/fact/entity/reflection/permanent），
   剔除非标准 type:*（如 type:self_reflection / type:lesson 等）
4. legacy 标签回填 adapter：user:123 → user:qq:123（默认频道由
   legacy_adapter_default 配置决定，默认 qq）
5. 剔除残留 "merged" 标记标签与空标签，去重保持顺序
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.memory.memory_store import MemoryStore  # noqa: E402
from agent.memory.memory_types import MemoryType  # noqa: E402

# DB type 列 → 标准 type: 标签
_CANONICAL_TYPE_TAG = {
    MemoryType.EPISODIC.value: "type:event",
    MemoryType.SEMANTIC.value: "type:fact",
    MemoryType.ENTITY.value: "type:entity",
    MemoryType.REFLECTION.value: "type:reflection",
    MemoryType.PERMANENT.value: "type:permanent",
}

# 标签段形态：prefix:value（prefix 允许中英文与下划线）
_TAG_SEGMENT_RE = re.compile(r"^[A-Za-z一-鿿_]+:[^\s]+$")
_LEGACY_ENTITY_RE = re.compile(r"^(user|group):([^:]+)$")


def _legacy_adapter() -> str:
    try:
        from core.config import get_config_str
        return get_config_str("legacy_adapter_default", "qq") or "qq"
    except Exception:
        return "qq"


def normalize_tags(tags: list[str], mem_type: str, adapter: str) -> tuple[list[str], list[str]]:
    """归一一条记忆的标签，返回 (新标签列表, 变更说明列表)。"""
    changes: list[str] = []
    segments: list[str] = []

    # 1. 粘连标签拆分
    for tag in tags:
        if not tag or not tag.strip():
            changes.append("剔除空标签")
            continue
        tag = tag.strip()
        if " " in tag:
            parts = [p for p in tag.split() if p]
            valid = [p for p in parts if _TAG_SEGMENT_RE.match(p)]
            if valid and len(valid) == len(parts):
                segments.extend(valid)
                changes.append(f"拆分粘连标签: {tag[:40]}")
            else:
                segments.append(tag)
        else:
            segments.append(tag)

    # 2. type: 归一（以 DB type 列为唯一权威）
    canonical = _CANONICAL_TYPE_TAG.get(mem_type)
    non_standard = [t for t in segments if t.startswith("type:") and t != canonical]
    if non_standard:
        changes.append(f"剔除非标准type: {', '.join(non_standard[:3])}")
    segments = [t for t in segments if not t.startswith("type:")]
    if canonical:
        segments.insert(0, canonical)

    # 3. legacy user:/group: 回填 adapter
    upgraded: list[str] = []
    for t in segments:
        m = _LEGACY_ENTITY_RE.match(t)
        if m:
            new_tag = f"{m.group(1)}:{adapter}:{m.group(2)}"
            upgraded.append(new_tag)
            changes.append(f"legacy回填: {t} → {new_tag}")
        else:
            upgraded.append(t)
    segments = upgraded

    # 4. 剔除 merged 标记 + 去重保序
    if "merged" in segments:
        changes.append("剔除merged标记")
    seen: set[str] = set()
    result: list[str] = []
    for t in segments:
        if t == "merged" or t in seen:
            continue
        seen.add(t)
        result.append(t)
    return result, changes


async def run(apply: bool) -> None:
    # 记忆库路径与生产一致：卷注册表解析（指派优先，默认主库 stem + "_memory"）
    store = MemoryStore()
    db_path = store._db_path
    adapter = _legacy_adapter()
    print(f"DB: {db_path}\nlegacy adapter: {adapter}\nmode: {'APPLY' if apply else 'DRY-RUN'}\n")

    try:
        # ---- 步骤 1：merged 残留归档 ----
        db = await store._get_db()
        cursor = await db.execute("SELECT COUNT(*) AS n FROM memories WHERE importance = 0")
        merged_count = int((await cursor.fetchone())["n"])
        print(f"[1] merged 残留（importance=0）: {merged_count} 条 → 软归档")
        if apply and merged_count:
            cursor = await db.execute("SELECT id FROM memories WHERE importance = 0")
            ids = [int(r["id"]) for r in await cursor.fetchall()]
            done = 0
            for mid in ids:
                if await store.archive_memory(mid, reason="merged占位清理"):
                    done += 1
            print(f"    已归档 {done} 条")

        # ---- 步骤 2-5：标签归一 ----
        cursor = await db.execute("SELECT id, type, tags_json FROM memories")
        rows = await cursor.fetchall()
        changed = 0
        change_samples: list[str] = []
        for row in rows:
            try:
                tags = json.loads(row["tags_json"] or "[]")
            except json.JSONDecodeError:
                tags = []
            new_tags, changes = normalize_tags(tags, row["type"], adapter)
            if new_tags != tags:
                changed += 1
                if len(change_samples) < 12:
                    change_samples.append(f"  mem:{row['id']} {'; '.join(dict.fromkeys(changes))}")
                if apply:
                    entry = await store.get(int(row["id"]))
                    if entry is not None:
                        entry.tags = new_tags
                        await store.update(entry)
        print(f"[2] 标签归一: {changed}/{len(rows)} 条需变更")
        for line in change_samples:
            print(line)
        if apply:
            print(f"    已更新 {changed} 条")

        # ---- 收尾统计 ----
        type_counts = await store.get_type_counts()
        archived = await store.count_archived()
        print(f"\n收尾: 活跃 {sum(type_counts.values())} 条 {type_counts}，归档 {archived} 条")
    finally:
        await store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="记忆库清洗")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(apply=args.apply))


if __name__ == "__main__":
    main()
