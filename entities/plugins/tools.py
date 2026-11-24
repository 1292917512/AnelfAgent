"""插件管理工具（plugins 组）— AI 自主完成插件的安装/升级/移除与市场订阅。

支持 plugin.json 和 DeepSeek Harness bundle，发现来源包括 marketplace.json 和 GitHub Topic。
阻塞安装操作经 asyncio.to_thread 移出事件循环。

Model Experience:
    管理工具提供来源开关、分页结果及各源错误；DSH 宿主包返回独立 profile 的 DSH_HOME。
    仅调用时增加格式和操作结果字段的 token，空闲时不注入宿主配置。
    工具声明变动随工具集缓存更新，操作状态沿用动态区。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from core.log import log
from core.plugins import PluginManager, get_plugin_manager
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
    order=63,
    show_in_tool_catalog=False,
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
        "plugins_context_inject": {
            "description": "是否向 AI 上下文注入插件操作状态（进行中的安装/升级与最近失败）",
            "default": True,
        },
        "plugin_tools_sleep_default": {
            "description": "插件工具默认沉睡（不驻留完整 schema，需要时激活分组使用）",
            "default": True,
        },
    },
})

wire_plugin_manager(get_plugin_manager())


@context_provider(
    name="plugins", priority=45, max_tokens=200,
    group="plugins", inject_key="plugins_context_inject",
)
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


def _manager() -> PluginManager:
    manager = get_plugin_manager()
    wire_plugin_manager(manager)
    return manager


# ==================================================================
# 插件管理
# ==================================================================

@tool(name="list_plugins", group="plugins", tags=["always"], concurrency_safe=True)
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
            "format": p.format,
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
async def search_plugins(query: str = "", marketplace: str = "", offset: int = 0, limit: int = 20) -> str:
    """分页检索已订阅市场。count 为匹配总数，next_offset 为空表示已到末页。

    Args:
        query: 检索关键词，留空列出目录条目。
        marketplace: 已启用的市场标识名，留空查询全部启用来源。首次读取按需联网；errors 列出不可用来源。
        offset: 起始位置，下一页使用结果中的 next_offset。
        limit: 每页条目数，1 至 100，默认 20。
    """
    if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 100:
        return tool_error("offset 须为非负整数，limit 须在 1 至 100 之间", cause=ErrorCause.PARAM, retryable=False)
    try:
        return _safe_json(await asyncio.to_thread(_manager().search_page, query, marketplace, offset, limit))
    except Exception as exc:
        return error_from_exception(exc, action="检索插件目录")


@tool(name="install_plugin", group="plugins")
async def install_plugin(name: str, marketplace: str = "") -> str:
    """按检索结果的 name 和 marketplace 安装。返回的 name 是后续管理使用的标识，format 表示运行方式。

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
    """直接从 git URL 或本地路径安装插件，支持 plugin.json 和 DSH bundle。独立 MCP 直接注册；Cordis 包安装到返回的独立 DSH_HOME。

    Args:
        source: git 仓库地址（https://... / git@...）或本地插件目录路径
        ref: Git 分支/tag/提交（可选）
        subdir: 来源内插件子目录（monorepo 场景，可选）
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
        errors: dict[str, str] = {}
        results = await asyncio.to_thread(manager.upgrade_all, errors=errors)
        return _safe_json({"results": results, "errors": errors})
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
    """列出所有内置和自定义来源。enabled 为启用状态，pending 表示目录尚待首次联网加载。"""
    return _safe_json(_manager().list_marketplaces())


@tool(name="add_marketplace", group="plugins")
async def add_marketplace(name: str, source: str, ref: str = "") -> str:
    """订阅插件市场（marketplace.json 所在的 Git 仓库、本地目录或 GitHub Topic 地址）。

    Args:
        name: 市场标识名（小写字母/数字，可含 - _ .）
        source: 提供 marketplace.json 的 Git 仓库、本地目录，或 https://github.com/topics/dsh-plugin 等 Topic 地址
        ref: Git 分支/tag/提交（可选）
    """
    try:
        record = await asyncio.to_thread(
            _manager().add_marketplace, name.strip(), source.strip(), ref.strip(),
        )
        return _safe_json({"subscribed": True, **record.to_dict()})
    except Exception as e:
        return error_from_exception(e, action=f"订阅市场 {name}")


@tool(name="toggle_marketplace", group="plugins")
async def toggle_marketplace(name: str, enabled: bool) -> str:
    """启停市场来源，已安装插件保持可用。

    Args:
        name: list_marketplaces 返回的来源标识名。
        enabled: true 参与浏览、检索和新安装，false 暂停该来源。
    """
    try:
        state = coerce_bool_arg(enabled, True)
        record = await asyncio.to_thread(_manager().toggle_marketplace, name.strip(), state)
        return _safe_json({"name": record.name, "enabled": record.enabled})
    except Exception as exc:
        return error_from_exception(exc, action="切换市场来源")


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
    """刷新订阅目录。results 是各市场条目数，-1 表示失败，errors 按市场提供原因。

    Args:
        name: 市场标识名；留空刷新全部
    """
    try:
        errors: dict[str, str] = {}
        results = await asyncio.to_thread(_manager().refresh_marketplaces, name.strip(), errors=errors)
        return _safe_json({"results": results, "errors": errors})
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
