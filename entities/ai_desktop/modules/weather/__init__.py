"""天气组件包 — 导入即注册，并对工具/路由重导出地区检索与配置解析服务。"""

from .geocode import search_locations
from .locations import parse_locations
from .module import WeatherModule

__all__ = ["WeatherModule", "parse_locations", "search_locations"]
