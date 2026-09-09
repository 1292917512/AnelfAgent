"""密码泄露体检：HIBP k-匿名检查 + 本地重复/弱密码分析。

HIBP（HaveIBeenPwned）k-匿名模型：只发送密码 SHA-1 的前 5 个十六进制字符，
服务器返回该前缀下全部已知泄露哈希后缀，本地比对——明文密码永不出本机。
联网开关：``vault_breach_check_enabled``。
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any, Dict, List, Tuple

import httpx

from core.config import get_config_bool
from core.log import log

from .generator import assess_strength

_HIBP_RANGE_URL = "https://api.pwnedpasswords.com/range/"
# 批量检查时每条间隔，避免触发 HIBP 速率限制
_REQUEST_INTERVAL = 0.2
_TIMEOUT = 15.0


async def check_password_pwned(password: str, *, client: httpx.AsyncClient) -> int:
    """k-匿名查询单个密码的泄露次数（0 = 未见泄露）。"""
    sha1 = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()  # noqa: S324
    prefix, suffix = sha1[:5], sha1[5:]
    resp = await client.get(f"{_HIBP_RANGE_URL}{prefix}", timeout=_TIMEOUT)
    resp.raise_for_status()
    for line in resp.text.splitlines():
        parts = line.split(":")
        if len(parts) == 2 and parts[0] == suffix:
            return int(parts[1])
    return 0


def analyze_local(entries: List[Tuple[str, str, str]]) -> Dict[str, Any]:
    """本地分析：重复密码与弱密码。

    Args:
        entries: (entry_id, title, password) 三元组列表。
    """
    by_password: Dict[str, List[Tuple[str, str]]] = {}
    weak: List[Dict[str, object]] = []
    for entry_id, title, password in entries:
        if not password:
            continue
        by_password.setdefault(password, []).append((entry_id, title))
        strength = assess_strength(password)
        if strength["level"] in ("weak", "fair"):
            weak.append({
                "id": entry_id, "title": title,
                "entropy": strength["entropy"], "issues": strength["issues"],
            })
    reused = [
        {"count": len(holders),
         "entries": [{"id": eid, "title": t} for eid, t in holders]}
        for holders in by_password.values() if len(holders) > 1
    ]
    return {"reused": reused, "weak": weak}


async def breach_report(
    entries: List[Tuple[str, str, str]],
) -> Dict[str, object]:
    """完整体检报告：HIBP 泄露（联网可选）+ 本地重复/弱密码。

    Args:
        entries: (entry_id, title, password) 三元组列表。
    """
    report: Dict[str, Any] = analyze_local(entries)
    pwned: List[Dict[str, object]] = []
    hibp_checked = False
    hibp_error = ""
    if get_config_bool("vault_breach_check_enabled", True):
        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": "AnelfAgent-Vault"},
                follow_redirects=True,
            ) as client:
                for entry_id, title, password in entries:
                    if not password:
                        continue
                    count = await check_password_pwned(password, client=client)
                    hibp_checked = True
                    if count > 0:
                        pwned.append({"id": entry_id, "title": title, "count": count})
                    await asyncio.sleep(_REQUEST_INTERVAL)
        except Exception as exc:
            hibp_error = str(exc)
            log(f"HIBP 泄露检查失败: {exc}", "WARNING")
    report.update({
        "pwned": pwned,
        "hibp_checked": hibp_checked,
        "hibp_error": hibp_error,
    })
    return report
