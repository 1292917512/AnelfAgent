"""按轮次停止执行与继续 Web 会话，复用运行时中断和原会话调度。

模型可见：继续是一条用户明确提交的续接请求，要求先核对中断中的操作。
Token：每次继续增加一条短消息；操作细节复用现有执行日志按需读取。
缓存：不改稳定前缀，不复制旧工具链，也不重放工具调用。
"""

from __future__ import annotations

from typing import Any, Literal

from agent.messages import parse_entity_scope
from services._runtime import get_runtime
from services.chat import ChatService
from services.workspace_activity import workspace_activity


class ExecutionControlError(Exception):
    """无法操作指定轮次时的业务错误。"""

    def __init__(self, detail: str, status_code: int = 409) -> None:
        super().__init__(detail)
        self.status_code = status_code


async def control_execution(run_id: str, action: Literal["stop", "resume"]) -> dict[str, Any]:
    """按运行时标识停止执行；Web 对话的续接保持原会话与幂等规则。"""
    runtime = get_runtime()
    if runtime is None:
        raise ExecutionControlError("智能体尚未就绪", 503)
    if action == "stop":
        scope = runtime.mind.interrupts.scope_for_run(run_id)
        if scope is None:
            if workspace_activity.get_run(run_id) is None:
                raise ExecutionControlError("执行记录已过期，请刷新工作区", 404)
            return {"status": "idle"}
        if runtime.mind.interrupts.is_requested(scope):
            return {"status": "accepted"}
        workspace_activity.set_control(run_id, "stopping")
        kind, channel, user, session = parse_entity_scope(scope)
        try:
            if kind == "user" and channel == "webui" and user == "web_user":
                result = await ChatService().interrupt_chat(session or "default")
            else:
                from agent.execution_control import stop_execution
                result = await stop_execution(runtime.mind, runtime.delegation_manager,
                                              run_id=run_id, reason="用户停止执行")
        except BaseException:
            workspace_activity.set_control(run_id, "")
            raise
        if result["status"] != "ok":
            workspace_activity.set_control(run_id, "")
        return result
    run = workspace_activity.get_run(run_id)
    if run is None:
        raise ExecutionControlError("执行记录已过期，请刷新工作区", 404)
    kind, channel, user, session = parse_entity_scope(run["scope"])
    if run["kind"] != "conversation" or kind != "user" or channel != "webui" or user != "web_user":
        raise ExecutionControlError("此入口仅支持 Web 对话", 400)
    if action == "resume" and run.get("control") in {"resuming", "resumed"}:
        return {"status": "accepted"}
    if workspace_activity.latest_conversation(run["scope"]) != run_id:
        raise ExecutionControlError("该会话已有后续轮次，请操作最新执行")
    chat = ChatService()
    if run["status"] != "cancelled":
        raise ExecutionControlError("只有已中止的对话可以继续，请等待停止完成")
    mind = runtime.mind
    if (run["scope"] in mind.active_reply_scopes
            or any(scope == run["scope"] for scope, *_ in runtime.pfc.peek_all_tasks())
            or mind.background_tasks.running(run["scope"])):
        raise ExecutionControlError("该会话仍有运行或待处理工作，请等待结束后继续")
    workspace_activity.set_control(run_id, "resuming")
    try:
        await chat.send_message(
            "继续刚才中止的任务。请根据本会话的中止记录和已执行操作摘要接着处理；"
            "已完成的操作不要重复执行，结果不确定的操作先核对实际状态，再决定是否重试。",
            chat_id=session or None, message_id=f"resume-{run_id}",
        )
    except BaseException:
        workspace_activity.set_control(run_id, "")
        raise
    workspace_activity.set_control(run_id, "resumed")
    return {"status": "accepted"}
