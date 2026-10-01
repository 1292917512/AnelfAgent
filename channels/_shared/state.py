"""频道数据目录与凭据持久化 — JSON 凭据的原子写 + 权限收紧 + 容错读取。

凭据（cookie/token）是真实机密：统一落数据目录 ``<data_dir>/channels/<id>/``
（不落仓库目录），写入一律 tmp + os.replace 原子替换并 chmod 600——
崩溃留半截 JSON 与默认 umask 权限都曾造成登录态丢失/凭据暴露。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional

from core.log import log
from core.path import ConfigPaths


def channel_data_dir(channel_id: str) -> str:
    """频道数据目录（随 ANELF_DATA_DIR / data_root 搬迁），不存在则创建。"""
    path = os.path.join(os.path.dirname(str(ConfigPaths.SQLITE_DB)), "channels", channel_id)
    os.makedirs(path, exist_ok=True)
    return path


def save_json_credential(path: str, payload: Dict[str, Any], *, log_name: str) -> None:
    """凭据原子落盘（tmp + replace，chmod 600）。saved_at 由本函数统一补记。"""
    data = {**payload, "saved_at": time.time()}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    log(f"{log_name}: 登录凭据已保存", tag="通道")


def load_json_credential(path: str, *, log_name: str) -> Optional[Dict[str, Any]]:
    """读取凭据；不存在/损坏/无 cookies 字段返回 None。"""
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data.get("cookies"), dict) or not data["cookies"]:
            return None
        return data
    except Exception as exc:
        log(f"{log_name}: 凭据文件解析失败 ({path}): {exc}", "WARNING", tag="通道")
        return None


def clear_json_credential(path: str, *, log_name: str) -> None:
    """清除凭据（退出登录）。"""
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError as exc:
        log(f"{log_name}: 凭据清除失败: {exc}", "WARNING", tag="通道")
