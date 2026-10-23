"""从存储卷声明构建数据库目录；不接管组件连接、迁移或恢复。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent.runtime.volumes import ensure_volume_modules
from core.database import DatabaseCapability, DatabaseComponent
from core.storage_volume import get_volume_registry
from services.db_common import DatabaseError


@dataclass(frozen=True)
class RegisteredDatabase:
    component: DatabaseComponent
    volume_id: str
    root: str
    needs_restart: bool

    @property
    def path(self) -> str:
        return str(Path(self.root) / self.component.relative_path)


def database_registry() -> dict[str, RegisteredDatabase]:
    """复用卷注册源，运行期优先呈现实际生效路径。"""
    ensure_volume_modules()
    registry = get_volume_registry()
    entries: dict[str, RegisteredDatabase] = {}
    for volume in registry.list():
        root = registry.active_path(volume.volume_id) or registry.resolve_path(volume.volume_id)
        for component in volume.databases:
            if component.database_id in entries:
                raise DatabaseError(f"重复数据库声明: {component.database_id}", status_code=500)
            entries[component.database_id] = RegisteredDatabase(
                component, volume.volume_id, root, registry.needs_restart(volume.volume_id),
            )
    return entries


def require_database(db_id: str, capability: DatabaseCapability) -> RegisteredDatabase:
    entry = database_registry().get(db_id)
    if entry is None:
        raise DatabaseError(f"未知数据库: {db_id}", status_code=404)
    if capability not in entry.component.capabilities:
        raise DatabaseError(
            f"数据库 {db_id} 不支持 {capability.value}；请通过所属模块或存储卷管理", status_code=403,
        )
    return entry
