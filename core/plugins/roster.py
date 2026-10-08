"""插件名册 — 已安装插件及其能力归属的紧凑清单，进 stable 工具块。

名册让模型每轮都能看到装了哪些插件、各提供了哪些技能/工具组/MCP server
（能力归属的单一呈现位：技能目录、MCP 目录、工具分组目录均不标注插件
来源）。需要插件没有的能力时先检索市场（search_plugins），而不是重造。

字节稳定（前缀缓存友好）：按安装时间排序，新插件只在尾部追加，既有行
不移动；注册表内容版本不变时名册文本字节不变（启停只翻转行尾「已禁用」
后缀，升级改变版本号属实质变更）。预算超限时降级为仅名称 + 省略计数，
任何库容下可注入。
"""
from __future__ import annotations

import threading
from typing import Dict, List, Tuple

from core.plugins.store import InstalledPlugin, PluginRegistry

_HEADER = (
    "[插件名册] 已安装插件与能力归属（组件随插件装卸增减，「已禁用」=组件已回收，"
    "toggle_plugin 可启用）。需要新能力先用 search_plugins 检索市场，"
    "详情与操作经 list_entity_methods(group=\"plugins\") 发现。"
)

_DESC_CHARS = 100

_cache_lock = threading.Lock()
# id(registry) → (registry 强引用, 内容版本, 名册文本)：不同实例（测试）互不串扰
_cache: Dict[int, Tuple[PluginRegistry, int, str]] = {}


def _roster_enabled() -> bool:
    from core.config import get_config_bool
    return get_config_bool("plugins_roster_enabled", True)


def _components_line(record: InstalledPlugin) -> str:
    """单插件的组件清单段：技能/工具组/MCP server（终名，与各目录可对上）。"""
    parts: List[str] = []
    if record.skills:
        parts.append(f"技能: {'、'.join(record.skills)}")
    if record.tools:
        parts.append(f"工具: {len(record.tools)} 个（组 plugin:{record.name}）")
    if record.mcp_servers:
        parts.append(f"MCP: {'、'.join(record.mcp_servers)}")
    return " | ".join(parts)


def roster_line(record: InstalledPlugin, desc_chars: int = _DESC_CHARS) -> str:
    """单条名册行内容（无列表前缀，供名册与装卸通知共用）：
    名称 版本（来源）: 描述 | 组件清单（desc_chars=0 时剥描述，保留组件归属）。"""
    origin = record.marketplace or record.source_type
    head = f"{record.name} v{record.version}（{origin}）"
    sections: List[str] = []
    if record.description and desc_chars > 0:
        desc = record.description.strip().replace("\n", " ")
        if len(desc) > desc_chars:
            desc = desc[:desc_chars - 1] + "…"
        sections.append(desc)
    components = _components_line(record)
    if components:
        sections.append(components)
    line = f"{head}: {' | '.join(sections)}" if sections else head
    if not record.enabled:
        line += " [已禁用]"
    return line


def _omission_line(count: int) -> str:
    return f"- …另有 {count} 个插件未列出（list_plugins 查看全部）"


def _render(records: List[InstalledPlugin]) -> str:
    """渲染名册正文：预算内保留完整行，超限降级为仅名称行。"""
    from core.config import get_config_int

    max_chars = get_config_int("plugins_roster_max_chars", 4000)

    def fits(lines: List[str]) -> bool:
        return len(_HEADER) + 1 + sum(len(line) + 1 for line in lines) <= max_chars

    ordered = sorted(records, key=lambda p: (p.installed_at or 0, p.name))
    full = [f"- {roster_line(p)}" for p in ordered]
    if fits(full):
        return "\n".join(full)

    names = [f"- {roster_line(p, desc_chars=0)}" for p in ordered]
    kept = len(names)
    while kept > 0 and not fits(names[:kept] + [_omission_line(len(names) - kept)]):
        kept -= 1
    return "\n".join(names[:kept] + [_omission_line(len(names) - kept)])


def roster_section(registry: PluginRegistry) -> str:
    """stable 工具块内的插件名册文本（未启用或未装插件时返回空串）。

    按注册表内容版本缓存：版本不变时文本字节不变。
    """
    if not _roster_enabled():
        return ""
    version = registry.version
    with _cache_lock:
        entry = _cache.get(id(registry))
        if entry and entry[0] is registry and entry[1] == version:
            return entry[2]
    records = registry.list_installed()
    text = f"{_HEADER}\n{_render(records)}" if records else ""
    with _cache_lock:
        _cache[id(registry)] = (registry, version, text)
    return text


def roster_factor(registry: PluginRegistry) -> str:
    """名册指纹因子（启用位 + 内容版本），供 stable 工具块指纹门控。"""
    return f"plugins-roster:{_roster_enabled()}:{registry.version}"


# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

from core.config import register_configs_safe  # noqa: E402

register_configs_safe({"plugins/roster": {
    "plugins_roster_enabled": {
        "description": "在稳定上下文注入已安装插件名册（能力归属的单一呈现位）",
        "default": True,
    },
    "plugins_roster_max_chars": {
        "description": "插件名册注入的最大字符数（超限降级为仅名称行 + 省略计数）",
        "default": 4000,
        "advanced": True,
        "unit": "字符",
    },
}})
