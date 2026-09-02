"""SillyTavern 嵌套仓库的 git 操作（更新 / 二次开发提交）。

仓库 origin 指向用户 fork（可推送），upstream 指向官方（仅拉取同步）。
"""

from __future__ import annotations

import subprocess
from typing import Any, Dict, List, Optional

from . import config as st_config


def _git(args: List[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=st_config.st_dir(),
        capture_output=True, text=True, timeout=timeout,
    )


def _ok(result: subprocess.CompletedProcess, action: str) -> str:
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[-500:]
        raise RuntimeError(f"{action}失败（退出码 {result.returncode}）: {detail}")
    return result.stdout.strip()


def status() -> Dict[str, Any]:
    """仓库状态：分支、远端、最新提交、脏文件列表。"""
    branch = _ok(_git(["rev-parse", "--abbrev-ref", "HEAD"]), "读取分支")
    commit = _ok(_git(["log", "-1", "--format=%h %ad %s", "--date=short"]), "读取提交")
    remotes = list(dict.fromkeys(
        _ok(_git(["remote"]), "读取远端").splitlines()))
    changed = [l for l in _ok(_git(["status", "--porcelain"]), "读取工作区").splitlines() if l]
    return {
        "branch": branch,
        "last_commit": commit,
        "remotes": remotes,
        "dirty_files": changed[:50],
        "dirty_count": len(changed),
    }


def remotes_detail() -> List[Dict[str, str]]:
    """远端名 → URL 映射（供面板展示/分支选择）。"""
    pairs = _ok(_git(["remote", "-v"]), "读取远端").splitlines()
    out: Dict[str, str] = {}
    for line in pairs:
        parts = line.split()
        if len(parts) >= 2 and parts[0] not in out:
            out[parts[0]] = parts[1]
    return [{"name": k, "url": v} for k, v in out.items()]


def remote_branches(remote: str) -> List[str]:
    """列出远端分支（本地缓存，不访问网络）。"""
    out = _ok(_git(["branch", "-r", "--list", f"{remote}/*"]), "读取远端分支")
    branches = []
    for line in out.splitlines():
        name = line.strip()
        if not name or "->" in name:
            continue
        branches.append(name.split("/", 1)[1] if "/" in name else name)
    return sorted(set(branches))


def remote_versions(remote: str = "origin") -> Dict[str, Any]:
    """列出远端可用版本（release/staging/main 等分支 + 当前 HEAD）。

    用于面板的"查看/切换版本"功能：优先展示常用主分支，
    HEAD 所在提交标注 current=true。
    """
    st = status()
    head = _ok(_git(["rev-parse", "--short", "HEAD"]), "读取HEAD")
    branches = remote_branches(remote)
    # 常用分支排在前面
    order = {"release": 0, "main": 1, "master": 2, "staging": 3, "develop": 4}
    branches.sort(key=lambda b: (order.get(b, 99), b))
    versions = []
    for b in branches:
        info = _ok(_git(["log", "-1", "--format=%h %ad %s", "--date=short",
                         f"{remote}/{b}"]), "读取分支提交")
        sha = info.split()[0] if info else ""
        versions.append({
            "name": b,
            "commit": info,
            "current": sha == head,
        })
    return {
        "remote": remote,
        "current_branch": st["branch"],
        "current_commit": head,
        "remotes": remotes_detail(),
        "versions": versions,
        "fetch_hint": "列表为本地缓存，切换前建议先 git fetch 获取远端最新信息",
    }


def checkout_version(remote: str, name: str) -> Dict[str, Any]:
    """切换到远端分支对应版本：fetch + checkout 新分支（或直接 checkout 已有分支）。

    有未提交修改时拒绝执行（防丢改动）；切换后建议重启酒馆。
    """
    dirty = _ok(_git(["status", "--porcelain"]), "检查工作区")
    if dirty:
        raise RuntimeError(
            "工作区有未提交修改，请先 sillytavern_commit 提交或手动处理后再切换")
    _ok(_git(["fetch", remote, name], timeout=300), "git fetch")
    # 本地已有同名分支直接切，否则从远端创建跟踪分支
    local = _ok(_git(["branch", "--list", name]), "检查本地分支")
    if local.strip():
        _ok(_git(["checkout", name]), "git checkout")
    else:
        _ok(_git(["checkout", "-b", name, "--track", f"{remote}/{name}"]), "git checkout")
    new_head = _ok(_git(["log", "-1", "--format=%h %ad %s", "--date=short"]), "读取提交")
    return {
        "ok": True,
        "branch": name,
        "commit": new_head,
        "running": service_is_running(),
        "hint": "版本已切换，建议 sillytavern_restart 让新代码生效",
    }


def service_is_running() -> bool:
    """惰性 import 避免循环依赖。"""
    from . import service
    return service.is_running()


def pull(remote: str = "origin", branch: Optional[str] = None) -> Dict[str, Any]:
    """拉取更新（默认 origin 当前分支，--ff-only 防意外合并）。"""
    args = ["pull", "--ff-only", remote]
    if branch:
        args.append(branch)
    out = _ok(_git(args, timeout=300), "git pull")
    return {"ok": True, "output": out[-1000:]}


def commit_push(message: str) -> Dict[str, Any]:
    """二次开发提交：add -A + commit + push origin HEAD。"""
    if not message or not message.strip():
        raise ValueError("提交信息不能为空")
    _ok(_git(["add", "-A"]), "git add")
    status_out = _ok(_git(["status", "--porcelain"]), "检查暂存区")
    if not status_out:
        return {"ok": True, "nothing_to_commit": True}
    _ok(_git(["commit", "-m", message.strip()]), "git commit")
    push_out = _ok(_git(["push", "origin", "HEAD"], timeout=180), "git push")
    commit = _ok(_git(["log", "-1", "--format=%h %s"]), "读取提交")
    return {"ok": True, "commit": commit, "output": push_out[-500:]}


def discard_changes() -> Dict[str, Any]:
    """丢弃工作区所有未提交修改（危险操作，二次开发回滚用）。"""
    _ok(_git(["reset", "--hard", "HEAD"]), "git reset")
    _ok(_git(["clean", "-fd"]), "git clean")
    return {"ok": True}
