"""执行过程的来源、目标及标签展示数据，不参与工具路由或授权判断。"""

from __future__ import annotations

import os
from typing import Any

from agent.messages.everything import parse_entity_scope
from core.sanitizer import sanitize_text
from core.tags import etag_all, strip_message_meta_tags
from core.tool_results import parse_tool_result_json
from services.workspace_context import strip_workspace_context


def source_reference(scope: str) -> dict[str, str]:
    """按规范 scope 解码频道、会话类型与对象标识，保留原值用于追溯。"""
    kind, channel, target, session = parse_entity_scope(scope)
    return {"scope": scope, "kind": kind, "channel": channel, "target": target, "session": session}


def presentation_label(text: str) -> str:
    """移除消息信封，保留可读主题；原始内容由执行记录另行保存。"""
    return sanitize_text(strip_message_meta_tags(strip_workspace_context(text))).strip()[:200]


def relative_workspace_path(path: str) -> str:
    """工作区内绝对路径转为前端文件树使用的相对路径，区外保持原值。"""
    if not path or not os.path.isabs(path):
        return path.replace("\\", "/")
    from services.filesystem import workspace_root
    try:
        relative = os.path.relpath(path, workspace_root())
    except (OSError, ValueError):
        return path
    if relative == ".." or relative.startswith(".." + os.sep):
        return path
    return relative.replace(os.sep, "/")


def tool_targets(arguments: str) -> list[dict[str, str]]:
    """提取参数中显式声明的操作对象，不根据工具名或用户文本推断路由。"""
    payload = parse_tool_result_json(arguments)
    if not isinstance(payload, dict):
        return []
    result: list[dict[str, str]] = []
    fields = ("target", "scope", "entity_scope", "channel", "adapter_key", "chat_id", "session_id",
              "uid", "group_id", "entity_name", "entity", "path", "file_path", "directory", "root")
    for key in fields:
        value: Any = payload.get(key)
        if not isinstance(value, (str, int)) or isinstance(value, bool) or value == "":
            continue
        text = sanitize_text(str(value))
        if key in {"path", "file_path", "directory"}:
            text = relative_workspace_path(text)
        tags = etag_all(text) if text.startswith("[") else []
        if tags:
            result.extend({"key": tag, "value": value[:400]} for tag, value in tags[:8])
        else:
            result.append({"key": key, "value": text[:400]})
    return result
