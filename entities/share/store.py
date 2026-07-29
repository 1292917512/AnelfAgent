"""ShareStore：分享链接的持久化存储。

存储：独立 SQLite 库（派生自主库路径，``{stem}_share.sqlite3``），WAL 模式。
单表设计，异步访问，asyncio.Lock 保护并发。

生命周期：通过 Lifecycle.register("share_store", ...) 注册，
on_tick 定期清理过期链接（sweep_expired）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import time
from typing import Any, Dict, List, Optional

import aiosqlite

from core.log import log

_SCHEMA = """
CREATE TABLE IF NOT EXISTS share_links (
    token TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_size INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    expires_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    created_by TEXT NOT NULL DEFAULT '',
    download_count INTEGER NOT NULL DEFAULT 0,
    last_download_at INTEGER NOT NULL DEFAULT 0,
    max_downloads INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS idx_share_status ON share_links(status);
CREATE INDEX IF NOT EXISTS idx_share_created ON share_links(created_at);
CREATE INDEX IF NOT EXISTS idx_share_path ON share_links(file_path);

CREATE TABLE IF NOT EXISTS download_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT NOT NULL,
    ip TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT '',
    downloaded_at INTEGER NOT NULL,
    file_name TEXT NOT NULL DEFAULT '',
    file_size INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_dl_token ON download_logs(token);
CREATE INDEX IF NOT EXISTS idx_dl_time ON download_logs(downloaded_at);

CREATE TABLE IF NOT EXISTS share_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_EXPIRES_MAP: Dict[str, int] = {
    "1h": 3600_000,
    "6h": 6 * 3600_000,
    "24h": 24 * 3600_000,
    "7d": 7 * 24 * 3600_000,
    "30d": 30 * 24 * 3600_000,
    "never": 0,  # 0 表示永不过期
}

_DEFAULT_CONFIG: Dict[str, Any] = {
    "default_expires_in": "24h",
    "token_length": 22,
    "ai_auto_share": True,
    "default_max_downloads": 0,
    "audit_enabled": True,
}


def _default_db_path() -> str:
    """派生分享库路径：与 MemoryStore 同一目录，stem + '_share'。"""
    try:
        from agent.storage.sqlite_backend import default_sqlite_path
        main = default_sqlite_path()
    except Exception:
        main = os.path.join("workspace", "data.sqlite3")
    stem, ext = os.path.splitext(main)
    return f"{stem}_share{ext or '.sqlite3'}"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _new_token(length: int = 22) -> str:
    """生成 URL 安全 token（默认 22 字符 base64，约 131 bit 熵）。"""
    return secrets.token_urlsafe(16)[:length]


def _hash_file(path: str) -> str:
    """计算文件 SHA256（用于变更检测）。"""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
    except OSError:
        return ""


def _row_to_entry(row: aiosqlite.Row) -> Dict[str, Any]:
    return {
        "token": row["token"],
        "file_path": row["file_path"],
        "file_name": row["file_name"],
        "file_size": row["file_size"],
        "content_hash": row["content_hash"],
        "description": row["description"],
        "expires_at": row["expires_at"],
        "created_at": row["created_at"],
        "created_by": row["created_by"],
        "download_count": row["download_count"],
        "last_download_at": row["last_download_at"],
        "max_downloads": row["max_downloads"],
        "status": row["status"],
    }


class ShareStore:
    """分享链接的统一存储（懒初始化单例）。"""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or _default_db_path()
        self._db: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()
        self._config: Dict[str, Any] = dict(_DEFAULT_CONFIG)

    async def _get_db(self) -> aiosqlite.Connection:
        if self._db is not None:
            return self._db
        async with self._lock:
            if self._db is not None:
                return self._db
            os.makedirs(os.path.dirname(os.path.abspath(self._db_path)), exist_ok=True)
            db = await aiosqlite.connect(self._db_path)
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA synchronous=NORMAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            await db.executescript(_SCHEMA)
            await db.commit()
            await self._migrate(db)
            self._db = db
            await self._load_config()
            log(f"ShareStore 就绪: {self._db_path}", tag="分享")
            return db

    async def _migrate(self, db: aiosqlite.Connection) -> None:
        """Schema 迁移：检测并添加缺失的列（SQLite ALTER TABLE ADD COLUMN）。"""
        migrations = [
            ("share_links", "max_downloads", "INTEGER NOT NULL DEFAULT 0"),
            ("share_links", "content_hash", "TEXT NOT NULL DEFAULT ''"),
        ]
        for table, column, col_type in migrations:
            try:
                cursor = await db.execute(f"PRAGMA table_info({table})")
                columns = {row["name"] for row in await cursor.fetchall()}
                if column not in columns:
                    await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
                    await db.commit()
                    log(f"分享库迁移: {table}.{column} 已添加", "DEBUG", tag="分享")
            except Exception as e:
                log(f"分享库迁移失败 {table}.{column}: {e}", "WARNING", tag="分享")

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    # ------------------------------------------------------------------
    # 配置管理
    # ------------------------------------------------------------------

    async def _load_config(self) -> None:
        """从 DB 加载配置，覆盖默认值。"""
        db = self._db
        if db is None:
            return
        try:
            cursor = await db.execute("SELECT key, value FROM share_config")
            for row in await cursor.fetchall():
                key = row["key"]
                if key in _DEFAULT_CONFIG:
                    try:
                        self._config[key] = json.loads(row["value"])
                    except Exception:
                        self._config[key] = row["value"]
        except Exception as e:
            log(f"分享配置加载失败: {e}", "DEBUG", tag="分享")

    async def get_config(self) -> Dict[str, Any]:
        """获取当前配置（含默认值）。"""
        await self._get_db()
        return dict(self._config)

    async def set_config(self, key: str, value: Any) -> None:
        """更新配置项。"""
        if key not in _DEFAULT_CONFIG:
            raise ValueError(f"未知配置项: {key}")
        db = await self._get_db()
        self._config[key] = value
        await db.execute(
            "INSERT INTO share_config(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )
        await db.commit()
        log(f"分享配置已更新: {key}={value}", "DEBUG", tag="分享")

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        file_path: str,
        description: str = "",
        expires_in: str = "24h",
        created_by: str = "",
        max_downloads: int = 0,
    ) -> Dict[str, Any]:
        """创建分享链接。相同 (file_path, content_hash) 复用旧 token 避免重复。"""
        db = await self._get_db()
        now = _now_ms()

        # 沙箱校验 + 文件存在性
        from entities.filesystem.tools import _safe_path
        try:
            fp = _safe_path(file_path)
        except ValueError as e:
            raise ValueError(f"路径沙箱校验失败: {e}") from e
        if not os.path.isfile(fp):
            raise FileNotFoundError(f"文件不存在: {file_path}")

        file_size = os.path.getsize(fp)
        content_hash = _hash_file(fp)
        file_name = os.path.basename(fp)
        expires_ms = _EXPIRES_MAP.get(expires_in, _EXPIRES_MAP["24h"])
        expires_at = now + expires_ms if expires_ms > 0 else 0
        token_len = int(self._config.get("token_length", 22))

        # 查重：同路径同 hash 的 active 链接直接复用
        async with self._lock:
            cursor = await db.execute(
                "SELECT * FROM share_links WHERE file_path=? AND content_hash=? AND status='active'",
                (file_path, content_hash),
            )
            existing = await cursor.fetchone()
            if existing:
                entry = _row_to_entry(existing)
                entry["deduplicated"] = True
                return entry

            token = _new_token(token_len)
            await db.execute(
                "INSERT INTO share_links(token, file_path, file_name, file_size, "
                "content_hash, description, expires_at, created_at, created_by, "
                "download_count, last_download_at, max_downloads, status) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,0,?,?)",
                (token, file_path, file_name, file_size, content_hash,
                 description, expires_at, now, created_by, max_downloads, "active"),
            )
            await db.commit()

        log(f"分享链接已创建: {file_name} (token={token[:8]}..., expires={expires_in})", tag="分享")
        return {
            "token": token,
            "file_path": file_path,
            "file_name": file_name,
            "file_size": file_size,
            "content_hash": content_hash,
            "description": description,
            "expires_at": expires_at,
            "created_at": now,
            "created_by": created_by,
            "download_count": 0,
            "last_download_at": 0,
            "max_downloads": max_downloads,
            "status": "active",
        }

    async def get_by_token(self, token: str) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        cursor = await db.execute("SELECT * FROM share_links WHERE token=?", (token,))
        row = await cursor.fetchone()
        return _row_to_entry(row) if row else None

    async def list(
        self,
        *,
        status: str = "active",
        page: int = 1,
        page_size: int = 20,
        query: str = "",
    ) -> Dict[str, Any]:
        db = await self._get_db()
        conditions: List[str] = []
        params: List[Any] = []

        if status != "all":
            conditions.append("status=?")
            params.append(status)
        if query:
            conditions.append("(file_path LIKE ? OR file_name LIKE ? OR description LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like, like])

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        cursor = await db.execute(
            f"SELECT COUNT(*) AS c FROM share_links {where}", params)
        total = (await cursor.fetchone())["c"]

        cursor = await db.execute(
            f"SELECT * FROM share_links {where} "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*params, page_size, (max(1, page) - 1) * page_size),
        )
        items = [_row_to_entry(r) for r in await cursor.fetchall()]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    async def revoke(self, token: str) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        async with self._lock:
            cursor = await db.execute(
                "UPDATE share_links SET status='revoked' WHERE token=? AND status='active'",
                (token,),
            )
            await db.commit()
            if cursor.rowcount == 0:
                return None
        log(f"分享链接已撤销: {token[:8]}...", tag="分享")
        return await self.get_by_token(token)

    async def touch_download(self, token: str) -> None:
        db = await self._get_db()
        await db.execute(
            "UPDATE share_links SET download_count=download_count+1, last_download_at=? "
            "WHERE token=?",
            (_now_ms(), token),
        )
        await db.commit()

    async def mark_expired(self, token: str) -> None:
        db = await self._get_db()
        await db.execute(
            "UPDATE share_links SET status='expired' WHERE token=? AND status='active'",
            (token,),
        )
        await db.commit()

    async def mark_exhausted(self, token: str) -> None:
        """下载次数达到上限，标记为 exhausted（归入 expired 状态）。"""
        db = await self._get_db()
        await db.execute(
            "UPDATE share_links SET status='expired' WHERE token=? AND status='active'",
            (token,),
        )
        await db.commit()
        log(f"分享链接下载次数已达上限: {token[:8]}...", tag="分享")

    async def sweep_expired(self) -> int:
        """标记所有已过期的 active 链接为 expired，返回处理数量。"""
        db = await self._get_db()
        now = _now_ms()
        cursor = await db.execute(
            "UPDATE share_links SET status='expired' "
            "WHERE status='active' AND expires_at > 0 AND expires_at < ?",
            (now,),
        )
        await db.commit()
        if cursor.rowcount:
            log(f"过期链接清理: {cursor.rowcount} 条", "DEBUG", tag="分享")
        return cursor.rowcount

    async def stats(self) -> Dict[str, Any]:
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT status, COUNT(*) AS c FROM share_links GROUP BY status")
        counts = {r["status"]: r["c"] for r in await cursor.fetchall()}
        cursor = await db.execute(
            "SELECT COALESCE(SUM(download_count),0) AS d FROM share_links")
        total_downloads = (await cursor.fetchone())["d"]
        cursor = await db.execute(
            "SELECT file_path, file_name, SUM(download_count) AS d "
            "FROM share_links GROUP BY file_path ORDER BY d DESC LIMIT 10")
        top_files = [
            {"file_path": r["file_path"], "file_name": r["file_name"], "count": r["d"]}
            for r in await cursor.fetchall()
        ]
        return {
            "total": sum(counts.values()),
            "active": counts.get("active", 0),
            "expired": counts.get("expired", 0),
            "revoked": counts.get("revoked", 0),
            "total_downloads": total_downloads,
            "top_files": top_files,
        }

    # ------------------------------------------------------------------
    # 审计日志
    # ------------------------------------------------------------------

    async def log_download(
        self,
        token: str,
        ip: str = "",
        user_agent: str = "",
        file_name: str = "",
        file_size: int = 0,
    ) -> None:
        """记录下载审计日志。"""
        if not self._config.get("audit_enabled", True):
            return
        db = await self._get_db()
        await db.execute(
            "INSERT INTO download_logs(token, ip, user_agent, downloaded_at, file_name, file_size) "
            "VALUES(?,?,?,?,?,?)",
            (token, ip, user_agent, _now_ms(), file_name, file_size),
        )
        await db.commit()

    async def get_download_logs(
        self,
        token: Optional[str] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Dict[str, Any]:
        """获取下载审计日志（支持按 token 过滤）。"""
        db = await self._get_db()
        conditions: List[str] = []
        params: List[Any] = []
        if token:
            conditions.append("token=?")
            params.append(token)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        cursor = await db.execute(
            f"SELECT COUNT(*) AS c FROM download_logs {where}", params)
        total = (await cursor.fetchone())["c"]

        cursor = await db.execute(
            f"SELECT * FROM download_logs {where} "
            "ORDER BY downloaded_at DESC LIMIT ? OFFSET ?",
            (*params, page_size, (max(1, page) - 1) * page_size),
        )
        items = [
            {
                "id": r["id"],
                "token": r["token"],
                "ip": r["ip"],
                "user_agent": r["user_agent"],
                "downloaded_at": r["downloaded_at"],
                "file_name": r["file_name"],
                "file_size": r["file_size"],
            }
            for r in await cursor.fetchall()
        ]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    # ------------------------------------------------------------------
    # 工具辅助
    # ------------------------------------------------------------------

    @staticmethod
    def is_expired(entry: Dict[str, Any]) -> bool:
        """判断链接是否已过期（expires_at=0 表示永不过期）。"""
        exp = entry.get("expires_at", 0)
        return exp > 0 and exp < _now_ms()

    @staticmethod
    def is_exhausted(entry: Dict[str, Any]) -> bool:
        """判断链接下载次数是否已达上限（max_downloads=0 表示无限制）。"""
        max_dl = entry.get("max_downloads", 0)
        return max_dl > 0 and entry.get("download_count", 0) >= max_dl


# ------------------------------------------------------------------
# 单例
# ------------------------------------------------------------------

_store: Optional[ShareStore] = None


def get_share_store() -> ShareStore:
    """获取 ShareStore 单例（首次调用时创建，DB 连接懒初始化）。"""
    global _store
    if _store is None:
        _store = ShareStore()
    return _store
