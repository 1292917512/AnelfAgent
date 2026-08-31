"""任务单元服务 -- config/tasks/**/*.json 的 CRUD、手动触发与注册表热重载。

文件写互斥锁复用 ``agent.task.registry.task_files_lock``（AI 工具侧同锁），
保证 Web API 与 AI 工具两条路径的 read-modify-write 不会交叉覆盖。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List

from core.log import log
from core.path import ConfigPaths
from services._parsing import to_bool as _to_bool
from services._runtime import get_runtime

_TASKS_DIR = Path(ConfigPaths.TASKS_DIR)

_TASK_DEFAULTS: Dict[str, Any] = {
    "display_name": "", "description": "", "scope": "global",
    "enabled": True, "memory_type": "semantic", "importance": 0.5,
    "tags": [], "source": "", "null_keywords": [], "tool_tags": [], "prompt": "",
    "allow_output_tools": False,
    "save_result_to_memory": True,
    "expires_at": "", "created_at": 0.0, "updated_at": 0.0,
}

_OPTIONAL_TASK_OVERRIDE_FIELDS = ("model_id", "reasoning_effort", "expires_at")


class TaskServiceError(Exception):
    """任务服务客户端错误（status_code 供路由层映射 HTTP 状态码）。"""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class TaskStorageError(Exception):
    """任务文件读写失败（路由层映射 500；action 为操作描述，cause 为原始异常）。"""

    def __init__(self, action: str, cause: Exception) -> None:
        super().__init__(str(cause))
        self.action = action
        self.cause = cause


def _ensure_tasks_dir() -> None:
    _TASKS_DIR.mkdir(parents=True, exist_ok=True)


def _sanitize_folder(folder: str) -> str:
    """标准化任务文件夹：限制为 tasks 目录内的相对路径，防路径穿越。"""
    normalized = folder.replace("\\", "/").strip("/").strip()
    if not normalized:
        return ""
    parts = [p for p in normalized.split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        raise TaskServiceError("非法的任务文件夹路径", status_code=400)
    return "/".join(parts)


def _task_path(name: str, folder: str = "") -> Path:
    if "/" in name or "\\" in name or name in {".", ".."}:
        raise TaskServiceError("非法的任务名称", status_code=400)
    base = _TASKS_DIR / folder if folder else _TASKS_DIR
    resolved = base.resolve()
    if not resolved.is_relative_to(_TASKS_DIR.resolve()):
        raise TaskServiceError("非法的任务文件夹路径", status_code=400)
    return resolved / f"{name}.json"


def _task_folder_of(json_file: Path) -> str:
    """由文件位置推导任务文件夹。

    _task_path 返回的是已 resolve 的绝对路径，而 rglob 给出相对路径，
    统一 resolve 后再做 relative_to，避免绝对/相对混用抛 ValueError。
    """
    folder = json_file.parent.resolve().relative_to(_TASKS_DIR.resolve()).as_posix()
    return "" if folder == "." else folder


def _normalize_task(data: Dict[str, Any]) -> Dict[str, Any]:
    """确保任务数据包含所有必需字段。"""
    for k, v in _TASK_DEFAULTS.items():
        if k not in data:
            data[k] = v
    data["allow_output_tools"] = _to_bool(data.get("allow_output_tools"), default=False)
    data["save_result_to_memory"] = _to_bool(data.get("save_result_to_memory"), default=True)
    return _normalize_optional_task_overrides(data)


def _validate_task_expiry(data: Dict[str, Any]) -> None:
    """写入路径的 expires_at 严格校验（非法 → 400；空/null 为移除语义，不校验）。"""
    raw = data.get("expires_at")
    if raw is None:
        return
    value = str(raw).strip()
    if not value:
        return
    from agent.task.model import parse_task_time
    if parse_task_time(value) is None:
        raise TaskServiceError(
            f"expires_at 格式非法: {value!r}（期望 YYYY-MM-DD 或 YYYY-MM-DD HH:MM）",
            status_code=400,
        )


def _normalize_optional_task_overrides(data: Dict[str, Any]) -> Dict[str, Any]:
    """标准化可选覆盖字段：空值转为“移除字段”，非空字符串做 trim。"""
    from agent.llm.reasoning import CANONICAL_EFFORTS
    valid_efforts = frozenset(CANONICAL_EFFORTS)
    for field in _OPTIONAL_TASK_OVERRIDE_FIELDS:
        if field not in data:
            continue
        raw = data.get(field)
        if raw is None:
            data.pop(field, None)
            continue
        normalized = str(raw).strip()
        if not normalized:
            data.pop(field, None)
            continue
        if field == "reasoning_effort":
            lowered = normalized.lower()
            if lowered in valid_efforts:
                data[field] = lowered
            else:
                data.pop(field, None)
            continue
        if field == "expires_at":
            from agent.task.model import normalize_task_time
            expiry = normalize_task_time(normalized)
            if expiry:
                data[field] = expiry
            else:
                # 非法值容错移除（读取路径安全；写入路径由 _validate_task_expiry 先行 400）
                data.pop(field, None)
            continue
        data[field] = normalized
    return data


def _load_task(name: str, folder: str = "") -> Dict[str, Any]:
    p = _task_path(name, _sanitize_folder(folder))
    if not p.exists():
        raise TaskServiceError(f"任务 [{name}] 不存在", status_code=404)
    try:
        data = _normalize_task(json.loads(p.read_text("utf-8")))
        data["folder"] = _task_folder_of(p)
        return data
    except TaskServiceError:
        raise
    except Exception as e:
        raise TaskStorageError("读取任务配置", e) from e


class TaskService:
    """任务单元管理服务（Web 侧入口）。"""

    def list_tasks(self) -> List[Dict[str, Any]]:
        """列出所有任务单元（config/tasks/**/*.json，递归子目录）。"""
        _ensure_tasks_dir()
        tasks: List[Dict[str, Any]] = []
        for json_file in sorted(_TASKS_DIR.rglob("*.json")):
            try:
                data = _normalize_task(json.loads(json_file.read_text("utf-8")))
                data["folder"] = _task_folder_of(json_file)
                tasks.append(data)
            except Exception as e:
                log(f"任务配置解析失败 ({json_file.name}): {e}", "DEBUG")
        return tasks

    def get_task(self, name: str, folder: str = "") -> Dict[str, Any]:
        """读取单个任务定义。"""
        return _load_task(name, folder)

    async def create_task(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """创建任务定义文件并热重载注册表。

        Args:
            payload: 任务字段字典（含 name/prompt/folder 等，来自创建请求）。

        Returns:
            规范化后的任务数据（含 folder 字段）。

        Raises:
            TaskServiceError: 名称/文件夹非法（400）或任务已存在（409）。
            TaskStorageError: 文件写入失败。
        """
        from agent.task.registry import task_files_lock
        from core.file_utils import atomic_write_text

        _ensure_tasks_dir()
        folder = _sanitize_folder(str(payload.get("folder") or ""))
        name = str(payload.get("name") or "")
        p = _task_path(name, folder)

        task_data = dict(payload)
        _validate_task_expiry(task_data)
        task_data = _normalize_optional_task_overrides(task_data)
        task_data.pop("folder", None)
        if not task_data.get("source"):
            task_data["source"] = name
        if not task_data.get("display_name"):
            task_data["display_name"] = name
        now = time.time()
        task_data["created_at"] = now
        task_data["updated_at"] = now
        _normalize_task(task_data)

        try:
            async with task_files_lock:
                if p.exists():
                    raise TaskServiceError(f"任务 [{name}] 已存在", status_code=409)
                p.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_text(p, json.dumps(task_data, ensure_ascii=False, indent=2))
        except TaskServiceError:
            raise
        except Exception as e:
            raise TaskStorageError("写入任务配置", e) from e

        self.reload_registry()
        task_data["folder"] = folder
        return task_data

    async def update_task(self, name: str, folder: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """更新任务定义（支持跨文件夹移动），并热重载注册表。

        Args:
            name: 任务名。
            folder: 当前所在文件夹（查询参数）。
            updates: 仅包含显式提供字段的更新字典（键集合即"已提供"语义，
                可选覆盖字段显式传 null 时由此集合识别并移除）。

        Returns:
            更新后的任务数据（含 folder 字段）。

        Raises:
            TaskServiceError: 路径非法（400）、任务不存在（404）或移动目标冲突（409）。
            TaskStorageError: 文件读写失败。
        """
        from agent.task.registry import task_files_lock

        old_folder = _sanitize_folder(folder)
        async with task_files_lock:
            existing = _load_task(name, old_folder)
            provided_fields = set(updates)
            updates = dict(updates)
            new_folder = _sanitize_folder(updates.pop("folder")) if "folder" in updates else None
            _validate_task_expiry(updates)
            updates = _normalize_optional_task_overrides(updates)
            existing.update(updates)
            for field in _OPTIONAL_TASK_OVERRIDE_FIELDS:
                if field in provided_fields and field not in updates:
                    existing.pop(field, None)
            existing.pop("folder", None)
            existing["updated_at"] = time.time()

            if new_folder is not None and new_folder != old_folder:
                moving = True
                target_folder = new_folder
            else:
                moving = False
                target_folder = old_folder
            target = _task_path(name, target_folder)
            if moving and target.exists():
                raise TaskServiceError(f"任务 [{name}] 在目标文件夹已存在", status_code=409)

            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                # 先写临时文件再 os.replace 原子替换；移动场景写新成功后才删旧，
                # 写新失败时旧文件保持不动（tmp 清理后抛出）
                tmp = target.with_name(target.name + ".tmp")
                try:
                    tmp.write_text(
                        json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    os.replace(tmp, target)
                except Exception:
                    tmp.unlink(missing_ok=True)
                    raise
                if moving:
                    _task_path(name, old_folder).unlink()
            except Exception as e:
                raise TaskStorageError("写入任务配置", e) from e

        self.reload_registry()
        existing["folder"] = target_folder
        return existing

    async def delete_task(self, name: str, folder: str = "") -> None:
        """删除任务定义文件，热重载注册表并同步移除心跳调度绑定。

        Raises:
            TaskServiceError: 路径非法（400）或任务不存在（404）。
            TaskStorageError: 文件删除失败。
        """
        from agent.task.registry import task_files_lock

        async with task_files_lock:
            p = _task_path(name, _sanitize_folder(folder))
            if not p.exists():
                raise TaskServiceError(f"任务 [{name}] 不存在", status_code=404)
            try:
                p.unlink()
            except Exception as e:
                raise TaskStorageError("删除任务", e) from e

        self.reload_registry()
        self._remove_task_schedule(name)

    def trigger_task(self, name: str, folder: str = "") -> None:
        """手动触发执行指定任务，在后台异步执行。

        Raises:
            TaskServiceError: 任务不存在（404）或 Agent 未初始化（503）。
        """
        p = _task_path(name, _sanitize_folder(folder))
        if not p.exists():
            raise TaskServiceError(f"任务 [{name}] 不存在", status_code=404)

        rt = get_runtime()
        if rt is None:
            raise TaskServiceError("Agent 尚未初始化", status_code=503)

        async def _run() -> None:
            try:
                result = await rt.mind.execute_task(name)
                log(f"Web 手动任务完成: {name} ({'有产出' if result else '无产出'})", tag="任务")
            except Exception as exc:
                log(f"Web 手动任务异常 [{name}]: {exc}", "WARNING", tag="任务")

        asyncio.create_task(_run(), name=f"agent.task.web_{name}")

    @staticmethod
    def reload_registry() -> None:
        """热重载运行中的任务注册表。"""
        try:
            rt = get_runtime()
            if rt is not None:
                rt.mind.heartbeat_engine.task_registry.reload()
        except Exception as e:
            log(f"任务注册表热重载失败: {e}", "DEBUG")

    @staticmethod
    def _remove_task_schedule(name: str) -> None:
        """任务删除后同步移除其心跳调度绑定并热重载引擎，避免孤儿调度项。"""
        from services.heartbeat import HeartbeatService
        HeartbeatService.remove_schedule_for_task(name)
