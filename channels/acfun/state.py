"""AcFun 频道状态 — 登录凭据（cookie）的持久化。

cookie 是真实凭据，落数据目录 ``<data_dir>/channels/acfun/``（不落仓库目录）；
读写纪律（原子写 + chmod 600 + 容错读取）收敛在 channels._shared.state；
轮询游标（防重放）由 agent.channel.poll_cursor.PollCursorStore 承载。
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from channels._shared.state import (
    channel_data_dir,
    clear_json_credential,
    load_json_credential,
    save_json_credential,
)

_COOKIE_FILE = "cookies.json"
_POLL_STATE_FILE = "poll_state.json"
_LOG_NAME = "AcFun"


def acfun_data_dir() -> str:
    """AcFun 频道数据目录（随 ANELF_DATA_DIR / data_root 搬迁）。"""
    return channel_data_dir("acfun")


def poll_state_path() -> str:
    """通知轮询游标文件路径。"""
    return os.path.join(acfun_data_dir(), _POLL_STATE_FILE)


def _cookie_path() -> str:
    return os.path.join(acfun_data_dir(), _COOKIE_FILE)


def save_cookies(username: str, uid: Any, cookies: Dict[str, str]) -> None:
    """持久化登录 cookie（JSON 明文，与 acfunsdk 的 B64 文件格式无关）。"""
    save_json_credential(_cookie_path(), {
        "username": username,
        "uid": str(uid or ""),
        "cookies": cookies,
    }, log_name=_LOG_NAME)


def load_cookies() -> Optional[Dict[str, Any]]:
    """读取已保存的登录凭据；不存在或损坏返回 None。"""
    return load_json_credential(_cookie_path(), log_name=_LOG_NAME)


def clear_cookies() -> None:
    """清除登录凭据（退出登录）。"""
    clear_json_credential(_cookie_path(), log_name=_LOG_NAME)
