"""运维工具 — 应用重启、前端构建、项目代码更新、崩溃信息查询。

提供 AI 自主管理部署流程的能力，核心逻辑统一在 service.py（与 Web 面板同源）：
- 重启应用（优雅关闭后由外层启动脚本按退出码 42 重新拉起）
- 查询最近一次进程崩溃信息（崩溃退出码由守护循环自动拉起并落盘）
- 构建前端并重启（构建失败则不重启）
- 从远程拉取项目最新代码，可一步完成更新并重启
- 遇到 Git 冲突时提示联系主人

重启交接体验（Model Experience 三行声明）：
① 模型看到什么——重启类工具返回值指示 AI 立即 end_reply 结束本轮；
   系统等待当前回复轮收尾后才真正关停；进程重新拉起后由
   RestartHandoffWatcher 向原会话推送 [push:devops] 一次性通知
   （写对话历史 system 角色），含"重启成功"与 AI 重启前的留言，并唤醒一轮思维。
② token 影响——仅重启后首轮一条通知（≤2000 字符），常态为零。
③ 缓存影响——通知写对话历史尾部纯追加（conversation 层），不触碰前缀缓存。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional, Set

from entities._sdk import (
    context_provider,
    entity,
    get_current_channel,
    get_owner_scope,
    push_notify,
    tool,
)

from . import service

entity("devops", "运维管理 - 应用重启、前端构建、项目代码更新")


def _result(action: str, **fields: Any) -> str:
    """组装工具返回（统一携带 action 标识）。"""
    return json.dumps({"action": action, **fields}, ensure_ascii=False)


def _restart_handoff(message: str) -> Dict[str, str]:
    """捕获当前会话上下文组装重启交接（scope / 回复路由 / AI 留言）。"""
    return {
        "scope": get_owner_scope(),
        "channel": get_current_channel(),
        "message": message.strip(),
    }


# 重启后唤醒推送的延迟（等频道与长驻服务就绪，保证回复可路由回原频道）
_WAKE_DELAY_SECONDS = 5.0


# ── 重启应用 ──────────────────────────────────────────────────────────

@tool(name="restart_app", group="devops")
def restart_app(message: str = "") -> str:
    """重启应用进程。

    触发优雅关闭（完整清理 WebUI / 频道 / MCP 等资源）后退出，
    由外层启动脚本（start.sh / start.bat）自动重新拉起应用。
    进程若非启动脚本守护拉起，重启等同关机，请求会被拒绝并说明。
    调用成功后请立即调用 end_reply 结束本轮回复：系统会等本轮思考
    收尾后再执行重启，重启完成后会自动唤醒你并回报重启结果。

    Args:
        message: 给重启后的自己的留言（接续指令 / 待办 / 要验证的事项），可空
    """
    result = service.request_restart(
        source="tool:restart_app", wait_idle=True,
        handoff=_restart_handoff(message),
    )
    if not result.get("ok"):
        return _result("restart_app", ok=False,
                       message=result.get("message", "重启请求被拒绝"))
    note = "重启已排定：请立即调用 end_reply 结束本轮回复（不要再调用其他工具）。" \
           "系统会等本轮思考收尾后自动重启，重启完成后会唤醒你并回报结果"
    if message.strip():
        note += "（含你的留言）。"
    else:
        note += "。"
    return _result("restart_app", ok=True, message=note,
                   already_pending=bool(result.get("already_pending")))


@tool(name="get_crash_report", concurrency_safe=True, group="devops")
def get_crash_report() -> str:
    """查询最近一次进程崩溃信息（退出码/信号/系统崩溃报告摘要）。

    异常重启后可用于排查崩溃原因；启动脚本守护循环会在进程以段错误等
    致命信号退出时自动重新拉起，并把崩溃状态落盘供本工具读取。
    无崩溃记录时返回 has_crash=false。
    """
    return _result("get_crash_report", **service.get_crash_info())


@tool(name="build_and_restart", group="devops", timeout=330)
def build_and_restart(message: str = "") -> str:
    """重新构建前端（npm run build）并重启应用。

    构建成功后自动重启；构建失败则取消重启并返回构建日志尾部。
    重启语义同 restart_app：构建成功后请立即 end_reply 结束本轮回复，
    系统会等本轮思考收尾后再重启，完成后自动唤醒你并回报结果。

    Args:
        message: 给重启后的自己的留言（接续指令 / 待办 / 要验证的事项），可空
    """
    result = service.build_and_restart_blocking(
        wait_idle=True, handoff=_restart_handoff(message))
    if result["ok"]:
        return _result("build_and_restart", ok=True,
                       message="前端构建成功。请立即调用 end_reply 结束本轮回复，"
                               "系统会等本轮思考收尾后自动重启，完成后唤醒你并回报结果。",
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
def update_and_restart(message: str = "") -> str:
    """一步完成：拉取最新代码 + 构建前端 + 重启应用。

    流程：git pull → 检查冲突 → 构建前端 → 构建成功则重启。
    遇到冲突或构建失败时不会重启，请主动联系主人解决。
    重启语义同 restart_app：成功后请立即 end_reply 结束本轮回复。

    Args:
        message: 给重启后的自己的留言（接续指令 / 待办 / 要验证的事项），可空
    """
    pull = service.git_pull()
    if not pull["ok"]:
        pull["message"] = f"{pull.get('message', '更新失败')}（不会重启）"
        return _result("update_and_restart", **pull)

    result = service.build_and_restart_blocking(
        wait_idle=True, handoff=_restart_handoff(message))
    if result["ok"]:
        return _result("update_and_restart", ok=True,
                       pull_result=pull.get("pull_result"),
                       message="代码已更新、前端已构建。请立即调用 end_reply 结束本轮回复，"
                               "系统会等本轮思考收尾后自动重启，完成后唤醒你并回报结果。")
    return _result("update_and_restart", ok=False,
                   pull_result=pull.get("pull_result"),
                   message="代码已更新，但前端构建失败，已取消重启",
                   log_tail=(result.get("build") or {}).get("log_tail", "")[-1000:])


# ── 重启交接唤醒 ──────────────────────────────────────────────────────


@context_provider(name="devops_restart", priority=90, max_tokens=50, group="devops")
class RestartHandoffWatcher:
    """重启交接 watcher：启动时消费交接文件，向原会话推送重启结果并唤醒思维。

    不提供 volatile 注入（provide 恒 None），仅借 provider 生命周期的
    on_start 在 bootstrap 末尾获得安全的启动钩子（此时运行时已组装，
    推送通道可用）。
    """

    def __init__(self) -> None:
        self._tasks: Set[asyncio.Task[None]] = set()

    async def on_start(self) -> None:
        """bootstrap 末尾调用：有交接则后台延迟投递（不阻塞启动流程）。"""
        handoff = service.consume_handoff()
        if not handoff:
            return
        task = asyncio.create_task(
            self._deliver(handoff), name="devops.restart_wakeup")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _deliver(self, handoff: Dict[str, Any]) -> None:
        """延迟推送"重启成功 + AI 留言"一次性通知并唤醒一轮思维。

        措辞为陈述式并显式标注一次性：该通知会作为一条历史消息留在会话
        窗口内数轮，祈使句式会让 AI 在后续无关轮次重复执行留言指令。
        """
        await asyncio.sleep(_WAKE_DELAY_SECONDS)
        message = str(handoff.get("message") or "").strip()
        content = "[系统] 应用已完成重启并恢复运行（本条为一次性重启回报，回应一次即可，之后无需再处理）。"
        if message:
            content += f"\n你在重启前留下的接续指令：\n{message}"
        else:
            content += "如重启前有未完成的事项，可基于对话历史继续。"
        push_notify(
            content,
            source="devops",
            scope=str(handoff.get("scope") or ""),
            channel=str(handoff.get("channel") or ""),
            trigger=True,
        )

    async def provide(self, scope: str) -> Optional[str]:
        """不注入 volatile 层（仅借生命周期做启动钩子）。"""
        return None
