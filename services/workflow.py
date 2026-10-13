"""工作流服务 — Web 侧薄门面（engine 单例经 runtime 收口）。

启动/续跑/停止直达 WorkflowEngine，查询读 journal（断点恢复的事实
源）。规格校验错误以 ``WorkflowServiceError`` 上抛，路由层转 HTTP 400。
"""

from __future__ import annotations

from typing import Any, Dict, List

from agent.workflow.engine import WorkflowEngine
from services._runtime import get_runtime


class WorkflowServiceError(Exception):
    """请求非法（规格校验失败 / 状态不允许），message 直接面向用户。"""


class WorkflowRuntimeError(Exception):
    """运行时未就绪（引擎不可用），路由层映射 503。"""


def _engine() -> WorkflowEngine | None:
    """解析工作流引擎（runtime 未就绪返回 None）。"""
    rt = get_runtime()
    if rt is None:
        return None
    return getattr(rt.mind, "workflow_engine", None)


def _require_engine() -> WorkflowEngine:
    engine = _engine()
    if engine is None:
        raise WorkflowRuntimeError("运行时未就绪，工作流引擎不可用")
    return engine


class WorkflowService:

    def validate(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        """按执行引擎的同一契约校验规格并返回依赖层级。"""
        from agent.workflow.spec import parse_spec, topo_layers
        try:
            parsed = parse_spec(spec)
        except ValueError as exc:
            raise WorkflowServiceError(str(exc)) from exc
        return {"spec": parsed.model_dump(mode="json"), "layers": topo_layers(parsed)}

    async def start(self, spec: Dict[str, Any] | None = None, *, scope: str = "",
                    resume_of: str = "", workflow_id: str = "") -> Dict[str, Any]:
        """启动工作流（后台执行）。"""
        engine = _engine()
        if engine is None:
            raise WorkflowRuntimeError("运行时未就绪，工作流引擎不可用")
        try:
            return await engine.start(spec, scope=scope, resume_of=resume_of, workflow_id=workflow_id)
        except ValueError as exc:
            raise WorkflowServiceError(str(exc)) from exc

    async def resume(self, run_id: str) -> Dict[str, Any]:
        """续跑已停止的运行。"""
        engine = _engine()
        if engine is None:
            raise WorkflowRuntimeError("运行时未就绪，工作流引擎不可用")
        try:
            return await engine.resume(run_id)
        except ValueError as exc:
            raise WorkflowServiceError(str(exc)) from exc

    def stop(self, run_id: str) -> Dict[str, Any]:
        """请求停止运行中的工作流。"""
        engine = _engine()
        if engine is None:
            raise WorkflowRuntimeError("运行时未就绪，工作流引擎不可用")
        result = engine.stop(run_id)
        if not result.get("ok"):
            raise WorkflowServiceError(str(result.get("error", "停止失败")))
        return result

    async def list_runs(self, limit: int = 30, offset: int = 0) -> List[Dict[str, Any]]:
        engine = _engine()
        return await engine.list_runs(limit, offset) if engine is not None else []

    async def list_definitions(self, limit: int = 30, offset: int = 0) -> List[Dict[str, Any]]:
        return await _require_engine().list_definitions(limit, offset)

    async def definition_detail(self, workflow_id: str) -> Dict[str, Any]:
        try:
            return await _require_engine().definition_detail(workflow_id)
        except ValueError as exc:
            raise WorkflowServiceError(str(exc)) from exc

    async def save_definition(self, spec: Dict[str, Any], workflow_id: str = "") -> Dict[str, Any]:
        try:
            return await _require_engine().save_definition(spec, workflow_id)
        except ValueError as exc:
            raise WorkflowServiceError(str(exc)) from exc

    async def delete_definition(self, workflow_id: str) -> Dict[str, Any]:
        try:
            return await _require_engine().delete_definition(workflow_id)
        except ValueError as exc:
            raise WorkflowServiceError(str(exc)) from exc

    async def delete_run(self, run_id: str) -> Dict[str, Any]:
        try:
            return await _require_engine().delete_run(run_id)
        except ValueError as exc:
            raise WorkflowServiceError(str(exc)) from exc

    async def cleanup_runs(self, days: int = 30, *, include_stopped: bool = False) -> Dict[str, Any]:
        try:
            return await _require_engine().cleanup_runs(days, include_stopped=include_stopped)
        except ValueError as exc:
            raise WorkflowServiceError(str(exc)) from exc

    async def run_detail(self, run_id: str) -> Dict[str, Any]:
        engine = _engine()
        if engine is None:
            raise WorkflowRuntimeError("运行时未就绪，工作流引擎不可用")
        try:
            return await engine.run_detail(run_id)
        except ValueError as exc:
            raise WorkflowServiceError(str(exc)) from exc
