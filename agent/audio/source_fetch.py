"""音源取回注册表 — 回听/分析定位原始音频文件的统一入口。

核心层只定义"路径 → 本地文件"的取回契约；具体来源（本地目录、OpenList
远程下载等）以组件形式从实体经 entities._sdk.register_audio_source_fetcher
注册。未注册任何取回器时仅支持本地文件直读。
"""

from __future__ import annotations

import os
from typing import Any, Awaitable, Callable, Dict, List, Tuple

from core.log import log

_LOG_TAG = "音频"

# 取回器契约：async (source_path) -> (local_path, is_temp)
# is_temp=True 表示临时文件（调用方用后负责删除）
SourceFetcher = Callable[[str], Awaitable[Tuple[str, bool]]]

_FETCHERS: List[Dict[str, Any]] = []


def register_source_fetcher(name: str, fetcher: SourceFetcher, priority: int = 50) -> None:
    """注册音源取回器（同名覆盖；priority 小值优先）。"""
    global _FETCHERS
    _FETCHERS[:] = [f for f in _FETCHERS if f["name"] != name]
    _FETCHERS.append({"name": name, "fetcher": fetcher, "priority": priority})
    _FETCHERS.sort(key=lambda f: f["priority"])
    log(f"音源取回器已注册: {name} (priority={priority})", "DEBUG", tag=_LOG_TAG)


def unregister_source_fetcher(name: str) -> None:
    _FETCHERS[:] = [f for f in _FETCHERS if f["name"] != name]


def list_source_fetchers() -> List[str]:
    return [f["name"] for f in _FETCHERS]


async def fetch_source(source_path: str) -> Tuple[str, bool]:
    """按优先级链取回源文件本地路径，返回 (local_path, is_temp)。

    Raises:
        FileNotFoundError: 所有取回器均无法提供该文件。
    """
    errors: List[str] = []
    for entry in _FETCHERS:
        try:
            local, is_temp = await entry["fetcher"](source_path)
        except Exception as exc:
            errors.append(f"{entry['name']}: {exc}")
            continue
        if local and os.path.isfile(local):
            return local, is_temp
        errors.append(f"{entry['name']}: 取回结果不可用")
    if os.path.isfile(source_path):
        return source_path, False
    detail = "；".join(errors) if errors else "本地不存在且未注册音源取回器"
    raise FileNotFoundError(f"源文件不可达: {source_path}（{detail}）")
