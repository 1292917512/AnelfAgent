"""执行过程的来源、目标及标签展示数据，不参与工具路由或授权判断。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agent.messages.everything import parse_entity_scope
from core.entity import EntityRegistry
from core.path import project_root
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


def _file_location(path: Path) -> dict[str, str]:
    """将绝对路径映射到可浏览的文件根，区外路径只展示、不创建错误链接。"""
    from services.filesystem import workspace_root

    if not path.is_absolute():
        return {"root": "unresolved"}
    path = path.resolve()
    for root, base in (("workspace", workspace_root()), ("project", project_root())):
        try:
            relative = path.relative_to(Path(base).resolve()).as_posix()
        except ValueError:
            continue
        return {"root": root, "path": relative} if len(relative) <= 400 else {"root": "unresolved"}
    return {"root": "external", "path": str(path)[:400]}


def tool_targets(arguments: str, tool_name: str = "") -> list[dict[str, str]]:
    """提取显式操作对象，文件位置由工具声明的解析器提供，保留原始索引键。"""
    payload = parse_tool_result_json(arguments)
    if not isinstance(payload, dict):
        return []
    result: list[dict[str, str]] = []
    entity = EntityRegistry.get(tool_name) if tool_name else None
    resolver = entity.path_resolver if entity else None
    fields = ("target", "scope", "entity_scope", "channel", "adapter_key", "chat_id", "session_id",
              "uid", "group_id", "entity_name", "entity", "path", "file_path", "directory", "root")
    for key in fields:
        value: Any = payload.get(key)
        if not isinstance(value, (str, int)) or isinstance(value, bool) or value == "":
            continue
        text = sanitize_text(str(value))
        location: dict[str, str] = {}
        if key in {"path", "file_path", "directory"}:
            try:
                if resolver:
                    location = _file_location(resolver(str(value)))
                elif os.path.isabs(str(value)):
                    location = _file_location(Path(str(value)))
            except Exception:
                location = {"root": "unresolved"}
            location = {name: sanitize_text(item) for name, item in location.items()}
        tags = etag_all(text) if text.startswith("[") else []
        if tags:
            result.extend({"key": tag, "value": value[:400]} for tag, value in tags[:8])
        else:
            result.append({"key": key, "value": text[:400], **location})
    return result
