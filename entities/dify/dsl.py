"""Dify DSL（应用 YAML）辅助 — 解析、校验、摘要与覆盖导入预处理。

Dify DSL 顶层结构（app/export 导出版本）：
- version: DSL 版本（如 "0.5.0"）
- kind: 资源类型（固定 "app"）
- app: {name, mode, icon, description, ...}
- model_config / workflow / chat_prompt_config 等按应用模式存在

本模块只做轻量结构化校验与摘要提取，完整语义校验交给 Dify 服务端
（POST /apps/imports 会返回结构化错误）。
"""

from __future__ import annotations

from typing import Any, Dict, List

import yaml


class DslError(ValueError):
    """DSL 内容错误（message 可直接展示给 AI/用户）。"""


def parse_dsl(yaml_content: str) -> Dict[str, Any]:
    """解析 DSL YAML，返回顶层字典；非法内容抛 DslError。"""
    if not yaml_content or not yaml_content.strip():
        raise DslError("DSL 内容为空")
    try:
        data = yaml.safe_load(yaml_content)
    except yaml.YAMLError as exc:
        raise DslError(f"DSL 不是合法 YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise DslError("DSL 顶层必须是 YAML 对象")
    return data


def validate_dsl(yaml_content: str) -> Dict[str, Any]:
    """校验 DSL 基本结构，返回规范化摘要 {app_name, mode, kind, version}。"""
    data = parse_dsl(yaml_content)
    app = data.get("app")
    if not isinstance(app, dict) or not str(app.get("name") or "").strip():
        raise DslError("DSL 缺少 app.name（应用名称）")
    mode = str(app.get("mode") or "").strip()
    if not mode:
        raise DslError("DSL 缺少 app.mode（应用模式，如 chat / workflow / agent-chat / completion / advanced-chat）")
    return {
        "app_name": str(app["name"]).strip(),
        "mode": mode,
        "kind": str(data.get("kind") or ""),
        "version": str(data.get("version") or ""),
        "has_workflow": isinstance(data.get("workflow"), dict),
    }


def summarize_dsl(yaml_content: str) -> Dict[str, Any]:
    """提取 DSL 摘要（应用名/模式/节点清单），供状态展示与 AI 决策参考。"""
    data = parse_dsl(yaml_content)
    app = data.get("app") or {}
    summary: Dict[str, Any] = {
        "app_name": str(app.get("name") or ""),
        "mode": str(app.get("mode") or ""),
        "description": str(app.get("description") or ""),
        "version": str(data.get("version") or ""),
    }
    workflow = data.get("workflow")
    if isinstance(workflow, dict):
        graph = workflow.get("graph") or {}
        nodes = graph.get("nodes") or []
        summary["node_count"] = len(nodes) if isinstance(nodes, list) else 0
        node_types: List[str] = []
        if isinstance(nodes, list):
            for node in nodes:
                if isinstance(node, dict):
                    node_type = ((node.get("data") or {}).get("type"))
                    if node_type:
                        node_types.append(str(node_type))
        summary["node_types"] = node_types
    return summary


def rename_dsl_app(yaml_content: str, new_name: str) -> str:
    """改写 DSL 中的应用名（导入副本场景），返回新的 YAML 文本。"""
    new_name = new_name.strip()
    if not new_name:
        raise DslError("新应用名不能为空")
    data = parse_dsl(yaml_content)
    app = data.setdefault("app", {})
    if not isinstance(app, dict):
        raise DslError("DSL app 字段结构异常")
    app["name"] = new_name
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def dsl_diff_stat(old_yaml: str, new_yaml: str) -> Dict[str, Any]:
    """计算两份 DSL 的行级差异统计（供 AI 确认变更范围，不做完整 diff）。"""
    old_lines = (old_yaml or "").splitlines()
    new_lines = (new_yaml or "").splitlines()
    import difflib

    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm=""))
    added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
    return {
        "old_lines": len(old_lines),
        "new_lines": len(new_lines),
        "added": added,
        "removed": removed,
        "changed": added > 0 or removed > 0,
    }
