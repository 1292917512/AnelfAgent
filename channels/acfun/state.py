"""AcFun 频道状态 — 登录凭据（cookie）的持久化。

cookie 是真实凭据，落数据目录 ``<data_dir>/channels/acfun/``（不落仓库目录）；
轮询游标（防重放）由 agent.channel.poll_cursor.PollCursorStore 承载。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional

from core.log import log
from core.path import ConfigPaths

_COOKIE_FILE = "cookies.json"
_POLL_STATE_FILE = "poll_state.json"


def acfun_data_dir() -> str:
    """AcFun 频道数据目录（随 ANELF_DATA_DIR / data_root 搬迁）。"""
    path = os.path.join(os.path.dirname(str(ConfigPaths.SQLITE_DB)), "channels", "acfun")
    os.makedirs(path, exist_ok=True)
    return path


def poll_state_path() -> str:
    """通知轮询游标文件路径。"""
    return os.path.join(acfun_data_dir(), _POLL_STATE_FILE)


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
