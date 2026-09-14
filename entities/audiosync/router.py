"""音源同步实体的 HTTP 路由（自动挂载到 /api/entity/audiosync）。

- /ingest：外部音源推送（X-Ingest-Token 鉴权，路径在 web/server.py
  _AUTH_EXEMPT 白名单，由本端点自行校验令牌；未配置 audiosync_ingest_token
  时 fail-closed）——解析结果经核心入库管线写入音频核心库
- /sync/*：目录镜像同步的手动触发 / 状态 / 预览 / 重建
- /source/*：同步来源组件清单与连通性体检
- /config：实体配置读写（entity/audiosync 组）

说话人/片段/识别等音频库管理面在核心路由 /api/audio（音频页签）。
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Body, HTTPException, Request

from core.config import ConfigManager, ConfigRegistry, get_config
from core.log import log
from entities._sdk import IngestPayload, audio_ingest_payload

from .framework import active_source, all_sources
from .outbound import notify_ingested

_LOG_TAG = "音源同步"


def _verify_ingest_token(request: Request) -> None:
    """校验上游推送令牌；未配置令牌时 ingest 关闭（fail-closed）。"""
    token = str(get_config("audiosync_ingest_token", "") or "").strip()
    if not token:
        raise HTTPException(
            status_code=503,
            detail="ingest 未启用：请在实体配置中设置 audiosync_ingest_token")
    provided = request.headers.get("x-ingest-token", "")
    if provided != token:
        raise HTTPException(status_code=401, detail="ingest token 无效")


def build_router() -> APIRouter:
    router = APIRouter()

    # ── 实体配置 ──────────────────────────────────────────────────

    @router.get("/config")
    async def get_entity_config() -> Dict[str, Any]:
        """读取 entity/audiosync 分组的配置项与当前值。"""
        items = []
        for item in ConfigRegistry.get_group_items("entity/audiosync"):
            items.append({
                "key": item.key,
                "description": item.description,
                "value_type": item.value_type.value
                if hasattr(item.value_type, "value") else str(item.value_type),
                "default_value": item.default_value,
                "current_value": ConfigManager.get(item.key, item.default_value),
            })
        return {"items": items}

    @router.put("/config")
    async def update_entity_config(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        """批量更新配置（仅接受 entity/audiosync 分组内已注册的键）。"""
        updates = payload.get("updates")
        if not isinstance(updates, dict):
            raise HTTPException(status_code=400, detail="updates 必须是对象")
        valid_keys = {item.key for item in ConfigRegistry.get_group_items("entity/audiosync")}
        count = 0
        for key, value in updates.items():
            if key not in valid_keys:
                continue
            ConfigManager.set(key, value)
            count += 1
        if count:
            ConfigManager.save()
            log(f"音源同步配置已更新: {sorted(k for k in updates if k in valid_keys)}",
                tag=_LOG_TAG)
        return {"updated": count}

    # ── 外部音源推送 ──────────────────────────────────────────────

    @router.post("/ingest")
    async def ingest(request: Request, payload: IngestPayload) -> Dict[str, Any]:
        """接收外部音源（上游 pipeline）推送的结构化语音片段。"""
        _verify_ingest_token(request)
        result = await audio_ingest_payload(payload)
        notify_ingested(payload, result)
        log(f"ingest 入库 {result.ingested} 段 [{payload.source_file}]", tag=_LOG_TAG)
        return result.model_dump()

    # ── 目录自动同步 ──────────────────────────────────────────────

    @router.post("/sync")
    async def sync_now() -> Dict[str, Any]:
        """手动触发一轮目录增量同步。"""
        from .watcher import get_audiosync_watcher
        watcher = get_audiosync_watcher()
        result = await watcher.sync_now()
        result["status"] = watcher.status()
        return result

    @router.get("/sync/status")
    async def sync_status() -> Dict[str, Any]:
        """目录同步状态（来源/最近扫描/最近结果/错误）。"""
        from .watcher import get_audiosync_watcher
        return get_audiosync_watcher().status()

    @router.get("/sync/preview")
    async def sync_preview() -> Dict[str, Any]:
        """待同步预览：来源与登记表 diff（只读不处理），含待同步单元清单。"""
        from .watcher import get_audiosync_watcher
        return await get_audiosync_watcher().preview()

    @router.post("/sync/rebuild")
    async def rebuild_recordings(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        """删除重建：指定录制单元（paths 数组）清理本地资源并立即重新入库。"""
        paths = payload.get("paths")
        if not isinstance(paths, list) or not paths:
            raise HTTPException(status_code=400, detail="paths 须为非空数组")
        from .watcher import get_audiosync_watcher
        return await get_audiosync_watcher().rebuild([str(p) for p in paths[:50]])

    # ── 同步来源组件 ──────────────────────────────────────────────

    @router.get("/source/list")
    async def source_list() -> Dict[str, Any]:
        """同步来源组件清单（注册的全部来源 + 配置就绪状态 + 当前生效者）。"""
        active = active_source()
        return {
            "sources": [{
                "key": s.key,
                "display_name": s.display_name,
                "priority": s.priority,
                "configured": s.is_configured(),
                "active": active is s,
            } for s in all_sources()],
        }

    @router.get("/source/status")
    async def source_status() -> Dict[str, Any]:
        """当前生效来源的连通性体检（配置/可达/延迟）。"""
        source = active_source()
        if source is None:
            return {"source": "", "configured": False, "reachable": False,
                    "latency_ms": 0, "error": "未配置同步来源"}
        return {"source": source.desc(), **await source.check_status()}

    return router
