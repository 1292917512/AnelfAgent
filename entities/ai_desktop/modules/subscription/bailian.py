"""百炼 Token Plan 只读额度查询，控制台会话由官方 CLI 管理。"""

from __future__ import annotations

import json
import math
import os
import shutil
from pathlib import Path
from typing import Any

from core.command import run_command
from core.file_utils import atomic_write_text
from core.path import config_dir


class ConsoleLoginRequired(ValueError):
    """额度查询缺少控制台会话。"""


def console_config_dir() -> Path:
    """额度组件专用的 CLI 配置目录，与模型及用户 CLI 配置隔离。"""
    return Path(config_dir()).resolve() / "ai_desktop" / "bailian"


def save_console_session(payload: dict[str, Any]) -> None:
    """仅持久化控制台授权字段，不保存或创建模型 API Key。"""
    token = payload.get("access_token")
    if not isinstance(token, str) or not token.strip() or len(token) > 16384:
        raise ValueError("控制台未返回有效授权 token")
    data: dict[str, Any] = {"access_token": token.strip(), "telemetry": False}
    for key in ("workspace_id", "console_region", "console_site"):
        value = payload.get(key)
        if isinstance(value, str) and len(value) <= 256:
            data[key] = value
    if data.get("console_site", "domestic") != "domestic":
        raise ValueError("当前额度登录仅支持百炼中国站")
    switch = payload.get("console_switch_agent")
    if isinstance(switch, (str, int)) and not isinstance(switch, bool) and str(switch).isdigit():
        data["console_switch_agent"] = int(switch)
    directory = console_config_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = directory / "config.json"
    atomic_write_text(target, json.dumps(data, ensure_ascii=False))
    target.chmod(0o600)


def auth_revision() -> str:
    """用凭据文件变更恢复登录失效后的采集，不读取或暴露 token。"""
    directory = console_config_dir()
    if not (directory / "config.json").exists():
        directory = Path(os.environ.get("BAILIAN_CONFIG_DIR", str(Path.home() / ".bailian")))
    revisions = [str(directory)]
    for name in ("config.json", "credentials.json"):
        try:
            stat = (directory / name).stat()
            revisions.append(f"{stat.st_mtime_ns}:{stat.st_size}")
        except FileNotFoundError:
            revisions.append("missing")
    return "|".join(revisions)


def cli_credential() -> tuple[str, str]:
    """探测 CLI 可执行文件，不读取或复制凭据。"""
    path = shutil.which("bl") or shutil.which("bailian")
    return (path, "Bailian CLI") if path else ("", "")


def console_authorization_present() -> bool:
    """只检测可复用授权是否存在，不验证网络、不返回凭据。"""
    directory = console_config_dir()
    if not (directory / "config.json").exists():
        directory = Path(os.environ.get("BAILIAN_CONFIG_DIR", str(Path.home() / ".bailian")))
    try:
        data = json.loads((directory / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and isinstance(data.get("access_token"), str) and bool(data["access_token"].strip())


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
        timeout_sec=20, shell=False, expected_returncodes=(3,),
        env_vars={"BAILIAN_CONFIG_DIR": str(console_config_dir())}
        if (console_config_dir() / "config.json").exists() else None,
    )
    if not result.ok:
        if result.returncode == 3:
            raise ConsoleLoginRequired("控制台未登录或授权已失效，请在订阅额度面板登录")
        raise ValueError("百炼额度查询失败，请检查 CLI 版本、控制台登录态与网络")
    try:
        data = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise ValueError("百炼 CLI 未返回有效 JSON，请检查 CLI 版本") from exc
    if not isinstance(data, dict) or data.get("error"):
        raise ValueError("百炼 CLI 额度响应无效")
    return parse_plan(data)
