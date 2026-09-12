"""子代理档案 schema — 委托侧代理定义的单一权威。

档案 = 模型面 + 执行面：

- **模型面**（models 有序候选池 + tier）：由 LLMManager 托管存储
  （llm_clients.json 顶层 ``sub_agents`` 键）与解析（池走查/降挡）；
- **执行面**（AgentFacets）：instructions（专职守则）/ tool_tags（reflect
  工具选择器）/ blocked_tools（追加屏蔽）/ output_schema（结构化产出契约）。
  只在委托的 reflect 临时上下文里生效，不注入任何 stable 前缀层（缓存纪律）。

内置难度档（easy/medium/hard，difficulty 1-3 的映射目标）是纯模型池，
不携带执行面——难度语义就是"换个档位的模型"；带执行面的专职代理一律
是自定义档案（tier 0）。事实归系统（schema 与校验在此）、决策归 AI
（档案内容经 create/update 工具自主维护）。

本模块是叶子：仅依赖标准库，供 agent.llm（存储宿主）与 agent.delegation
（消费方）共同引用，避免两头定义。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# 内置难度档：difficulty 参数 → 档案名的唯一映射（语义糖）
DIFFICULTY_AGENTS: Dict[int, str] = {1: "easy", 2: "medium", 3: "hard"}
DIFFICULTY_DESCRIPTIONS: Dict[int, str] = {
    1: "简单任务（检索/格式化等机械工作，最经济）",
    2: "中等任务（常规分析/执行）",
    3: "困难任务（复杂推理/多步规划，最强）",
}
BUILTIN_AGENT_NAMES = frozenset(DIFFICULTY_AGENTS.values())

# 命名子代理的合法名称：英文字母开头，字母/数字/下划线/连字符，≤32 字符
_SUB_AGENT_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")

# 执行面字段上限（写入侧硬校验，防档案膨胀撑爆子代理上下文）
MAX_INSTRUCTIONS_CHARS = 4000
MAX_TOOL_TAGS = 12
MAX_BLOCKED_TOOLS = 24
MAX_SCHEMA_CHARS = 4000


def valid_sub_agent_name(name: str) -> bool:
    return bool(_SUB_AGENT_NAME_RE.match(name or ""))


def normalize_tag_list(raw: Any, *, max_items: int) -> List[str]:
    """标签列表归一：容忍 str / list / None，按逗号/空白切分，去重保序，超限截断。"""
    if raw is None:
        return []
    items: List[str] = [raw] if isinstance(raw, str) else [
        v for v in raw if isinstance(v, str)
    ]
    result: List[str] = []
    for item in items:
        for part in re.split(r"[,\uFF0C\s]+", item):
            tag = part.strip()
            if tag and tag not in result:
                result.append(tag)
            if len(result) >= max_items:
                return result
    return result


def normalize_instructions(raw: Any) -> str:
    """专职守则归一：strip + 长度钳制。"""
    return str(raw or "").strip()[:MAX_INSTRUCTIONS_CHARS]


def parse_output_schema(raw: Any) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    """写入侧解析输出契约 → (schema, 错误信息)。合法空值返回 (None, None)。

    接受 dict 或 JSON 字符串（工具参数两种形态都合法）；非 object / 解析
    失败 / 超长返回具体错误（能力契约写入前检查，不静默吞掉）。
    """
    if raw is None or raw == "" or raw == {}:
        return None, None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None, "output_schema 不是合法 JSON"
    if not isinstance(raw, dict):
        return None, "output_schema 必须是 JSON object"
    if len(json.dumps(raw, ensure_ascii=False)) > MAX_SCHEMA_CHARS:
        return None, f"output_schema 序列化长度超上限（>{MAX_SCHEMA_CHARS} 字符）"
    return raw, None


def _validate_output_schema(raw: Any) -> Optional[Dict[str, Any]]:
    """读取侧（存储加载）的宽容解析：非法契约静默丢弃，不阻断档案加载。"""
    schema, _err = parse_output_schema(raw)
    return schema


@dataclass
class AgentFacets:
    """档案执行面：注入子代理 reflect 上下文的四项契约。"""

    instructions: str = ""
    """专职守则：替换默认子代理模板中段的补充指令（做什么/怎么做/红线）。"""
    tool_tags: List[str] = field(default_factory=list)
    """reflect 工具选择器（空 = 默认 heartbeat 常态集，可 activate_tool_group 扩展）。"""
    blocked_tools: List[str] = field(default_factory=list)
    """追加屏蔽的工具名（在角色屏蔽之外叠加）。"""
    output_schema: Optional[Dict[str, Any]] = None
    """结构化产出契约：JSON object，注入为输出纪律；产出经 JSON 提取校验。"""

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {}
        if self.instructions:
            d["instructions"] = self.instructions
        if self.tool_tags:
            d["tool_tags"] = list(self.tool_tags)
        if self.blocked_tools:
            d["blocked_tools"] = list(self.blocked_tools)
        if self.output_schema:
            d["output_schema"] = self.output_schema
        return d

    @classmethod
    def from_dict(cls, raw: Any) -> "AgentFacets":
        if not isinstance(raw, dict):
            return cls()
        return cls(
            instructions=normalize_instructions(raw.get("instructions")),
            tool_tags=normalize_tag_list(
                raw.get("tool_tags"), max_items=MAX_TOOL_TAGS,
            ),
            blocked_tools=normalize_tag_list(
                raw.get("blocked_tools"), max_items=MAX_BLOCKED_TOOLS,
            ),
            output_schema=_validate_output_schema(raw.get("output_schema")),
        )

    def __bool__(self) -> bool:
        return bool(
            self.instructions or self.tool_tags
            or self.blocked_tools or self.output_schema
        )


@dataclass
class SubAgentProfile:
    """子代理档案：模型面（候选池 + tier）+ 执行面（facets）。

    统一注册表：内置难度档（easy/medium/hard，tier 1-3，delegate_task 的
    difficulty 参数是其语法糖）与自定义档案（tier 0）同构存储、同套 CRUD。
    解析时按池内顺序取首个可用模型；内置难度档保留降挡（hard→medium→easy）。
    """

    name: str
    models: List[str] = field(default_factory=list)
    description: str = ""
    tier: int = 0
    """难度挡位：0 = 自定义档案；1/2/3 = 内置难度档（受保护，不可删除/改名）。"""
    facets: AgentFacets = field(default_factory=AgentFacets)
    """执行面（内置难度档恒为空——难度语义只是换模型，不换行为契约）。"""

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "models": list(self.models),
            "description": self.description,
        }
        if self.tier:
            d["tier"] = self.tier
        if self.facets:
            d.update(self.facets.to_dict())
        return d
