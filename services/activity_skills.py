"""执行轮次内的技能事实投影，与有界过程正文分离保存。"""

from __future__ import annotations

from typing import Any

from core.sanitizer import sanitize_text
from core.tool_results import parse_tool_result_json


def merge_skill_evidence(run: dict[str, Any], facts: list[dict[str, Any]]) -> None:
    """合并同名技能，成功读取优先于显式注入与候选，保留匹配来源。"""
    skills = {fact["name"]: fact for fact in run["skills"]}
    ranks = {"candidate": 0, "explicit": 1, "read": 2}
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
                               "truncated": entry.get("truncated", False)}])
