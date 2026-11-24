"""通过官方 DSH CLI 管理每个 Cordis 插件独占的 Web profile。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from core.plugins.manifest import PluginError
from core.plugins.store import dsh_profile_home
from core.sanitizer import sanitize_text

RUNTIME_PACKAGE = "deepseek-harness-runtime-bin==0.2.2rc2"


def _run(home: Path, *args: str) -> None:
    cli = shutil.which("dsh")
    uv = shutil.which("uv")
    if cli:
        command = [cli]
    elif uv:
        command = [uv, "tool", "run", "--from", RUNTIME_PACKAGE, "dsh"]
    else:
        raise PluginError("Cordis 插件需要官方 dsh 命令，或 uv 按需获取 DSH 运行时")
    try:
        result = subprocess.run(
            [*command, *args], cwd=home, env={**os.environ, "DSH_HOME": str(home)},
            stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8", errors="replace",
            timeout=180, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PluginError(f"DSH 命令执行失败: {exc}") from exc
    if result.returncode:
        detail = sanitize_text((result.stderr or result.stdout).strip()[-1500:])
        raise PluginError(f"DSH 命令执行失败 ({result.returncode}): {detail}")


def install_bundle(name: str, source: Path, recorded_home: str = "") -> str:
    """把插件安装进专属 Web profile 并验证最终配置能由官方宿主组合。"""
    home = dsh_profile_home(name, recorded_home)
    home.mkdir(parents=True, exist_ok=True)
    if not (home / "profiles" / "web" / "package.json").is_file():
        _run(home, "--profile", "web", "--dump-config")
    _run(home, "plugin", "--profile", "web", "add", f"file:{source.resolve().as_posix()}")
    _run(home, "--profile", "web", "--dump-config")
    return str(home)


def disable_bundle(name: str, package_name: str, recorded_home: str = "") -> None:
    """从专属 profile 移除 bundle，保留用户在该宿主中的设置。"""
    home = dsh_profile_home(name, recorded_home)
    manifest_path = home / "profiles" / "web" / "package.json"
    if not manifest_path.is_file():
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if package_name not in manifest.get("dependencies", {}) and package_name not in manifest["dsh"]["profile"]["bundles"]:
            return
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise PluginError(f"DSH profile 清单不可读: {manifest_path}") from exc
    _run(home, "plugin", "--profile", "web", "remove", package_name)
