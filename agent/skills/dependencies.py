"""技能依赖 — SKILL.md frontmatter ``dependencies`` 声明的运行时检查。

技能可声明 MCP server 依赖：
    dependencies:
      - type: mcp
        name: notion
        url: https://mcp.notion.com/mcp

消费全文的路径（get_skill / 手势强制注入）在依赖缺失时附带提示，
引导经 mcp_manage 直接添加或经插件市场安装；无缺失时零占用。
"""
from __future__ import annotations

from typing import Any, Dict, List

from core.log import log


def missing_mcp_dependencies(skill) -> List[Dict[str, Any]]:
    """返回技能声明但当前未安装的 MCP server 依赖列表（无声明/无缺失返回空）。"""
    declared = [
        d for d in (skill.dependencies or [])
        if isinstance(d, dict) and d.get("type") == "mcp" and d.get("name")
    ]
    if not declared:
        return []
    try:
        from entities.mcp.config import MCPServerStore
        existing = set(MCPServerStore().get_server_names())
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


def dependency_notice(skill) -> str:
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
