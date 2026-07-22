"""运维工具 — 记忆备份、项目更新、应用重启。

提供 AI 自主管理部署流程的能力：
- 记忆文件同步到私有 GitHub 仓库
- 从远程拉取项目最新代码
- 更新后自动重启应用（合并为一步操作）
- 遇到 Git 冲突时提示联系主人
"""

from __future__ import annotations

import json
import os
import platform
from typing import List

from entities._sdk import tool, entity

entity("devops", "运维管理 - 记忆备份同步、项目代码更新、应用重启")


def _project_root() -> str:
    """获取项目根目录。"""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _run(command: List[str], cwd: str | None = None, timeout: int = 60) -> dict:
    """执行命令并返回结构化结果，支持指定工作目录。

    复用 core/command.py 的 run_command（参数列表形式，shell=False，
    POSIX 下独立进程组、超时整组终止）。
    """
    from core.command import run_command
    result = run_command(list(command), timeout_sec=timeout, cwd=cwd or _project_root())
    return {
        "ok": result.ok,
        "stdout": (result.stdout or "").strip()[:3000],
        "stderr": (result.stderr or "").strip()[:1000],
    }


def _pick_script(name: str) -> str:
    """根据操作系统选择 .bat 或 .sh 脚本。"""
    root = _project_root()
    if platform.system() == "Windows":
        return os.path.join(root, "scripts", f"{name}.bat")
    return os.path.join(root, "scripts", f"{name}.sh")


def _has_conflict(output: str) -> bool:
    """检测 git pull 输出是否包含冲突标志。"""
    conflict_markers = ("CONFLICT", "Automatic merge failed", "fix conflicts")
    return any(m in output for m in conflict_markers)


# ── 记忆备份 ──────────────────────────────────────────────────────────

@tool(name="backup_memories", group="devops")
def backup_memories(message: str = "") -> str:
    """将记忆文件同步备份到私有 GitHub 仓库。

    自动执行 secrets-backup 脚本：复制配置和记忆文件到 .secrets/，提交并推送。

    Args:
        message: 可选的提交信息，留空则使用默认格式（backup 日期时间）
    """
    script = _pick_script("secrets-backup")
    if not os.path.exists(script):
        return json.dumps({"error": f"备份脚本不存在: {script}"}, ensure_ascii=False)

    cmd = [script]
    if message:
        cmd.append(message)

    result = _run(cmd, cwd=_project_root(), timeout=120)
    return json.dumps({
        "action": "backup_memories",
        **result,
    }, ensure_ascii=False)


# ── 项目更新 ──────────────────────────────────────────────────────────

@tool(name="update_project", group="devops")
def update_project() -> str:
    """从远程仓库拉取项目最新代码（git pull）。

    如果遇到合并冲突，不会自动解决，请主动联系主人处理。
    """
    root = _project_root()

    status = _run(["git", "status", "--porcelain"], cwd=root, timeout=15)
    if status["ok"] and status["stdout"].strip():
        return json.dumps({
            "action": "update_project",
            "ok": False,
            "message": "工作区有未提交的修改，请先提交或暂存后再更新",
            "dirty_files": status["stdout"][:500],
        }, ensure_ascii=False)

    result = _run(["git", "pull", "--ff-only"], cwd=root, timeout=120)

    if not result["ok"] or _has_conflict(result["stdout"] + result["stderr"]):
        return json.dumps({
            "action": "update_project",
            "ok": False,
            "conflict": True,
            "message": "代码更新遇到冲突，请主动联系主人解决，不要尝试自动处理！",
            "detail": result["stdout"][:500],
        }, ensure_ascii=False)

    return json.dumps({
        "action": "update_project",
        **result,
    }, ensure_ascii=False)


# ── 更新并重启 ────────────────────────────────────────────────────────

@tool(name="update_and_restart", group="devops")
def update_and_restart() -> str:
    """一步完成：拉取最新代码 + 重启应用。

    流程：git pull → 检查冲突 → 无冲突则重启。
    遇到冲突时不会重启，请主动联系主人解决。
    """
    root = _project_root()

    status = _run(["git", "status", "--porcelain"], cwd=root, timeout=15)
    if status["ok"] and status["stdout"].strip():
        return json.dumps({
            "action": "update_and_restart",
            "ok": False,
            "message": "工作区有未提交的修改，请先提交或暂存后再更新",
            "dirty_files": status["stdout"][:500],
        }, ensure_ascii=False)

    pull = _run(["git", "pull", "--ff-only"], cwd=root, timeout=120)

    if not pull["ok"] or _has_conflict(pull["stdout"] + pull["stderr"]):
        return json.dumps({
            "action": "update_and_restart",
            "ok": False,
            "conflict": True,
            "message": "代码更新遇到冲突，不会重启！请主动联系主人解决！",
            "detail": pull["stdout"][:500],
        }, ensure_ascii=False)

    if not _schedule_restart():
        return json.dumps({
            "action": "update_and_restart",
            "ok": False,
            "error": "代码已更新，但无法调度重启任务：未找到运行中的事件循环，请手动重启",
        }, ensure_ascii=False)
    return json.dumps({
        "action": "update_and_restart",
        "ok": True,
        "pull_result": pull["stdout"][:300],
        "message": "代码已更新，应用将在 2 秒后重启...",
    }, ensure_ascii=False)


# ── 重启应用 ──────────────────────────────────────────────────────────

@tool(name="restart_app", group="devops")
def restart_app() -> str:
    """重启应用进程。

    通过 os.execv 原地替换当前进程实现热重启，保持相同的启动参数。
    """
    if not _schedule_restart():
        return json.dumps({
            "action": "restart_app",
            "ok": False,
            "error": "无法调度重启任务：未找到运行中的事件循环，请手动重启",
        }, ensure_ascii=False)
    return json.dumps({
        "action": "restart_app",
        "ok": True,
        "message": "应用将在 2 秒后重启...",
    }, ensure_ascii=False)


def _build_frontend() -> None:
    """尝试构建前端，失败不阻塞重启。"""
    from core.log import log
    root = _project_root()
    frontend_dir = os.path.join(root, "web", "frontend")
    pkg_json = os.path.join(frontend_dir, "package.json")
    if not os.path.exists(pkg_json):
        return

    log("🔨 构建前端...", tag="运维")
    try:
        result = _run(["pnpm", "run", "build"], cwd=frontend_dir, timeout=120)
        if result["ok"]:
            log("✅ 前端构建完成", tag="运维")
        else:
            log(f"⚠️ 前端构建失败（不阻塞重启）: {result['stderr'][:200]}", "WARNING", tag="运维")
    except Exception as e:
        log(f"⚠️ 前端构建异常（不阻塞重启）: {e}", "WARNING", tag="运维")


_RESTART_EXIT_CODE = 42


def _schedule_restart() -> bool:
    """延迟 2 秒后执行：前端构建 → 生命周期清理 → 以退出码 42 退出触发重启。

    启动脚本（start.bat / start.sh）检测到退出码 42 会自动重新启动应用。
    同步工具在 to_thread 工作线程中执行时，经后台注册表绑定的主循环桥回调度。

    Returns:
        是否成功调度重启任务
    """
    import asyncio
    from core.log import log

    async def _do_restart() -> None:
        log("🔄 应用重启流程启动...", tag="运维")
        await asyncio.sleep(2)

        _build_frontend()

        try:
            from core.lifecycle import Lifecycle
            await Lifecycle.shutdown_all()
        except Exception as e:
            log(f"⚠️ 重启前生命周期清理异常: {e}", "WARNING", tag="运维")

        log(f"🔄 以退出码 {_RESTART_EXIT_CODE} 退出，等待启动脚本重启...", tag="运维")
        os._exit(_RESTART_EXIT_CODE)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_do_restart())
        return True
    except RuntimeError:
        pass

    from entities._sdk import get_background_registry
    registry = get_background_registry()
    loop = getattr(registry, "_loop", None) if registry else None
    if loop and loop.is_running():
        loop.call_soon_threadsafe(lambda: asyncio.ensure_future(_do_restart()))
        return True
    return False
