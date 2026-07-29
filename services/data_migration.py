"""数据目录迁移服务 — Web 端发起的数据目录搬迁。

流程：目标校验 → 在线热拷贝（SQLite 走 Backup API，其余文件 copy2）
→ 逐文件校验 → 写入 data_root → 提示重启。源目录保留不删，由用户确认后手动清理。

注意：迁移期间 Agent 保持运行（Backup API 对并发写安全），
但新目录需重启后生效（各模块的连接与缓存路径在运行期不会切换）。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite

from core.log import log
from core.path import data_dir, project_root

# SQLite WAL 侧文件不拷贝（Backup API 产出的是自包含主库文件）
_SKIP_SUFFIXES = ("-wal", "-shm")

_state: Dict[str, Any] = {
    "state": "idle",  # idle / running / done / error
    "target": "",
    "current_file": "",
    "done": 0,
    "total": 0,
    "started_at": 0.0,
    "finished_at": 0.0,
    "error": "",
}
_running = False


class MigrationError(RuntimeError):
    """数据迁移操作错误（router 转成 HTTPException）。"""

    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _reset_state() -> None:
    _state.update({
        "state": "idle", "target": "", "current_file": "",
        "done": 0, "total": 0, "started_at": 0.0, "finished_at": 0.0, "error": "",
    })


def resolved_data_dir() -> Path:
    """当前数据目录的绝对路径（相对路径基于项目根解析）。"""
    p = Path(data_dir())
    if not p.is_absolute():
        p = Path(project_root()) / p
    return p.resolve()


def _data_dir_source() -> str:
    """数据目录来源：env（ANELF_DATA_DIR）/ config（data_root）/ default。"""
    if os.environ.get("ANELF_DATA_DIR", "").strip():
        return "env"
    try:
        from core.config import ConfigManager
        if str(ConfigManager.get("data_root", "") or "").strip():
            return "config"
    except Exception:
        pass
    return "default"


def _dir_size(root: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if name.endswith(_SKIP_SUFFIXES):
                continue
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass
    return total


def get_location() -> Dict[str, Any]:
    """当前数据位置概览：解析路径 / 来源 / 占用明细。"""
    root = resolved_data_dir()
    entries: List[Dict[str, Any]] = []
    total = 0
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if child.is_dir():
                size = _dir_size(child)
                entries.append({"name": child.name, "kind": "dir", "size_bytes": size})
            elif child.is_file() and not child.name.endswith(_SKIP_SUFFIXES):
                size = child.stat().st_size
                entries.append({"name": child.name, "kind": "file", "size_bytes": size})
            else:
                continue
            total += size
    return {
        "path": str(root),
        "source": _data_dir_source(),
        "env_override": os.environ.get("ANELF_DATA_DIR", ""),
        "exists": root.is_dir(),
        "total_bytes": total,
        "entries": entries,
    }


def check_target(target: str) -> Dict[str, Any]:
    """校验迁移目标目录，返回问题与警告标识列表（供前端 i18n 展示）。"""
    problems: List[str] = []
    warnings: List[str] = []
    raw = (target or "").strip()
    current = resolved_data_dir()
    required = _dir_size(current) if current.is_dir() else 0
    resolved = ""

    if not raw:
        problems.append("empty")
    else:
        p = Path(os.path.expanduser(raw))
        if not p.is_absolute():
            problems.append("not_absolute")
        else:
            resolved = str(p.resolve())
            rp = p.resolve()
            if rp == current:
                problems.append("same_as_current")
            elif _is_relative_to(rp, current):
                problems.append("inside_current")
            elif _is_relative_to(current, rp):
                problems.append("contains_current")
            else:
                if rp.exists():
                    if not rp.is_dir():
                        problems.append("not_a_directory")
                    else:
                        if not os.access(rp, os.W_OK):
                            problems.append("not_writable")
                        if any(rp.iterdir()):
                            warnings.append("not_empty")
                else:
                    ancestor = rp.parent
                    while not ancestor.exists() and ancestor != ancestor.parent:
                        ancestor = ancestor.parent
                    if not os.access(ancestor, os.W_OK):
                        problems.append("not_writable")
                if not problems:
                    try:
                        usage = shutil.disk_usage(rp if rp.exists() else ancestor)
                        if usage.free < int(required * 1.1):
                            problems.append("insufficient_space")
                    except OSError:
                        pass
    return {
        "target": resolved or raw,
        "ok": not problems,
        "problems": problems,
        "warnings": warnings,
        "required_bytes": required,
    }


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def migration_status() -> Dict[str, Any]:
    return dict(_state)


def start_migration(target: str) -> Dict[str, Any]:
    """启动后台迁移任务（单飞行）。返回初始状态。"""
    global _running
    if _running:
        raise MigrationError("已有迁移任务进行中", status_code=409)

    if _data_dir_source() == "env":
        raise MigrationError(
            "ANELF_DATA_DIR 环境变量生效中，优先级高于 data_root 配置；请先移除环境变量再迁移",
            status_code=409,
        )
    check = check_target(target)
    if not check["ok"]:
        raise MigrationError(f"目标目录校验未通过: {', '.join(check['problems'])}", status_code=400)

    _reset_state()
    _state.update({
        "state": "running",
        "target": check["target"],
        "started_at": time.time(),
    })
    _running = True
    asyncio.get_running_loop().create_task(
        _run_migration(resolved_data_dir(), Path(check["target"])),
        name="services.data_migration",
    )
    log(f"数据迁移已启动: {_state['target']}", tag="迁移")
    return migration_status()


async def _run_migration(source: Path, target: Path) -> None:
    global _running
    try:
        target.mkdir(parents=True, exist_ok=True)
        files = await asyncio.to_thread(_collect_files, source)
        _state["total"] = len(files)

        for index, src in enumerate(files):
            rel = src.relative_to(source)
            dest = target / rel
            _state["current_file"] = rel.as_posix()
            dest.parent.mkdir(parents=True, exist_ok=True)
            if src.suffix == ".sqlite3":
                await _backup_sqlite(src, dest)
            else:
                await asyncio.to_thread(shutil.copy2, src, dest)
                if dest.stat().st_size != src.stat().st_size:
                    raise MigrationError(f"文件校验失败: {rel.as_posix()}")
            _state["done"] = index + 1

        # 全部拷贝完成 → 切换 data_root
        from core.config import ConfigManager
        ConfigManager.initialize()
        ConfigManager.set("data_root", str(target))
        if not ConfigManager.save():
            raise MigrationError("data_root 配置写入失败", status_code=500)

        _state.update({"state": "done", "finished_at": time.time(), "current_file": ""})
        log(f"数据迁移完成: {source} -> {target}（{_state['done']} 个文件），需重启生效", tag="迁移")
    except Exception as exc:
        _state.update({"state": "error", "finished_at": time.time(), "error": str(exc)})
        log(f"数据迁移失败: {exc}", "ERROR", tag="迁移")
    finally:
        _running = False


def _collect_files(source: Path) -> List[Path]:
    if not source.is_dir():
        return []
    return sorted(
        p for p in source.rglob("*")
        if p.is_file() and not p.name.endswith(_SKIP_SUFFIXES)
    )


async def _backup_sqlite(src: Path, dest: Path) -> None:
    """SQLite 在线热备份（Backup API，对并发写安全，自包含输出）。"""
    src_conn = await aiosqlite.connect(str(src))
    try:
        dest_conn = await aiosqlite.connect(str(dest))
        try:
            await src_conn.backup(dest_conn)
        finally:
            await dest_conn.close()
    finally:
        await src_conn.close()
