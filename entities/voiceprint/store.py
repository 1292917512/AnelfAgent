"""VoiceprintStore：说话人声纹库 + 语音片段库的统一存储。

存储：独立 SQLite 库（派生自主库路径，``{stem}_voiceprints.sqlite3``），WAL 模式。
索引：声纹向量/文本向量 BLOB 为权威数据，sqlite-vec vec0 表为派生索引（与 MemoryStore
      同一范式）；无 sqlite-vec 时降级 Python 余弦全表扫描；FTS5 支撑转写全文检索。

三张主表：
- speakers：说话人档案（姓名/角色/独立阈值/确认状态/累计统计）
- voice_samples：声纹多样本池（每人最多 N 条不同场景样本，FIFO 淘汰最早）
- voice_segments：语音片段（转写文本 + 说话人归属 + 文件内时间戳 + 未读标记）
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from array import array
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiosqlite

from core.config import get_config
from core.log import log

# cam++ 声纹向量维度（FunASR 默认说话人模型输出）
VOICEPRINT_DIMS = 192

_SCHEMA = """
CREATE TABLE IF NOT EXISTS speakers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    speaker_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'confirmed',
    threshold REAL,
    notes TEXT NOT NULL DEFAULT '',
    device_source TEXT NOT NULL DEFAULT '',
    total_audio_ms INTEGER NOT NULL DEFAULT 0,
    first_seen_ns INTEGER NOT NULL,
    last_seen_ns INTEGER NOT NULL,
    match_count INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_speakers_status ON speakers(status, archived);

CREATE TABLE IF NOT EXISTS voice_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    speaker_id INTEGER NOT NULL REFERENCES speakers(id),
    vector BLOB NOT NULL,
    segment_id INTEGER,
    score REAL NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT '',
    created_ns INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_samples_speaker ON voice_samples(speaker_id);

CREATE TABLE IF NOT EXISTS voice_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recording_path TEXT NOT NULL DEFAULT '',
    source_file TEXT NOT NULL DEFAULT '',
    device_source TEXT NOT NULL DEFAULT '',
    start_ms INTEGER NOT NULL DEFAULT 0,
    end_ms INTEGER NOT NULL DEFAULT 0,
    part_start_ms INTEGER NOT NULL DEFAULT 0,
    speaker_id INTEGER,
    is_new_speaker INTEGER NOT NULL DEFAULT 0,
    similarity REAL NOT NULL DEFAULT 0,
    transcript TEXT NOT NULL DEFAULT '',
    transcript_embedding BLOB,
    ts_ns INTEGER NOT NULL,
    read INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_seg_speaker_ts ON voice_segments(speaker_id, ts_ns);
CREATE INDEX IF NOT EXISTS idx_seg_ts ON voice_segments(ts_ns);
CREATE INDEX IF NOT EXISTS idx_seg_read ON voice_segments(read);
CREATE INDEX IF NOT EXISTS idx_seg_recording ON voice_segments(recording_path);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 录制单元登记表（目录镜像同步的增量依据；单元 = 文件夹 或 散装单文件）
CREATE TABLE IF NOT EXISTS recordings (
    path TEXT PRIMARY KEY,
    kind TEXT NOT NULL DEFAULT 'folder',
    fingerprint TEXT NOT NULL DEFAULT '',
    started_ns INTEGER NOT NULL DEFAULT 0,
    file_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'done',
    error TEXT NOT NULL DEFAULT '',
    segments INTEGER NOT NULL DEFAULT 0,
    files_json TEXT NOT NULL DEFAULT '[]',
    synced_ns INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_recordings_started ON recordings(started_ns);
"""

_FTS_TRIGGERS = [
    """CREATE TRIGGER IF NOT EXISTS segments_ai AFTER INSERT ON voice_segments BEGIN
        INSERT INTO segments_fts(rowid, transcript) VALUES (new.id, new.transcript);
    END;""",
    """CREATE TRIGGER IF NOT EXISTS segments_ad AFTER DELETE ON voice_segments BEGIN
        INSERT INTO segments_fts(segments_fts, rowid, transcript)
        VALUES('delete', old.id, old.transcript);
    END;""",
    """CREATE TRIGGER IF NOT EXISTS segments_au AFTER UPDATE OF transcript ON voice_segments BEGIN
        INSERT INTO segments_fts(segments_fts, rowid, transcript)
        VALUES('delete', old.id, old.transcript);
        INSERT INTO segments_fts(rowid, transcript) VALUES (new.id, new.transcript);
    END;""",
]

# 上下文摘要缓存的说话人名单上限
_SUMMARY_NAMES_LIMIT = 12


def _default_db_path() -> str:
    """派生音源库路径：与 MemoryStore 同一目录，stem + '_voiceprints'。"""
    try:
        from agent.storage.sqlite_backend import default_sqlite_path
        main = default_sqlite_path()
    except Exception:
        main = os.path.join("workspace", "data.sqlite3")
    stem, ext = os.path.splitext(main)
    return f"{stem}_voiceprints{ext or '.sqlite3'}"


def _vec_to_blob(vec: List[float]) -> bytes:
    return array("f", vec).tobytes()


def _blob_to_vec(blob: bytes) -> List[float]:
    a = array("f")
    a.frombytes(blob)
    return list(a)


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _mean_vec(vectors: List[List[float]]) -> List[float]:
    """向量均值（质心）：多样本的代表向量。"""
    dims = len(vectors[0])
    acc = [0.0] * dims
    for vec in vectors:
        for i, x in enumerate(vec):
            acc[i] += x
    return [x / len(vectors) for x in acc]


def build_fts_query(raw: str) -> Optional[str]:
    """构建 FTS5 查询：中文 bigram 切分 + 英文原词，OR 组合（与记忆检索同策略）。"""
    raw = raw.strip()
    if not raw:
        return None
    tokens: List[str] = []
    for word in raw.split():
        word = word.replace('"', '""')
        cjk_chars = [ch for ch in word if '一' <= ch <= '鿿']
        if len(cjk_chars) >= 2:
            for i in range(0, len(cjk_chars) - 1, 2):
                end = min(i + 2, len(cjk_chars))
                if end - i >= 2:
                    tokens.append("".join(cjk_chars[i:end]))
            if len(cjk_chars) > 2 and len(cjk_chars) % 2 == 1:
                tokens.append("".join(cjk_chars[-2:]))
        elif len(word) >= 2:
            tokens.append(word)
    if not tokens:
        return None
    seen: set[str] = set()
    unique: List[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    return " OR ".join(f'"{t}"' for t in unique)


def parse_time_ns(expr: str) -> Optional[int]:
    """解析自然时间表达式为纳秒时间戳。

    支持：'YYYY-MM-DD' / 'YYYY-MM-DD HH:MM[:SS]' / epoch 秒/毫秒/纳秒整数字符串。
    空串或无法解析返回 None。
    """
    text = (expr or "").strip()
    if not text:
        return None
    if text.isdigit():
        value = int(text)
        if value > 10**14:  # 纳秒
            return value
        if value > 10**11:  # 毫秒
            return value * 1_000_000
        return value * 1_000_000_000  # 秒
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return int(datetime.strptime(text, fmt).timestamp() * 1_000_000_000)
        except ValueError:
            continue
    return None


def _scalar(row: Optional[aiosqlite.Row], key: str) -> Any:
    """从聚合查询行取标量（COUNT/SUM 恒返回一行，此处仅满足类型收窄）。"""
    assert row is not None
    return row[key]


def _row_to_speaker(row: aiosqlite.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "speaker_key": row["speaker_key"],
        "name": row["name"],
        "role": row["role"],
        "status": row["status"],
        "threshold": row["threshold"],
        "notes": row["notes"],
        "device_source": row["device_source"],
        "total_audio_ms": row["total_audio_ms"],
        "first_seen_ns": row["first_seen_ns"],
        "last_seen_ns": row["last_seen_ns"],
        "match_count": row["match_count"],
        "archived": bool(row["archived"]),
    }


def _row_to_sample(row: aiosqlite.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "speaker_id": row["speaker_id"],
        "segment_id": row["segment_id"],
        "score": row["score"],
        "source": row["source"],
        "created_ns": row["created_ns"],
        "dims": (len(row["vector"]) // 4) if row["vector"] else 0,
    }


def _row_to_segment(row: aiosqlite.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "recording_path": row["recording_path"] if "recording_path" in row.keys() else "",
        "source_file": row["source_file"],
        "device_source": row["device_source"],
        "start_ms": row["start_ms"],
        "part_start_ms": row["part_start_ms"] if "part_start_ms" in row.keys() else 0,
        "end_ms": row["end_ms"],
        "speaker_id": row["speaker_id"],
        "speaker_name": row["speaker_name"] if "speaker_name" in row.keys() else "",
        "speaker_key": row["speaker_key"] if "speaker_key" in row.keys() else "",
        "is_new_speaker": bool(row["is_new_speaker"]),
        "similarity": row["similarity"],
        "transcript": row["transcript"],
        "has_embedding": row["transcript_embedding"] is not None,
        "ts_ns": row["ts_ns"],
        "read": bool(row["read"]),
    }


class VoiceprintStore:
    """声纹库与语音片段库的统一存储（懒初始化单例）。"""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or _default_db_path()
        self._db: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()
        self._vec_available = False
        self.fts_available = False
        # 上下文摘要缓存（写路径置脏，provide 时按需重算，稳态零 I/O）
        self._summary_cache: Optional[Dict[str, Any]] = None
        self._summary_dirty = True

    # ------------------------------------------------------------------
    # 连接与 schema
    # ------------------------------------------------------------------

    async def _get_db(self) -> aiosqlite.Connection:
        existing = self._db
        if existing is not None:
            return existing
        async with self._lock:
            if self._db is None:
                os.makedirs(os.path.dirname(os.path.abspath(self._db_path)), exist_ok=True)
                db = await aiosqlite.connect(self._db_path)
                db.row_factory = aiosqlite.Row
                await db.execute("PRAGMA journal_mode=WAL;")
                await db.execute("PRAGMA synchronous=NORMAL;")
                await db.execute("PRAGMA busy_timeout=5000;")
                self._vec_available = await self._load_vec_extension(db)
                await db.executescript(_SCHEMA)
                await self._init_fts(db)
                await db.commit()
                self._db = db
                log(f"VoiceprintStore 就绪: {self._db_path} "
                    f"(vec={self._vec_available}, fts={self.fts_available})", tag="音源库")
        return self._db

    async def _load_vec_extension(self, db: aiosqlite.Connection) -> bool:
        try:
            import sqlite_vec
            await db.enable_load_extension(True)
            try:
                await db.load_extension(sqlite_vec.loadable_path())
            finally:
                await db.enable_load_extension(False)
            cursor = await db.execute("SELECT vec_version()")
            await cursor.fetchone()
            return True
        except Exception as exc:
            log(f"sqlite-vec 不可用，声纹检索降级为全表扫描: {exc}", "WARNING", tag="音源库")
            return False

    async def _init_fts(self, db: aiosqlite.Connection) -> None:
        """创建 segments_fts 及同步触发器；FTS5 不可用时降级 LIKE 检索。"""
        try:
            await db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts "
                "USING fts5(transcript, content='voice_segments', content_rowid='id', "
                "tokenize='unicode61 remove_diacritics 2')")
            for sql in _FTS_TRIGGERS:
                try:
                    await db.execute(sql)
                except Exception as exc:
                    log(f"FTS 触发器创建: {exc}", "DEBUG", tag="音源库")
            self.fts_available = True
        except Exception as exc:
            log(f"FTS5 不可用，转写检索降级为 LIKE: {exc}", "WARNING", tag="音源库")
            self.fts_available = False

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def initialize(self) -> None:
        """启动时建库建表（bootstrap on_start 钩子调用，幂等）。"""
        await self._get_db()

    def _mark_dirty(self) -> None:
        self._summary_dirty = True

    # ------------------------------------------------------------------
    # 录制单元登记（目录镜像同步：文件夹/散装文件 ↔ 本地资源的增量依据）
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_recording(row: aiosqlite.Row) -> Dict[str, Any]:
        try:
            files = json.loads(row["files_json"]) if row["files_json"] else []
        except Exception:
            files = []
        return {
            "path": row["path"],
            "kind": row["kind"],
            "fingerprint": row["fingerprint"],
            "started_ns": row["started_ns"],
            "file_count": row["file_count"],
            "status": row["status"],
            "error": row["error"],
            "segments": row["segments"],
            "files": files,
            "synced_ns": row["synced_ns"],
        }

    async def get_recording(self, path: str) -> Optional[Dict[str, Any]]:
        """查询录制单元登记（增量判定：fingerprint 一致才视为未变化）。"""
        db = await self._get_db()
        cursor = await db.execute("SELECT * FROM recordings WHERE path=?", (path,))
        row = await cursor.fetchone()
        return self._row_to_recording(row) if row else None

    async def list_recording_paths(self) -> List[str]:
        """全部已登记录制单元路径（镜像删除判定的差集来源）。"""
        db = await self._get_db()
        cursor = await db.execute("SELECT path FROM recordings")
        return [r["path"] for r in await cursor.fetchall()]

    async def mark_recording(
        self,
        path: str,
        *,
        kind: str = "folder",
        fingerprint: str = "",
        started_ns: int = 0,
        file_count: int = 0,
        status: str = "done",
        error: str = "",
        segments: int = 0,
    ) -> None:
        """登记/更新录制单元处理结果。status: done | no_speech | error。"""
        db = await self._get_db()
        await db.execute(
            "INSERT INTO recordings(path, kind, fingerprint, started_ns, file_count, "
            "status, error, segments, synced_ns) VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET kind=excluded.kind, "
            "fingerprint=excluded.fingerprint, started_ns=excluded.started_ns, "
            "file_count=excluded.file_count, status=excluded.status, "
            "error=excluded.error, segments=excluded.segments, synced_ns=excluded.synced_ns",
            (path, kind, fingerprint, started_ns, file_count,
             status, error, segments, time.time_ns()))
        await db.commit()

    async def set_recording_files(self, path: str, files: List[Dict[str, Any]]) -> None:
        """写入录制单元的合并清单（[{path, duration_s}] 按合并顺序，回听定位用）。"""
        db = await self._get_db()
        await db.execute(
            "UPDATE recordings SET files_json=? WHERE path=?",
            (json.dumps(files, ensure_ascii=False), path))
        await db.commit()

    async def list_recordings(
        self, *, limit: int = 50, offset: int = 0,
    ) -> Dict[str, Any]:
        """录制单元清单（按录制时间倒序）。"""
        db = await self._get_db()
        cursor = await db.execute("SELECT COUNT(*) AS c FROM recordings")
        total = int(_scalar(await cursor.fetchone(), "c"))
        cursor = await db.execute(
            "SELECT * FROM recordings ORDER BY started_ns DESC, synced_ns DESC "
            "LIMIT ? OFFSET ?", (limit, offset))
        items = [self._row_to_recording(r) for r in await cursor.fetchall()]
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    async def delete_recording(self, path: str) -> Dict[str, Any]:
        """删除录制单元及其全部衍生资源（镜像同步的删除传播）。

        级联：片段（含 FTS/vec 索引）→ 片段关联的声纹样本（含 vec 索引）→ 登记行。
        说话人档案保留（可能还有其他录制的样本）。
        """
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT id FROM voice_segments WHERE recording_path=?", (path,))
        segment_ids = [r["id"] for r in await cursor.fetchall()]
        sample_ids: List[int] = []
        if segment_ids:
            placeholders = ",".join("?" for _ in segment_ids)
            cursor = await db.execute(
                f"SELECT id FROM voice_samples WHERE segment_id IN ({placeholders})",
                segment_ids)
            sample_ids = [r["id"] for r in await cursor.fetchall()]
            await db.execute(
                f"DELETE FROM voice_samples WHERE segment_id IN ({placeholders})",
                segment_ids)
            await db.execute(
                "DELETE FROM voice_segments WHERE recording_path=?", (path,))
        await db.execute("DELETE FROM recordings WHERE path=?", (path,))
        await db.commit()
        for sid in sample_ids:
            await self._vec_delete("samples", sid)
        for seg_id in segment_ids:
            await self._vec_delete("segments", seg_id)
        self._mark_dirty()
        return {"segments_deleted": len(segment_ids), "samples_deleted": len(sample_ids)}

    # ------------------------------------------------------------------
    # vec0 派生索引（samples 固定 192 维；segments 维度首次回填时确定）
    # ------------------------------------------------------------------

    async def _ensure_vec_table(self, db: aiosqlite.Connection, kind: str, dims: int) -> bool:
        """确保 vec0 索引表存在且维度匹配；维度变更时从 BLOB 重建。返回可用性。"""
        if not self._vec_available:
            return False
        table = f"{kind}_vec"
        meta_key = f"{kind}_vec_dims"
        cursor = await db.execute("SELECT value FROM meta WHERE key=?", (meta_key,))
        row = await cursor.fetchone()
        existing_dims = int(row["value"]) if row else 0

        if existing_dims == dims:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
            if await cursor.fetchone():
                return True

        if existing_dims and existing_dims != dims:
            log(f"{kind} 向量维度变更 {existing_dims}→{dims}，重建 vec 索引", "WARNING", tag="音源库")
            await db.execute(f"DROP TABLE IF EXISTS {table}")

        try:
            await db.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS {table} "
                f"USING vec0(embedding float[{dims}] distance_metric=cosine)")
            await db.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (meta_key, str(dims)))
        except Exception as exc:
            log(f"vec 索引表创建失败（降级全表扫描）: {exc}", "WARNING", tag="音源库")
            self._vec_available = False
            return False

        # 从权威 BLOB 回填派生索引
        main_table = "voice_samples" if kind == "samples" else "voice_segments"
        vec_col = "vector" if kind == "samples" else "transcript_embedding"
        try:
            import sqlite_vec
            cursor = await db.execute(
                f"SELECT id, {vec_col} FROM {main_table} WHERE {vec_col} IS NOT NULL")
            for row in await cursor.fetchall():
                vec = _blob_to_vec(row[vec_col])
                if len(vec) != dims:
                    continue
                await db.execute(
                    f"INSERT OR REPLACE INTO {table}(rowid, embedding) VALUES(?, ?)",
                    (row["id"], sqlite_vec.serialize_float32(vec)))
            await db.commit()
        except Exception as exc:
            log(f"vec 索引回填失败: {exc}", "WARNING", tag="音源库")
        return True

    async def _vec_upsert(self, kind: str, rowid: int, vec: List[float]) -> None:
        db = await self._get_db()
        if not await self._ensure_vec_table(db, kind, len(vec)):
            return
        try:
            import sqlite_vec
            # vec0 虚表的 INSERT OR REPLACE 不一定生效，先删后插保证幂等
            await db.execute(f"DELETE FROM {kind}_vec WHERE rowid=?", (rowid,))
            await db.execute(
                f"INSERT INTO {kind}_vec(rowid, embedding) VALUES(?, ?)",
                (rowid, sqlite_vec.serialize_float32(vec)))
            await db.commit()
        except Exception as exc:
            log(f"vec 写入失败: {exc}", "DEBUG", tag="音源库")

    async def _vec_delete(self, kind: str, rowid: int) -> None:
        if not self._vec_available:
            return
        try:
            db = await self._get_db()
            await db.execute(f"DELETE FROM {kind}_vec WHERE rowid=?", (rowid,))
            await db.commit()
        except Exception:
            log("_vec_delete 异常已忽略", "DEBUG")

    async def _vec_search(
        self, kind: str, query_vec: List[float], limit: int,
    ) -> Optional[List[Dict[str, Any]]]:
        """vec0 KNN 检索，返回 [{id, score}]；不可用返回 None。"""
        if not self._vec_available:
            return None
        db = await self._get_db()
        if not await self._ensure_vec_table(db, kind, len(query_vec)):
            return None
        try:
            import sqlite_vec
            cursor = await db.execute(
                f"SELECT rowid, distance FROM {kind}_vec "
                f"WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                (sqlite_vec.serialize_float32(query_vec), limit))
            rows = await cursor.fetchall()
            return [{"id": r["rowid"], "score": round(1.0 - r["distance"], 4)} for r in rows]
        except Exception as exc:
            log(f"vec 检索失败（降级全表扫描）: {exc}", "WARNING", tag="音源库")
            return None

    # ------------------------------------------------------------------
    # 说话人档案
    # ------------------------------------------------------------------

    async def _next_speaker_seq(self, db: aiosqlite.Connection) -> int:
        cursor = await db.execute("SELECT value FROM meta WHERE key='speaker_seq'")
        row = await cursor.fetchone()
        seq = int(row["value"]) + 1 if row else 1
        await db.execute(
            "INSERT INTO meta(key, value) VALUES('speaker_seq', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(seq),))
        return seq

    async def create_speaker(
        self,
        *,
        name: str = "",
        role: str = "",
        status: str = "confirmed",
        threshold: Optional[float] = None,
        notes: str = "",
        device_source: str = "",
    ) -> Dict[str, Any]:
        """创建说话人档案，返回完整记录。status: confirmed | pending。"""
        db = await self._get_db()
        now = time.time_ns()
        seq = await self._next_speaker_seq(db)
        prefix = "spk_tmp" if status == "pending" else "spk"
        speaker_key = f"{prefix}_{seq:04d}"
        cursor = await db.execute(
            "INSERT INTO speakers(speaker_key, name, role, status, threshold, notes, "
            "device_source, total_audio_ms, first_seen_ns, last_seen_ns, match_count, archived) "
            "VALUES(?,?,?,?,?,?,?,0,?,?,0,0)",
            (speaker_key, name, role, status, threshold, notes, device_source, now, now))
        await db.commit()
        self._mark_dirty()
        assert cursor.lastrowid is not None
        speaker = await self.get_speaker(int(cursor.lastrowid))
        assert speaker is not None
        return speaker

    async def get_speaker(self, speaker_id: int) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT * FROM speakers WHERE id=? AND archived=0", (speaker_id,))
        row = await cursor.fetchone()
        return _row_to_speaker(row) if row else None

    async def get_speaker_by_key(self, speaker_key: str) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT * FROM speakers WHERE speaker_key=? AND archived=0", (speaker_key,))
        row = await cursor.fetchone()
        return _row_to_speaker(row) if row else None

    async def find_speakers(self, ref: str) -> List[Dict[str, Any]]:
        """按引用解析说话人：数字 id / speaker_key / 姓名精确 / 姓名模糊，按相关度返回。"""
        ref = (ref or "").strip()
        if not ref:
            return []
        db = await self._get_db()
        if ref.isdigit():
            speaker = await self.get_speaker(int(ref))
            return [speaker] if speaker else []
        speaker = await self.get_speaker_by_key(ref)
        if speaker:
            return [speaker]
        cursor = await db.execute(
            "SELECT * FROM speakers WHERE archived=0 AND name=? ORDER BY last_seen_ns DESC",
            (ref,))
        rows = await cursor.fetchall()
        if rows:
            return [_row_to_speaker(r) for r in rows]
        cursor = await db.execute(
            "SELECT * FROM speakers WHERE archived=0 AND (name LIKE ? OR role LIKE ?) "
            "ORDER BY last_seen_ns DESC LIMIT 10",
            (f"%{ref}%", f"%{ref}%"))
        return [_row_to_speaker(r) for r in await cursor.fetchall()]

    _UPDATABLE_FIELDS = ("name", "role", "status", "threshold", "notes", "device_source")

    async def update_speaker(self, speaker_id: int, **fields: Any) -> Optional[Dict[str, Any]]:
        """更新说话人档案（白名单字段），返回更新后的记录；不存在返回 None。"""
        current = await self.get_speaker(speaker_id)
        if not current:
            return None
        updates: List[str] = []
        values: List[Any] = []
        for key in self._UPDATABLE_FIELDS:
            if key in fields and fields[key] is not None:
                updates.append(f"{key}=?")
                values.append(fields[key])
        if updates:
            # 待确认 → 已确认时同步刷新 speaker_key 前缀
            if fields.get("status") == "confirmed" and current["speaker_key"].startswith("spk_tmp"):
                db = await self._get_db()
                seq = await self._next_speaker_seq(db)
                updates.append("speaker_key=?")
                values.append(f"spk_{seq:04d}")
            db = await self._get_db()
            values.append(speaker_id)
            await db.execute(f"UPDATE speakers SET {', '.join(updates)} WHERE id=?", values)
            await db.commit()
            self._mark_dirty()
        return await self.get_speaker(speaker_id)

    async def delete_speaker(self, speaker_id: int) -> Optional[Dict[str, Any]]:
        """删除说话人（级联）：档案 + 声纹样本池 + 其全部话语片段一并删除。

        删除即整体移除相关内容——不产生"未知说话人"孤儿片段；
        归属调整请走合并（speaker_merge/consolidate）或逐段改派。
        """
        current = await self.get_speaker(speaker_id)
        if not current:
            return None
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT id FROM voice_samples WHERE speaker_id=?", (speaker_id,))
        sample_ids = [r["id"] for r in await cursor.fetchall()]
        cursor = await db.execute(
            "SELECT id FROM voice_segments WHERE speaker_id=?", (speaker_id,))
        segment_ids = [r["id"] for r in await cursor.fetchall()]
        await db.execute("DELETE FROM voice_samples WHERE speaker_id=?", (speaker_id,))
        await db.execute("DELETE FROM voice_segments WHERE speaker_id=?", (speaker_id,))
        await db.execute("DELETE FROM speakers WHERE id=?", (speaker_id,))
        await db.commit()
        for sid in sample_ids:
            await self._vec_delete("samples", sid)
        for seg_id in segment_ids:
            await self._vec_delete("segments", seg_id)
        self._mark_dirty()
        return current

    async def archive_speaker(self, speaker_id: int) -> Optional[Dict[str, Any]]:
        """软归档说话人：样本从检索中移除，片段归属保留。"""
        current = await self.get_speaker(speaker_id)
        if not current:
            return None
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT id FROM voice_samples WHERE speaker_id=?", (speaker_id,))
        sample_ids = [r["id"] for r in await cursor.fetchall()]
        await db.execute("UPDATE speakers SET archived=1 WHERE id=?", (speaker_id,))
        await db.execute("DELETE FROM voice_samples WHERE speaker_id=?", (speaker_id,))
        await db.commit()
        for sid in sample_ids:
            await self._vec_delete("samples", sid)
        self._mark_dirty()
        result = dict(current)
        result["archived"] = True
        return result

    async def touch_speaker_match(self, speaker_id: int, audio_ms: int, ts_ns: int) -> None:
        """命中回写：累计匹配次数/有效音频时长/最近出现时间。"""
        db = await self._get_db()
        await db.execute(
            "UPDATE speakers SET match_count=match_count+1, "
            "total_audio_ms=total_audio_ms+?, last_seen_ns=? WHERE id=?",
            (max(0, audio_ms), ts_ns, speaker_id))
        await db.commit()

    async def prune_pending_speakers(
        self, *, include_with_samples: bool = False,
    ) -> List[Dict[str, Any]]:
        """批量剔除临时（pending）说话人。

        include_with_samples=False：只清理无样本且**无片段**的空壳档案（安全默认，
        有片段归属的 pending 绝不动，防归属被误置未知）；
        include_with_samples=True：剔除全部 pending（样本一并删除，片段重指未知）。
        返回被删除的说话人简报列表。
        """
        db = await self._get_db()
        if include_with_samples:
            cursor = await db.execute(
                "SELECT * FROM speakers WHERE archived=0 AND status='pending'")
        else:
            cursor = await db.execute(
                "SELECT s.* FROM speakers s WHERE s.archived=0 AND s.status='pending' "
                "AND NOT EXISTS (SELECT 1 FROM voice_samples v WHERE v.speaker_id=s.id) "
                "AND NOT EXISTS (SELECT 1 FROM voice_segments g WHERE g.speaker_id=s.id)")
        candidates = [_row_to_speaker(r) for r in await cursor.fetchall()]
        deleted: List[Dict[str, Any]] = []
        for speaker in candidates:
            result = await self.delete_speaker(int(speaker["id"]))
            if result:
                deleted.append({
                    "id": speaker["id"], "speaker_key": speaker["speaker_key"],
                    "name": speaker["name"],
                })
        return deleted

    async def list_speakers(
        self, *, status: str = "", keyword: str = "", limit: int = 50, offset: int = 0,
    ) -> Dict[str, Any]:
        """说话人列表（含样本数），支持状态过滤与姓名/角色关键字。"""
        db = await self._get_db()
        where = ["archived=0"]
        params: List[Any] = []
        if status:
            where.append("status=?")
            params.append(status)
        if keyword:
            where.append("(name LIKE ? OR role LIKE ? OR speaker_key LIKE ?)")
            like = f"%{keyword}%"
            params.extend([like, like, like])
        where_sql = " AND ".join(where)
        cursor = await db.execute(f"SELECT COUNT(*) AS c FROM speakers WHERE {where_sql}", params)
        total = int(_scalar(await cursor.fetchone(), "c"))
        cursor = await db.execute(
            f"SELECT s.*, (SELECT COUNT(*) FROM voice_samples v WHERE v.speaker_id=s.id) "
            f"AS sample_count FROM speakers s WHERE {where_sql} "
            f"ORDER BY s.last_seen_ns DESC LIMIT ? OFFSET ?",
            (*params, limit, offset))
        items = []
        for row in await cursor.fetchall():
            item = _row_to_speaker(row)
            item["sample_count"] = row["sample_count"]
            items.append(item)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    # ------------------------------------------------------------------
    # 声纹样本池
    # ------------------------------------------------------------------

    async def add_sample(
        self,
        speaker_id: int,
        vector: List[float],
        *,
        segment_id: Optional[int] = None,
        score: float = 0.0,
        source: str = "",
        max_samples: int = 5,
    ) -> int:
        """样本入池，返回样本 id（新样本被判为极端样本时返回 -1 未入池）。

        淘汰策略（voiceprint_sample_evict_strategy）：
        - outlier（默认）：池满时计算全部候选（现有+新样本）的质心，
          淘汰与质心相似度最低的极端样本——可能是新样本本身（噪音拒入），
          样本池随使用自我优化、保持代表性
        - fifo：淘汰最早样本（时间先进先出）
        """
        db = await self._get_db()
        strategy = str(get_config("voiceprint_sample_evict_strategy", "outlier")
                       or "outlier")

        if strategy == "outlier":
            cursor = await db.execute(
                "SELECT id, vector FROM voice_samples WHERE speaker_id=? "
                "ORDER BY created_ns ASC", (speaker_id,))
            existing = [(int(r["id"]), _blob_to_vec(r["vector"]))
                    for r in await cursor.fetchall()]
            if len(existing) >= max(1, max_samples):
                candidates: List[tuple[Optional[int], List[float]]] = [
                    *existing, (None, vector)]
                centroid = _mean_vec([v for _, v in candidates])
                worst_id: Optional[int] = None
                worst_is_new = False
                worst_sim = 2.0
                for cand_id, cand_vec in candidates:
                    sim = _cosine(cand_vec, centroid)
                    if sim < worst_sim:
                        worst_sim = sim
                        worst_id = cand_id
                        worst_is_new = cand_id is None
                if worst_is_new:
                    log(f"新样本为极端样本（质心相似度 {worst_sim:.3f}），拒绝入池",
                        "DEBUG", tag="音源库")
                    return -1
                await db.execute(
                    "DELETE FROM voice_samples WHERE id=?", (worst_id,))
                await db.commit()
                if worst_id is not None:
                    await self._vec_delete("samples", worst_id)

        now = time.time_ns()
        cursor = await db.execute(
            "INSERT INTO voice_samples(speaker_id, vector, segment_id, score, source, created_ns) "
            "VALUES(?,?,?,?,?,?)",
            (speaker_id, _vec_to_blob(vector), segment_id, score, source, now))
        assert cursor.lastrowid is not None
        sample_id = int(cursor.lastrowid)

        if strategy != "outlier":
            # FIFO 淘汰最早样本
            cursor = await db.execute(
                "SELECT id FROM voice_samples WHERE speaker_id=? ORDER BY created_ns DESC",
                (speaker_id,))
            all_ids = [r["id"] for r in await cursor.fetchall()]
            evict_ids = all_ids[max(1, max_samples):]
            if evict_ids:
                placeholders = ",".join("?" for _ in evict_ids)
                await db.execute(
                    f"DELETE FROM voice_samples WHERE id IN ({placeholders})", evict_ids)
                await db.commit()
                for evict_id in evict_ids:
                    await self._vec_delete("samples", evict_id)
        else:
            await db.commit()
        await self._vec_upsert("samples", sample_id, vector)
        return sample_id

    async def get_sample_vector(self, sample_id: int) -> Optional[List[float]]:
        """读取单条样本的声纹向量（合并迁移用）。"""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT vector FROM voice_samples WHERE id=?", (sample_id,))
        row = await cursor.fetchone()
        return _blob_to_vec(row["vector"]) if row else None

    async def get_speaker_vectors(self, speaker_id: int) -> List[List[float]]:
        """读取说话人全部样本向量（质心计算用）。"""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT vector FROM voice_samples WHERE speaker_id=?", (speaker_id,))
        return [_blob_to_vec(r["vector"]) for r in await cursor.fetchall()]

    async def reassign_segments(self, from_speaker_id: int, to_speaker_id: int) -> int:
        """批量改派片段归属（身份合并用），返回影响行数。"""
        db = await self._get_db()
        cursor = await db.execute(
            "UPDATE voice_segments SET speaker_id=? WHERE speaker_id=?",
            (to_speaker_id, from_speaker_id))
        await db.commit()
        return cursor.rowcount

    async def merge_speaker_stats(self, target_id: int, source: Dict[str, Any]) -> None:
        """把 source 档案的统计量（时长/命中数/最近出现）累加进 target。"""
        db = await self._get_db()
        await db.execute(
            "UPDATE speakers SET total_audio_ms=total_audio_ms+?, "
            "match_count=match_count+?, last_seen_ns=MAX(last_seen_ns, ?) WHERE id=?",
            (source["total_audio_ms"], source["match_count"],
             source["last_seen_ns"], target_id))
        await db.commit()

    async def list_samples(self, speaker_id: int) -> List[Dict[str, Any]]:
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT * FROM voice_samples WHERE speaker_id=? ORDER BY created_ns DESC",
            (speaker_id,))
        return [_row_to_sample(r) for r in await cursor.fetchall()]

    async def delete_sample(self, sample_id: int) -> bool:
        db = await self._get_db()
        cursor = await db.execute("DELETE FROM voice_samples WHERE id=?", (sample_id,))
        await db.commit()
        if cursor.rowcount:
            await self._vec_delete("samples", sample_id)
            return True
        return False

    async def search_sample_vectors(
        self, query_vec: List[float], limit: int = 25,
    ) -> List[Dict[str, Any]]:
        """样本级 KNN：返回 [{sample_id, speaker_id, score}]，vec 不可用时全表余弦降级。"""
        db = await self._get_db()
        hits = await self._vec_search("samples", query_vec, limit)
        if hits is not None:
            if not hits:
                return []
            result = []
            for hit in hits:
                cursor = await db.execute(
                    "SELECT speaker_id FROM voice_samples WHERE id=?", (hit["id"],))
                row = await cursor.fetchone()
                if row:
                    result.append({
                        "sample_id": hit["id"],
                        "speaker_id": row["speaker_id"],
                        "score": hit["score"],
                    })
            return result
        # 降级：Python 全表余弦
        cursor = await db.execute("SELECT id, speaker_id, vector FROM voice_samples")
        scored: List[Dict[str, Any]] = []
        for row in await cursor.fetchall():
            score = _cosine(query_vec, _blob_to_vec(row["vector"]))
            if score > 0:
                scored.append({
                    "sample_id": row["id"],
                    "speaker_id": row["speaker_id"],
                    "score": round(score, 4),
                })
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:limit]

    # ------------------------------------------------------------------
    # 语音片段
    # ------------------------------------------------------------------

    _SEGMENT_SELECT = (
        "SELECT seg.*, spk.name AS speaker_name, spk.speaker_key AS speaker_key "
        "FROM voice_segments seg LEFT JOIN speakers spk ON spk.id=seg.speaker_id"
    )

    async def add_segment(
        self,
        *,
        recording_path: str = "",
        source_file: str = "",
        device_source: str = "",
        start_ms: int = 0,
        end_ms: int = 0,
        part_start_ms: int = 0,
        speaker_id: Optional[int] = None,
        is_new_speaker: bool = False,
        similarity: float = 0.0,
        transcript: str = "",
        ts_ns: Optional[int] = None,
    ) -> int:
        """新增语音片段（默认未读），返回片段 id。"""
        db = await self._get_db()
        cursor = await db.execute(
            "INSERT INTO voice_segments(recording_path, source_file, device_source, "
            "start_ms, end_ms, part_start_ms, speaker_id, is_new_speaker, similarity, "
            "transcript, ts_ns, read) VALUES(?,?,?,?,?,?,?,?,?,?,?,0)",
            (recording_path, source_file, device_source, start_ms, end_ms,
             part_start_ms, speaker_id,
             1 if is_new_speaker else 0, similarity, transcript,
             ts_ns if ts_ns is not None else time.time_ns()))
        await db.commit()
        self._mark_dirty()
        assert cursor.lastrowid is not None
        return int(cursor.lastrowid)

    async def attach_segment_to_latest_sample(
        self, speaker_id: int, segment_id: int,
    ) -> None:
        """把该说话人最新一条未关联样本挂到片段上（录制删除时的级联依据）。"""
        db = await self._get_db()
        await db.execute(
            "UPDATE voice_samples SET segment_id=? WHERE id = ("
            "SELECT id FROM voice_samples WHERE speaker_id=? AND segment_id IS NULL "
            "ORDER BY created_ns DESC LIMIT 1)",
            (segment_id, speaker_id))
        await db.commit()

    async def get_segment(self, segment_id: int) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        cursor = await db.execute(f"{self._SEGMENT_SELECT} WHERE seg.id=?", (segment_id,))
        row = await cursor.fetchone()
        return _row_to_segment(row) if row else None

    async def update_segment_speaker(
        self, segment_id: int, speaker_id: Optional[int],
    ) -> Optional[Dict[str, Any]]:
        """改派片段说话人归属（AI/用户编辑），is_new_speaker 标记同步清除。"""
        current = await self.get_segment(segment_id)
        if not current:
            return None
        db = await self._get_db()
        await db.execute(
            "UPDATE voice_segments SET speaker_id=?, is_new_speaker=0 WHERE id=?",
            (speaker_id, segment_id))
        await db.commit()
        return await self.get_segment(segment_id)

    async def update_transcript(self, segment_id: int, transcript: str) -> Optional[Dict[str, Any]]:
        """修订片段转写文本（FTS 触发器自动同步；文本向量置空待回填）。"""
        current = await self.get_segment(segment_id)
        if not current:
            return None
        db = await self._get_db()
        await db.execute(
            "UPDATE voice_segments SET transcript=?, transcript_embedding=NULL WHERE id=?",
            (transcript, segment_id))
        await db.commit()
        await self._vec_delete("segments", segment_id)
        return await self.get_segment(segment_id)

    async def replace_in_transcripts(
        self,
        find: str,
        replace: str,
        *,
        speaker_id: Optional[int] = None,
        from_ns: Optional[int] = None,
        to_ns: Optional[int] = None,
        limit: int = 500,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """批量查找替换转写文本（人名/术语纠错的主力路径）。

        命中：transcript LIKE %find%（+说话人/时间硬过滤，按时间正序限 limit 条）。
        替换后文本向量置空待回填（FTS 由触发器同步）。dry_run 只统计不写入。
        """
        find = find.strip()
        if not find:
            return {"matched": 0, "changed": 0, "samples": []}
        db = await self._get_db()
        where = ["transcript LIKE ?"]
        params: List[Any] = [f"%{find}%"]
        if speaker_id is not None:
            where.append("speaker_id=?")
            params.append(speaker_id)
        if from_ns is not None:
            where.append("ts_ns>=?")
            params.append(from_ns)
        if to_ns is not None:
            where.append("ts_ns<=?")
            params.append(to_ns)
        cursor = await db.execute(
            f"SELECT id, transcript FROM voice_segments WHERE {' AND '.join(where)} "
            f"ORDER BY ts_ns ASC LIMIT ?", (*params, max(1, min(limit, 2000))))
        rows = await cursor.fetchall()

        changed_ids: List[int] = []
        samples: List[Dict[str, Any]] = []
        for row in rows:
            old = str(row["transcript"])
            if find not in old:
                continue
            new = old.replace(find, replace)
            changed_ids.append(int(row["id"]))
            if len(samples) < 5:
                samples.append({"id": int(row["id"]), "before": old, "after": new})
            if not dry_run:
                await db.execute(
                    "UPDATE voice_segments SET transcript=?, transcript_embedding=NULL "
                    "WHERE id=?", (new, row["id"]))
        if changed_ids and not dry_run:
            await db.commit()
            for seg_id in changed_ids:
                await self._vec_delete("segments", seg_id)
            try:
                from agent.memory.embedding import wake_embedding_worker
                wake_embedding_worker()
            except Exception as exc:
                log(f"embedding worker 唤醒失败: {exc}", "DEBUG", tag="音源库")
        return {
            "matched": len(changed_ids),
            "changed": 0 if dry_run else len(changed_ids),
            "samples": samples,
            "ids": changed_ids if not dry_run else [],
        }

    async def merge_segments(
        self,
        segment_ids: List[int],
        *,
        transcript: Optional[str] = None,
        speaker_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """合并多个相邻片段为一条（转写碎片归并）。

        保留首条：文本拼接（或自定义）、时间跨度取首 start~尾 end、
        归属为首条（或指定说话人）、向量置空待重建；其余片段删除。
        限同一录制单元内的片段（跨录制合并会破坏镜像删除语义）。
        """
        if len(segment_ids) < 2:
            return None
        segments: List[Dict[str, Any]] = []
        for seg_id in segment_ids:
            seg = await self.get_segment(int(seg_id))
            if not seg:
                return None
            segments.append(seg)
        recordings = {s["recording_path"] for s in segments}
        if len(recordings) > 1:
            raise ValueError("只能合并同一录制单元内的片段")

        first = segments[0]
        last = segments[-1]
        merged_text = transcript if transcript is not None else " ".join(
            s["transcript"].strip() for s in segments if s["transcript"].strip())
        merged_speaker = speaker_id if speaker_id is not None else first["speaker_id"]

        db = await self._get_db()
        await db.execute(
            "UPDATE voice_segments SET transcript=?, transcript_embedding=NULL, "
            "start_ms=?, end_ms=?, speaker_id=?, is_new_speaker=?, read=? WHERE id=?",
            (merged_text, first["start_ms"], last["end_ms"], merged_speaker,
             1 if first["is_new_speaker"] else 0,
             0 if any(not s["read"] for s in segments) else 1,
             first["id"]))
        rest_ids = [s["id"] for s in segments[1:]]
        placeholders = ",".join("?" for _ in rest_ids)
        await db.execute(
            f"DELETE FROM voice_segments WHERE id IN ({placeholders})", rest_ids)
        await db.commit()
        await self._vec_delete("segments", int(first["id"]))
        for seg_id in rest_ids:
            await self._vec_delete("segments", int(seg_id))
        self._mark_dirty()
        return await self.get_segment(int(first["id"]))

    async def split_segment(
        self,
        segment_id: int,
        at_ms: int,
        *,
        text_first: Optional[str] = None,
        text_second: Optional[str] = None,
        speaker_second_id: Optional[int] = None,
        speaker_second_set: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """把片段在指定时间点拆为两段（一段含多人话语/切错了边界时用）。

        首段：保留原 id，end_ms=at_ms（可换文本）；次段：新建片段，
        时间/录制/批偏移继承原段（ts 按切点偏移顺延），归属可指定
        （默认继承原归属，speaker_second_set=True 且 None 时置为未知）。
        两者语义向量均置空待重建。
        """
        segment = await self.get_segment(segment_id)
        if not segment:
            return None
        if not int(segment["start_ms"]) < at_ms < int(segment["end_ms"]):
            raise ValueError(
                f"切点 {at_ms}ms 须在片段区间 ({segment['start_ms']}, {segment['end_ms']}) 内")

        db = await self._get_db()
        await db.execute(
            "UPDATE voice_segments SET end_ms=?, transcript=?, transcript_embedding=NULL "
            "WHERE id=?",
            (at_ms, text_first if text_first is not None else segment["transcript"],
             segment_id))
        second_speaker = (speaker_second_id if speaker_second_set
                          else segment["speaker_id"])
        second_ts = int(segment["ts_ns"]) + (at_ms - int(segment["start_ms"])) * 1_000_000
        cursor = await db.execute(
            "INSERT INTO voice_segments(recording_path, source_file, device_source, "
            "start_ms, end_ms, part_start_ms, speaker_id, is_new_speaker, similarity, "
            "transcript, ts_ns, read) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (segment["recording_path"], segment["source_file"], segment["device_source"],
             at_ms, segment["end_ms"], segment["part_start_ms"], second_speaker,
             1 if segment["is_new_speaker"] else 0, segment["similarity"],
             text_second if text_second is not None else segment["transcript"],
             second_ts, 1 if not segment["read"] else 0))
        await db.commit()
        await self._vec_delete("segments", segment_id)
        self._mark_dirty()
        assert cursor.lastrowid is not None
        return {
            "first": await self.get_segment(segment_id),
            "second": await self.get_segment(int(cursor.lastrowid)),
        }

    async def delete_segment(self, segment_id: int) -> bool:
        db = await self._get_db()
        cursor = await db.execute("DELETE FROM voice_segments WHERE id=?", (segment_id,))
        await db.commit()
        if cursor.rowcount:
            await self._vec_delete("segments", segment_id)
            self._mark_dirty()
            return True
        return False

    async def list_segments(
        self,
        *,
        speaker_id: Optional[int] = None,
        recording_path: str = "",
        from_ns: Optional[int] = None,
        to_ns: Optional[int] = None,
        unread_only: bool = False,
        limit: int = 20,
        offset: int = 0,
        order: str = "desc",
    ) -> Dict[str, Any]:
        """片段时间线查询（说话人/录制/时间范围/未读硬过滤）。

        order: 'desc' 最新在前（默认）/ 'asc' 时间正序（时间线视图）。
        """
        db = await self._get_db()
        where = ["1=1"]
        params: List[Any] = []
        if speaker_id is not None:
            where.append("seg.speaker_id=?")
            params.append(speaker_id)
        if recording_path:
            where.append("seg.recording_path=?")
            params.append(recording_path)
        if from_ns is not None:
            where.append("seg.ts_ns>=?")
            params.append(from_ns)
        if to_ns is not None:
            where.append("seg.ts_ns<=?")
            params.append(to_ns)
        if unread_only:
            where.append("seg.read=0")
        where_sql = " AND ".join(where)
        order_sql = "ASC" if order == "asc" else "DESC"
        cursor = await db.execute(
            f"SELECT COUNT(*) AS c FROM voice_segments seg WHERE {where_sql}", params)
        total = int(_scalar(await cursor.fetchone(), "c"))
        cursor = await db.execute(
            f"{self._SEGMENT_SELECT} WHERE {where_sql} "
            f"ORDER BY seg.ts_ns {order_sql} LIMIT ? OFFSET ?",
            (*params, limit, offset))
        items = [_row_to_segment(r) for r in await cursor.fetchall()]
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    async def search_segments(
        self,
        query: str,
        *,
        query_vec: Optional[List[float]] = None,
        speaker_id: Optional[int] = None,
        from_ns: Optional[int] = None,
        to_ns: Optional[int] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """转写混合检索：FTS5/向量双路召回 + 说话人/时间硬过滤。

        评分：0.6 × 语义分 + 0.4 × FTS 归一分；query 为空时退化为时间线查询。
        """
        if not query.strip():
            result = await self.list_segments(
                speaker_id=speaker_id, from_ns=from_ns, to_ns=to_ns, limit=limit)
            return result["items"]

        db = await self._get_db()
        candidate_ids: set[int] = set()
        scores: Dict[int, float] = {}

        # 向量召回
        if query_vec:
            vec_hits = await self._vec_search("segments", query_vec, limit * 3)
            if vec_hits is None:
                vec_hits = await self._python_segment_scan(query_vec, limit * 3)
            for hit in vec_hits:
                candidate_ids.add(hit["id"])
                scores[hit["id"]] = max(scores.get(hit["id"], 0.0), 0.6 * hit["score"])

        # FTS 召回（不可用时 LIKE）
        fts_query = build_fts_query(query)
        fts_rows: List[aiosqlite.Row] = []
        if fts_query and self.fts_available:
            try:
                cursor = await db.execute(
                    "SELECT rowid, rank FROM segments_fts WHERE segments_fts MATCH ? "
                    "ORDER BY rank LIMIT ?",
                    (fts_query, limit * 3))
                fts_rows = list(await cursor.fetchall())
            except Exception as exc:
                log(f"FTS 检索失败（降级 LIKE）: {exc}", "DEBUG", tag="音源库")
                self.fts_available = False
        if not fts_rows:
            like = f"%{query.strip()}%"
            cursor = await db.execute(
                "SELECT id, 0.0 AS rank FROM voice_segments WHERE transcript LIKE ? LIMIT ?",
                (like, limit * 3))
            fts_rows = list(await cursor.fetchall())
        fts_count = len(fts_rows)
        for idx, row in enumerate(fts_rows):
            rowid = row["rowid"] if "rowid" in row.keys() else row["id"]
            # FTS rank 为负数（越小越相关），用位置归一化为 0..1
            norm = 1.0 - (idx / fts_count) if fts_count else 0.0
            candidate_ids.add(rowid)
            scores[rowid] = scores.get(rowid, 0.0) + 0.4 * norm

        if not candidate_ids:
            return []

        # 硬过滤 + 组装
        placeholders = ",".join("?" for _ in candidate_ids)
        where = [f"seg.id IN ({placeholders})"]
        params: List[Any] = list(candidate_ids)
        if speaker_id is not None:
            where.append("seg.speaker_id=?")
            params.append(speaker_id)
        if from_ns is not None:
            where.append("seg.ts_ns>=?")
            params.append(from_ns)
        if to_ns is not None:
            where.append("seg.ts_ns<=?")
            params.append(to_ns)
        cursor = await db.execute(
            f"{self._SEGMENT_SELECT} WHERE {' AND '.join(where)}", params)
        results = []
        for row in await cursor.fetchall():
            item = _row_to_segment(row)
            item["score"] = round(scores.get(item["id"], 0.0), 4)
            results.append(item)
        results.sort(key=lambda x: (x["score"], x["ts_ns"]), reverse=True)
        return results[:limit]

    async def _python_segment_scan(
        self, query_vec: List[float], limit: int,
    ) -> List[Dict[str, Any]]:
        """vec 索引不可用时的片段向量全表余弦降级。"""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT id, transcript_embedding FROM voice_segments "
            "WHERE transcript_embedding IS NOT NULL")
        scored: List[Dict[str, Any]] = []
        for row in await cursor.fetchall():
            score = _cosine(query_vec, _blob_to_vec(row["transcript_embedding"]))
            if score > 0.05:
                scored.append({"id": row["id"], "score": round(score, 4)})
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:limit]

    async def mark_read(self, segment_ids: Optional[List[int]] = None) -> int:
        """标记片段已读；segment_ids 为 None 时全部已读。返回影响行数。"""
        db = await self._get_db()
        if segment_ids is None:
            cursor = await db.execute("UPDATE voice_segments SET read=1 WHERE read=0")
        elif segment_ids:
            placeholders = ",".join("?" for _ in segment_ids)
            cursor = await db.execute(
                f"UPDATE voice_segments SET read=1 WHERE id IN ({placeholders})", segment_ids)
        else:
            return 0
        await db.commit()
        self._mark_dirty()
        return cursor.rowcount

    async def unread_count(self) -> int:
        db = await self._get_db()
        cursor = await db.execute("SELECT COUNT(*) AS c FROM voice_segments WHERE read=0")
        return int(_scalar(await cursor.fetchone(), "c"))

    # ------------------------------------------------------------------
    # 转写文本向量（后台回填）
    # ------------------------------------------------------------------

    async def list_missing_transcript_embeddings(self, limit: int) -> List[Dict[str, Any]]:
        """列出待回填文本向量的片段（id + transcript）。"""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT id, transcript FROM voice_segments "
            "WHERE transcript_embedding IS NULL AND transcript != '' LIMIT ?", (limit,))
        return [{"id": r["id"], "transcript": r["transcript"]} for r in await cursor.fetchall()]

    async def set_transcript_embedding(self, segment_id: int, vec: List[float]) -> None:
        """写入片段文本向量并同步 vec 索引。"""
        db = await self._get_db()
        await db.execute(
            "UPDATE voice_segments SET transcript_embedding=? WHERE id=?",
            (_vec_to_blob(vec), segment_id))
        await db.commit()
        await self._vec_upsert("segments", segment_id, vec)

    # ------------------------------------------------------------------
    # 统计与上下文摘要
    # ------------------------------------------------------------------

    async def stats(self) -> Dict[str, Any]:
        """库总览统计。"""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT COUNT(*) AS c, "
            "SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending "
            "FROM speakers WHERE archived=0")
        row = await cursor.fetchone()
        assert row is not None
        cursor = await db.execute("SELECT COUNT(*) AS c FROM voice_samples")
        samples = int(_scalar(await cursor.fetchone(), "c"))
        cursor = await db.execute(
            "SELECT COUNT(*) AS c, SUM(CASE WHEN read=0 THEN 1 ELSE 0 END) AS unread "
            "FROM voice_segments")
        seg = await cursor.fetchone()
        assert seg is not None
        cursor = await db.execute(
            "SELECT COUNT(*) AS c FROM voice_segments WHERE transcript_embedding IS NULL "
            "AND transcript != ''")
        missing_embed = int(_scalar(await cursor.fetchone(), "c"))
        cursor = await db.execute("SELECT COUNT(*) AS c FROM recordings")
        recordings = int(_scalar(await cursor.fetchone(), "c"))
        return {
            "speakers": row["c"] or 0,
            "pending_speakers": row["pending"] or 0,
            "samples": samples,
            "segments": seg["c"] or 0,
            "unread_segments": seg["unread"] or 0,
            "missing_embeddings": missing_embed,
            "recordings": recordings,
            "vec_available": self._vec_available,
            "fts_available": self.fts_available,
        }

    async def summary(self) -> Dict[str, Any]:
        """上下文注入用摘要（内存缓存 + 写路径置脏，稳态零 I/O）。"""
        if self._summary_cache is not None and not self._summary_dirty:
            return self._summary_cache
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT name, role FROM speakers "
            "WHERE archived=0 AND status='confirmed' AND name != '' "
            "ORDER BY last_seen_ns DESC LIMIT ?", (_SUMMARY_NAMES_LIMIT,))
        names = [
            f"{r['name']}({r['role']})" if r["role"] else r["name"]
            for r in await cursor.fetchall()
        ]
        cursor = await db.execute(
            "SELECT COUNT(*) AS c FROM speakers WHERE archived=0 AND status='pending'")
        pending = int(_scalar(await cursor.fetchone(), "c"))
        unread = await self.unread_count()
        self._summary_cache = {
            "confirmed_names": names,
            "pending_count": pending,
            "unread_count": unread,
        }
        self._summary_dirty = False
        return self._summary_cache


# ------------------------------------------------------------------
# 单例
# ------------------------------------------------------------------

_store: Optional[VoiceprintStore] = None


def get_voiceprint_store() -> VoiceprintStore:
    """获取 VoiceprintStore 单例（首次调用时创建，DB 连接懒初始化）。"""
    global _store
    if _store is None:
        _store = VoiceprintStore()
    return _store
