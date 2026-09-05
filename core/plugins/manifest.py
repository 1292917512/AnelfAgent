"""插件清单与市场目录解析。

清单发现顺序（先命中先生效）：
    plugin.json（根目录）
    .codex-plugin/plugin.json
    .claude-plugin/plugin.json
    .cursor-plugin/plugin.json

组件约定：
- skills：manifest ``skills`` 字段（字符串或数组），缺省 ``./skills``
- mcpServers：manifest ``mcpServers`` 字段（路径或内联对象），缺省 ``./.mcp.json`` / ``./mcp.json``
- tools：插件根存在 ``tools.py`` 即作为工具模块加载（经 entities._sdk @tool 注册）
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

_MANIFEST_CANDIDATES = (
    "plugin.json",
    ".codex-plugin/plugin.json",
    ".claude-plugin/plugin.json",
    ".cursor-plugin/plugin.json",
)

_MCP_CONFIG_CANDIDATES = (".mcp.json", "mcp.json")

_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class PluginError(ValueError):
    """插件清单/市场目录非法或插件操作失败。"""


def validate_plugin_name(name: str) -> str:
    """校验插件标识（小写字母/数字开头，可含 . _ -，最长 64 字符）。"""
    if not _NAME_PATTERN.match(name or ""):
        raise PluginError(f"插件名非法: {name!r}（须为小写字母/数字开头，可含 . _ -）")
    return name


@dataclass
class PluginInterface:
    """插件 UI 展示元数据（manifest ``interface`` 块）。"""

    display_name: str = ""
    short_description: str = ""
    long_description: str = ""
    developer_name: str = ""
    category: str = ""
    capabilities: List[str] = field(default_factory=list)
    brand_color: str = ""
    default_prompt: List[str] = field(default_factory=list)
    logo: str = ""

    @classmethod
    def from_dict(cls, data: Any) -> "PluginInterface":
        if not isinstance(data, dict):
            return cls()
        prompts = data.get("defaultPrompt") or []
        if isinstance(prompts, str):
            prompts = [prompts]
        return cls(
            display_name=str(data.get("displayName") or ""),
            short_description=str(data.get("shortDescription") or ""),
            long_description=str(data.get("longDescription") or ""),
            developer_name=str(data.get("developerName") or ""),
            category=str(data.get("category") or ""),
            capabilities=[str(c) for c in data.get("capabilities") or []],
            brand_color=str(data.get("brandColor") or ""),
            default_prompt=[str(p)[:128] for p in prompts][:3],
            logo=str(data.get("logo") or data.get("composerIcon") or ""),
        )


@dataclass
class PluginManifest:
    """插件清单：身份元数据 + 组件声明（路径已解析为相对插件根的字符串）。"""

    name: str
    version: str = ""
    description: str = ""
    author: str = ""
    homepage: str = ""
    repository: str = ""
    license: str = ""
    keywords: List[str] = field(default_factory=list)
    interface: PluginInterface = field(default_factory=PluginInterface)
    skills: List[str] = field(default_factory=list)
    mcp_servers_file: str = ""
    mcp_servers_inline: Dict[str, Any] = field(default_factory=dict)
    tools_file: str = ""
    commands_dir: str = ""

    @property
    def display_name(self) -> str:
        """UI 展示名（interface.displayName 缺省回退插件名）。"""
        return self.interface.display_name or self.name


def find_manifest_file(root: Path) -> Optional[Path]:
    """按约定的多点发现顺序定位插件清单文件，不存在返回 None。"""
    for rel in _MANIFEST_CANDIDATES:
        candidate = root / rel
        if candidate.is_file():
            return candidate
    return None


def _resolve_component_path(root: Path, raw: Any) -> str:
    """将 manifest 中的组件路径字段规整为相对插件根的路径（拒绝越界与绝对路径）。"""
    if not isinstance(raw, str) or not raw.strip():
        return ""
    rel = raw.strip()
    resolved = (root / rel).resolve()
    root_resolved = root.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise PluginError(f"组件路径越出插件根目录: {raw}")
    return rel


def parse_manifest(root: Path) -> PluginManifest:
    """解析插件根目录的清单，缺失或非法时抛 PluginError。"""
    manifest_file = find_manifest_file(root)
    if manifest_file is None:
        raise PluginError(f"未找到插件清单: {root}（需要 plugin.json 或 .codex-plugin/plugin.json）")
    try:
        data = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise PluginError(f"插件清单解析失败: {manifest_file} - {e}") from e
    if not isinstance(data, dict):
        raise PluginError(f"插件清单必须是 JSON 对象: {manifest_file}")

    name = validate_plugin_name(str(data.get("name") or root.name))

    author_raw = data.get("author")
    if isinstance(author_raw, dict):
        author = str(author_raw.get("name") or "")
    else:
        author = str(author_raw or "")

    skills_raw = data.get("skills")
    if isinstance(skills_raw, str):
        skills = [skills_raw]
    elif isinstance(skills_raw, list):
        skills = [s for s in skills_raw if isinstance(s, str)]
    else:
        skills = []
    skills = [_resolve_component_path(root, s) for s in skills]
    skills = [s for s in skills if s]
    if not skills and (root / "skills").is_dir():
        skills = ["./skills"]

    mcp_file = ""
    mcp_inline: Dict[str, Any] = {}
    mcp_raw = data.get("mcpServers")
    if isinstance(mcp_raw, str):
        mcp_file = _resolve_component_path(root, mcp_raw)
    elif isinstance(mcp_raw, dict):
        mcp_inline = mcp_raw.get("mcpServers", mcp_raw)
        if not isinstance(mcp_inline, dict):
            raise PluginError("mcpServers 内联声明必须是对象")
    if not mcp_file and not mcp_inline:
        for rel in _MCP_CONFIG_CANDIDATES:
            if (root / rel).is_file():
                mcp_file = rel
                break

    tools_file = ""
    tools_raw = data.get("tools")
    if isinstance(tools_raw, str):
        tools_file = _resolve_component_path(root, tools_raw)
    elif (root / "tools.py").is_file():
        tools_file = "./tools.py"

    commands_dir = ""
    commands_raw = data.get("commands")
    if isinstance(commands_raw, str):
        commands_dir = _resolve_component_path(root, commands_raw)
    elif (root / "commands").is_dir():
        commands_dir = "./commands"

    return PluginManifest(
        name=name,
        version=str(data.get("version") or ""),
        description=str(data.get("description") or ""),
        author=author,
        homepage=str(data.get("homepage") or ""),
        repository=str(data.get("repository") or ""),
        license=str(data.get("license") or ""),
        keywords=[str(k) for k in data.get("keywords") or []],
        interface=PluginInterface.from_dict(data.get("interface")),
        skills=skills,
        mcp_servers_file=mcp_file,
        mcp_servers_inline=dict(mcp_inline),
        tools_file=tools_file,
        commands_dir=commands_dir,
    )


def load_plugin_mcp_servers(root: Path, manifest: PluginManifest) -> Dict[str, Dict[str, Any]]:
    """读取插件声明的 MCP server 配置（内联优先，其次引用的 JSON 文件）。"""
    if manifest.mcp_servers_inline:
        return {
            str(k): v for k, v in manifest.mcp_servers_inline.items() if isinstance(v, dict)
        }
    if not manifest.mcp_servers_file:
        return {}
    mcp_path = root / manifest.mcp_servers_file
    try:
        data = json.loads(mcp_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise PluginError(f"插件 MCP 配置解析失败: {mcp_path} - {e}") from e
    servers = data.get("mcpServers", data) if isinstance(data, dict) else {}
    if not isinstance(servers, dict):
        raise PluginError(f"插件 MCP 配置必须是对象: {mcp_path}")
    return {str(k): v for k, v in servers.items() if isinstance(v, dict)}


@dataclass
class MarketplacePlugin:
    """市场目录中的插件条目。"""

    name: str
    source_type: str            # local | git
    path: str = ""              # local：相对市场根的路径
    url: str = ""               # git：仓库地址
    ref: str = ""               # git：分支/tag/sha
    subdir: str = ""            # git：仓库内插件子目录
    category: str = ""
    description: str = ""
    installation: str = "AVAILABLE"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "source_type": self.source_type,
            "path": self.path,
            "url": self.url,
            "ref": self.ref,
            "subdir": self.subdir,
            "category": self.category,
            "description": self.description,
            "installation": self.installation,
        }


@dataclass
class Marketplace:
    """插件市场目录（marketplace.json）。"""

    name: str
    display_name: str = ""
    plugins: List[MarketplacePlugin] = field(default_factory=list)

    def find(self, name: str) -> Optional[MarketplacePlugin]:
        for entry in self.plugins:
            if entry.name == name:
                return entry
        return None


def _parse_entry_source(raw: Any) -> Dict[str, str]:
    """规整插件条目的来源声明为统一字段（source_type/path/url/ref/subdir）。

    兼容的声明形态：
    - 字符串简写："./plugins/foo" → 本地相对路径
    - {"source": "local", "path": ...}
    - {"source": "git" | "url", "url": ..., "ref"/"sha": ...}
    - {"source": "git-subdir", "url": ..., "path": ..., "ref"/"sha": ...}
    - {"source": "github", "repo": "owner/name", ...} → 推导仓库 URL
    """
    if isinstance(raw, str):
        return {"source_type": "local", "path": raw}
    if not isinstance(raw, dict):
        return {"source_type": "local", "path": ""}

    kind = str(raw.get("source") or "local").strip().lower()
    ref = str(raw.get("ref") or raw.get("ref_name") or raw.get("sha") or "")
    if kind == "github":
        repo = str(raw.get("repo") or "").strip().strip("/")
        url = f"https://github.com/{repo}.git" if repo else ""
        return {"source_type": "git", "url": url, "ref": ref,
                "subdir": str(raw.get("path") or "")}
    if kind == "git-subdir":
        return {"source_type": "git", "url": str(raw.get("url") or ""),
                "ref": ref, "subdir": str(raw.get("path") or "")}
    if kind in ("git", "url"):
        return {"source_type": "git", "url": str(raw.get("url") or ""),
                "ref": ref, "subdir": str(raw.get("subdir") or raw.get("path") or "")}
    return {"source_type": "local", "path": str(raw.get("path") or "")}


def parse_marketplace(data: Any) -> Marketplace:
    """解析 marketplace.json 内容，非法时抛 PluginError。"""
    if not isinstance(data, dict):
        raise PluginError("marketplace.json 必须是 JSON 对象")
    name = str(data.get("name") or "").strip()
    if not name:
        raise PluginError("marketplace.json 缺少 name 字段")
    interface = data.get("interface") or {}
    entries: List[MarketplacePlugin] = []
    for raw in data.get("plugins") or []:
        if not isinstance(raw, dict):
            continue
        entry_name = str(raw.get("name") or "").strip()
        if not entry_name:
            continue
        source = _parse_entry_source(raw.get("source"))
        if source["source_type"] == "git" and not source.get("url"):
            continue  # 无 url 的 git 条目无法安装，跳过
        policy = raw.get("policy") or {}
        entries.append(MarketplacePlugin(
            name=entry_name,
            source_type=source["source_type"],
            path=source.get("path", ""),
            url=source.get("url", ""),
            ref=source.get("ref", ""),
            subdir=source.get("subdir", ""),
            category=str(raw.get("category") or ""),
            description=str(raw.get("description") or ""),
            installation=str(policy.get("installation") or "AVAILABLE"),
        ))
    return Marketplace(
        name=name,
        display_name=str(interface.get("displayName") or "") if isinstance(interface, dict) else "",
        plugins=entries,
    )
