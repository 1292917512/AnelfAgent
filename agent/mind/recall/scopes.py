"""从对话与媒体标签识别召回关联对象，不执行检索。"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Dict, List

if TYPE_CHECKING:
    from agent.messages import Everything

_RELATED_UID_RE = re.compile(r"\[(?:uid|at_uid):([^\]]+)\]")


def _speaker_scopes(text: str) -> List[str]:
    """解析 [speaker_scope:...] / [face_scope:...] 标签：声纹/人脸识别绑定的实体（画像召回键）。

    标签值为音频库/人脸库绑定格式（user:qq:123），转换为召回层权威的
    entity_scope 格式（user_qq:123）；agent: 绑定是自我指涉（自我画像恒注入）
    不参与召回。声纹（听到的是谁）与人脸（看到的是谁）共用同一召回机制。
    """
    from core.tags import etag_all
    scopes: List[str] = []
    for key, value in etag_all(text or ""):
        if key not in ("speaker_scope", "face_scope"):
            continue
        for prefix in ("user:", "group:"):
            if value.startswith(prefix):
                scopes.append(f"{prefix[:-1]}_{value[len(prefix):]}")
                break
    return scopes


def _extract_related_scopes(
        conversation_tail: List[Dict], primary_scope: str,
) -> List[str]:
    """从对话中提取涉及的实体 scope，构建画像加载列表。

    - [speaker_scope:...] / [face_scope:...]（声纹/人脸识别的绑定实体）：私聊/通话/群聊均生效
    - [uid:] / [at_uid:]（群成员）：uid 是 adapter 相对引用，仅群聊场景参与
    """
    is_group = primary_scope.startswith("group_")
    adapter = ""
    if is_group:
        from agent.messages import parse_entity_scope
        _, adapter, _, _ = parse_entity_scope(primary_scope)
    seen: set[str] = {primary_scope}
    scopes: List[str] = []
    for msg in conversation_tail:
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue
        for scope in _speaker_scopes(content):
            if scope not in seen:
                seen.add(scope)
                scopes.append(scope)
        if not is_group:
            continue
        from agent.messages import build_entity_scope
        for m in _RELATED_UID_RE.finditer(content):
            uid = m.group(1)
            if uid == "all":
                continue
            scope = build_entity_scope("user", adapter, uid)
            if scope not in seen:
                seen.add(scope)
                scopes.append(scope)
    return scopes


def _extract_scopes_from_anything(
        anything: "Everything", primary_scope: str,
) -> List[str]:
    """从当前消息对象提取发送者 uid 和 [at_uid:xxx] 中的 uid。"""
    from agent.messages import build_entity_scope
    adapter = str(getattr(anything, "adapter_key", "") or "")
    seen: set[str] = {primary_scope}
    scopes: List[str] = []
    if anything.uid and anything.uid not in (0, "0"):
        scope = build_entity_scope("user", adapter, str(anything.uid))
        if scope not in seen:
            seen.add(scope)
            scopes.append(scope)
    content = anything.get_text_content() if hasattr(anything, "get_text_content") else ""
    if content:
        for scope in _speaker_scopes(content):
            if scope not in seen:
                seen.add(scope)
                scopes.append(scope)
        for m in _RELATED_UID_RE.finditer(content):
            uid = m.group(1)
            if uid == "all":
                continue
            scope = build_entity_scope("user", adapter, uid)
            if scope not in seen:
                seen.add(scope)
                scopes.append(scope)
    return scopes
