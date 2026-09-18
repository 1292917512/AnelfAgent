"""FaceStore：人脸核心库的统一存储（人物档案 / 样本池 / 出现事件）。

定位：全部人脸识别产物（人物身份、人脸样本、画面出现事件）的统一内部库
——频道图片、视觉源帧、Web 上传的识别结果都落这里，供 AI 检索
（"在哪见过谁"）与对话上下文使用。引擎（检测+512 维向量提取）归外部
服务，库与匹配治理归 Agent（与声纹库同一范式）。

存储：独立 SQLite 卷（storage_volume "face"，默认为主库同族派生路径
stem + '_face'），WAL。向量以 float32 BLOB 存；匹配走锚矩阵全库扫描
（人物量级小，不建样本级向量索引）。

三张主表：
- persons：人物身份档案（姓名/角色/独立阈值/确认状态/实体绑定/累计统计
  + 人脸锚：历史合格样本的质量加权质心及其累计权重）
- face_samples：人脸多样本池（带来源与质量分的近期窗口，
  池满按同来源先进先出淘汰以保持采集多样性）
- face_events：画面出现事件（一图一行，faces_json 记录全部命中，
  person_ids 为命中人物 CSV 供索引过滤；未读收件箱语义同声纹片段）

实体绑定：persons.entity_scope 关联实体画像 scope（user:/group:/agent:self），
人脸身份与实体系统双向可查（绑定后画面识别即知"看到的是哪个实体"，
经 face_scope 标签驱动画像与记忆自动召回）。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from array import array
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite
import numpy as np

from core.config import get_config, get_config_float, get_config_int
from core.log import log

from .vectors import blend, cosine, quality_weight, unit_rows

_LOG_TAG = "人脸"

_SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS persons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'confirmed',
    threshold REAL,
    notes TEXT NOT NULL DEFAULT '',
    entity_scope TEXT NOT NULL DEFAULT '',
    first_seen_ns INTEGER NOT NULL,
    last_seen_ns INTEGER NOT NULL,
    match_count INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    vector BLOB,
    anchor_weight REAL NOT NULL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_persons_status ON persons(status, archived);
CREATE INDEX IF NOT EXISTS idx_persons_entity ON persons(entity_scope);

CREATE TABLE IF NOT EXISTS face_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL REFERENCES persons(id),
    vector BLOB NOT NULL,
    image_path TEXT NOT NULL DEFAULT '',
    bbox_json TEXT NOT NULL DEFAULT '[]',
    quality REAL NOT NULL DEFAULT 0,
    pose_json TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL DEFAULT '',
    event_id INTEGER,
    created_ns INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_face_samples_person ON face_samples(person_id);

CREATE TABLE IF NOT EXISTS face_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    image_path TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    width INTEGER NOT NULL DEFAULT 0,
    height INTEGER NOT NULL DEFAULT 0,
    faces_json TEXT NOT NULL DEFAULT '[]',
    person_ids TEXT NOT NULL DEFAULT ',',
    faces_count INTEGER NOT NULL DEFAULT 0,
    ts_ns INTEGER NOT NULL,
    read INTEGER NOT NULL DEFAULT 0,
    created_ns INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_face_events_ts ON face_events(ts_ns);
CREATE INDEX IF NOT EXISTS idx_face_events_persons ON face_events(person_ids);
CREATE INDEX IF NOT EXISTS idx_face_events_read ON face_events(read);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# 上下文摘要缓存的人物名单上限
_SUMMARY_NAMES_LIMIT = 12

# 合法的实体绑定 scope 前缀（实体画像 scope 约定，与声纹库一致）
_ENTITY_SCOPE_PREFIXES = ("user:", "group:", "agent:")


def coherence_floor() -> float:
    """样本入池相干门限（face_coherence_floor，默认 0.20）。

    与锚余弦低于此值的样本视为异人/劣质脸拒入——门限远低于匹配阈值
    （ArcFace 异人余弦通常 <0.2），只拦截投毒，不拦姿态/光照漂移。"""
    return get_config_float("face_coherence_floor", 0.20)


def face_upload_root() -> str:
    """人脸引用图片持久目录（uploads/face/）；gc 白名单唯一来源。"""
    from core.path import ConfigPaths
    return os.path.join(str(ConfigPaths.UPLOAD_DIR), "face")


def _default_db_path() -> str:
    """人脸核心库默认路径：主库同目录，stem + '_face'（同族库派生约定）。"""
    from core.storage_volume import main_sqlite_path
    stem, ext = os.path.splitext(main_sqlite_path())
    return f"{stem}_face{ext or '.sqlite3'}"


def _register_volume() -> None:
    from core.storage_volume import VolumeDescriptor, VolumeKind, register_volume
    register_volume(VolumeDescriptor(
        volume_id="face",
        name="人脸核心库",
        description="人脸识别产物（人物身份/样本池/画面出现事件）的统一内部存储",
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


def _scalar(row: Optional[aiosqlite.Row], key: str) -> Any:
    """从聚合查询行取标量（COUNT/SUM 恒返回一行，此处仅满足类型收窄）。"""
    assert row is not None
    return row[key]


def _row_to_person(row: aiosqlite.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "person_key": row["person_key"],
        "name": row["name"],
        "role": row["role"],
        "status": row["status"],
        "threshold": row["threshold"],
        "notes": row["notes"],
        "entity_scope": row["entity_scope"],
        "first_seen_ns": row["first_seen_ns"],
        "last_seen_ns": row["last_seen_ns"],
        "match_count": row["match_count"],
        "archived": bool(row["archived"]),
        "anchor_weight": round(float(row["anchor_weight"] or 0.0), 2),
    }


def _row_to_sample(row: aiosqlite.Row) -> Dict[str, Any]:
    try:
        bbox = json.loads(row["bbox_json"]) if row["bbox_json"] else []
    except Exception:
        bbox = []
    try:
        pose = json.loads(row["pose_json"]) if row["pose_json"] else {}
    except Exception:
        pose = {}
    return {
        "id": row["id"],
        "person_id": row["person_id"],
        "image_path": row["image_path"],
        "bbox": bbox,
        "quality": round(float(row["quality"] or 0.0), 4),
        "pose": pose,
        "source": row["source"],
        "event_id": row["event_id"],
        "created_ns": row["created_ns"],
        "dims": (len(row["vector"]) // 4) if row["vector"] else 0,
    }


def _row_to_event(row: aiosqlite.Row) -> Dict[str, Any]:
    try:
        faces = json.loads(row["faces_json"]) if row["faces_json"] else []
    except Exception:
        faces = []
    return {
        "id": row["id"],
        "image_path": row["image_path"],
        "source": row["source"],
        "width": row["width"],
        "height": row["height"],
        "faces": faces,
        "faces_count": row["faces_count"],
        "ts_ns": row["ts_ns"],
        "read": bool(row["read"]),
        "created_ns": row["created_ns"],
    }


class FaceStore:
    """人脸核心库统一存储（进程内单例，经 get_face_store 获取）。"""

    def __init__(self, db_path: Optional[str] = None) -> None:
        from core.storage_volume import get_volume_registry
        if db_path is None:
            db_path = get_volume_registry().resolve_path("face")
            get_volume_registry().mark_active("face", db_path)
        self._db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()
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
                await self._sync_schema(db)
                await db.commit()
                self._db = db
                log(f"FaceStore 就绪: {self._db_path}", tag=_LOG_TAG)
        return self._db

    async def _sync_schema(self, db: aiosqlite.Connection) -> None:
        """建表 + 版本门（user_version 落后即清库重建，数据可由图片重新累积）。"""
        cursor = await db.execute("PRAGMA user_version")
        row = await cursor.fetchone()
        assert row is not None
        version = int(row[0])
        if version == _SCHEMA_VERSION:
            return
        await db.executescript("""
            DROP TABLE IF EXISTS face_samples;
            DROP TABLE IF EXISTS face_events;
            DROP TABLE IF EXISTS persons;
        """)
        if version:
            log(f"人脸库模型升级 v{version}→v{_SCHEMA_VERSION}：清空数据重建",
                "WARNING", tag=_LOG_TAG)
        await db.executescript(_SCHEMA)
        await db.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def initialize(self) -> None:
        """启动时建库建表（幂等）。"""
        await self._get_db()

    def _mark_dirty(self) -> None:
        self._summary_dirty = True

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
    # 人物档案（身份 + 实体绑定）
    # ------------------------------------------------------------------

    async def _next_person_seq(self, db: aiosqlite.Connection) -> int:
        cursor = await db.execute("SELECT value FROM meta WHERE key='person_seq'")
        row = await cursor.fetchone()
        seq = int(row["value"]) + 1 if row else 1
        await db.execute(
            "INSERT INTO meta(key, value) VALUES('person_seq', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(seq),))
        return seq

    async def create_person(
        self,
        *,
        name: str = "",
        role: str = "",
        status: str = "confirmed",
        threshold: Optional[float] = None,
        notes: str = "",
        entity_scope: str = "",
    ) -> Dict[str, Any]:
        """创建人物身份档案，返回完整记录。status: confirmed | pending。"""
        db = await self._get_db()
        now = time.time_ns()
        seq = await self._next_person_seq(db)
        prefix = "fc_tmp" if status == "pending" else "fc"
        person_key = f"{prefix}_{seq:04d}"
        cursor = await db.execute(
            "INSERT INTO persons(person_key, name, role, status, threshold, notes, "
            "entity_scope, first_seen_ns, last_seen_ns, match_count, archived) "
            "VALUES(?,?,?,?,?,?,?,?,?,0,0)",
            (person_key, name, role, status, threshold, notes,
             entity_scope, now, now))
        await db.commit()
        self._mark_dirty()
        assert cursor.lastrowid is not None
        person = await self.get_person(int(cursor.lastrowid))
        assert person is not None
        return person

    async def get_person(self, person_id: int) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT * FROM persons WHERE id=? AND archived=0", (person_id,))
        row = await cursor.fetchone()
        return _row_to_person(row) if row else None

    async def get_person_by_key(self, person_key: str) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT * FROM persons WHERE person_key=? AND archived=0", (person_key,))
        row = await cursor.fetchone()
        return _row_to_person(row) if row else None

    async def find_persons(self, ref: str) -> List[Dict[str, Any]]:
        """按引用解析人物：数字 id / person_key / 姓名精确 / 姓名模糊，按相关度返回。"""
        ref = (ref or "").strip()
        if not ref:
            return []
        db = await self._get_db()
        if ref.isdigit():
            person = await self.get_person(int(ref))
            return [person] if person else []
        person = await self.get_person_by_key(ref)
        if person:
            return [person]
        cursor = await db.execute(
            "SELECT * FROM persons WHERE archived=0 AND name=? ORDER BY last_seen_ns DESC",
            (ref,))
        rows = await cursor.fetchall()
        if rows:
            return [_row_to_person(r) for r in rows]
        cursor = await db.execute(
            "SELECT * FROM persons WHERE archived=0 AND (name LIKE ? OR role LIKE ?) "
            "ORDER BY last_seen_ns DESC LIMIT 10",
            (f"%{ref}%", f"%{ref}%"))
        return [_row_to_person(r) for r in await cursor.fetchall()]

    _UPDATABLE_FIELDS = ("name", "role", "status", "threshold", "notes")

    async def update_person(self, person_id: int, **fields: Any) -> Optional[Dict[str, Any]]:
        """更新人物档案（白名单字段），返回更新后的记录；不存在返回 None。"""
        current = await self.get_person(person_id)
        if not current:
            return None
        updates: List[str] = []
        values: List[Any] = []
        for key in self._UPDATABLE_FIELDS:
            if key in fields and fields[key] is not None:
                updates.append(f"{key}=?")
                values.append(fields[key])
        if updates:
            # 待确认 → 已确认时同步刷新 person_key 前缀
            if fields.get("status") == "confirmed" and current["person_key"].startswith("fc_tmp"):
                db = await self._get_db()
                seq = await self._next_person_seq(db)
                updates.append("person_key=?")
                values.append(f"fc_{seq:04d}")
            db = await self._get_db()
            values.append(person_id)
            await db.execute(f"UPDATE persons SET {', '.join(updates)} WHERE id=?", values)
            await db.commit()
            self._mark_dirty()
        return await self.get_person(person_id)

    async def bind_entity(self, person_id: int, entity_scope: str) -> Optional[Dict[str, Any]]:
        """人脸身份 ↔ 实体画像绑定（空串解绑）。

        绑定后画面识别命中即产出 face_scope 召回键，思维层据此自动
        召回该实体的画像/记忆/关系（与声纹 speaker_scope 同一机制）。
        """
        entity_scope = (entity_scope or "").strip()
        if entity_scope and not entity_scope.startswith(_ENTITY_SCOPE_PREFIXES):
            raise ValueError(
                f"非法实体 scope: {entity_scope}（须以 user:/group:/agent: 开头）")
        current = await self.get_person(person_id)
        if not current:
            return None
        db = await self._get_db()
        await db.execute(
            "UPDATE persons SET entity_scope=? WHERE id=?", (entity_scope, person_id))
        await db.commit()
        self._mark_dirty()
        return await self.get_person(person_id)

    async def persons_for_entity(self, entity_scope: str) -> List[Dict[str, Any]]:
        """反向查询：实体画像绑定的全部人脸身份。"""
        entity_scope = (entity_scope or "").strip()
        if not entity_scope:
            return []
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT * FROM persons WHERE archived=0 AND entity_scope=? "
            "ORDER BY last_seen_ns DESC", (entity_scope,))
        return [_row_to_person(r) for r in await cursor.fetchall()]

    async def delete_person(self, person_id: int) -> Optional[Dict[str, Any]]:
        """删除人物（级联）：档案 + 样本池一并删除。

        出现事件保留（画面历史是客观事实，人物归属信息在 faces_json 中
        变为陈旧引用，时间线按事件浏览不受影响）。
        """
        current = await self.get_person(person_id)
        if not current:
            return None
        db = await self._get_db()
        await db.execute("DELETE FROM face_samples WHERE person_id=?", (person_id,))
        await db.execute("DELETE FROM persons WHERE id=?", (person_id,))
        await db.commit()
        self._mark_dirty()
        return current

    async def archive_person(self, person_id: int) -> Optional[Dict[str, Any]]:
        """软归档人物：档案标记归档（查询面过滤），样本池删除，事件保留。

        归档是单向终态：样本池与锚不可恢复，仅用于确认不再参与识别的
        历史身份（路人脸批量退场走此路径，不污染删除审计）。
        """
        current = await self.get_person(person_id)
        if not current:
            return None
        db = await self._get_db()
        await db.execute("UPDATE persons SET archived=1, vector=NULL, anchor_weight=0 "
                         "WHERE id=?", (person_id,))
        await db.execute("DELETE FROM face_samples WHERE person_id=?", (person_id,))
        await db.commit()
        self._mark_dirty()
        result = dict(current)
        result["archived"] = True
        return result

    async def touch_person_match(self, person_id: int, ts_ns: int) -> None:
        """命中回写：累计匹配次数与最近出现时间。"""
        db = await self._get_db()
        await db.execute(
            "UPDATE persons SET match_count=match_count+1, last_seen_ns=? WHERE id=?",
            (ts_ns, person_id))
        await db.commit()

    async def prune_pending_persons(
        self, *, include_with_samples: bool = False,
    ) -> List[Dict[str, Any]]:
        """批量剔除临时（pending）人物档案。

        include_with_samples=False：只清理无样本的空壳档案（安全默认）；
        include_with_samples=True：剔除全部 pending（级联删样本）。
        返回被删除的人物简报列表。
        """
        db = await self._get_db()
        if include_with_samples:
            cursor = await db.execute(
                "SELECT * FROM persons WHERE archived=0 AND status='pending'")
        else:
            cursor = await db.execute(
                "SELECT p.* FROM persons p WHERE p.archived=0 AND p.status='pending' "
                "AND NOT EXISTS (SELECT 1 FROM face_samples s WHERE s.person_id=p.id)")
        candidates = [_row_to_person(r) for r in await cursor.fetchall()]
        deleted: List[Dict[str, Any]] = []
        for person in candidates:
            result = await self.delete_person(int(person["id"]))
            if result:
                deleted.append({
                    "id": person["id"], "person_key": person["person_key"],
                    "name": person["name"],
                })
        return deleted

    async def list_persons(
        self, *, status: str = "", keyword: str = "", limit: int = 50, offset: int = 0,
    ) -> Dict[str, Any]:
        """人物列表（含样本数与来源分布），支持状态过滤与姓名/角色关键字。"""
        db = await self._get_db()
        where = ["archived=0"]
        params: List[Any] = []
        if status:
            where.append("status=?")
            params.append(status)
        if keyword:
            where.append("(name LIKE ? OR role LIKE ? OR person_key LIKE ?)")
            like = f"%{keyword}%"
            params.extend([like, like, like])
        where_sql = " AND ".join(where)
        cursor = await db.execute(f"SELECT COUNT(*) AS c FROM persons WHERE {where_sql}", params)
        total = int(_scalar(await cursor.fetchone(), "c"))
        cursor = await db.execute(
            f"SELECT p.*, (SELECT COUNT(*) FROM face_samples s WHERE s.person_id=p.id) "
            f"AS sample_count, (SELECT GROUP_CONCAT(source, ',') FROM face_samples s "
            f"WHERE s.person_id=p.id) AS sources_csv "
            f"FROM persons p WHERE {where_sql} "
            f"ORDER BY p.last_seen_ns DESC LIMIT ? OFFSET ?",
            (*params, limit, offset))
        items = []
        for row in await cursor.fetchall():
            item = _row_to_person(row)
            item["sample_count"] = row["sample_count"]
            sources: Dict[str, int] = {}
            for source in str(row["sources_csv"] or "").split(","):
                if source:
                    sources[source] = sources.get(source, 0) + 1
            item["sources"] = sources
            items.append(item)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    # ------------------------------------------------------------------
    # 人脸样本池与锚
    # ------------------------------------------------------------------

    async def add_sample(
        self,
        person_id: int,
        vector: List[float],
        *,
        image_path: str = "",
        bbox: Optional[List[float]] = None,
        pose: Optional[Dict[str, Any]] = None,
        det_score: float = 0.0,
        source: str = "",
        event_id: Optional[int] = None,
        max_samples: Optional[int] = None,
    ) -> int:
        """样本入池并折叠人脸锚，返回样本 id（相干门未过返回 -1 未入池）。

        门控：与既有锚的余弦低于 face_coherence_floor 的样本拒入
        （错认人/劣质脸防投毒；新档案无锚时不设门）。
        淘汰：池满优先淘汰同来源最早样本（来源涌入只挤占自己，保持池的
        采集多样性），该来源无样本时淘汰全局最早。
        折叠：锚 = 历史合格样本的质量加权质心（det_score × 正脸系数），
        增量更新（学习率随累积量自然衰减）。
        """
        db = await self._get_db()
        anchor, weight = await self.get_person_anchor(person_id)
        if anchor and cosine(vector, anchor) < coherence_floor():
            log(f"样本与人脸锚相干度过低，拒绝入池（人物 {person_id}）",
                "DEBUG", tag=_LOG_TAG)
            return -1

        quality = quality_weight(det_score, pose)
        limit = max(1, max_samples if max_samples is not None
                    else get_config("face_max_samples_per_person", 10))
        cursor = await db.execute(
            "SELECT COUNT(*) AS c FROM face_samples WHERE person_id=?", (person_id,))
        if int(_scalar(await cursor.fetchone(), "c")) >= limit:
            cursor = await db.execute(
                "SELECT id FROM face_samples WHERE person_id=? AND source=? "
                "ORDER BY created_ns ASC LIMIT 1", (person_id, source))
            row = await cursor.fetchone()
            if row is None:
                cursor = await db.execute(
                    "SELECT id FROM face_samples WHERE person_id=? "
                    "ORDER BY created_ns ASC LIMIT 1", (person_id,))
                row = await cursor.fetchone()
            if row is not None:
                await db.execute("DELETE FROM face_samples WHERE id=?", (row["id"],))

        cursor = await db.execute(
            "INSERT INTO face_samples(person_id, vector, image_path, bbox_json, "
            "quality, pose_json, source, event_id, created_ns) VALUES(?,?,?,?,?,?,?,?,?)",
            (person_id, _vec_to_blob(vector), image_path,
             json.dumps(bbox or [], ensure_ascii=False), quality,
             json.dumps(pose or {}, ensure_ascii=False), source, event_id,
             time.time_ns()))
        assert cursor.lastrowid is not None
        sample_id = int(cursor.lastrowid)

        if anchor:
            merged, total = blend(anchor, weight, vector, quality)
        else:
            merged, total = list(vector), quality
        await db.execute(
            "UPDATE persons SET vector=?, anchor_weight=? WHERE id=?",
            (_vec_to_blob(merged), total, person_id))
        await db.commit()
        return sample_id

    async def get_person_anchor(self, person_id: int) -> Tuple[List[float], float]:
        """读取人脸锚与其累计权重（未建立为空）。"""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT vector, anchor_weight FROM persons "
            "WHERE id=? AND vector IS NOT NULL", (person_id,))
        row = await cursor.fetchone()
        return (_blob_to_vec(row["vector"]), float(row["anchor_weight"])) if row else ([], 0.0)

    async def set_person_anchor(self, person_id: int, vector: List[float], weight: float) -> None:
        """写入人脸锚与累计权重（样本池重立/合并精确合成）。"""
        db = await self._get_db()
        await db.execute(
            "UPDATE persons SET vector=?, anchor_weight=? WHERE id=?",
            (_vec_to_blob(vector), weight, person_id))
        await db.commit()

    async def person_anchor_matrix(self) -> Tuple[List[int], np.ndarray]:
        """全部在档人脸锚 → (人物 id 序列, 行归一化矩阵 [N, D])。

        维度以首行为准（单引擎单模型，正常全库同维；异常行剔除）。
        行归一化后一次矩阵-向量积即得全库锚相似度。
        """
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT id, vector FROM persons WHERE archived=0 AND vector IS NOT NULL")
        rows = await cursor.fetchall()
        if not rows:
            return [], np.zeros((0, 0))
        decoded = [(int(r["id"]), np.frombuffer(r["vector"], dtype=np.float32)
                    .astype(np.float64)) for r in rows]
        dims = int(decoded[0][1].shape[0])
        kept = [(pid, vec) for pid, vec in decoded if vec.shape[0] == dims]
        matrix = np.stack([vec for _, vec in kept]) if kept else np.zeros((0, dims))
        return [pid for pid, _ in kept], unit_rows(matrix)

    async def get_person_samples(
        self, person_id: int, source: str = "",
    ) -> List[Tuple[List[float], float, str]]:
        """读取样本池 (向量, 质量权重, 来源)；source 非空时只取该来源。"""
        db = await self._get_db()
        if source:
            cursor = await db.execute(
                "SELECT vector, quality, source FROM face_samples "
                "WHERE person_id=? AND source=?", (person_id, source))
        else:
            cursor = await db.execute(
                "SELECT vector, quality, source FROM face_samples WHERE person_id=?",
                (person_id,))
        return [(_blob_to_vec(r["vector"]), float(r["quality"]), str(r["source"]))
                for r in await cursor.fetchall()]

    async def move_samples(self, from_person_id: int, to_person_id: int) -> int:
        """样本池整体迁移（身份合并）：保留来源/质量/挂接，返回迁移条数。

        池溢出按创建时间保留最近样本；锚不在此折叠——由合并方对两档案
        锚做加权精确合成（与重放全部历史样本等价）。
        """
        db = await self._get_db()
        cursor = await db.execute(
            "UPDATE face_samples SET person_id=? WHERE person_id=?",
            (to_person_id, from_person_id))
        moved = cursor.rowcount
        limit = max(1, get_config("face_max_samples_per_person", 10))
        cursor = await db.execute(
            "SELECT id FROM face_samples WHERE person_id=? "
            "ORDER BY created_ns DESC LIMIT -1 OFFSET ?", (to_person_id, limit))
        overflow = [r["id"] for r in await cursor.fetchall()]
        if overflow:
            placeholders = ",".join("?" for _ in overflow)
            await db.execute(
                f"DELETE FROM face_samples WHERE id IN ({placeholders})", overflow)
        await db.commit()
        return moved

    async def merge_person_stats(self, target_id: int, source: Dict[str, Any]) -> None:
        """把 source 档案的统计量（命中数/最近出现）累加进 target。"""
        db = await self._get_db()
        await db.execute(
            "UPDATE persons SET match_count=match_count+?, "
            "last_seen_ns=MAX(last_seen_ns, ?) WHERE id=?",
            (source["match_count"], source["last_seen_ns"], target_id))
        await db.commit()

    async def list_samples(self, person_id: int) -> List[Dict[str, Any]]:
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT * FROM face_samples WHERE person_id=? ORDER BY created_ns DESC",
            (person_id,))
        return [_row_to_sample(r) for r in await cursor.fetchall()]

    async def delete_sample(self, sample_id: int) -> bool:
        """删除单条样本（锚的历史贡献不清算，需要复位时执行锚重建）。"""
        db = await self._get_db()
        cursor = await db.execute("DELETE FROM face_samples WHERE id=?", (sample_id,))
        await db.commit()
        return bool(cursor.rowcount)

    # ------------------------------------------------------------------
    # 画面出现事件
    # ------------------------------------------------------------------

    async def add_event(
        self,
        *,
        image_path: str,
        source: str = "",
        width: int = 0,
        height: int = 0,
        faces: Optional[List[Dict[str, Any]]] = None,
        person_ids: Optional[List[int]] = None,
        ts_ns: Optional[int] = None,
    ) -> int:
        """新增画面出现事件（默认未读），返回事件 id。

        person_ids 存为带哨兵逗号的 CSV（',5,12,'），LIKE '%,5,%' 即索引
        级人物过滤；faces 为命中明细 JSON（人物/相似度/框，时间线展示）。
        """
        db = await self._get_db()
        ids_csv = "," + ",".join(str(int(i)) for i in (person_ids or [])) + ","
        cursor = await db.execute(
            "INSERT INTO face_events(image_path, source, width, height, faces_json, "
            "person_ids, faces_count, ts_ns, read, created_ns) VALUES(?,?,?,?,?,?,?,?,0,?)",
            (image_path, source, width, height,
             json.dumps(faces or [], ensure_ascii=False), ids_csv,
             len(faces or []),
             ts_ns if ts_ns is not None else time.time_ns(), time.time_ns()))
        await db.commit()
        self._mark_dirty()
        await self.prune_expired_events()
        assert cursor.lastrowid is not None
        return int(cursor.lastrowid)

    async def attach_samples_to_event(self, sample_ids: List[int], event_id: int) -> None:
        """把本次识别新增的样本挂到出现事件上（来源追溯依据）。"""
        ids = [int(i) for i in sample_ids if int(i) > 0]
        if not ids:
            return
        db = await self._get_db()
        placeholders = ",".join("?" for _ in ids)
        await db.execute(
            f"UPDATE face_samples SET event_id=? WHERE id IN ({placeholders})",
            (event_id, *ids))
        await db.commit()

    async def get_event(self, event_id: int) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        cursor = await db.execute("SELECT * FROM face_events WHERE id=?", (event_id,))
        row = await cursor.fetchone()
        return _row_to_event(row) if row else None

    async def list_events(
        self,
        *,
        person_id: Optional[int] = None,
        entity_scope: str = "",
        source: str = "",
        from_ns: Optional[int] = None,
        to_ns: Optional[int] = None,
        unread_only: bool = False,
        limit: int = 20,
        offset: int = 0,
        order: str = "desc",
    ) -> Dict[str, Any]:
        """出现事件时间线查询（人物/绑定实体/来源/时间范围/未读硬过滤）。

        entity_scope 按人物的实体绑定过滤（人脸→实体的检索路径）。
        """
        db = await self._get_db()
        where = ["1=1"]
        params: List[Any] = []
        if person_id is not None:
            where.append("person_ids LIKE ?")
            params.append(f"%,{int(person_id)},%")
        if entity_scope:
            where.append("EXISTS (SELECT 1 FROM persons p WHERE p.entity_scope=? "
                         "AND instr(e.person_ids, ',' || p.id || ',') > 0)")
            params.append(entity_scope)
        if source:
            where.append("source=?")
            params.append(source)
        if from_ns is not None:
            where.append("ts_ns>=?")
            params.append(from_ns)
        if to_ns is not None:
            where.append("ts_ns<=?")
            params.append(to_ns)
        if unread_only:
            where.append("read=0")
        where_sql = " AND ".join(where)
        order_sql = "ASC" if order == "asc" else "DESC"
        cursor = await db.execute(
            f"SELECT COUNT(*) AS c FROM face_events e WHERE {where_sql}", params)
        total = int(_scalar(await cursor.fetchone(), "c"))
        cursor = await db.execute(
            f"SELECT e.* FROM face_events e WHERE {where_sql} "
            f"ORDER BY e.ts_ns {order_sql} LIMIT ? OFFSET ?",
            (*params, limit, offset))
        items = [_row_to_event(r) for r in await cursor.fetchall()]
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    async def mark_read(
        self, event_ids: Optional[List[int]] = None, *, read: bool = True,
    ) -> int:
        """标记事件已读/未读；event_ids 为 None 时作用于全部。返回影响行数。"""
        db = await self._get_db()
        flag = 1 if read else 0
        if event_ids is None:
            cursor = await db.execute(
                f"UPDATE face_events SET read={flag} WHERE read={1 - flag}")
        elif event_ids:
            placeholders = ",".join("?" for _ in event_ids)
            cursor = await db.execute(
                f"UPDATE face_events SET read={flag} WHERE id IN ({placeholders})",
                event_ids)
        else:
            return 0
        await db.commit()
        self._mark_dirty()
        return cursor.rowcount

    async def delete_event(self, event_id: int) -> bool:
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT image_path FROM face_events WHERE id=?", (event_id,))
        row = await cursor.fetchone()
        cursor = await db.execute("DELETE FROM face_events WHERE id=?", (event_id,))
        await db.commit()
        if cursor.rowcount:
            self._mark_dirty()
            if row is not None:
                await self.gc_image(str(row["image_path"]))
            return True
        return False

    async def gc_image(self, path: str) -> bool:
        """回收 uploads/face 内的孤立副本（无任何事件/样本引用时删文件）。

        仅作用于人脸持久目录内的引用（ingest 复制产物）；目录外路径
        （频道上传/生成图等原文件）不归人脸库管，绝不触碰。
        """
        if not path:
            return False
        root = os.path.realpath(face_upload_root())
        real = os.path.realpath(path)
        if real != root and not real.startswith(root + os.sep):
            return False
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT 1 FROM face_events WHERE image_path=? LIMIT 1", (path,))
        if await cursor.fetchone():
            return False
        cursor = await db.execute(
            "SELECT 1 FROM face_samples WHERE image_path=? LIMIT 1", (path,))
        if await cursor.fetchone():
            return False
        try:
            os.unlink(real)
        except OSError:
            return False
        return True

    async def prune_expired_events(self) -> int:
        """按保留期清理出现事件（face_event_retention_days，0=不限）。

        写入路径顺带执行（单条索引 DELETE，开销可忽略）：人脸事件含
        截图/聊天图引用，长期无限累积既是存储负担也是隐私负担。
        被清事件的持久图副本若无其他引用一并回收。
        """
        days = get_config_int("face_event_retention_days", 30)
        if days <= 0:
            return 0
        cutoff = time.time_ns() - days * 86400 * 1_000_000_000
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT image_path FROM face_events WHERE ts_ns < ?", (cutoff,))
        paths = [str(r["image_path"]) for r in await cursor.fetchall()]
        cursor = await db.execute("DELETE FROM face_events WHERE ts_ns < ?", (cutoff,))
        await db.commit()
        if cursor.rowcount:
            self._mark_dirty()
            for path in paths:
                await self.gc_image(path)
        return cursor.rowcount

    async def unread_count(self) -> int:
        db = await self._get_db()
        cursor = await db.execute("SELECT COUNT(*) AS c FROM face_events WHERE read=0")
        return int(_scalar(await cursor.fetchone(), "c"))

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
            "FROM persons WHERE archived=0")
        row = await cursor.fetchone()
        assert row is not None
        cursor = await db.execute("SELECT COUNT(*) AS c FROM face_samples")
        samples = int(_scalar(await cursor.fetchone(), "c"))
        cursor = await db.execute(
            "SELECT COUNT(*) AS c, SUM(CASE WHEN read=0 THEN 1 ELSE 0 END) AS unread "
            "FROM face_events")
        events = await cursor.fetchone()
        assert events is not None
        cursor = await db.execute(
            "SELECT vector FROM face_samples ORDER BY created_ns DESC LIMIT 1")
        latest = await cursor.fetchone()
        face_dims = (len(latest["vector"]) // 4) if latest else 0
        return {
            "persons": row["c"] or 0,
            "pending_persons": row["pending"] or 0,
            "bound_persons": row["bound"] or 0,
            "samples": samples,
            "events": events["c"] or 0,
            "unread_events": events["unread"] or 0,
            "face_dims": face_dims,
            "db_path": self._db_path,
        }

    async def summary(self) -> Dict[str, Any]:
        """上下文注入用摘要（内存缓存 + 写路径置脏，稳态零 I/O）。"""
        if self._summary_cache is not None and not self._summary_dirty:
            return self._summary_cache
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT name, role, entity_scope FROM persons "
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
            "SELECT COUNT(*) AS c FROM persons WHERE archived=0 AND status='pending'")
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

_store: Optional[FaceStore] = None


def get_face_store() -> FaceStore:
    """获取 FaceStore 单例（首次调用时创建，DB 连接懒初始化）。"""
    global _store
    if _store is None:
        _store = FaceStore()
    return _store
