"""MediaPipeline: convert media segments to path-based tags.

All media files are referenced by local path. AI uses tools
(recognize_image, voice_to_text, read_file, etc.) to process them.
"""

from __future__ import annotations

from typing import List

from core.log import log


class MediaPipeline:
    """Convert media segments to [media_file:type:path] tags."""

    async def process_segments(self, segments: list) -> List[str]:
        """Convert media segments to tag strings for LLM context.

        Returns list of strings like '[media_file:voice:workspace/uploads/voice/xxx.ogg]'.
        """
        from agent.channel.schemas import SegmentType
        from core.tags import tag_label

        results: List[str] = []
        for seg in segments:
            path = getattr(seg, "file_path", "") or getattr(seg, "url", "")
            if not path:
                continue

            seg_type = seg.type.value if hasattr(seg.type, "value") else str(seg.type)
            tag_text = tag_label("media_file", f"{seg_type}:{path}")
            results.append(tag_text)
            log(f"media tag: [{seg_type}] {path}", "DEBUG", tag="媒体")

        return results
