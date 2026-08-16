"""运维工具 — 应用重启、前端构建、项目代码更新、崩溃信息查询。

提供 AI 自主管理部署流程的能力，核心逻辑统一在 service.py（与 Web 面板同源）：
- 重启应用（优雅关闭后由外层启动脚本按退出码 42 重新拉起）
- 查询最近一次进程崩溃信息（崩溃退出码由守护循环自动拉起并落盘）
- 构建前端并重启（构建失败则不重启）
- 从远程拉取项目最新代码，可一步完成更新并重启
- 遇到 Git 冲突时提示联系主人
"""

from __future__ import annotations

import json
from typing import Any

from entities._sdk import entity, tool

from . import service

entity("devops", "运维管理 - 应用重启、前端构建、项目代码更新")


def _result(action: str, **fields: Any) -> str:
    """组装工具返回（统一携带 action 标识）。"""
    return json.dumps({"action": action, **fields}, ensure_ascii=False)


# ── 重启应用 ──────────────────────────────────────────────────────────

@tool(name="restart_app", group="devops")
def restart_app() -> str:
    """重启应用进程。

    触发优雅关闭（完整清理 WebUI / 频道 / MCP 等资源）后退出，
    由外层启动脚本（start.sh / start.bat）自动重新拉起应用。
    """
    service.request_restart()
    return _result("restart_app", ok=True, message="应用即将优雅重启...")


@tool(name="get_crash_report", group="devops")
def get_crash_report() -> str:
    """查询最近一次进程崩溃信息（退出码/信号/系统崩溃报告摘要）。

    异常重启后可用于排查崩溃原因；启动脚本守护循环会在进程以段错误等
    致命信号退出时自动重新拉起，并把崩溃状态落盘供本工具读取。
    无崩溃记录时返回 has_crash=false。
    """
    return _result("get_crash_report", **service.get_crash_info())


@tool(name="build_and_restart", group="devops", timeout=330)
def build_and_restart() -> str:
    """重新构建前端（npm run build）并重启应用。

    构建成功后自动重启；构建失败则取消重启并返回构建日志尾部。
    """
    result = service.build_and_restart_blocking()
    if result["ok"]:
        return _result("build_and_restart", ok=True,
                       message="前端构建成功，应用即将重启...",
                       duration=result["build"]["duration"])
    error = result.get("error")
    if error == "build_failed":
        return _result("build_and_restart", ok=False,
                       message="前端构建失败，已取消重启",
                       log_tail=result["build"]["log_tail"][-1000:])
    messages = {"build_in_progress": "已有构建任务进行中", "frontend_not_found": "未找到前端目录"}
    return _result("build_and_restart", ok=False, message=messages.get(error or "", error))


# ── 项目更新 ──────────────────────────────────────────────────────────

@tool(name="update_project", group="devops")
def update_project() -> str:
    """从远程仓库拉取项目最新代码（git pull --ff-only）。

    如果遇到合并冲突，不会自动解决，请主动联系主人处理。
    """
    result = service.git_pull()
    return _result("update_project", **result)


@tool(name="update_and_restart", group="devops", timeout=330)
def update_and_restart() -> str:
    """一步完成：拉取最新代码 + 构建前端 + 重启应用。

    流程：git pull → 检查冲突 → 构建前端 → 构建成功则重启。
    遇到冲突或构建失败时不会重启，请主动联系主人解决。
    """
    pull = service.git_pull()
    if not pull["ok"]:
        pull["message"] = f"{pull.get('message', '更新失败')}（不会重启）"
        return _result("update_and_restart", **pull)

    result = service.build_and_restart_blocking()
    if result["ok"]:
        return _result("update_and_restart", ok=True,
                       pull_result=pull.get("pull_result"),
                       message="代码已更新、前端已构建，应用即将重启...")
    return _result("update_and_restart", ok=False,
                   pull_result=pull.get("pull_result"),
                   message="代码已更新，但前端构建失败，已取消重启",
                   log_tail=(result.get("build") or {}).get("log_tail", "")[-1000:])
