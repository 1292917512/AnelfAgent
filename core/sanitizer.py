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
    # 值要求 ≥8 字符且不含 ,}（避免匹配 JSON 结构字符和短布尔/枚举值如 true/none）；
    # 短于 8 字符的真实密钥极少见，放宽阈值会显著增加误报（password: 123456 等场景）
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
    # URL 内联凭证（scheme://user:pass@host）—— 记忆/便签中常见泄漏形式
    ("url_credential", re.compile(
        r"\b([a-zA-Z][a-zA-Z0-9+.-]{1,15}://)([^/\s:@]+):([^@\s/]+)@"
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
        elif name == "url_credential":
            # 保留 scheme 与 host，仅遮盖 user:pass
            result = pattern.sub(lambda m: f"{m.group(1)}{_MASK}@", result)
        else:
            result = pattern.sub(lambda m: _mask_value(m.group(0), keep_prefix), result)
    return result


def contains_sensitive(text: str) -> bool:
    """快速检测文本是否包含敏感模式（不做替换）。"""
    if not text:
        return False
    return any(pattern.search(text) for _, pattern, _ in _PATTERNS)


# ------------------------------------------------------------------
# 上下文出向边界（LLM-bound）清洗原语
# ------------------------------------------------------------------

# 孤代理对（U+D800–U+DFFF）会让部分 LLM 提供商直接 400，整轮对话作废
_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")

# 中部截断标记（保留尾部：近期的内容通常比开头更重要）
_TRUNCATION_MARKER = "\n...[内容过长，中部已省略]...\n"


def clean_surrogates(text: str) -> str:
    """移除孤代理对字符（UTF-8 无法编码，部分 LLM 接口会因此拒绝整个请求）。"""
    if not text:
        return text
    return _SURROGATE_RE.sub("", text)


def has_surrogates(text: str) -> bool:
    """快速检测文本是否包含孤代理对字符（不做替换）。"""
    return bool(text) and bool(_SURROGATE_RE.search(text))


def truncate_middle(
        text: str,
        max_chars: int,
        *,
        head_ratio: float = 0.7,
        marker: str = _TRUNCATION_MARKER,
) -> str:
    """超长文本中部截断：保留头部与尾部，中间以标记替代。

    与粗暴的 text[:max] 不同，尾部通常包含最近/最关键的信息
    （记忆召回的尾部分数最高、错误日志的尾部是堆栈），必须保留。

    Args:
        text: 原文
        max_chars: 结果最大字符数（含标记）
        head_ratio: 头部占比（默认 0.7，尾部 0.3）
    """
    if not text or len(text) <= max_chars:
        return text
    budget = max_chars - len(marker)
    if budget <= 0:
        return text[:max_chars]
    head = int(budget * head_ratio)
    tail = budget - head
    return text[:head] + marker + text[len(text) - tail:]


def sanitize_for_context(text: str, max_chars: int = 6000) -> str:
    """LLM 出向边界一站式清洗：孤代理 → 脱敏 → 中部截断。

    所有"文本即将进入 LLM 上下文"的位置（记忆召回注入、压缩摘要输入、
    跨频道叙事等）统一走此入口，避免各处自行拼装导致漏环节。

    Args:
        text: 待注入文本
        max_chars: 注入上下文的最大字符数（默认 6000，可按场景调小）
    """
    if not text:
        return text
    result = clean_surrogates(text)
    if is_sanitize_enabled():
        result = sanitize_text(result)
    return truncate_middle(result, max_chars)


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
