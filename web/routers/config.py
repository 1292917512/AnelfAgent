"""统一配置 API 路由 -- 为前端提供 WebUI 配置及全局配置快照。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.log import log
from core.path import ConfigPaths
from services import AgentStatusService

router = APIRouter(prefix="/config", tags=["config"])

_status_svc = AgentStatusService()

_APP_CONFIG_PATH = Path(ConfigPaths.APP_CONFIG)

# 不允许通过 Web 修改的路径类字段（避免误改导致启动失败）
_APP_READONLY_FIELDS = frozenset({
    "personas_dir", "personas_config_path", "mcp_config_path",
    "mind_config_path", "sqlite_path",
})

# 敏感字段，保存时若值含 **** 则跳过
_APP_SECRET_FIELDS = frozenset({"telegram_bot_token", "telegram_webhook_secret"})

_WEBUI_CONFIG_PATH = Path(ConfigPaths.WEBUI_CONFIG)


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
    """返回 WebUI 配置（品牌、主题、导航、配置索引）。"""
    return _load_webui_config()


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
    """返回所有配置文件的聚合快照（不含敏感信息）。"""
    webui = _load_webui_config()
    configs_index: Dict[str, str] = webui.get("configs", {})

    snapshot: Dict[str, Any] = {
        "branding": webui.get("branding", {}),
        "server": webui.get("server", {}),
    }

    for key, path_str in configs_index.items():
        p = Path(path_str)
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
        raise HTTPException(status_code=500, detail=f"读取配置失败: {e}") from e
    return _mask_app_secrets(data)


class AppConfigUpdate(BaseModel):
    max_conversation_size: Optional[int] = None
    max_tool_iterations: Optional[int] = None
    connect_timeout: Optional[float] = None
    read_timeout: Optional[float] = None
    total_timeout: Optional[float] = None
    retry_count: Optional[int] = None
    retry_delay: Optional[float] = None
    backoff_factor: Optional[float] = None
    http_proxy: Optional[str] = None
    https_proxy: Optional[str] = None
    proxy_enabled: Optional[bool] = None
    chunk_size: Optional[int] = None
    user_agent: Optional[str] = None
    overwrite_existing: Optional[bool] = None
    verify_download: Optional[bool] = None
    default_download_dir: Optional[str] = None
    llm_stream_enabled: Optional[bool] = None
    workspace_root: Optional[str] = None
    sandbox_enabled: Optional[bool] = None
    heartbeat_interval: Optional[float] = None
    meta_decision_temperature: Optional[float] = None
    analysis_temperature: Optional[float] = None
    reflect_min_hours: Optional[float] = None
    conversation_analysis_threshold: Optional[int] = None
    log_ai_output: Optional[bool] = None
    send_interim_text: Optional[bool] = None
    vector_search_batch_size: Optional[int] = None
    memory_recall_top_k: Optional[int] = None
    memory_recall_min_score: Optional[float] = None
    memory_time_decay_days: Optional[int] = None
    memory_warn_threshold: Optional[int] = None
    memory_max_per_type: Optional[int] = None
    entity_merge_threshold: Optional[int] = None
    reflection_merge_threshold: Optional[int] = None
    heartbeat_max_entries: Optional[int] = None
    auto_consolidate_enabled: Optional[bool] = None
    conv_recall_scan_limit: Optional[int] = None
    conv_recall_backfill_batch: Optional[int] = None
    conv_recall_min_score: Optional[float] = None
    conv_recall_max_results: Optional[int] = None
    http_api_enabled: Optional[bool] = None
    http_api_host: Optional[str] = None
    http_api_port: Optional[int] = None
    http_api_reply_timeout: Optional[int] = None
    telegram_enabled: Optional[bool] = None
    telegram_bot_token: Optional[str] = None
    telegram_proxy_host: Optional[str] = None
    telegram_proxy_port: Optional[int] = None
    telegram_require_mention: Optional[bool] = None
    telegram_reply_to_mode: Optional[str] = None
    telegram_stream_mode: Optional[str] = None
    telegram_parse_mode: Optional[str] = None
    telegram_link_preview: Optional[bool] = None
    telegram_text_limit: Optional[int] = None
    telegram_webhook_enabled: Optional[bool] = None
    telegram_webhook_url: Optional[str] = None
    telegram_webhook_secret: Optional[str] = None
    telegram_webhook_port: Optional[int] = None


@router.put("/app")
async def save_app_config(data: AppConfigUpdate) -> Dict[str, str]:
    """保存 app_config.json（只更新传入的非 None 字段，路径类字段不可修改）。"""
    if not _APP_CONFIG_PATH.exists():
        raise HTTPException(status_code=404, detail="app_config.json 不存在")
    try:
        existing: Dict[str, Any] = json.loads(_APP_CONFIG_PATH.read_text("utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取配置失败: {e}") from e

    updates = {k: v for k, v in data.model_dump().items() if v is not None}
    for key, val in updates.items():
        if key in _APP_READONLY_FIELDS:
            continue
        # 敏感字段：若值仍是脱敏形式则跳过
        if key in _APP_SECRET_FIELDS and isinstance(val, str) and "****" in val:
            continue
        existing[key] = val

    try:
        _APP_CONFIG_PATH.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入配置失败: {e}") from e

    return {"status": "ok"}


# ──────────────────────────────────────────────────────────────────────────────
# Mind 配置（mind_config.json，代理到 AgentStatusService）
# ──────────────────────────────────────────────────────────────────────────────


from web.routers.schemas import MindConfigUpdate


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
    from agent.heartbeat.config import get_heartbeat_config
    return get_heartbeat_config().to_dict()


class HeartbeatConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    interval_seconds: Optional[int] = None
    analysis_temperature: Optional[float] = None
    min_conversations_for_analysis: Optional[int] = None
    task_schedules: Optional[List[Dict[str, Any]]] = None


@router.put("/heartbeat")
async def save_heartbeat_config(data: HeartbeatConfigUpdate) -> Dict[str, str]:
    """保存心跳配置并热重载。"""
    from agent.heartbeat.config import get_heartbeat_config, TaskSchedule

    cfg = get_heartbeat_config()
    if data.enabled is not None:
        cfg.enabled = data.enabled
    if data.interval_seconds is not None:
        cfg.interval_seconds = max(10, data.interval_seconds)
    if data.analysis_temperature is not None:
        cfg.analysis_temperature = data.analysis_temperature
    if data.min_conversations_for_analysis is not None:
        cfg.min_conversations_for_analysis = data.min_conversations_for_analysis
    if data.task_schedules is not None:
        cfg.task_schedules = [TaskSchedule.from_dict(s) for s in data.task_schedules]
    cfg.save()

    from services._runtime import get_runtime
    rt = get_runtime()
    if rt is not None:
        rt.mind.heartbeat_engine.reload()

    return {"status": "ok"}


@router.get("/heartbeat/status")
async def get_heartbeat_status() -> Dict[str, Any]:
    """返回心跳引擎运行状态。"""
    from services._runtime import get_runtime
    rt = get_runtime()
    if rt is None:
        return {"enabled": False, "total_ticks": 0, "message": "Agent 尚未初始化"}
    return rt.mind.heartbeat_engine.get_status()


@router.post("/heartbeat/trigger")
async def trigger_heartbeat() -> Dict[str, str]:
    """手动触发一次心跳。"""
    import asyncio
    from services._runtime import get_runtime
    rt = get_runtime()
    if rt is None:
        raise HTTPException(status_code=503, detail="Agent 尚未初始化")

    async def _run() -> None:
        try:
            executed = await rt.mind.heartbeat_engine.tick()
            log(f"Web 手动心跳完成: 执行了 {len(executed)} 个任务", tag="心跳")
        except Exception as exc:
            log(f"Web 手动心跳异常: {exc}", "WARNING", tag="心跳")

    asyncio.create_task(_run(), name="agent.heartbeat.web_manual_tick")
    return {"status": "triggered"}


# ──────────────────────────────────────────────────────────────────────────────
# 任务单元 CRUD + 触发（config/tasks/*.json）
# ──────────────────────────────────────────────────────────────────────────────

_TASKS_DIR = Path(ConfigPaths.TASKS_DIR)


def _ensure_tasks_dir() -> None:
    _TASKS_DIR.mkdir(parents=True, exist_ok=True)


def _task_path(name: str) -> Path:
    return _TASKS_DIR / f"{name}.json"


_TASK_DEFAULTS: Dict[str, Any] = {
    "display_name": "", "description": "", "scope": "global",
    "enabled": True, "memory_type": "semantic", "importance": 0.5,
    "tags": [], "source": "", "null_keywords": [], "tool_tags": [], "prompt": "",
    "allow_output_tools": False,
    "save_result_to_memory": True,
}

_OPTIONAL_TASK_OVERRIDE_FIELDS = ("model_id", "reasoning_effort")
_TASK_REASONING_EFFORTS = frozenset({"low", "medium", "high", "max"})


def _to_bool(value: Any, *, default: bool = False) -> bool:
    """兼容字符串/数字的布尔值解析。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _normalize_task(data: Dict[str, Any]) -> Dict[str, Any]:
    """确保任务数据包含所有必需字段（兼容旧格式缺失字段）。"""
    for k, v in _TASK_DEFAULTS.items():
        if k not in data:
            data[k] = v
    data["allow_output_tools"] = _to_bool(data.get("allow_output_tools"), default=False)
    data["save_result_to_memory"] = _to_bool(data.get("save_result_to_memory"), default=True)
    return _normalize_optional_task_overrides(data)


def _normalize_optional_task_overrides(data: Dict[str, Any]) -> Dict[str, Any]:
    """标准化可选覆盖字段：空值转为“移除字段”，非空字符串做 trim。"""
    for field in _OPTIONAL_TASK_OVERRIDE_FIELDS:
        if field not in data:
            continue
        raw = data.get(field)
        if raw is None:
            data.pop(field, None)
            continue
        normalized = str(raw).strip()
        if not normalized:
            data.pop(field, None)
            continue
        if field == "reasoning_effort":
            lowered = normalized.lower()
            if lowered in _TASK_REASONING_EFFORTS:
                data[field] = lowered
            else:
                data.pop(field, None)
            continue
        data[field] = normalized
    return data


def _load_task(name: str) -> Dict[str, Any]:
    p = _task_path(name)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"任务 [{name}] 不存在")
    try:
        return _normalize_task(json.loads(p.read_text("utf-8")))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取任务配置失败: {e}") from e


@router.get("/tasks")
async def list_tasks() -> List[Dict[str, Any]]:
    """列出所有任务单元（config/tasks/*.json）。"""
    _ensure_tasks_dir()
    tasks: List[Dict[str, Any]] = []
    for json_file in sorted(_TASKS_DIR.glob("*.json")):
        try:
            data = _normalize_task(json.loads(json_file.read_text("utf-8")))
            tasks.append(data)
        except Exception as e:
            log(f"任务配置解析失败 ({json_file.name}): {e}", "DEBUG")
    return tasks


@router.get("/tasks/{name}")
async def get_task(name: str) -> Dict[str, Any]:
    return _load_task(name)


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


@router.post("/tasks", status_code=201)
async def create_task(data: TaskCreate) -> Dict[str, Any]:
    _ensure_tasks_dir()
    p = _task_path(data.name)
    if p.exists():
        raise HTTPException(status_code=409, detail=f"任务 [{data.name}] 已存在")

    task_data = _normalize_optional_task_overrides(data.model_dump())
    if not task_data.get("source"):
        task_data["source"] = data.name
    if not task_data.get("display_name"):
        task_data["display_name"] = data.name
    _normalize_task(task_data)

    try:
        p.write_text(json.dumps(task_data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入失败: {e}") from e

    _reload_task_registry()
    return task_data


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


@router.put("/tasks/{name}")
async def update_task(name: str, data: TaskUpdate) -> Dict[str, Any]:
    existing = _load_task(name)
    provided_fields = set(data.model_fields_set)
    updates = data.model_dump(exclude_unset=True)
    updates = _normalize_optional_task_overrides(updates)
    existing.update(updates)
    for field in _OPTIONAL_TASK_OVERRIDE_FIELDS:
        if field in provided_fields and field not in updates:
            existing.pop(field, None)

    try:
        _task_path(name).write_text(
            json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入失败: {e}") from e

    _reload_task_registry()
    return existing


@router.delete("/tasks/{name}")
async def delete_task(name: str) -> Dict[str, str]:
    p = _task_path(name)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"任务 [{name}] 不存在")
    try:
        p.unlink()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除失败: {e}") from e

    _reload_task_registry()
    return {"status": "ok"}


@router.post("/tasks/trigger/{name}")
async def trigger_task(name: str) -> Dict[str, str]:
    """手动触发执行指定任务，在后台异步执行。"""
    import asyncio

    p = _task_path(name)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"任务 [{name}] 不存在")

    from services._runtime import get_runtime
    rt = get_runtime()
    if rt is None:
        raise HTTPException(status_code=503, detail="Agent 尚未初始化")

    async def _run() -> None:
        from core.log import log
        try:
            result = await rt.mind.execute_task(name)
            log(f"Web 手动任务完成: {name} ({'有产出' if result else '无产出'})", tag="任务")
        except Exception as exc:
            log(f"Web 手动任务异常 [{name}]: {exc}", "WARNING", tag="任务")

    asyncio.create_task(_run(), name=f"agent.task.web_{name}")
    return {"status": "triggered", "task": name}


def _reload_task_registry() -> None:
    """热重载运行中的任务注册表。"""
    try:
        from services._runtime import get_runtime
        rt = get_runtime()
        if rt is not None:
            rt.mind.heartbeat_engine.task_registry.reload()
    except Exception as e:
        log(f"任务注册表热重载失败: {e}", "DEBUG")


# ──────────────────────────────────────────────────────────────────────────────
# Web 工具配置（entities/web/config.json）
# ──────────────────────────────────────────────────────────────────────────────


class WebToolsConfigUpdate(BaseModel):
    baidu_api_key: Optional[str] = None
    proxy: Optional[str] = None


@router.get("/web-tools")
async def get_web_tools_config() -> Dict[str, Any]:
    """返回 Web 工具配置（API Key 已脱敏）。"""
    from entities.web.baidu_search import get_config
    config = get_config()
    if config.get("baidu_api_key"):
        config["baidu_api_key"] = _mask_key(config["baidu_api_key"])
    return config


@router.put("/web-tools")
async def save_web_tools_config(data: WebToolsConfigUpdate) -> Dict[str, str]:
    """保存 Web 工具配置（代理、API Key 等）。"""
    from entities.web.baidu_search import update_config
    updates: Dict[str, Any] = {}
    if data.proxy is not None:
        updates["proxy"] = data.proxy
    if data.baidu_api_key is not None and "****" not in data.baidu_api_key:
        updates["baidu_api_key"] = data.baidu_api_key
    if not updates:
        return {"status": "ok", "message": "无变更"}
    update_config(updates)
    return {"status": "ok"}
