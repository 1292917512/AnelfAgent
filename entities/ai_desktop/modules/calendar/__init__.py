"""日历日程组件包 — 导入即注册组件与 AI 工具。"""

from . import tools  # noqa: F401  # 触发 @tool 注册
from .module import CalendarModule

__all__ = ["CalendarModule"]
