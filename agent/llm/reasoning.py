"""思考等级（reasoning_effort）单一权威模块：7 级规范词汇 + 配置驱动下发。

全系统统一的规范词汇表：所有子系统（Mind 全局、任务、心跳、cognee、每模型
专属）只产生/消费本模块定义的规范等级。

核心原则：**本模块不含任何模型名/供应商特判**。模型如何下发思考参数
（目标字段、档位映射、关闭档语义）由每个模型配置的 ``thinking`` 契约声明
（见 agent.llm.config.LLMClientConfig），LLMClient 只做"读契约填值"。

Model Experience：① 模型看到的只是思考档位参数（reasoning_effort 顶层 /
thinking.type 对象），不注入 prompt 内容；② token 影响仅思考预算档位本身；
③ 参数经 extra_body 尾部合并，不触碰 prompt 前缀层，无缓存影响。

等级语义：
    off     显式关闭思考（litellm 侧记作 "none"）
    minimal 极简思考
    low     低
    medium  中
    high    高
    xhigh   超高
    max     最大

空字符串 "" 一律表示"不设置/继承"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

# 规范等级（不含空值）；顺序即强度升序
CANONICAL_EFFORTS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")

# 同义词归一（外部输入兼容）
_EFFORT_SYNONYMS = {
    "none": "off",
    "disable": "off",
    "disabled": "off",
    "false": "off",
    "auto": "",
    "default": "",
}


def normalize_effort(value: Any) -> str:
    """标准化思考等级输入：trim/lower + 同义词归一；非法值返回 ""。"""
    if value is None:
        return ""
    effort = str(value).strip().lower()
    if not effort:
        return ""
    effort = _EFFORT_SYNONYMS.get(effort, effort)
    return effort if effort in CANONICAL_EFFORTS else ""


def to_litellm_effort(effort: str) -> str:
    """规范等级 → 顶层 reasoning_effort 透传值（off → "none"）。"""
    return "none" if effort == "off" else effort


# ------------------------------------------------------------------
# 思考契约（模型配置驱动的下发规格）
# ------------------------------------------------------------------

# 合法的目标字段路径（第一级必须是这两个之一，防止任意注入）
_VALID_PARAM_ROOTS = ("reasoning_effort", "thinking")


@dataclass(frozen=True)
class ThinkingSpec:
    """模型思考契约的解析结果：把规范档解析为"字段路径 + 原生值"。

    由模型配置 ``thinking`` 对象声明（供应商无关）：
      - ``param``   目标字段路径（如 reasoning_effort / thinking.type）
      - ``map``     规范档 → 原生档映射（有档位的模型；缺省档不映射，视为不支持）
      - ``on``      无档位差异的开关型模型开启思考的值（如 enabled / adaptive）
      - ``off``     显式关闭思考的值；缺省表示该模型无法关闭（off 不下发参数）
    """

    param: str
    map: Mapping[str, str] = field(default_factory=dict)
    on: str = ""
    off: Optional[str] = None


def parse_thinking_spec(raw: Any) -> Optional[ThinkingSpec]:
    """把模型配置的 ``thinking`` 对象解析为 ThinkingSpec；非法/缺失返回 None。

    宽容解析：结构不合法时返回 None（调用方回退通用透传），不抛异常——
    配置项不应阻断启动主流程。
    """
    if not isinstance(raw, dict) or not raw:
        return None
    param = raw.get("param")
    if not isinstance(param, str) or not param.strip():
        return None
    param = param.strip()
    if param.split(".", 1)[0] not in _VALID_PARAM_ROOTS:
        return None
    mapping = raw.get("map")
    if mapping is not None and not isinstance(mapping, dict):
        return None
    on = raw.get("on")
    off = raw.get("off")
    if on is not None and not isinstance(on, str):
        return None
    if off is not None and not isinstance(off, str):
        return None
    return ThinkingSpec(
        param=param,
        map={str(k): str(v) for k, v in (mapping or {}).items()},
        on=(on or "").strip(),
        off=off.strip() if isinstance(off, str) else None,
    )


def resolve_thinking_value(spec: ThinkingSpec, effort: str) -> Optional[Any]:
    """按契约把规范档解析为原生值；返回 None 表示该档位不下发。

    - effort == "off"：有 off 值就返回该值（关闭思考），没有则不下发关闭参数。
    - 有档位的模型（map 非空）：查 map，未列出的档视为不支持 → 不下发。
    - 开关型模型（map 为空、on 非空）：所有非 off 档统一映射为 on 值。
    """
    if effort == "off":
        return spec.off
    if spec.map:
        return spec.map.get(effort)
    if spec.on:
        return spec.on
    return None


def set_nested_field(container: dict, dotted: str, value: Any) -> None:
    """按点号路径把值写入嵌套 dict（如 thinking.type → container["thinking"]["type"]）。

    仅支持单层嵌套（两段路径），与 _VALID_PARAM_ROOTS 对齐。
    """
    if "." in dotted:
        root, leaf = dotted.split(".", 1)
        node = container.get(root)
        if not isinstance(node, dict):
            node = {}
            container[root] = node
        node[leaf] = value
    else:
        container[dotted] = value
