"""工作流 API 路由。"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from services.workflow import (
    WorkflowRuntimeError,
    WorkflowService,
    WorkflowServiceError,
)

router = APIRouter(prefix="/workflow", tags=["workflow"])

_wf_svc = WorkflowService()


class StartRunRequest(BaseModel):
    """启动请求：spec 为工作流规格对象；resume_of 为修订源 run_id。"""

    spec: Dict[str, Any] | None = None
    scope: str = ""
    resume_of: str = ""
    workflow_id: str = ""


class SaveDefinitionRequest(BaseModel):
    """可复用定义的完整规格；保存不触发执行。"""

    spec: Dict[str, Any]


class CleanupRunsRequest(BaseModel):
    """保留天数为 0 时清理全部可清理历史，默认保留停止记录。"""

    days: int = Field(default=30, ge=0)
    include_stopped: bool = False


@router.get("/runs")
async def list_runs(limit: int = Query(30, ge=1, le=100),
                    offset: int = Query(0, ge=0)) -> Dict[str, Any]:
    return {"runs": await _wf_svc.list_runs(limit, offset)}


@router.post("/validate")
async def validate_spec(req: SaveDefinitionRequest) -> Dict[str, Any]:
    try:
        return _wf_svc.validate(req.spec)
    except WorkflowServiceError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/runs")
async def start_run(req: StartRunRequest) -> Dict[str, Any]:
    try:
        return await _wf_svc.start(req.spec, scope=req.scope, resume_of=req.resume_of,
                                   workflow_id=req.workflow_id)
    except WorkflowRuntimeError as e:
        raise HTTPException(503, str(e)) from e
    except WorkflowServiceError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/runs/cleanup")
async def cleanup_runs(req: CleanupRunsRequest) -> Dict[str, Any]:
    try:
        return await _wf_svc.cleanup_runs(req.days, include_stopped=req.include_stopped)
    except WorkflowRuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    except WorkflowServiceError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/runs/{run_id}")
async def delete_run(run_id: str) -> Dict[str, Any]:
    try:
        return await _wf_svc.delete_run(run_id)
    except WorkflowRuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    except WorkflowServiceError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/definitions")
async def list_definitions(limit: int = Query(30, ge=1, le=100),
                           offset: int = Query(0, ge=0)) -> Dict[str, Any]:
    try:
        return {"definitions": await _wf_svc.list_definitions(limit, offset)}
    except WorkflowRuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/definitions")
async def create_definition(req: SaveDefinitionRequest) -> Dict[str, Any]:
    return await save_definition("", req)


@router.put("/definitions/{workflow_id}")
async def save_definition(workflow_id: str, req: SaveDefinitionRequest) -> Dict[str, Any]:
    try:
        return await _wf_svc.save_definition(req.spec, workflow_id)
    except WorkflowRuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    except WorkflowServiceError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/definitions/{workflow_id}")
async def definition_detail(workflow_id: str) -> Dict[str, Any]:
    try:
        return await _wf_svc.definition_detail(workflow_id)
    except WorkflowRuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    except WorkflowServiceError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.delete("/definitions/{workflow_id}")
async def delete_definition(workflow_id: str) -> Dict[str, Any]:
    try:
        return await _wf_svc.delete_definition(workflow_id)
    except WorkflowRuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    except WorkflowServiceError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/runs/{run_id}")
async def run_detail(run_id: str) -> Dict[str, Any]:
    try:
        return await _wf_svc.run_detail(run_id)
    except WorkflowRuntimeError as e:
        raise HTTPException(503, str(e)) from e
    except WorkflowServiceError as e:
        raise HTTPException(404, str(e)) from e


@router.post("/runs/{run_id}/stop")
async def stop_run(run_id: str) -> Dict[str, Any]:
    try:
        return _wf_svc.stop(run_id)
    except WorkflowRuntimeError as e:
        raise HTTPException(503, str(e)) from e
    except WorkflowServiceError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/runs/{run_id}/resume")
async def resume_run(run_id: str) -> Dict[str, Any]:
    try:
        return await _wf_svc.resume(run_id)
    except WorkflowRuntimeError as e:
        raise HTTPException(503, str(e)) from e
    except WorkflowServiceError as e:
        raise HTTPException(400, str(e)) from e
