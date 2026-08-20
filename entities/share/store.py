"""ShareStore：分享链接的持久化存储。

存储：独立 SQLite 库（派生自主库路径，``{stem}_share.sqlite3``），WAL 模式。
单表设计，异步访问，asyncio.Lock 保护并发。

生命周期：通过 Lifecycle.register("share_store", ...) 注册，
on_tick 定期清理过期链接（sweep_expired）。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

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
    status TEXT NOT NULL DEFAULT 'active',
    share_type TEXT NOT NULL DEFAULT 'file',
    target_url TEXT NOT NULL DEFAULT '',
    media_kind TEXT NOT NULL DEFAULT ''
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
"""

# 分享类型
SHARE_TYPE_FILE = "file"
SHARE_TYPE_MEDIA = "media"
SHARE_TYPE_LINK = "link"
_SHARE_TYPES = (SHARE_TYPE_FILE, SHARE_TYPE_MEDIA, SHARE_TYPE_LINK)

# 扩展名 → 媒体种类（预览页按此选择渲染方式）
_MEDIA_EXT_MAP: Dict[str, str] = {
    **{e: "image" for e in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg")},
    **{e: "video" for e in (".mp4", ".webm", ".mov", ".mkv", ".avi", ".flv")},
    **{e: "audio" for e in (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".amr", ".opus")},
    ".pdf": "pdf",
    ".html": "html",
    ".htm": "html",
}

_EXPIRES_MAP: Dict[str, int] = {
    "1h": 3600_000,
    "6h": 6 * 3600_000,
    "24h": 24 * 3600_000,
    "7d": 7 * 24 * 3600_000,
    "30d": 30 * 24 * 3600_000,
    "never": 0,  # 0 表示永不过期
}

# 下载路由路径（router.py 挂载点 + /d/{token}），工具层拼 URL 时复用
DOWNLOAD_PATH = "/api/entity/share/d"
# 预览页路由路径（/v/{token}），media/link 类型的主链接
VIEW_PATH = "/api/entity/share/v"


def build_download_url(token: str, base_url: str = "") -> str:
    """拼接下载链接。base_url 为公网基址，空则返回相对路径。"""
    base = base_url.rstrip("/")
    return f"{base}{DOWNLOAD_PATH}/{token}"


def build_view_url(token: str, base_url: str = "") -> str:
    """拼接预览页链接。base_url 为公网基址，空则返回相对路径。"""
    base = base_url.rstrip("/")
    return f"{base}{VIEW_PATH}/{token}"


def detect_media_kind(file_name: str) -> str:
    """按扩展名检测可渲染媒体种类（image/video/audio/pdf/html），不可渲染返回空串。"""
    ext = os.path.splitext(file_name)[1].lower()
    return _MEDIA_EXT_MAP.get(ext, "")


def _link_name_from_url(url: str) -> str:
    """从 URL 推导链接分享的展示名称（host + path 摘要）。"""
    parts = urlsplit(url)
    name = parts.netloc or url
    if parts.path and parts.path != "/":
        name = f"{name}{parts.path}"
    return name[:120]


def get_public_base_url() -> str:
    """读取配置的公网基址（实体配置项 share_public_base_url）。"""
    from core.config import get_config
    return str(get_config("share_public_base_url", "") or "")


def _derive_db_path() -> str:
    """派生分享库默认路径：主库同目录，stem + '_share'。"""
    from core.storage_volume import main_sqlite_path

    stem, ext = os.path.splitext(main_sqlite_path())
    return f"{stem}_share{ext or '.sqlite3'}"


def _register_volume() -> None:
    from core.storage_volume import VolumeDescriptor, VolumeKind, register_volume

    register_volume(VolumeDescriptor(
        volume_id="share",
        name="分享库",
        description="分享链接与下载审计",
        kind=VolumeKind.SQLITE,
        default_path=_derive_db_path,
    ))


_register_volume()


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
        "share_type": row["share_type"],
        "target_url": row["target_url"],
        "media_kind": row["media_kind"],
    }


class ShareStore:
    """分享链接的统一存储（懒初始化单例）。"""

    def __init__(self, db_path: Optional[str] = None) -> None:
        from core.storage_volume import get_volume_registry

        self._db_path = db_path or get_volume_registry().resolve_path("share")
        get_volume_registry().mark_active("share", self._db_path)
        self._db: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

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
            self._db = db
            log(f"ShareStore 就绪: {self._db_path}", tag="分享")
            return db

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        file_path: str = "",
        description: str = "",
        expires_in: str = "24h",
        created_by: str = "",
        max_downloads: int = 0,
        share_type: str = SHARE_TYPE_FILE,
        target_url: str = "",
    ) -> Dict[str, Any]:
        """创建分享链接。

        share_type 三种取值：
        - file: 文件下载（沙箱校验 + 强制下载）
        - media: 媒体渲染（按扩展名检测 media_kind，预览页内嵌渲染）
        - link: 网址推送（target_url 必填，落地页 iframe 嵌入 + 直接访问）

        相同内容复用旧 token 避免重复：file/media 按 (file_path, content_hash)，
        link 按 target_url。
        """
        if share_type not in _SHARE_TYPES:
            raise ValueError(f"不支持的分享类型: {share_type}（可选 {'/'.join(_SHARE_TYPES)}）")

        db = await self._get_db()
        now = _now_ms()

        media_kind = ""
        content_hash = ""
        file_size = 0

        if share_type == SHARE_TYPE_LINK:
            url = target_url.strip()
            if not url:
                raise ValueError("link 类型分享必须提供 target_url")
            if not (url.startswith("http://") or url.startswith("https://")):
                raise ValueError("target_url 必须以 http:// 或 https:// 开头")
            file_path = url
            file_name = _link_name_from_url(url)
        else:
            if not file_path:
                raise ValueError("file/media 类型分享必须提供 file_path")
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
            if share_type == SHARE_TYPE_MEDIA:
                media_kind = detect_media_kind(file_name)
                if not media_kind:
                    raise ValueError(
                        f"该文件类型不可渲染（{file_name}），media 分享仅支持 "
                        "图片/视频/音频/PDF/HTML，其他文件请用 file 类型下载分享"
                    )

        expires_ms = _EXPIRES_MAP.get(expires_in, _EXPIRES_MAP["24h"])
        expires_at = now + expires_ms if expires_ms > 0 else 0
        from core.config import get_config_int
        token_len = max(8, min(64, get_config_int("share_token_length", 22)))

        # 查重：file/media 同路径同 hash、link 同目标 URL 的 active 链接直接复用
        if share_type == SHARE_TYPE_LINK:
            dedup_sql = (
                "SELECT * FROM share_links WHERE share_type='link' "
                "AND target_url=? AND status='active'"
            )
            dedup_params: tuple = (target_url.strip(),)
        else:
            dedup_sql = (
                "SELECT * FROM share_links WHERE file_path=? AND content_hash=? "
                "AND share_type=? AND status='active'"
            )
            dedup_params = (file_path, content_hash, share_type)

        async with self._lock:
            cursor = await db.execute(dedup_sql, dedup_params)
            existing = await cursor.fetchone()
            if existing:
                entry = _row_to_entry(existing)
                entry["deduplicated"] = True
                return entry

            token = _new_token(token_len)
            await db.execute(
                "INSERT INTO share_links(token, file_path, file_name, file_size, "
                "content_hash, description, expires_at, created_at, created_by, "
                "download_count, last_download_at, max_downloads, status, "
                "share_type, target_url, media_kind) "
                "VALUES(?,?,?,?,?,?,?,?,?,0,0,?,?,?,?,?)",
                (token, file_path, file_name, file_size, content_hash,
                 description, expires_at, now, created_by, max_downloads, "active",
                 share_type, target_url.strip(), media_kind),
            )
            await db.commit()

        log(
            f"分享链接已创建: [{share_type}] {file_name} "
            f"(token={token[:8]}..., expires={expires_in})",
            tag="分享",
        )
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
            "share_type": share_type,
            "target_url": target_url.strip(),
            "media_kind": media_kind,
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
        from core.config import get_config_bool
        if not get_config_bool("share_audit_enabled", True):
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
