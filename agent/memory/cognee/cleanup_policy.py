"""版本清理的增长信号与无进展扫描覆盖，不访问文件或数据库。

模型看到什么：沿用现有记忆与工具通道，不新增记忆文本或提示词。
token 影响：维护策略不调用模型，不产生抽取或向量化请求。
缓存影响：仅改变存储维护调度，不修改上下文稳定前缀。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class VersionFootprint:
    """已提交索引的版本清单数与 LanceDB 逻辑字节，不包含保护快照副本。"""

    files: int
    bytes: int

    @classmethod
    def from_publication(cls, status: Mapping[str, object]) -> VersionFootprint | None:
        files, size = status.get("lance_version_files"), status.get("lance_bytes")
        if type(files) is not int or type(size) is not int or files < 0 or size < 0:
            return None
        return cls(files, size)


class CleanupPressure:
    """维护后的增长基线、待续跑信号与跨轮扫描覆盖，由协调器独占持有。"""

    def __init__(self) -> None:
        self.baseline = VersionFootprint(0, 0)
        self.pending = False
        self.failed = False
        self._empty_tables = 0

    def reason(
        self, current: VersionFootprint | None, *, versions: int, growth_bytes: int,
    ) -> Literal["versions", "growth", "backlog"] | None:
        if self.failed or not (versions or growth_bytes):
            return None
        if self.pending:
            return "backlog"
        if current is None:
            return None
        if versions > 0 and current.files - self.baseline.files >= versions:
            return "versions"
        if growth_bytes > 0 and current.bytes - self.baseline.bytes >= growth_bytes:
            return "growth"
        return None

    def begin(self, *, continuing: bool) -> None:
        if not continuing:
            self._empty_tables = 0

    def observe_round(self, *, removed: int, visited: int, total: int) -> bool:
        """无进展扫描已覆盖整棵数据树时返回 True；有回收进展则重新计数。"""
        self._empty_tables = 0 if removed else self._empty_tables + visited
        return removed == 0 and self._empty_tables >= total

    def finish(self, current: VersionFootprint | None, *, pending: bool, failed: bool) -> None:
        if current is not None:
            self.baseline = current
        self.failed = failed
        self.pending = pending and not failed
