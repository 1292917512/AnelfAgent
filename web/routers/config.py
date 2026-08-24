"""统一配置 API 路由 -- 为前端提供 WebUI 配置及全局配置快照。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from core.log import log
from core.path import ConfigPaths
from services import (
    AgentStatusService,
    ConfigService,
    HeartbeatService,
    TaskService,
)
from services.heartbeat import HeartbeatServiceError
from services.task import TaskServiceError, TaskStorageError
from web.routers._errors import server_error
from web.routers.schemas import MindConfigUpdate

router = APIRouter(prefix="/config", tags=["config"])

_status_svc = AgentStatusService()
_config_svc = ConfigService()
_heartbeat_svc = HeartbeatService()
_task_svc = TaskService()

_APP_CONFIG_PATH = Path(ConfigPaths.APP_CONFIG)

# 敏感字段，读取时脱敏
_APP_SECRET_FIELDS = frozenset({"telegram_bot_token", "telegram_webhook_secret"})

_WEBUI_CONFIG_PATH = Path(ConfigPaths.WEBUI_CONFIG)

# snapshot 聚合时，已知配置 key 映射到 ConfigPaths 属性（动态解析，跟随目录配置）
_SNAPSHOT_PATH_KEYS = {
    "app": "APP_CONFIG",
    "mind": "MIND_CONFIG",
    "llm": "LLM_CLIENTS",
    "mcp": "MCP_SERVERS",
    "personas": "PERSONAS_INDEX",
}


def _load_webui_config() -> Dict[str, Any]:
    """加载 webui.json 配置（每次读取以支持热更新）。"""
    if _WEBUI_CONFIG_PATH.exists():
        try:
            return json.loads(_WEBUI_CONFIG_PATH.read_text("utf-8"))
        except Exception as e:
            log(f"WebUI 配置加载失败: {e}", "DEBUG")
    return {}


@router.get("/webui")
async def get_webui_config() -> Dict[str, Any]:
    """返回 WebUI 配置（品牌、主题、导航、配置索引）。

    导航由 webui.json 显式声明 + 实体 manifest 自动推导合并（显式优先）。
    """
    cfg = _load_webui_config()
    nav: List[Dict[str, Any]] = list(cfg.get("navigation", []))
    known = {item.get("path") for item in nav}
    # 从已注册实体推导补充导航（不覆盖 webui.json 中的显式声明）
    try:
        from core.entity import EntityRegistry
        for group in EntityRegistry.list_groups():
            manifest = EntityRegistry.get_group_manifest(group) or {}
            nav_meta = manifest.get("nav")
            if not nav_meta:
                continue
            path = nav_meta.get("path") or f"/{group}"
            if path in known:
                continue
            nav.append({
                "path": path,
                "label": nav_meta.get("label", group),
                "icon": nav_meta.get("icon") or manifest.get("icon", "Box"),
                "group": nav_meta.get("nav_group", "group_ability"),
            })
    except Exception as e:
        log(f"实体导航推导失败: {e}", "DEBUG")
    return {**cfg, "navigation": nav}


@router.get("/webui/navigation")
async def get_navigation() -> Dict[str, Any]:
    """仅返回导航配置。"""
    cfg = _load_webui_config()
    return {"navigation": cfg.get("navigation", [])}


@router.get("/webui/theme")
async def get_theme() -> Dict[str, Any]:
    """仅返回主题配置。"""
    cfg = _load_webui_config()
    return {
        "branding": cfg.get("branding", {}),
        "theme": cfg.get("theme", {}),
    }


@router.get("/snapshot")
async def get_config_snapshot() -> Dict[str, Any]:
    """返回所有配置文件的聚合快照（不含敏感信息）。

    已知配置 key 优先经 ConfigPaths 动态解析（跟随 ANELF_CONFIG_DIR 等目录配置），
    webui.json configs 索引中的字面路径仅作为未知 key 的回退。
    """
    webui = _load_webui_config()
    configs_index: Dict[str, str] = webui.get("configs", {})

    snapshot: Dict[str, Any] = {
        "branding": webui.get("branding", {}),
        "server": webui.get("server", {}),
    }

    for key, path_str in configs_index.items():
        resolved = _SNAPSHOT_PATH_KEYS.get(key)
        p = Path(getattr(ConfigPaths, resolved)) if resolved else Path(path_str)
        if not p.exists():
            snapshot[key] = None
            continue
        try:
            data = json.loads(p.read_text("utf-8"))
            if key == "llm":
                data = _mask_llm_secrets(data)
            elif key == "app":
                data = _mask_app_secrets(data)
            snapshot[key] = data
        except Exception as e:
            log(f"配置快照读取失败 ({key}): {e}", "DEBUG")
            snapshot[key] = None

    return snapshot


def _mask_key(key: str) -> str:
    if key and len(key) > 8:
        return key[:4] + "****" + key[-4:]
    return "****" if key else ""


def _mask_llm_secrets(data: Dict[str, Any]) -> Dict[str, Any]:
    """遮蔽 LLM 配置中的 API Key（兼容新旧格式）。"""
    masked = dict(data)
    # 新格式：providers 列表
    for prov in masked.get("providers", []):
        if isinstance(prov, dict) and "api_key" in prov:
            prov["api_key"] = _mask_key(prov["api_key"])
    # 旧格式兼容
    for client in masked.get("clients", []):
        if isinstance(client, dict) and "api_key" in client:
            client["api_key"] = _mask_key(client["api_key"])
    return masked


def _mask_app_secrets(data: Dict[str, Any]) -> Dict[str, Any]:
    """遮蔽应用配置中的敏感字段。"""
    masked = dict(data)
    for k in _APP_SECRET_FIELDS:
        if k in masked and masked[k] and len(str(masked[k])) > 8:
            v = str(masked[k])
            masked[k] = v[:4] + "****" + v[-4:]
    return masked


@router.get("/app")
async def get_app_config() -> Dict[str, Any]:
    """返回 app_config.json 内容（敏感字段已脱敏）。"""
    if not _APP_CONFIG_PATH.exists():
        raise HTTPException(status_code=404, detail="app_config.json 不存在")
    try:
        data: Dict[str, Any] = json.loads(_APP_CONFIG_PATH.read_text("utf-8"))
    except Exception as e:
        raise server_error("读取配置", e) from e
    return _mask_app_secrets(data)


# ──────────────────────────────────────────────────────────────────────────────
# Mind 配置（mind_config.json，代理到 AgentStatusService）
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/mind")
async def get_mind_config() -> Dict[str, Any]:
    """返回 Mind 配置（代理到 AgentStatusService）。"""
    config = _status_svc.get_mind_config()
    return {"config": config or {}}


@router.put("/mind")
async def save_mind_config(data: MindConfigUpdate) -> Dict[str, str]:
    """保存 Mind 配置（代理到 AgentStatusService）。"""
    params = {k: v for k, v in data.model_dump().items() if v is not None}
    _status_svc.save_mind_config(params)
    return {"status": "ok"}


# ──────────────────────────────────────────────────────────────────────────────
# 心跳系统配置（config/heartbeat.json）
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/heartbeat")
async def get_heartbeat_config() -> Dict[str, Any]:
    """返回心跳调度配置。"""
    return _heartbeat_svc.get_config()


class HeartbeatConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    interval_seconds: Optional[int] = None
    analysis_temperature: Optional[float] = None
    min_conversations_for_analysis: Optional[int] = None
    task_schedules: Optional[List[Dict[str, Any]]] = None


@router.put("/heartbeat")
async def save_heartbeat_config(data: HeartbeatConfigUpdate) -> Dict[str, str]:
    """保存心跳配置并热重载。"""
    params = {k: v for k, v in data.model_dump().items() if v is not None}
    try:
        _heartbeat_svc.save_config(params)
    except HeartbeatServiceError as e:
        raise HTTPException(e.status_code, str(e)) from e
    return {"status": "ok"}


@router.get("/heartbeat/status")
async def get_heartbeat_status() -> Dict[str, Any]:
    """返回心跳引擎运行状态。"""
    return _heartbeat_svc.get_status()


@router.post("/heartbeat/trigger")
async def trigger_heartbeat() -> Dict[str, str]:
    """手动触发一次心跳。"""
    try:
        _heartbeat_svc.trigger()
    except HeartbeatServiceError as e:
        raise HTTPException(e.status_code, str(e)) from e
    return {"status": "triggered"}


# ──────────────────────────────────────────────────────────────────────────────
# 任务单元 CRUD + 触发（config/tasks/*.json）
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/tasks")
async def list_tasks() -> List[Dict[str, Any]]:
    """列出所有任务单元（config/tasks/**/*.json，递归子目录）。"""
    return _task_svc.list_tasks()


@router.get("/tasks/{name}")
async def get_task(name: str, folder: str = Query("")) -> Dict[str, Any]:
    try:
        return _task_svc.get_task(name, folder)
    except TaskServiceError as e:
        raise HTTPException(e.status_code, str(e)) from e
    except TaskStorageError as e:
        raise server_error(e.action, e.cause) from e


class TaskCreate(BaseModel):
    name: str
    display_name: str = ""
    description: str = ""
    scope: str = "global"
    enabled: bool = True
    memory_type: str = "semantic"
    importance: float = 0.5
    tags: List[str] = []
    source: str = ""
    null_keywords: List[str] = []
    tool_tags: List[str] = []
    prompt: str
    allow_output_tools: bool = False
    save_result_to_memory: bool = True
    model_id: Optional[str] = None
    reasoning_effort: Optional[str] = None
    folder: str = ""


@router.post("/tasks", status_code=201)
async def create_task(data: TaskCreate) -> Dict[str, Any]:
    try:
        return await _task_svc.create_task(data.model_dump())
    except TaskServiceError as e:
        raise HTTPException(e.status_code, str(e)) from e
    except TaskStorageError as e:
        raise server_error(e.action, e.cause) from e


class TaskUpdate(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    scope: Optional[str] = None
    enabled: Optional[bool] = None
    memory_type: Optional[str] = None
    importance: Optional[float] = None
    tags: Optional[List[str]] = None
    source: Optional[str] = None
    null_keywords: Optional[List[str]] = None
    tool_tags: Optional[List[str]] = None
    prompt: Optional[str] = None
    allow_output_tools: Optional[bool] = None
    save_result_to_memory: Optional[bool] = None
    model_id: Optional[str] = None
    reasoning_effort: Optional[str] = None
    folder: Optional[str] = None


@router.put("/tasks/{name}")
async def update_task(name: str, data: TaskUpdate, folder: str = Query("")) -> Dict[str, Any]:
    try:
        return await _task_svc.update_task(name, folder, data.model_dump(exclude_unset=True))
    except TaskServiceError as e:
        raise HTTPException(e.status_code, str(e)) from e
    except TaskStorageError as e:
        raise server_error(e.action, e.cause) from e


@router.delete("/tasks/{name}")
async def delete_task(name: str, folder: str = Query("")) -> Dict[str, str]:
    try:
        await _task_svc.delete_task(name, folder)
    except TaskServiceError as e:
        raise HTTPException(e.status_code, str(e)) from e
    except TaskStorageError as e:
        raise server_error(e.action, e.cause) from e
    return {"status": "ok"}


@router.post("/tasks/trigger/{name}")
async def trigger_task(name: str, folder: str = Query("")) -> Dict[str, str]:
    """手动触发执行指定任务，在后台异步执行。"""
    try:
        _task_svc.trigger_task(name, folder)
    except TaskServiceError as e:
        raise HTTPException(e.status_code, str(e)) from e
    return {"status": "triggered", "task": name}


# ──────────────────────────────────────────────────────────────────────────────
# Web 工具配置（entities/web/config.json）
# ──────────────────────────────────────────────────────────────────────────────


class WebToolsConfigUpdate(BaseModel):
    proxy: Optional[str] = None


@router.get("/web-tools")
async def get_web_tools_config() -> Dict[str, Any]:
    """返回 Web 工具配置（仅非敏感字段；提供者矩阵管理走 /api/entity/web/matrix）。"""
    return {"proxy": _config_svc.get_web_tools_proxy()}


@router.put("/web-tools")
async def save_web_tools_config(data: WebToolsConfigUpdate) -> Dict[str, str]:
    """保存 Web 工具配置（代理等）。"""
    updates: Dict[str, Any] = {}
    if data.proxy is not None:
        updates["proxy"] = data.proxy
    if not updates:
        return {"status": "ok", "message": "无变更"}
    _config_svc.update_web_tools_config(updates)
    return {"status": "ok"}
