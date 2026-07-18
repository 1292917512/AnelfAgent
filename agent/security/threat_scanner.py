"""威胁模式扫描（参考 hermes-agent threat_patterns，补充中文注入模式）。

扫描上下文文件、记忆写入、工具结果中的已知 prompt 注入与攻击模式：
- 经典注入（忽略以上指令 / ignore previous instructions）
- 角色劫持（你现在是 / you are now）
- 凭证外泄（curl/cat + KEY/TOKEN/SECRET）
- 持久化（authorized_keys / 修改配置文件）
- 隐形字符与全角同形绕过（NFKC 归一化）

scope 三级（从严到宽）：all ⊂ context ⊂ strict
- all:     所有场景通用
- context: 上下文文件 / 工具结果 / 记忆召回
- strict:  记忆写入 / 技能内容（最严格）
"""
from __future__ import annotations

import re
import unicodedata
from typing import List, Optional, Tuple

from core.log import log

MAX_SCAN_CHARS = 65_536

# 有界填充：防多词绕过且避免正则回溯爆炸
_FILLER = r"(?:\w+\s+){0,8}"

# scope 层级：all ⊂ context ⊂ strict
_SCOPE_ORDER = {"all": 0, "context": 1, "strict": 2}

# (正则, pattern_id, scope)
_PATTERNS: List[Tuple[str, str, str]] = [
    # ---- 经典注入 ----
    (r"ignore\s+" + _FILLER + r"(previous|above|prior|all)\s+" + _FILLER + r"(instructions?|prompts?|rules?)",
     "ignore_previous_instructions", "all"),
    (r"忽略(以上|之前|前面|所有|全部)(的)?[^，。,.\s]{0,4}?(指令|指示|提示|规则|要求)",
     "ignore_previous_instructions_zh", "all"),
    (r"disregard\s+" + _FILLER + r"(instructions?|guidelines?|rules?)",
     "disregard_instructions", "all"),
    (r"forget\s+" + _FILLER + r"(everything|instructions?|your\s+training)",
     "forget_instructions", "all"),
    (r"忘记(你|你的)?(所有|全部)?(指令|规则|设定|训练)",
     "forget_instructions_zh", "all"),
    (r"<!--\s*(ignore|system|instruction)", "html_comment_injection", "context"),
    (r"<div\s+[^>]*display\s*:\s*none", "hidden_div_injection", "context"),

    # ---- 角色劫持 ----
    (r"you\s+are\s+now\s+" + _FILLER + r"(a|an|the)?\s*\w+", "role_hijack", "context"),
    (r"你现在是(一个|一名|新的)", "role_hijack_zh", "context"),
    (r"act\s+as\s+(if\s+you\s+(are|were)\s+)?" + _FILLER + r"(dan|jailbreak|evil|unrestricted)",
     "act_as_jailbreak", "context"),
    (r"(从现在开始|从现在起)(你)?(将|必须|应该)(扮演|成为|充当)",
     "role_hijack_from_now_zh", "context"),
    (r"new\s+persona|override\s+" + _FILLER + r"persona", "persona_override", "context"),

    # ---- 系统提示窃取 ----
    (r"(reveal|show|print|repeat|output)\s+" + _FILLER + r"(system\s+prompt|instructions?|initial\s+prompt)",
     "system_prompt_exfil", "context"),
    (r"(显示|输出|重复|打印)(你的)?(系统提示|初始指令|系统设定)",
     "system_prompt_exfil_zh", "context"),

    # ---- 凭证外泄 ----
    (r"(curl|wget|cat|type)\s+[^\n]*(\.env|id_rsa|credentials|secrets?)",
     "credential_file_access", "strict"),
    (r"(api[_-]?key|token|secret|password)\s*[=:]\s*[\"']?[A-Za-z0-9_./+-]{16,}",
     "hardcoded_secret", "strict"),
    (r"(curl|wget)\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD)",
     "credential_exfil_command", "strict"),
    (r"(发送|上传|传输).{0,20}(密钥|令牌|密码|凭证).{0,20}(到|至)", "credential_exfil_zh", "strict"),

    # ---- 持久化 / 配置篡改 ----
    (r"authorized_keys", "ssh_persistence", "strict"),
    (r"(crontab|systemctl\s+enable|rc\.local)", "persistence_mechanism", "strict"),
    (r"(修改|篡改|覆盖).{0,16}(配置文件|系统提示|人设)", "config_tamper_zh", "strict"),

    # ---- 代理劫持（针对 Agent 的 promptware）----
    (r"register\s+" + _FILLER + r"(as\s+)?(a\s+)?node", "agent_node_register", "strict"),
    (r"(heartbeat|beacon)\s+" + _FILLER + r"(to|endpoint|server)", "agent_beacon", "strict"),
    (r"unset\s+\w*(KEY|TOKEN|SECRET)", "env_var_unset", "strict"),
]

# 隐形 unicode 字符（零宽字符等，用于隐藏注入内容）
_INVISIBLE_CHARS = (
    "​", "‌", "‍", "⁠", "﻿",
    "᠎", " ", " ", " ", " ",
    " ", " ", " ", " ", " ", " ", " ",
)

_COMPILED: List[Tuple["re.Pattern[str]", str, str]] = [
    (re.compile(p, re.IGNORECASE | re.MULTILINE), pid, scope)
    for p, pid, scope in _PATTERNS
]


def _normalize(content: str) -> str:
    """NFKC 归一化：防全角/同形字符绕过。"""
    try:
        return unicodedata.normalize("NFKC", content)
    except Exception:
        return content


def scan_for_threats(content: str, scope: str = "context") -> List[str]:
    """扫描内容中的威胁模式，返回命中的 pattern_id 列表。

    Args:
        content: 待扫描内容（超长时只扫描前 MAX_SCAN_CHARS 字符）
        scope: 扫描级别（all / context / strict），级别越宽命中的模式越多
    """
    if not content:
        return []

    text = content[:MAX_SCAN_CHARS]
    hits: List[str] = []

    # 隐形字符检测
    if any(ch in text for ch in _INVISIBLE_CHARS):
        hits.append("invisible_unicode_chars")

    normalized = _normalize(text)
    target_level = _SCOPE_ORDER.get(scope, 1)
    for pattern, pattern_id, pattern_scope in _COMPILED:
        if _SCOPE_ORDER.get(pattern_scope, 1) > target_level:
            continue
        if pattern.search(normalized):
            hits.append(pattern_id)

    return hits


def first_threat_message(content: str, scope: str = "strict") -> Optional[str]:
    """返回首个命中威胁的可读描述（阻断型调用方使用），无命中返回 None。"""
    hits = scan_for_threats(content, scope=scope)
    if not hits:
        return None
    return f"检测到潜在威胁模式: {', '.join(hits[:5])}"


def scan_and_log(content: str, *, scope: str = "context", source: str = "") -> List[str]:
    """扫描并记录日志（命中时 WARNING），返回命中列表。"""
    hits = scan_for_threats(content, scope=scope)
    if hits:
        log(
            f"威胁扫描命中 ({source or '未知来源'}, scope={scope}): {', '.join(hits[:5])}",
            "WARNING", tag="安全",
        )
    return hits


def is_threat_scan_enabled() -> bool:
    """威胁扫描总开关。"""
    from core.config import get_config_bool
    return get_config_bool("security_threat_scan_enabled", True)


# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

_THREAT_SCAN_CONFIGS = {
    "安全": {
        "security_threat_scan_enabled": {
            "description": "是否启用威胁模式扫描（prompt 注入检测）",
            "default": True,
        },
        "security_scan_tool_results": {
            "description": "是否扫描工具返回结果中的注入模式（命中时向 AI 标记警告）",
            "default": True,
        },
    },
}

from core.config import register_configs_safe  # noqa: E402

register_configs_safe(_THREAT_SCAN_CONFIGS)
