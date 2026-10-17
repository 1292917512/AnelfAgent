"""外部消息排队规则。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent.messages import (
    Everything,
    EverythingGroup,
)
from core.log import log

if TYPE_CHECKING:
    from agent.mind.mind import Mind


def should_enqueue_external_message(mind: Mind, anything: Everything) -> bool:
    """判断外部消息是否应进入待回复队列。"""
    if not bool(getattr(anything, "trigger_mind", True)):
        return False
    text = (anything.get_text_content() or "").strip()
    has_media = bool(getattr(anything, "images", None) or getattr(anything, "media_segments", None))
    if not text and not has_media:
        log(f"忽略空消息入队: {anything.entity_scope}", "DEBUG", tag="思维")
        return False
    if (
        mind._reflecting
        and isinstance(anything, EverythingGroup)
        and not bool(getattr(anything, "to_me", False))
    ):
        return False
    return True
