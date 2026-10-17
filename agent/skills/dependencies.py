"""技能依赖 — SKILL.md frontmatter ``dependencies`` 声明的运行时检查。

技能可声明 MCP server 依赖：
    dependencies:
      - type: mcp
        name: notion
        url: https://mcp.notion.com/mcp

读取全文的 get_skill 在依赖缺失时附带提示，
引导经 mcp_manage 直接添加或经插件市场安装；无缺失时零占用。
本地 MCP 关联存于技能账本，独立于可分发的 dependencies 声明。

Model Experience：召回候选与读取技能附带关联 MCP 的只读状态，由 AI 决定唤醒。
Token：每个关联服务一段短状态，不展开工具 schema、地址或凭据。
缓存：只进入技能动态召回或工具结果，不改变稳定目录和 SKILL.md。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, List, Literal, Mapping

from agent.skills.skill_store import Skill, SkillStore
from core.latebind import LateBinding
from core.log import log


@dataclass(frozen=True)
class MCPServerState:
    """MCP 提供方事实与指定会话的工具激活状态，不含配置或凭据。"""

    enabled: bool
    connected: bool
    tool_count: int
    activation: Literal["active", "sleeping", "deactivated", "unavailable", "on_demand"]


mcp_status_port: LateBinding[Callable[[str | None], Mapping[str, MCPServerState]]] = LateBinding("skills.mcp_status")


def mcp_snapshot(scope: str | None = None) -> Mapping[str, MCPServerState]:
    """读取本地配置和内存状态，不主动连接服务器。"""
    if not mcp_status_port.bound:
        raise RuntimeError("MCP 状态服务尚未就绪")
    return mcp_status_port.get()(scope)


def set_mcp_bindings(store: SkillStore, name: str, servers: list[str]) -> Skill:
    """替换本地关联；允许保留已失效关联，但禁止新增不存在的服务。"""
    skill = store.get(name)
    if skill is None:
        raise ValueError(f"技能 '{name}' 不存在")
    names = list(dict.fromkeys(value.strip() for value in servers))
    if any(not value or len(value) > 256 for value in names) or len(names) > 16:
        raise ValueError("MCP 服务名称不能为空或超过 256 字符，最多关联 16 个服务")
    added = set(names) - set(skill.mcp_servers)
    if added:
        unknown = added - mcp_snapshot().keys()
        if unknown:
            raise ValueError(f"MCP 服务不存在: {', '.join(sorted(unknown))}；请先通过 list_mcp_servers 查看服务")
    return store.set_mcp_servers(name, names)


def mcp_binding_status(skill: Skill, scope: str | None = None) -> list[dict[str, Any]]:
    """返回关联状态；状态源不可用时明确标为未知，不误报未安装。"""
    if not skill.mcp_servers:
        return []
    try:
        snapshot = mcp_snapshot(scope)
    except Exception as exc:
        log(f"技能 MCP 状态读取失败: {exc}", "DEBUG", tag="技能")
        return [{"name": name, "configured": None} for name in skill.mcp_servers]
    return [{"name": name, "configured": name in snapshot, **(asdict(snapshot[name]) if name in snapshot else {})}
            for name in skill.mcp_servers]


def mcp_binding_notice(skill: Skill, scope: str, *, states: list[dict[str, Any]] | None = None) -> str:
    """把关联状态压缩为候选旁的短提示，不执行连接或激活动作。"""
    if states is None:
        states = mcp_binding_status(skill, scope)
    if not states:
        return ""
    items = []
    labels = {"active": "分组已激活", "sleeping": "分组未激活", "deactivated": "本会话已停用", "on_demand": "按会话唤醒", "unavailable": "工具不可用"}
    for state in states:
        if state["configured"] is None:
            status = "状态未知"
        elif not state["configured"]:
            status = "未配置"
        else:
            status = f"{'启用' if state['enabled'] else '停用'}、{'已连接' if state['connected'] else '未连接'}、{labels[state['activation']]}"
        items.append(f"{state['name']}（{status}）")
    return "关联 MCP: " + "；".join(items) + "。按任务需要连接或 activate_tool_group(group='mcp:服务名')；关联不代表已唤醒。"


def missing_mcp_dependencies(skill: Skill) -> List[Dict[str, Any]]:
    """返回技能声明但当前未安装的 MCP server 依赖列表（无声明/无缺失返回空）。"""
    declared = [
        d for d in (skill.dependencies or [])
        if isinstance(d, dict) and d.get("type") == "mcp" and d.get("name")
    ]
    if not declared:
        return []
    try:
        existing = set(mcp_snapshot())
    except Exception as e:
        log(f"技能依赖检查读取 MCP 配置失败: {e}", "DEBUG", tag="技能")
        return []
    missing = []
    for dep in declared:
        name = str(dep["name"])
        # 插件合并的冲突前缀名（<插件>__<name>）视为已满足
        if name in existing or any(n.endswith(f"__{name}") for n in existing):
            continue
        missing.append(dep)
    return missing


def dependency_notice(skill: Skill) -> str:
    """依赖缺失时生成给 AI 的引导文本；无缺失返回空串（零占用）。"""
    missing = missing_mcp_dependencies(skill)
    if not missing:
        return ""
    items = "、".join(
        f"{d['name']}（{d.get('url') or d.get('command') or '见技能说明'}）"
        for d in missing
    )
    return (
        f"[依赖提示] 本技能依赖的 MCP server 未安装: {items}。"
        f"可用 mcp_manage 工具直接添加，或经 search_plugins / install_plugin "
        f"从插件市场检索安装对应插件后重试。"
    )
