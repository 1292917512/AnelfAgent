"""task：独立任务系统 — 定义、注册与执行。"""

from .executor import TaskExecutor
from .history import clear_history, get_history, get_summary, record_execution
from .model import TaskDefinition, TaskResult, TaskScope
from .registry import TaskRegistry

__all__ = [
    "TaskDefinition",
    "TaskResult",
    "TaskScope",
    "TaskRegistry",
    "TaskExecutor",
    "record_execution",
    "get_history",
    "get_summary",
    "clear_history",
]
