"""市场目录、订阅与来源解析。所有写操作与插件安装共用管理器的事务锁。"""

from __future__ import annotations

import json
import shutil
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.log import log
from core.plugins.github_topic import refresh_topic, topic_from_url
from core.plugins.manifest import (
    Marketplace,
    MarketplacePlugin,
    PluginError,
    PluginNotFound,
    parse_marketplace,
    validate_plugin_name,
)
from core.plugins.sources import (
    cleanup_staging,
    fetch_git,
    looks_like_git_url,
    new_staging_dir,
    replacing_payload,
    updating_git_checkout,
)
from core.plugins.status import get_operation_board
from core.plugins.store import MarketplaceSource, PluginRegistry, marketplace_clone_dir

_MARKETPLACE_FILE_CANDIDATES = (
    ".agents/plugins/marketplace.json",
    ".claude-plugin/marketplace.json",
    ".cursor-plugin/marketplace.json",
    ".atomcode-plugin/marketplace.json",
    "marketplace.json",
)


class MarketplaceCatalogs:
    """订阅、刷新与查询公共市场目录；插件运行时由 PluginManager 管理。"""

    def __init__(self, registry: PluginRegistry, lock: threading.RLock) -> None:
        self._registry = registry
        self._lock = lock
        self._preset_locks = {s.name: threading.Lock() for s in registry.list_marketplaces()}
        self._failures: dict[str, tuple[float, str]] = {}

    @contextmanager
    def _new_cache(self, source: MarketplaceSource) -> Iterator[Marketplace]:
        """拉取并校验候选快照，订阅写入也成功后才保留它。"""
        staging = new_staging_dir()
        try:
            if source.source_type == "github-topic":
                payload = staging / "topic"
                refresh_topic(source.name, source.url, payload)
            else:
                payload, _ = fetch_git(source.url, source.ref, staging, timeout=45)
            with replacing_payload(payload, marketplace_clone_dir(source.name)):
                yield self.load_catalog(source.name, source)
        finally:
            cleanup_staging(staging)

    def _sync_remote(self, source: MarketplaceSource) -> Marketplace:
        root = marketplace_clone_dir(source.name)
        if not root.exists() or (source.source_type == "git" and not (root / ".git").is_dir()):
            with self._new_cache(source) as catalog:
                return catalog
        if source.source_type == "github-topic":
            refresh_topic(source.name, source.url, root)
            return self.load_catalog(source.name, source)
        with updating_git_checkout(root, source.ref):
            return self.load_catalog(source.name, source)

    def add_marketplace(self, name: str, source: str, ref: str = "") -> MarketplaceSource:
        """订阅 GitHub Topic、Git 仓库或本地目录，校验快照后写入注册表。"""
        validate_plugin_name(name)
        if not source.strip():
            raise PluginError("请提供市场的 Git 仓库、本地目录或 GitHub Topic 地址")
        with get_operation_board().track("订阅市场", name):
            with self._lock:
                if self._registry.get_marketplace(name) is not None:
                    raise PluginError(f"市场 '{name}' 已订阅")
                source_type = "git" if looks_like_git_url(source) else "local"
                if topic_from_url(source):
                    if ref:
                        raise PluginError("GitHub Topic 没有分支或 tag，请清空 ref")
                    source_type = "github-topic"
                record = MarketplaceSource(name=name, source_type=source_type, ref=ref,
                                           url=source if source_type != "local" else "",
                                           path=source if source_type == "local" else "")
                if source_type != "local":
                    with self._new_cache(record):
                        self._registry.upsert_marketplace(record)
                else:
                    self.load_catalog(name, source=record)
                    self._registry.upsert_marketplace(record)
        log(f"市场已订阅: {name} ({source_type}: {source})", tag="插件")
        return record

    def remove_marketplace(self, name: str) -> MarketplaceSource:
        """取消订阅市场；仍有该市场来源的已装插件时拒绝（避免孤儿无法升级）。全程持锁。"""
        with self._lock:
            source = self._registry.get_marketplace(name)
            if source and self._registry.is_builtin(source):
                raise PluginError(f"内置来源 '{name}' 可通过来源开关停用")
            dependents = [p.name for p in self._registry.list_installed()
                          if p.marketplace == name]
            if dependents:
                raise PluginError(
                    f"市场 '{name}' 下仍有已安装插件: {', '.join(dependents)}，请先移除"
                )
            record = self._registry.remove_marketplace(name)
            if record is None:
                raise PluginNotFound(f"市场 '{name}' 未订阅")
            if record.source_type != "local":
                shutil.rmtree(marketplace_clone_dir(name), ignore_errors=True)
        log(f"市场已取消订阅: {name}", tag="插件")
        return record

    def toggle_marketplace(self, name: str, enabled: bool) -> MarketplaceSource:
        """仅更改发现与新安装的可见性，保留已安装插件和快照。"""
        with self._lock:
            source = self._registry.get_marketplace(name)
            if source is None:
                raise PluginNotFound(f"市场 '{name}' 未订阅")
            if source.enabled != enabled:
                source = replace(source, enabled=enabled)
                self._registry.upsert_marketplace(source)
            return source

    def refresh_marketplaces(self, name: str = "", *, errors: dict[str, str] | None = None) -> Dict[str, int]:
        """刷新市场目录（git 市场执行 pull），返回 {市场名: 插件条目数}。"""
        sources = self._sources_for(name)
        results: Dict[str, int] = {}
        for source in sources:
            try:
                lock = self._preset_locks[source.name] if self._registry.is_builtin(source) else self._lock
                with lock, get_operation_board().track("刷新市场", source.name):
                    if self._registry.get_marketplace(source.name) != source:
                        raise PluginError(f"市场订阅已变更: {source.name}")
                    if source.source_type == "local":
                        results[source.name] = len(self.load_catalog(source.name, source).plugins)
                    else:
                        results[source.name] = len(self._sync_remote(source).plugins)
                    self._failures.pop(source.name, None)
            except Exception as e:
                log(f"市场刷新失败: {source.name} - {e}", "ERROR")
                if errors is not None:
                    errors[source.name] = str(e)
                results[source.name] = -1
        return results

    def list_marketplaces(self) -> List[Dict[str, object]]:
        """列出已订阅市场（含目录条目数；目录读取失败为 -1）。"""
        result: List[Dict[str, object]] = []
        for source in self._registry.list_marketplaces():
            try:
                count = len(self.load_catalog(source.name, source=source).plugins)
            except PluginError:
                count = -1
            builtin = self._registry.is_builtin(source)
            pending = builtin and not marketplace_clone_dir(source.name).exists()
            result.append({**source.to_dict(), "builtin": builtin, "pending": pending, "plugin_count": count})
        return result

    def load_catalog(self, name: str,
                     source: Optional[MarketplaceSource] = None) -> Marketplace:
        """读取市场目录（每次从磁盘解析，天然反映外部改动）。"""
        if source is None:
            source = self._registry.get_marketplace(name)
            if source is None:
                raise PluginNotFound(f"市场 '{name}' 未订阅")
        catalog_file, _ = self._locate_catalog_file(source)
        try:
            data = json.loads(catalog_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise PluginError(f"市场目录解析失败: {catalog_file} - {e}") from e
        return parse_marketplace(data)

    def _sources_for(self, marketplace: str) -> list[MarketplaceSource]:
        if not marketplace:
            return [s for s in self._registry.list_marketplaces() if s.enabled]
        source = self._registry.get_marketplace(marketplace)
        if source is None:
            raise PluginNotFound(f"市场 '{marketplace}' 未订阅")
        if not source.enabled:
            raise PluginError(f"来源 '{marketplace}' 已停用")
        return [source]

    def _read_active(self, source: MarketplaceSource) -> Marketplace:
        if not self._registry.is_builtin(source):
            return self.load_catalog(source.name, source)
        with self._preset_locks[source.name]:
            if not marketplace_clone_dir(source.name).exists():
                failure = self._failures.get(source.name)
                if failure and time.monotonic() - failure[0] < 30:
                    raise PluginError(failure[1])
                try:
                    return self._sync_remote(source)
                except (OSError, PluginError) as exc:
                    self._failures[source.name] = (time.monotonic(), str(exc))
                    raise PluginError(str(exc)) from exc
            return self.load_catalog(source.name, source)

    def _available_catalogs(self, marketplace: str, errors: dict[str, str] | None = None) -> Iterator[tuple[MarketplaceSource, Marketplace]]:
        sources = self._sources_for(marketplace)
        if len(sources) == 1 and marketplace:
            yield sources[0], self._read_active(sources[0])
            return
        if not sources:
            return
        with ThreadPoolExecutor(max_workers=min(4, len(sources))) as pool:
            pending = [(s, pool.submit(self._read_active, s)) for s in sources]
        for source, future in pending:
            try:
                catalog = future.result()
            except PluginError as exc:
                if errors is not None:
                    errors[source.name] = str(exc)
                log(f"市场目录不可用，跳过: {source.name} - {exc}", "WARNING")
            else:
                yield source, catalog

    def search(self, query: str = "", marketplace: str = "", *, errors: dict[str, str] | None = None) -> List[Dict[str, object]]:
        """跨市场检索插件条目（名称/描述/分类子串匹配，空串列出全部）。"""
        needle = (query or "").strip().lower()
        records = self._registry.list_installed()
        by_name = {record.name: record for record in records}
        by_source = {(r.source_type, r.source, r.subdir): r for r in records}
        by_repository = {r.source: r for r in records if r.source_type == "git"}
        results: List[Dict[str, object]] = []
        for source, catalog in self._available_catalogs(marketplace, errors):
            for entry in catalog.plugins:
                haystack = " ".join([entry.name, entry.description, entry.category]).lower()
                if needle and needle not in haystack:
                    continue
                installed = by_name.get(entry.name)
                if records and installed is None:
                    uri = self.entry_source_uri(entry, source)
                    match = by_source.get((entry.source_type, uri, entry.subdir))
                    if match is None and source.source_type == "github-topic":
                        match = by_repository.get(uri)
                    if match and (not match.marketplace_entry or match.marketplace_entry == entry.name):
                        installed = match
                results.append({
                    **entry.to_dict(),
                    "marketplace": source.name,
                    "installed": installed is not None,
                    "installed_version": installed.version if installed else "",
                })
        return results

    def resolve_entry(self, name: str,
                       marketplace: str) -> Tuple[MarketplacePlugin, MarketplaceSource]:
        """在市场目录中定位插件条目（未指定市场时全市场唯一匹配）。"""
        candidates: List[Tuple[MarketplacePlugin, MarketplaceSource]] = []
        for source, catalog in self._available_catalogs(marketplace):
            entry = catalog.find(name)
            if entry is not None:
                candidates.append((entry, source))
        if not candidates:
            scope = f"市场 '{marketplace}'" if marketplace else "全部已订阅市场"
            raise PluginError(f"{scope}中未找到插件 '{name}'")
        if len(candidates) > 1:
            names = ", ".join(s.name for _, s in candidates)
            raise PluginError(f"插件 '{name}' 在多个市场存在（{names}），请指定 marketplace")
        entry, source = candidates[0]
        if entry.installation == "NOT_AVAILABLE":
            raise PluginError(f"插件 '{name}' 被市场策略标记为不可安装")
        return entry, source

    def entry_source_uri(self, entry: MarketplacePlugin,
                          marketplace: Optional[MarketplaceSource]) -> str:
        """计算条目的绝对来源 URI（写入记录供升级复用）。"""
        if entry.source_type == "git":
            return entry.url
        if marketplace is not None:
            _, base = self._locate_catalog_file(marketplace)
            path = (base / entry.path).resolve()
            if marketplace.source_type != "local" and not path.is_relative_to(base.resolve()):
                raise PluginError(f"市场条目路径越出仓库: {entry.path}")
            return str(path)
        return str(Path(entry.path).expanduser().resolve())

    def _locate_catalog_file(self, source: MarketplaceSource) -> Tuple[Path, Path]:
        """定位市场的 marketplace.json，返回 (文件路径, 条目相对路径解析基准)。

        基准规则：marketplace.json 位于 <root>/.agents/plugins/ 或
        <root>/.claude-plugin/、<root>/.cursor-plugin/ 下时，条目相对路径相对
        <root> 解析；位于市场根时相对其所在目录解析。
        """
        if source.source_type != "local":
            root = marketplace_clone_dir(source.name)
        else:
            root = Path(source.path).expanduser().resolve()
        candidates = (root,) if root.is_file() else (root / rel for rel in _MARKETPLACE_FILE_CANDIDATES)
        for candidate in candidates:
            if candidate.is_file():
                base = candidate.parent
                if candidate.name == "marketplace.json":
                    if base.name == "plugins" and base.parent.name == ".agents":
                        base = base.parent.parent
                    elif base.name in (".claude-plugin", ".cursor-plugin", ".atomcode-plugin"):
                        base = base.parent
                return candidate, base
        raise PluginError(f"市场 '{source.name}' 缺少 marketplace.json: {root}")
