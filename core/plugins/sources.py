"""插件负载获取 — git 克隆 / 本地拷贝，统一暂存后由调用方原子替换。

git 操作剥离 GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE 等环境变量，
防止调用方 shell 的仓库环境把克隆/拉取重定向到非预期仓库。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from core.log import log
from core.plugins.manifest import PluginError

_GIT_ENV_BLOCKLIST = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)

_DEFAULT_GIT_TIMEOUT = 120.0


def _git_env() -> dict:
    """构造 git 子进程环境：剥离仓库环境变量，按需注入代理。

    代理取 plugin_proxy 配置（配置中心可改，改后下次 git 操作即生效）；
    未配置时继承进程环境（系统 http_proxy 等变量自然透传）。
    """
    env = dict(os.environ)
    for key in _GIT_ENV_BLOCKLIST:
        env.pop(key, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    proxy = _configured_proxy()
    if proxy:
        for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
                    "all_proxy", "ALL_PROXY"):
            env[key] = proxy
    return env


def _configured_proxy() -> str:
    """读取插件代理配置（每次 git 操作实时读取，配置修改即时生效）。"""
    try:
        from core.config import get_config
        return str(get_config("plugin_proxy", "") or "").strip()
    except Exception:
        return ""


def _run_git(args: list, cwd: Optional[Path] = None, timeout: float = _DEFAULT_GIT_TIMEOUT) -> str:
    """执行 git 命令，失败抛 PluginError；成功返回 stdout 摘要。"""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            env=_git_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as e:
        raise PluginError("未找到 git 可执行文件，无法从 git 源安装插件") from e
    except subprocess.TimeoutExpired as e:
        raise PluginError(f"git 操作超时（{timeout}s）: git {' '.join(args[:2])}") from e
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise PluginError(f"git {' '.join(args[:2])} 失败: {detail}")
    return (result.stdout or "").strip()


def new_staging_dir() -> Path:
    """在插件负载根下创建暂存目录（与正式目录同盘，os.replace 才原子）。"""
    from core.plugins.store import plugins_dir

    return Path(tempfile.mkdtemp(prefix=".staging_", dir=str(plugins_dir())))


def cleanup_staging(staging: Path) -> None:
    """清理暂存目录（忽略不存在/占用等失败）。"""
    try:
        shutil.rmtree(staging, ignore_errors=True)
    except OSError as e:
        log(f"插件暂存目录清理失败: {staging} - {e}", "DEBUG")


def fetch_local(path: str, staging: Path) -> Tuple[Path, str]:
    """从本地目录拷贝插件负载到暂存目录，返回 (负载目录, 内容指纹)。

    暂存目录名保留来源目录名：清单缺省 name 字段时按目录名回退。
    """
    source = Path(os.path.expanduser(path)).resolve()
    if not source.is_dir():
        raise PluginError(f"本地插件路径不存在: {path}")
    target = staging / source.name
    ignore = shutil.ignore_patterns(".git", "__pycache__", "node_modules", ".venv")
    try:
        shutil.copytree(source, target, ignore=ignore)
    except OSError as e:
        raise PluginError(f"拷贝本地插件失败: {source} - {e}") from e
    return target, _dir_fingerprint(target)


def _dir_fingerprint(root: Path) -> str:
    """目录内容指纹（相对路径 + 文件内容的 sha256），用于本地源升级变更检测。"""
    import hashlib

    digest = hashlib.sha256()
    for file in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(str(file.relative_to(root)).encode())
        try:
            digest.update(file.read_bytes())
        except OSError:
            continue
    return digest.hexdigest()


def _repo_dir_name(url: str) -> str:
    """从仓库地址推导目录名（清单缺省 name 时按仓库名回退）。"""
    text = url.rstrip("/")
    if ":" in text and not text.startswith(("http://", "https://", "file://", "ssh://")):
        text = text.split(":")[-1]  # git@host:owner/repo 形式
    name = text.split("/")[-1] or "repo"
    return name[:-4] if name.endswith(".git") else name


def _is_commit_sha(ref: str) -> bool:
    """判断 ref 是否为完整 commit sha（40 位十六进制）。"""
    return len(ref) == 40 and all(c in "0123456789abcdef" for c in ref.lower())


def fetch_git(url: str, ref: str, staging: Path, *, subdir: str = "",
              timeout: float = _DEFAULT_GIT_TIMEOUT) -> Tuple[Path, str]:
    """浅克隆 git 仓库到暂存目录，返回 (负载目录, commit sha)。

    ref 为分支/tag 时经 --branch 浅克隆；为完整 commit sha 时先浅克隆
    默认分支再按 sha 定点拉取（--branch 不接受 sha）。
    """
    clone_dir = staging / _repo_dir_name(url)
    if ref and _is_commit_sha(ref):
        _run_git(["clone", "--depth", "1", url, str(clone_dir)], timeout=timeout)
        _run_git(["fetch", "--depth", "1", "origin", ref], cwd=clone_dir, timeout=timeout)
        _run_git(["checkout", "--detach", "FETCH_HEAD"], cwd=clone_dir, timeout=timeout)
    else:
        args = ["clone", "--depth", "1"]
        if ref:
            args += ["--branch", ref]
        args += [url, str(clone_dir)]
        _run_git(args, timeout=timeout)
    sha = _run_git(["rev-parse", "HEAD"], cwd=clone_dir)
    payload = clone_dir / subdir if subdir else clone_dir
    if not payload.is_dir():
        raise PluginError(f"仓库中不存在插件子目录: {subdir}")
    return payload, sha


def git_pull(repo_dir: Path, *, timeout: float = _DEFAULT_GIT_TIMEOUT) -> str:
    """在已有克隆上拉取最新代码，返回最新 commit sha。"""
    _run_git(["fetch", "--depth", "1", "origin"], cwd=repo_dir, timeout=timeout)
    _run_git(["reset", "--hard", "FETCH_HEAD"], cwd=repo_dir, timeout=timeout)
    return _run_git(["rev-parse", "HEAD"], cwd=repo_dir)


def replace_payload(payload: Path, target: Path) -> None:
    """将暂存负载原子替换到正式目录（先移走旧目录再 rename，失败回滚）。"""
    backup = target.with_name(f"{target.name}.old")
    moved_old = False
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
            os.replace(target, backup)
            moved_old = True
        os.replace(payload, target)
    except OSError as e:
        if moved_old and not target.exists():
            try:
                os.replace(backup, target)
            except OSError:
                log(f"插件目录回滚失败: {backup}", "ERROR")
        raise PluginError(f"替换插件目录失败: {target} - {e}") from e
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
