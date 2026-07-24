"""
思考等级（reasoning_effort）单一权威模块。

全系统统一的 7 级规范词汇表与供应商/模型适配逻辑，参考 hermes-agent 的
resolve_reasoning_config 单点设计：所有子系统（Mind 全局、任务、心跳、
cognee、每模型专属）只产生/消费本模块定义的规范等级，由 LLMClient 在
调用 litellm 前统一钳制与映射，确保参数对任何模型都合法。

等级语义：
    off     显式关闭思考（映射为 litellm "none"）
    minimal 极简思考
    low     低
    medium  中
    high    高
    xhigh   超高（仅部分新模型支持，如 Claude 4.7+）
    max     最大（仅 Anthropic adaptive-thinking 模型支持）

空字符串 "" 一律表示"不设置/继承"。
"""

from __future__ import annotations

from typing import Any, Optional

# 规范等级（不含空值）；顺序即强度升序，也是降级阶梯的依据
CANONICAL_EFFORTS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")

# 降级阶梯（高强度 → 低强度）：端点 400 拒绝时逐级下降，None 表示丢弃参数
EFFORT_LADDER = ("max", "xhigh", "high", "medium", "low", "minimal")

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


def is_valid_effort(value: Any) -> bool:
    """判断输入是否为合法的非空规范等级。"""
    return normalize_effort(value) != ""


def to_litellm_effort(effort: str) -> str:
    """规范等级 → litellm 接受的 reasoning_effort 值（off → "none"）。"""
    return "none" if effort == "off" else effort


def from_litellm_effort(effort: str) -> str:
    """litellm reasoning_effort 值 → 规范等级（"none" → off）。"""
    return normalize_effort(effort)


def downgrade_effort(effort: str) -> Optional[str]:
    """沿降级阶梯下降一档；已到最低档返回 None（表示应丢弃参数）。

    off 不在降级阶梯中（显式关闭被端点拒绝时不应"降级"为开启思考），
    直接返回 None。
    """
    if effort not in EFFORT_LADDER:
        return None
    idx = EFFORT_LADDER.index(effort)
    if idx + 1 >= len(EFFORT_LADDER):
        return None
    return EFFORT_LADDER[idx + 1]


# ------------------------------------------------------------------
# 按供应商/模型钳制
# ------------------------------------------------------------------

# Anthropic adaptive-thinking 模型（支持 output_config.effort，含 max）
# 参考 litellm anthropic transformation 与 hermes-agent 的模型代际表
_ANTHROPIC_ADAPTIVE_SUBSTRINGS = (
    "claude-opus-4-6", "claude-opus-4.6", "claude-opus-4_6",
    "claude-sonnet-4-6", "claude-sonnet-4.6", "claude-sonnet-4_6",
    "claude-opus-4-7", "claude-opus-4.7", "claude-opus-4_7",
    "claude-sonnet-4-7", "claude-sonnet-4.7", "claude-sonnet-4_7",
    "claude-opus-4-8", "claude-opus-4.8", "claude-opus-4_8",
    "claude-sonnet-4-8", "claude-sonnet-4.8", "claude-sonnet-4_8",
    "claude-haiku-4-6", "claude-haiku-4.6", "claude-haiku-4_6",
)

# 支持 xhigh 的 Anthropic 模型（4.6 代无 xhigh，参考 hermes _NO_XHIGH_CLAUDE_SUBSTRINGS）
_ANTHROPIC_XHIGH_SUBSTRINGS = (
    "claude-opus-4-7", "claude-opus-4.7", "claude-opus-4_7",
    "claude-sonnet-4-7", "claude-sonnet-4.7", "claude-sonnet-4_7",
    "claude-opus-4-8", "claude-opus-4.8", "claude-opus-4_8",
    "claude-sonnet-4-8", "claude-sonnet-4.8", "claude-sonnet-4_8",
)

# 走 Anthropic 兼容通道但不支持 reasoning_effort 档位的供应商。
# 官方文档仅声明 thinking: {type: adaptive|disabled}（MiniMax M3）或 thinking 强制开启
# 不可关闭（MiniMax M2.x、Kimi anthropic）。任何非 off 档位若直接透传为 reasoning_effort
# 将被端点拒绝（400）；clamp 全部降为 off，由 litellm 翻译为 thinking: disabled 即可。
_NO_REASONING_EFFORT_SUBSTRINGS = (
    "minimax-m", "minimax-m2", "minimax-m3",
    "kimi-", "kimi-for-coding", "k3",
)


# ------------------------------------------------------------------
# 供应商原生档位（用于 MiniMax/Kimi 等 anthropic 兼容通道的专项 payload 转换）
# ------------------------------------------------------------------
# 把规范档（off/minimal/low/medium/high/xhigh/max）映射到供应商原生档位字符串。
# 返回 None 表示该供应商+模型完全不支持该档位（调用方应静默跳过）。
#
# 各供应商原生档位语义：
#   MiniMax M3:       "disabled" | "adaptive"   （thinking.type，仅二档）
#   MiniMax M2.x:     "adaptive"               （M2.x 始终思考，type=disabled 被忽略）
#   Kimi K3 / k3:     "low" | "high" | "max"   （顶层 reasoning_effort，3 档）
#   Kimi K2.7-code:   "enabled"                （始终思考，type=disabled 会报错）
#   Kimi K2.5/K2.6:   "enabled" | "disabled"   （默认 enabled）
_MINIMAX_M3_EFFORTS = {"off": "disabled", "low": "adaptive", "medium": "adaptive",
                       "high": "adaptive", "max": "adaptive",
                       "minimal": "adaptive", "xhigh": "adaptive"}
_MINIMAX_M2X_EFFORTS = {"off": "adaptive", "low": "adaptive", "medium": "adaptive",
                        "high": "adaptive", "max": "adaptive",
                        "minimal": "adaptive", "xhigh": "adaptive"}
# K3 顶层 reasoning_effort：3 档；off → 不传
_KIMI_K3_EFFORTS = {"off": None, "minimal": "low", "low": "low", "medium": "high",
                    "high": "high", "xhigh": "high", "max": "max"}
# K2.7-code：始终思考，所有档位映射为 enabled；off 仍 enabled（端点会报 disabled 错误）
_KIMI_K27CODE_EFFORTS = {"off": "enabled", "low": "enabled", "medium": "enabled",
                         "high": "enabled", "max": "enabled",
                         "minimal": "enabled", "xhigh": "enabled"}
# K2.5/K2.6：默认 enabled，可显式 disabled
_KIMI_K25_K26_EFFORTS = {"off": "disabled", "low": "enabled", "medium": "enabled",
                         "high": "enabled", "max": "enabled",
                         "minimal": "enabled", "xhigh": "enabled"}

# Kimi 模型代际子串（用于识别 K3 / K2.7-code / K2.5-K2.6）
# K3 字面：用户配置可能用 "k3"（裸名）或 "kimi-k3"。
# 使用"完整 token 匹配"语义（在分隔符边界上匹配），避免误中其他模型。
_KIMI_K3_SUBSTRINGS = ("kimi-k3", "kimi_k3", "kimi.k3")  # 含分隔符的复合名
# 裸 "k3" 单独检测（Kimi K3 官方裸名）
_KIMI_K3_BARE_TOKEN = "k3"
# K2.7-code：用户配置别名 kimi-for-coding 也映射到这里（同一系列）
_KIMI_K27CODE_SUBSTRINGS = ("k2.7-code", "k2_7_code", "k2-7-code", "kimi-k2.7",
                            "kimi-for-coding", "kimi-for-coding-highspeed")
_KIMI_K25_K26_SUBSTRINGS = ("k2.5", "k2_5", "k2-5", "k2.6", "k2_6", "k2-6")
# K2.7-code：用户配置别名 kimi-for-coding 也映射到这里（同一系列）
_KIMI_K27CODE_SUBSTRINGS = ("k2.7-code", "k2_7_code", "k2-7-code", "kimi-k2.7",
                            "kimi-for-coding", "kimi-for-coding-highspeed")
_KIMI_K25_K26_SUBSTRINGS = ("k2.5", "k2_5", "k2-5", "k2.6", "k2_6", "k2-6")

# MiniMax 模型代际子串
_MINIMAX_M3_SUBSTRINGS = ("minimax-m3", "minimax-m-3", "minimax_m3", "minimax.m3")
_MINIMAX_M2X_SUBSTRINGS = ("minimax-m2", "minimax-m-2", "minimax_m2", "minimax.m2",
                           "minimax-m2.7", "minimax-m2.5", "minimax-m2.1",
                           "minimax-m2.7-highspeed", "minimax-m2.5-highspeed",
                           "minimax-m2.1-highspeed")


def _is_provider_specific(model_lower: str) -> Optional[Dict[str, str]]:
    """识别走 Anthropic 兼容通道但需要供应商专项 payload 转换的供应商。

    返回供应商档位映射表（M3/M2.x/K3/K2.7-code/K2.5-K2.6）。
    否则返回 None，表示走通用 litellm reasoning_effort 路径。
    """
    if any(s in model_lower for s in _MINIMAX_M3_SUBSTRINGS):
        return _MINIMAX_M3_EFFORTS
    if any(s in model_lower for s in _MINIMAX_M2X_SUBSTRINGS):
        return _MINIMAX_M2X_EFFORTS
    if any(s in model_lower for s in _KIMI_K3_SUBSTRINGS):
        return _KIMI_K3_EFFORTS
    # 裸 "k3" token 匹配（前后为分隔符 / 字符串边界）
    if _matches_bare_token(model_lower, _KIMI_K3_BARE_TOKEN):
        return _KIMI_K3_EFFORTS
    if any(s in model_lower for s in _KIMI_K27CODE_SUBSTRINGS):
        return _KIMI_K27CODE_EFFORTS
    if any(s in model_lower for s in _KIMI_K25_K26_SUBSTRINGS):
        return _KIMI_K25_K26_EFFORTS
    return None


def _matches_bare_token(model_lower: str, token: str) -> bool:
    """模型字符串中是否含独立 token（前后为 -/_/.  或字符串边界）。"""
    if token not in model_lower:
        return False
    idx = 0
    while True:
        pos = model_lower.find(token, idx)
        if pos < 0:
            return False
        before_ok = pos == 0 or model_lower[pos - 1] in "-_./:"
        after_pos = pos + len(token)
        after_ok = after_pos == len(model_lower) or model_lower[after_pos] in "-_./:"
        if before_ok and after_ok:
            return True
        idx = pos + 1


def provider_specific_effort(effort: str, model: str) -> Optional[str]:
    """把规范档映射到供应商原生档位字符串；返回 None 表示不下发。

    用于 MiniMax/Kimi anthropic 兼容通道：这些供应商不识别 litellm 的
    reasoning_effort kwarg，必须直接把档位填到对应 API 字段。
    返回 None 表示该档位与供应商语义冲突（如下发 K2.7-code 的 disabled），
    调用方应静默跳过，避免端点 400。
    """
    if not effort:
        return None
    model_lower = (model or "").lower()
    table = _is_provider_specific(model_lower)
    if table is None:
        return None  # 非供应商专项，走通用 reasoning_effort 路径
    return table.get(effort)


def _model_capability_flag(model: str, flag: str) -> Optional[bool]:
    """查询 litellm 模型能力标志（supports_*_reasoning_effort）；未知返回 None。"""
    try:
        import litellm

        info = litellm.get_model_info(model)
        if isinstance(info, dict):
            value = info.get(flag)
            if value is not None:
                return bool(value)
    except Exception:
        pass
    return None


def _clamp_anthropic(effort: str, model_lower: str, model: str) -> str:
    """Anthropic 家族钳制：max 仅 adaptive 模型；xhigh 仅 4.7+ 代。"""
    if effort == "max":
        flag = _model_capability_flag(model, "supports_max_reasoning_effort")
        if flag is True:
            return effort
        if flag is False:
            return "high"
        if not any(s in model_lower for s in _ANTHROPIC_ADAPTIVE_SUBSTRINGS):
            return "high"
    elif effort == "xhigh":
        flag = _model_capability_flag(model, "supports_xhigh_reasoning_effort")
        if flag is True:
            return effort
        if flag is False:
            return "high"
        if not any(s in model_lower for s in _ANTHROPIC_XHIGH_SUBSTRINGS):
            return "high"
    return effort


def _clamp_generic_high(effort: str) -> str:
    """通用钳制：max/xhigh 最高只保留到 high（OpenAI/Gemini/xAI 等家族）。"""
    return "high" if effort in ("max", "xhigh") else effort


def clamp_effort(effort: str, model: str, api_type: str) -> str:
    """将规范等级钳制为指定供应商/模型可接受的等级。

    只降不升；off 与空值原样返回。优先采用 litellm 模型能力表
    （1.93 内置 supports_*_reasoning_effort 标志），查不到再走
    家族子串规则。钳制是静态最优努力，运行时仍有降级重试兜底。

    供应商专项模型（MiniMax / Kimi）走 _is_provider_specific 路径，
    由 provider_specific_effort 直接映射到供应商原生档位，本函数仅
    做轻微语义裁剪（max 在 M3 上保留，其他档位统一允许）。
    """
    if not effort or effort == "off":
        return effort
    model_lower = (model or "").lower()

    # 供应商专项模型：保留 7 级原样，由 provider_specific_effort 映射
    if _is_provider_specific(model_lower) is not None:
        return effort

    # 其他 anthropic 兼容通道但不支持 reasoning_effort 档位的供应商
    if any(s in model_lower for s in _NO_REASONING_EFFORT_SUBSTRINGS):
        return "off"

    if api_type == "anthropic" or "claude" in model_lower:
        return _clamp_anthropic(effort, model_lower, model)
    if "gemini" in model_lower:
        if effort == "minimal":
            return "low"
        return _clamp_generic_high(effort)
    return _clamp_generic_high(effort)


# ------------------------------------------------------------------
# 端点拒绝识别（运行时降级重试用）
# ------------------------------------------------------------------

_EFFORT_REJECTION_KEYWORDS = (
    "reasoning_effort",
    "reasoning effort",
    "reasoning.effort",
    "invalid effort",
    "effort=",
    "thinking",
    "budget_tokens",
)


def is_effort_rejection(exc: BaseException) -> bool:
    """判断异常是否为端点对思考参数的 400 拒绝。

    仅匹配 4xx 客户端错误（参数本身非法），5xx/网络错误不触发降级，
    避免把正常的服务端故障误判为参数问题而掩盖真实错误。
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    try:
        if status is None or not (400 <= int(status) < 500):
            return False
    except (TypeError, ValueError):
        return False
    message = str(exc).lower()
    return any(kw in message for kw in _EFFORT_REJECTION_KEYWORDS)
