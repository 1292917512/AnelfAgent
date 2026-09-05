"""插件注册表 — 已安装插件与已订阅市场的持久化（plugins.json）。

存储布局：
- 注册表：``ConfigPaths.PLUGINS_REGISTRY``（config/plugins.json）
- 插件负载：``ConfigPaths.PLUGINS_DIR``/<name>/（workspace/plugins/<name>/）
- 市场克隆：``ConfigPaths.PLUGINS_DIR``/_marketplaces/<name>/

读写经单把 RLock 串行化 + 原子落盘；记录中的组件清单（技能/工具/MCP server
名称）由激活层回写，供卸载时精确回收。
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.file_utils import atomic_write_text
from core.log import log
from core.path import ConfigPaths


@dataclass
class InstalledPlugin:
    """已安装插件记录。"""

    name: str
    version: str = ""
    sha: str = ""
    marketplace: str = ""           # 来源市场名（直接来源安装时为 ""）
    source_type: str = "local"      # local | git
    source: str = ""                # 原始来源（绝对路径或仓库 URL）
    ref: str = ""                   # git 源的分支/tag
    subdir: str = ""                # git 源仓库内的插件子目录
    enabled: bool = True
    description: str = ""
    display_name: str = ""
    category: str = ""
    installed_at: float = 0.0
    updated_at: float = 0.0
    # 激活产物（由激活层回写，卸载时按名回收）
    skills: List[str] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)
    mcp_servers: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "InstalledPlugin":
        fields = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**fields)


@dataclass
class MarketplaceSource:
    """已订阅的插件市场来源。"""

    name: str
    source_type: str                # local | git
    url: str = ""                   # git：仓库地址
    path: str = ""                  # local：市场根目录（含 marketplace.json 或其父级）
    ref: str = ""
    added_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MarketplaceSource":
        fields = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**fields)


class PluginRegistry:
    """plugins.json 注册表读写（线程安全，原子落盘）。"""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path else Path(ConfigPaths.PLUGINS_REGISTRY)
        self._lock = threading.RLock()
        self._installed: Dict[str, InstalledPlugin] = {}
        self._marketplaces: Dict[str, MarketplaceSource] = {}
        self._load()

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def _load(self) -> None:
        with self._lock:
            self._installed.clear()
            self._marketplaces.clear()
            if not self._path.exists():
                return
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                log(f"插件注册表解析失败: {self._path} - {e}", "ERROR")
                return
            for name, raw in (data.get("installed") or {}).items():
                if isinstance(raw, dict):
                    raw.setdefault("name", name)
                    self._installed[name] = InstalledPlugin.from_dict(raw)
            for name, raw in (data.get("marketplaces") or {}).items():
                if isinstance(raw, dict):
                    raw.setdefault("name", name)
                    self._marketplaces[name] = MarketplaceSource.from_dict(raw)

    def _save(self) -> None:
        payload = {
            "installed": {n: p.to_dict() for n, p in self._installed.items()},
            "marketplaces": {n: m.to_dict() for n, m in self._marketplaces.items()},
        }
        atomic_write_text(self._path, json.dumps(payload, ensure_ascii=False, indent=2))

    def reload(self) -> None:
        """从磁盘重载注册表（外部途径改动后对齐内存视图）。"""
        self._load()

    # ------------------------------------------------------------------
    # 已安装插件
    # ------------------------------------------------------------------

    def get(self, name: str) -> Optional[InstalledPlugin]:
        with self._lock:
            return self._installed.get(name)

    def list_installed(self) -> List[InstalledPlugin]:
        with self._lock:
            return sorted(self._installed.values(), key=lambda p: p.name)

    def upsert(self, record: InstalledPlugin) -> None:
        with self._lock:
            if not record.installed_at:
                record.installed_at = time.time()
            record.updated_at = time.time()
            self._installed[record.name] = record
            self._save()

    def remove(self, name: str) -> Optional[InstalledPlugin]:
        with self._lock:
            record = self._installed.pop(name, None)
            if record is not None:
                self._save()
            return record

    # ------------------------------------------------------------------
    # 市场订阅
    # ------------------------------------------------------------------

    def get_marketplace(self, name: str) -> Optional[MarketplaceSource]:
        with self._lock:
            return self._marketplaces.get(name)

    def list_marketplaces(self) -> List[MarketplaceSource]:
        with self._lock:
            return sorted(self._marketplaces.values(), key=lambda m: m.name)

    def upsert_marketplace(self, source: MarketplaceSource) -> None:
        with self._lock:
            if not source.added_at:
                source.added_at = time.time()
            self._marketplaces[source.name] = source
            self._save()

    def remove_marketplace(self, name: str) -> Optional[MarketplaceSource]:
        with self._lock:
            source = self._marketplaces.pop(name, None)
            if source is not None:
                self._save()
            return source


# ------------------------------------------------------------------
# 路径辅助
# ------------------------------------------------------------------

def plugins_dir() -> Path:
    """插件负载根目录（workspace/plugins/）。"""
    root = Path(ConfigPaths.PLUGINS_DIR)
    root.mkdir(parents=True, exist_ok=True)
    return root


def plugin_payload_dir(name: str) -> Path:
    """单个插件的负载目录。"""
    return plugins_dir() / name


def marketplace_clone_dir(name: str) -> Path:
    """git 市场的本地克隆目录。"""
    return plugins_dir() / "_marketplaces" / name
