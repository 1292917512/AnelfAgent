"""任务执行显式登记的工作区文件与频道确认的文件回执。

模型可见：登记工具返回相对路径和大小，按会话查询历史可见归属的文件与回执。
Token 影响：仅在调用时返回有界记录。
缓存影响：结果通过工具消息返回，不进入稳定前缀。
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict

from agent.messages import is_conversation_scope
from core.path import workspace_root

_LIMIT = 32


class FileReceipt(TypedDict):
    scope: str
    message_id: str


class TaskArtifact(TypedDict):
    path: str
    size: int
    presented: bool
    receipts: list[FileReceipt]


@dataclass
class TaskFiles:
    task: str
    owner: str
    files: dict[str, TaskArtifact] = field(default_factory=dict)
    closed: bool = False

    def register(self, path: Path) -> TaskArtifact:
        root = Path(workspace_root()).resolve()
        file = (root / path).resolve(strict=True)
        if not file.is_relative_to(root) or not file.is_file():
            raise ValueError("请提供工作区内的普通文件")
        relative = file.relative_to(root).as_posix()
        if relative not in self.files and len(self.files) >= _LIMIT:
            raise ValueError(f"单次任务最多登记 {_LIMIT} 个文件")
        size = file.stat().st_size
        item = self.files.setdefault(relative, {"path": relative, "size": size, "presented": False, "receipts": []})
        item["size"] = size
        return item


_current: ContextVar[TaskFiles | None] = ContextVar("task_files", default=None)


@contextmanager
def capture_task_files(task: str, owner: str) -> Iterator[TaskFiles]:
    run = TaskFiles(task, owner)
    token = _current.set(run)
    try:
        yield run
    finally:
        run.closed = True
        _current.reset(token)


def present_file(path: str) -> dict[str, Any]:
    run = _current.get()
    if run is None or run.closed:
        raise ValueError("仅可在任务执行期间登记产物")
    item = run.register(Path(path))
    item["presented"] = True
    return {"path": item["path"], "size": item["size"]}


def confirm_file_delivery(path: str, scope: str, message_id: object) -> None:
    """仅归档任务中已获确认的工作区文件。归档失败不得改变出站结果。"""
    run = _current.get()
    if run is None or run.closed or not is_conversation_scope(scope):
        return
    try:
        item = run.register(Path(path).absolute())
    except (OSError, RuntimeError, ValueError):
        return
    receipts = item["receipts"]
    receipt: FileReceipt = {"scope": scope, "message_id": str(message_id or "")[:256]}
    if len(receipts) < _LIMIT and receipt not in receipts:
        receipts.append(receipt)


def history_for_scope(record: dict[str, Any], scope: str) -> dict[str, Any]:
    """保留原记录摘要，文件按发起会话或已确认的接收会话投影。"""
    row = record.copy()
    files = row.pop("artifacts", [])
    owner = row.pop("artifact_scope", "")
    run = _current.get()
    own_task = bool(run and not run.closed and run.task == row.get("task_name") and run.owner == owner)
    if own_task and not is_conversation_scope(scope):
        row["artifacts"] = files
        return row
    if not is_conversation_scope(scope):
        return row
    visible = []
    for item in files:
        receipts = [r for r in item["receipts"] if r["scope"] == scope]
        if receipts or (scope == owner and item.get("presented")):
            visible.append({"path": item["path"], "size": item["size"], "receipts": receipts})
    if visible:
        row["artifacts"] = visible
    return row
