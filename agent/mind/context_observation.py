"""从实际装配结果生成有界的上下文观测，不改写模型消息。"""

from __future__ import annotations

from typing import Any

from core.activity import ACTIVITY_TEXT_LIMIT, EVENT_ACTIVITY_CONTEXT
from core.event_bus import event_bus
from core.sanitizer import sanitize_text

_LABELS = {
    "memory": "记忆召回与技能候选", "profile": "实体画像", "relation": "记忆关联",
    "hub": "主标签记忆", "context": "便签与文件索引", "volatile": "短期记忆",
}
_SOURCE_LABELS = {
    "memory_recall": "记忆召回", "cross_channel_recall": "跨频道召回",
    "cross_channel_narrative": "跨频道关联", "skill_match": "技能匹配与显式调用",
}


async def emit_context_summary(messages: list[dict[str, Any]], duration_ms: int) -> None:
    """发布最终进入上下文的记忆与技能块，保留标签和预算截断事实。"""
    blocks: list[dict[str, str]] = []
    remaining = ACTIVITY_TEXT_LIMIT
    total = 0
    for message in messages:
        layer = message.get("_layer", "")
        if layer not in _LABELS or not message.get("content"):
            continue
        total += 1
        content = sanitize_text(str(message["content"]))
        if remaining > 0 and len(blocks) < 16:
            source = message.get("_source") or {}
            label = _SOURCE_LABELS.get(source.get("origin", ""), _LABELS[layer])
            blocks.append({"layer": layer, "label": label, "content": content[:remaining]})
            remaining -= len(content)
    await event_bus.emit(EVENT_ACTIVITY_CONTEXT, {
        "status": "done", "duration_ms": duration_ms, "blocks": blocks,
        "block_count": total, "truncated": remaining < 0 or total > len(blocks),
    })
