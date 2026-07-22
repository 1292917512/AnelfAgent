"""微信频道状态持久化 — 凭据 / 长轮询游标 / context_token / typing ticket / 消息去重。

存储目录固定为 ``<项目根>/workspace/weixin/accounts/``：

- ``{account_id}.json``                — 扫码登录保存的账号凭据（chmod 600）
- ``{account_id}.sync.json``           — getupdates 长轮询游标
- ``{account_id}.context-tokens.json`` — 每个对端的最新 context_token
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from core.log import log

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def account_dir() -> Path:
    path = _PROJECT_ROOT / "workspace" / "weixin" / "accounts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def atomic_json_write(path: Path, payload: Dict[str, Any]) -> None:
    """原子写 JSON（temp + fsync + os.replace）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _safe_id(value: Optional[str], keep: int = 8) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "?"
    return raw if len(raw) <= keep else raw[:keep]


# ======================================================================
# 账号凭据
# ======================================================================

def _account_file(account_id: str) -> Path:
    return account_dir() / f"{account_id}.json"


def save_weixin_account(
    *,
    account_id: str,
    token: str,
    base_url: str,
    user_id: str = "",
) -> None:
    """持久化账号凭据供后续复用。"""
    payload = {
        "token": token,
        "base_url": base_url,
        "user_id": user_id,
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path = _account_file(account_id)
    atomic_json_write(path, payload)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def load_weixin_account(account_id: str) -> Optional[Dict[str, Any]]:
    """加载持久化的账号凭据。"""
    path = _account_file(account_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


# ======================================================================
# 长轮询游标
# ======================================================================

def _sync_buf_path(account_id: str) -> Path:
    return account_dir() / f"{account_id}.sync.json"


def load_sync_buf(account_id: str) -> str:
    path = _sync_buf_path(account_id)
    if not path.exists():
        return ""
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("get_updates_buf", "")
    except Exception:
        return ""


def save_sync_buf(account_id: str, sync_buf: str) -> None:
    atomic_json_write(_sync_buf_path(account_id), {"get_updates_buf": sync_buf})


# ======================================================================
# Context token（出站回复必须回显对端最新 token）
# ======================================================================

class ContextTokenStore:
    """落盘的 ``context_token`` 缓存，按 account + peer 键控。"""

    def __init__(self) -> None:
        self._root = account_dir()
        self._cache: Dict[str, str] = {}

    def _path(self, account_id: str) -> Path:
        return self._root / f"{account_id}.context-tokens.json"

    def _key(self, account_id: str, user_id: str) -> str:
        return f"{account_id}:{user_id}"

    def restore(self, account_id: str) -> None:
        path = self._path(account_id)
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            log(f"微信: 恢复 context token 失败 ({_safe_id(account_id)}): {exc}", "WARNING", tag="通道")
            return
        restored = 0
        for user_id, token in data.items():
            if isinstance(token, str) and token:
                self._cache[self._key(account_id, user_id)] = token
                restored += 1
        if restored:
            log(f"微信: 已恢复 {restored} 个 context token ({_safe_id(account_id)})", tag="通道")

    def get(self, account_id: str, user_id: str) -> Optional[str]:
        return self._cache.get(self._key(account_id, user_id))

    def set(self, account_id: str, user_id: str, token: str) -> None:
        self._cache[self._key(account_id, user_id)] = token
        self._persist(account_id)

    def pop(self, account_id: str, user_id: str) -> None:
        """会话过期时清除该 peer 的 token（发送侧降级重试用）。"""
        self._cache.pop(self._key(account_id, user_id), None)
        self._persist(account_id)

    def _persist(self, account_id: str) -> None:
        prefix = f"{account_id}:"
        payload = {
            key[len(prefix):]: value
            for key, value in self._cache.items()
            if key.startswith(prefix)
        }
        try:
            atomic_json_write(self._path(account_id), payload)
        except Exception as exc:
            log(f"微信: 持久化 context token 失败 ({_safe_id(account_id)}): {exc}", "WARNING", tag="通道")


# ======================================================================
# Typing ticket（getconfig 获取，iLink 有效期 600s）
# ======================================================================

class TypingTicketCache:
    """短时效 typing ticket 内存缓存。"""

    def __init__(self, ttl_seconds: float = 600.0):
        self._ttl_seconds = ttl_seconds
        self._cache: Dict[str, Tuple[str, float]] = {}

    def get(self, user_id: str) -> Optional[str]:
        entry = self._cache.get(user_id)
        if not entry:
            return None
        if time.time() - entry[1] >= self._ttl_seconds:
            self._cache.pop(user_id, None)
            return None
        return entry[0]

    def set(self, user_id: str, ticket: str) -> None:
        self._cache[user_id] = (ticket, time.time())


# ======================================================================
# 消息去重
# ======================================================================

class MessageDeduplicator:
    """TTL 去重器（消息 ID + 内容指纹二级去重）。"""

    def __init__(self, ttl_seconds: int = 300, max_size: int = 2000):
        self._ttl_seconds = ttl_seconds
        self._max_size = max_size
        self._seen: Dict[str, float] = {}

    def is_duplicate(self, key: str) -> bool:
        now = time.time()
        self._evict(now)
        if key in self._seen:
            return True
        self._seen[key] = now
        return False

    def _evict(self, now: float) -> None:
        if len(self._seen) < self._max_size:
            expired = [k for k, ts in self._seen.items() if now - ts > self._ttl_seconds]
            for k in expired:
                del self._seen[k]
            return
        # 超限：先清过期，仍超限则保留最新的一半
        expired = [k for k, ts in self._seen.items() if now - ts > self._ttl_seconds]
        for k in expired:
            del self._seen[k]
        if len(self._seen) >= self._max_size:
            ordered = sorted(self._seen.items(), key=lambda kv: kv[1])
            for k, _ in ordered[: len(ordered) // 2]:
                del self._seen[k]
