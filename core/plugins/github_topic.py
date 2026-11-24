"""以 GitHub Topic 作为公开仓库发现源，刷新成功后原子发布本地市场目录。"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from core.file_utils import atomic_write_text
from core.network import network_client
from core.plugins.manifest import PluginError, find_manifest_file

_TOPIC = re.compile(r"/topics/([a-z0-9][a-z0-9-]{0,49})/?")
_REPO = re.compile(r"[\w.-]+/[\w.-]+", re.ASCII)


def topic_from_url(source: str) -> str:
    parsed = urlsplit(source)
    match = _TOPIC.fullmatch(parsed.path)
    return match[1] if parsed.netloc.lower() == "github.com" and parsed.scheme == "https" and match else ""


def refresh_topic(name: str, url: str, directory: Path) -> None:
    """缓存按星数排序的首批 100 个仓库；GitHub 限流时保留最近成功的目录。"""
    try:
        with network_client(timeout=20) as client:
            response = client.get("https://api.github.com/search/repositories", params={
                "q": f"topic:{topic_from_url(url)} archived:false", "sort": "stars", "per_page": 100,
            }, headers={"Accept": "application/vnd.github+json"})
            response.raise_for_status()
            items = response.json()["items"]
        if not isinstance(items, list):
            raise ValueError("items 应为数组")
        entries = []
        for item in items:
            repo = item["full_name"]
            if not isinstance(repo, str) or not _REPO.fullmatch(repo):
                raise ValueError("无效的 GitHub 仓库名")
            entries.append({"name": repo, "description": item.get("description") or "",
                            "source": {"source": "github", "repo": repo}})
        atomic_write_text(directory / "marketplace.json", json.dumps({"name": name, "plugins": entries}))
    except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError) as exc:
        raise PluginError(f"GitHub Topic 目录刷新失败: {exc}") from exc


def locate_topic_package(root: Path) -> Path:
    """仓库根或唯一的 DSH 子包可直接安装；歧义时返回可供直接安装的子目录。"""
    if find_manifest_file(root):
        return root
    candidates: list[Path] = []
    for parent, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "node_modules"]
        folder = Path(parent)
        if folder == root or "package.json" not in files:
            continue
        try:
            manifest = find_manifest_file(folder)
        except PluginError:
            continue
        if manifest and manifest.name == "package.json":
            candidates.append(folder)
    if len(candidates) == 1:
        return candidates[0]
    paths = ", ".join(sorted(path.relative_to(root).as_posix() for path in candidates)[:12])
    if paths:
        raise PluginError(f"仓库含多个 DSH 插件，请从来源安装并指定子目录: {paths}")
    raise PluginError("该主题仓库没有可识别的插件清单，请选择包含插件清单的仓库或子目录")
