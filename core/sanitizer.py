"""敏感信息脱敏 — API Key / Token / 密码 / 私钥等模式的自动遮盖。

纯函数实现，供以下场景共用：
- 工具返回结果注入 LLM 上下文前脱敏（think_loop）
- 日志输出脱敏（core.log）
- 错误消息脱敏（llm_manager 之外的通用兜底）
"""
from __future__ import annotations

import re
from typing import List, Tuple

# (模式名, 正则, 替换函数用的保留前缀长度)
_PATTERNS: List[Tuple[str, "re.Pattern[str]", int]] = [
    # Anthropic / OpenAI 风格 API Key
    ("api_key", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}\b"), 7),
    # AWS Access Key
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), 4),
    # GitHub Token
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b"), 4),
    # JWT
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), 4),
    # Bearer Token
    ("bearer", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}\b", re.IGNORECASE), 7),
    # 通用密钥赋值（api_key/password/secret/token = "..."）
    ("credential_assign", re.compile(
        r"(?i)\b(api[_-]?key|passwd|password|secret|access[_-]?token|auth[_-]?token|private[_-]?key)"
        r"([\s]*[=:][\s]*[\"']?)([^\s\"'}{,]{8,})"
    ), 0),
    # 私钥块
    ("private_key_block", re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY(?: BLOCK)?-----"
        r"[\s\S]*?"
        r"-----END (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY(?: BLOCK)?-----"
    ), 0),
]

_MASK = "****"


def _mask_value(value: str, keep_prefix: int) -> str:
    """遮盖敏感值，保留少量前缀便于识别类型。"""
    if keep_prefix > 0 and len(value) > keep_prefix + 4:
        return f"{value[:keep_prefix]}{_MASK}"
    return _MASK


def sanitize_text(text: str) -> str:
    """检测并遮盖文本中的敏感信息（API Key、Token、密码、私钥等）。"""
    if not text:
        return text

    result = text
    for name, pattern, keep_prefix in _PATTERNS:
        if name == "credential_assign":
            # 保留 "key=" 部分，仅遮盖值
            result = pattern.sub(
                lambda m: f"{m.group(1)}{m.group(2)}{_MASK}",
                result,
            )
        elif name == "private_key_block":
            result = pattern.sub("[PRIVATE KEY REDACTED]", result)
        else:
            result = pattern.sub(lambda m: _mask_value(m.group(0), keep_prefix), result)
    return result


def contains_sensitive(text: str) -> bool:
    """快速检测文本是否包含敏感模式（不做替换）。"""
    if not text:
        return False
    return any(pattern.search(text) for _, pattern, _ in _PATTERNS)


# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

_SANITIZER_CONFIGS = {
    "安全": {
        "security_sanitize_enabled": {
            "description": "是否对工具返回结果和日志自动脱敏（API Key/Token/密码遮盖）",
            "default": True,
        },
    },
}

from core.config import register_configs_safe  # noqa: E402

register_configs_safe(_SANITIZER_CONFIGS)


def is_sanitize_enabled() -> bool:
    """脱敏总开关。"""
    from core.config import get_config_bool
    return get_config_bool("security_sanitize_enabled", True)
