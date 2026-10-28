"""Move a stopped checkout transactionally, preserving user files and Git state.

The desktop requests shutdown separately, after user confirmation. This worker never
kills a service. A durable journal recovers interrupted commits before reconnecting.
"""

from __future__ import annotations

import configparser
import errno
import hashlib
import json
import os
import shutil
import stat
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable

from app.manager import pid_alive
from app.migration_links import Link, create_link, environment_links, plan_link, restore_links
from core.host_protocol import HostLock, write_json

Report = Callable[[str, int, int], None]
_LOCK_FILES = {"logs/project-operation.lock", "logs/manager/manager.lock", "logs/anelf.pid.lock"}


def validate_project(root: Path, *, moving: bool = False) -> Path:
    """Accept existing checkouts; linked Git worktrees may be used only in place."""
    root = root.absolute()
    if not all((root / name).is_file() for name in ("pyproject.toml", "uv.lock", "launch.py")):
        raise ValueError("请选择包含 pyproject.toml、uv.lock 和 launch.py 的 AnelfAgent 项目目录。")
    if not (root / ".git").exists():
        raise ValueError("所选目录不是 Git 项目。")
    if moving:
        if root.is_symlink() or root.is_junction() or not (root / ".git").is_dir():
            raise ValueError("链接目录或 Git worktree 请取消迁移勾选，直接使用原目录。")
        _validate_git_directory(root / ".git", root)
    return root.resolve()


def _validate_git_directory(directory: Path, root: Path) -> None:
    for name in ("worktrees", "objects/info/alternates"):
        item = directory / name
        if item.exists() and (any(item.iterdir()) if item.is_dir() else item.stat().st_size):
            raise ValueError(f"Git 关联尚不能独立迁移：{item.relative_to(root)}。请保留原目录使用；更换下载源不会修复此结构。")


def validate_git_link(item: Path, root: Path) -> None:
    """Relative submodule links wholly inside the moved tree remain valid after relocation."""
    value = item.read_text("utf-8").strip()
    if not value.startswith("gitdir:"):
        return
    reference = Path(value[7:].strip())
    directory = (item.parent / reference).resolve()
    if reference.is_absolute() or not directory.is_relative_to(root.resolve()) or not directory.is_dir():
        raise ValueError(f"Git 链接指向项目外部或绝对路径：{item.relative_to(root)}。请保留原目录使用。")
    common = directory / "commondir"
    if common.exists():
        raise ValueError(f"Git linked worktree 需要单独迁移：{item.relative_to(root)}。请保留原目录使用。")
    config = configparser.RawConfigParser(strict=False, allow_no_value=True)
    try:
        config.read(directory / "config", encoding="utf-8")
        worktree = config.get("core", "worktree", fallback="") or ""
        if worktree.startswith('"'):
            worktree = str(json.loads(worktree))
    except (configparser.Error, ValueError) as exc:
        raise ValueError(f"无法验证 Git 工作路径：{item.relative_to(root)}。请保留原目录使用。") from exc
    if worktree and (Path(worktree).is_absolute() or (directory / worktree).resolve() != item.parent.resolve()):
        raise ValueError(f"Git 工作路径不能随目录移动：{item.relative_to(root)}。请保留原目录使用。")
    _validate_git_directory(directory, root)


def _stopped(root: Path) -> None:
    for name in ("logs/anelf.pid", "logs/anelf_guardian.pid", "logs/manager/runtime.json", "logs/manager/connection.json"):
        item = root / name
        if item.exists():
            raw = item.read_text("utf-8")
            pid = json.loads(raw).get("pid", 0) if item.suffix == ".json" else int(raw.strip() or "0")
            if isinstance(pid, int) and pid_alive(pid):
                raise RuntimeError("项目服务仍在运行，迁移未执行。请先安全关闭服务。")


def _inventory(root: Path) -> dict[str, tuple[int, int, int, str]]:
    result: dict[str, tuple[int, int, int, str]] = {}
    def unreadable(error: OSError) -> None:
        raise error

    for parent, folders, files in os.walk(root, followlinks=False, onerror=unreadable):
        for name in [*folders, *files]:
            item = Path(parent) / name
            if item.relative_to(root).as_posix() in _LOCK_FILES:
                continue
            info = item.lstat()
            link = item.is_symlink() or item.is_junction()
            if not (link or stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise ValueError(f"无法安全迁移特殊文件：{item}")
            if name == ".git":
                if item.is_file():
                    validate_git_link(item, root)
                elif item.is_dir():
                    _validate_git_directory(item, root)
            result[item.relative_to(root).as_posix()] = (
                info.st_mode, info.st_size, info.st_mtime_ns, os.readlink(item) if link else "",
            )
            if link and name in folders:
                folders.remove(name)
    return result


def _digest(file: Path, check: Callable[[], None]) -> str:
    digest = hashlib.sha256()
    with file.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            check()
            digest.update(chunk)
    return digest.hexdigest()


def _remove_tree(root: Path) -> None:
    """Remove a verified temporary tree, including Windows read-only Git objects."""
    def writable(function: Callable[..., Any], name: str, error: BaseException) -> None:
        if os.name != "nt" or not isinstance(error, PermissionError):
            raise error
        os.chmod(name, stat.S_IWRITE)
        function(name)

    shutil.rmtree(root, onexc=writable)


def _preferences(data: Path, target: Path) -> None:
    file = data / "bootstrap.json"
    value = json.loads(file.read_text("utf-8")) if file.exists() else {}
    value.update(root=str(target), configured=False, projectChoice="existing")
    write_json(file, value)
    file = data / "projects.json"
    entries = json.loads(file.read_text("utf-8")) if file.exists() else []
    entries = [entry for entry in entries if Path(entry["root"]).resolve() != target]
    entries.append({"root": str(target), "realRoot": str(target), "owned": False, "id": uuid.uuid4().hex})
    write_json(file, entries)


def _prepare_target(target: Path, source: Path, token: str, links: dict[str, Link | str] | None = None) -> None:
    state = target / "logs/manager"
    saved = state / "migration-backups" / token
    venv = target / ".venv"
    if venv.exists():
        saved.mkdir(parents=True, exist_ok=True)
        venv.rename(saved / "venv")
    restore_links(target, environment_links(target, saved / "venv", links or {})
                  if (saved / "venv").exists() else links or {}, token)
    # An imported project never inherits another installation's deletion ownership.
    ownership = state / "ownership.json"
    if ownership.exists():
        saved.mkdir(parents=True, exist_ok=True)
        ownership.rename(saved / "ownership.json")
    write_json(state / "installation.json", {"ready": False})
    write_json(state / "migration.json", {"id": token, "source": str(source),
               "environment_backup": str((saved / "venv").relative_to(target)) if (saved / "venv").exists() else ""})


def recover(data: Path) -> str | None:
    """Complete a published move, or restore its original directory after interruption."""
    file = data / "migration.json"
    if not file.exists():
        return None
    record = json.loads(file.read_text("utf-8"))
    source, target, staging, backup = (Path(record[key]) for key in ("source", "target", "staging", "backup"))
    token = record["id"]
    if (staging.parent != target.parent or backup.parent != source.parent
            or staging.name != f".anelf-transfer-{token}" or backup.name != f".anelf-original-{token}"
            or source == target or source in target.parents or target in source.parents):
        raise RuntimeError("迁移记录路径异常，请保留文件并检查。")
    marker = target / "logs/manager/migration.json"
    published = marker.is_file() and json.loads(marker.read_text("utf-8")).get("id") == token
    if not published and (record.get("mode") == "rename" or record.get("verified")) and target.is_dir() and not source.exists():
        info = target.stat()
        if [info.st_dev, info.st_ino] != record.get("identity"):
            raise RuntimeError("迁移目标发生变化，未执行恢复或删除。")
        _prepare_target(target, source, token, record.get("links"))
        published = True
    if published:
        _preferences(data, target)
        if backup.exists():
            _remove_tree(backup)
        file.unlink()
        return str(target)
    if backup.exists():
        if source.exists():
            raise RuntimeError(f"原路径被重新创建；原项目保留在 {backup}，请检查后重试。")
        backup.rename(source)
    if staging.exists():
        _remove_tree(staging)
    file.unlink()
    return None


def migrate(source: Path, target: Path, data: Path, report: Report, cancel: Path) -> str:
    """Rename on one volume; copy and verify only when crossing filesystem boundaries."""
    data = data.resolve()
    recovered = recover(data)
    if recovered:
        return recovered
    source = validate_project(source, moving=True)
    target = target.resolve()
    if (source == target or source in target.parents or target in source.parents or source == data
            or source in data.parents or target == data or target in data.parents):
        raise ValueError("原目录与目标目录不能相同、互相包含或覆盖 APP 数据目录。")
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ValueError("目标目录已有内容，请选择空目录；不会合并或覆盖现有文件。")
    _stopped(source)
    locks = [HostLock(source / name) for name in (
        "logs/project-operation.lock", "logs/manager/manager.lock", "logs/anelf.pid.lock",
    )]
    token = uuid.uuid4().hex
    staging, backup = target.parent / f".anelf-transfer-{token}", source.parent / f".anelf-original-{token}"
    journal = data / "migration.json"
    record: dict[str, Any] = {"id": token, "source": str(source), "target": str(target),
                              "staging": str(staging), "backup": str(backup), "mode": "copy"}

    def check() -> None:
        if cancel.exists():
            raise RuntimeError("Cancelled: 迁移已取消，原项目保留在原目录。")

    try:
        for lock in locks:
            if not lock.acquire():
                raise RuntimeError("项目正在启动或执行操作，迁移未执行。")
        _stopped(source)
        check()
        report("正在检查项目文件", 0, 0)
        target.parent.mkdir(parents=True, exist_ok=True)
        before = _inventory(source)
        links = {name: plan_link(source, target, name, link) for name, (_mode, _size, _time, link) in before.items() if link}
        record["links"] = links
        info = source.stat()
        if info.st_dev == target.parent.stat().st_dev:
            if links:
                report("正在检查目录联接与符号链接", 0, 0)
                with tempfile.TemporaryDirectory(prefix=".anelf-links-", dir=target.parent) as probe:
                    for index, link_plan in enumerate(links.values()):
                        check()
                        create_link(Path(probe) / str(index), link_plan, original=True)
            report("同盘快速移动 · 不复制项目文件", 0, 0)
            record.update(mode="rename", identity=[info.st_dev, info.st_ino])
            write_json(journal, record)
            check()
            for lock in locks:
                lock.release()
            _stopped(source)
            if target.exists():
                target.rmdir()
            try:
                source.rename(target)
            except OSError as exc:
                if exc.errno != errno.EXDEV:
                    raise
                journal.unlink()
                record["mode"] = "copy"
                for lock in locks:
                    if not lock.acquire():
                        raise RuntimeError("项目正在执行操作，迁移未执行。") from exc
            else:
                recover(data)
                report("快速移动完成，接下来检查运行环境", 1, 1)
                return str(target)
        report("跨盘迁移 · 正在统计文件大小", 0, 0)
        total = sum(size for mode, size, _mtime, link in before.values() if stat.S_ISREG(mode) and not link)
        if shutil.disk_usage(target.parent).free < total + 512 * 1024**2:
            raise ValueError("目标磁盘空间不足，需要容纳完整项目并预留 512 MiB。")
        data.mkdir(parents=True, exist_ok=True)
        write_json(journal, record)
        staging.mkdir()
        for relative, (mode, _size, _mtime, link) in before.items():
            check()
            if stat.S_ISDIR(mode) and not link:
                (staging / relative).mkdir(exist_ok=True)
        report("正在检查目录联接与符号链接", 0, 0)
        for relative, link_plan in links.items():
            check()
            create_link(staging / relative, link_plan, original=True)
        done = 0
        hashes: dict[str, str] = {}
        for relative, (mode, _size, _mtime, link) in before.items():
            check()
            old, new = source / relative, staging / relative
            if not link and not stat.S_ISDIR(mode):
                digest = hashlib.sha256()
                with old.open("rb") as reader, new.open("xb") as writer:
                    while chunk := reader.read(1024 * 1024):
                        check()
                        writer.write(chunk)
                        digest.update(chunk)
                        done += len(chunk)
                        report(f"正在迁移 · {relative}", done, total)
                shutil.copystat(old, new)
                hashes[relative] = digest.hexdigest()
        report("正在校验文件完整性", 0, total)
        verified = 0
        for relative, expected in hashes.items():
            check()
            if _digest(staging / relative, check) != expected or _digest(source / relative, check) != expected:
                raise RuntimeError(f"文件在迁移期间发生变化或校验失败：{relative}；原项目保持不变。")
            verified += before[relative][1]
            report("正在校验文件完整性", verified, total)
        if _inventory(source) != before:
            raise RuntimeError("项目在迁移期间发生变化，已停止迁移；请关闭相关程序后重试。")
        for relative, (mode, _size, _mtime, link) in reversed(before.items()):
            if stat.S_ISDIR(mode) and not link:
                shutil.copystat(source / relative, staging / relative)
        info = staging.stat()
        record.update(verified=True, identity=[info.st_dev, info.st_ino])
        write_json(journal, record)
        check()
        _stopped(source)
        # Windows cannot rename directories containing our open lock handles.
        for lock in locks:
            lock.release()
        report("正在切换项目目录", done, total)
        source.rename(backup)
        if target.exists():
            target.rmdir()
        staging.rename(target)
        recover(data)
        report("项目迁移完成，接下来检查运行环境", total, total)
        return str(target)
    except BaseException:
        for lock in locks:
            lock.release()
        if journal.exists():
            recover(data)
        raise
    finally:
        for lock in locks:
            lock.release()
