"""agent.llm.reasoning 规范词汇 + 配置驱动下发引擎测试。

核心原则：引擎对模型名零特判，只读 thinking 契约。覆盖规范词汇归一、
契约解析、档位映射、嵌套字段写入。
"""

from __future__ import annotations

from agent.llm.reasoning import (
    CANONICAL_EFFORTS,
    ThinkingSpec,
    normalize_effort,
    parse_thinking_spec,
    resolve_thinking_value,
    set_nested_field,
    to_litellm_effort,
)

# ------------------------------------------------------------------
# 规范词汇
# ------------------------------------------------------------------


def test_normalize_trims_and_lowers() -> None:
    assert normalize_effort(" High ") == "high"
    assert normalize_effort("MAX") == "max"


def test_normalize_off_synonyms() -> None:
    for synonym in ("none", "disable", "disabled", "false"):
        assert normalize_effort(synonym) == "off"


def test_normalize_auto_and_default_synonyms() -> None:
    assert normalize_effort("auto") == ""
    assert normalize_effort("default") == ""


def test_normalize_empty_and_invalid() -> None:
    assert normalize_effort("") == ""
    assert normalize_effort(None) == ""
    assert normalize_effort(0) == ""
    assert normalize_effort("turbo") == ""


def test_normalize_accepts_all_seven_levels() -> None:
    for level in CANONICAL_EFFORTS:
        assert normalize_effort(level) == level


def test_to_litellm_off_maps_to_none() -> None:
    assert to_litellm_effort("off") == "none"
    for level in CANONICAL_EFFORTS:
        if level != "off":
            assert to_litellm_effort(level) == level


# ------------------------------------------------------------------
# 契约解析
# ------------------------------------------------------------------


def test_parse_thinking_spec_full() -> None:
    spec = parse_thinking_spec({
        "param": "reasoning_effort",
        "map": {"low": "low", "high": "high", "max": "max"},
        "off": "low",
    })
    assert spec is not None
    assert spec.param == "reasoning_effort"
    assert spec.map == {"low": "low", "high": "high", "max": "max"}
    assert spec.off == "low"


def test_parse_thinking_spec_toggle() -> None:
    spec = parse_thinking_spec({
        "param": "thinking.type", "on": "enabled", "off": "disabled",
    })
    assert spec is not None
    assert spec.param == "thinking.type"
    assert spec.on == "enabled"
    assert spec.off == "disabled"


def test_parse_thinking_spec_rejects_invalid() -> None:
    assert parse_thinking_spec(None) is None
    assert parse_thinking_spec({}) is None
    assert parse_thinking_spec("str") is None
    assert parse_thinking_spec({"map": {}}) is None  # 缺 param
    assert parse_thinking_spec({"param": "foo"}) is None  # 非法根
    assert parse_thinking_spec({"param": 123}) is None


# ------------------------------------------------------------------
# 档位解析
# ------------------------------------------------------------------


def test_resolve_map_model() -> None:
    """有档位的模型：查 map；缺省档不下发；off 用契约 off 值。"""
    spec = ThinkingSpec(
        param="reasoning_effort",
        map={"low": "low", "medium": "high", "high": "high",
             "xhigh": "max", "max": "max"},
        off="low",
    )
    assert resolve_thinking_value(spec, "low") == "low"
    assert resolve_thinking_value(spec, "medium") == "high"
    assert resolve_thinking_value(spec, "xhigh") == "max"
    assert resolve_thinking_value(spec, "off") == "low"  # 思考不可关 → off 用 low
    assert resolve_thinking_value(spec, "minimal") is None  # 未列出 → 不下发


def test_resolve_toggle_model() -> None:
    """开关型模型：非 off 统一映射 on；off 映射 off；无 off 则不下发关闭参数。"""
    spec = ThinkingSpec(param="thinking.type", on="enabled", off="disabled")
    for effort in ("minimal", "low", "medium", "high", "xhigh", "max"):
        assert resolve_thinking_value(spec, effort) == "enabled", effort
    assert resolve_thinking_value(spec, "off") == "disabled"


def test_resolve_toggle_no_off() -> None:
    """开关型但无法关闭（无 off 值）：off 不下发参数。"""
    spec = ThinkingSpec(param="thinking.type", on="adaptive")
    assert resolve_thinking_value(spec, "high") == "adaptive"
    assert resolve_thinking_value(spec, "off") is None


# ------------------------------------------------------------------
# 嵌套字段写入
# ------------------------------------------------------------------


def test_set_nested_field_flat() -> None:
    d: dict = {}
    set_nested_field(d, "reasoning_effort", "high")
    assert d == {"reasoning_effort": "high"}


def test_set_nested_field_dotted() -> None:
    d: dict = {}
    set_nested_field(d, "thinking.type", "enabled")
    assert d == {"thinking": {"type": "enabled"}}


def test_set_nested_field_preserves_existing_siblings() -> None:
    d: dict = {"thinking": {"clear_thinking": False}}
    set_nested_field(d, "thinking.type", "enabled")
    assert d == {"thinking": {"clear_thinking": False, "type": "enabled"}}
