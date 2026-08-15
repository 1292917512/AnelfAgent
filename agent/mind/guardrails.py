"""工具调用守卫 — 程序级死循环防护（参考 hermes-agent tool_guardrails）。

在工具执行前后跟踪调用历史，检测三类异常模式并给出处置决策：
1. 精确失败重复：同工具同参数连续失败（warn / block）
2. 同工具连续失败：不同参数但同工具 N 次连续失败（warn / halt）
3. 无进展循环：幂等工具同参数反复返回相同结果（warn / block）

决策动作：
- allow:  放行
- warn:   在工具结果尾部追加警告文本，提示 AI 换策略
- block:  不执行真实工具，直接返回合成错误结果（需开启 hard_stop）
- halt:   终止本轮思维循环（force_end）

本模块为纯逻辑实现，不依赖 Mind/PFC，便于独立测试。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Dict, Optional

from core.log import log

# ------------------------------------------------------------------
# 配置
# ------------------------------------------------------------------


@dataclass(frozen=True)
class GuardrailConfig:
    """工具守卫阈值配置。"""

    enabled: bool = True
    hard_stop_enabled: bool = False
    exact_failure_warn_after: int = 2
    exact_failure_block_after: int = 5
    same_tool_failure_warn_after: int = 3
    same_tool_failure_halt_after: int = 8
    no_progress_warn_after: int = 2
    no_progress_block_after: int = 5

    @classmethod
    def from_config_manager(cls) -> "GuardrailConfig":
        """从 ConfigManager 加载（不可用时使用默认值）。"""
        from core.config import get_config_bool, get_config_int
        return cls(
            enabled=get_config_bool("guardrails_enabled", True),
            hard_stop_enabled=get_config_bool("guardrails_hard_stop_enabled", False),
            exact_failure_warn_after=get_config_int("guardrails_exact_failure_warn_after", 2),
            exact_failure_block_after=get_config_int("guardrails_exact_failure_block_after", 5),
            same_tool_failure_warn_after=get_config_int("guardrails_same_tool_failure_warn_after", 3),
            same_tool_failure_halt_after=get_config_int("guardrails_same_tool_failure_halt_after", 8),
            no_progress_warn_after=get_config_int("guardrails_no_progress_warn_after", 2),
            no_progress_block_after=get_config_int("guardrails_no_progress_block_after", 5),
        )


# ------------------------------------------------------------------
# 决策与签名
# ------------------------------------------------------------------

_ACTION_ALLOW = "allow"
_ACTION_WARN = "warn"
_ACTION_BLOCK = "block"
_ACTION_HALT = "halt"


@dataclass(frozen=True)
class GuardrailDecision:
    """守卫决策结果。"""

    action: str = _ACTION_ALLOW
    reason: str = ""
    message: str = ""
    count: int = 0
    tool_name: str = ""

    @property
    def should_block(self) -> bool:
        return self.action == _ACTION_BLOCK

    @property
    def should_halt(self) -> bool:
        return self.action == _ACTION_HALT

    @property
    def should_warn(self) -> bool:
        return self.action in (_ACTION_WARN, _ACTION_HALT)


_ALLOW = GuardrailDecision()


class ToolCallSignature:
    """工具调用签名：工具名 + 规范化参数的哈希（不存原始参数，节省内存）。"""

    __slots__ = ("tool_name", "args_hash")

    def __init__(self, tool_name: str, args_hash: str) -> None:
        self.tool_name = tool_name
        self.args_hash = args_hash

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, ToolCallSignature)
            and self.tool_name == other.tool_name
            and self.args_hash == other.args_hash
        )

    def __hash__(self) -> int:
        return hash((self.tool_name, self.args_hash))

    @classmethod
    def from_call(cls, tool_name: str, arguments: str) -> "ToolCallSignature":
        return cls(tool_name, _hash_arguments(arguments))


def _canonical_args(arguments: str) -> str:
    """将工具参数规范化为排序紧凑 JSON（无法解析时按原文处理）。"""
    if not arguments:
        return "{}"
    try:
        obj = json.loads(arguments)
    except (json.JSONDecodeError, TypeError):
        return arguments.strip()
    try:
        return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError):
        return arguments.strip()


def _hash_arguments(arguments: str) -> str:
    return hashlib.sha256(_canonical_args(arguments).encode("utf-8")).hexdigest()[:16]


def _hash_result(result: str) -> str:
    return hashlib.sha256((result or "").encode("utf-8")).hexdigest()[:16]


# 分级提醒中参数预览的字符上限（对齐 dsh argumentsPreviewChars）：
# 截断的只是给模型看的文本，检测永远用全量 canonical 串比较
# ——"cap bounds the reminder, never the detection"
_ARGS_PREVIEW_CHARS = 500


def _args_preview(arguments: str) -> str:
    """生成有界的参数预览文本（供分级提醒的详细报告使用）。"""
    canonical = _canonical_args(arguments)
    if len(canonical) <= _ARGS_PREVIEW_CHARS:
        return canonical
    return canonical[:_ARGS_PREVIEW_CHARS] + "…"


# ------------------------------------------------------------------
# 失败判定与幂等性
# ------------------------------------------------------------------

# 名称前缀启发式：只读类工具视为幂等（同参数同结果即无进展）
_IDEMPOTENT_PREFIXES = (
    "get", "list", "search", "recall", "query", "read", "fetch",
    "check", "describe", "peek", "view", "find", "lookup",
)

# 无进展检测豁免：系统提示明确鼓励用这些工具轮询等待，
# 同参数同结果是正常等待行为而非死循环
_NO_PROGRESS_EXEMPT_TOOLS = frozenset({"check_background_tasks"})


def is_idempotent_tool(tool_name: str) -> bool:
    """判断工具是否幂等（优先读实体 meta 声明，回退名称启发式）。"""
    try:
        from core.entity import EntityRegistry
        entity = EntityRegistry.get(tool_name)
        if entity is not None and "idempotent" in entity.meta:
            return bool(entity.meta["idempotent"])
    except Exception:
        log("is_idempotent_tool 异常已忽略", "DEBUG")
    lowered = tool_name.lower()
    return any(lowered.startswith(p) for p in _IDEMPOTENT_PREFIXES)


def classify_tool_failure(result: str) -> bool:
    """判定工具结果是否为失败（JSON error 键 / success=false / 文本错误信号）。"""
    if not result:
        return False
    text = result.strip()
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            # 真值判断：{"error": null/""} 是成功结果的常见字段形态，不算失败
            if parsed.get("error"):
                return True
            if parsed.get("success") is False or parsed.get("ok") is False:
                return True
            return False
    lowered = text[:300].lower()
    return lowered.startswith(("error:", "错误", "failed", "失败"))


# ------------------------------------------------------------------
# 守卫控制器
# ------------------------------------------------------------------


class GuardrailController:
    """工具调用守卫控制器：跟踪一次思维会话内的调用历史并给出决策。"""

    def __init__(self, config: Optional[GuardrailConfig] = None) -> None:
        self.config = config or GuardrailConfig.from_config_manager()
        # 签名 -> 连续精确失败次数
        self._exact_failure_counts: Dict[ToolCallSignature, int] = {}
        # 工具名 -> 连续失败次数
        self._same_tool_failure_counts: Dict[str, int] = {}
        # 签名 -> (结果哈希, 连续相同结果次数)
        self._no_progress: Dict[ToolCallSignature, tuple[str, int]] = {}
        # 首个 halt 决策（轮末生成受控终止用）
        self._halt_decision: Optional[GuardrailDecision] = None

    @property
    def halt_decision(self) -> Optional[GuardrailDecision]:
        return self._halt_decision

    def reset(self) -> None:
        """清空全部跟踪状态（新会话开始时调用）。"""
        self._exact_failure_counts.clear()
        self._same_tool_failure_counts.clear()
        self._no_progress.clear()
        self._halt_decision = None

    # ------------------------------------------------------------------
    # 执行前拦截（hard_stop 模式）
    # ------------------------------------------------------------------

    def before_call(self, tool_name: str, arguments: str) -> GuardrailDecision:
        """执行前检查：已知必败的调用直接拦截（仅 hard_stop 模式生效）。"""
        if not self.config.enabled or not self.config.hard_stop_enabled:
            return _ALLOW

        signature = ToolCallSignature.from_call(tool_name, arguments)
        exact_count = self._exact_failure_counts.get(signature, 0)
        if exact_count >= self.config.exact_failure_block_after:
            return GuardrailDecision(
                action=_ACTION_BLOCK,
                reason="repeated_exact_failure_block",
                message=(
                    f"工具 {tool_name} 以相同参数已连续失败 {exact_count} 次，"
                    "本次调用已被守卫拦截。请更换参数或改用其他工具。"
                ),
                count=exact_count,
                tool_name=tool_name,
            )

        previous = self._no_progress.get(signature)
        if previous and previous[1] >= self.config.no_progress_block_after:
            return GuardrailDecision(
                action=_ACTION_BLOCK,
                reason="idempotent_no_progress_block",
                message=(
                    f"工具 {tool_name} 以相同参数反复返回相同结果（{previous[1]} 次），"
                    "本次调用已被守卫拦截。继续调用不会产生新信息。"
                ),
                count=previous[1],
                tool_name=tool_name,
            )
        return _ALLOW

    # ------------------------------------------------------------------
    # 执行后检查
    # ------------------------------------------------------------------

    def after_call(
            self,
            tool_name: str,
            arguments: str,
            result: str,
            *,
            failed: Optional[bool] = None,
    ) -> GuardrailDecision:
        """工具执行后检查，返回处置决策。

        被拒调用也计数（对齐 dsh repeat-tool-reminder）：审批拒绝/权限拦截
        返回的 error 结果经 classify_tool_failure 判为失败并累加——"模型猛锤
        一个被拒的调用，正是要打破的循环"。守卫自身拦截（before_call block）
        的合成结果在上游以 skip_guardrail 跳过本方法，避免同一调用双重计数。
        """
        if not self.config.enabled:
            return _ALLOW

        if failed is None:
            failed = classify_tool_failure(result)
        signature = ToolCallSignature.from_call(tool_name, arguments)

        if failed:
            return self._on_failure(signature, tool_name, arguments)
        return self._on_success(signature, tool_name, result)

    def _on_failure(
            self, signature: ToolCallSignature, tool_name: str, arguments: str,
    ) -> GuardrailDecision:
        cfg = self.config
        # 失败路径：清除无进展记录，累加两类失败计数
        self._no_progress.pop(signature, None)

        exact_count = self._exact_failure_counts.get(signature, 0) + 1
        self._exact_failure_counts[signature] = exact_count

        same_count = self._same_tool_failure_counts.get(tool_name, 0) + 1
        self._same_tool_failure_counts[tool_name] = same_count

        # 同工具连续失败达到 halt 阈值 → 终止本轮（无论是否 hard_stop）
        if same_count >= cfg.same_tool_failure_halt_after:
            decision = GuardrailDecision(
                action=_ACTION_HALT,
                reason="same_tool_failure_halt",
                message=(
                    f"工具 {tool_name} 已连续失败 {same_count} 次，守卫强制结束本轮。"
                    "请停止调用该工具，向用户说明当前遇到的阻碍。"
                ),
                count=same_count,
                tool_name=tool_name,
            )
            if self._halt_decision is None:
                self._halt_decision = decision
            log(f"工具守卫 halt: {tool_name} 连续失败 {same_count} 次", "WARNING", tag="守卫")
            return decision

        if exact_count >= cfg.exact_failure_warn_after:
            log(f"工具守卫 warn: {tool_name} 同参数连续失败 {exact_count} 次", "WARNING", tag="守卫")
            return GuardrailDecision(
                action=_ACTION_WARN,
                reason="repeated_exact_failure_warning",
                message=self._graduate_exact_message(tool_name, arguments, exact_count),
                count=exact_count,
                tool_name=tool_name,
            )

        if same_count >= cfg.same_tool_failure_warn_after:
            log(f"工具守卫 warn: {tool_name} 连续失败 {same_count} 次", "WARNING", tag="守卫")
            return GuardrailDecision(
                action=_ACTION_WARN,
                reason="same_tool_failure_warning",
                message=(
                    f"工具 {tool_name} 已连续失败 {same_count} 次（参数不同）。"
                    "该工具当前可能不可用，请改用其他工具或调用 end_reply 结束。"
                ),
                count=same_count,
                tool_name=tool_name,
            )
        return _ALLOW

    def _graduate_exact_message(self, tool_name: str, arguments: str, count: int) -> str:
        """分级提醒文案（对齐 dsh repeat-tool-reminder）：首次达阈温和提示，
        之后给出完整报告（工具名 + 连续次数 + 参数预览），并明令停止该调用。"""
        if count <= self.config.exact_failure_warn_after:
            # 首次达阈：温和提醒，鼓励换思路
            return (
                f"调用 {tool_name} 似乎没有取得进展。"
                "建议换一种方式：调整参数、换用其他工具，或调用 end_reply 结束。"
            )
        # 后续：详细报告 + 明确禁令
        return (
            f"检测到重复的工具调用：\n"
            f"- 工具: {tool_name}\n"
            f"- 连续失败次数: {count}\n"
            f"- 参数: {_args_preview(arguments)}\n"
            "重复调用不会产生不同结果。请勿再以完全相同的参数调用该工具，"
            "换用其他方法或调用 end_reply 结束。"
        )

    def _on_success(
            self, signature: ToolCallSignature, tool_name: str, result: str,
    ) -> GuardrailDecision:
        # 成功路径：清零失败计数
        self._exact_failure_counts.pop(signature, None)
        self._same_tool_failure_counts.pop(tool_name, None)

        if not is_idempotent_tool(tool_name) or tool_name in _NO_PROGRESS_EXEMPT_TOOLS:
            return _ALLOW

        # 幂等工具：同签名同结果 = 无进展
        result_hash = _hash_result(result)
        previous = self._no_progress.get(signature)
        repeat_count = previous[1] + 1 if previous and previous[0] == result_hash else 1
        self._no_progress[signature] = (result_hash, repeat_count)

        if repeat_count >= self.config.no_progress_warn_after:
            log(
                f"工具守卫 warn: {tool_name} 同参数同结果 {repeat_count} 次（无进展）",
                "WARNING", tag="守卫",
            )
            return GuardrailDecision(
                action=_ACTION_WARN,
                reason="idempotent_no_progress_warning",
                message=(
                    f"工具 {tool_name} 以相同参数已返回相同结果 {repeat_count} 次，"
                    "继续调用不会产生新信息。请基于已有结果继续任务，或调用 end_reply 结束。"
                ),
                count=repeat_count,
                tool_name=tool_name,
            )
        return _ALLOW


# ------------------------------------------------------------------
# 决策应用辅助
# ------------------------------------------------------------------


def append_guardrail_guidance(result: str, decision: GuardrailDecision) -> str:
    """在工具结果尾部追加守卫警告文本（warn/halt 时调用）。"""
    guidance = (
        f"\n\n[工具守卫警告: {decision.reason}; 次数={decision.count}] "
        f"{decision.message}"
    )
    return f"{result}{guidance}"


def synthetic_block_result(decision: GuardrailDecision) -> str:
    """生成 block 决策的合成工具结果（不执行真实工具）。"""
    return json.dumps({
        "error": decision.message,
        "guardrail": {
            "reason": decision.reason,
            "count": decision.count,
            "tool": decision.tool_name,
        },
    }, ensure_ascii=False)


# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

_GUARDRAIL_CONFIGS = {
    "mind/guardrails": {
        "guardrails_enabled": {
            "description": "是否启用工具调用守卫（死循环检测）",
            "default": True,
        },
        "guardrails_hard_stop_enabled": {
            "description": "是否启用执行前硬拦截（已知必败的调用直接阻止）",
            "default": False,
        },
        "guardrails_exact_failure_warn_after": {
            "description": "同工具同参数连续失败警告阈值",
            "default": 2,
            "advanced": True,
            "unit": "次",
        },
        "guardrails_exact_failure_block_after": {
            "description": "同工具同参数连续失败拦截阈值（需开启硬拦截）",
            "default": 5,
            "advanced": True,
            "unit": "次",
        },
        "guardrails_same_tool_failure_warn_after": {
            "description": "同工具连续失败警告阈值",
            "default": 3,
            "advanced": True,
            "unit": "次",
        },
        "guardrails_same_tool_failure_halt_after": {
            "description": "同工具连续失败强制结束阈值",
            "default": 8,
            "advanced": True,
            "unit": "次",
        },
        "guardrails_no_progress_warn_after": {
            "description": "幂等工具无进展警告阈值",
            "default": 2,
            "advanced": True,
            "unit": "次",
        },
        "guardrails_no_progress_block_after": {
            "description": "幂等工具无进展拦截阈值（需开启硬拦截）",
            "default": 5,
            "advanced": True,
            "unit": "次",
        },
    },
}

from core.config import register_configs_safe  # noqa: E402

register_configs_safe(_GUARDRAIL_CONFIGS)
