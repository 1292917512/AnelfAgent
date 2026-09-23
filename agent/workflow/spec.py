"""工作流规格 — 声明式 DAG 的模型、校验与拓扑分层。

工作流由 AI 或用户以 JSON 声明：步骤（ask 委托子代理 / tool 调用工具）
经 depends_on 构成有向无环图；工具步可带 gate（结果门：不满足时先让
子代理按失败上下文修复再重跑，有界轮次——「模型生成、代码把关」的
声明式形态）。

校验失败收集为错误清单一次性返回（不是抛第一个），调用方可完整反馈
给 AI 自纠正。
"""

from __future__ import annotations

import hashlib
import json
import re
from graphlib import CycleError, TopologicalSorter
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ValidationError

# 步骤数上限（防 AI 生成巨型 DAG）
MAX_STEPS = 64

_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")


class WorkflowSpecError(ValueError):
    """规格非法（message 为换行分隔的完整错误清单）。"""


class GateSpec(BaseModel):
    """工具步结果门：字段值不等于期望时进入「修复→重跑」循环。

    field 是结果 JSON 的点路径（如 "exit_code"、"summary.ok"）；
    max_rounds 是工具执行总次数上限（含首轮）；repair_goal 缺省用
    通用修复提示（携带上轮结果全文）。
    """

    field: str
    equals: Any
    repair_goal: str = ""
    max_rounds: int = 3


class WorkflowStepSpec(BaseModel):
    """单个步骤定义。

    kind=ask：goal 委托子代理（可用 agent_name 指定档案）；上游步骤
    结果自动注入 context（有界），continue_from 以另一 ask 步的完整
    transcript 续聊（跨步骤连续性，等价声明一条隐式依赖边）。
    kind=tool：调用注册表工具，静态参数；gate 可选。
    """

    key: str
    kind: str = "ask"
    phase: Optional[str] = None
    depends_on: List[str] = Field(default_factory=list)
    goal: str = ""
    context: str = ""
    agent_name: str = ""
    continue_from: str = ""
    tool: str = ""
    args: Dict[str, Any] = Field(default_factory=dict)
    gate: Optional[GateSpec] = None
    retries: int = 0
    timeout: float = 0.0


class WorkflowSpec(BaseModel):
    """工作流规格（name + steps；steps 间经 depends_on 组成 DAG）。"""

    name: str
    description: str = ""
    steps: List[WorkflowStepSpec]


def validate_spec(spec: WorkflowSpec) -> List[str]:
    """完整校验，返回错误清单（空列表 = 通过）。"""
    errors: List[str] = []
    if not spec.name.strip():
        errors.append("name 不能为空")
    if not spec.steps:
        errors.append("steps 不能为空")
    if len(spec.steps) > MAX_STEPS:
        errors.append(f"步骤数超限（{len(spec.steps)} > {MAX_STEPS}），请拆分工作流")

    keys = [s.key for s in spec.steps]
    seen: set = set()
    for key in keys:
        if not _KEY_PATTERN.match(key):
            errors.append(f"步骤 key 非法（须为标识符，≤64 字符）: {key!r}")
        if key in seen:
            errors.append(f"步骤 key 重复: {key}")
        seen.add(key)

    by_key = {s.key: s for s in spec.steps}
    for step in spec.steps:
        if step.kind not in ("ask", "tool"):
            errors.append(f"步骤 {step.key}: kind 必须是 ask 或 tool")
            continue
        if step.kind == "ask":
            if not step.goal.strip():
                errors.append(f"步骤 {step.key}: ask 步骤缺少 goal")
            if step.tool:
                errors.append(f"步骤 {step.key}: ask 步骤不接受 tool 字段")
            if step.gate is not None:
                errors.append(f"步骤 {step.key}: gate 只适用于 tool 步骤")
        else:
            if not step.tool.strip():
                errors.append(f"步骤 {step.key}: tool 步骤缺少 tool")
            if step.goal:
                errors.append(f"步骤 {step.key}: tool 步骤不接受 goal 字段")
            if step.gate is not None and step.gate.max_rounds < 1:
                errors.append(f"步骤 {step.key}: gate.max_rounds 至少为 1")
        if step.phase is not None and not str(step.phase).strip():
            errors.append(f"步骤 {step.key}: phase 不能为空串（不分组请省略）")
        for dep in step.depends_on:
            if dep == step.key:
                errors.append(f"步骤 {step.key}: 不能依赖自身")
            elif dep not in by_key:
                errors.append(f"步骤 {step.key}: 依赖未定义步骤 {dep!r}")
        if step.continue_from:
            target = by_key.get(step.continue_from)
            if target is None:
                errors.append(f"步骤 {step.key}: continue_from 指向未定义步骤 {step.continue_from!r}")
            elif target.kind != "ask":
                errors.append(f"步骤 {step.key}: continue_from 只能指向 ask 步骤")
        if step.retries < 0 or step.retries > 5:
            errors.append(f"步骤 {step.key}: retries 须在 0..5")

    if errors:
        return errors

    # DAG：显式依赖 + continue_from 隐式边
    graph: Dict[str, set] = {}
    for step in spec.steps:
        deps = set(step.depends_on)
        if step.continue_from:
            deps.add(step.continue_from)
        graph[step.key] = deps
    sorter = TopologicalSorter(graph)
    try:
        sorter.prepare()
    except CycleError as exc:
        errors.append(f"步骤依赖存在环: {exc.args[1]}")
    return errors


def parse_spec(data: Any) -> WorkflowSpec:
    """解析并校验规格，非法时抛 WorkflowSpecError（完整错误清单）。"""
    try:
        spec = WorkflowSpec.model_validate(data)
    except ValidationError as exc:
        raise WorkflowSpecError(f"规格解析失败: {exc}") from exc
    errors = validate_spec(spec)
    if errors:
        raise WorkflowSpecError("\n".join(errors))
    return spec


def canonical_spec(spec: WorkflowSpec) -> str:
    """规范化序列（确定性：键排序、紧凑分隔；hash 与导入比对的基础）。"""
    return json.dumps(
        spec.model_dump(mode="json"), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    )


def spec_hash(spec: WorkflowSpec) -> str:
    """规格指纹（sha256 前 12 位十六进制）。"""
    return hashlib.sha256(canonical_spec(spec).encode("utf-8")).hexdigest()[:12]


def dependency_graph(spec: WorkflowSpec) -> Dict[str, set]:
    """调度用依赖图（显式依赖 + continue_from 隐式边）。"""
    graph: Dict[str, set] = {}
    for step in spec.steps:
        deps = set(step.depends_on)
        if step.continue_from:
            deps.add(step.continue_from)
        graph[step.key] = deps
    return graph


def topo_layers(spec: WorkflowSpec) -> List[List[str]]:
    """拓扑分层（层内可并发；层内按声明序稳定排序）。"""
    graph = dependency_graph(spec)
    order = {s.key: i for i, s in enumerate(spec.steps)}
    sorter = TopologicalSorter(graph)
    sorter.prepare()
    layers: List[List[str]] = []
    while sorter.is_active():
        ready = sorted(sorter.get_ready(), key=order.__getitem__)
        layers.append(ready)
        sorter.done(*ready)
    return layers
