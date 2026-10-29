"""在受保护的工作区内校正搬迁后的 Cognee 原文位置，不修改内容或索引身份。

模型看到什么：沿用既有记忆与召回内容，不新增注入。
token 影响：仅修正本地元数据，不调用模型。
缓存影响：不改变提示词或稳定前缀。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from urllib.parse import unquote, urlsplit
from uuid import UUID

from core.log import log


def _local_path(location: str) -> PurePath | None:
    """解析迁移前平台的绝对路径；不把远端 URL 或路径穿越当作受管文件。"""
    if PureWindowsPath(location).is_absolute():
        path: PurePath = PureWindowsPath(location)
    elif PurePosixPath(location).is_absolute():
        path = PurePosixPath(location)
    else:
        uri = urlsplit(location)
        if uri.scheme != "file" or uri.query or uri.fragment:
            return None
        value = unquote(uri.path)
        if uri.netloc and uri.netloc != "localhost":
            value = f"//{uri.netloc}{value}"
        elif len(value) > 3 and value[0] == "/" and value[2] == ":":
            value = value[1:]
        path = PureWindowsPath(value) if PureWindowsPath(value).drive else PurePosixPath(value)
    return path if path.is_absolute() and ".." not in path.parts else None


def _data_root(location: str, owner_id: str) -> PurePath | None:
    """只按 Cognee 的 data/owner 布局识别旧目录，不按文件名搜索。"""
    path = _local_path(location)
    if path is None:
        return None
    try:
        owner = UUID(owner_id)
    except ValueError:
        return None
    for index in reversed(range(len(path.parts) - 2)):
        if path.parts[index] != "data":
            continue
        try:
            matches_owner = UUID(path.parts[index + 1]) == owner
        except ValueError:
            continue
        if not matches_owner:
            continue
        return type(path)(*path.parts[:index + 1])
    return None


def _relocated_location(location: str, previous: PurePath, managed: Path) -> str:
    """只重定位同一受管目录内的文件；原始外部文档和远端 URL 保持原样。"""
    path = _local_path(location)
    if path is None or not path.is_relative_to(previous):
        return location
    target = managed / Path(*path.relative_to(previous).parts)
    if not target.resolve().is_relative_to(managed):
        raise RuntimeError("Cognee 迁移原文路径越出受管数据目录；原数据保留")
    relocated = target.as_uri()
    if relocated != location and not target.is_file():
        raise FileNotFoundError(f"Cognee 迁移后的原文缺失：{target}；请检查迁移是否完整，原数据保留")
    return relocated


def relocate_sources(root: Path) -> int:
    """SDK 打开前原子校正本地元数据；调用者持有工作区独占锁及快照保护。"""
    database = root / "system/databases/cognee_db"
    if not database.is_file():
        return 0
    managed = root.resolve() / "data"
    connection = sqlite3.connect(database.absolute().as_uri() + "?mode=rw", uri=True)
    changed = 0
    try:
        connection.execute("BEGIN IMMEDIATE")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(data)")}
        if not columns:
            return 0
        if not {"id", "owner_id", "raw_data_location", "original_data_location"} <= columns:
            raise RuntimeError("Cognee 原文元数据结构不兼容，未修改迁移数据")
        rows = connection.execute("SELECT id, owner_id, raw_data_location, original_data_location FROM data")
        for identity, owner, raw, original in rows:
            previous = _data_root(raw, str(owner)) if raw else None
            if previous is None:
                continue
            new_raw = _relocated_location(raw, previous, managed)
            new_original = original
            if original:
                new_original = new_raw if original == raw else _relocated_location(original, previous, managed)
            if (new_raw, new_original) != (raw, original):
                connection.execute("UPDATE data SET raw_data_location=?, original_data_location=? WHERE id=?",
                                   (new_raw, new_original, identity))
                changed += 1
        connection.commit()
    finally:
        connection.close()
    if changed:
        log(f"Cognee 已校正 {changed} 条迁移文档的本地原文路径", "INFO", tag="记忆")
    return changed
