#!/usr/bin/env python3
"""记忆召回评测：对标准问答集跑 search_unified，报告 recall@k 命中率。

用例集为 JSON 数组（见 eval_recall_cases.example.json）：
  {"query": "检索查询", "expect": ["命中片段应包含的子串", ...], "note": "可选说明"}
判定：top-k 任一结果的 snippet 包含任一 expect 子串即算命中。

用法：
  .venv/bin/python scripts/eval_memory_recall.py [cases.json] [--db 路径] [-k 5]

说明：在数据库副本上离线运行（不影响线上）；embedder 离线不可用，
本评测衡量 FTS + 标签通道（jieba 改造的主要受益面）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _default_db() -> Path:
    """经存储卷注册表解析记忆库路径（卷指派/环境变量优先于历史默认布局）。"""
    import agent.memory.memory_store  # noqa: F401  触发卷登记
    from core.storage_volume import get_volume_registry

    return Path(get_volume_registry().resolve_path("memory"))


DEFAULT_CASES = ROOT / "scripts/eval_recall_cases.json"


async def run(cases_path: Path, db_path: Path, top_k: int) -> int:
    from agent.memory.memory_store import MemoryStore

    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    # 复制到临时文件：避免评测对线上库产生任何写操作（含 WAL 副作用）
    tmp = Path(tempfile.mkdtemp()) / "eval.db"
    shutil.copy(db_path, tmp)
    for suffix in ("-wal", "-shm"):
        side = Path(str(db_path) + suffix)
        if side.exists():
            shutil.copy(side, Path(str(tmp) + suffix))

    store = MemoryStore(str(tmp))
    try:
        hits = 0
        for i, case in enumerate(cases, 1):
            query = case["query"]
            expect = case["expect"]
            results = await store.search_unified(query=query, limit=top_k)
            found = any(
                any(e in r.snippet for e in expect) for r in results
            )
            hits += found
            mark = "✅" if found else "❌"
            top = results[0].snippet[:40].replace("\n", " ") if results else "（无结果）"
            print(f"{mark} [{i:2d}] {query[:30]:<32} top1: {top}")
        total = len(cases)
        print(f"\nrecall@{top_k}: {hits}/{total} = {hits/total*100:.1f}%")
        return 0 if hits == total else 1
    finally:
        await store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="记忆召回评测")
    parser.add_argument("cases", nargs="?", default=str(DEFAULT_CASES), help="用例集 JSON")
    parser.add_argument("--db", default=str(_default_db()), help="记忆库路径")
    parser.add_argument("-k", type=int, default=5, help="top-k（默认 5）")
    args = parser.parse_args()

    cases_path = Path(args.cases)
    if not cases_path.exists():
        print(f"用例集不存在: {cases_path}\n参考 scripts/eval_recall_cases.example.json 创建")
        return 2
    return asyncio.run(run(cases_path, Path(args.db), args.k))


if __name__ == "__main__":
    sys.exit(main())
