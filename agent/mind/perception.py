"""外部消息排队规则与技能手势识别。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent.messages import (
    Everything,
    EverythingGroup,
)
from core.log import log

if TYPE_CHECKING:
    from agent.mind.mind import Mind


def detect_skill_gesture(mind: Mind, anything: Everything, scope: str) -> None:
    """检测 /name 技能手势并登记待注入（recollection 消费后清空）。"""
    try:
        from core.config import get_config_bool
        if not get_config_bool("skill_user_gesture_enabled", True):
            return
        from agent.skills.gesture import parse_skill_gesture
        from core.tags import strip_message_meta_tags
        text = strip_message_meta_tags(anything.get_text_content() or "").strip()
        name = parse_skill_gesture(text)
        if not name:
            return
        pending = mind._pending_skill_gestures.setdefault(scope, [])
        if len(pending) < 3 and name not in pending:
            pending.append(name)
            log(f"识别到技能手势 /{name}，将在召回时确定性注入: {scope}", "DEBUG", tag="技能")
    except Exception:
        pass  # 手势检测失败不影响消息入队


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
