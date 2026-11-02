"""统一配置元数据 API — 数据驱动的配置中心。

GET  /api/config/meta        返回全部配置项元数据（按组组织，PASSWORD 类型值掩码）
PUT  /api/config/meta/{key}  保存单个配置项（热更生效，自动路由存储后端）
PUT  /api/config/meta/channels/{channel_id}  原子保存一个频道的配置草稿

设计要点：
- 配置项元数据来自 ConfigRegistry（各模块声明式注册，频道经 adapter/<id> 组接入）
- MindConfig 字段保存时路由到 save_mind_config（实时生效 + 持久化 + 同步 ConfigManager）
- 其余配置走 ConfigManager.set_persisted（落盘成功后通知消费方热更）
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


def _display_value(item: ConfigItem) -> Any:
    value = ConfigManager.get(item.key, item.default_value)
    return mask_secret(str(value)) if item.is_secret and value is not None else value


def _serialize_item(item: ConfigItem) -> Dict[str, Any]:
    """将 ConfigItem 序列化为前端可用的元数据（PASSWORD 类型值掩码）。"""
    environment = ConfigManager.environment_override(item.key)
    return {
        "key": item.key,
        "description": item.description or item.key,
        "type": item.type_name,
        "value": _display_value(item),
        "default": item.default_value,
        "editable": item.editable and environment is None,
        "environment_variable": environment,
        "value_source": "environment" if environment else "configured" if ConfigManager.has(item.key) else "default",
        "options": item.enum_options,
        "advanced": item.advanced,
        "min": item.min_value,
        "max": item.max_value,
        "step": item.step,
        "unit": item.unit,
        "tag": item.tag,
        "related_key": item.related_key,
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


def _validated_update(item: ConfigItem, raw: Any) -> Dict[str, Any]:
    """复用单项编辑权限与校验，掩码占位符不回写存储。"""
    key = item.key
    if not item.editable:
        raise HTTPException(403, f"配置项不可编辑: {key}")
    environment = ConfigManager.environment_override(key)
    if environment:
        raise HTTPException(409, f"配置由环境变量 {environment} 管理，请修改环境变量后重新加载")

    # PASSWORD 项提交当前掩码值 = 用户未改动，保留现值（掩码不可逆，无法回写）
    if item.is_secret and isinstance(raw, str) and raw and raw == _display_value(item):
        return {}

    try:
        value = item.clamp(item.coerce_value(raw))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    if item.type_name == "enum" and item.enum_options and value not in item.enum_options:
        raise HTTPException(400, f"配置项 {key} 的值必须是 {item.enum_options} 之一")
    return {key: value}


@router.put("/meta/channels/{channel_id}")
async def save_channel_draft(channel_id: str, values: Dict[str, Any]) -> Dict[str, Any]:
    """先验证整个频道草稿，再一次落盘和通知，防止新账号配上旧密钥。"""
    items = {item.key: item for item in ConfigRegistry.get_group_items(f"adapter/{channel_id}")}
    if not items:
        raise HTTPException(404, "频道配置不存在")
    patch: Dict[str, Any] = {}
    for key, raw in values.items():
        if key not in items:
            raise HTTPException(400, f"配置项不属于此频道: {key}")
        patch.update(_validated_update(items[key], raw))
    try:
        ConfigManager.set_persisted(patch)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except Exception as exc:
        raise server_error("保存频道配置", exc) from exc
    return {"values": {key: _display_value(items[key]) for key in values}}


@router.put("/meta/{key}")
async def save_config_meta(key: str, data: ConfigValueUpdate) -> Dict[str, Any]:
    """保存单个配置项（热更生效）。"""
    item = ConfigRegistry.get_item(key)
    if item is None:
        raise HTTPException(404, f"配置项不存在: {key}")
    patch = _validated_update(item, data.value)
    if not patch:
        return {"status": "ok", "key": key, "unchanged": True}

    if key in _mind_fields():
        # MindConfig 字段：走 save_mind_config 保证双轨同步 + 实时生效
        try:
            _config_svc.save_mind_value(key, patch[key])
        except Exception as exc:
            raise server_error("保存 Mind 配置", exc) from exc
    else:
        try:
            ConfigManager.set_persisted(patch)
        except Exception as exc:
            raise server_error("保存配置", exc) from exc

    return {"status": "ok", "key": key, "value": _display_value(item)}
