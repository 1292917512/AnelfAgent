"""插件静态预检。仅投影清单中的组件和运行需求，不加载插件代码。"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.plugins.manifest import MarketplacePlugin, PluginError, PluginManifest, load_plugin_mcp_servers


class SourceUnavailable(PluginError):
    """未取得来源负载，无法判断其格式。"""


@dataclass(frozen=True)
class PreparedPlugin:
    root: Path
    sha: str
    subdir: str
    manifest: PluginManifest

    def revision(self, entry: MarketplacePlugin) -> str:
        """绑定来源快照、选定子目录与市场内联声明。"""
        identity = [self.sha, self.subdir, self.manifest.name, entry.inline_manifest]
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode())
        if entry.source_type == "local":
            for file in sorted(p for p in self.root.rglob("*") if p.is_file()):
                relative = file.relative_to(self.root).as_posix().encode()
                digest.update(len(relative).to_bytes(8, "big"))
                digest.update(relative)
                # 旧本地指纹已覆盖内容；显式边界可区分路径与内容的不同划分。
                digest.update(file.stat().st_size.to_bytes(8, "big"))
        return digest.hexdigest()


def skill_directories(root: Path) -> list[Path]:
    """返回该技能入口实际包含的技能目录，供预检与激活共用。"""
    if (root / "SKILL.md").is_file():
        return [root]
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if (p / "SKILL.md").is_file())


def inspect_payload(root: Path, revision: str, manifest: PluginManifest) -> dict[str, Any]:
    servers = load_plugin_mcp_servers(root, manifest)
    return {
        "status": "verified", "name": manifest.name, "format": manifest.format,
        "version": manifest.version, "revision": revision, "requirements": manifest.requirements,
        "skills": [p.name for rel in manifest.skills for p in skill_directories(root / rel)],
        "commands": sorted(p.stem for p in (root / manifest.commands_dir).glob("*.md"))
        if manifest.commands_dir else [],
        "tools_file": Path(manifest.tools_file).as_posix() if manifest.tools_file else "", "mcp_servers": sorted(servers),
        "host_reason": manifest.host_reason,
    }
