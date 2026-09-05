"""统一配置元数据 API — 数据驱动的配置中心。

GET  /api/config/meta        返回全部配置项元数据（按组组织，PASSWORD 类型值掩码）
PUT  /api/config/meta/{key}  保存单个配置项（热更生效，自动路由存储后端）

设计要点：
- 配置项元数据来自 ConfigRegistry（各模块声明式注册，频道经 adapter/<id> 组接入）
- MindConfig 字段保存时路由到 save_mind_config（实时生效 + 持久化 + 同步 ConfigManager）
- 其余配置走 ConfigManager.set + save（变更监听驱动频道等消费方即时热更）
- PASSWORD 类型：GET 掩码返回；PUT 提交掩码占位符时保留现值（留空则清空）
"""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.config import ConfigItem, ConfigManager, ConfigRegistry, mask_secret
from services import ConfigService
from web.routers._errors import server_error

router = APIRouter(prefix="/config", tags=["config"])

_config_svc = ConfigService()


def _mind_fields() -> frozenset:
    """MindConfig 字段集合（保存时路由到 save_mind_config 以保证双轨同步）。"""
    return _config_svc.mind_fields()


def _serialize_item(item: ConfigItem) -> Dict[str, Any]:
    """将 ConfigItem 序列化为前端可用的元数据（PASSWORD 类型值掩码）。"""
    value = ConfigManager.get(item.key, item.default_value)
    if item.is_secret and isinstance(value, str) and value:
        value = mask_secret(value)
    return {
        "key": item.key,
        "description": item.description or item.key,
        "type": item.type_name,
        "value": value,
        "default": item.default_value,
        "editable": item.editable,
        "options": item.enum_options,
        "advanced": item.advanced,
        "min": item.min_value,
        "max": item.max_value,
        "step": item.step,
        "unit": item.unit,
        "tag": item.tag,
        "source": "mind" if item.key in _mind_fields() else "config_manager",
    }


@router.get("/meta")
async def get_config_meta() -> Dict[str, Any]:
    """返回全部配置项元数据（按组组织）。"""
    groups: List[Dict[str, Any]] = []
    for group_name, items in ConfigRegistry.get_grouped_items().items():
        serialized = [_serialize_item(item) for item in items]
        if serialized:
            groups.append({"group": group_name, "items": serialized})
    return {"groups": groups}


class ConfigValueUpdate(BaseModel):
    value: Any


@router.put("/meta/{key}")
async def save_config_meta(key: str, data: ConfigValueUpdate) -> Dict[str, Any]:
    """保存单个配置项（热更生效）。"""
    item = ConfigRegistry.get_item(key)
    if item is None:
        raise HTTPException(404, f"配置项不存在: {key}")
    if not item.editable:
        raise HTTPException(403, f"配置项不可编辑: {key}")

    # PASSWORD 项提交当前掩码值 = 用户未改动，保留现值（掩码不可逆，无法回写）
    if item.is_secret and isinstance(data.value, str):
        current = ConfigManager.get(key, item.default_value)
        if isinstance(current, str) and current and data.value == mask_secret(current):
            return {"status": "ok", "key": key, "unchanged": True}

    try:
        value = item.clamp(item.coerce_value(data.value))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    if item.type_name == "enum" and item.enum_options and value not in item.enum_options:
        raise HTTPException(400, f"配置项 {key} 的值必须是 {item.enum_options} 之一")

    if key in _mind_fields():
        # MindConfig 字段：走 save_mind_config 保证双轨同步 + 实时生效
        try:
            _config_svc.save_mind_value(key, value)
        except Exception as exc:
            raise server_error("保存 Mind 配置", exc) from exc
    else:
        ConfigManager.set(key, value)
        ConfigManager.save()

    return {"status": "ok", "key": key, "value": value}
