"""OpenList 同步来源 — 经 OpenList（AList 系）HTTP 访问远程音频目录。"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .. import openlist
from ..framework import AudioSyncSource, sync_source

# OpenList 递归下钻深度上限（防御异常目录结构）
_OPENLIST_MAX_DEPTH = 4


@sync_source
class OpenListSource(AudioSyncSource):
    """OpenList 远程目录（配置后优先于本地目录）。"""

    key = "openlist"
    display_name = "OpenList"
    priority = 10

    def is_configured(self) -> bool:
        return openlist.is_configured()

    def desc(self) -> str:
        return f"openlist:{openlist.configured_root()}"

    async def check_status(self) -> Dict[str, Any]:
        return await openlist.check_status()

    async def scan(self, exts: Tuple[str, ...], recursive: bool) -> List[Dict[str, Any]]:
        root = openlist.configured_root()
        candidates: List[Dict[str, Any]] = []
        listing = await openlist.list_dir(root)
        # 根目录散装音频 → 单文件单元
        for f in listing["files"]:
            if f["name"].lower().endswith(exts):
                candidates.append({"path": f["path"], "kind": "file", "files": [f]})
        # 子目录 → 文件夹单元（按需递归下钻；含音频的目录本身成单元，
        # 其子目录仍继续下钻——与本地目录的扫描语义一致）
        pending = [(d["path"], 1) for d in listing["dirs"]]
        while pending:
            dir_path, depth = pending.pop(0)
            sub = await openlist.list_dir(dir_path)
            audio = [f for f in sub["files"] if f["name"].lower().endswith(exts)]
            if audio:
                candidates.append({"path": dir_path, "kind": "folder", "files": audio})
            if recursive and depth < _OPENLIST_MAX_DEPTH and sub["dirs"]:
                pending.extend((d["path"], depth + 1) for d in sub["dirs"])
        return candidates

    async def fetch(self, path: str) -> Tuple[str, bool]:
        return await openlist.download(path), True
