"""密码生成器：secrets 加密学安全随机 + 熵估算强度评估。

两种模式：
- random：字符集组合（大小写/数字/符号，可排除歧义字符 Il1O0）
- memorable：辅音+元音音节交替的可读密码 + 数字后缀（无需词表）
"""

from __future__ import annotations

import math
import secrets
import string
from typing import Dict

_AMBIGUOUS = "Il1O0"
_CONSONANTS = "bcdfghjklmnpqrstvwxz"
_VOWELS = "aeiouy"


def generate_password(
    length: int = 20,
    *,
    upper: bool = True,
    lower: bool = True,
    digits: bool = True,
    symbols: bool = True,
    exclude_ambiguous: bool = False,
) -> str:
    """生成随机强密码，保证每种启用字符集至少出现一次。"""
    pools = []
    if lower:
        pools.append(string.ascii_lowercase)
    if upper:
        pools.append(string.ascii_uppercase)
    if digits:
        pools.append(string.digits)
    if symbols:
        pools.append("!@#$%^&*-_=+?")
    if not pools:
        raise ValueError("至少启用一种字符集")
    if exclude_ambiguous:
        pools = ["".join(c for c in p if c not in _AMBIGUOUS) or p for p in pools]
    length = max(4, min(128, int(length)))
    alphabet = "".join(pools)
    while True:
        chars = [secrets.choice(p) for p in pools]
        chars += [secrets.choice(alphabet) for _ in range(length - len(chars))]
        secrets.SystemRandom().shuffle(chars)
        password = "".join(chars)
        if all(any(c in p for c in password) for p in pools):
            return password


def generate_memorable(word_count: int = 4, *, digits_suffix: bool = True) -> str:
    """生成可读音节密码：CVCV 音节段 + 连接符 + 可选数字后缀。"""
    word_count = max(2, min(8, int(word_count)))
    rng = secrets.SystemRandom()
    parts = []
    for _ in range(word_count):
        syllables = "".join(
            rng.choice(_CONSONANTS) + rng.choice(_VOWELS)
            for _ in range(rng.choice((1, 2)))
        )
        parts.append(syllables.capitalize() if rng.random() < 0.5 else syllables)
    password = "-".join(parts)
    if digits_suffix:
        password += str(rng.randrange(10, 99))
    return password


def password_entropy(password: str) -> float:
    """按实际字符集大小估算信息熵（bits）。"""
    if not password:
        return 0.0
    space = 0
    if any(c in string.ascii_lowercase for c in password):
        space += 26
    if any(c in string.ascii_uppercase for c in password):
        space += 26
    if any(c in string.digits for c in password):
        space += 10
    if any(not c.isalnum() for c in password):
        space += 32
    return len(password) * math.log2(max(space, 1))


def assess_strength(password: str) -> Dict[str, object]:
    """强度评估：熵 + 分级 + 常见问题。"""
    entropy = password_entropy(password)
    issues = []
    if len(password) < 12:
        issues.append("长度不足 12 位")
    if password.lower() in {
        "123456", "password", "123456789", "12345678", "qwerty", "abc123",
        "111111", "123123", "admin", "letmein", "iloveyou", "000000",
    }:
        issues.append("常见弱密码")
    if len(set(password)) <= max(2, len(password) // 4):
        issues.append("字符重复度过高")
    if entropy >= 90:
        level = "excellent"
    elif entropy >= 60:
        level = "strong"
    elif entropy >= 40:
        level = "fair"
    else:
        level = "weak"
    return {"entropy": round(entropy, 1), "level": level, "issues": issues}
