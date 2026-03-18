"""task：独立任务系统 — 定义、注册与执行。"""

from .model import TaskDefinition, TaskResult, TaskScope
from .registry import TaskRegistry
from .executor import TaskExecutor

__all__ = [
    "TaskDefinition",
    "TaskResult",
    "TaskScope",
    "TaskRegistry",
    "TaskExecutor",
]
