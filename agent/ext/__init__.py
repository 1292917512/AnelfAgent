"""ext：扩展层（FastAPI 路由注册 + 插件基类 + 配置提供器兼容导出）。"""

from .router import ExtRouter
from .plugin_base import PluginBase
from agent.core.config import BotConfigProvider, get_config_provider

__all__ = ["ExtRouter", "PluginBase", "BotConfigProvider", "get_config_provider"]
