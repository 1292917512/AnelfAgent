"""飞书频道已知会话注册表的持久化。

``_known_chats``（chat_id → 类型/p2p 对端 open_id）是 scope 归一与群/私
分类的事实源：p2p 会话的规范 scope 为 ``user_feishu:{open_id}#{chat_id}``，
发送目标解析与回复历史归并都依赖 chat_id ↔ open_id 的双向映射。纯内存
实现重启即丢失，重启后首条入站消息前的主动发送会再次撕裂会话 scope，
故落盘数据目录 ``<data_dir>/channels/feishu/``（随 ANELF_DATA_DIR 搬迁）。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict

from core.log import log
from core.path import ConfigPaths

_KNOWN_CHATS_FILE = "known_chats.json"
_KNOWN_CHATS_MAX = 500


def feishu_data_dir() -> str:
    """飞书频道数据目录（随 ANELF_DATA_DIR / data_root 搬迁）。"""
    path = os.path.join(os.path.dirname(str(ConfigPaths.SQLITE_DB)), "channels", "feishu")
    os.makedirs(path, exist_ok=True)
    return path


def _known_chats_path() -> str:
    return os.path.join(feishu_data_dir(), _KNOWN_CHATS_FILE)


def load_known_chats() -> Dict[str, Dict[str, Any]]:
    """加载已知会话注册表（文件缺失/损坏时返回空表，不抛异常）。"""
    path = _known_chats_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        chats = payload.get("chats")
        if not isinstance(chats, dict):
            return {}
        return {
            str(chat_id): dict(info)
            for chat_id, info in chats.items()
            if isinstance(info, dict) and chat_id
        }
    except Exception as exc:
        log(f"飞书: 已知会话注册表加载失败（按空表启动）: {exc}", "WARNING", tag="通道")
        return {}


def save_known_chats(chats: Dict[str, Dict[str, Any]]) -> None:
    """持久化已知会话注册表（原子替换；超出上限时按插入序淘汰最旧条目）。"""
    trimmed = dict(list(chats.items())[-_KNOWN_CHATS_MAX:])
    payload = {"updated_at": time.time(), "chats": trimmed}
    tmp = _known_chats_path() + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _known_chats_path())
    except Exception as exc:
        log(f"飞书: 已知会话注册表保存失败: {exc}", "WARNING", tag="通道")
