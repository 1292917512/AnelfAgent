"""AcFun 频道状态 — 登录凭据（cookie）与通知轮询游标的持久化。

cookie 是真实凭据，落数据目录 ``<data_dir>/channels/acfun/``（不落仓库目录）；
轮询游标记录各通知类型已见条目键集合（有界 LRU），防重启后重复触发历史通知。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Set

from core.log import log
from core.path import ConfigPaths

_COOKIE_FILE = "cookies.json"
_POLL_STATE_FILE = "poll_state.json"
_MAX_SEEN_PER_KIND = 200


def acfun_data_dir() -> str:
    """AcFun 频道数据目录（随 ANELF_DATA_DIR / data_root 搬迁）。"""
    path = os.path.join(os.path.dirname(str(ConfigPaths.SQLITE_DB)), "channels", "acfun")
    os.makedirs(path, exist_ok=True)
    return path


def _cookie_path() -> str:
    return os.path.join(acfun_data_dir(), _COOKIE_FILE)


def save_cookies(username: str, uid: Any, cookies: Dict[str, str]) -> None:
    """持久化登录 cookie（JSON 明文，与 acfunsdk 的 B64 文件格式无关）。"""
    payload = {
        "username": username,
        "uid": str(uid or ""),
        "saved_at": time.time(),
        "cookies": cookies,
    }
    with open(_cookie_path(), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    log(f"AcFun: 登录凭据已保存 uid={uid}", tag="通道")


def load_cookies() -> Optional[Dict[str, Any]]:
    """读取已保存的登录凭据；不存在或损坏返回 None。"""
    path = _cookie_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data.get("cookies"), dict) or not data["cookies"]:
            return None
        return data
    except Exception as exc:
        log(f"AcFun: 凭据文件解析失败 ({path}): {exc}", "WARNING", tag="通道")
        return None


def clear_cookies() -> None:
    """清除登录凭据（退出登录）。"""
    path = _cookie_path()
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError as exc:
        log(f"AcFun: 凭据清除失败: {exc}", "WARNING", tag="通道")


class PollCursorStore:
    """通知轮询游标：每类通知维护一个已见键的有界集合，持久化到数据目录。

    首次运行（某类无任何已见键）时只做播种不派发，避免启动时把历史通知重放给思维。
    """

    def __init__(self) -> None:
        self._seen: Dict[str, List[str]] = {}
        self._seeded: Set[str] = set()
        self._dirty = False

    @property
    def _path(self) -> str:
        return os.path.join(acfun_data_dir(), _POLL_STATE_FILE)

    def load(self) -> None:
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            self._seen = {str(k): [str(x) for x in v] for k, v in data.get("seen", {}).items()}
            self._seeded = {str(x) for x in data.get("seeded", [])}
        except Exception as exc:
            log(f"AcFun: 轮询游标解析失败，按空状态重建: {exc}", "DEBUG", tag="通道")
            self._seen = {}
            self._seeded = set()

    def save(self) -> None:
        """落盘（仅在发生变更后由轮询循环调用）。"""
        if not self._dirty:
            return
        payload = {"seen": self._seen, "seeded": sorted(self._seeded)}
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            self._dirty = False
        except OSError as exc:
            log(f"AcFun: 轮询游标保存失败: {exc}", "WARNING", tag="通道")

    def is_seen(self, kind: str, key: str) -> bool:
        return key in self._seen.get(kind, [])

    def is_seeded(self, kind: str) -> bool:
        return kind in self._seeded

    def mark_seeded(self, kind: str) -> None:
        self._seeded.add(kind)
        self._dirty = True

    def mark(self, kind: str, key: str) -> None:
        """登记已见键（最新在尾部，超出上限淘汰最旧）。"""
        seen = self._seen.setdefault(kind, [])
        if key in seen:
            return
        seen.append(key)
        del seen[: max(0, len(seen) - _MAX_SEEN_PER_KIND)]
        self._dirty = True
