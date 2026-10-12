"""百炼 Token Plan 只读额度查询，控制台会话由官方 CLI 管理。"""

from __future__ import annotations

import json
import math
import shutil
from typing import Any

from core.command import run_command


class ConsoleLoginRequired(ValueError):
    """额度查询缺少控制台会话。"""


def cli_credential() -> tuple[str, str]:
    """探测 CLI 可执行文件，不读取或复制凭据。"""
    path = shutil.which("bl") or shutil.which("bailian")
    return (path, "Bailian CLI") if path else ("", "")


def _number(value: Any) -> float | None:
    """将有限数值归一化，缺失及非法值保留未知。"""
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (ValueError, TypeError):
        return None
    return result if math.isfinite(result) else None


def parse_plan(data: dict[str, Any]) -> dict[str, Any]:
    """月度使用比例为 0~1，缺失不表示零用量或无限额度。"""
    ratio = _number(data.get("per1MonthPercentage"))
    reset = _number(data.get("per1MonthResetTime"))
    return {"plan": "Token Plan", "windows": [{
        "label": "每月",
        "remaining_percent": (1 - ratio) * 100 if ratio is not None and 0 <= ratio <= 1 else None,
        "reset_at": reset / 1000 if reset is not None and 0 < reset < 253402300799000 else None,
    }]}


def fetch_usage(path: str) -> dict[str, Any]:
    """通过官方 JSON 命令查询订阅额度，失败不暴露原始输出。"""
    result = run_command(
        [path, "usage", "token-plan", "--output", "json", "--timeout", "15", "--quiet"],
        timeout_sec=20, shell=False,
    )
    if not result.ok:
        if result.returncode == 3:
            raise ConsoleLoginRequired("请先执行 bl auth login --console 登录百炼控制台")
        raise ValueError("百炼额度查询失败，请检查 CLI 版本、控制台登录态与网络")
    try:
        data = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise ValueError("百炼 CLI 未返回有效 JSON，请检查 CLI 版本") from exc
    if not isinstance(data, dict) or data.get("error"):
        raise ValueError("百炼 CLI 额度响应无效")
    return parse_plan(data)
