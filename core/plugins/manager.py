"""PluginManager — 插件安装/移除/升级与市场订阅的编排门面。

职责边界：本类只管注册表与负载文件的生命周期；运行时激活（技能入库、
MCP 合并、工具注册、事件广播）经 ``on_activate``/``on_deactivate`` 钩子
由上层实体层注入，core 不反向依赖 entities/agent。
"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from core.log import log
from core.plugins.manifest import (
    Marketplace,
    MarketplacePlugin,
    PluginError,
    parse_manifest,
    parse_marketplace,
    validate_plugin_name,
)
from core.plugins.sources import (
    cleanup_staging,
    fetch_git,
    fetch_local,
    git_pull,
    new_staging_dir,
    replace_payload,
)
from core.plugins.status import get_operation_board
from core.plugins.store import (
    InstalledPlugin,
    MarketplaceSource,
    PluginRegistry,
    marketplace_clone_dir,
    plugin_payload_dir,
)

ActivateHook = Callable[[InstalledPlugin, Path], InstalledPlugin]
DeactivateHook = Callable[[InstalledPlugin], None]

_MARKETPLACE_FILE_CANDIDATES = (
    ".agents/plugins/marketplace.json",
    ".claude-plugin/marketplace.json",
    ".cursor-plugin/marketplace.json",
    "marketplace.json",
)


class PluginManager:
    """插件管理门面（注册表 + 负载 + 市场，激活经钩子外置）。"""

    def __init__(self, registry: Optional[PluginRegistry] = None) -> None:
        self._registry = registry or PluginRegistry()
        self._lock = threading.RLock()
        # 激活钩子：安装/升级/启用时调用，返回组件清单已回写的记录
        self.on_activate: Optional[ActivateHook] = None
        # 去激活钩子：移除/升级/禁用时调用
        self.on_deactivate: Optional[DeactivateHook] = None

    @property
    def registry(self) -> PluginRegistry:
        return self._registry

    # ==================================================================
    # 已安装插件
    # ==================================================================

    def list_plugins(self) -> List[InstalledPlugin]:
        return self._registry.list_installed()

    def get_plugin(self, name: str) -> Optional[InstalledPlugin]:
        return self._registry.get(name)

    def install(self, name: str, marketplace: str = "") -> InstalledPlugin:
        """从已订阅市场安装插件。"""
        entry, source = self._resolve_entry(name, marketplace)
        return self._install_entry(entry, source)

    def install_from_source(self, source: str, ref: str = "", subdir: str = "") -> InstalledPlugin:
        """直接从 git URL 或本地路径安装插件（不经市场）。"""
        source_type = "git" if _looks_like_git_url(source) else "local"
        entry = MarketplacePlugin(
            name="", source_type=source_type,
            url=source if source_type == "git" else "",
            path=source if source_type == "local" else "",
            ref=ref, subdir=subdir,
        )
        return self._install_entry(entry, None)

    def _install_entry(self, entry: MarketplacePlugin,
                       marketplace: Optional[MarketplaceSource]) -> InstalledPlugin:
        label = entry.name or entry.url or entry.path
        with get_operation_board().track("安装", label):
            with self._lock:  # 安装全程串行化：同名并发安装/升级不会互相覆盖
                return self._install_entry_locked(entry, marketplace)

    def _install_entry_locked(self, entry: MarketplacePlugin,
                              marketplace: Optional[MarketplaceSource]) -> InstalledPlugin:
        staging = new_staging_dir()
        record: Optional[InstalledPlugin] = None
        try:
            payload, sha = self._fetch_entry(entry, marketplace, staging)
            manifest = parse_manifest(payload)
            name = manifest.name
            if entry.name and entry.name != name:
                log(f"插件名与市场条目不一致: {entry.name} → {name}", "WARNING")
            with self._lock:
                if self._registry.get(name) is not None:
                    raise PluginError(f"插件 '{name}' 已安装，请先移除或升级")
            record = InstalledPlugin(
                name=name,
                version=manifest.version,
                sha=sha,
                marketplace=marketplace.name if marketplace else "",
                source_type=entry.source_type,
                source=self._entry_source_uri(entry, marketplace),
                ref=entry.ref,
                subdir=entry.subdir,
                description=manifest.description,
                display_name=manifest.display_name,
                category=entry.category or manifest.interface.category,
            )
            target = plugin_payload_dir(name)
            replace_payload(payload, target)
            if self.on_activate is not None:
                record = self.on_activate(record, target)
            with self._lock:
                self._registry.upsert(record)
            log(f"插件已安装: {name} {record.version} ({record.source_type})", tag="插件")
            return record
        except Exception:
            if record is not None and self.on_deactivate is not None:
                try:
                    self.on_deactivate(record)
                except Exception as e:
                    log(f"插件安装失败回滚（去激活）异常: {record.name} - {e}", "WARNING")
                shutil.rmtree(plugin_payload_dir(record.name), ignore_errors=True)
            raise
        finally:
            cleanup_staging(staging)

    def remove(self, name: str) -> InstalledPlugin:
        """移除插件：先去激活回收组件，再删注册记录与负载目录。"""
        with get_operation_board().track("移除", name):
            with self._lock:
                record = self._registry.get(name)
                if record is None:
                    raise PluginError(f"插件 '{name}' 未安装")
                if self.on_deactivate is not None:
                    self.on_deactivate(record)
                self._registry.remove(name)
                shutil.rmtree(plugin_payload_dir(name), ignore_errors=True)
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
            raise PluginError(f"插件 '{name}' 未安装")
        staging = new_staging_dir()
        try:
            payload, sha = self._fetch_record_source(record, staging)
            manifest = parse_manifest(payload)
            if sha and sha == record.sha and manifest.version == record.version:
                return record, False
            if self.on_deactivate is not None:
                self.on_deactivate(record)
            target = plugin_payload_dir(record.name)
            replace_payload(payload, target)
            record.version = manifest.version
            record.sha = sha
            record.description = manifest.description
            record.display_name = manifest.display_name
            record.skills = []
            record.tools = []
            record.mcp_servers = []
            if self.on_activate is not None:
                record = self.on_activate(record, target)
            self._registry.upsert(record)
            log(f"插件已升级: {record.name} → {record.version} {sha[:8]}", tag="插件")
            return record, True
        except Exception:
            # 升级失败：负载目录可能已被替换一半，以注册表记录为准重新激活
            if self.on_activate is not None and plugin_payload_dir(record.name).is_dir():
                try:
                    refreshed = self.on_activate(record, plugin_payload_dir(record.name))
                    self._registry.upsert(refreshed)
                except Exception as e:
                    log(f"插件升级失败后重激活异常: {record.name} - {e}", "ERROR")
            raise
        finally:
            cleanup_staging(staging)

    def upgrade_all(self) -> Dict[str, bool]:
        """升级全部已安装插件，返回 {插件名: 是否有变更}（单个失败不阻断其余）。"""
        results: Dict[str, bool] = {}
        for record in self._registry.list_installed():
            try:
                _, changed = self.upgrade(record.name)
                results[record.name] = changed
            except Exception as e:
                log(f"插件升级失败: {record.name} - {e}", "ERROR")
                get_operation_board().record_failure("升级", record.name, str(e))
                results[record.name] = False
        return results

    def toggle(self, name: str, enabled: bool) -> InstalledPlugin:
        """启用/禁用插件（不卸载负载；禁用时去激活回收组件）。全程持锁串行化。"""
        with get_operation_board().track("启用" if enabled else "禁用", name):
            with self._lock:
                record = self._registry.get(name)
                if record is None:
                    raise PluginError(f"插件 '{name}' 未安装")
                if record.enabled == enabled:
                    return record
                target = plugin_payload_dir(name)
                # 先完成运行时切换再落盘——激活失败不留"已启用但无组件"的假状态
                if enabled:
                    if self.on_activate is not None and target.is_dir():
                        record = self.on_activate(record, target)
                elif self.on_deactivate is not None:
                    self.on_deactivate(record)
                record.enabled = enabled
                self._registry.upsert(record)
        log(f"插件已{'启用' if enabled else '禁用'}: {name}", tag="插件")
        return record

    # ==================================================================
    # 市场订阅
    # ==================================================================

    def add_marketplace(self, name: str, source: str, ref: str = "") -> MarketplaceSource:
        """订阅市场（git URL 或本地路径），立即拉取并校验 marketplace.json。全程持锁。"""
        validate_plugin_name(name)
        with get_operation_board().track("订阅市场", name):
            with self._lock:
                if self._registry.get_marketplace(name) is not None:
                    raise PluginError(f"市场 '{name}' 已订阅")
                source_type = "git" if _looks_like_git_url(source) else "local"
                record = MarketplaceSource(name=name, source_type=source_type, ref=ref,
                                           url=source if source_type == "git" else "",
                                           path=source if source_type == "local" else "")
                if source_type == "git":
                    staging = new_staging_dir()
                    try:
                        payload, _ = fetch_git(record.url, ref, staging)
                        clone_dir = marketplace_clone_dir(name)
                        replace_payload(payload, clone_dir)
                    finally:
                        cleanup_staging(staging)
                # 校验目录可读（本地路径非法在此暴露）
                self.load_catalog(name, source=record)
                self._registry.upsert_marketplace(record)
        log(f"市场已订阅: {name} ({source_type}: {source})", tag="插件")
        return record

    def remove_marketplace(self, name: str) -> MarketplaceSource:
        """取消订阅市场；仍有该市场来源的已装插件时拒绝（避免孤儿无法升级）。全程持锁。"""
        with self._lock:
            dependents = [p.name for p in self._registry.list_installed()
                          if p.marketplace == name]
            if dependents:
                raise PluginError(
                    f"市场 '{name}' 下仍有已安装插件: {', '.join(dependents)}，请先移除"
                )
            record = self._registry.remove_marketplace(name)
            if record is None:
                raise PluginError(f"市场 '{name}' 未订阅")
            if record.source_type == "git":
                shutil.rmtree(marketplace_clone_dir(name), ignore_errors=True)
        log(f"市场已取消订阅: {name}", tag="插件")
        return record

    def refresh_marketplaces(self, name: str = "") -> Dict[str, int]:
        """刷新市场目录（git 市场执行 pull），返回 {市场名: 插件条目数}。"""
        sources = self._registry.list_marketplaces()
        if name:
            sources = [s for s in sources if s.name == name]
            if not sources:
                raise PluginError(f"市场 '{name}' 未订阅")
        results: Dict[str, int] = {}
        for source in sources:
            try:
                with get_operation_board().track("刷新市场", source.name):
                    if source.source_type == "git":
                        git_pull(marketplace_clone_dir(source.name))
                    catalog = self.load_catalog(source.name, source=source)
                    results[source.name] = len(catalog.plugins)
            except Exception as e:
                log(f"市场刷新失败: {source.name} - {e}", "ERROR")
                get_operation_board().record_failure("刷新市场", source.name, str(e))
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
            result.append({**source.to_dict(), "plugin_count": count})
        return result

    def load_catalog(self, name: str,
                     source: Optional[MarketplaceSource] = None) -> Marketplace:
        """读取市场目录（每次从磁盘解析，天然反映外部改动）。"""
        if source is None:
            source = self._registry.get_marketplace(name)
            if source is None:
                raise PluginError(f"市场 '{name}' 未订阅")
        catalog_file, _ = self._locate_catalog_file(source)
        try:
            import json
            data = json.loads(catalog_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise PluginError(f"市场目录解析失败: {catalog_file} - {e}") from e
        return parse_marketplace(data)

    def search(self, query: str = "") -> List[Dict[str, object]]:
        """跨市场检索插件条目（名称/描述/分类子串匹配，空串列出全部）。"""
        needle = (query or "").strip().lower()
        results: List[Dict[str, object]] = []
        for source in self._registry.list_marketplaces():
            try:
                catalog = self.load_catalog(source.name, source=source)
            except PluginError as e:
                log(f"市场目录不可用，跳过检索: {source.name} - {e}", "WARNING")
                continue
            for entry in catalog.plugins:
                haystack = " ".join([entry.name, entry.description, entry.category]).lower()
                if needle and needle not in haystack:
                    continue
                installed = self._registry.get(entry.name) or self._find_installed_by_source(
                    entry, source)
                results.append({
                    **entry.to_dict(),
                    "marketplace": source.name,
                    "installed": installed is not None,
                    "installed_version": installed.version if installed else "",
                })
        return results

    def _find_installed_by_source(self, entry: MarketplacePlugin,
                                  marketplace: MarketplaceSource) -> Optional[InstalledPlugin]:
        """按来源同一性查找已装记录（条目名与清单名不一致时的兜底匹配）。"""
        entry_source = self._entry_source_uri(entry, marketplace)
        for record in self._registry.list_installed():
            if record.source_type == entry.source_type and record.source == entry_source \
                    and record.subdir == entry.subdir:
                return record
        return None

    # ==================================================================
    # 内部：条目解析与负载获取
    # ==================================================================

    def _resolve_entry(self, name: str,
                       marketplace: str) -> Tuple[MarketplacePlugin, MarketplaceSource]:
        """在市场目录中定位插件条目（未指定市场时全市场唯一匹配）。"""
        validate_plugin_name(name)
        candidates: List[Tuple[MarketplacePlugin, MarketplaceSource]] = []
        for source in self._registry.list_marketplaces():
            if marketplace and source.name != marketplace:
                continue
            try:
                catalog = self.load_catalog(source.name, source=source)
            except PluginError as e:
                log(f"市场目录不可用，跳过: {source.name} - {e}", "WARNING")
                continue
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

    def _fetch_entry(self, entry: MarketplacePlugin,
                     marketplace: Optional[MarketplaceSource],
                     staging: Path) -> Tuple[Path, str]:
        """按条目来源获取负载到暂存目录。"""
        if entry.source_type == "git":
            return fetch_git(entry.url, entry.ref, staging, subdir=entry.subdir)
        path = entry.path
        if marketplace is not None:
            _, base = self._locate_catalog_file(marketplace)
            path = str(base / entry.path)
        return fetch_local(path, staging)

    def _fetch_record_source(self, record: InstalledPlugin, staging: Path) -> Tuple[Path, str]:
        """按已装记录的来源重新获取负载（升级用）。"""
        if record.source_type == "git":
            return fetch_git(record.source, record.ref, staging, subdir=record.subdir)
        return fetch_local(record.source, staging)

    def _entry_source_uri(self, entry: MarketplacePlugin,
                          marketplace: Optional[MarketplaceSource]) -> str:
        """计算条目的绝对来源 URI（写入记录供升级复用）。"""
        if entry.source_type == "git":
            return entry.url
        if marketplace is not None:
            _, base = self._locate_catalog_file(marketplace)
            return str((base / entry.path).resolve())
        return str(Path(entry.path).expanduser().resolve())

    def _locate_catalog_file(self, source: MarketplaceSource) -> Tuple[Path, Path]:
        """定位市场的 marketplace.json，返回 (文件路径, 条目相对路径解析基准)。

        基准规则：marketplace.json 位于 <root>/.agents/plugins/ 或
        <root>/.claude-plugin/、<root>/.cursor-plugin/ 下时，条目相对路径相对
        <root> 解析；位于市场根时相对其所在目录解析。
        """
        if source.source_type == "git":
            root = marketplace_clone_dir(source.name)
        else:
            root = Path(source.path).expanduser().resolve()
        if root.is_file():
            return root, root.parent
        for rel in _MARKETPLACE_FILE_CANDIDATES:
            candidate = root / rel
            if candidate.is_file():
                base = candidate.parent
                if base.name == "plugins" and base.parent.name == ".agents":
                    base = base.parent.parent
                elif base.name in (".claude-plugin", ".cursor-plugin"):
                    base = base.parent
                return candidate, base
        raise PluginError(f"市场 '{source.name}' 缺少 marketplace.json: {root}")


def _looks_like_git_url(source: str) -> bool:
    """判断来源字符串是否为 git 仓库地址。"""
    text = source.strip()
    return (
        text.startswith(("http://", "https://", "git@", "ssh://", "file://"))
        or text.endswith(".git")
    )


_manager: Optional[PluginManager] = None
_manager_lock = threading.Lock()


def get_plugin_manager() -> PluginManager:
    """全局插件管理器单例。"""
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = PluginManager()
        return _manager
