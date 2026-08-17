"""TaskRegistry：从 config/tasks/*.json 加载、CRUD 和热重载任务定义。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.log import log
from core.path import ConfigPaths

from .model import TaskDefinition

_TASKS_DIR = Path(ConfigPaths.TASKS_DIR)

# 任务文件写锁：AI 工具（agent/task/tools.py）与 Web API（web/routers/config.py）
# 同进程并发 CRUD 的 read-modify-write 互斥，防后写者覆盖先写者的字段
task_files_lock = asyncio.Lock()


class TaskRegistry:
    """任务注册表：管理所有 JSON 定义的任务。"""

    def __init__(self, tasks_dir: Optional[Path] = None) -> None:
        self._dir = tasks_dir or _TASKS_DIR
        self._tasks: Dict[str, TaskDefinition] = {}
        # 磁盘上存在的任务文件名（stem）集合：解析失败的文件也计入，
        # 用于区分"任务文件已删除"与"文件仍在但加载失败"两种缺失
        self._file_stems: set[str] = set()
        self.reload()

    def reload(self) -> int:
        """重新加载所有任务定义（递归子目录），返回加载数量。"""
        self._tasks.clear()
        self._file_stems.clear()
        if not self._dir.is_dir():
            return 0
        for json_file in sorted(self._dir.rglob("*.json")):
            self._file_stems.add(json_file.stem)
            try:
                data: Dict[str, Any] = json.loads(json_file.read_text("utf-8"))
                task = TaskDefinition.from_dict(data)
                folder = json_file.parent.relative_to(self._dir).as_posix()
                task.folder = "" if folder == "." else folder
                if task.name in self._tasks:
                    log(f"任务名称冲突 [{task.name}] ({json_file})，后者覆盖前者", "WARNING", tag="任务")
                self._tasks[task.name] = task
            except Exception as exc:
                log(f"任务加载失败 [{json_file.name}]: {exc}", "WARNING", tag="任务")
        log(f"任务注册表加载: {len(self._tasks)} 个任务", tag="任务")
        return len(self._tasks)

    def get(self, name: str) -> Optional[TaskDefinition]:
        return self._tasks.get(name)

    def task_file_exists(self, name: str) -> bool:
        """任务定义文件是否仍在磁盘上（含解析失败的文件，按文件名匹配）。"""
        return name in self._file_stems

    def list_all(self) -> List[TaskDefinition]:
        return list(self._tasks.values())

    def list_info(self) -> List[Dict[str, Any]]:
        """返回所有任务的摘要信息（供 AI 工具调用）。"""
        return [
            {
                "name": t.name,
                "display_name": t.display_name,
                "description": t.description,
                "scope": t.scope.value,
                "enabled": t.enabled,
                "folder": t.folder,
                "tool_tags": t.tool_tags,
                "allow_output_tools": t.allow_output_tools,
                "save_result_to_memory": t.save_result_to_memory,
                "reasoning_effort": t.reasoning_effort or "",
            }
            for t in self._tasks.values()
        ]

    def names(self) -> List[str]:
        return list(self._tasks.keys())
