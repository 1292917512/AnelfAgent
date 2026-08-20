"""存储卷注册表 — 数据平面模块化管理的基础抽象。

存储卷（Volume）是所有持久化数据的统一登记单元：每个 SQLite 库 /
cognee 数据树 / 便签树在所属模块 import 时自注册一条 VolumeDescriptor。
注册表只负责事实（谁在哪、能力是什么、指派是什么），管理操作
（备份/恢复/迁移/SQL 导出）在 services 层消费本注册表。

路径解析优先级（每卷独立）：环境变量覆盖（声明者）> 位置指派
（``config/storage_volumes.json`` 或卷自定义读写钩子）> 模块默认派生。
无指派文件时所有卷落在既有默认路径，数据位置零变化。
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional

from core.log import log


class VolumeKind(str, Enum):
    """存储卷的物理形态。"""

    SQLITE = "sqlite"
    COGNEE_TREE = "cognee_tree"
    NOTES_TREE = "notes_tree"


class VolumeCapability(str, Enum):
    """存储卷支持的管理操作。"""

    BACKUP = "backup"
    RESTORE = "restore"
    RELOCATE = "relocate"
    EXPORT_SQL = "export_sql"
    IMPORT_SQL = "import_sql"


def kind_capabilities(kind: VolumeKind) -> frozenset[VolumeCapability]:
    """按卷形态返回能力集合。"""
    if kind is VolumeKind.SQLITE:
        return frozenset(VolumeCapability)
    if kind is VolumeKind.COGNEE_TREE:
        return frozenset(
            {VolumeCapability.BACKUP, VolumeCapability.RESTORE, VolumeCapability.RELOCATE}
        )
    return frozenset({VolumeCapability.BACKUP, VolumeCapability.RESTORE})


@dataclass(frozen=True)
class VolumeLocation:
    """卷的位置指派（backend 预留运行时外置扩展位，本期仅 local）。"""

    backend: str = "local"
    path: str = ""


@dataclass(frozen=True)
class VolumeDescriptor:
    """存储卷描述符：事实声明，不含任何管理逻辑。"""

    volume_id: str
    name: str
    description: str
    kind: VolumeKind
    # 默认路径惰性求值（读取时才解析，保持测试 tmp_path 隔离）
    default_path: Callable[[], str]
    # 环境变量覆盖（声明即最高优先级，如 agent 卷的 ANELF_BOT_SQLITE_PATH）
    env_override: str = ""
    # 位置指派读写钩子：缺省走中央 storage_volumes.json；
    # cognee 卷转发 cognee.json 的 data_root（单一权威）
    location_reader: Optional[Callable[[], Optional[VolumeLocation]]] = None
    location_writer: Optional[Callable[[Optional[str]], None]] = None
    capabilities: frozenset[VolumeCapability] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not self.capabilities:
            object.__setattr__(self, "capabilities", kind_capabilities(self.kind))


class VolumeRegistry:
    """存储卷注册表：登记 / 路径解析 / 位置指派 / 生效状态观测。"""

    def __init__(self, assignment_path: Optional[str] = None) -> None:
        self._volumes: Dict[str, VolumeDescriptor] = {}
        # 运行期已生效路径（各存储构造/启动时 mark_active），用于 needs_restart 判定
        self._active_paths: Dict[str, str] = {}
        self._file_lock = threading.Lock()
        self._assignment_path = assignment_path or _default_assignment_path()

    # ---------------- 登记 ----------------

    def register(self, descriptor: VolumeDescriptor) -> None:
        """登记卷描述符（幂等，重复登记以后者为准）。"""
        self._volumes[descriptor.volume_id] = descriptor

    def get(self, volume_id: str) -> VolumeDescriptor:
        if volume_id not in self._volumes:
            raise KeyError(f"未注册的存储卷: {volume_id}")
        return self._volumes[volume_id]

    def list(self) -> List[VolumeDescriptor]:
        return list(self._volumes.values())

    # ---------------- 路径解析 ----------------

    def resolve_path(self, volume_id: str) -> str:
        """解析卷当前应落位的路径：env_override > 指派 > 默认派生。"""
        desc = self.get(volume_id)
        if desc.env_override:
            env_path = os.getenv(desc.env_override, "").strip()
            if env_path:
                return env_path
        location = self.read_location(volume_id)
        if location is not None and location.path:
            return location.path
        return desc.default_path()

    def location_source(self, volume_id: str) -> str:
        """路径来源：env / assignment / default。"""
        desc = self.get(volume_id)
        if desc.env_override:
            env_path = os.getenv(desc.env_override, "").strip()
            if env_path:
                return "env"
        location = self.read_location(volume_id)
        if location is not None and location.path:
            return "assignment"
        return "default"

    # ---------------- 位置指派（中央 storage_volumes.json 或卷自定义钩子） ----------------

    def read_location(self, volume_id: str) -> Optional[VolumeLocation]:
        desc = self.get(volume_id)
        if desc.location_reader is not None:
            try:
                return desc.location_reader()
            except Exception as exc:
                log(f"卷 {volume_id} 位置指派读取失败（按默认处理）: {exc}", "WARNING")
                return None
        raw = self._load_assignments().get(volume_id)
        if not isinstance(raw, dict):
            return None
        path = str(raw.get("path", "") or "").strip()
        backend = str(raw.get("backend", "local") or "local").strip() or "local"
        if not path:
            return None
        return VolumeLocation(backend=backend, path=path)

    def write_location(self, volume_id: str, path: str) -> None:
        """写入位置指派（path 为空串则清除指派、回到默认派生）。"""
        desc = self.get(volume_id)
        if desc.location_writer is not None:
            desc.location_writer(path.strip() or None)
            return
        assignments = self._load_assignments()
        if path.strip():
            assignments[volume_id] = {"backend": "local", "path": path.strip()}
        else:
            assignments.pop(volume_id, None)
        self._save_assignments(assignments)

    def _load_assignments(self) -> Dict[str, dict]:
        with self._file_lock:
            path = Path(self._assignment_path)
            if not path.is_absolute():
                path = _project_root() / path
            if not path.is_file():
                return {}
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                items = raw.get("volumes", {})
                return {k: v for k, v in items.items() if isinstance(v, dict)} if isinstance(items, dict) else {}
            except (OSError, ValueError) as exc:
                log(f"存储卷指派文件加载失败（按无指派处理）: {exc}", "WARNING")
                return {}

    def _save_assignments(self, assignments: Dict[str, dict]) -> None:
        from core.file_utils import atomic_write_text

        with self._file_lock:
            path = Path(self._assignment_path)
            if not path.is_absolute():
                path = _project_root() / path
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(
                path,
                json.dumps({"version": 1, "volumes": assignments}, ensure_ascii=False, indent=2),
            )

    # ---------------- 生效状态观测 ----------------

    def mark_active(self, volume_id: str, path: str) -> None:
        """存储构造/启动时登记实际生效路径（供 needs_restart 判定）。"""
        self._active_paths[volume_id] = path

    def active_path(self, volume_id: str) -> Optional[str]:
        return self._active_paths.get(volume_id)

    def needs_restart(self, volume_id: str) -> bool:
        """指派已变更但运行中的存储仍持有旧路径（重启后生效）。"""
        if self.get(volume_id).env_override and self.location_source(volume_id) == "env":
            return False
        resolved = self.resolve_path(volume_id)
        active = self._active_paths.get(volume_id)
        return active is not None and active != resolved


_registry: Optional[VolumeRegistry] = None


def get_volume_registry() -> VolumeRegistry:
    """进程级卷注册表单例。"""
    global _registry
    if _registry is None:
        _registry = VolumeRegistry()
    return _registry


def register_volume(descriptor: VolumeDescriptor) -> None:
    """登记存储卷（各存储模块 import 时调用）。"""
    get_volume_registry().register(descriptor)


def main_sqlite_path() -> str:
    """主库（agent 卷）默认路径的唯一权威：环境变量 > 项目根下 ConfigPaths.SQLITE_DB。

    同族库（memory/stickers/voiceprints/skill_vectors/share）的默认路径
    均由本路径派生 stem——放在 core 使 entities 层无需依赖 agent。
    """
    import os

    from core.path import ConfigPaths, project_root

    env_path = os.getenv("ANELF_BOT_SQLITE_PATH")
    if env_path and env_path.strip():
        return env_path.strip()
    return str(Path(project_root()) / ConfigPaths.SQLITE_DB)


def _default_assignment_path() -> str:
    from core.path import ConfigPaths

    return ConfigPaths.STORAGE_VOLUMES


def _project_root() -> Path:
    from core.path import project_root

    return Path(project_root())
