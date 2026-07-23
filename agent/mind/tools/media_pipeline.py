"""MediaPipeline: convert media segments to path-based tags.

Media files are referenced by local path when available; otherwise the
tag carries the remote URL or platform file_id plus a download hint, so
AI can fetch the file on demand (web_download / qq_download_file) and
then process it with tools (recognize_image, voice_to_text, read_file, etc.).
"""

from __future__ import annotations

import os
from typing import List

from core.log import log


class MediaPipeline:
    """Convert media segments to [media_file:type:path] tags."""

    async def process_segments(self, segments: list) -> List[str]:
        """Convert media segments to tag strings for LLM context.

        Returns list of strings like '[media_file:voice:workspace/uploads/voice/xxx.ogg]'.
        Segments without a local file emit a download hint instead of a fake path.
        """
        from core.tags import tag_label

        results: List[str] = []
        for seg in segments:
            seg_type = seg.type.value if hasattr(seg.type, "value") else str(seg.type)
            file_path = getattr(seg, "file_path", "") or ""
            url = getattr(seg, "url", "") or ""
            file_id = getattr(seg, "file_id", "") or ""
            file_name = getattr(seg, "file_name", "") or ""

            # 图片感知索引：投递后台 worker（phash/描述/向量沉淀，支撑文搜图/图搜图）；
            # 本地未下载的 http URL 由 worker 自行下载，索引失败不影响消息管线
            if seg_type == "image":
                try:
                    from core.config import get_config_bool
                    if get_config_bool("image_index_enabled", True):
                        from entities.sticker.worker import submit_image
                        candidate = file_path if file_path and os.path.isfile(file_path) else url
                        if candidate:
                            submit_image(candidate, source="inbound")
                except Exception:
                    pass

            if file_path and os.path.isfile(file_path):
                tag_text = tag_label("media_file", f"{seg_type}:{file_path}")
                results.append(tag_text)
                log(f"media tag: [{seg_type}] {file_path}", "DEBUG", tag="媒体")
                continue

            if not url and not file_id:
                continue

            name_part = f" 文件名: {file_name}" if file_name else ""
            if url.startswith(("http://", "https://")):
                dl = f'web_download(url="{url}"' + (
                    f', filename="{file_name}"' if file_name else "") + ")"
                if seg_type in ("voice", "audio"):
                    hint = f'识别: voice_to_text(url="{url}") 或下载: {dl}'
                else:
                    hint = f"下载: {dl}"
            else:
                hint = f'下载: qq_download_file(file_id="{file_id}")'
            tag_text = (
                f"{tag_label('media_file', f'{seg_type}:未下载')}{name_part} | {hint}"
            )
            results.append(tag_text)
            log(f"media tag: [{seg_type}] 未下载 ({file_name or file_id or url})", "DEBUG", tag="媒体")

        return results
