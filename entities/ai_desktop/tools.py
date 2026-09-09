"""AI 桌面管理工具 — AI 侧的组件综合信息查看与配置管理。"""

from __future__ import annotations

import json
from typing import Union

from core.config import ConfigRegistry
from core.tool_errors import ErrorCause
from entities._sdk import save_config_value, tool, tool_error

from . import framework
from .modules.weather import parse_locations, search_locations


@tool(name="ai_desktop_modules", group="ai_desktop", concurrency_safe=True)
def ai_desktop_modules(
    action: str = "list", module: str = "", name: str = "", value: str = "",
) -> str:
    """管理 AI 桌面上下文组件：list 查看组件综合信息（启停状态/配置项/当前注入文本/实时详情），preview 预览整体注入内容，enable/disable 启停组件，set_config 修改组件配置（如时区），search_location/add_location/remove_location 管理天气地区（地区经检索确认落库，保证不错配城市）。

    Args:
        action: 操作类型：list（默认）/ preview / enable / disable / set_config / search_location / add_location / remove_location
        module: 组件 key（enable/disable/set_config 时必填，如 datetime、weather）
        name: set_config 时为配置项短名（如 timezone）；add_location/remove_location 时为地区名
        value: 配置值（set_config 时必填；列表类配置传 JSON 字符串）
    """
    action = action.strip().lower()

    if action == "list":
        modules = [m.describe() for m in framework.all_modules()]
        return json.dumps({"modules": modules, "count": len(modules)},
                          ensure_ascii=False, default=str)

    if action == "preview":
        content = framework.render_context()
        return json.dumps(
            {"injecting": bool(content), "content": content or None},
            ensure_ascii=False,
        )

    if action in ("enable", "disable"):
        target = _resolve_module(module)
        if isinstance(target, str):
            return target
        enabled = action == "enable"
        save_config_value(target.enabled_key, enabled)
        return json.dumps(
            {"success": True, "module": target.key, "enabled": enabled},
            ensure_ascii=False,
        )

    if action == "set_config":
        target = _resolve_module(module)
        if isinstance(target, str):
            return target
        config_name = name.strip()
        if not config_name or config_name not in target.config_schema:
            known = list(target.config_schema.keys())
            return tool_error(
                f"组件 {target.key} 没有配置项: {config_name or '(未提供)'}"
                f"（可用: {', '.join(known)}）",
                cause=ErrorCause.PARAM,
            )
        # 经统一配置架构的类型矫正与边界收敛（与 Web/配置中心同纪律），
        # 避免 "true" 这类字符串裸写进布尔配置
        full_key = target.full_config_key(config_name)
        item = ConfigRegistry.get_item(full_key)
        coerced: object = value
        if item is not None:
            try:
                coerced = item.clamp(item.coerce_value(value))
            except ValueError as exc:
                return tool_error(str(exc), cause=ErrorCause.PARAM)
        save_config_value(full_key, coerced)
        return json.dumps(
            {"success": True, "module": target.key, "name": config_name,
             "value": coerced},
            ensure_ascii=False, default=str,
        )

    if action == "search_location":
        return _search_location(name or value)

    if action == "add_location":
        return _add_location(name)

    if action == "remove_location":
        return _remove_location(name)

    return tool_error(
        f"未知操作: {action}（可用: list / preview / enable / disable / "
        f"set_config / search_location / add_location / remove_location）",
        cause=ErrorCause.PARAM,
    )


def _resolve_module(key: str) -> Union["framework.DesktopModule", str]:
    """解析组件 key，失败返回 tool_error 文本。"""
    if not key.strip():
        return tool_error("需要提供 module 参数", cause=ErrorCause.PARAM)
    target = framework.get_module(key.strip())
    if target is None:
        known = [m.key for m in framework.all_modules()]
        return tool_error(
            f"未找到组件: {key}（可用: {', '.join(known)}）",
            cause=ErrorCause.NOT_FOUND,
        )
    return target


def _weather_locations_key() -> str:
    """天气组件地区列表的完整配置键。"""
    module = framework.get_module("weather")
    assert module is not None
    return module.full_config_key("locations")


def _load_locations() -> list:
    """读取当前天气地区配置。"""
    from core.config import ConfigManager
    return parse_locations(ConfigManager.get(_weather_locations_key(), ""))


def _save_locations(locations: list) -> None:
    """写回天气地区配置（触发配置监听器即时重采集）。"""
    save_config_value(_weather_locations_key(),
                      json.dumps(locations, ensure_ascii=False))


def _search_location(query: str) -> str:
    """检索地区候选（同步网络调用，经工具线程池执行）。"""
    query = query.strip()
    if not query:
        return tool_error("search_location 需要提供 name 参数（城市名）",
                          cause=ErrorCause.PARAM)
    try:
        candidates = search_locations(query)
    except Exception as exc:
        return tool_error(f"地区检索失败: {exc}", cause=ErrorCause.NETWORK)
    if not candidates:
        return tool_error(f"未找到地区: {query}（请换更精确的城市名）",
                          cause=ErrorCause.NOT_FOUND)
    return json.dumps({"candidates": candidates}, ensure_ascii=False)


def _add_location(name: str) -> str:
    """按城市名检索确认并添加地区（带坐标落库，杜绝错配）。"""
    name = name.strip()
    if not name:
        return tool_error("add_location 需要提供 name 参数（城市名）",
                          cause=ErrorCause.PARAM)
    locations = _load_locations()
    if any(loc["name"] == name for loc in locations):
        return tool_error(f"地区已存在: {name}", cause=ErrorCause.PARAM)
    try:
        candidates = search_locations(name, count=5)
    except Exception as exc:
        return tool_error(f"地区检索失败: {exc}", cause=ErrorCause.NETWORK)
    if not candidates:
        return tool_error(f"未找到地区: {name}（请换更精确的城市名）",
                          cause=ErrorCause.NOT_FOUND)
    top = candidates[0]
    locations.append({
        "name": top["name"],
        "label": top["label"],
        "latitude": top["latitude"],
        "longitude": top["longitude"],
        "enabled": True,
    })
    _save_locations(locations)
    return json.dumps({
        "success": True,
        "added": locations[-1],
        "alternatives": [c["label"] for c in candidates[1:]],
    }, ensure_ascii=False)


def _remove_location(name: str) -> str:
    """按名称移除地区。"""
    name = name.strip()
    locations = _load_locations()
    remaining = [loc for loc in locations if loc["name"] != name]
    if len(remaining) == len(locations):
        known = [loc["name"] for loc in locations]
        return tool_error(
            f"地区不存在: {name or '(未提供)'}（当前: {', '.join(known) or '空'}）",
            cause=ErrorCause.NOT_FOUND,
        )
    _save_locations(remaining)
    return json.dumps({"success": True, "removed": name}, ensure_ascii=False)

