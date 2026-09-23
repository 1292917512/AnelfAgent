"""工作流 journal — SQLite 断点恢复的唯一事实源。

写入纪律（对齐成熟 journal 设计，是断点恢复正确性的全部前提）：

- **准入即落 running**：步骤派发前先写 running 行，崩溃后 running 行
  即「执行中被打断」的证据（恢复时重派）。
- **终态一笔写**：run 的终态与结算袋（stop_reason/failure/result/
  finished_at）一条 UPDATE 写完——分两条会开出「completed 但无 result」
  的崩溃窗口；翻回非终态（resume）同笔清空结算袋。
- **事件单调序**：sequence 在写锁内 max+1 分配；run-settled 必须是
  事件流最后一条。

恢复语义（engine 消费）：

- 同 run 续跑（resume）：completed 行以 input_hash 防御性比对后缓存
  结算（不重付费）；running 行重派；failed 行复现失败。
- 修订运行（amend，start 时 resume_of=父 run）：父 run 每步「最新
  completed 行」可导入——子 run 执行到该步时 input_hash 一致即结算
  为 cached，分歧（hash 不一致）自然级联为重跑。
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

import aiosqlite

from core.log import log
from core.path import ConfigPaths

RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
STOPPED = "stopped"
TERMINAL_RUN_STATUSES = (COMPLETED, FAILED, STOPPED)

NODE_RUNNING = "running"
NODE_COMPLETED = "completed"
NODE_FAILED = "failed"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_run (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    spec_json TEXT NOT NULL,
    spec_hash TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT '',
    parent_run_id TEXT,
    status TEXT NOT NULL CHECK(status IN ('running','completed','failed','stopped')),
    stop_reason TEXT,
    failure_json TEXT,
    result_json TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS workflow_run_updated_idx ON workflow_run(updated_at DESC);

CREATE TABLE IF NOT EXISTS workflow_node (
    run_id TEXT NOT NULL,
    step_key TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('ask','tool','repair')),
    status TEXT NOT NULL CHECK(status IN ('running','completed','failed')),
    input_hash TEXT NOT NULL,
    input_json TEXT,
    result_text TEXT,
    error_text TEXT,
    delegation_id TEXT,
    usage_json TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (run_id, step_key, ordinal)
);

CREATE TABLE IF NOT EXISTS workflow_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    ts REAL NOT NULL,
    UNIQUE (run_id, sequence)
);
"""


def _row_to_dict(row: aiosqlite.Row) -> Dict[str, Any]:
    return {key: row[key] for key in row.keys()}


class WorkflowJournal:
    """工作流 journal（单连接 + 写锁；WAL；测试可用 db_path 覆盖）。"""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or ConfigPaths.WORKFLOW_DB
        self._conn: Optional[aiosqlite.Connection] = None
        self._conn_lock: Optional[Any] = None  # asyncio.Lock，首次使用时创建
        self._write_lock: Optional[Any] = None

    async def _get_db(self) -> aiosqlite.Connection:
        import asyncio

        if self._conn_lock is None:
            self._conn_lock = asyncio.Lock()
            self._write_lock = asyncio.Lock()
        if self._conn is not None:
            return self._conn
        async with self._conn_lock:
            if self._conn is not None:
                return self._conn
            import os

            os.makedirs(os.path.dirname(os.path.abspath(self._db_path)) or ".", exist_ok=True)
            db = await aiosqlite.connect(self._db_path)
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA synchronous=NORMAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            db.row_factory = aiosqlite.Row
            await db.executescript(_SCHEMA)
            await db.commit()
            self._conn = db
            return db

    async def aclose(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # run
    # ------------------------------------------------------------------

    async def create_run(
            self, run_id: str, name: str, spec_json: str, spec_hash: str,
            scope: str, parent_run_id: str = "",
    ) -> None:
        db = await self._get_db()
        now = time.time()
        async with self._write_lock:
            await db.execute(
                "INSERT INTO workflow_run (id, name, spec_json, spec_hash, scope,"
                " parent_run_id, status, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, name, spec_json, spec_hash, scope,
                 parent_run_id or None, RUNNING, now, now),
            )
            await db.commit()

    async def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        async with db.execute("SELECT * FROM workflow_run WHERE id = ?", (run_id,)) as cur:
            row = await cur.fetchone()
        return _row_to_dict(row) if row is not None else None

    async def settle_run(
            self, run_id: str, status: str, *, stop_reason: str = "",
            failure: Optional[Dict[str, Any]] = None,
            result: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """run 终态一笔写（status/stop_reason/failure/result/finished_at 同笔）。"""
        if status not in TERMINAL_RUN_STATUSES:
            raise ValueError(f"非法 run 终态: {status}")
        db = await self._get_db()
        async with self._write_lock:
            cur = await db.execute(
                "UPDATE workflow_run SET status = ?, stop_reason = ?, failure_json = ?,"
                " result_json = ?, finished_at = ?, updated_at = ? WHERE id = ?",
                (status, stop_reason or None,
                 json.dumps(failure, ensure_ascii=False) if failure else None,
                 json.dumps(result, ensure_ascii=False) if result else None,
                 time.time(), time.time(), run_id),
            )
            await db.commit()
            return cur.rowcount > 0

    async def reopen_run(self, run_id: str) -> bool:
        """续跑开账：翻回 running 并同笔清空结算袋（缺一即出脏终态）。"""
        db = await self._get_db()
        async with self._write_lock:
            cur = await db.execute(
                "UPDATE workflow_run SET status = ?, stop_reason = NULL, failure_json = NULL,"
                " result_json = NULL, finished_at = NULL, updated_at = ?"
                " WHERE id = ? AND status = ?",
                (RUNNING, time.time(), run_id, STOPPED),
            )
            await db.commit()
            return cur.rowcount > 0

    async def list_runs(self, limit: int = 30) -> List[Dict[str, Any]]:
        db = await self._get_db()
        sql = "SELECT id, name, status, stop_reason, scope, parent_run_id, spec_hash," \
              " created_at, updated_at, finished_at FROM workflow_run" \
              " ORDER BY updated_at DESC LIMIT ?"
        async with db.execute(sql, (max(1, limit),)) as cur:
            rows = await cur.fetchall()
        return [_row_to_dict(r) for r in rows]

    async def list_non_terminal_runs(self) -> List[Dict[str, Any]]:
        """崩溃残留（永远停在 running 的 run）；启动收敛用。"""
        db = await self._get_db()
        async with db.execute(
                "SELECT * FROM workflow_run WHERE status = 'running'") as cur:
            rows = await cur.fetchall()
        return [_row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # node
    # ------------------------------------------------------------------

    async def admit_node(
            self, run_id: str, step_key: str, ordinal: int, kind: str,
            input_hash: str, input_json: str = "",
    ) -> None:
        """步骤准入（先落 running 再派发；已存在则更新 input 与时间）。"""
        db = await self._get_db()
        now = time.time()
        async with self._write_lock:
            await db.execute(
                "INSERT INTO workflow_node (run_id, step_key, ordinal, kind, status,"
                " input_hash, input_json, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?)"
                " ON CONFLICT(run_id, step_key, ordinal) DO UPDATE SET"
                " input_hash = excluded.input_hash, input_json = excluded.input_json,"
                " updated_at = excluded.updated_at",
                (run_id, step_key, ordinal, kind, input_hash, input_json, now, now),
            )
            await db.commit()

    async def settle_node(
            self, run_id: str, step_key: str, ordinal: int, *,
            result_text: str = "", error_text: str = "",
            delegation_id: str = "", usage: Optional[Dict[str, Any]] = None,
    ) -> None:
        """步骤终态（completed/error 二选一由 error_text 是否非空决定）。"""
        db = await self._get_db()
        status = NODE_FAILED if error_text else NODE_COMPLETED
        async with self._write_lock:
            await db.execute(
                "UPDATE workflow_node SET status = ?, result_text = ?, error_text = ?,"
                " delegation_id = ?, usage_json = ?, updated_at = ?"
                " WHERE run_id = ? AND step_key = ? AND ordinal = ?",
                (status, result_text, error_text or None, delegation_id or None,
                 json.dumps(usage, ensure_ascii=False) if usage else None,
                 time.time(), run_id, step_key, ordinal),
            )
            await db.commit()

    async def get_node(self, run_id: str, step_key: str, ordinal: int) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        async with db.execute(
                "SELECT * FROM workflow_node WHERE run_id = ? AND step_key = ? AND ordinal = ?",
                (run_id, step_key, ordinal)) as cur:
            row = await cur.fetchone()
        return _row_to_dict(row) if row is not None else None

    async def latest_nodes(self, run_id: str) -> Dict[str, Dict[str, Any]]:
        """每 step_key 的最大 ordinal 行（同 run 续跑的重放事实源）。"""
        db = await self._get_db()
        async with db.execute(
                "SELECT n.* FROM workflow_node n JOIN ("
                "  SELECT step_key, MAX(ordinal) AS mo FROM workflow_node"
                "  WHERE run_id = ? GROUP BY step_key"
                ") m ON n.run_id = ? AND n.step_key = m.step_key AND n.ordinal = m.mo",
                (run_id, run_id)) as cur:
            rows = await cur.fetchall()
        return {_row_to_dict(r)["step_key"]: _row_to_dict(r) for r in rows}

    async def next_ordinal(self, run_id: str, step_key: str) -> int:
        """该步骤的下一个执行序号（重试/门控轮次各占一档）。"""
        db = await self._get_db()
        async with db.execute(
                "SELECT COALESCE(MAX(ordinal), 0) AS mo FROM workflow_node"
                " WHERE run_id = ? AND step_key = ?", (run_id, step_key)) as cur:
            row = await cur.fetchone()
        return int(row["mo"]) + 1

    async def importable_nodes(self, parent_run_id: str) -> Dict[str, Dict[str, Any]]:
        """父 run 可导入的步骤成果：每 key 的最新 completed 行（input_hash 比对用）。"""
        db = await self._get_db()
        async with db.execute(
                "SELECT n.* FROM workflow_node n JOIN ("
                "  SELECT step_key, MAX(ordinal) AS mo FROM workflow_node"
                "  WHERE run_id = ? AND status = 'completed' GROUP BY step_key"
                ") m ON n.run_id = ? AND n.step_key = m.step_key AND n.ordinal = m.mo",
                (parent_run_id, parent_run_id)) as cur:
            rows = await cur.fetchall()
        return {_row_to_dict(r)["step_key"]: _row_to_dict(r) for r in rows}

    async def nodes_of_run(self, run_id: str) -> List[Dict[str, Any]]:
        """run 全部节点（按 key、ordinal 排序；详情页数据源）。"""
        db = await self._get_db()
        async with db.execute(
                "SELECT * FROM workflow_node WHERE run_id = ?"
                " ORDER BY step_key, ordinal", (run_id,)) as cur:
            rows = await cur.fetchall()
        return [_row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # event
    # ------------------------------------------------------------------

    async def append_event(self, run_id: str, event_type: str,
                           payload: Optional[Dict[str, Any]] = None) -> int:
        """追加事件（写锁内 max+1 分配单调序；返回 sequence）。"""
        db = await self._get_db()
        async with self._write_lock:
            async with db.execute(
                    "SELECT COALESCE(MAX(sequence), -1) AS ms FROM workflow_event"
                    " WHERE run_id = ?", (run_id,)) as cur:
                row = await cur.fetchone()
            sequence = int(row["ms"]) + 1
            await db.execute(
                "INSERT INTO workflow_event (run_id, sequence, type, payload_json, ts)"
                " VALUES (?, ?, ?, ?, ?)",
                (run_id, sequence, event_type,
                 json.dumps(payload or {}, ensure_ascii=False), time.time()),
            )
            await db.commit()
            return sequence

    async def list_events(self, run_id: str, after_sequence: int = 0,
                          limit: int = 200) -> List[Dict[str, Any]]:
        db = await self._get_db()
        async with db.execute(
                "SELECT sequence, type, payload_json, ts FROM workflow_event"
                " WHERE run_id = ? AND sequence > ? ORDER BY sequence LIMIT ?",
                (run_id, after_sequence, max(1, limit))) as cur:
            rows = await cur.fetchall()
        events: List[Dict[str, Any]] = []
        for row in rows:
            item = _row_to_dict(row)
            try:
                item["payload"] = json.loads(item.pop("payload_json") or "{}")
            except ValueError:
                item["payload"] = {}
            events.append(item)
        return events

    # ------------------------------------------------------------------
    # retention
    # ------------------------------------------------------------------

    async def purge_expired(self, days: int) -> int:
        """清理超期终态 run（连带节点与事件；幂等）。"""
        if days <= 0:
            return 0
        db = await self._get_db()
        cutoff = time.time() - days * 86400
        async with self._write_lock:
            cur = await db.execute(
                "DELETE FROM workflow_run WHERE status != 'running' AND finished_at < ?",
                (cutoff,),
            )
            deleted = cur.rowcount
            await db.execute(
                "DELETE FROM workflow_node WHERE run_id NOT IN (SELECT id FROM workflow_run)")
            await db.execute(
                "DELETE FROM workflow_event WHERE run_id NOT IN (SELECT id FROM workflow_run)")
            await db.commit()
        if deleted:
            log(f"工作流 journal 清理: {deleted} 个超期 run", "DEBUG", tag="工作流")
        return int(deleted)
