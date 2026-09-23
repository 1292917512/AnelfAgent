"""工作流工具面 — AI 在对话中启动/查询/停止/续跑工作流。

WorkflowEngine 引用经 ``workflow_engine_port`` 晚绑定端口分发（由
agent.runtime.wiring 统一施绑）。工作流后台执行、完成自动通知（后台
任务注册表路由），启动返回 run_id 供查询与续跑。
"""

from __future__ import annotations

import json

from core.entity import EntityRegistry
from core.latebind import LateBinding
from core.log import log
from core.tool_errors import ErrorCause, error_from_exception, tool_error
from entities._sdk import deferred_tool

EntityRegistry.register_group_order("workflow", 24)

#: 工作流引擎端口（bootstrap 经 agent.runtime.wiring 施绑）
workflow_engine_port: LateBinding = LateBinding("workflow.engine")


def _engine_or_none():
    if not workflow_engine_port.bound:
        return None
    return workflow_engine_port.get()


def _engine_not_ready() -> str:
    return tool_error(
        "工作流引擎未初始化",
        cause=ErrorCause.STATE, retryable=False,
        hint="工作流组件未就绪，请检查服务启动状态",
    )


def _spec_example() -> dict:
    return {
        "name": "并行调研并汇总",
        "steps": [
            {"key": "topic_a", "kind": "ask", "goal": "调研主题 A 并输出要点",
             "phase": "并行调研"},
            {"key": "topic_b", "kind": "ask", "goal": "调研主题 B 并输出要点",
             "phase": "并行调研"},
            {"key": "summary", "kind": "ask", "depends_on": ["topic_a", "topic_b"],
             "goal": "汇总上游结果为结构化报告", "continue_from": "topic_a",
             "phase": "汇总"},
            {"key": "verify", "kind": "tool", "depends_on": ["summary"],
             "tool": "shell", "args": {"command": "echo ok"},
             "gate": {"field": "exit_code", "equals": 0, "max_rounds": 3},
             "phase": "把关"},
        ],
    }


@deferred_tool(
    name="workflow_start",
    group="workflow", tags=["always"], source="mind.workflow",
    description="启动可恢复工作流：声明式步骤 DAG（ask=子代理委托 / tool=工具调用），"
    "依赖自动注入上游结果、断点续跑、门控修复重跑。后台执行完成自动通知。"
    "规格：{\"name\":..., \"steps\":[{key,kind,goal|tool,args,depends_on,phase,"
    "gate:{field,equals,max_rounds},continue_from,retries,timeout}]}。"
    "resume_of 传已结束的 run_id 可修订重启（输入一致的已完成步骤直接导入不重跑）。",
)
async def workflow_start(
        spec: str = "",
        resume_of: str = "",
) -> str:
    """启动工作流（立即返回 run_id，后台执行完成自动通知）。

    Args:
        spec: 工作流规格 JSON 字符串（结构见工具描述）
        resume_of: 可选，修订源 run_id——其输入一致的已完成步骤直接导入
    """
    engine = _engine_or_none()
    if engine is None:
        return _engine_not_ready()
    if not spec.strip():
        return tool_error(
            "必须提供 spec 参数",
            cause=ErrorCause.PARAM, retryable=False,
            example=json.dumps(_spec_example(), ensure_ascii=False),
        )
    try:
        payload = json.loads(spec)
    except json.JSONDecodeError as exc:
        return error_from_exception(
            exc, action="解析 spec 参数",
            hint="spec 应为工作流规格的 JSON 对象字符串",
        )
    from agent.mind.tool_activation import current_owner_scope
    try:
        summary = await engine.start(payload, scope=current_owner_scope(),
                                     resume_of=resume_of.strip())
    except ValueError as exc:
        return error_from_exception(exc, action="启动工作流")
    except Exception as exc:
        # WorkflowSpecError（ValueError 子类）已在上方归因；此处兜底引擎异常
        return error_from_exception(exc, action="启动工作流")
    return json.dumps({
        "ok": True, **summary,
        "message": f"工作流已启动（{summary['run_id']}），后台执行中，"
                   "完成后系统会自动通知你；期间可用 workflow_status 查询进度。",
    }, ensure_ascii=False)


@deferred_tool(
    name="workflow_status",
    group="workflow", tags=["always"], source="mind.workflow",
    description="查询工作流状态：指定 run_id 返回该运行的步骤明细与最近事件，"
    "缺省返回最近运行列表（含进行中）。启动工作流后用它查询进度，禁止凭空猜测状态。",
)
async def workflow_status(run_id: str = "", limit: int = 10) -> str:
    """查询工作流状态（单运行详情或最近列表）。

    Args:
        run_id: 可选，指定运行时返回步骤明细与事件时间线
        limit: 列表模式的返回条数
    """
    engine = _engine_or_none()
    if engine is None:
        return _engine_not_ready()
    try:
        if run_id.strip():
            detail = await engine.run_detail(run_id.strip())
            # 事件流只带尾部（时间线语义：AI 关心最近进展，全量落 Web 页）
            detail["events"] = detail["events"][-40:]
            return json.dumps(detail, ensure_ascii=False)
        runs = await engine.list_runs(max(1, min(limit, 50)))
        return json.dumps({"runs": runs}, ensure_ascii=False)
    except ValueError as exc:
        return tool_error(str(exc), cause=ErrorCause.NOT_FOUND, retryable=False)


@deferred_tool(
    name="workflow_stop",
    group="workflow", tags=["always"], source="mind.workflow",
    description="停止运行中的工作流：在飞步骤安全取消，已完成步骤保留；"
    "停止后可用 workflow_resume 断点续跑（不重付费）。",
)
async def workflow_stop(run_id: str) -> str:
    """停止运行中的工作流。

    Args:
        run_id: workflow_start 返回的运行 ID
    """
    engine = _engine_or_none()
    if engine is None:
        return _engine_not_ready()
    result = engine.stop(run_id.strip())
    if not result.get("ok"):
        return tool_error(str(result.get("error", "停止失败")),
                          cause=ErrorCause.NOT_FOUND, retryable=False)
    log(f"AI 停止工作流: {run_id}", tag="工作流")
    return json.dumps({"ok": True, **result}, ensure_ascii=False)


@deferred_tool(
    name="workflow_resume",
    group="workflow", tags=["always"], source="mind.workflow",
    description="续跑已停止的工作流：已完成步骤经输入指纹校验后直接复用"
    "（不重付费），中断步骤重新执行；后台运行完成自动通知。",
)
async def workflow_resume(run_id: str) -> str:
    """续跑已停止的工作流。

    Args:
        run_id: workflow_start 返回、后被停止的运行 ID
    """
    engine = _engine_or_none()
    if engine is None:
        return _engine_not_ready()
    try:
        summary = await engine.resume(run_id.strip())
    except ValueError as exc:
        return tool_error(str(exc), cause=ErrorCause.NOT_FOUND, retryable=False)
    return json.dumps({
        "ok": True, **summary,
        "message": "工作流已续跑（已完成步骤直接复用），完成后自动通知。",
    }, ensure_ascii=False)
