"""从实际装配结果生成有界的上下文观测。

模型输入保持原样；技能事实只随 _source 到观测面，发送前剥离。
不增加模型 token，不改变任何缓存前缀或技能选择策略。
"""

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


def context_skills(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按最终消息范围提取可见技能，预算外候选不显示为已注入。"""
    skills: list[dict[str, Any]] = []
    for message in messages:
        source = message.get("_source") or {}
        if source.get("origin") != "skill_match":
            continue
        content = str(message.get("content", "")).removesuffix("\n（已截断）")
        for fact in source.get("skills", []):
            if fact["heading_end"] > len(content):
                continue
            skills.append({
                "name": sanitize_text(fact["name"])[:200],
                "description": sanitize_text(fact["description"])[:300],
                "state": fact["state"], "mode": fact["mode"], "score": fact["score"],
                "truncated": fact["truncated"] or fact["end"] > len(content),
            })
            if len(skills) >= 16:
                return skills
    return skills


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
        "skills": context_skills(messages),
        "block_count": total, "truncated": remaining < 0 or total > len(blocks),
    })
