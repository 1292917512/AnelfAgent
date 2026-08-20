"""存储卷管理操作 — 每卷独立的备份 / 恢复 / 迁移 / 外部 SQL 导出导入。

所有操作基于 core.storage_volume 注册表的事实（卷清单 / 路径 / 能力）；
长任务（树压缩 / 拷贝 / SQL 传输）后台执行并维护每卷单飞状态机
（对齐 services.data_migration 的状态模式）。

恢复与迁移均为「校验 + 拷贝 + 配置切换 + 重启生效」语义：
- 恢复：产物暂存 + pending 标记，重启后由 bootstrap 的
  agent.storage.volume_restore 完成文件交换
- 迁移：在线拷贝（SQLite 走 Backup API；cognee 树经 coordinator
  空闲窗口与写入互斥）+ 卷指派写入
两者重启前的新写入不落新位置（与整目录迁移一致，UI 需明示）。

外部 SQL 导出/导入为快照语义：导出会重建目标侧同名（可加前缀）表并
全量覆写，同时在目标库登记导出清单表（`{prefix}_anelf_export`）；
导入仅接受由导出产生的目标库。派生索引（FTS5/vec0 影子表）不参与
传输，导入后由各存储的建表逻辑自动重建。
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import tarfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import aiosqlite

from core.file_utils import walk_files
from core.log import log
from core.storage_volume import VolumeCapability, VolumeKind, get_volume_registry
from services.data_migration import validate_target_dir
from services.database import _SHADOW_PATTERNS, ensure_volume_modules, online_sqlite_backup

# 备份产物在备份目录内的固定名（manifest.json 同目录）
_SQLITE_ARTIFACT = "volume.sqlite3"
_TREE_ARTIFACT = "volume.tar.gz"
_MANIFEST = "manifest.json"
# 行流式传输批大小（导出按 rowid 窗口 / 导入按 OFFSET 分页）
_BATCH_ROWS = 1000


class VolumeOperationError(RuntimeError):
    """存储卷操作错误（router 转成 HTTPException）。"""

    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


# ----------------------------------------------------------------------
# 目录与工具
# ----------------------------------------------------------------------


def _volume_backup_dir(volume_id: str) -> Path:
    from agent.storage.volume_restore import backups_root

    return backups_root() / volume_id


def _staging_dir() -> Path:
    from agent.storage.volume_restore import backups_root

    return backups_root() / ".staging"


def _descriptor(volume_id: str):
    registry = get_volume_registry()
    try:
        return registry.get(volume_id)
    except KeyError:
        pass
    # 首次遇到未知卷：触发各存储模块的卷登记后再查一次
    ensure_volume_modules()
    try:
        return registry.get(volume_id)
    except KeyError:
        raise VolumeOperationError(f"未知存储卷: {volume_id}", status_code=404) from None


def _require_capability(volume_id: str, cap: VolumeCapability) -> None:
    descriptor = _descriptor(volume_id)
    if cap not in descriptor.capabilities:
        raise VolumeOperationError(
            f"存储卷 {volume_id} 不支持该操作（形态 {descriptor.kind.value}）", status_code=400
        )


def _resolved_path(volume_id: str) -> Path:
    return Path(get_volume_registry().resolve_path(volume_id))


def _notes_members(root: Path) -> List[Path]:
    """便签树成员：根级 *.md + events/ + groups/ + profile_backups/（数据根其余内容不属便签卷）。"""
    members = sorted(p for p in root.glob("*.md") if p.is_file())
    for sub in ("events", "groups", "profile_backups"):
        members.extend(walk_files(root / sub))
    return members


def _members_size(members: List[Path]) -> int:
    total = 0
    for path in members:
        try:
            total += path.stat().st_size
        except OSError:
            log("_members_size 异常已忽略", "DEBUG")
    return total


async def _volume_size_bytes(volume_id: str, kind: VolumeKind, path: Path) -> int:
    """卷占用：cognee 走统计缓存（大库遍历数十秒），便签树只统计卷成员。"""
    if kind is VolumeKind.SQLITE:
        return path.stat().st_size if path.is_file() else 0
    if kind is VolumeKind.COGNEE_TREE:
        from agent.memory.cognee.storage import cognee_storage_stats

        stats = await cognee_storage_stats.get(str(path))
        return int(stats.get("total_bytes", 0))
    return await asyncio.to_thread(lambda: _members_size(_notes_members(path)))


def _cognee_coordinator_alive() -> bool:
    from agent.memory.cognee.runtime import get_cognee_coordinator

    coordinator = get_cognee_coordinator()
    return coordinator is not None and bool(coordinator._task and not coordinator._task.done())


async def _cognee_quiescence(job: Callable[[], Any]) -> Any:
    """cognee 数据树操作的静默期：worker 空闲窗口执行（与写入互斥）。"""
    from agent.memory.cognee.runtime import get_cognee_coordinator

    coordinator = get_cognee_coordinator()
    if coordinator is None:
        return await job()
    return await coordinator.run_in_idle_window(job())


# ----------------------------------------------------------------------
# 每卷单飞状态机
# ----------------------------------------------------------------------

_op_states: Dict[str, Dict[str, Any]] = {}


def _reset_state(volume_id: str, op: str) -> Dict[str, Any]:
    state = {
        "op": op,
        "state": "running",
        "phase": "",
        "done": 0,
        "total": 0,
        "error": "",
        "result": {},
        "started_at": time.time(),
        "finished_at": 0.0,
    }
    _op_states[volume_id] = state
    return state


def require_volume(volume_id: str) -> None:
    """校验卷存在（不存在抛 404），供路由参数校验。"""
    _descriptor(volume_id)


def operation_status(volume_id: str) -> Dict[str, Any]:
    state = _op_states.get(volume_id)
    if not state:
        return {"op": None, "state": "idle"}
    return dict(state)


def _ensure_idle(volume_id: str) -> None:
    state = _op_states.get(volume_id)
    if state and state["state"] == "running":
        raise VolumeOperationError(
            f"存储卷 {volume_id} 已有操作进行中（{state['op']}）", status_code=409
        )


def _pending_volume_ids() -> List[str]:
    from agent.storage.volume_restore import pending_summary

    return pending_summary() or []


def _ensure_not_pending(volume_id: str) -> None:
    if volume_id in _pending_volume_ids():
        raise VolumeOperationError(
            f"存储卷 {volume_id} 存在待重启落盘的恢复任务，禁止再操作", status_code=409
        )


def _spawn(volume_id: str, coro_factory: Callable[[Dict[str, Any]], Any]) -> None:
    """以后台任务执行并保活（完成写终态，异常写 error）。"""
    state = _op_states[volume_id]

    async def _runner() -> None:
        try:
            result = await coro_factory(state)
            state.update({"state": "done", "finished_at": time.time(), "result": result or {}})
        except Exception as exc:
            state.update({"state": "error", "finished_at": time.time(), "error": str(exc)})
            log(f"存储卷 {volume_id} {state['op']} 失败: {exc}", "ERROR", tag="存储卷")

    asyncio.get_running_loop().create_task(_runner(), name=f"services.volume_ops.{volume_id}")


# ----------------------------------------------------------------------
# 卷清单
# ----------------------------------------------------------------------


async def list_volumes() -> List[Dict[str, Any]]:
    """卷清单：路径 / 大小 / 指派 / 生效状态 / 备份概览（卷面板数据源）。"""
    registry = get_volume_registry()
    ensure_volume_modules()
    pending = set(_pending_volume_ids())
    items: List[Dict[str, Any]] = []
    for descriptor in registry.list():
        volume_id = descriptor.volume_id
        path = _resolved_path(volume_id)
        if descriptor.kind is VolumeKind.SQLITE:
            exists = path.is_file()
        else:
            exists = path.is_dir()
        location = registry.read_location(volume_id)
        backups = list_backups(volume_id)
        items.append({
            "volume_id": volume_id,
            "name": descriptor.name,
            "description": descriptor.description,
            "kind": descriptor.kind.value,
            "capabilities": sorted(c.value for c in descriptor.capabilities),
            "path": str(path),
            "default_path": descriptor.default_path(),
            "location_source": registry.location_source(volume_id),
            "assignment": location.path if location else None,
            "active_path": registry.active_path(volume_id),
            "needs_restart": registry.needs_restart(volume_id),
            "pending_restore": volume_id in pending,
            "exists": exists,
            "size_bytes": await _volume_size_bytes(volume_id, descriptor.kind, path),
            "backup_count": len(backups),
            "last_backup_at": backups[0]["created_at"] if backups else 0.0,
        })
    return items


# ----------------------------------------------------------------------
# 备份
# ----------------------------------------------------------------------


def list_backups(volume_id: str) -> List[Dict[str, Any]]:
    """备份清单（新 → 旧），读取各 manifest 摘要。"""
    _descriptor(volume_id)
    root = _volume_backup_dir(volume_id)
    items: List[Dict[str, Any]] = []
    if not root.is_dir():
        return items
    for entry in sorted(root.iterdir(), reverse=True):
        if not entry.is_dir():
            continue
        manifest = _read_manifest(entry)
        if manifest is None:
            continue
        items.append({
            "backup_id": entry.name,
            "created_at": float(manifest.get("created_at", 0.0)),
            "kind": manifest.get("kind", ""),
            "artifact": manifest.get("artifact", ""),
            "size_bytes": int(manifest.get("size_bytes", 0)),
            "table_count": int(manifest.get("table_count", 0)),
            "file_count": int(manifest.get("file_count", 0)),
            "consistency": manifest.get("consistency", ""),
        })
    return items


def _read_manifest(backup_dir: Path) -> Optional[Dict[str, Any]]:
    path = backup_dir / _MANIFEST
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, ValueError):
        return None


def _write_manifest(backup_dir: Path, payload: Dict[str, Any]) -> None:
    (backup_dir / _MANIFEST).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def get_backup(volume_id: str, backup_id: str) -> Dict[str, Any]:
    """单份备份详情（含产物绝对路径；backup_id 仅允许时间戳命名目录）。"""
    _descriptor(volume_id)
    if not backup_id.replace("-", "").isdigit():
        raise VolumeOperationError("非法备份标识", status_code=400)
    backup_dir = _volume_backup_dir(volume_id) / backup_id
    manifest = _read_manifest(backup_dir)
    if manifest is None:
        raise VolumeOperationError(f"备份不存在: {backup_id}", status_code=404)
    artifact = manifest.get("artifact", "")
    info = dict(manifest)
    info["backup_id"] = backup_id
    info["artifact_path"] = str(backup_dir / artifact)
    return info


def delete_backup(volume_id: str, backup_id: str) -> None:
    get_backup(volume_id, backup_id)  # 校验存在
    _ensure_not_pending(volume_id)
    backup_dir = _volume_backup_dir(volume_id) / backup_id
    shutil.rmtree(backup_dir, ignore_errors=True)
    log(f"存储卷 {volume_id} 备份已删除: {backup_id}", tag="存储卷")


def _prune_backups(volume_id: str, keep: int) -> None:
    if keep <= 0:
        return
    backups = list_backups(volume_id)
    for item in backups[keep:]:
        backup_dir = _volume_backup_dir(volume_id) / item["backup_id"]
        shutil.rmtree(backup_dir, ignore_errors=True)


def create_backup(volume_id: str) -> Dict[str, Any]:
    """启动后台备份任务（每卷单飞）。"""
    _require_capability(volume_id, VolumeCapability.BACKUP)
    _ensure_idle(volume_id)
    _ensure_not_pending(volume_id)
    _reset_state(volume_id, "backup")
    _spawn(volume_id, lambda state: _run_backup(volume_id, state))
    return operation_status(volume_id)


async def _run_backup(volume_id: str, state: Dict[str, Any]) -> Dict[str, Any]:
    from core.config import get_config_int

    descriptor = _descriptor(volume_id)
    source = _resolved_path(volume_id)
    # 毫秒后缀防同秒碰撞（备份目录名即备份标识，须保持纯数字+连字符）
    backup_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{int((time.time() % 1) * 1000):03d}"
    backup_dir = _volume_backup_dir(volume_id) / backup_id
    backup_dir.mkdir(parents=True, exist_ok=True)

    if descriptor.kind is VolumeKind.SQLITE:
        if not source.is_file():
            raise VolumeOperationError(f"卷数据文件不存在: {source}", status_code=409)
        artifact_path = backup_dir / _SQLITE_ARTIFACT
        state["phase"] = "sqlite_backup"
        await online_sqlite_backup(source, artifact_path)
        table_count = await _count_tables(artifact_path)
        manifest = {
            "volume_id": volume_id,
            "kind": descriptor.kind.value,
            "created_at": time.time(),
            "artifact": _SQLITE_ARTIFACT,
            "size_bytes": artifact_path.stat().st_size,
            "table_count": table_count,
            "consistency": "online_backup",
        }
    else:
        artifact_path = backup_dir / _TREE_ARTIFACT
        state["phase"] = "tree_archive"
        file_count, consistency = await _archive_tree(
            descriptor.kind, source, artifact_path, state
        )
        manifest = {
            "volume_id": volume_id,
            "kind": descriptor.kind.value,
            "created_at": time.time(),
            "artifact": _TREE_ARTIFACT,
            "size_bytes": artifact_path.stat().st_size,
            "file_count": file_count,
            "consistency": consistency,
        }
    _write_manifest(backup_dir, manifest)
    keep = get_config_int("volume_backup_retention", 5)
    _prune_backups(volume_id, keep)
    log(
        f"存储卷 {volume_id} 备份完成: {manifest['size_bytes']} bytes"
        f"（保留策略 {keep}）",
        tag="存储卷",
    )
    return {"backup_id": backup_id, "size_bytes": manifest["size_bytes"]}


async def _count_tables(db_path: Path) -> int:
    conn = await aiosqlite.connect(str(db_path))
    try:
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        row = await cursor.fetchone()
        return int(row[0]) if row else 0
    finally:
        await conn.close()


async def _archive_tree(
    kind: VolumeKind,
    source: Path,
    artifact: Path,
    state: Dict[str, Any],
) -> Tuple[int, str]:
    """树卷打包为 tgz，返回 (文件数, 一致性级别)。

    cognee 走 coordinator 空闲窗口静默期（与写入互斥）；便签树为原子写
    文件，直接打包即可保证一致性。
    """
    if not source.is_dir():
        raise VolumeOperationError(f"卷数据目录不存在: {source}", status_code=409)

    members = await asyncio.to_thread(
        _notes_members if kind is VolumeKind.NOTES_TREE else walk_files, source
    )
    state["total"] = len(members)

    def _tar() -> int:
        with tarfile.open(artifact, "w:gz") as tar:
            for index, path in enumerate(members):
                tar.add(path, arcname=str(path.relative_to(source)))
                state["done"] = index + 1
        return len(members)

    if kind is VolumeKind.COGNEE_TREE:
        quiesced = _cognee_coordinator_alive()
        count = int(await _cognee_quiescence(lambda: asyncio.to_thread(_tar)))
        return count, "idle_window" if quiesced else "live"
    return int(await asyncio.to_thread(_tar)), "live"


# ----------------------------------------------------------------------
# 恢复（暂存 + 重启落盘）
# ----------------------------------------------------------------------


def restore_backup(volume_id: str, backup_id: str) -> Dict[str, Any]:
    """校验备份 → 暂存 → 写 pending 标记（重启后由 bootstrap 完成交换）。"""
    _require_capability(volume_id, VolumeCapability.RESTORE)
    _ensure_idle(volume_id)
    _ensure_not_pending(volume_id)
    descriptor = _descriptor(volume_id)
    info = get_backup(volume_id, backup_id)
    artifact = Path(info["artifact_path"])
    if not artifact.is_file():
        raise VolumeOperationError(f"备份产物缺失: {artifact}", status_code=409)

    staging = _staging_dir()
    staging.mkdir(parents=True, exist_ok=True)
    staged = staging / f"{volume_id}-{backup_id}-{artifact.name}"
    shutil.copy2(artifact, staged)

    if descriptor.kind is VolumeKind.SQLITE:
        _verify_sqlite(staged)
    else:
        _verify_tar(staged)

    from agent.storage.volume_restore import stage_restore_entry

    stage_restore_entry(volume_id, descriptor.kind, str(staged))
    state = _reset_state(volume_id, "restore")
    state.update({
        "state": "done",
        "finished_at": time.time(),
        "result": {"staged": str(staged), "needs_restart": True},
    })
    log(f"存储卷 {volume_id} 恢复已暂存，等待重启落盘: {backup_id}", tag="存储卷")
    return dict(state)


def _verify_sqlite(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        row = conn.execute("PRAGMA quick_check").fetchone()
        if not row or str(row[0]).lower() != "ok":
            raise VolumeOperationError(f"备份完整性校验失败: {row[0] if row else '无结果'}")
    except sqlite3.DatabaseError as exc:
        raise VolumeOperationError(f"备份文件无法读取: {exc}", status_code=409) from exc
    finally:
        conn.close()


def _verify_tar(path: Path) -> None:
    try:
        with tarfile.open(path, "r:gz") as tar:
            members = tar.getmembers()
        if not members:
            raise VolumeOperationError("备份压缩包为空", status_code=409)
    except tarfile.TarError as exc:
        raise VolumeOperationError(f"备份压缩包损坏: {exc}", status_code=409) from exc


# ----------------------------------------------------------------------
# 迁移（在线拷贝 + 指派切换 + 重启生效）
# ----------------------------------------------------------------------


async def check_relocate_target(volume_id: str, target: str) -> Dict[str, Any]:
    """校验卷迁移目标目录（问题/警告为 i18n 标识，供前端展示）。

    尺寸估计走各自口径（cognee 统计缓存 / 便签成员 / SQLite 文件），
    不在事件循环上同步遍历大目录。
    """
    _require_capability(volume_id, VolumeCapability.RELOCATE)
    descriptor = _descriptor(volume_id)
    current = _resolved_path(volume_id)
    current_base = current.parent if descriptor.kind is VolumeKind.SQLITE else current
    required = await _volume_size_bytes(volume_id, descriptor.kind, current)
    result = validate_target_dir(target, current_base, required_bytes=required)
    if result["ok"] and descriptor.kind is VolumeKind.SQLITE:
        dest_file = Path(result["target"]) / current.name
        if dest_file.exists():
            result["ok"] = False
            result["problems"].append("target_file_exists")
    return result


async def relocate_volume(volume_id: str, target_dir: str) -> Dict[str, Any]:
    """启动后台迁移任务（拷贝 → 指派写入 → 重启生效）。"""
    check = await check_relocate_target(volume_id, target_dir)
    if not check["ok"]:
        raise VolumeOperationError(
            f"目标目录校验未通过: {', '.join(check['problems'])}", status_code=400
        )
    _ensure_idle(volume_id)
    _ensure_not_pending(volume_id)
    if get_volume_registry().location_source(volume_id) == "env":
        raise VolumeOperationError(
            "该卷路径正被环境变量钉死（优先级最高），无法迁移", status_code=409
        )
    _reset_state(volume_id, "relocate")
    target = Path(check["target"])
    _spawn(volume_id, lambda state: _run_relocate(volume_id, target, state))
    return operation_status(volume_id)


async def _run_relocate(volume_id: str, target_dir: Path, state: Dict[str, Any]) -> Dict[str, Any]:
    descriptor = _descriptor(volume_id)
    source = _resolved_path(volume_id)
    registry = get_volume_registry()

    if descriptor.kind is VolumeKind.SQLITE:
        dest = target_dir / source.name
        target_dir.mkdir(parents=True, exist_ok=True)
        state["phase"] = "sqlite_backup"
        state["total"] = 1
        await online_sqlite_backup(source, dest)
        state["done"] = 1
        if not dest.is_file() or dest.stat().st_size == 0:
            raise VolumeOperationError("迁移拷贝校验失败：目标文件为空")
        assignment = str(dest)
    else:
        dest = target_dir / source.name
        state["phase"] = "tree_copy"
        files = await asyncio.to_thread(walk_files, source)
        state["total"] = len(files)

        def _copy() -> None:
            dest.mkdir(parents=True, exist_ok=True)
            for index, path in enumerate(files):
                rel = path.relative_to(source)
                to = dest / rel
                to.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, to)
                state["done"] = index + 1

        if descriptor.kind is VolumeKind.COGNEE_TREE:
            await _cognee_quiescence(lambda: asyncio.to_thread(_copy))
        else:
            await asyncio.to_thread(_copy)
        if not any(dest.iterdir()):
            raise VolumeOperationError("迁移拷贝校验失败：目标目录为空")
        assignment = str(dest)

    registry.write_location(volume_id, assignment)
    log(
        f"存储卷 {volume_id} 迁移拷贝完成: {source} -> {assignment}（重启生效，"
        f"重启前的写入仍落旧位置）",
        tag="存储卷",
    )
    return {"assignment": assignment, "needs_restart": True}


# ----------------------------------------------------------------------
# 外部 SQL 导出 / 导入（快照语义）
# ----------------------------------------------------------------------


def _is_derived_table(name: str) -> bool:
    """派生表过滤：FTS5/vec0 影子表（由存储侧建表逻辑重建）。"""
    return any(pat in name for pat in _SHADOW_PATTERNS)


def export_sql(
    volume_id: str,
    connection_id: str,
    *,
    table_prefix: str = "",
    drop_existing: bool = True,
) -> Dict[str, Any]:
    """启动后台导出任务：SQLite 基表 → 外部 SQL（快照覆写 + 清单登记）。"""
    _require_capability(volume_id, VolumeCapability.EXPORT_SQL)
    _ensure_idle(volume_id)
    _ensure_not_pending(volume_id)
    _reset_state(volume_id, "export")
    _spawn(
        volume_id,
        lambda state: _run_export(volume_id, connection_id, table_prefix, drop_existing, state),
    )
    return operation_status(volume_id)


async def _run_export(
    volume_id: str,
    connection_id: str,
    table_prefix: str,
    drop_existing: bool,
    state: Dict[str, Any],
) -> Dict[str, Any]:
    from services.db_connections import SqlTransferClient, get_connection_store

    _descriptor(volume_id)
    source = _resolved_path(volume_id)
    if not source.is_file():
        raise VolumeOperationError(f"卷数据文件不存在: {source}", status_code=409)

    conn = await aiosqlite.connect(str(source))
    client = SqlTransferClient(get_connection_store().get(connection_id))
    exported: List[str] = []
    total_rows = 0
    try:
        await client.connect()
        tables = await _exportable_tables(conn)
        state["total"] = len(tables) + 1  # +1 为清单表
        for index, table in enumerate(tables):
            state["phase"] = f"table:{table}"
            rows = await _export_table(conn, client, table, table_prefix, drop_existing)
            exported.append(table)
            total_rows += rows
            state["done"] = index + 1
        state["phase"] = "manifest"
        await client.write_export_manifest(volume_id, table_prefix, exported)
        state["done"] = len(tables) + 1
    finally:
        await conn.close()
        await client.close()
    log(
        f"存储卷 {volume_id} 已导出到外部连接 {connection_id}: "
        f"{len(exported)} 表 {total_rows} 行（前缀 '{table_prefix}'）",
        tag="存储卷",
    )
    return {"tables": exported, "rows": total_rows, "prefix": table_prefix}


async def _exportable_tables(conn: aiosqlite.Connection) -> List[str]:
    from services.db_connections import EXPORT_MANIFEST_TABLE

    cursor = await conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table'")
    tables: List[str] = []
    for name, ddl in await cursor.fetchall():
        if name.startswith("sqlite_") or _is_derived_table(name) or name == EXPORT_MANIFEST_TABLE:
            continue
        if ddl and "VIRTUAL TABLE" in ddl.upper():
            continue  # FTS5 / vec0 派生索引：由存储侧建表逻辑重建
        tables.append(name)
    return sorted(tables)


async def _export_table(
    conn: aiosqlite.Connection,
    client: Any,
    table: str,
    prefix: str,
    drop_existing: bool,
) -> int:
    """单表导出：DDL 翻译建表 + rowid 窗口流式批量写入。"""
    columns = await _table_columns(conn, table)
    await client.create_table_from_sqlite(f"{prefix}{table}", columns, drop=drop_existing)
    insert_sql = client.insert_sql(f"{prefix}{table}", [c["name"] for c in columns])
    total = 0
    last_rowid = 0
    while True:
        cursor = await conn.execute(
            f'SELECT rowid, * FROM "{table}" WHERE rowid > ? ORDER BY rowid LIMIT {_BATCH_ROWS}',
            (last_rowid,),
        )
        rows = list(await cursor.fetchall())
        if not rows:
            break
        last_rowid = rows[-1][0]
        await client.executemany_rows(insert_sql, [tuple(r[1:]) for r in rows])
        total += len(rows)
    return total


async def _table_columns(conn: aiosqlite.Connection, table: str) -> List[Dict[str, Any]]:
    cursor = await conn.execute(f'PRAGMA table_info("{table}")')
    columns = []
    for _cid, name, decl, notnull, default, pk in await cursor.fetchall():
        columns.append({
            "name": name,
            "type": decl or "",
            "notnull": bool(notnull),
            "default": default,
            "pk": int(pk or 0),
        })
    return columns


def import_sql(volume_id: str, connection_id: str) -> Dict[str, Any]:
    """启动后台导入任务：外部 SQL（须含导出清单）→ 暂存 SQLite → 重启落盘。"""
    _require_capability(volume_id, VolumeCapability.IMPORT_SQL)
    _ensure_idle(volume_id)
    _ensure_not_pending(volume_id)
    _reset_state(volume_id, "import")
    _spawn(volume_id, lambda state: _run_import(volume_id, connection_id, state))
    return operation_status(volume_id)


async def _run_import(volume_id: str, connection_id: str, state: Dict[str, Any]) -> Dict[str, Any]:
    from services.db_connections import SqlTransferClient, get_connection_store

    descriptor = _descriptor(volume_id)
    client = SqlTransferClient(get_connection_store().get(connection_id))
    staging = _staging_dir()
    staging.mkdir(parents=True, exist_ok=True)
    staged = staging / f"{volume_id}-import-{time.strftime('%Y%m%d-%H%M%S')}.sqlite3"
    staged.unlink(missing_ok=True)
    try:
        await client.connect()
        manifest = await client.read_export_manifest()
        if not manifest.get("tables"):
            raise VolumeOperationError(
                "外部库中没有导出清单（仅接受由本系统导出产生的数据库）", status_code=409
            )
        prefix = str(manifest.get("prefix", "") or "")
        tables = [str(t) for t in manifest["tables"]]
        state["total"] = len(tables)
        for index, table in enumerate(tables):
            state["phase"] = f"table:{table}"
            await _import_table(client, staged, f"{prefix}{table}", table)
            state["done"] = index + 1
    finally:
        await client.close()

    _verify_sqlite(staged)
    from agent.storage.volume_restore import stage_restore_entry

    stage_restore_entry(volume_id, descriptor.kind, str(staged))
    log(f"存储卷 {volume_id} 已从外部连接 {connection_id} 导入并暂存，等待重启落盘", tag="存储卷")
    return {"staged": str(staged), "needs_restart": True}


async def _import_table(client: Any, staged: Path, ext_table: str, target_table: str) -> int:
    """外部表 → 暂存 SQLite 表（分页读取 + 批量插入）。"""
    columns = await client.list_columns(ext_table)
    decls = [f'"{c["name"]}" {client.sqlite_type(c["type"])}' for c in columns]
    pks = [c["name"] for c in columns if c.get("pk")]
    if pks:
        decls.append(f'PRIMARY KEY ({", ".join(chr(34) + c + chr(34) for c in pks)})')
    conn = sqlite3.connect(str(staged))
    try:
        conn.execute(f'CREATE TABLE IF NOT EXISTS "{target_table}" ({", ".join(decls)})')
        names = [c["name"] for c in columns]
        marks = ",".join("?" for _ in names)
        sql = f'INSERT INTO "{target_table}" ({",".join(chr(34) + n + chr(34) for n in names)}) VALUES ({marks})'
        total = 0
        offset = 0
        while True:
            rows = await client.fetch_table_page(ext_table, names, limit=_BATCH_ROWS, offset=offset)
            if not rows:
                break
            conn.executemany(sql, [_normalize_row(r) for r in rows])
            conn.commit()
            total += len(rows)
            offset += len(rows)
        return total
    finally:
        conn.close()


def _normalize_row(row: Tuple[Any, ...]) -> Tuple[Any, ...]:
    """asyncpg bytea 返回 memoryview，统一为 bytes 供 sqlite 绑定。"""
    return tuple(bytes(v) if isinstance(v, memoryview) else v for v in row)


def pending_restart_hint() -> Optional[str]:
    """有待落盘恢复时的提示文案键（供状态接口）。"""
    pending = _pending_volume_ids()
    return ", ".join(pending) if pending else None
