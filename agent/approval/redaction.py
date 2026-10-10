"""权限评审和审计共用的参数脱敏。"""

import re
from typing import Any

from core.sanitizer import sanitize_text, truncate_middle

_SENSITIVE = re.compile(
    r"(?:^|[_-])(?:api[_-]?key|token|password|passwd|secret|auth|authorization|private[_-]?key)(?:$|[_-])",
    re.IGNORECASE,
)


def redact_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """递归遮盖凭据，保留配置项名称用于评估配置修改。"""
    def clean(value: Any, depth: int = 0) -> Any:
        if depth > 12:
            return "[嵌套内容省略]"
        if isinstance(value, dict):
            setting = value.get("key")
            secret_setting = isinstance(setting, str) and bool(_SENSITIVE.search(setting))
            return {
                str(key): "***REDACTED***"
                if _SENSITIVE.search(str(key)) or (key == "value" and secret_setting)
                else clean(item, depth + 1)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [clean(item, depth + 1) for item in value[:100]]
        if isinstance(value, str):
            return truncate_middle(sanitize_text(value), 2000)
        return value

    result = clean(arguments)
    return result if isinstance(result, dict) else {}
