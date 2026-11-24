"""插件负载获取 — git 克隆 / 本地拷贝，统一暂存后由调用方原子替换。

git 操作剥离 GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE 等环境变量，
防止调用方 shell 的仓库环境把克隆/拉取重定向到非预期仓库。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlsplit

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


def _git_env(proxy: str | None = None) -> dict[str, str]:
    """隔离仓库环境与代理环境，代理只作用于当前 git 子进程。"""
    env = dict(os.environ)
    for key in (*_GIT_ENV_BLOCKLIST, "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
                "all_proxy", "ALL_PROXY", "no_proxy", "NO_PROXY"):
        env.pop(key, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    count = int(env.get("GIT_CONFIG_COUNT", "0"))
    env[f"GIT_CONFIG_KEY_{count}"] = "http.proxy"
    env[f"GIT_CONFIG_VALUE_{count}"] = proxy or ""
    env["GIT_CONFIG_COUNT"] = str(count + 1)
    return env


def _run_git(args: list[str], cwd: Optional[Path] = None,
             timeout: float = _DEFAULT_GIT_TIMEOUT, *, url: str = "") -> str:
    """执行 git 命令；远程只读操作连接失败时按全局规则尝试代理。"""
    from core.network import resolve_network_route
    from core.sanitizer import sanitize_text

    proxies = resolve_network_route(url).proxies if url.startswith(("https://", "http://")) else (None,)
    for index, proxy in enumerate(proxies):
        try:
            result = subprocess.run(
                ["git", *args], cwd=str(cwd) if cwd else None, env=_git_env(proxy),
                capture_output=True, encoding="utf-8", errors="replace", timeout=timeout,
            )
        except FileNotFoundError as exc:
            raise PluginError("未找到 git 可执行文件，无法从 git 源安装插件") from exc
        except subprocess.TimeoutExpired as exc:
            raise PluginError(f"git 操作超时（{timeout}s）: git {args[0]}") from exc
        if result.returncode == 0:
            return (result.stdout or "").strip()
        detail = (result.stderr or result.stdout or "").strip()
        connect_failure = any(message in detail.lower() for message in (
            "could not resolve host", "couldn't resolve host", "failed to connect", "couldn't connect to server",
        ))
        if index + 1 < len(proxies) and connect_failure:
            continue
        raise PluginError(f"git {args[0]} 失败: {sanitize_text(detail)}")
    raise PluginError("git 网络路由为空")


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


def fetch_local(path: str, staging: Path, *, subdir: str = "") -> Tuple[Path, str]:
    """从本地目录拷贝插件负载到暂存目录，返回 (负载目录, 内容指纹)。

    暂存目录名保留来源目录名：清单缺省 name 字段时按目录名回退。
    """
    source = Path(os.path.expanduser(path)).resolve()
    if subdir:
        selected = (source / subdir).resolve()
        if Path(subdir).anchor or not selected.is_relative_to(source):
            raise PluginError(f"插件子目录越出本地来源: {subdir}")
        source = selected
    if not source.is_dir():
        raise PluginError(f"本地插件路径不存在: {path}")
    if staging.resolve().is_relative_to(source):
        raise PluginError("本地来源包含插件暂存目录，请指定插件包的子目录")
    target = staging / source.name
    ignore = shutil.ignore_patterns(".git", "__pycache__", "node_modules", ".venv")
    try:
        shutil.copytree(source, target, ignore=ignore)
        return target, _dir_fingerprint(target)
    except OSError as e:
        raise PluginError(f"拷贝本地插件失败: {source} - {e}") from e


def _dir_fingerprint(root: Path) -> str:
    """目录内容指纹（相对路径 + 文件内容的 sha256），用于本地源升级变更检测。"""
    import hashlib

    digest = hashlib.sha256()
    for file in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(str(file.relative_to(root)).encode())
        with file.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()


def looks_like_git_url(source: str) -> bool:
    """判断来源字符串是否为 git 仓库地址。"""
    text = source.strip()
    return (
        text.startswith(("http://", "https://", "git@", "ssh://", "file://"))
        or text.endswith(".git")
    )


def _repo_dir_name(url: str) -> str:
    """从仓库地址推导目录名（清单缺省 name 时按仓库名回退）。"""
    text = url.replace("\\", "/").rstrip("/")
    if "://" in text:
        text = urlsplit(text).path.rstrip("/")
    if ":" in text and not text.startswith(("http://", "https://", "file://", "ssh://")):
        text = text.split(":")[-1]  # git@host:owner/repo 形式
    name = text.split("/")[-1] or "repo"
    name = name.removesuffix(".git")
    if not name or name in {".", ".."} or ":" in name:
        raise PluginError(f"仓库目录名非法: {name}")
    return name


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
    staging_root = staging.resolve()
    if not clone_dir.resolve().is_relative_to(staging_root):
        raise PluginError("仓库克隆路径越出暂存目录")
    payload = clone_dir / subdir if subdir else clone_dir
    if not payload.resolve().is_relative_to(clone_dir.resolve()):
        raise PluginError(f"插件子目录越出仓库: {subdir}")
    if ref and _is_commit_sha(ref):
        _run_git(["clone", "--depth", "1", "--", url, str(clone_dir)], timeout=timeout, url=url)
        _run_git(["fetch", "--depth", "1", "--", "origin", ref], cwd=clone_dir, timeout=timeout, url=url)
        _run_git(["checkout", "--detach", "FETCH_HEAD"], cwd=clone_dir, timeout=timeout)
    else:
        args = ["clone", "--depth", "1"]
        if ref:
            args += ["--branch", ref]
        args += ["--", url, str(clone_dir)]
        _run_git(args, timeout=timeout, url=url)
    sha = _run_git(["rev-parse", "HEAD"], cwd=clone_dir)
    if not payload.resolve().is_relative_to(clone_dir.resolve()):
        raise PluginError(f"插件子目录越出仓库: {subdir}")
    if not payload.is_dir():
        raise PluginError(f"仓库中不存在插件子目录: {subdir}")
    return payload, sha


@contextmanager
def updating_git_checkout(repo_dir: Path, ref: str) -> Iterator[None]:
    """按订阅的 ref 刷新受管检出，目录校验失败时恢复上次可用提交。"""
    previous = _run_git(["rev-parse", "HEAD"], cwd=repo_dir)
    url = _run_git(["remote", "get-url", "origin"], cwd=repo_dir)
    _run_git(["fetch", "--depth", "1", "--", "origin", ref or "HEAD"], cwd=repo_dir, url=url)
    _run_git(["reset", "--hard", "FETCH_HEAD"], cwd=repo_dir)
    try:
        yield
    except BaseException:
        _run_git(["reset", "--hard", previous], cwd=repo_dir)
        raise


@contextmanager
def replacing_payload(payload: Path | None, target: Path) -> Iterator[None]:
    """发布或撤回目录；调用方成功提交后回收原目录，失败时还原。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    rollback = Path(tempfile.mkdtemp(prefix=".rollback_", dir=target.parent))
    previous = rollback / "previous"
    published = False
    try:
        if target.exists():
            os.replace(target, previous)
        if payload is not None:
            os.replace(payload, target)
            published = True
        yield
    except BaseException:
        if published and payload is not None:
            os.replace(target, payload)
        if previous.exists():
            os.replace(previous, target)
        cleanup_staging(rollback)
        raise
    else:
        cleanup_staging(rollback)
