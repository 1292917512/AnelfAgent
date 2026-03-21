"""TaskRegistry：从 config/tasks/*.json 加载、CRUD 和热重载任务定义。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.log import log

from .model import TaskDefinition

_TASKS_DIR = Path("config/tasks")


class TaskRegistry:
    """任务注册表：管理所有 JSON 定义的任务。"""

    def __init__(self, tasks_dir: Optional[Path] = None) -> None:
        self._dir = tasks_dir or _TASKS_DIR
        self._tasks: Dict[str, TaskDefinition] = {}
        self.reload()

    def reload(self) -> int:
        """重新加载所有任务定义，返回加载数量。"""
        self._tasks.clear()
        if not self._dir.is_dir():
            return 0
        for json_file in sorted(self._dir.glob("*.json")):
            try:
                data: Dict[str, Any] = json.loads(json_file.read_text("utf-8"))
                task = TaskDefinition.from_dict(data)
                self._tasks[task.name] = task
            except Exception as exc:
                log(f"任务加载失败 [{json_file.name}]: {exc}", "WARNING", tag="任务")
        log(f"任务注册表加载: {len(self._tasks)} 个任务", tag="任务")
        return len(self._tasks)

    def get(self, name: str) -> Optional[TaskDefinition]:
        return self._tasks.get(name)

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
                "tool_tags": t.tool_tags,
                "allow_output_tools": t.allow_output_tools,
                "save_result_to_memory": t.save_result_to_memory,
                "reasoning_effort": t.reasoning_effort or "",
            }
            for t in self._tasks.values()
        ]

    def names(self) -> List[str]:
        return list(self._tasks.keys())
