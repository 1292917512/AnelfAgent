"""统一配置元数据 API — 数据驱动的配置中心。

GET  /api/config/meta        返回全部配置项元数据（按组组织，含当前值）
PUT  /api/config/meta/{key}  保存单个配置项（热更生效，自动路由存储后端）

设计要点：
- 配置项元数据来自 ConfigRegistry（各模块声明式注册）
- MindConfig 字段保存时路由到 save_mind_config（实时生效 + 持久化 + 同步 ConfigManager）
- 其余配置走 ConfigManager.set + save（ConfigManager 实时读取，天然热更）
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.config import ConfigManager, ConfigRegistry, ConfigValueType

router = APIRouter(prefix="/config", tags=["config"])

# MindConfig 字段集合（保存时路由到 save_mind_config 以保证双轨同步）
_MIND_FIELDS_CACHE: Optional[frozenset] = None


def _mind_fields() -> frozenset:
    global _MIND_FIELDS_CACHE
    if _MIND_FIELDS_CACHE is None:
        try:
            from agent.config import _MIND_SYNC_FIELDS
            _MIND_FIELDS_CACHE = frozenset((*_MIND_SYNC_FIELDS, "tool_system_rules"))
        except Exception:
            _MIND_FIELDS_CACHE = frozenset()
    return _MIND_FIELDS_CACHE


def _infer_type(item: Any, value: Any) -> str:
    """推断配置项的前端控件类型。"""
    vt = item.value_type
    vt_value = vt.value if isinstance(vt, ConfigValueType) else str(vt)
    if vt_value and vt_value != "auto":
        return vt_value
    if item.enum_options:
        return "enum"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    if isinstance(value, (list, dict)):
        return "json"
    return "string"


def _serialize_item(item: Any) -> Dict[str, Any]:
    """将 ConfigItem 序列化为前端可用的元数据。"""
    value = ConfigManager.get(item.key, item.default_value)
    return {
        "key": item.key,
        "description": item.description or item.key,
        "type": _infer_type(item, value),
        "value": value,
        "default": item.default_value,
        "editable": item.editable,
        "options": item.enum_options,
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


def _coerce_value(key: str, value: Any, expected_type: str) -> Any:
    """按声明类型校验并转换配置值，非法值抛 400。"""
    try:
        if expected_type == "boolean":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.lower() in ("true", "1", "yes")
            return bool(value)
        if expected_type == "integer":
            return int(value)
        if expected_type in ("float", "range"):
            return float(value)
        if expected_type == "enum":
            return str(value)
        if expected_type == "json":
            return value
        return str(value) if not isinstance(value, str) else value
    except (TypeError, ValueError):
        raise HTTPException(400, f"配置项 {key} 的值类型错误（期望 {expected_type}）")


@router.put("/meta/{key}")
async def save_config_meta(key: str, data: ConfigValueUpdate) -> Dict[str, Any]:
    """保存单个配置项（热更生效）。"""
    item = ConfigRegistry.get_item(key)
    if item is None:
        raise HTTPException(404, f"配置项不存在: {key}")
    if not item.editable:
        raise HTTPException(403, f"配置项不可编辑: {key}")

    expected_type = _infer_type(item, ConfigManager.get(key, item.default_value))
    value = _coerce_value(key, data.value, expected_type)
    if expected_type == "enum" and item.enum_options and value not in item.enum_options:
        raise HTTPException(400, f"配置项 {key} 的值必须是 {item.enum_options} 之一")

    if key in _mind_fields():
        # MindConfig 字段：走 save_mind_config 保证双轨同步 + 实时生效
        try:
            from agent.config import get_config_provider
            get_config_provider().save_mind_config(**{key: value})
        except Exception as exc:
            raise HTTPException(500, f"保存 Mind 配置失败: {exc}")
    else:
        ConfigManager.set(key, value)
        ConfigManager.save()

    return {"status": "ok", "key": key, "value": value}
