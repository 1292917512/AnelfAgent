"""GitHub 事件和消息字段的可选项目录。"""

from __future__ import annotations

import json
from pathlib import Path

EVENT_ACTIONS: dict[str, list[str]] = json.loads(
    Path(__file__).with_name("event_catalog.json").read_text(encoding="utf-8")
)
EVENT_OPTIONS = ["*", *sorted(EVENT_ACTIONS), *sorted(
    f"{name}.{action}" for name, actions in EVENT_ACTIONS.items() for action in actions if action
)]
MESSAGE_FIELDS = ["time", "actor", "body", "url", "context", "local_path"]


def validate_events(values: list[str]) -> list[str]:
    """规范化事件选择，允许符合命名规则的未来事件。"""
    import re

    normalized = list(dict.fromkeys(value.strip().lower() for value in values))
    if any(value != "*" and not re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)?", value)
           for value in normalized):
        raise ValueError("事件须为事件族、event.action 或 *")
    return normalized
