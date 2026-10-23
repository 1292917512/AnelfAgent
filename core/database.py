"""数据库管理契约：卷内组件、可用操作和只读观测，不持有引擎连接。

卷负责备份/恢复的一致性边界；组件负责声明展示与访问能力。
所属模块提供轻量观测，数据库服务不得为探测组件而自行打开原生引擎。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum


class DatabaseCapability(str, Enum):
    """通用管理端可执行的操作，由所属模块显式开放。"""

    BROWSE = "browse"
    QUERY = "query"
    EDIT = "edit"
    HEALTH = "health"
    BACKUP = "backup"
    OPTIMIZE = "optimize"


@dataclass(frozen=True)
class DatabaseObservation:
    """观测值；未测量用 None，容量观测不代表引擎完整性验收。"""

    size_bytes: int | None = None
    measured_at: float | None = None


@dataclass(frozen=True)
class DatabaseComponent:
    """卷内数据库声明；相对路径由卷根派生，不独立指派或恢复。"""

    database_id: str
    name: str
    description: str
    engine: str
    relative_path: str = ""
    capabilities: frozenset[DatabaseCapability] = frozenset()
    managed_by: str = ""
    management_url: str = ""
    observe: Callable[[str], Awaitable[DatabaseObservation]] | None = None
