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
_SEGMENT_SIZE = 65_536

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
    "\u200b", "\u200c", "\u200d", "\u2060", "\ufeff",
    "\u180e", "\u2000", "\u2001", "\u2002", "\u2003",
    "\u2004", "\u2005", "\u2006", "\u2007", "\u2008", "\u2009", "\u200a",
)

# ZWJ/ZWNJ 在 emoji 组合序列或阿拉伯/波斯文上下文中是正常字符，需豁免
_ZWJ_ZWNJ = frozenset(("\u200d", "\u200c"))


def _is_emoji_context(text: str, idx: int) -> bool:
    """判断 idx 处的 ZWJ/ZWNJ 是否处于 emoji 修饰符/扩展 pictographic 上下文中。"""
    # 检查前一个字符是否为 emoji 相关（Extended_Pictographic 或修饰符）
    if idx > 0:
        prev = text[idx - 1]
        if unicodedata.category(prev).startswith("So") or ord(prev) >= 0x1F000:
            return True
        # 肤色修饰符 U+1F3FB..U+1F3FF
        if 0x1F3FB <= ord(prev) <= 0x1F3FF:
            return True
    # 检查后一个字符
    next_idx = idx + 1
    if next_idx < len(text):
        nxt = text[next_idx]
        if unicodedata.category(nxt).startswith("So") or ord(nxt) >= 0x1F000:
            return True
        if 0x1F3FB <= ord(nxt) <= 0x1F3FF:
            return True
    return False


def _is_arabic_persian_context(text: str, idx: int) -> bool:
    """判断 idx 处的 ZWNJ 是否处于阿拉伯/波斯文书写上下文中。"""
    # 阿拉伯文字范围 U+0600-U+06FF, U+0750-U+077F, U+FB50-U+FDFF, U+FE70-U+FEFF
    arabic_ranges = (
        (0x0600, 0x06FF), (0x0750, 0x077F), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF),
    )
    for offset in (-1, 1):
        pos = idx + offset
        if 0 <= pos < len(text):
            cp = ord(text[pos])
            if any(lo <= cp <= hi for lo, hi in arabic_ranges):
                return True
    return False


def _has_suspicious_invisible(text: str) -> bool:
    """检测隐形字符，豁免 emoji 组合序列和阿拉伯/波斯文上下文中的 ZWJ/ZWNJ。"""
    for i, ch in enumerate(text):
        if ch not in _INVISIBLE_CHARS:
            continue
        if ch in _ZWJ_ZWNJ:
            if _is_emoji_context(text, i) or _is_arabic_persian_context(text, i):
                continue
        return True
    return False

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

    超长内容（>MAX_SCAN_CHARS）分段扫描，确保尾部内容不被遗漏。

    Args:
        content: 待扫描内容
        scope: 扫描级别（all / context / strict），级别越宽命中的模式越多
    """
    if not content:
        return []

    hits: List[str] = []
    seen: set[str] = set()

    # 分段扫描：每段 _SEGMENT_SIZE 字符，段间重叠 256 字符防跨段漏检
    overlap = 256
    segments: List[str] = []
    if len(content) <= _SEGMENT_SIZE:
        segments.append(content)
    else:
        start = 0
        n = len(content)
        while start < n:
            end = min(start + _SEGMENT_SIZE, n)
            segments.append(content[start:end])
            # 末段已覆盖尾部即结束：end 封顶后 start 会停滞在 n-overlap 造成死循环
            if end >= n:
                break
            start = end - overlap

    target_level = _SCOPE_ORDER.get(scope, 1)

    for segment in segments:
        # 隐形字符检测（豁免 emoji/阿拉伯文上下文）
        if "invisible_unicode_chars" not in seen and _has_suspicious_invisible(segment):
            seen.add("invisible_unicode_chars")
            hits.append("invisible_unicode_chars")

        normalized = _normalize(segment)
        for pattern, pattern_id, pattern_scope in _COMPILED:
            if pattern_id in seen:
                continue
            if _SCOPE_ORDER.get(pattern_scope, 1) > target_level:
                continue
            if pattern.search(normalized):
                seen.add(pattern_id)
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
