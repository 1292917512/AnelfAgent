#!/usr/bin/env python3
"""离线评测 search_unified 的文本检索。默认使用随仓合成语料。

python scripts/eval_memory_recall.py [-k 5]
python scripts/eval_memory_recall.py cases.json --db /path/to/memory.db

合成用例用 relevant 指定目标记录键，empty 表示应无结果。外部数据库使用
SQLite 在线只读快照；旧式 expect 子串数组中的任一命中仍计为通过。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DEFAULT_CASES = ROOT / "scripts/eval_recall_cases.json"


class Sample(BaseModel):
    key: str = Field(min_length=1)
    content: str = Field(min_length=1)
    previous: str = ""


class Case(BaseModel):
    query: str = Field(min_length=1)
    relevant: list[str] = Field(default_factory=list)
    expect: list[str] = Field(default_factory=list)
    empty: bool = False
    note: str = ""


class Corpus(BaseModel):
    memories: list[Sample] = Field(default_factory=list)
    cases: list[Case] = Field(min_length=1)


async def run(cases_path: Path, db_path: Path | None, top_k: int) -> int:
    from agent.memory.memory_store import MemoryStore
    from agent.memory.memory_types import MemoryEntry, MemoryType
    from core.sqlite_utils import online_sqlite_backup

    if top_k < 1:
        raise ValueError("k 必须大于零")
    raw = json.loads(cases_path.read_text(encoding="utf-8"))
    corpus = Corpus.model_validate({"cases": raw} if isinstance(raw, list) else raw)
    if db_path is None and not corpus.memories:
        raise ValueError("外部用例须用 --db 指定配套记忆库")
    if db_path is not None and corpus.memories:
        raise ValueError("合成语料已自带 memories；使用 --db 时请指定与该库配套的用例集")
    keys = {sample.key for sample in corpus.memories}
    if len(keys) != len(corpus.memories):
        raise ValueError("合成记忆 key 重复")
    for case in corpus.cases:
        if sum((bool(case.relevant), bool(case.expect), case.empty)) != 1:
            raise ValueError(f"{case.query} 必须选择 relevant、expect 或 empty 中的一种判定")
        if any(not term.strip() for term in (*case.expect, *case.relevant)):
            raise ValueError("目标或匹配子串不能为空")
        if keys and not keys.issuperset(case.relevant):
            raise ValueError(f"{case.query} 引用了未知记忆")

    with TemporaryDirectory(prefix="anelf-recall-") as directory:
        tmp = Path(directory) / "eval.db"
        if db_path is not None:
            if not db_path.is_file():
                raise ValueError(f"记忆库不存在 {db_path}")
            await online_sqlite_backup(db_path, tmp)
        store = MemoryStore(str(tmp))
        try:
            store.set_cognee_projection_enabled(False)
            ids: dict[str, str] = {}
            for sample in corpus.memories:
                entry = MemoryEntry(memory_type=MemoryType.SEMANTIC, content=sample.previous or sample.content,
                                    source="eval", timestamp=1700000000.)
                entry.id = await store.add(entry)
                if sample.previous:
                    entry.content = sample.content
                    if not await store.update(entry):
                        raise RuntimeError(f"无法更新合成记忆 {sample.key}")
                ids[sample.key] = f"mem:{entry.id}"

            hits = positives = passed = empty_ok = empty_total = 0
            reciprocal_ranks = 0.0
            recalls: list[float] = []
            for case in corpus.cases:
                results = await store.search_unified(query=case.query, limit=top_k)
                targets = {ids.get(key, key) for key in case.relevant}
                if case.empty:
                    ok = not results
                    empty_ok += ok
                    empty_total += 1
                else:
                    ranks = [i for i, item in enumerate(results, 1)
                             if item.id in targets or any(word in item.snippet for word in case.expect)]
                    positives += 1
                    hits += bool(ranks)
                    reciprocal_ranks += 1 / ranks[0] if ranks else 0
                    ok = bool(ranks)
                    if targets:
                        found = targets.intersection(item.id for item in results)
                        recalls.append(len(found) / len(targets))
                        ok = found == targets
                passed += ok
                print(f"{'PASS' if ok else 'MISS'}  {case.query}  {case.note}")
            if positives:
                print(f"Hit@{top_k}: {hits}/{positives} ({hits / positives:.1%})")
                print(f"MRR@{top_k}: {reciprocal_ranks / positives:.3f}")
            if recalls:
                print(f"Recall@{top_k} (macro, labeled targets): {sum(recalls) / len(recalls):.1%}")
            if empty_total:
                print(f"Empty queries: {empty_ok}/{empty_total}")
            return 0 if passed == len(corpus.cases) else 1
        finally:
            await store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cases", nargs="?", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--db", type=Path, help="与外部用例集配套的记忆库")
    parser.add_argument("-k", type=int, default=5)
    args = parser.parse_args()
    try:
        return asyncio.run(run(args.cases, args.db, args.k))
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(2, f"评测输入或数据库错误 {exc}\n")


if __name__ == "__main__":
    sys.exit(main())
