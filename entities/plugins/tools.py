"""插件管理工具（plugins 组）— AI 自主完成插件的安装/升级/移除与市场订阅。

插件 = 清单 + skills/ + .mcp.json + tools.py 的目录包；
市场 = marketplace.json 目录（本地路径或 git 仓库）。
所有阻塞操作（git 克隆、文件拷贝）经 asyncio.to_thread 移出事件循环。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from core.log import log
from core.plugins import get_plugin_manager
from core.sanitizer import is_sanitize_enabled, sanitize_text
from core.tool_errors import ErrorCause, error_from_exception, tool_error
from entities._sdk import (
    coerce_bool_arg,
    context_provider,
    entity,
    entity_manifest,
    tool,
)
from entities.plugins.activation import activate_installed_plugins, wire_plugin_manager

entity("plugins", "插件管理 - 插件安装/升级/移除与市场订阅")
entity_manifest(
    display_name="插件管理",
    icon="puzzle",
    description="插件包的安装、升级、移除与市场订阅管理",
    version="1.0.0",
    group="plugins",
)

from core.config import register_configs_safe  # noqa: E402

register_configs_safe({
    "entity/plugins": {
        "plugins_enabled": {
            "description": "是否在启动时自动激活已安装的插件",
            "default": True,
        },
        "plugin_tools_sleep_default": {
            "description": "插件工具默认沉睡（不驻留完整 schema，需要时激活分组使用）",
            "default": True,
        },
        "plugin_proxy": {
            "description": "插件 git 操作（市场订阅/安装/升级）使用的代理地址，如 http://127.0.0.1:7890；留空走系统环境",
            "default": "",
        },
    },
})

wire_plugin_manager(get_plugin_manager())


@context_provider(name="plugins", priority=30, max_tokens=200, group="plugins")
async def plugin_operation_status(scope: str):
    """插件操作状态：进行中的安装/升级/订阅与最近失败。

    仅在有进行中操作或窗口期内失败时返回一行文本，常态返回 None
    零占用；group 声明随实体启停联动。
    """
    import time

    from core.plugins.status import get_operation_board

    snap = get_operation_board().snapshot()
    if not snap["active"] and not snap["failures"]:
        return None
    parts = []
    if snap["active"]:
        ops = "、".join(
            f"{o['action']} {o['name']}（已进行 {int(time.time() - float(o['started_at']))}s）"
            for o in snap["active"]
        )
        parts.append(f"进行中: {ops}")
    if snap["failures"]:
        fails = "；".join(
            f"{f['action']} {f['name']} 失败（{f['error']}）"
            for f in snap["failures"][:3]
        )
        parts.append(f"最近失败: {fails}")
    return "[插件] " + " | ".join(parts)


def _safe_json(payload: Any) -> str:
    """序列化为 JSON 并脱敏（配置中可能含密钥类字段）。"""
    text = json.dumps(payload, ensure_ascii=False)
    return sanitize_text(text) if is_sanitize_enabled() else text


def _manager():
    manager = get_plugin_manager()
    wire_plugin_manager(manager)
    return manager


# ==================================================================
# 插件管理
# ==================================================================

@tool(name="list_plugins", group="plugins", concurrency_safe=True)
def list_plugins() -> str:
    """列出全部已安装插件（版本、来源市场、启用状态、携带的技能/工具/MCP server 数量）。"""
    records = _manager().list_plugins()
    return _safe_json([
        {
            "name": p.name,
            "version": p.version,
            "display_name": p.display_name,
            "description": p.description,
            "marketplace": p.marketplace,
            "enabled": p.enabled,
            "skills": p.skills,
            "tools": p.tools,
            "mcp_servers": p.mcp_servers,
        }
        for p in records
    ])


@tool(name="plugin_info", group="plugins", concurrency_safe=True)
def plugin_info(name: str) -> str:
    """查看单个已安装插件的完整信息（来源、安装时间、组件清单）。

    Args:
        name: 插件名
    """
    record = _manager().get_plugin(name.strip())
    if record is None:
        return tool_error(f"插件 '{name}' 未安装", cause=ErrorCause.NOT_FOUND, retryable=False)
    return _safe_json(record.to_dict())


@tool(name="search_plugins", group="plugins", concurrency_safe=True)
def search_plugins(query: str = "") -> str:
    """跨全部已订阅市场检索可安装的插件（名称/描述/分类匹配，空串列出全部）。

    Args:
        query: 检索关键词，留空列出全部市场插件
    """
    results = _manager().search(query)
    return _safe_json({"count": len(results), "plugins": results})


@tool(name="install_plugin", group="plugins")
async def install_plugin(name: str, marketplace: str = "") -> str:
    """从已订阅市场安装插件（技能入库、MCP server 合并、工具注册自动完成）。

    Args:
        name: 市场目录中的插件名
        marketplace: 指定来源市场名；留空时全市场唯一匹配
    """
    try:
        record = await asyncio.to_thread(_manager().install, name.strip(), marketplace.strip())
        return _safe_json({"installed": True, **record.to_dict()})
    except Exception as e:
        return error_from_exception(e, action=f"安装插件 {name}")


@tool(name="install_plugin_from_source", group="plugins")
async def install_plugin_from_source(source: str, ref: str = "", subdir: str = "") -> str:
    """直接从 git URL 或本地路径安装插件（不经市场）。

    Args:
        source: git 仓库地址（https://... / git@...）或本地插件目录路径
        ref: git 分支/tag（可选）
        subdir: 仓库内插件子目录（monorepo 场景，可选）
    """
    try:
        record = await asyncio.to_thread(
            _manager().install_from_source, source.strip(), ref.strip(), subdir.strip(),
        )
        return _safe_json({"installed": True, **record.to_dict()})
    except Exception as e:
        return error_from_exception(e, action=f"从来源安装插件 {source}")


@tool(name="remove_plugin", group="plugins")
async def remove_plugin(name: str) -> str:
    """移除插件：回收其技能/工具/MCP server，删除负载与注册记录。

    Args:
        name: 插件名
    """
    try:
        record = await asyncio.to_thread(_manager().remove, name.strip())
        return _safe_json({"removed": True, "name": record.name})
    except Exception as e:
        return error_from_exception(e, action=f"移除插件 {name}")


@tool(name="upgrade_plugins", group="plugins")
async def upgrade_plugins(name: str = "") -> str:
    """升级插件（重新拉取来源，有变更才替换并重激活）。

    Args:
        name: 插件名；留空升级全部已安装插件
    """
    manager = _manager()
    try:
        if name.strip():
            record, changed = await asyncio.to_thread(manager.upgrade, name.strip())
            return _safe_json({"name": record.name, "upgraded": changed, "version": record.version})
        results = await asyncio.to_thread(manager.upgrade_all)
        return _safe_json({"results": results})
    except Exception as e:
        return error_from_exception(e, action=f"升级插件 {name or '(全部)'}")


@tool(name="toggle_plugin", group="plugins")
async def toggle_plugin(name: str, enabled: bool = True) -> str:
    """启用/禁用插件（不卸载；禁用时回收其技能/工具/MCP server）。

    Args:
        name: 插件名
        enabled: true 启用 / false 禁用
    """
    try:
        record = await asyncio.to_thread(
            _manager().toggle, name.strip(), coerce_bool_arg(enabled, True),
        )
        return _safe_json({"name": record.name, "enabled": record.enabled})
    except Exception as e:
        return error_from_exception(e, action=f"切换插件状态 {name}")


# ==================================================================
# 市场订阅
# ==================================================================

@tool(name="list_marketplaces", group="plugins", concurrency_safe=True)
def list_marketplaces() -> str:
    """列出全部已订阅的插件市场（来源类型、地址、目录插件数）。"""
    return _safe_json(_manager().list_marketplaces())


@tool(name="add_marketplace", group="plugins")
async def add_marketplace(name: str, source: str, ref: str = "") -> str:
    """订阅插件市场（marketplace.json 所在的 git 仓库或本地目录）。

    Args:
        name: 市场标识名（小写字母/数字，可含 - _ .）
        source: git 仓库地址或本地目录路径（目录含 marketplace.json 或 .agents/plugins/marketplace.json）
        ref: git 分支/tag（可选）
    """
    try:
        record = await asyncio.to_thread(
            _manager().add_marketplace, name.strip(), source.strip(), ref.strip(),
        )
        return _safe_json({"subscribed": True, **record.to_dict()})
    except Exception as e:
        return error_from_exception(e, action=f"订阅市场 {name}")


@tool(name="remove_marketplace", group="plugins")
def remove_marketplace(name: str) -> str:
    """取消订阅市场（该市场下仍有已装插件时拒绝）。

    Args:
        name: 市场标识名
    """
    try:
        record = _manager().remove_marketplace(name.strip())
        return _safe_json({"removed": True, "name": record.name})
    except Exception as e:
        return error_from_exception(e, action=f"取消订阅市场 {name}")


@tool(name="refresh_marketplaces", group="plugins")
async def refresh_marketplaces(name: str = "") -> str:
    """刷新市场目录（git 市场执行 pull 拉取最新插件列表）。

    Args:
        name: 市场标识名；留空刷新全部
    """
    try:
        results = await asyncio.to_thread(_manager().refresh_marketplaces, name.strip())
        return _safe_json({"results": results})
    except Exception as e:
        return error_from_exception(e, action=f"刷新市场 {name or '(全部)'}")


# ==================================================================
# 启动激活（供 discover_entities 调用）
# ==================================================================

def activate_installed() -> int:
    """激活全部已安装插件（由实体发现流程在内置实体扫描后调用）。"""
    from core.config import get_config_bool
    if not get_config_bool("plugins_enabled", True):
        return 0
    try:
        return activate_installed_plugins()
    except Exception as e:
        log(f"插件启动激活失败: {e}", "ERROR", tag="插件")
        return 0
