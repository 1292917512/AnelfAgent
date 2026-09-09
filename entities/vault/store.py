"""VaultStore：密码本条目的加密存储（独立 SQLite 库 + 存储卷注册）。

存储：独立库 ``{stem}_vault.sqlite3``（派生自主库路径），WAL 模式，文件权限 0600。
本层只做数据读写，不感知加解密——password/totp/notes 以密文形态落库，
加密/解密由 service 层在入库前/出库后完成。

两张表：
- entries：条目（可检索字段明文：title/username/url/tags；敏感字段密文）
- meta：KDF 参数 / salt / wrapped DEK / verifier / schema 版本
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Dict, List, Optional, Sequence

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    username TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    password_enc TEXT NOT NULL DEFAULT '',
    totp_enc TEXT NOT NULL DEFAULT '',
    notes_enc TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    favorite INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vault_entries_title ON entries(title);
CREATE INDEX IF NOT EXISTS idx_vault_entries_favorite ON entries(favorite);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _derive_db_path() -> str:
    """派生密码本库默认路径：主库同目录，stem + '_vault'。"""
    from core.storage_volume import main_sqlite_path

    stem, ext = os.path.splitext(main_sqlite_path())
    return f"{stem}_vault{ext or '.sqlite3'}"


def _register_volume() -> None:
    from core.storage_volume import VolumeDescriptor, VolumeKind, register_volume

    register_volume(VolumeDescriptor(
        volume_id="vault",
        name="密码本",
        description="密码 / TOTP 验证器 / 备注（字段级 AES-256-GCM 加密）",
        kind=VolumeKind.SQLITE,
        default_path=_derive_db_path,
        env_override="ANELF_VAULT_DB",
    ))


_register_volume()


class VaultStore:
    """密码本 SQLite 存储（长连接 + asyncio.Lock 懒初始化）。"""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or _derive_db_path()
        self._db: Optional[aiosqlite.Connection] = None
        self._init_lock = asyncio.Lock()

    @property
    def db_path(self) -> str:
        return self._db_path

    async def initialize(self) -> None:
        async with self._init_lock:
            if self._db is not None:
                return
            os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
            self._db = await aiosqlite.connect(self._db_path)
            await self._db.execute("PRAGMA journal_mode=WAL")
            await self._db.execute("PRAGMA synchronous=NORMAL")
            await self._db.execute("PRAGMA busy_timeout=5000")
            await self._db.executescript(_SCHEMA)
            await self._db.commit()
            try:
                os.chmod(self._db_path, 0o600)
            except OSError:
                pass

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            await self.initialize()
        assert self._db is not None
        return self._db

    # ------------------------------------------------------------------
    # meta
    # ------------------------------------------------------------------

    async def get_meta(self, key: str) -> Optional[str]:
        db = await self._conn()
        async with db.execute("SELECT value FROM meta WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
        return str(row[0]) if row else None

    async def set_meta(self, key: str, value: str) -> None:
        db = await self._conn()
        await db.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        await db.commit()

    # ------------------------------------------------------------------
    # entries
    # ------------------------------------------------------------------

    async def insert_entry(self, entry: Dict[str, Any]) -> None:
        db = await self._conn()
        now = time.time()
        await db.execute(
            "INSERT INTO entries(id, title, username, url, password_enc, totp_enc,"
            " notes_enc, tags, favorite, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                entry["id"], entry.get("title", ""), entry.get("username", ""),
                entry.get("url", ""), entry.get("password_enc", ""),
                entry.get("totp_enc", ""), entry.get("notes_enc", ""),
                json.dumps(entry.get("tags") or [], ensure_ascii=False),
                1 if entry.get("favorite") else 0, now, now,
            ),
        )
        await db.commit()

    async def update_entry(self, entry_id: str, fields: Dict[str, Any]) -> bool:
        """按给定字段更新（fields 键须为列名子集），返回是否命中。"""
        allowed = {"title", "username", "url", "password_enc", "totp_enc",
                   "notes_enc", "tags", "favorite"}
        sets: List[str] = []
        values: List[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            sets.append(f"{key}=?")
            if key == "tags":
                value = json.dumps(value or [], ensure_ascii=False)
            elif key == "favorite":
                value = 1 if value else 0
            values.append(value)
        if not sets:
            return True
        sets.append("updated_at=?")
        values.append(time.time())
        values.append(entry_id)
        db = await self._conn()
        cur = await db.execute(
            f"UPDATE entries SET {', '.join(sets)} WHERE id=?", values)
        await db.commit()
        return cur.rowcount > 0

    async def delete_entry(self, entry_id: str) -> bool:
        db = await self._conn()
        cur = await db.execute("DELETE FROM entries WHERE id=?", (entry_id,))
        await db.commit()
        return cur.rowcount > 0

    @staticmethod
    def _row_to_entry(row: Sequence[Any]) -> Dict[str, Any]:
        try:
            tags = json.loads(row[7] or "[]")
        except (json.JSONDecodeError, TypeError):
            tags = []
        return {
            "id": row[0], "title": row[1], "username": row[2], "url": row[3],
            "password_enc": row[4], "totp_enc": row[5], "notes_enc": row[6],
            "tags": tags, "favorite": bool(row[8]),
            "created_at": row[9], "updated_at": row[10],
        }

    async def get_entry(self, entry_id: str) -> Optional[Dict[str, Any]]:
        db = await self._conn()
        async with db.execute(
            "SELECT id, title, username, url, password_enc, totp_enc, notes_enc,"
            " tags, favorite, created_at, updated_at FROM entries WHERE id=?",
            (entry_id,),
        ) as cur:
            row = await cur.fetchone()
        return self._row_to_entry(row) if row else None

    async def list_entries(self) -> List[Dict[str, Any]]:
        db = await self._conn()
        async with db.execute(
            "SELECT id, title, username, url, password_enc, totp_enc, notes_enc,"
            " tags, favorite, created_at, updated_at FROM entries"
            " ORDER BY favorite DESC, updated_at DESC"
        ) as cur:
            rows = await cur.fetchall()
        return [self._row_to_entry(r) for r in rows]

    async def count_entries(self) -> int:
        db = await self._conn()
        async with db.execute("SELECT COUNT(*) FROM entries") as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0
