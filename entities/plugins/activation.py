"""插件运行时激活 — 技能入库、MCP 合并、工具注册与变更通知。

PluginManager 的 on_activate/on_deactivate/on_change 钩子实现：把插件负载
接入 Anelf 运行时（core/plugins 不反向依赖 entities，编排在本层完成）。

激活产物回收约定：
- 技能：拷入 workspace/skills/<name>/，目录内放置 ``.anelf_plugin`` 来源标记，
  卸载时仅删除标记属于本插件的目录（不误删用户同名技能）
- MCP server：写入 mcp_servers.json 并打 ``plugin`` 来源字段，卸载按名回收
- 工具：tools.py 导入前后对 EntityRegistry 做差集，卸载按记录名注销
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path
from typing import List

from core.entity import EntityMetadata, EntityRegistry, EntityType
from core.log import log
from core.path import workspace_root
from core.plugins.manifest import PluginError, load_plugin_mcp_servers, parse_manifest
from core.plugins.roster import roster_line
from core.plugins.store import InstalledPlugin

_TAG = "插件"
_SKILL_MARKER = ".anelf_plugin"

_ACTION_VERBS = {
    "installed": "已安装",
    "removed": "已移除",
    "upgraded": "已升级",
    "enabled": "已启用",
    "disabled": "已禁用",
}


# ==================================================================
# 激活 / 去激活（PluginManager 钩子）
# ==================================================================

def activate_plugin(record: InstalledPlugin, payload_dir: Path) -> InstalledPlugin:
    """激活插件负载：技能入库 + MCP 合并 + 工具注册 + PLUGIN 实体登记。

    单组件失败不阻断整体（记 WARNING 继续），与内置实体发现的容错口径一致。
    """
    try:
        manifest = parse_manifest(payload_dir)
    except PluginError as e:
        log(f"插件清单失效，跳过激活: {record.name} - {e}", "ERROR", tag=_TAG)
        return record

    record.skills = _activate_skills(record.name, payload_dir, manifest.skills)
    record.skills += _activate_commands(record.name, payload_dir, manifest.commands_dir)
    record.mcp_servers = _activate_mcp_servers(record.name, payload_dir, manifest)
    record.tools = _activate_tools(record.name, payload_dir, manifest)
    _register_plugin_entity(record)
    log(
        f"插件已激活: {record.name} "
        f"(技能 {len(record.skills)} / 工具 {len(record.tools)} / MCP {len(record.mcp_servers)})",
        tag=_TAG,
    )
    return record


def deactivate_plugin(record: InstalledPlugin) -> None:
    """去激活：按记录名回收工具 / MCP server / 技能，注销 PLUGIN 实体。"""
    _deactivate_tools(record)
    _deactivate_mcp_servers(record)
    _deactivate_skills(record)
    EntityRegistry.unregister(f"plugin:{record.name}")
    log(f"插件已去激活: {record.name}", tag=_TAG)


def activate_installed_plugins() -> int:
    """启动时激活全部已安装且启用的插件，返回激活数。

    由 discover_entities() 在内置实体扫描后调用；单个插件失败不阻断启动。
    """
    from core.plugins import get_plugin_manager
    from core.plugins.store import plugin_payload_dir

    manager = get_plugin_manager()
    wire_plugin_manager(manager)
    activated = 0
    for record in manager.list_plugins():
        if not record.enabled:
            continue
        payload = plugin_payload_dir(record.name)
        if not payload.is_dir():
            log(f"插件负载目录缺失，跳过激活: {record.name} ({payload})", "WARNING", tag=_TAG)
            continue
        try:
            refreshed = activate_plugin(record, payload)
            manager.registry.upsert(refreshed)
            activated += 1
        except Exception as e:
            log(f"插件激活失败: {record.name} - {e}", "ERROR", tag=_TAG)
    if activated:
        log(f"插件激活完成: {activated} 个", tag=_TAG)
    return activated


def wire_plugin_manager(manager) -> None:
    """把激活与变更钩子注入插件管理器（幂等）。"""
    manager.on_activate = activate_plugin
    manager.on_deactivate = deactivate_plugin
    manager.on_change = notify_plugin_change


def notify_plugin_change(action: str, record: InstalledPlugin) -> None:
    """插件变更通知：仅非 AI 会话发起的操作（Web/外部路径）推送告知 AI。

    AI 工具路径发起的操作经工具返回值直达发起会话，重复推送是噪音；
    PushHub 线程安全（call_soon_threadsafe 回主循环），安装全程在
    to_thread 工作线程执行无碍。
    """
    from entities._sdk import get_current_scope, push_notify

    if get_current_scope() != "_global":
        return
    verb = _ACTION_VERBS.get(action, action)
    push_notify(f"插件 {record.name} {verb}。{roster_line(record)}", "plugins")


# ==================================================================
# 技能
# ==================================================================

def _activate_skills(plugin_name: str, payload_dir: Path, skill_roots: List[str]) -> List[str]:
    """把插件技能目录拷入 workspace/skills/（同名冲突加插件名前缀）。"""
    linked: List[str] = []
    skills_base = Path(workspace_root()) / "skills"
    skills_base.mkdir(parents=True, exist_ok=True)
    for root_rel in skill_roots:
        root = payload_dir / root_rel
        if not root.is_dir():
            continue
        skill_dirs = sorted(
            d for d in root.iterdir() if d.is_dir() and (d / "SKILL.md").is_file()
        )
        if (root / "SKILL.md").is_file():
            skill_dirs = [root]
        for skill_dir in skill_dirs:
            try:
                target_name = _resolve_skill_target(skills_base, plugin_name, skill_dir.name)
                target = skills_base / target_name
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(skill_dir, target)
                (target / _SKILL_MARKER).write_text(plugin_name, encoding="utf-8")
                linked.append(target_name)
            except OSError as e:
                log(f"插件技能入库失败: {skill_dir.name} - {e}", "WARNING", tag=_TAG)
    return linked


def _activate_commands(plugin_name: str, payload_dir: Path, commands_dir: str) -> List[str]:
    """把插件 commands/*.md 斜杠命令转换为技能入库（``<插件>__<命令名>`` 目录）。

    命令文件可带 YAML frontmatter（description 等），正文作为技能内容；
    user_invocable 置真，用户可直接 /命令名 手势触发。
    """
    if not commands_dir:
        return []
    root = payload_dir / commands_dir
    if not root.is_dir():
        return []
    import yaml

    skills_base = Path(workspace_root()) / "skills"
    skills_base.mkdir(parents=True, exist_ok=True)
    linked: List[str] = []
    for cmd_file in sorted(root.glob("*.md")):
        cmd_name = cmd_file.stem
        try:
            text = cmd_file.read_text(encoding="utf-8")
            meta, body = _split_command_frontmatter(text, yaml)
            description = str(meta.get("description") or "").strip() or f"{plugin_name} 插件的 /{cmd_name} 命令"
            skill_meta = {
                "name": f"{plugin_name}__{cmd_name}",
                "description": description,
                "user_invocable": True,
                "trigger_patterns": [cmd_name],
            }
            target_name = _resolve_skill_target(skills_base, plugin_name, skill_meta["name"])
            target = skills_base / target_name
            if target.exists():
                shutil.rmtree(target)
            target.mkdir(parents=True)
            (target / "SKILL.md").write_text(
                f"---\n{yaml.safe_dump(skill_meta, allow_unicode=True, sort_keys=False)}---\n\n{body.strip()}\n",
                encoding="utf-8",
            )
            (target / _SKILL_MARKER).write_text(plugin_name, encoding="utf-8")
            linked.append(target_name)
        except (OSError, yaml.YAMLError) as e:
            log(f"插件命令转换失败: {cmd_file.name} - {e}", "WARNING", tag=_TAG)
    if linked:
        log(f"插件命令已转换为技能: {plugin_name} → {', '.join(linked)}", tag=_TAG)
    return linked


def _split_command_frontmatter(text: str, yaml) -> tuple:
    """拆分命令文件的 YAML frontmatter 与正文（无 frontmatter 时返回空 meta）。"""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    raw_meta = text[3:end].strip()
    body = text[end + 4:]
    meta = yaml.safe_load(raw_meta) if raw_meta else {}
    return (meta if isinstance(meta, dict) else {}), body


def _resolve_skill_target(skills_base: Path, plugin_name: str, skill_name: str) -> str:
    """确定技能落库目录名：无冲突用原名，被非本插件占用时加 ``<插件>__`` 前缀。"""
    target = skills_base / skill_name
    if not target.exists():
        return skill_name
    marker = target / _SKILL_MARKER
    if marker.is_file() and marker.read_text(encoding="utf-8", errors="ignore").strip() == plugin_name:
        return skill_name
    prefixed = f"{plugin_name}__{skill_name}"
    log(f"技能名冲突，以前缀入库: {skill_name} → {prefixed}", "WARNING", tag=_TAG)
    return prefixed


def _deactivate_skills(record: InstalledPlugin) -> None:
    skills_base = Path(workspace_root()) / "skills"
    for name in record.skills:
        target = skills_base / name
        marker = target / _SKILL_MARKER
        try:
            if not marker.is_file() or marker.read_text(
                encoding="utf-8", errors="ignore"
            ).strip() != record.name:
                continue
            shutil.rmtree(target)
        except OSError as e:
            log(f"插件技能移除失败: {name} - {e}", "WARNING", tag=_TAG)


# ==================================================================
# MCP server
# ==================================================================

def _activate_mcp_servers(plugin_name: str, payload_dir: Path, manifest) -> List[str]:
    """把插件声明的 MCP server 合并进 mcp_servers.json（带 plugin 来源标记）。

    reconcile 语义（重复激活幂等）：
    - 本插件来源（plugin 字段）的既有 server 就地更新，不再视为同名冲突——
      配置文件跨进程持久化，重启后上次合并的产物仍在，误当冲突会每次
      启动增生一层前缀副本
    - 非本插件占用同名时才加 ``<插件>__`` 前缀
    - 本插件残留但清单已不声明的 server 直接回收
    """
    from entities.mcp.config import MCPServerStore

    try:
        servers = load_plugin_mcp_servers(payload_dir, manifest)
    except PluginError as e:
        log(f"插件 MCP 配置无效: {plugin_name} - {e}", "WARNING", tag=_TAG)
        return []

    store = MCPServerStore()  # 批量合并期间不逐条热重载，结束后统一触发一次
    raw = store.load_config().get("mcpServers", {})
    # 本插件此前合并的 server 名（plugin 来源标记为凭据）
    owned = {
        name for name, cfg in raw.items()
        if isinstance(cfg, dict) and cfg.get("plugin") == plugin_name
    }
    foreign = set(raw) - owned
    if not servers and not owned:
        return []

    added: List[str] = []
    for server_name, cfg in sorted(servers.items()):
        final_name = server_name
        leftover = f"{plugin_name}__{server_name}"
        if leftover in owned and final_name in foreign:
            # 本插件此前已合并出"前缀"残留 + 清单纯名当前为 foreign
            # —— 区分两种 foreign 场景：
            # ① 清单纯名无 plugin 标记（或标记被外部抹掉）→ 视为可接管，回收前缀
            # ② 清单纯名 plugin 标记为他插件（other）→ 不能抢他人位置，保留前缀
            legacy = raw.get(leftover) or {}
            pure = raw.get(final_name) or {}
            if (
                isinstance(legacy, dict)
                and isinstance(pure, dict)
                and legacy.get("plugin") == plugin_name
                and not pure.get("plugin")  # 纯名无 plugin 标记 → 视为可接管
            ):
                # 回收前缀和清单纯名（清单纯名无明确归属，可接管）
                # 把清单纯名残留的 enabled/stay_awake/env/headers 治理字段
                # 先并入 cfg，再删除两个旧条目，最后 create_server 重建
                for governed in ("enabled", "stay_awake"):
                    if governed in pure:
                        cfg[governed] = pure[governed]
                for field in ("env", "headers"):
                    p_def = cfg.get(field, {})
                    p_over = pure.get(field, {})
                    if isinstance(p_def, dict) and isinstance(p_over, dict) and (p_def or p_over):
                        cfg[field] = {**p_def, **p_over}
                for stale in (leftover, final_name):
                    try:
                        store.remove_server(stale)
                    except ValueError:
                        pass
                if leftover in owned:
                    owned.discard(leftover)
                foreign.discard(final_name)
            else:
                # 纯名被他人明确占据或前置条件不符 → 保留前缀
                final_name = leftover
                log(f"MCP server 名冲突，以前缀合并: {server_name} → {final_name}", "WARNING", tag=_TAG)
        elif final_name in foreign:
            # 清单纯名被他人明确占据 → 本插件以前缀形式保留
            final_name = leftover
            log(f"MCP server 名冲突，以前缀合并: {server_name} → {final_name}", "WARNING", tag=_TAG)
        cfg = dict(cfg)
        cfg["plugin"] = plugin_name
        try:
            if final_name in owned:
                # 重激活合并策略（清单优先 + 治理字段保护）：
                # 1) 启停/常驻（enabled/stay_awake）是用户治理状态——保留旧值，
                #    防止 replace 整体重建时把禁用翻回启用。
                # 2) env / headers 按"清单声明为底、运行配置为覆盖"合并——env
                #    常含密钥，密钥更新需走完整配置变更路径而非清单覆盖。
                # 3) 其他字段全部以清单为准（command/args/transport/timeout 等
                #    连接参数随版本升级由清单主导）。
                existing = raw.get(final_name)
                if isinstance(existing, dict):
                    for governed in ("enabled", "stay_awake"):
                        if governed in existing:
                            cfg[governed] = existing[governed]
                    for field in ("env", "headers"):
                        defaults = cfg.get(field, {})
                        overrides = existing.get(field, {})
                        if isinstance(defaults, dict) and isinstance(overrides, dict) and (defaults or overrides):
                            cfg[field] = {**defaults, **overrides}
                store.update_server_config(final_name, cfg, replace=True, reload=False)
                owned.discard(final_name)
            else:
                store.create_server(final_name, cfg)
                foreign.add(final_name)
            added.append(final_name)
        except ValueError as e:
            log(f"插件 MCP server 合并失败: {server_name} - {e}", "WARNING", tag=_TAG)
    # 清单已不声明的残留 server 回收
    for stale in sorted(owned):
        try:
            store.remove_server(stale)
            log(f"插件 MCP server 残留已回收: {stale}", tag=_TAG)
        except ValueError as e:
            log(f"插件 MCP server 残留回收失败: {stale} - {e}", "WARNING", tag=_TAG)
    if added or owned:
        _reload_mcp_bridge()
    return added


def _deactivate_mcp_servers(record: InstalledPlugin) -> None:
    if not record.mcp_servers:
        return
    from entities.mcp.config import MCPServerStore

    store = MCPServerStore()
    removed = 0
    for name in record.mcp_servers:
        try:
            store.remove_server(name)
            removed += 1
        except ValueError as e:
            log(f"插件 MCP server 移除失败: {name} - {e}", "WARNING", tag=_TAG)
    if removed:
        _reload_mcp_bridge()


def _reload_mcp_bridge() -> None:
    """统一触发 MCP Bridge 热重载（bridge 未就绪时静默跳过，启动期由 init_mcp 接管）。"""
    try:
        from entities.mcp.bridge import get_mcp_bridge
        bridge = get_mcp_bridge()
        if bridge is not None:
            bridge.reload_config()
    except Exception as e:
        log(f"MCP 热重载失败: {e}", "WARNING", tag=_TAG)


# ==================================================================
# 工具
# ==================================================================

def _activate_tools(plugin_name: str, payload_dir: Path, manifest) -> List[str]:
    """导入插件 tools.py（@tool 注册），按注册表差集收编工具。

    重复激活幂等：先注销本插件此前收编的工具（source 标记为凭据）再导入，
    差集才能正确反映本次注册——否则已注册工具不在差集内，记录为空导致
    后续去激活无法回收（残留注册表）；从插件代码中删除的工具也因此不再回归。

    收编动作：
    - 改组到 ``plugin:<name>`` 并注册分组描述（工具目录可见，AI 可按需唤醒）
    - 默认沉睡（plugin_tools_sleep_default）：不驻留完整 schema，保持 tools
      前缀紧凑；作者显式声明 allow_sleep 或 always 标签的工具尊重原设置
    - 在当前对话 scope 自动激活分组（装完立即可用；版本号变化驱动下一轮
      重组装与目录重建，无需重启会话）
    """
    tools_file = manifest.tools_file
    if not tools_file:
        return []
    tools_path = payload_dir / tools_file
    if not tools_path.is_file():
        return []

    source_tag = f"plugin:{plugin_name}"
    for e in EntityRegistry.get_by_type(EntityType.TOOL):
        if e.source == source_tag:
            EntityRegistry.unregister(e.name)

    module_name = f"anelf_plugin_{plugin_name.replace('-', '_')}_tools"
    before = {e.name for e in EntityRegistry.get_by_type(EntityType.TOOL)}
    sys.modules.pop(module_name, None)
    if str(payload_dir) not in sys.path:
        sys.path.insert(0, str(payload_dir))
    try:
        spec = importlib.util.spec_from_file_location(module_name, tools_path)
        if spec is None or spec.loader is None:
            raise PluginError(f"无法加载插件工具模块: {tools_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    except Exception as e:
        log(f"插件工具模块导入失败: {plugin_name} - {e}", "ERROR", tag=_TAG)
        return []
    after = {e.name for e in EntityRegistry.get_by_type(EntityType.TOOL)}
    registered = sorted(after - before)
    if not registered:
        return []

    from dataclasses import replace

    from core.config import get_config_bool
    from entities._sdk import activate_tool_group_now, get_current_scope

    group = f"plugin:{plugin_name}"
    sleep = get_config_bool("plugin_tools_sleep_default", True)
    brief = f"插件 {plugin_name}（{len(registered)} 个工具）"
    EntityRegistry.register_group(
        group, manifest.description or f"插件 {plugin_name} 提供的工具",
    )
    for tool_name in registered:
        entity = EntityRegistry.get(tool_name)
        if entity is None:
            continue
        meta = dict(entity.meta)
        if sleep and not entity.allow_sleep and "always" not in entity.tags:
            meta["allow_sleep"] = True
            meta["sleep_brief"] = brief
        # 先注销再以新元数据注册，避免同名覆盖告警并保证索引一致
        EntityRegistry.unregister(tool_name)
        EntityRegistry.register(replace(
            entity, group=group, source=source_tag, meta=meta,
        ))
    # 对话进行中安装/启用时自动激活分组：装完当会话即可用；
    # 启动期无会话 scope，分组以沉睡形态进目录，AI 用时自行唤醒
    if get_current_scope() != "_global":
        activate_tool_group_now(group)
    log(f"插件工具注册: {plugin_name} → {', '.join(registered)}", tag=_TAG)
    return registered


def _deactivate_tools(record: InstalledPlugin) -> None:
    from core.plugins.store import plugin_payload_dir
    from entities._sdk import notify_tool_set_changed

    removed = 0
    for tool_name in record.tools:
        entity = EntityRegistry.get(tool_name)
        if entity is not None and entity.source == f"plugin:{record.name}":
            EntityRegistry.unregister(tool_name)
            removed += 1
    sys.modules.pop(f"anelf_plugin_{record.name.replace('-', '_')}_tools", None)
    payload_path = str(plugin_payload_dir(record.name))
    while payload_path in sys.path:
        sys.path.remove(payload_path)
    if removed:
        # 成员减少不触碰激活状态，仅通知重组装：下一轮把已注销工具
        # 从冻结数组与目录中滤除
        notify_tool_set_changed()


# ==================================================================
# PLUGIN 实体与事件
# ==================================================================

def _register_plugin_entity(record: InstalledPlugin) -> None:
    """把已安装插件登记为 PLUGIN 类型实体（实体清单/状态页可见）。"""
    EntityRegistry.register(EntityMetadata(
        name=f"plugin:{record.name}",
        entity_type=EntityType.PLUGIN,
        description=record.description or record.display_name or record.name,
        enabled=record.enabled,
        group="plugins",
        source=record.marketplace or record.source_type,
        meta={
            "version": record.version,
            "sha": record.sha,
            "display_name": record.display_name,
            "category": record.category,
            "skills": list(record.skills),
            "tools": list(record.tools),
            "mcp_servers": list(record.mcp_servers),
        },
    ))
