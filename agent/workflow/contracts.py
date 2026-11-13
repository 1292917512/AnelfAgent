"""工作流输入绑定与结果验收的纯函数契约。

模型看到的背景仍由引擎有界呈现；完整依赖摘要只用于内部复用判定。
Token 影响：摘要不注入模型，零增量。
缓存影响：不改写会话、工具目录或稳定前缀。
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any

from agent.workflow.spec import GateSpec, WorkflowStepSpec


def content_digest(text: str) -> str:
    """完整文本摘要，不以展示预览代替执行证据。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def input_digest(value: Mapping[str, Any]) -> str:
    """规范化 JSON 的完整摘要。"""
    return content_digest(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def receipt_origin(node: Mapping[str, Any]) -> str:
    """缓存导入沿用原始执行身份；新尝试即使文本相同也不等于旧证据。"""
    return str(node.get("result_origin") or f"{node['run_id']}/{node['step_key']}/{node['ordinal']}")


def step_input(step: WorkflowStepSpec, context: str) -> dict[str, Any]:
    """实际派发输入；兼容旧 journal 的输入摘要计算。"""
    if step.kind == "ask":
        return {"goal": step.goal, "context": context, "agent": step.agent_name,
                "continue_from": step.continue_from}
    return {"tool": step.tool, "args": step.args}


def bind_input(step: WorkflowStepSpec, context: str,
               dependencies: Mapping[str, Mapping[str, str]]) -> str:
    """绑定派发输入、验收契约与完整依赖证据，包含隐式续聊依赖。"""
    return "v2:" + input_digest({
        "input": step_input(step, context),
        "gate": step.gate.model_dump() if step.gate is not None else None,
        "dependencies": dependencies,
    })


def _json_equal(actual: Any, expected: Any) -> bool:
    """JSON 值递归比较；布尔与数字分离，JSON 数字允许整数/浮点等值。"""
    if type(actual) in (int, float) and type(expected) in (int, float):
        if (isinstance(actual, float) and not math.isfinite(actual)) \
                or (isinstance(expected, float) and not math.isfinite(expected)):
            return False
        return bool(actual == expected)
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(
            _json_equal(value, expected[key]) for key, value in actual.items())
    if isinstance(actual, list):
        return len(actual) == len(expected) and all(
            _json_equal(left, right) for left, right in zip(actual, expected, strict=True))
    return bool(actual == expected)


def check_gate(result_text: str, gate: GateSpec) -> tuple[bool, Any]:
    """严格读取 JSON 点路径；缺失与显式 null 分离，不接受非有限数字。"""
    def reject_constant(value: str) -> Any:
        raise ValueError(f"非 JSON 数字: {value}")

    try:
        actual = json.loads(result_text, parse_constant=reject_constant)
    except ValueError:
        return False, "<非 JSON 结果>"
    for part in gate.field.split("."):
        if isinstance(actual, dict) and part in actual:
            actual = actual[part]
        elif isinstance(actual, list) and part.isascii() and part.isdigit():
            try:
                index = int(part)
            except ValueError:
                return False, "<字段不存在>"
            if index >= len(actual):
                return False, "<字段不存在>"
            actual = actual[index]
        else:
            return False, "<字段不存在>"
    return _json_equal(actual, gate.equals), actual
