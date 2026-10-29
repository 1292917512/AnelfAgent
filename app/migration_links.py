"""Relocate filesystem links without copying or deleting their external targets."""

from __future__ import annotations

import ntpath
import os
import posixpath
import stat
from pathlib import Path, PurePath, PureWindowsPath
from typing import TypedDict

from app.windows_junction import create_junction


class Link(TypedDict):
    target: str
    original: str
    junction: bool
    directory: bool


def plain_path(path: PurePath) -> PurePath:
    """Compare Windows drive/UNC paths consistently with readlink's extended spelling."""
    value = str(path)
    if isinstance(path, PureWindowsPath):
        if value[:8].lower() == "\\\\?\\unc\\":
            return PureWindowsPath("\\\\" + value[8:])
        if value.startswith("\\\\?\\") and len(value) > 5 and value[5] == ":":
            return PureWindowsPath(value[4:])
    return path


def relocated_target(source: PurePath, target: PurePath, name: str, resolved: PurePath, *, junction: bool) -> str:
    source, target, resolved = (plain_path(item) for item in (source, target, resolved))
    destination = target / resolved.relative_to(source) if resolved.is_relative_to(source) else resolved
    return relative_target(destination, (target / name).parent, junction=junction)


def relative_target(destination: PurePath, parent: PurePath, *, junction: bool) -> str:
    if junction:
        return str(destination)
    paths = ntpath if isinstance(parent, PureWindowsPath) else posixpath
    try:
        return paths.relpath(str(destination), str(parent))
    except ValueError:
        # Windows has no relative path across drives or UNC shares.
        return str(destination)


def environment_links(root: Path, backup: Path, links: dict[str, Link | str]) -> dict[str, Link | str]:
    """Retain links into and within the old virtual environment at its backup location."""
    environment = root / ".venv"
    result: dict[str, Link | str] = {}
    for name, value in links.items():
        old = root / name
        item = backup / old.relative_to(environment) if old.is_relative_to(environment) else old
        link: Link = ({"target": value, "original": "", "junction": False, "directory": item.is_dir()}
                      if isinstance(value, str) else value)
        destination = Path(plain_path(Path(os.path.abspath(old.parent / link["target"]))))
        if destination.is_relative_to(environment):
            destination = backup / destination.relative_to(environment)
        result[item.relative_to(root).as_posix()] = {
            **link, "target": relative_target(destination, item.parent, junction=link["junction"]),
        }
    return result


def plan_link(source: Path, target: Path, name: str, raw: str) -> Link:
    item = source / name
    # Keep the direct target, including missing paths and chains; resolving would
    # flatten junctions and can fail on cycles or temporarily unavailable targets.
    resolved = plain_path(Path(os.path.abspath(item.parent / raw)))
    info = item.lstat()
    junction = item.is_junction()
    directory = item.is_dir() or bool(getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_DIRECTORY)
    return {"target": relocated_target(source, target, name, resolved, junction=junction),
            "original": str(resolved), "junction": junction, "directory": directory}


def create_link(item: Path, link: Link, *, original: bool = False) -> None:
    destination = link["original"] if original else link["target"]
    try:
        if os.name == "nt" and link["junction"]:
            create_junction(item, destination)
        else:
            item.symlink_to(destination, target_is_directory=link["directory"])
    except OSError as exc:
        raise RuntimeError(f"无法创建迁移链接：{item} → {destination}。{exc}") from exc


def restore_links(root: Path, links: dict[str, Link | str], token: str) -> None:
    """Replay journaled link replacements after directory publication, including interrupted ones."""
    for name, value in links.items():
        item = root / name
        link: Link = ({"target": value, "original": "", "junction": False, "directory": item.is_dir()}
                      if isinstance(value, str) else value)
        if item.is_symlink() or item.is_junction():
            if item.is_junction() == link["junction"] and plain_path(Path(os.readlink(item))) == plain_path(Path(link["target"])):
                continue
        elif os.path.lexists(item):
            raise RuntimeError(f"迁移链接位置被其他文件占用，文件已保留：{item}")
        temporary = item.with_name(item.name + ".anelf-link-" + token)
        if not os.path.lexists(temporary):
            create_link(temporary, link)
        elif not (temporary.is_symlink() or temporary.is_junction()) or plain_path(Path(os.readlink(temporary))) != plain_path(Path(link["target"])):
            raise RuntimeError(f"迁移临时链接发生变化，文件已保留：{temporary}")
        # Windows cannot replace a junction with rename. The durable plan permits
        # recovery between removing the old link and publishing its replacement.
        if item.is_junction():
            item.rmdir()
        elif item.is_symlink():
            item.unlink()
        temporary.rename(item)
