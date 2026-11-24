"""PluginManager — 插件安装/移除/升级与市场订阅的编排门面。

职责边界：本类只管注册表与负载文件的生命周期；运行时激活（技能入库、
MCP 合并、工具注册、事件广播）经 ``on_activate``/``on_deactivate`` 钩子
由上层实体层注入，core 不反向依赖 entities/agent。
"""

from __future__ import annotations

import json
import shutil
import threading
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from core.log import log
from core.plugins import dsh_host
from core.plugins.catalog import MarketplaceCatalogs
from core.plugins.defaults import DEFAULT_MARKETPLACES
from core.plugins.github_topic import locate_topic_package, topic_from_url
from core.plugins.inspection import PreparedPlugin, SourceUnavailable, inspect_payload
from core.plugins.manifest import (
    Marketplace,
    MarketplacePlugin,
    PluginError,
    PluginNotFound,
    find_manifest_file,
    parse_manifest,
)
from core.plugins.sources import (
    cleanup_staging,
    fetch_git,
    fetch_local,
    looks_like_git_url,
    new_staging_dir,
    replacing_payload,
)
from core.plugins.status import get_operation_board
from core.plugins.store import (
    InstalledPlugin,
    MarketplaceSource,
    PluginRegistry,
    plugin_host_dir,
    plugin_payload_dir,
)

ActivateHook = Callable[[InstalledPlugin, Path], InstalledPlugin]
DeactivateHook = Callable[[InstalledPlugin], None]
ChangeHook = Callable[[str, InstalledPlugin], None]

# on_change 动作标识
ACTION_INSTALLED = "installed"
ACTION_REMOVED = "removed"
ACTION_UPGRADED = "upgraded"
ACTION_ENABLED = "enabled"
ACTION_DISABLED = "disabled"

class PluginManager:
    """插件管理门面（注册表 + 负载 + 市场，激活经钩子外置）。"""

    def __init__(self, registry: Optional[PluginRegistry] = None) -> None:
        self._registry = registry or PluginRegistry(defaults=DEFAULT_MARKETPLACES)
        self._lock = threading.RLock()
        self._catalogs = MarketplaceCatalogs(self._registry, self._lock)
        # 激活钩子：安装/升级/启用时调用，返回组件清单已回写的记录
        self.on_activate: Optional[ActivateHook] = None
        # 去激活钩子：移除/升级/禁用时调用
        self.on_deactivate: Optional[DeactivateHook] = None
        # 变更钩子：装卸/升级/启停成功后在管理器锁内调用（不得回调管理器方法）
        self.on_change: Optional[ChangeHook] = None

    @property
    def registry(self) -> PluginRegistry:
        return self._registry

    def _notify(self, action: str, record: InstalledPlugin) -> None:
        """触发变更钩子（未施绑或钩子异常不影响操作结果）。"""
        if self.on_change is None:
            return
        try:
            self.on_change(action, record)
        except Exception as e:
            log(f"插件变更钩子异常 ({action} {record.name}): {e}", "WARNING")

    # ==================================================================
    # 已安装插件
    # ==================================================================

    def list_plugins(self) -> List[InstalledPlugin]:
        return self._registry.list_installed()

    def get_plugin(self, name: str) -> Optional[InstalledPlugin]:
        return self._registry.get(name)

    def restore_enabled(self) -> int:
        """启动时重建 Anelf 侧注册；已安装的 DSH profile 保存在磁盘上。"""
        if not self.on_activate:
            return 0
        restored = 0
        with self._lock:
            for record in self._registry.list_installed():
                if not record.enabled:
                    continue
                try:
                    target = plugin_payload_dir(record.name)
                    if not target.is_dir():
                        raise PluginError(f"插件负载目录缺失: {target}")
                    self._registry.upsert(self.on_activate(replace(record), target))
                    restored += 1
                except Exception as exc:
                    log(f"插件启动激活失败: {record.name} - {exc}", "ERROR", tag="插件")
        return restored

    def install(self, name: str, marketplace: str = "", expected_revision: str = "") -> InstalledPlugin:
        """从已订阅市场安装插件。"""
        record, _ = self.install_package(name=name, marketplace=marketplace, expected_revision=expected_revision)
        return record

    @staticmethod
    def _direct_entry(source: str, ref: str, subdir: str) -> MarketplacePlugin:
        if not source.strip():
            raise PluginError("请提供插件目录或 Git 仓库地址")
        if topic_from_url(source):
            raise PluginError("请先通过 add_marketplace 订阅 GitHub Topic，再从市场选择插件")
        source_type = "git" if looks_like_git_url(source) else "local"
        return MarketplacePlugin(
            name="", source_type=source_type,
            url=source if source_type == "git" else "",
            path=source if source_type == "local" else "",
            ref=ref, subdir=subdir,
        )

    def install_from_source(self, source: str, ref: str = "", subdir: str = "", expected_revision: str = "") -> InstalledPlugin:
        """直接从 Git 或本地来源安装，可绑定预检取得的修订。"""
        record, _ = self.install_package(source=source, ref=ref, subdir=subdir, expected_revision=expected_revision)
        return record

    def install_package(self, *, name: str = "", marketplace: str = "", source: str = "",
                        ref: str = "", subdir: str = "", expected_revision: str = "") -> tuple[InstalledPlugin, str]:
        """统一安装入口，返回插件和本次操作的可查询 ID。"""
        with get_operation_board().track("安装", name or source) as operation_id, self._lock:
            entry, market = self._resolve_package(name, marketplace, source, ref, subdir)
            return self._install_entry(entry, market, expected_revision), operation_id

    def _resolve_package(self, name: str, marketplace: str, source: str, ref: str,
                         subdir: str) -> tuple[MarketplacePlugin, MarketplaceSource | None]:
        if bool(name) == bool(source) or (source and marketplace) or (name and (ref or subdir)):
            raise PluginError("请选择市场条目或一个直接来源")
        if source:
            return self._direct_entry(source, ref, subdir), None
        get_operation_board().stage("catalog")
        return self._catalogs.resolve_entry(name, marketplace)

    def inspect(self, name: str = "", marketplace: str = "", source: str = "", ref: str = "", subdir: str = "") -> dict:
        """读取暂存副本的静态声明。目录或来源不可用时，兼容性保持未知。"""
        try:
            entry, market = self._resolve_package(name, marketplace, source, ref, subdir)
        except PluginError as exc:
            return {"status": "unverified", "detail": str(exc)}
        try:
            with self._prepare_entry(entry, market) as package:
                return inspect_payload(package.root, package.revision(entry), package.manifest)
        except (SourceUnavailable, OSError) as exc:
            return {"status": "unverified", "detail": str(exc)}
        except PluginError as exc:
            return {"status": "invalid", "detail": str(exc)}

    @contextmanager
    def _prepare_entry(self, entry: MarketplacePlugin, marketplace: MarketplaceSource | None) -> Iterator[PreparedPlugin]:
        staging = new_staging_dir()
        try:
            get_operation_board().stage("fetch")
            try:
                payload, sha = self._fetch_entry(entry, marketplace, staging)
            except PluginError as exc:
                raise SourceUnavailable(str(exc)) from exc
            get_operation_board().stage("manifest")
            subdir = entry.subdir
            if marketplace and marketplace.source_type == "github-topic":
                root = payload
                payload = locate_topic_package(root)
                subdir = payload.relative_to(root).as_posix() if payload != root else ""
            self._materialize_manifest(payload, entry)
            yield PreparedPlugin(payload, sha, subdir, parse_manifest(payload))
        finally:
            cleanup_staging(staging)

    def _install_entry(self, entry: MarketplacePlugin, marketplace: MarketplaceSource | None,
                       expected_revision: str) -> InstalledPlugin:
        with self._prepare_entry(entry, marketplace) as package:
            get_operation_board().stage("manifest", name=package.manifest.name)
            if expected_revision and package.revision(entry) != expected_revision:
                raise PluginError("来源内容已变化，请重新预检")
            manifest = package.manifest
            name = manifest.name
            if entry.name and entry.name != name:
                log(f"插件名与市场条目不一致: {entry.name} → {name}", "WARNING")
            if self._registry.get(name) is not None:
                raise PluginError(f"插件 '{name}' 已安装，请先移除或升级")
            record = InstalledPlugin(
                name=name,
                version=manifest.version,
                format=manifest.format,
                sha=package.sha,
                marketplace=marketplace.name if marketplace else "",
                marketplace_entry=entry.name if marketplace else "",
                source_type=entry.source_type,
                source=self._catalogs.entry_source_uri(entry, marketplace),
                ref=entry.ref,
                subdir=package.subdir,
                description=manifest.description,
                display_name=manifest.display_name,
                category=entry.category or manifest.interface.category,
            )
            target = plugin_payload_dir(name)
            with replacing_payload(package.root, target):
                try:
                    record = self._activate(record, target)
                    get_operation_board().stage("persist")
                    self._registry.upsert(record)
                except Exception:
                    self._discard_activation(record)
                    self._remove_host(record)
                    raise
            log(f"插件已安装: {name} {record.version} ({record.source_type})", tag="插件")
            self._notify(ACTION_INSTALLED, record)
            return record

    def _activate(self, record: InstalledPlugin, target: Path) -> InstalledPlugin:
        if record.format == "dsh-host":
            get_operation_board().stage("host")
            record.dsh_home = dsh_host.install_bundle(record.name, target, record.dsh_home)
        get_operation_board().stage("register")
        return self.on_activate(record, target) if self.on_activate else record

    @staticmethod
    def _materialize_manifest(payload: Path, entry: MarketplacePlugin | None) -> None:
        """将仅在市场中声明的技能包清单固化到暂存负载，供重启和启停复用。"""
        if entry and entry.inline_manifest is not None and find_manifest_file(payload) is None:
            (payload / "plugin.json").write_text(json.dumps(entry.inline_manifest, ensure_ascii=False), encoding="utf-8")

    def _discard_activation(self, record: InstalledPlugin) -> None:
        if self.on_deactivate:
            self.on_deactivate(record)

    @staticmethod
    def _remove_host(record: InstalledPlugin) -> None:
        if record.format == "dsh-host":
            home = plugin_host_dir(record.name)
            if home.is_dir():
                shutil.rmtree(home)

    def remove(self, name: str) -> InstalledPlugin:
        """移除插件：先去激活回收组件，再删注册记录与负载目录。"""
        with get_operation_board().track("移除", name):
            with self._lock:
                record = self._registry.get(name)
                if record is None:
                    raise PluginNotFound(f"插件 '{name}' 未安装")
                target = plugin_payload_dir(name)
                try:
                    self._discard_activation(record)
                    with ExitStack() as transaction:
                        transaction.enter_context(replacing_payload(None, target))
                        if record.format == "dsh-host":
                            transaction.enter_context(replacing_payload(None, plugin_host_dir(name)))
                        get_operation_board().stage("persist")
                        self._registry.remove(name)
                except Exception:
                    if record.enabled and target.is_dir() and self.on_activate:
                        self.on_activate(record, target)
                    raise
                self._notify(ACTION_REMOVED, record)
        log(f"插件已移除: {name}", tag="插件")
        return record

    def upgrade(self, name: str) -> Tuple[InstalledPlugin, bool]:
        """升级单个插件，返回 (记录, 是否有变更)。全程持锁串行化。"""
        with get_operation_board().track("升级", name):
            with self._lock:
                return self._upgrade_locked(name)

    def _upgrade_locked(self, name: str) -> Tuple[InstalledPlugin, bool]:
        record = self._registry.get(name)
        if record is None:
            raise PluginNotFound(f"插件 '{name}' 未安装")
        staging = new_staging_dir()
        try:
            get_operation_board().stage("fetch")
            payload, sha = self._fetch_record_source(record, staging)
            get_operation_board().stage("manifest")
            entry = (self.load_catalog(record.marketplace).find(record.marketplace_entry)
                     if record.marketplace and record.marketplace_entry and find_manifest_file(payload) is None else None)
            self._materialize_manifest(payload, entry)
            manifest = parse_manifest(payload)
            if manifest.name != record.name:
                raise PluginError(f"插件标识发生变化: {record.name} → {manifest.name}")
            if sha and sha == record.sha and manifest.version == record.version and (
                not entry or parse_manifest(plugin_payload_dir(name)) == manifest
            ):
                return record, False
            updated = replace(
                record, version=manifest.version, sha=sha, description=manifest.description,
                display_name=manifest.display_name, format=manifest.format, skills=[], tools=[], mcp_servers=[],
                dsh_home=record.dsh_home if manifest.format == "dsh-host" else "",
            )
            target = plugin_payload_dir(name)
            if record.enabled:
                self._discard_activation(record)
            try:
                with ExitStack() as transaction:
                    transaction.enter_context(replacing_payload(payload, target))
                    if record.format == "dsh-host" and updated.format != "dsh-host":
                        transaction.enter_context(replacing_payload(None, plugin_host_dir(name)))
                    if updated.enabled:
                        updated = self._activate(updated, target)
                    get_operation_board().stage("persist")
                    self._registry.upsert(updated)
            except Exception:
                if updated.enabled:
                    self._discard_activation(updated)
                if record.format != "dsh-host":
                    self._remove_host(updated)
                if record.enabled and target.is_dir():
                    self._activate(record, target)
                raise
            self._notify(ACTION_UPGRADED, updated)
            return updated, True
        finally:
            cleanup_staging(staging)

    def upgrade_all(self, *, errors: dict[str, str] | None = None) -> Dict[str, bool]:
        """升级全部已安装插件，返回 {插件名: 是否有变更}（单个失败不阻断其余）。"""
        results: Dict[str, bool] = {}
        for record in self._registry.list_installed():
            try:
                _, changed = self.upgrade(record.name)
                results[record.name] = changed
            except Exception as e:
                log(f"插件升级失败: {record.name} - {e}", "ERROR")
                if errors is not None:
                    errors[record.name] = str(e)
                results[record.name] = False
        return results

    def toggle(self, name: str, enabled: bool) -> InstalledPlugin:
        """启用/禁用插件（不卸载负载；禁用时去激活回收组件）。全程持锁串行化。"""
        with get_operation_board().track("启用" if enabled else "禁用", name):
            with self._lock:
                record = self._registry.get(name)
                if record is None:
                    raise PluginNotFound(f"插件 '{name}' 未安装")
                if record.enabled == enabled:
                    return record
                target = plugin_payload_dir(name)
                updated = replace(record, enabled=enabled)
                if enabled and not target.is_dir() and (self.on_activate or record.format == "dsh-host"):
                    raise PluginError(f"插件负载目录缺失: {name}")
                try:
                    if enabled:
                        updated = self._activate(updated, target)
                    else:
                        self._disable(record)
                    get_operation_board().stage("persist")
                    self._registry.upsert(updated)
                except Exception:
                    if enabled:
                        self._disable(updated)
                    else:
                        self._activate(record, target)
                    raise
                record = updated
                self._notify(ACTION_ENABLED if enabled else ACTION_DISABLED, record)
        log(f"插件已{'启用' if enabled else '禁用'}: {name}", tag="插件")
        return record

    def _disable(self, record: InstalledPlugin) -> None:
        if record.format == "dsh-host":
            dsh_host.disable_bundle(record.name, record.display_name, record.dsh_home)
        self._discard_activation(record)

    # ==================================================================
    # 市场订阅
    # ==================================================================

    def add_marketplace(self, name: str, source: str, ref: str = "") -> MarketplaceSource:
        return self._catalogs.add_marketplace(name, source, ref)

    def remove_marketplace(self, name: str) -> MarketplaceSource:
        return self._catalogs.remove_marketplace(name)

    def toggle_marketplace(self, name: str, enabled: bool) -> MarketplaceSource:
        return self._catalogs.toggle_marketplace(name, enabled)

    def refresh_marketplaces(self, name: str = "", *, errors: dict[str, str] | None = None) -> Dict[str, int]:
        return self._catalogs.refresh_marketplaces(name, errors=errors)

    def list_marketplaces(self) -> List[Dict[str, object]]:
        return self._catalogs.list_marketplaces()

    def load_catalog(self, name: str, source: Optional[MarketplaceSource] = None) -> Marketplace:
        return self._catalogs.load_catalog(name, source)

    def search(self, query: str = "", marketplace: str = "") -> List[Dict[str, object]]:
        return self._catalogs.search(query, marketplace)

    def search_page(self, query: str, marketplace: str, offset: int, limit: int | None) -> dict[str, object]:
        """返回稳定顺序的目录分页，以及未能参与跨市场搜索的来源。"""
        if offset < 0 or (limit is not None and not 1 <= limit <= 100):
            raise PluginError("offset 须为非负整数，limit 须在 1 至 100 之间")
        errors: dict[str, str] = {}
        matches = self._catalogs.search(query, marketplace, errors=errors)
        end = offset + limit if limit is not None else len(matches)
        return {"count": len(matches), "plugins": matches[offset:end],
                "next_offset": end if end < len(matches) else None, "errors": errors}

    def _fetch_entry(self, entry: MarketplacePlugin,
                     marketplace: Optional[MarketplaceSource],
                     staging: Path) -> Tuple[Path, str]:
        """按条目来源获取负载到暂存目录。"""
        if entry.source_type == "git":
            return fetch_git(entry.url, entry.ref, staging, subdir=entry.subdir)
        path = self._catalogs.entry_source_uri(entry, marketplace)
        return fetch_local(path, staging, subdir=entry.subdir)

    def _fetch_record_source(self, record: InstalledPlugin, staging: Path) -> Tuple[Path, str]:
        """按已装记录的来源重新获取负载（升级用）。"""
        if record.source_type == "git":
            return fetch_git(record.source, record.ref, staging, subdir=record.subdir)
        return fetch_local(record.source, staging, subdir=record.subdir)


_manager: Optional[PluginManager] = None
_manager_lock = threading.Lock()


def get_plugin_manager() -> PluginManager:
    """全局插件管理器单例。"""
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = PluginManager()
        return _manager
