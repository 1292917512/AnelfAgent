"""插件引擎 — 插件包（清单 + 技能 + MCP server + 工具模块）的解析、安装与编排。

模块划分：
- ``manifest``：插件清单（plugin.json）与市场目录（marketplace.json）的解析模型
- ``store``：已安装插件与已订阅市场的注册表持久化（plugins.json）
- ``sources``：插件负载获取（git 克隆 / 本地拷贝，暂存 + 原子替换）
- ``manager``：PluginManager 编排门面（安装/移除/升级/市场订阅/检索）

本包只依赖 core 内部设施；技能链接、MCP 合并、工具注册等运行时激活
由上层实体（entities/plugins）经钩子注入实现。
"""

from core.plugins.manager import PluginManager, get_plugin_manager
from core.plugins.manifest import (
    Marketplace,
    MarketplacePlugin,
    PluginManifest,
    find_manifest_file,
    parse_manifest,
    parse_marketplace,
)
from core.plugins.store import InstalledPlugin, MarketplaceSource, PluginRegistry

__all__ = [
    "InstalledPlugin",
    "Marketplace",
    "MarketplacePlugin",
    "MarketplaceSource",
    "PluginManager",
    "PluginManifest",
    "PluginRegistry",
    "find_manifest_file",
    "get_plugin_manager",
    "parse_manifest",
    "parse_marketplace",
]
