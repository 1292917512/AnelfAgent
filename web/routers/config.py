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
# 反思系统配置（introspection.json）
# ──────────────────────────────────────────────────────────────────────────────

_INTROSPECTION_CONFIG_PATH = Path(ConfigPaths.INTROSPECTION_CONFIG)


@router.get("/introspection")
async def get_introspection_config() -> Dict[str, Any]:
    """返回反思系统配置（config/introspection.json），屏蔽 last_reflect_time 等运行时字段。"""
    if not _INTROSPECTION_CONFIG_PATH.exists():
        return {}
    try:
        data: Dict[str, Any] = json.loads(_INTROSPECTION_CONFIG_PATH.read_text("utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取反思配置失败: {e}") from e
    # 过滤运行时字段，前端只需查看/编辑全局开关与时间参数
    return {k: v for k, v in data.items() if k != "units"}


class IntrospectionConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    reflect_min_hours: Optional[float] = None
    reflect_max_hours: Optional[float] = None
    analysis_temperature: Optional[float] = None
    min_conversations_for_analysis: Optional[int] = None


@router.put("/introspection")
async def save_introspection_config(data: IntrospectionConfigUpdate) -> Dict[str, str]:
    """保存反思系统全局参数到 config/introspection.json 并热重载到运行中的 Mind。"""
    if not _INTROSPECTION_CONFIG_PATH.exists():
        raise HTTPException(status_code=404, detail="introspection.json 不存在")
    try:
        existing: Dict[str, Any] = json.loads(_INTROSPECTION_CONFIG_PATH.read_text("utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取反思配置失败: {e}") from e

    updates = {k: v for k, v in data.model_dump().items() if v is not None}
    existing.update(updates)

    try:
        _INTROSPECTION_CONFIG_PATH.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入反思配置失败: {e}") from e

    # 热重载：使 Mind 内的 IntrospectionConfig 立即生效，无需重启
    try:
        from agent.introspection.config import reload_introspection_config
        new_cfg = reload_introspection_config()
        from services._runtime import get_runtime
        rt = get_runtime()
        if rt is not None:
            rt.mind.intro.config = new_cfg
    except Exception as e:
        log(f"反思配置热重载失败（不影响文件已写入）: {e}", "DEBUG")

    return {"status": "ok"}


# ──────────────────────────────────────────────────────────────────────────────
# 配置型反思单元 CRUD（config/introspection/*.json）
# ──────────────────────────────────────────────────────────────────────────────

_UNITS_DIR = Path(ConfigPaths.INTROSPECTION_DIR)

# 内置单元名称（不允许通过 API 修改/删除）
_BUILTIN_UNIT_NAMES = frozenset({"self_reflection", "entity_analysis", "memory_health"})


def _ensure_units_dir() -> None:
    _UNITS_DIR.mkdir(parents=True, exist_ok=True)


def _unit_path(name: str) -> Path:
    return _UNITS_DIR / f"{name}.json"


def _load_unit(name: str) -> Dict[str, Any]:
    p = _unit_path(name)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"单元 [{name}] 不存在")
    try:
        return json.loads(p.read_text("utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取单元配置失败: {e}") from e


def _reload_intro_units() -> None:
    """热重载运行中的 Introspection 配置型单元。"""
    try:
        from services._runtime import get_runtime
        rt = get_runtime()
        if rt is not None:
            rt.mind.intro.reload_config_units()
    except Exception as e:
        log(f"反思单元热重载失败: {e}", "DEBUG")


@router.get("/introspection/units")
async def list_introspection_units() -> List[Dict[str, Any]]:
    """列出所有配置型反思单元（config/introspection/*.json）。"""
    _ensure_units_dir()
    units: List[Dict[str, Any]] = []
    for json_file in sorted(_UNITS_DIR.glob("*.json")):
        try:
            data = json.loads(json_file.read_text("utf-8"))
            units.append(data)
        except Exception as e:
            log(f"反思单元配置解析失败 ({json_file.name}): {e}", "DEBUG")
    return units


@router.get("/introspection/units/{name}")
async def get_introspection_unit(name: str) -> Dict[str, Any]:
    """获取指定配置型反思单元。"""
    return _load_unit(name)


class IntrospectionUnitCreate(BaseModel):
    name: str
    display_name: str = ""
    description: str = ""
    scope: str = "global"
    enabled: bool = True
    memory_type: str = "reflection"
    importance: float = 0.5
    tags: List[str] = []
    source: str = ""
    null_keywords: List[str] = []
    prompt: str


@router.post("/introspection/units", status_code=201)
async def create_introspection_unit(data: IntrospectionUnitCreate) -> Dict[str, Any]:
    """创建新的配置型反思单元（写入 JSON 文件并热重载）。"""
    if data.name in _BUILTIN_UNIT_NAMES:
        raise HTTPException(status_code=400, detail=f"[{data.name}] 是内置单元，无法通过 API 创建")
    _ensure_units_dir()
    p = _unit_path(data.name)
    if p.exists():
        raise HTTPException(status_code=409, detail=f"单元 [{data.name}] 已存在")

    unit_data = data.model_dump()
    if not unit_data.get("source"):
        unit_data["source"] = data.name
    if not unit_data.get("display_name"):
        unit_data["display_name"] = data.name

    try:
        p.write_text(json.dumps(unit_data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入失败: {e}") from e

    _reload_intro_units()
    return unit_data


class IntrospectionUnitUpdate(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    scope: Optional[str] = None
    enabled: Optional[bool] = None
    memory_type: Optional[str] = None
    importance: Optional[float] = None
    tags: Optional[List[str]] = None
    source: Optional[str] = None
    null_keywords: Optional[List[str]] = None
    prompt: Optional[str] = None


@router.put("/introspection/units/{name}")
async def update_introspection_unit(name: str, data: IntrospectionUnitUpdate) -> Dict[str, Any]:
    """更新配置型反思单元并热重载。"""
    existing = _load_unit(name)
    updates = {k: v for k, v in data.model_dump().items() if v is not None}
    existing.update(updates)

    try:
        _unit_path(name).write_text(
            json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入失败: {e}") from e

    _reload_intro_units()
    return existing


@router.delete("/introspection/units/{name}")
async def delete_introspection_unit(name: str) -> Dict[str, str]:
    """删除配置型反思单元文件并热重载。"""
    if name in _BUILTIN_UNIT_NAMES:
        raise HTTPException(status_code=400, detail=f"[{name}] 是内置单元，不允许删除")
    p = _unit_path(name)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"单元 [{name}] 不存在")
    try:
        p.unlink()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除失败: {e}") from e

    _reload_intro_units()
    return {"status": "ok"}


@router.post("/introspection/trigger")
async def trigger_introspection() -> Dict[str, str]:
    """手动触发一次全局反思（跳过间隔限制），在后台异步执行。"""
    import asyncio

    from services._runtime import get_runtime
    rt = get_runtime()
    if rt is None:
        raise HTTPException(status_code=503, detail="Agent 尚未初始化，无法触发反思")

    async def _run() -> None:
        from core.log import log
        try:
            count = await rt.mind._execute_reflect(skip_interval=True)
            log(f"Web 手动反思完成: {count} 个单元有产出", tag="内省")
        except Exception as exc:
            log(f"Web 手动反思异常: {exc}", "WARNING", tag="内省")

    asyncio.create_task(_run(), name="agent.mind.web_manual_reflect")
    return {"status": "triggered"}


# ──────────────────────────────────────────────────────────────────────────────
# 任务单元 CRUD + 触发（config/tasks/*.json）
# ──────────────────────────────────────────────────────────────────────────────

_TASKS_DIR = Path(ConfigPaths.TASKS_DIR)


def _ensure_tasks_dir() -> None:
    _TASKS_DIR.mkdir(parents=True, exist_ok=True)


def _task_path(name: str) -> Path:
    return _TASKS_DIR / f"{name}.json"


def _load_task(name: str) -> Dict[str, Any]:
    p = _task_path(name)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"任务 [{name}] 不存在")
    try:
        return json.loads(p.read_text("utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取任务配置失败: {e}") from e


@router.get("/tasks")
async def list_tasks() -> List[Dict[str, Any]]:
    """列出所有任务单元（config/tasks/*.json）。"""
    _ensure_tasks_dir()
    tasks: List[Dict[str, Any]] = []
    for json_file in sorted(_TASKS_DIR.glob("*.json")):
        try:
            data = json.loads(json_file.read_text("utf-8"))
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


@router.post("/tasks", status_code=201)
async def create_task(data: TaskCreate) -> Dict[str, Any]:
    _ensure_tasks_dir()
    p = _task_path(data.name)
    if p.exists():
        raise HTTPException(status_code=409, detail=f"任务 [{data.name}] 已存在")

    task_data = data.model_dump()
    task_data["mode"] = "task"
    if not task_data.get("source"):
        task_data["source"] = data.name
    if not task_data.get("display_name"):
        task_data["display_name"] = data.name

    try:
        p.write_text(json.dumps(task_data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入失败: {e}") from e

    _reload_intro_units()
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


@router.put("/tasks/{name}")
async def update_task(name: str, data: TaskUpdate) -> Dict[str, Any]:
    existing = _load_task(name)
    updates = {k: v for k, v in data.model_dump().items() if v is not None}
    existing.update(updates)
    existing["mode"] = "task"

    try:
        _task_path(name).write_text(
            json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入失败: {e}") from e

    _reload_intro_units()
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

    _reload_intro_units()
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
            log(f"Web 手动任务完成: {name} ({'有产出' if result else '无产出'})", tag="内省")
        except Exception as exc:
            log(f"Web 手动任务异常 [{name}]: {exc}", "WARNING", tag="内省")

    asyncio.create_task(_run(), name=f"agent.mind.web_task_{name}")
    return {"status": "triggered", "task": name}
