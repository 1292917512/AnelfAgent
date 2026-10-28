"""Detect an existing upstream and switch only explicitly selected, known mirrors."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import TypedDict
from urllib.parse import urlsplit

from app.processes import background_options
from app.settings import REPOSITORY_SOURCES


class RepositoryInfo(TypedDict):
    origin: str
    source: str
    switchable: bool


def identity(url: str) -> tuple[str, str]:
    if "://" not in url and re.match(r"[^/]+@[^/]+:", url):
        host, repository = url.split("@", 1)[1].split(":", 1)
    else:
        parsed = urlsplit(url)
        host, repository = parsed.hostname or "", parsed.path
    return host.lower(), repository.strip("/").removesuffix(".git").lower()


def origin(root: Path, git: str | None) -> str:
    """Query Git itself, including linked checkouts and include rules, without network I/O."""
    if not git or not (root / ".git").exists():
        return ""
    try:
        result = subprocess.run([git, "--no-optional-locks", "-C", str(root), "remote", "get-url", "origin"],
                                capture_output=True, text=True, encoding="utf-8", timeout=10, **background_options())
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Unable to inspect the project's Git origin; check Git availability") from exc
    if result.returncode not in {0, 2}:
        raise RuntimeError("Unable to inspect the project's Git origin")
    return result.stdout.strip() if result.returncode == 0 else ""


def describe(url: str) -> RepositoryInfo:
    host, _repository = identity(url)
    source = "github" if host == "github.com" else "gitee" if host == "gitee.com" else "custom" if url else ""
    # Never send credentials, query strings or fragments to the renderer.
    public = url
    if "://" in url:
        parsed = urlsplit(url)
        public = f"{parsed.scheme}://{parsed.netloc.rsplit('@', 1)[-1]}{parsed.path}"
    return {"origin": public, "source": source,
            "switchable": not url or identity(url) in {identity(value) for value in REPOSITORY_SOURCES.values()}}


def selected_origin(current: str, source: str) -> str:
    if source == "auto":
        return current
    if source not in REPOSITORY_SOURCES:
        raise ValueError("Unknown repository source")
    description = describe(current)
    if not description["switchable"]:
        if description["source"] == source:
            return current
        raise ValueError("当前是自定义仓库，无法推断另一平台的镜像。请沿用原仓库，或先通过 Git 配置对应 origin。")
    return str(REPOSITORY_SOURCES[source])


def set_source(root: Path, git: str | None, source: str) -> RepositoryInfo:
    """Keep custom origins by default; explicit official mirror changes preserve branches and files."""
    if not (root / ".git").exists():
        return describe("")
    if not git and source == "auto":
        return describe("")
    if not git:
        raise RuntimeError("Git is required to inspect or change the project's repository")
    current = origin(root, git)
    target = selected_origin(current, source)
    change_origin(root, git, current, target)
    return describe(target)


def change_origin(root: Path, git: str, current: str, target: str) -> None:
    if target == current:
        return
    operation = (["remove", "origin"] if not target else
                 ["set-url", "origin", target] if current else ["add", "origin", target])
    try:
        result = subprocess.run([git, "-C", str(root), "remote", *operation], capture_output=True,
                                text=True, encoding="utf-8", timeout=10, **background_options())
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Unable to change Git origin; check Git availability") from exc
    if result.returncode:
        raise RuntimeError("Unable to change Git origin; project files were preserved")
