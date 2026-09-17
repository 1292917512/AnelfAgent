"""AudioStore：音频核心库的统一存储（片段 / 声纹身份 / 样本池 / 录制单元）。

定位：全部音频解析产物（转写片段、说话人身份、声纹样本、录制单元登记）
的统一内部库——语音会话、文件同步解析、外部推送的解析结果都落这里，
供 AI 检索（"谁说过什么"）与对话上下文使用。核心能力由 Agent 集成，
具体业务（目录同步、外部推送）以组件形式经 entities._sdk 桥接读写。

存储：独立 SQLite 卷（storage_volume "audio"，默认为主库同族派生路径
stem + '_audio'），WAL。索引：文本向量 BLOB 为权威数据，sqlite-vec vec0
表为派生索引（与 MemoryStore 同一范式）；无 sqlite-vec 时降级 Python
余弦全表扫描；FTS5（预分词 transcript_tokens，CJK 可检索）支撑转写全文
检索。声纹匹配走锚扫描（说话人量级小），不建样本级向量索引。

四张主表：
- audio_segments：语音片段（转写 + 说话人归属 + 文件内时间戳 + 未读标记）
- speakers：声纹身份档案（姓名/角色/独立阈值/确认状态/实体绑定/累计统计
  + 声纹锚：历史合格样本的加权质心及其累计权重）
- voice_samples：声纹多样本池（带信道标注与时长的近期窗口，
  池满按同信道先进先出淘汰以保持信道多样性）
- recordings：录制单元登记（同步增量依据 + 合并清单，回听定位用）

实体绑定：speakers.entity_scope 关联实体画像 scope（user:/group:/agent:self），
声纹身份与实体系统双向可查（绑定后 AI 检索话语即知"这是哪个实体说的话"）。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from array import array
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite
import numpy as np

from core.config import get_config, get_config_float
from core.log import log

from .vectors import blend, cosine, sample_weight, unit_rows

_LOG_TAG = "音频"

# 声纹子系统 schema 版本（PRAGMA user_version）：落后时清声纹数据重建
_SCHEMA_VERSION = 2


def coherence_floor() -> float:
    """样本入池相干门限（audio_sample_coherence_floor，默认 0.45）。

    与锚余弦低于此值的样本视为异人/噪音拒入——门限远低于匹配阈值，
    只拦截投毒，不拦信道漂移。"""
    return get_config_float("audio_sample_coherence_floor", 0.45)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audio_segments (
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
    transcript_tokens TEXT NOT NULL DEFAULT '',
    transcript_embedding BLOB,
    ts_ns INTEGER NOT NULL,
    read INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_seg_speaker_ts ON audio_segments(speaker_id, ts_ns);
CREATE INDEX IF NOT EXISTS idx_seg_ts ON audio_segments(ts_ns);
CREATE INDEX IF NOT EXISTS idx_seg_read ON audio_segments(read);
CREATE INDEX IF NOT EXISTS idx_seg_recording ON audio_segments(recording_path);

CREATE TABLE IF NOT EXISTS speakers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    speaker_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'confirmed',
    threshold REAL,
    notes TEXT NOT NULL DEFAULT '',
    device_source TEXT NOT NULL DEFAULT '',
    entity_scope TEXT NOT NULL DEFAULT '',
    total_audio_ms INTEGER NOT NULL DEFAULT 0,
    first_seen_ns INTEGER NOT NULL,
    last_seen_ns INTEGER NOT NULL,
    match_count INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    vector BLOB,
    anchor_weight REAL NOT NULL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_speakers_status ON speakers(status, archived);
CREATE INDEX IF NOT EXISTS idx_speakers_entity ON speakers(entity_scope);

CREATE TABLE IF NOT EXISTS voice_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    speaker_id INTEGER NOT NULL REFERENCES speakers(id),
    vector BLOB NOT NULL,
    segment_id INTEGER,
    channel TEXT NOT NULL DEFAULT '',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    score REAL NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT '',
    created_ns INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_samples_speaker ON voice_samples(speaker_id);

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

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS audio_segments_fts USING fts5(
    transcript_tokens, content='audio_segments', content_rowid='id'
);
"""

_FTS_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS audio_segments_ai AFTER INSERT ON audio_segments BEGIN
    INSERT INTO audio_segments_fts(rowid, transcript_tokens)
    VALUES (new.id, new.transcript_tokens);
END;
CREATE TRIGGER IF NOT EXISTS audio_segments_ad AFTER DELETE ON audio_segments BEGIN
    INSERT INTO audio_segments_fts(audio_segments_fts, rowid, transcript_tokens)
    VALUES ('delete', old.id, old.transcript_tokens);
END;
CREATE TRIGGER IF NOT EXISTS audio_segments_au AFTER UPDATE OF transcript, transcript_tokens
ON audio_segments BEGIN
    INSERT INTO audio_segments_fts(audio_segments_fts, rowid, transcript_tokens)
    VALUES ('delete', old.id, old.transcript_tokens);
    INSERT INTO audio_segments_fts(rowid, transcript_tokens)
    VALUES (new.id, new.transcript_tokens);
END;
"""

# 上下文摘要缓存的说话人名单上限
_SUMMARY_NAMES_LIMIT = 12

# 合法的实体绑定 scope 前缀（实体画像 scope 约定）
_ENTITY_SCOPE_PREFIXES = ("user:", "group:", "agent:")


def _default_db_path() -> str:
    """音频核心库默认路径：主库同目录，stem + '_audio'（同族库派生约定）。"""
    from core.storage_volume import main_sqlite_path
    stem, ext = os.path.splitext(main_sqlite_path())
    return f"{stem}_audio{ext or '.sqlite3'}"


def _register_volume() -> None:
    from core.storage_volume import VolumeDescriptor, VolumeKind, register_volume
    register_volume(VolumeDescriptor(
        volume_id="audio",
        name="音频核心库",
        description="音频解析产物（转写片段/声纹身份/样本池/录制单元）的统一内部存储",
        kind=VolumeKind.SQLITE,
        default_path=_default_db_path,
    ))


_register_volume()


def _vec_to_blob(vec: List[float]) -> bytes:
    return array("f", vec).tobytes()


def _blob_to_vec(blob: bytes) -> List[float]:
    a = array("f")
    a.frombytes(blob)
    return list(a)


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
        "entity_scope": row["entity_scope"],
        "total_audio_ms": row["total_audio_ms"],
        "first_seen_ns": row["first_seen_ns"],
        "last_seen_ns": row["last_seen_ns"],
        "match_count": row["match_count"],
        "archived": bool(row["archived"]),
        "anchor_weight": round(float(row["anchor_weight"] or 0.0), 1),
    }


def _row_to_sample(row: aiosqlite.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "speaker_id": row["speaker_id"],
        "segment_id": row["segment_id"],
        "channel": row["channel"],
        "duration_ms": row["duration_ms"],
        "score": row["score"],
        "source": row["source"],
        "created_ns": row["created_ns"],
        "dims": (len(row["vector"]) // 4) if row["vector"] else 0,
    }


def _row_to_segment(row: aiosqlite.Row) -> Dict[str, Any]:
    keys = row.keys()
    return {
        "id": row["id"],
        "recording_path": row["recording_path"],
        "source_file": row["source_file"],
        "device_source": row["device_source"],
        "start_ms": row["start_ms"],
        "part_start_ms": row["part_start_ms"],
        "end_ms": row["end_ms"],
        "speaker_id": row["speaker_id"],
        "speaker_name": row["speaker_name"] if "speaker_name" in keys else "",
        "speaker_key": row["speaker_key"] if "speaker_key" in keys else "",
        "entity_scope": row["entity_scope"] if "entity_scope" in keys else "",
        "is_new_speaker": bool(row["is_new_speaker"]),
        "similarity": row["similarity"],
        "transcript": row["transcript"],
        "has_embedding": row["transcript_embedding"] is not None,
        "ts_ns": row["ts_ns"],
        "read": bool(row["read"]),
    }


async def _tokenize(text: str) -> str:
    """转写文本预分词（记忆子系统同一分词器，CJK bigram，FTS 可检索）。"""
    from agent.memory.store.tokenizer import tokenize_for_index
    return await asyncio.to_thread(tokenize_for_index, text)


class AudioStore:
    """音频核心库统一存储（进程内单例，经 get_audio_store 获取）。"""

    def __init__(self, db_path: Optional[str] = None) -> None:
        from core.storage_volume import get_volume_registry
        if db_path is None:
            db_path = get_volume_registry().resolve_path("audio")
            get_volume_registry().mark_active("audio", db_path)
        self._db_path = db_path
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
                await self._sync_schema(db)
                await self._init_fts(db)
                await db.commit()
                self._db = db
                log(f"AudioStore 就绪: {self._db_path} "
                    f"(vec={self._vec_available}, fts={self.fts_available})", tag=_LOG_TAG)
        return self._db

    async def _sync_schema(self, db: aiosqlite.Connection) -> None:
        """建表 + 声纹子系统版本门。

        user_version 落后（含未标记的 0）时先丢弃旧声纹表再按当前布局
        重建（旧列布局下直接执行建索引会失败；全新库 DROP IF EXISTS
        为空操作）；声纹可由录音/通话重新累积，转写片段与录制登记保留，
        片段归属重置为未知（由新模型重新识别归属）。
        """
        cursor = await db.execute("PRAGMA user_version")
        row = await cursor.fetchone()
        assert row is not None
        version = int(row[0])
        if version == _SCHEMA_VERSION:
            return
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='speakers'")
        had_legacy = await cursor.fetchone() is not None
        await db.executescript("""
            DROP TABLE IF EXISTS voice_samples;
            DROP TABLE IF EXISTS speakers;
            DROP TABLE IF EXISTS samples_vec;
        """)
        if had_legacy:
            log(f"声纹库模型升级 v{version}→v{_SCHEMA_VERSION}：清空声纹数据重建",
                "WARNING", tag=_LOG_TAG)
        await db.executescript(_SCHEMA)
        await db.execute("UPDATE audio_segments SET speaker_id=NULL, is_new_speaker=0")
        await db.execute("DELETE FROM meta WHERE key='samples_vec_dims'")
        await db.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")

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
            log(f"sqlite-vec 不可用，向量检索降级为全表扫描: {exc}", "WARNING", tag=_LOG_TAG)
            return False

    async def _init_fts(self, db: aiosqlite.Connection) -> None:
        """创建 FTS 虚表及同步触发器；FTS5 不可用时降级 LIKE 检索。"""
        try:
            await db.execute(_FTS_SCHEMA)
            await db.executescript(_FTS_TRIGGERS)
            self.fts_available = True
        except Exception as exc:
            log(f"FTS5 不可用，转写检索降级为 LIKE: {exc}", "WARNING", tag=_LOG_TAG)
            self.fts_available = False

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def initialize(self) -> None:
        """启动时建库建表（幂等）。"""
        await self._get_db()

    def _mark_dirty(self) -> None:
        self._summary_dirty = True

    @staticmethod
    def _wake_embedding_worker() -> None:
        """唤醒文本向量回填（转写编辑后语义索引滞后重建；fail-open）。"""
        try:
            from agent.memory.embedding import wake_embedding_worker
            wake_embedding_worker()
        except Exception as exc:
            log(f"embedding worker 唤醒失败: {exc}", "DEBUG", tag=_LOG_TAG)

    async def _get_meta(self, key: str) -> Optional[str]:
        db = await self._get_db()
        cursor = await db.execute("SELECT value FROM meta WHERE key=?", (key,))
        row = await cursor.fetchone()
        return str(row["value"]) if row else None

    async def _set_meta(self, key: str, value: str) -> None:
        db = await self._get_db()
        await db.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        await db.commit()

    # ------------------------------------------------------------------
    # 录制单元登记（同步增量依据 + 合并清单）
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
        """删除录制单元及其全部衍生资源（同步镜像的删除传播）。

        级联：片段（含 FTS/vec 索引）→ 片段关联的声纹样本 → 登记行。
        说话人档案保留（可能还有其他录制的样本；锚的历史贡献不清算，
        需要复位时对该说话人执行声纹重建）。
        """
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT id FROM audio_segments WHERE recording_path=?", (path,))
        segment_ids = [r["id"] for r in await cursor.fetchall()]
        sample_count = 0
        if segment_ids:
            placeholders = ",".join("?" for _ in segment_ids)
            cursor = await db.execute(
                f"SELECT COUNT(*) AS c FROM voice_samples WHERE segment_id IN ({placeholders})",
                segment_ids)
            sample_count = int(_scalar(await cursor.fetchone(), "c"))
            await db.execute(
                f"DELETE FROM voice_samples WHERE segment_id IN ({placeholders})",
                segment_ids)
            await db.execute(
                "DELETE FROM audio_segments WHERE recording_path=?", (path,))
        await db.execute("DELETE FROM recordings WHERE path=?", (path,))
        await db.commit()
        for seg_id in segment_ids:
            await self._vec_delete(seg_id)
        self._mark_dirty()
        return {"segments_deleted": len(segment_ids), "samples_deleted": sample_count}

    # ------------------------------------------------------------------
    # vec0 派生索引（转写文本向量；维度首次回填时确定）
    # ------------------------------------------------------------------

    async def _ensure_vec_table(self, db: aiosqlite.Connection, dims: int) -> bool:
        """确保 vec0 索引表存在且维度匹配；维度变更时从 BLOB 重建。返回可用性。"""
        if not self._vec_available:
            return False
        cursor = await db.execute("SELECT value FROM meta WHERE key='segments_vec_dims'")
        row = await cursor.fetchone()
        existing_dims = int(row["value"]) if row else 0

        if existing_dims == dims:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='segments_vec'")
            if await cursor.fetchone():
                return True

        if existing_dims and existing_dims != dims:
            log(f"向量维度变更 {existing_dims}→{dims}，重建 vec 索引", "WARNING", tag=_LOG_TAG)
            await db.execute("DROP TABLE IF EXISTS segments_vec")

        try:
            await db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS segments_vec "
                f"USING vec0(embedding float[{dims}] distance_metric=cosine)")
            await db.execute(
                "INSERT INTO meta(key, value) VALUES('segments_vec_dims', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(dims),))
        except Exception as exc:
            log(f"vec 索引表创建失败（降级全表扫描）: {exc}", "WARNING", tag=_LOG_TAG)
            self._vec_available = False
            return False

        # 从权威 BLOB 回填派生索引
        try:
            import sqlite_vec
            cursor = await db.execute(
                "SELECT id, transcript_embedding FROM audio_segments "
                "WHERE transcript_embedding IS NOT NULL")
            for row in await cursor.fetchall():
                vec = _blob_to_vec(row["transcript_embedding"])
                if len(vec) != dims:
                    continue
                await db.execute(
                    "INSERT OR REPLACE INTO segments_vec(rowid, embedding) VALUES(?, ?)",
                    (row["id"], sqlite_vec.serialize_float32(vec)))
            await db.commit()
        except Exception as exc:
            log(f"vec 索引回填失败: {exc}", "WARNING", tag=_LOG_TAG)
        return True

    async def _vec_upsert(self, rowid: int, vec: List[float]) -> None:
        db = await self._get_db()
        if not await self._ensure_vec_table(db, len(vec)):
            return
        try:
            import sqlite_vec
            # vec0 虚表的 INSERT OR REPLACE 不一定生效，先删后插保证幂等
            await db.execute("DELETE FROM segments_vec WHERE rowid=?", (rowid,))
            await db.execute(
                "INSERT INTO segments_vec(rowid, embedding) VALUES(?, ?)",
                (rowid, sqlite_vec.serialize_float32(vec)))
            await db.commit()
        except Exception as exc:
            log(f"vec 写入失败: {exc}", "DEBUG", tag=_LOG_TAG)

    async def _vec_delete(self, rowid: int) -> None:
        if not self._vec_available:
            return
        try:
            db = await self._get_db()
            await db.execute("DELETE FROM segments_vec WHERE rowid=?", (rowid,))
            await db.commit()
        except Exception:
            log("_vec_delete 异常已忽略", "DEBUG")

    async def _vec_search(
        self, query_vec: List[float], limit: int,
    ) -> Optional[List[Dict[str, Any]]]:
        """vec0 KNN 检索，返回 [{id, score}]；不可用返回 None。"""
        if not self._vec_available:
            return None
        db = await self._get_db()
        if not await self._ensure_vec_table(db, len(query_vec)):
            return None
        try:
            import sqlite_vec
            cursor = await db.execute(
                "SELECT rowid, distance FROM segments_vec "
                "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                (sqlite_vec.serialize_float32(query_vec), limit))
            rows = await cursor.fetchall()
            return [{"id": r["rowid"], "score": round(1.0 - r["distance"], 4)} for r in rows]
        except Exception as exc:
            log(f"vec 检索失败（降级全表扫描）: {exc}", "WARNING", tag=_LOG_TAG)
            return None

    # ------------------------------------------------------------------
    # 声纹身份（说话人档案 + 实体绑定）
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
        entity_scope: str = "",
    ) -> Dict[str, Any]:
        """创建声纹身份档案，返回完整记录。status: confirmed | pending。"""
        db = await self._get_db()
        now = time.time_ns()
        seq = await self._next_speaker_seq(db)
        prefix = "spk_tmp" if status == "pending" else "spk"
        speaker_key = f"{prefix}_{seq:04d}"
        cursor = await db.execute(
            "INSERT INTO speakers(speaker_key, name, role, status, threshold, notes, "
            "device_source, entity_scope, total_audio_ms, first_seen_ns, last_seen_ns, "
            "match_count, archived) VALUES(?,?,?,?,?,?,?,?,0,?,?,0,0)",
            (speaker_key, name, role, status, threshold, notes, device_source,
             entity_scope, now, now))
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

    async def _speaker_id_by_key_any_state(self, speaker_key: str) -> Optional[int]:
        """按 key 查说话人 id（含已归档；迁移去重判定用）。"""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT id FROM speakers WHERE speaker_key=?", (speaker_key,))
        row = await cursor.fetchone()
        return int(row["id"]) if row else None

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

    async def bind_entity(self, speaker_id: int, entity_scope: str) -> Optional[Dict[str, Any]]:
        """声纹身份 ↔ 实体画像绑定（空串解绑）。

        绑定后该说话人的话语检索结果携带 entity_scope，AI 可直接关联到
        实体画像（user:/group:/agent:self 前缀的 scope）。
        """
        entity_scope = (entity_scope or "").strip()
        if entity_scope and not entity_scope.startswith(_ENTITY_SCOPE_PREFIXES):
            raise ValueError(
                f"非法实体 scope: {entity_scope}（须以 user:/group:/agent: 开头）")
        current = await self.get_speaker(speaker_id)
        if not current:
            return None
        db = await self._get_db()
        await db.execute(
            "UPDATE speakers SET entity_scope=? WHERE id=?", (entity_scope, speaker_id))
        await db.commit()
        self._mark_dirty()
        return await self.get_speaker(speaker_id)

    async def speakers_for_entity(self, entity_scope: str) -> List[Dict[str, Any]]:
        """反向查询：实体画像绑定的全部声纹身份。"""
        entity_scope = (entity_scope or "").strip()
        if not entity_scope:
            return []
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT * FROM speakers WHERE archived=0 AND entity_scope=? "
            "ORDER BY last_seen_ns DESC", (entity_scope,))
        return [_row_to_speaker(r) for r in await cursor.fetchall()]

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
            "SELECT id FROM audio_segments WHERE speaker_id=?", (speaker_id,))
        segment_ids = [r["id"] for r in await cursor.fetchall()]
        await db.execute("DELETE FROM voice_samples WHERE speaker_id=?", (speaker_id,))
        await db.execute("DELETE FROM audio_segments WHERE speaker_id=?", (speaker_id,))
        await db.execute("DELETE FROM speakers WHERE id=?", (speaker_id,))
        await db.commit()
        for seg_id in segment_ids:
            await self._vec_delete(seg_id)
        self._mark_dirty()
        return current

    async def archive_speaker(self, speaker_id: int) -> Optional[Dict[str, Any]]:
        """软归档说话人：档案标记归档（查询面过滤），样本池删除，片段归属保留。

        归档是单向终态（无 unarchive 通道）：样本池与锚不可恢复，归属重建
        需重新注册/合并。仅用于确认不再参与识别的历史身份。
        """
        current = await self.get_speaker(speaker_id)
        if not current:
            return None
        db = await self._get_db()
        await db.execute("UPDATE speakers SET archived=1, vector=NULL, anchor_weight=0 "
                         "WHERE id=?", (speaker_id,))
        await db.execute("DELETE FROM voice_samples WHERE speaker_id=?", (speaker_id,))
        await db.commit()
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
        include_with_samples=True：剔除全部 pending（走 delete_speaker 级联：
        样本与全部话语片段一并删除）。
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
                "AND NOT EXISTS (SELECT 1 FROM audio_segments g WHERE g.speaker_id=s.id)")
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
        """说话人列表（含样本数与信道分布），支持状态过滤与姓名/角色关键字。"""
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
            f"AS sample_count, (SELECT GROUP_CONCAT(channel, ',') FROM voice_samples v "
            f"WHERE v.speaker_id=s.id) AS channels_csv "
            f"FROM speakers s WHERE {where_sql} "
            f"ORDER BY s.last_seen_ns DESC LIMIT ? OFFSET ?",
            (*params, limit, offset))
        items = []
        for row in await cursor.fetchall():
            item = _row_to_speaker(row)
            item["sample_count"] = row["sample_count"]
            channels: Dict[str, int] = {}
            for channel in str(row["channels_csv"] or "").split(","):
                if channel:
                    channels[channel] = channels.get(channel, 0) + 1
            item["channels"] = channels
            items.append(item)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    # ------------------------------------------------------------------
    # 声纹样本池与锚
    # ------------------------------------------------------------------

    async def add_sample(
        self,
        speaker_id: int,
        vector: List[float],
        *,
        segment_id: Optional[int] = None,
        channel: str = "",
        duration_ms: int = 0,
        score: float = 0.0,
        source: str = "",
        max_samples: Optional[int] = None,
    ) -> int:
        """样本入池并折叠声纹锚，返回样本 id（相干门未过返回 -1 未入池）。

        门控：与既有锚的余弦低于 audio_sample_coherence_floor 的样本拒入
        （错认人/噪音防投毒；新档案无锚时不设门）。
        淘汰：池满优先淘汰同信道最早样本（信道涌入只挤占自己，保持池的
        信道多样性），该信道无样本时淘汰全局最早。
        折叠：锚 = 历史合格样本的时长加权质心，增量更新（学习率随
        累积量自然衰减）。
        """
        db = await self._get_db()
        anchor, weight = await self.get_speaker_anchor(speaker_id)
        if anchor and cosine(vector, anchor) < coherence_floor():
            log(f"样本与声纹锚相干度过低，拒绝入池（说话人 {speaker_id}）",
                "DEBUG", tag=_LOG_TAG)
            return -1

        limit = max(1, max_samples if max_samples is not None
                    else get_config("audio_max_samples_per_speaker", 10))
        cursor = await db.execute(
            "SELECT COUNT(*) AS c FROM voice_samples WHERE speaker_id=?", (speaker_id,))
        if int(_scalar(await cursor.fetchone(), "c")) >= limit:
            cursor = await db.execute(
                "SELECT id FROM voice_samples WHERE speaker_id=? AND channel=? "
                "ORDER BY created_ns ASC LIMIT 1", (speaker_id, channel))
            row = await cursor.fetchone()
            if row is None:
                cursor = await db.execute(
                    "SELECT id FROM voice_samples WHERE speaker_id=? "
                    "ORDER BY created_ns ASC LIMIT 1", (speaker_id,))
                row = await cursor.fetchone()
            if row is not None:
                await db.execute("DELETE FROM voice_samples WHERE id=?", (row["id"],))

        cursor = await db.execute(
            "INSERT INTO voice_samples(speaker_id, vector, segment_id, channel, "
            "duration_ms, score, source, created_ns) VALUES(?,?,?,?,?,?,?,?)",
            (speaker_id, _vec_to_blob(vector), segment_id, channel,
             max(0, duration_ms), score, source, time.time_ns()))
        assert cursor.lastrowid is not None
        sample_id = int(cursor.lastrowid)

        if anchor:
            merged, total = blend(anchor, weight, vector, sample_weight(duration_ms))
        else:
            merged, total = list(vector), sample_weight(duration_ms)
        await db.execute(
            "UPDATE speakers SET vector=?, anchor_weight=? WHERE id=?",
            (_vec_to_blob(merged), total, speaker_id))
        await db.commit()
        return sample_id

    async def get_speaker_anchor(self, speaker_id: int) -> Tuple[List[float], float]:
        """读取声纹锚与其累计权重（未建立为空）。"""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT vector, anchor_weight FROM speakers "
            "WHERE id=? AND vector IS NOT NULL", (speaker_id,))
        row = await cursor.fetchone()
        return (_blob_to_vec(row["vector"]), float(row["anchor_weight"])) if row else ([], 0.0)

    async def set_speaker_anchor(
        self, speaker_id: int, vector: List[float], weight: float,
    ) -> None:
        """写入声纹锚与累计权重（样本池重立/合并精确合成）。"""
        db = await self._get_db()
        await db.execute(
            "UPDATE speakers SET vector=?, anchor_weight=? WHERE id=?",
            (_vec_to_blob(vector), weight, speaker_id))
        await db.commit()

    async def speaker_anchor_matrix(self) -> Tuple[List[int], np.ndarray]:
        """全部在档声纹锚 → (说话人 id 序列, 行归一化矩阵 [N, D])。

        维度以首行为准（单提供者单模型，正常全库同维；异常行剔除）。
        行归一化后一次矩阵-向量积即得全库锚相似度。
        """
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT id, vector FROM speakers WHERE archived=0 AND vector IS NOT NULL")
        rows = await cursor.fetchall()
        if not rows:
            return [], np.zeros((0, 0))
        decoded = [(int(r["id"]), np.frombuffer(r["vector"], dtype=np.float32)
                    .astype(np.float64)) for r in rows]
        dims = int(decoded[0][1].shape[0])
        kept = [(sid, vec) for sid, vec in decoded if vec.shape[0] == dims]
        matrix = np.stack([vec for _, vec in kept]) if kept else np.zeros((0, dims))
        return [sid for sid, _ in kept], unit_rows(matrix)

    async def get_speaker_samples(
        self, speaker_id: int, channel: str = "",
    ) -> List[Tuple[List[float], int, str]]:
        """读取样本池 (向量, 时长毫秒, 信道)；channel 非空时只取该信道。"""
        db = await self._get_db()
        if channel:
            cursor = await db.execute(
                "SELECT vector, duration_ms, channel FROM voice_samples "
                "WHERE speaker_id=? AND channel=?", (speaker_id, channel))
        else:
            cursor = await db.execute(
                "SELECT vector, duration_ms, channel FROM voice_samples "
                "WHERE speaker_id=?", (speaker_id,))
        return [(_blob_to_vec(r["vector"]), int(r["duration_ms"]), str(r["channel"]))
                for r in await cursor.fetchall()]

    async def move_samples(self, from_speaker_id: int, to_speaker_id: int) -> int:
        """样本池整体迁移（身份合并）：保留信道/时长/挂接，返回迁移条数。

        池溢出按创建时间保留最近样本；锚不在此折叠——由合并方对两档案
        锚做加权精确合成（与重放全部历史样本等价）。
        """
        db = await self._get_db()
        cursor = await db.execute(
            "UPDATE voice_samples SET speaker_id=? WHERE speaker_id=?",
            (to_speaker_id, from_speaker_id))
        moved = cursor.rowcount
        limit = max(1, get_config("audio_max_samples_per_speaker", 10))
        cursor = await db.execute(
            "SELECT id FROM voice_samples WHERE speaker_id=? "
            "ORDER BY created_ns DESC LIMIT -1 OFFSET ?", (to_speaker_id, limit))
        overflow = [r["id"] for r in await cursor.fetchall()]
        if overflow:
            placeholders = ",".join("?" for _ in overflow)
            await db.execute(
                f"DELETE FROM voice_samples WHERE id IN ({placeholders})", overflow)
        await db.commit()
        return moved

    async def reassign_segments(self, from_speaker_id: int, to_speaker_id: int) -> int:
        """批量改派片段归属（身份合并用），返回影响行数。"""
        db = await self._get_db()
        cursor = await db.execute(
            "UPDATE audio_segments SET speaker_id=? WHERE speaker_id=?",
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
        """删除单条样本（锚的历史贡献不清算，需要复位时执行声纹重建）。"""
        db = await self._get_db()
        cursor = await db.execute("DELETE FROM voice_samples WHERE id=?", (sample_id,))
        await db.commit()
        return bool(cursor.rowcount)

    # ------------------------------------------------------------------
    # 语音片段
    # ------------------------------------------------------------------

    _SEGMENT_SELECT = (
        "SELECT seg.*, spk.name AS speaker_name, spk.speaker_key AS speaker_key, "
        "spk.entity_scope AS entity_scope "
        "FROM audio_segments seg LEFT JOIN speakers spk ON spk.id=seg.speaker_id"
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
            "INSERT INTO audio_segments(recording_path, source_file, device_source, "
            "start_ms, end_ms, part_start_ms, speaker_id, is_new_speaker, similarity, "
            "transcript, transcript_tokens, ts_ns, read, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0,?)",
            (recording_path, source_file, device_source, start_ms, end_ms,
             part_start_ms, speaker_id,
             1 if is_new_speaker else 0, similarity, transcript,
             await _tokenize(transcript),
             ts_ns if ts_ns is not None else time.time_ns(), time.time()))
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
            "UPDATE audio_segments SET speaker_id=?, is_new_speaker=0 WHERE id=?",
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
            "UPDATE audio_segments SET transcript=?, transcript_tokens=?, "
            "transcript_embedding=NULL WHERE id=?",
            (transcript, await _tokenize(transcript), segment_id))
        await db.commit()
        await self._vec_delete(segment_id)
        self._wake_embedding_worker()
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
            f"SELECT id, transcript FROM audio_segments WHERE {' AND '.join(where)} "
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
                    "UPDATE audio_segments SET transcript=?, transcript_tokens=?, "
                    "transcript_embedding=NULL WHERE id=?",
                    (new, await _tokenize(new), row["id"]))
        if changed_ids and not dry_run:
            await db.commit()
            for seg_id in changed_ids:
                await self._vec_delete(seg_id)
            self._wake_embedding_worker()
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
            "UPDATE audio_segments SET transcript=?, transcript_tokens=?, "
            "transcript_embedding=NULL, start_ms=?, end_ms=?, speaker_id=?, "
            "is_new_speaker=?, read=? WHERE id=?",
            (merged_text, await _tokenize(merged_text), first["start_ms"], last["end_ms"],
             merged_speaker,
             1 if first["is_new_speaker"] else 0,
             0 if any(not s["read"] for s in segments) else 1,
             first["id"]))
        rest_ids = [s["id"] for s in segments[1:]]
        placeholders = ",".join("?" for _ in rest_ids)
        # 被并片段挂接的声纹样本改挂到保留片段（防录制删除时级联漏清理）
        await db.execute(
            f"UPDATE voice_samples SET segment_id=? WHERE segment_id IN ({placeholders})",
            (first["id"], *rest_ids))
        await db.execute(
            f"DELETE FROM audio_segments WHERE id IN ({placeholders})", rest_ids)
        await db.commit()
        await self._vec_delete(int(first["id"]))
        for seg_id in rest_ids:
            await self._vec_delete(int(seg_id))
        self._mark_dirty()
        self._wake_embedding_worker()
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

        first_text = text_first if text_first is not None else segment["transcript"]
        second_text = text_second if text_second is not None else segment["transcript"]
        db = await self._get_db()
        await db.execute(
            "UPDATE audio_segments SET end_ms=?, transcript=?, transcript_tokens=?, "
            "transcript_embedding=NULL WHERE id=?",
            (at_ms, first_text, await _tokenize(first_text), segment_id))
        second_speaker = (speaker_second_id if speaker_second_set
                          else segment["speaker_id"])
        second_ts = int(segment["ts_ns"]) + (at_ms - int(segment["start_ms"])) * 1_000_000
        cursor = await db.execute(
            "INSERT INTO audio_segments(recording_path, source_file, device_source, "
            "start_ms, end_ms, part_start_ms, speaker_id, is_new_speaker, similarity, "
            "transcript, transcript_tokens, ts_ns, read, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (segment["recording_path"], segment["source_file"], segment["device_source"],
             at_ms, segment["end_ms"], segment["part_start_ms"], second_speaker,
             1 if segment["is_new_speaker"] else 0, segment["similarity"],
             second_text, await _tokenize(second_text),
             second_ts, 1 if segment["read"] else 0, time.time()))
        await db.commit()
        await self._vec_delete(segment_id)
        self._mark_dirty()
        self._wake_embedding_worker()
        assert cursor.lastrowid is not None
        return {
            "first": await self.get_segment(segment_id),
            "second": await self.get_segment(int(cursor.lastrowid)),
        }

    async def delete_segment(self, segment_id: int) -> bool:
        db = await self._get_db()
        cursor = await db.execute("DELETE FROM audio_segments WHERE id=?", (segment_id,))
        await db.commit()
        if cursor.rowcount:
            await self._vec_delete(segment_id)
            self._mark_dirty()
            return True
        return False

    async def list_segments(
        self,
        *,
        speaker_id: Optional[int] = None,
        entity_scope: str = "",
        recording_path: str = "",
        from_ns: Optional[int] = None,
        to_ns: Optional[int] = None,
        unread_only: bool = False,
        limit: int = 20,
        offset: int = 0,
        order: str = "desc",
    ) -> Dict[str, Any]:
        """片段时间线查询（说话人/绑定实体/录制/时间范围/未读硬过滤）。

        entity_scope 按说话人的实体绑定过滤（声纹→实体的检索路径）。
        order: 'desc' 最新在前（默认）/ 'asc' 时间正序（时间线视图）。
        """
        db = await self._get_db()
        where = ["1=1"]
        params: List[Any] = []
        if speaker_id is not None:
            where.append("seg.speaker_id=?")
            params.append(speaker_id)
        if entity_scope:
            where.append("seg.speaker_id IN "
                         "(SELECT id FROM speakers WHERE entity_scope=?)")
            params.append(entity_scope)
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
            f"SELECT COUNT(*) AS c FROM audio_segments seg WHERE {where_sql}", params)
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
        entity_scope: str = "",
        from_ns: Optional[int] = None,
        to_ns: Optional[int] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """转写混合检索：FTS5/向量双路召回 + 说话人/绑定实体/时间硬过滤。

        评分：0.6 × 语义分 + 0.4 × FTS 归一分；query 为空时退化为时间线查询。
        """
        if not query.strip():
            result = await self.list_segments(
                speaker_id=speaker_id, entity_scope=entity_scope,
                from_ns=from_ns, to_ns=to_ns, limit=limit)
            return result["items"]

        db = await self._get_db()
        candidate_ids: set[int] = set()
        scores: Dict[int, float] = {}

        # 向量召回
        if query_vec:
            vec_hits = await self._vec_search(query_vec, limit * 3)
            if vec_hits is None:
                vec_hits = await self._python_segment_scan(query_vec, limit * 3)
            for hit in vec_hits:
                candidate_ids.add(hit["id"])
                scores[hit["id"]] = max(scores.get(hit["id"], 0.0), 0.6 * hit["score"])

        # FTS 召回（不可用时 LIKE）
        from agent.memory.store._shared import build_fts_query
        fts_query = build_fts_query(query)
        fts_rows: List[aiosqlite.Row] = []
        if fts_query and self.fts_available:
            try:
                cursor = await db.execute(
                    "SELECT rowid, rank FROM audio_segments_fts "
                    "WHERE audio_segments_fts MATCH ? ORDER BY rank LIMIT ?",
                    (fts_query, limit * 3))
                fts_rows = list(await cursor.fetchall())
            except Exception as exc:
                log(f"FTS 检索失败（降级 LIKE）: {exc}", "DEBUG", tag=_LOG_TAG)
                self.fts_available = False
        if not fts_rows:
            like = f"%{query.strip()}%"
            cursor = await db.execute(
                "SELECT id, 0.0 AS rank FROM audio_segments WHERE transcript LIKE ? LIMIT ?",
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
        if entity_scope:
            where.append("seg.speaker_id IN "
                         "(SELECT id FROM speakers WHERE entity_scope=?)")
            params.append(entity_scope)
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
            "SELECT id, transcript_embedding FROM audio_segments "
            "WHERE transcript_embedding IS NOT NULL")
        scored: List[Dict[str, Any]] = []
        for row in await cursor.fetchall():
            score = cosine(query_vec, _blob_to_vec(row["transcript_embedding"]))
            if score > 0.05:
                scored.append({"id": row["id"], "score": round(score, 4)})
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:limit]

    async def mark_read(
        self, segment_ids: Optional[List[int]] = None, *, read: bool = True,
    ) -> int:
        """标记片段已读/未读；segment_ids 为 None 时作用于全部。返回影响行数。"""
        db = await self._get_db()
        flag = 1 if read else 0
        if segment_ids is None:
            cursor = await db.execute(
                f"UPDATE audio_segments SET read={flag} WHERE read={1 - flag}")
        elif segment_ids:
            placeholders = ",".join("?" for _ in segment_ids)
            cursor = await db.execute(
                f"UPDATE audio_segments SET read={flag} WHERE id IN ({placeholders})",
                segment_ids)
        else:
            return 0
        await db.commit()
        self._mark_dirty()
        return cursor.rowcount

    async def delete_segments(
        self,
        *,
        speaker_id: Optional[int] = None,
        entity_scope: str = "",
        recording_path: str = "",
        from_ns: Optional[int] = None,
        to_ns: Optional[int] = None,
        unread_only: bool = False,
    ) -> Dict[str, int]:
        """按筛选批量删除片段（时间线的批量清空）；级联删除挂接的声纹样本。

        筛选与 list_segments 同构；全部条件为空时清空片段表。
        说话人档案与录制登记保留（录制单元请走 delete_recording 级联）。
        """
        db = await self._get_db()
        where = ["1=1"]
        params: List[Any] = []
        if speaker_id is not None:
            where.append("seg.speaker_id=?")
            params.append(speaker_id)
        if entity_scope:
            where.append("seg.speaker_id IN "
                         "(SELECT id FROM speakers WHERE entity_scope=?)")
            params.append(entity_scope)
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
        cursor = await db.execute(
            f"SELECT seg.id FROM audio_segments seg WHERE {' AND '.join(where)}", params)
        ids = [int(r["id"]) for r in await cursor.fetchall()]
        if not ids:
            return {"deleted": 0, "samples_deleted": 0}
        placeholders = ",".join("?" for _ in ids)
        cursor = await db.execute(
            f"SELECT COUNT(*) AS c FROM voice_samples WHERE segment_id IN ({placeholders})",
            ids)
        samples_deleted = int(_scalar(await cursor.fetchone(), "c"))
        await db.execute(
            f"DELETE FROM voice_samples WHERE segment_id IN ({placeholders})", ids)
        await db.execute(
            f"DELETE FROM audio_segments WHERE id IN ({placeholders})", ids)
        await db.commit()
        for seg_id in ids:
            await self._vec_delete(seg_id)
        self._mark_dirty()
        return {"deleted": len(ids), "samples_deleted": samples_deleted}

    async def unread_count(self) -> int:
        db = await self._get_db()
        cursor = await db.execute("SELECT COUNT(*) AS c FROM audio_segments WHERE read=0")
        return int(_scalar(await cursor.fetchone(), "c"))

    # ------------------------------------------------------------------
    # 转写文本向量（后台回填）
    # ------------------------------------------------------------------

    async def list_missing_transcript_embeddings(self, limit: int) -> List[Dict[str, Any]]:
        """列出待回填文本向量的片段（id + transcript）。"""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT id, transcript FROM audio_segments "
            "WHERE transcript_embedding IS NULL AND transcript != '' LIMIT ?", (limit,))
        return [{"id": r["id"], "transcript": r["transcript"]} for r in await cursor.fetchall()]

    async def set_transcript_embedding(self, segment_id: int, vec: List[float]) -> None:
        """写入片段文本向量并同步 vec 索引。"""
        db = await self._get_db()
        await db.execute(
            "UPDATE audio_segments SET transcript_embedding=? WHERE id=?",
            (_vec_to_blob(vec), segment_id))
        await db.commit()
        await self._vec_upsert(segment_id, vec)

    # ------------------------------------------------------------------
    # 统计与上下文摘要
    # ------------------------------------------------------------------

    async def stats(self) -> Dict[str, Any]:
        """库总览统计。"""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT COUNT(*) AS c, "
            "SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending, "
            "SUM(CASE WHEN entity_scope != '' THEN 1 ELSE 0 END) AS bound "
            "FROM speakers WHERE archived=0")
        row = await cursor.fetchone()
        assert row is not None
        cursor = await db.execute("SELECT COUNT(*) AS c FROM voice_samples")
        samples = int(_scalar(await cursor.fetchone(), "c"))
        cursor = await db.execute(
            "SELECT COUNT(*) AS c, SUM(CASE WHEN read=0 THEN 1 ELSE 0 END) AS unread, "
            "COALESCE(SUM(end_ms - start_ms), 0) AS audio_ms FROM audio_segments")
        seg = await cursor.fetchone()
        assert seg is not None
        cursor = await db.execute(
            "SELECT COUNT(*) AS c FROM audio_segments WHERE transcript_embedding IS NULL "
            "AND transcript != ''")
        missing_embed = int(_scalar(await cursor.fetchone(), "c"))
        cursor = await db.execute("SELECT COUNT(*) AS c FROM recordings")
        recordings = int(_scalar(await cursor.fetchone(), "c"))
        cursor = await db.execute(
            "SELECT vector FROM voice_samples ORDER BY created_ns DESC LIMIT 1")
        latest = await cursor.fetchone()
        voiceprint_dims = (len(latest["vector"]) // 4) if latest else 0
        return {
            "speakers": row["c"] or 0,
            "pending_speakers": row["pending"] or 0,
            "bound_speakers": row["bound"] or 0,
            "samples": samples,
            "segments": seg["c"] or 0,
            "unread_segments": seg["unread"] or 0,
            "audio_ms": seg["audio_ms"] or 0,
            "missing_embeddings": missing_embed,
            "recordings": recordings,
            "voiceprint_dims": voiceprint_dims,
            "vec_available": self._vec_available,
            "fts_available": self.fts_available,
            "db_path": self._db_path,
        }

    async def summary(self) -> Dict[str, Any]:
        """上下文注入用摘要（内存缓存 + 写路径置脏，稳态零 I/O）。"""
        if self._summary_cache is not None and not self._summary_dirty:
            return self._summary_cache
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT name, role, entity_scope FROM speakers "
            "WHERE archived=0 AND status='confirmed' AND name != '' "
            "ORDER BY last_seen_ns DESC LIMIT ?", (_SUMMARY_NAMES_LIMIT,))
        names: List[str] = []
        bindings: List[str] = []
        for r in await cursor.fetchall():
            label = f"{r['name']}({r['role']})" if r["role"] else r["name"]
            names.append(label)
            scope = str(r["entity_scope"] or "")
            if scope:
                bindings.append(f"{label}→{scope}")
        cursor = await db.execute(
            "SELECT COUNT(*) AS c FROM speakers WHERE archived=0 AND status='pending'")
        pending = int(_scalar(await cursor.fetchone(), "c"))
        unread = await self.unread_count()
        self._summary_cache = {
            "confirmed_names": names,
            "entity_bindings": bindings,
            "pending_count": pending,
            "unread_count": unread,
        }
        self._summary_dirty = False
        return self._summary_cache


# ------------------------------------------------------------------
# 单例
# ------------------------------------------------------------------

_store: Optional[AudioStore] = None


def get_audio_store() -> AudioStore:
    """获取 AudioStore 单例（首次调用时创建，DB 连接懒初始化）。"""
    global _store
    if _store is None:
        _store = AudioStore()
    return _store
