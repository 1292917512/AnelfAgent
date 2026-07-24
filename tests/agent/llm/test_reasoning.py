"""agent.llm.reasoning 规范等级模块测试。

覆盖 7 级词汇归一、按供应商/模型钳制、降级阶梯、端点拒绝识别。
clamp_effort 优先采用 litellm 模型能力标志，未知模型走家族子串规则
（已对每个测试用例验证实际输出）。
"""

from __future__ import annotations

import pytest

from agent.llm.reasoning import (
    CANONICAL_EFFORTS,
    _matches_bare_token,
    clamp_effort,
    downgrade_effort,
    from_litellm_effort,
    is_effort_rejection,
    normalize_effort,
    provider_specific_effort,
    to_litellm_effort,
)


# ------------------------------------------------------------------
# normalize
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


# ------------------------------------------------------------------
# to / from litellm
# ------------------------------------------------------------------


def test_to_litellm_off_maps_to_none() -> None:
    assert to_litellm_effort("off") == "none"


def test_other_levels_pass_through() -> None:
    for level in CANONICAL_EFFORTS:
        if level != "off":
            assert to_litellm_effort(level) == level


def test_litellm_roundtrip() -> None:
    for level in CANONICAL_EFFORTS:
        assert from_litellm_effort(to_litellm_effort(level)) == level
    assert from_litellm_effort("none") == "off"
    assert from_litellm_effort("garbage") == ""


# ------------------------------------------------------------------
# 降级阶梯
# ------------------------------------------------------------------


def test_downgrade_walks_full_ladder() -> None:
    assert downgrade_effort("max") == "xhigh"
    assert downgrade_effort("xhigh") == "high"
    assert downgrade_effort("high") == "medium"
    assert downgrade_effort("medium") == "low"
    assert downgrade_effort("low") == "minimal"


def test_downgrade_bottom_returns_none_to_drop_param() -> None:
    assert downgrade_effort("minimal") is None


def test_downgrade_off_returns_none_immediately() -> None:
    # off 是显式关闭，端点拒绝时不应"降级"为开启思考
    assert downgrade_effort("off") is None


# ------------------------------------------------------------------
# 按供应商/模型钳制
# ------------------------------------------------------------------


def test_clamp_anthropic_non_adaptive_drops_max_and_xhigh() -> None:
    assert clamp_effort("max", "claude-sonnet-4-5", "anthropic") == "high"
    assert clamp_effort("xhigh", "claude-sonnet-4-5", "anthropic") == "high"


def test_clamp_anthropic_adaptive_keeps_max_drops_xhigh() -> None:
    # Opus 4.6 支持 adaptive（max 保留），但 4.6 代无 xhigh
    assert clamp_effort("max", "claude-opus-4-6", "anthropic") == "max"
    assert clamp_effort("xhigh", "claude-opus-4-6", "anthropic") == "high"


def test_clamp_anthropic_keeps_xhigh_on_4_7_plus() -> None:
    assert clamp_effort("xhigh", "claude-opus-4-7", "anthropic") == "xhigh"


def test_clamp_anthropic_preserves_standard_levels() -> None:
    assert clamp_effort("medium", "claude-sonnet-4-5", "anthropic") == "medium"
    assert clamp_effort("low", "claude-haiku-4-5", "anthropic") == "low"


def test_clamp_openai_drops_max_and_xhigh() -> None:
    assert clamp_effort("max", "gpt-5.1", "openai") == "high"
    assert clamp_effort("xhigh", "o3", "openai") == "high"


def test_clamp_openai_keeps_minimal() -> None:
    assert clamp_effort("minimal", "gpt-5.1", "openai") == "minimal"


def test_clamp_gemini_minimal_becomes_low_max_becomes_high() -> None:
    assert clamp_effort("minimal", "gemini-2.5-pro", "gemini") == "low"
    assert clamp_effort("max", "gemini-3-pro", "gemini") == "high"
    assert clamp_effort("xhigh", "gemini-2.5-flash", "gemini") == "high"


def test_clamp_xai_drops_max() -> None:
    assert clamp_effort("max", "grok-4", "xai") == "high"


def test_clamp_off_passthrough() -> None:
    assert clamp_effort("off", "claude-sonnet-4-5", "anthropic") == "off"
    assert clamp_effort("off", "gpt-5.1", "openai") == "off"


def test_clamp_empty_passthrough() -> None:
    assert clamp_effort("", "any-model", "openai") == ""


def test_clamp_anthropic_detected_via_model_substring() -> None:
    # api_type=openai 但模型名含 claude → 走 Anthropic 子串规则
    assert clamp_effort("max", "claude-sonnet-4-5", "openai") == "high"


def test_clamp_minimax_preserves_levels_for_provider_translation() -> None:
    """MiniMax 模型在 clamp 阶段保留 7 级原样：由 provider_specific_effort
    映射到 thinking.type (adaptive/disabled)。clamp 不在此处裁剪。
    """
    # clamp 不裁剪（保留 7 级以便后续 provider_specific_effort 处理）
    for effort in ("minimal", "low", "medium", "high", "xhigh", "max"):
        assert clamp_effort(effort, "MiniMax-M3", "anthropic") == effort
        assert clamp_effort(effort, "MiniMax-M2.7-highspeed", "anthropic") == effort
    # off 原样透传
    assert clamp_effort("off", "MiniMax-M3", "anthropic") == "off"


def test_clamp_kimi_preserves_levels_for_provider_translation() -> None:
    """Kimi 模型在 clamp 阶段保留 7 级原样：由 provider_specific_effort
    映射到顶层 reasoning_effort (low/high/max) 或 thinking.type (enabled/disabled)。
    """
    for effort in ("minimal", "low", "medium", "high", "xhigh", "max"):
        assert clamp_effort(effort, "kimi-k3", "anthropic") == effort
        assert clamp_effort(effort, "k3", "anthropic") == effort
        assert clamp_effort(effort, "kimi-for-coding", "anthropic") == effort
    assert clamp_effort("off", "kimi-k3", "anthropic") == "off"


def test_clamp_qwen_uses_generic_anthropic_clamp() -> None:
    """Qwen 走阿里百炼 anthropic 网关：模型名不含 claude-4-6/4-7 子串
    且不在 MiniMax/Kimi 子串表，按通用 anthropic 钳制（max/xhigh→high）。
    不强制降为 off——网关完整支持 thinking 与 reasoning_effort。"""
    assert clamp_effort("high", "qwen3.8-max-preview", "anthropic") == "high"
    assert clamp_effort("medium", "qwen3.7-plus", "anthropic") == "medium"
    assert clamp_effort("max", "qwen3.7-max", "anthropic") == "high"


def test_clamp_aliyun_deepseek_anthropic_uses_generic_clamp() -> None:
    """DeepSeek anthropic 通道无明确 reasoning_effort 文档但未列入
    MiniMax/Kimi 特殊表，按通用 anthropic 钳制（保守截断到 high）。"""
    assert clamp_effort("low", "deepseek-v4-pro", "anthropic") == "low"
    assert clamp_effort("max", "deepseek-v4-pro", "anthropic") == "high"


# ------------------------------------------------------------------
# 端点拒绝识别
# ------------------------------------------------------------------


class _HttpError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def test_is_effort_rejection_matches_known_keywords() -> None:
    assert is_effort_rejection(_HttpError("Invalid reasoning_effort: 'ultra'"))
    assert is_effort_rejection(_HttpError("thinking.budget_tokens: invalid"))
    assert is_effort_rejection(_HttpError("effort='xhigh' is not supported by this model"))


def test_is_effort_rejection_ignores_unrelated_bad_request() -> None:
    assert not is_effort_rejection(
        _HttpError("messages: text content blocks must be non-empty"),
    )


def test_is_effort_rejection_ignores_5xx() -> None:
    # 5xx 是服务端故障，不应被误判为参数问题
    assert not is_effort_rejection(
        _HttpError("reasoning_effort internal error", status_code=500),
    )


def test_is_effort_rejection_ignores_when_no_status_code() -> None:
    # 网络错误等无状态码异常不触发降级
    assert not is_effort_rejection(Exception("reasoning_effort down"))

# ------------------------------------------------------------------
# 供应商原生档位映射（MiniMax / Kimi）
# ------------------------------------------------------------------


def test_minimax_m3_maps_to_thinking_type() -> None:
    """M3 档位：off→disabled；其余档位统一映射到 adaptive（官方仅二档）。
    无档位差异。"""
    assert provider_specific_effort("off", "MiniMax-M3") == "disabled"
    assert provider_specific_effort("minimal", "MiniMax-M3") == "adaptive"
    assert provider_specific_effort("low", "MiniMax-M3") == "adaptive"
    assert provider_specific_effort("medium", "MiniMax-M3") == "adaptive"
    assert provider_specific_effort("high", "MiniMax-M3") == "adaptive"
    assert provider_specific_effort("xhigh", "MiniMax-M3") == "adaptive"
    assert provider_specific_effort("max", "MiniMax-M3") == "adaptive"


def test_minimax_m2x_always_adaptive() -> None:
    """M2.x thinking 无法关闭，所有档位都映射为 adaptive（disabled 端点忽略）。"""
    for effort in ("off", "minimal", "low", "medium", "high", "xhigh", "max"):
        assert provider_specific_effort(effort, "MiniMax-M2.7-highspeed") == "adaptive"
        assert provider_specific_effort(effort, "MiniMax-M2.7") == "adaptive"
        assert provider_specific_effort(effort, "MiniMax-M2.5") == "adaptive"


def test_kimi_k3_maps_to_top_level_reasoning_effort() -> None:
    """K3 仅 3 档顶层 reasoning_effort（low/high/max，off 不下发）。"""
    assert provider_specific_effort("off", "kimi-k3") is None  # off 不下发
    assert provider_specific_effort("minimal", "kimi-k3") == "low"
    assert provider_specific_effort("low", "kimi-k3") == "low"
    assert provider_specific_effort("medium", "kimi-k3") == "high"
    assert provider_specific_effort("high", "kimi-k3") == "high"
    assert provider_specific_effort("xhigh", "kimi-k3") == "high"
    assert provider_specific_effort("max", "kimi-k3") == "max"


def test_kimi_k3_bare_token_k3_recognized() -> None:
    """用户配置裸模型名 "k3" 必须被精确识别为 K3（不被其他模型误中）。"""
    assert provider_specific_effort("low", "k3") == "low"
    assert provider_specific_effort("high", "k3") == "high"
    assert provider_specific_effort("max", "k3") == "max"
    # 不会误中包含 "k3" 但不是 Kimi 的模型（精确 token 匹配）
    assert _matches_bare_token("k3", "k3") is True
    assert _matches_bare_token("some-k3", "k3") is True
    assert _matches_bare_token("k3-2024", "k3") is True
    assert _matches_bare_token("k3b", "k3") is False  # 不应误中
    assert _matches_bare_token("k3x-model", "k3") is False  # 不应误中


def test_kimi_k27code_always_enabled() -> None:
    """K2.7-code thinking 强制开启，type=disabled 会报错 → 所有档位→enabled。"""
    for effort in ("off", "low", "medium", "high", "xhigh", "max"):
        assert provider_specific_effort(effort, "kimi-k2.7-code") == "enabled"
        assert provider_specific_effort(effort, "kimi-for-coding") == "enabled"
        assert provider_specific_effort(
            effort, "kimi-for-coding-highspeed"
        ) == "enabled"
        assert provider_specific_effort(effort, "kimi-k2.7-code-highspeed") == "enabled"


def test_kimi_k25_k26_supports_off() -> None:
    """K2.5/K2.6 默认 enabled，可显式 disabled。"""
    assert provider_specific_effort("off", "kimi-k2.5") == "disabled"
    assert provider_specific_effort("off", "kimi-k2.6") == "disabled"
    assert provider_specific_effort("low", "kimi-k2.5") == "enabled"
    assert provider_specific_effort("high", "kimi-k2.6") == "enabled"


def test_provider_specific_returns_none_for_unsupported_models() -> None:
    """非供应商专项模型返回 None，走通用 litellm reasoning_effort 路径。"""
    assert provider_specific_effort("high", "claude-sonnet-4-5") is None
    assert provider_specific_effort("high", "gpt-5.1") is None
    assert provider_specific_effort("high", "gemini-2.5-pro") is None
    assert provider_specific_effort("high", "grok-4") is None
    # 完全无档位时直接返回 None
    assert provider_specific_effort("", "MiniMax-M3") is None
