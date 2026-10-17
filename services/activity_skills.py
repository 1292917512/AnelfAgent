"""执行轮次内的技能事实投影，与有界过程正文分离保存。"""

from __future__ import annotations

from typing import Any

from core.sanitizer import sanitize_text
from core.tool_results import parse_tool_result_json


def merge_skill_evidence(run: dict[str, Any], facts: list[dict[str, Any]]) -> None:
    """合并同名技能，成功读取优先于候选，保留匹配来源。"""
    skills = {fact["name"]: fact for fact in run["skills"]}
    ranks = {"candidate": 0, "read": 1}
    for fact in facts:
        name = sanitize_text(str(fact.get("name", "")))[:200]
        mode = fact.get("mode")
        if not name or mode not in ranks:
            continue
        previous = skills.get(name, {})
        if ranks.get(previous.get("mode"), -1) > ranks[mode]:
            continue
        skills[name] = {
            **previous, "name": name, "mode": mode,
            "description": sanitize_text(str(fact.get("description") or previous.get("description", "")))[:300],
            "state": fact.get("state", previous.get("state", "")),
            "score": fact.get("score", previous.get("score")),
            "truncated": bool(fact.get("truncated")),
            **({"mcp_status": mcp_status_facts(fact["mcp_status"])} if "mcp_status" in fact else {}),
        }
    if len(skills) > 32:
        run["truncated"] = True
    run["skills"] = sorted(skills.values(), key=lambda fact: -ranks[fact["mode"]])[:32]


def record_skill_read(run: dict[str, Any], entry: dict[str, Any]) -> None:
    """从工具终态记录成功读取；截断的观测回执使用原始调用参数定位。"""
    if entry["name"] != "get_skill" or entry["status"] != "done":
        return
    arguments = parse_tool_result_json(entry["arguments"])
    result = parse_tool_result_json(entry["result"])
    arguments = arguments if isinstance(arguments, dict) else {}
    result = result if isinstance(result, dict) else {}
    if result.get("ok") is False or result.get("error"):
        return
    name = result.get("name") or arguments.get("name")
    if not isinstance(name, str) or not name:
        return
    merge_skill_evidence(run, [{"name": name, "mode": "read", "description": result.get("description", ""),
                               "truncated": entry.get("truncated", False),
                               **({"mcp_status": result["mcp_status"]} if "mcp_status" in result else {})}])


def mcp_status_facts(value: Any) -> list[dict[str, Any]]:
    """保留技能关联的有界状态快照，剥离其他工具输出字段。"""
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value[:16]:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        fact: dict[str, Any] = {"name": sanitize_text(item["name"])[:256]}
        for key in ("configured", "enabled", "connected"):
            if isinstance(item.get(key), bool) or (key == "configured" and item.get(key) is None):
                fact[key] = item.get(key)
        if isinstance(item.get("tool_count"), int):
            fact["tool_count"] = max(0, item["tool_count"])
        if item.get("activation") in ("active", "sleeping", "deactivated", "unavailable", "on_demand"):
            fact["activation"] = item["activation"]
        result.append(fact)
    return result
