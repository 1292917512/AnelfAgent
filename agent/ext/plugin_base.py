"""PluginBase：外部插件基类。

提供工具注册、提示词注入、事件订阅、插件配置的统一接口。
工具注册直接走 EntityRegistry，卸载时通过 owner 标识自动清理。

用法::

    from agent.ext import PluginBase

    class WeatherPlugin(PluginBase):
        name = "weather"
        description = "天气查询插件"

        async def on_load(self):
            self.register_tool("get_weather", self.get_weather, description="查询天气")

        async def get_weather(self, city: str) -> str:
            return f"{city} 晴 25C"
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from core.log import log


class PluginBase:
    """插件基类。"""

    name: str = "unnamed_plugin"
    description: str = ""
    version: str = "0.1.0"
    author: str = ""
    enabled: bool = True
    subscriptions: List[str] = []

    def __init__(self) -> None:
        self._tools: Dict[str, str] = {}
        self._prompts: List[str] = []

    @property
    def _owner_key(self) -> str:
        return f"plugin:{self.name}"

    # 生命周期
    async def on_load(self) -> None:
        """插件加载时调用，子类重写注册工具/提示词。"""

    async def on_unload(self) -> None:
        """插件卸载时调用。"""

    async def on_message(self, payload: Dict[str, Any]) -> None:
        """消息到达回调（需在 subscriptions 中包含 'message_received'）。"""

    def inject_prompts(self) -> List[str]:
        """返回需注入到系统提示词的额外内容。"""
        return list(self._prompts)

    # 工具注册
    def register_tool(self, name: str, func: Callable, *, description: str = "", parameters: Optional[List[Dict[str, Any]]] = None) -> None:
        from entities._sdk import _extract_params
        from core.entity import EntityRegistry, ToolParam

        params = [ToolParam(name=p.get("name", ""), description=p.get("description", ""), type=p.get("type", "string"), required=p.get("required", True)) for p in parameters] if parameters else _extract_params(func)
        EntityRegistry.register_tool(name=name, func=func, description=description or name, params=params, source="plugin")
        self._tools[name] = name

    # 提示词
    def add_prompt(self, prompt: str) -> None:
        self._prompts.append(prompt)

    # 信息
    def info(self) -> Dict[str, Any]:
        return {"name": self.name, "description": self.description, "version": self.version, "author": self.author, "enabled": self.enabled, "tools": list(self._tools.keys())}


# ── 插件管理器 ────────────────────────────────────────────────────────

_plugins: Dict[str, PluginBase] = {}


async def load_plugin(plugin: PluginBase) -> None:
    """加载并注册一个插件。"""
    if not plugin.enabled:
        return
    await plugin.on_load()
    _plugins[plugin.name] = plugin

    try:
        from core.entity import EntityMetadata, EntityRegistry, EntityType
        EntityRegistry.register(EntityMetadata(
            name=f"plugin:{plugin.name}", entity_type=EntityType.PLUGIN,
            description=plugin.description, enabled=True, group="plugin",
            source="external", instance=plugin,
            meta={"version": plugin.version, "tools": list(plugin._tools.keys())},
        ))
    except Exception as e:
        log(f"插件实体注册失败 ({plugin.name}): {e}", "DEBUG")

    if plugin.subscriptions:
        try:
            from core.event_bus import event_bus
            for event_name in plugin.subscriptions:
                handler = getattr(plugin, f"on_{event_name}", None)
                if handler is None and event_name == "message_received":
                    handler = plugin.on_message
                if handler is not None:
                    event_bus.on(event_name, handler, owner=plugin._owner_key)
        except ImportError:
            pass

    log(f"插件已加载: {plugin.name} v{plugin.version}")


async def unload_plugin(name: str) -> None:
    """卸载一个插件。"""
    plugin = _plugins.pop(name, None)
    if not plugin:
        return
    try:
        from core.event_bus import event_bus
        event_bus.off_by_owner(plugin._owner_key)
    except ImportError:
        pass
    try:
        from core.entity import EntityRegistry
        for tool_name in plugin._tools:
            EntityRegistry.unregister(tool_name)
        EntityRegistry.unregister(f"plugin:{name}")
    except (KeyError, ValueError):
        pass
    await plugin.on_unload()
    log(f"插件已卸载: {name}")


def get_all_plugins() -> Dict[str, PluginBase]:
    return dict(_plugins)


def get_plugin(name: str) -> Optional[PluginBase]:
    return _plugins.get(name)


def get_all_prompts() -> List[str]:
    """收集所有已加载插件的提示词。"""
    prompts: List[str] = []
    for plugin in _plugins.values():
        prompts.extend(plugin.inject_prompts())
    return prompts
