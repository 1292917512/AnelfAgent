"""本地目录同步来源 — NAS 挂载点等本机文件系统目录。"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Tuple

from core.config import get_config

from ..framework import AudioSyncSource, sync_source


def _watch_dir() -> str:
    return str(get_config("audiosync_watch_dir", "") or "").strip()


@sync_source
class LocalDirSource(AudioSyncSource):
    """本地音频监听目录（根目录散装文件 → 单文件单元；子目录 → 文件夹单元）。"""

    key = "local_dir"
    display_name = "本地目录"
    priority = 20

    def is_configured(self) -> bool:
        watch_dir = _watch_dir()
        return bool(watch_dir and os.path.isdir(watch_dir))

    def desc(self) -> str:
        return f"local:{_watch_dir()}"

    async def check_status(self) -> Dict[str, Any]:
        watch_dir = _watch_dir()
        configured = bool(watch_dir)
        reachable = configured and os.path.isdir(watch_dir)
        return {
            "configured": configured,
            "reachable": reachable,
            "latency_ms": 0,
            "error": "" if reachable else ("目录不存在或不可读" if configured else "未配置"),
        }

    async def scan(self, exts: Tuple[str, ...], recursive: bool) -> List[Dict[str, Any]]:
        watch_dir = _watch_dir()
        if not watch_dir or not os.path.isdir(watch_dir):
            return []

        def _walk() -> List[Dict[str, Any]]:
            candidates: List[Dict[str, Any]] = []
            # 根目录散装音频 → 单文件单元
            try:
                root_entries = sorted(os.scandir(watch_dir), key=lambda e: e.name)
            except OSError:
                return []
            for entry in root_entries:
                if entry.is_file() and entry.name.lower().endswith(exts):
                    try:
                        st = entry.stat()
                    except OSError:
                        continue
                    candidates.append({
                        "path": entry.path, "kind": "file",
                        "files": [{
                            "path": entry.path, "name": entry.name,
                            "size": st.st_size, "mtime_ns": st.st_mtime_ns,
                        }],
                    })
            # 含音频的目录 → 文件夹单元（每个目录一个单元）
            for root, dirs, files in os.walk(watch_dir):
                dirs.sort()
                if root == watch_dir:
                    continue
                if not recursive and os.path.dirname(root) != watch_dir:
                    dirs.clear()
                    continue
                audio_files: List[Dict[str, Any]] = []
                for name in sorted(files):
                    if not name.lower().endswith(exts):
                        continue
                    path = os.path.join(root, name)
                    try:
                        st = os.stat(path)
                    except OSError:
                        continue
                    audio_files.append({
                        "path": path, "name": name,
                        "size": st.st_size, "mtime_ns": st.st_mtime_ns,
                    })
                if audio_files:
                    candidates.append({"path": root, "kind": "folder", "files": audio_files})
            return candidates

        return await asyncio.to_thread(_walk)

    async def fetch(self, path: str) -> Tuple[str, bool]:
        if not os.path.isfile(path):
            raise FileNotFoundError(f"源文件不存在: {path}")
        return path, False
