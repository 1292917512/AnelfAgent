"""子代理 — 隔离执行的二级思维单元。

子代理通过 mind.reflect() 在隔离的上下文中执行子任务：
- 独立的消息上下文（不污染主对话）
- 受限工具集（默认禁止外发消息；档案执行面可收紧/放宽工具选择器）
- 角色模型：leaf（不可再委托）/ orchestrator（可再委托，受深度限制）
- 独立迭代预算（防止无限循环）
- 执行面契约（AgentFacets）：instructions 专职守则 / tool_tags 工具
  选择器 / blocked_tools 追加屏蔽 / output_schema 结构化产出

委托深度通过 contextvars 跟踪，异步任务间隔离。

产物三件套（经 delegation.journal 落盘）：进度流行（执行期间由
DelegationManager 事件桥追加）、最终产出（追加进进度流供增量读取）、
transcript 消息链（completion 容器带出，follow_up_agent 的续跑数据源）。
"""
from __future__ import annotations

import asyncio
import json
import re
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from core.log import log

if TYPE_CHECKING:
    from agent.delegation.profile import AgentFacets
    from agent.mind.mind import Mind

# 当前委托深度（主 Agent 为 0，每委托一层 +1）
_delegate_depth: ContextVar[int] = ContextVar("delegate_depth", default=0)
# 当前委托 ID（主 Agent 为空；子代理执行期间绑定，供进度事件归属）
_delegate_id: ContextVar[str] = ContextVar("delegate_id", default="")

_ROLE_LEAF = "leaf"
_ROLE_ORCHESTRATOR = "orchestrator"
_VALID_ROLES = frozenset({_ROLE_LEAF, _ROLE_ORCHESTRATOR})

_DEFAULT_MAX_ITERATIONS = 15


@dataclass
class SubAgentResult:
    """子代理执行结果。"""

    goal: str
    success: bool
    output: str = ""
    error: str = ""
    role: str = _ROLE_LEAF
    task_index: int = 0
    cancelled: bool = False
    # 结束原因：completed（正常收束）/ budget_exhausted（轮次预算用尽，
    # 产出可能只是中途状态，父级可决策拆小重委托）/ interrupted / no_output
    completed_reason: str = "completed"
    # 执行用量（LLM 事件归集）：turns / input_tokens / output_tokens / duration_ms
    usage: Dict[str, int] = field(default_factory=dict)
    # reflect 结束时的完整消息链（transcript 持久化用；取消/超时路径无值）
    messages: Optional[List[Dict]] = None
    # 输出契约校验：output_schema 存在时为 True/False（是否解析出合法 JSON），否则 None
    schema_ok: Optional[bool] = None

    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "role": self.role,
            "task_index": self.task_index,
            "cancelled": self.cancelled,
            "completed_reason": self.completed_reason,
            "usage": dict(self.usage),
            "schema_ok": self.schema_ok,
        }


def current_depth() -> int:
    """当前委托深度（主 Agent 为 0）。"""
    return _delegate_depth.get()


def current_delegation_id() -> str:
    """当前委托 ID（主 Agent 为空字符串）。"""
    return _delegate_id.get()


def bind_delegation_id(delegation_id: str) -> Token:
    """绑定当前委托 ID（DelegationManager 在启动子代理前调用）。"""
    return _delegate_id.set(delegation_id)


def reset_delegation_id(token: Token) -> None:
    """恢复上一个委托 ID 绑定。"""
    _delegate_id.reset(token)


def normalize_role(role: Optional[str]) -> str:
    """规范化角色名（未知角色降级为 leaf）。"""
    return role if role in _VALID_ROLES else _ROLE_LEAF


def max_spawn_depth() -> int:
    """最大委托深度（配置 delegation_max_depth，默认 2）。"""
    from core.config import get_config_int
    return max(1, get_config_int("delegation_max_depth", 2))


def default_max_iterations() -> int:
    """子代理默认迭代预算。"""
    from core.config import get_config_int
    return get_config_int("delegation_default_iterations", _DEFAULT_MAX_ITERATIONS)


def clamp_iterations(value: int) -> int:
    """钳制迭代预算到 [1, 硬上限]（<=0 时按默认预算再钳制）。"""
    from core.config import get_config_int
    cap = max(1, get_config_int("delegation_max_iterations_cap", 50))
    budget = value if value > 0 else default_max_iterations()
    return max(1, min(budget, cap))


def delegation_timeout_seconds() -> float:
    """单个子代理整体执行超时（配置 delegation_timeout_seconds，默认 600s）。"""
    from core.config import get_config_float
    return max(1.0, get_config_float("delegation_timeout_seconds", 600.0))


_SUB_AGENT_PROMPT = """你是一个子代理，负责完成主代理委托的子任务。

[子任务目标]
{goal}

[背景上下文]
{context}
{history_block}{instructions_block}{schema_block}[执行要求]
1. 专注于完成上述子任务，不要偏离目标
2. 你可以使用工具完成查询、计算、分析等操作
3. 完成后，{summary_noun}总结结果（主代理只能看到你的最终总结）{schema_rule}
4. 总结必须自包含：主代理看不到你的中间过程，关键数据和结论都要写进总结
{role_hint}
"""

_INSTRUCTIONS_HEADER = "[专职守则]（本委托的执行契约，优先级高于以上通用要求）\n"
_SCHEMA_PROMPT = (
    "\n[输出契约]\n最终总结必须是一段合法 JSON（不要包裹代码块），结构如下，"
    "字段名严格一致：\n{schema}\n只输出该 JSON，不要附加其他解释文字。\n"
)


def extract_json_output(output: str) -> Optional[dict]:
    """从子代理产出中提取 JSON 对象（输出契约校验）。

    依次尝试：整体解析 → 剥离代码围栏 → 首个平衡的大括号块。宽容提取、
    事实报告——是否可接受由父级 AI 决策（系统不替 AI 拒绝产出）。
    """
    text = (output or "").strip()
    if not text:
        return None
    for candidate in _json_candidates(text):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _json_candidates(text: str) -> List[str]:
    """产出 JSON 候选：原文 → 剥围栏 → 首个平衡大括号块。"""
    candidates = [text]
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", text, flags=re.DOTALL)
    if fence:
        candidates.append(fence.group(1))
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start:i + 1])
                    break
    return candidates


class SubAgent:
    """子代理：在隔离上下文中执行单个子任务。"""

    def __init__(
            self,
            mind: "Mind",
            goal: str,
            context: str = "",
            *,
            role: str = _ROLE_LEAF,
            max_iterations: int = 0,
            task_index: int = 0,
            model_id: str = "",
            agent_name: str = "",
            delegation_id: str = "",
            parent_history: str = "",
            facets: Optional["AgentFacets"] = None,
            base_messages: Optional[List[Dict]] = None,
            parent_delegation_id: str = "",
    ) -> None:
        self._mind = mind
        self.goal = goal
        self.context = context
        self.role = normalize_role(role)
        self.max_iterations = clamp_iterations(max_iterations)
        self.task_index = task_index
        # 命名档案/难度分级解析后的模型 ID；空串 = 使用默认模型
        self.model_id = model_id
        # 命名子代理档案名（日志/事件归因，空 = 未指定）
        self.agent_name = agent_name
        # 所属委托 ID（steer 转向寻址；空 = 不可转向，如测试直构）
        self.delegation_id = delegation_id
        # 主对话近期记录（fork_context=True 时由 DelegationManager 注入；
        # 仅作背景参考的只读快照，子代理对主对话无写路径）
        self.parent_history = parent_history
        # 档案执行面（instructions/tool_tags/blocked_tools/output_schema）
        self.facets = facets
        # 续跑模式：上次 transcript 的消息链作 base_messages（跳过模板构建）
        self.base_messages = base_messages
        # 续跑血缘（上次委托 ID；空 = 全新委托）
        self.parent_delegation_id = parent_delegation_id

    # ------------------------------------------------------------------
    # prompt 构建
    # ------------------------------------------------------------------

    def build_prompt(self) -> str:
        """全新委托的任务指令（模板 + 档案执行面注入）。"""
        instructions_block = (
            f"\n{_INSTRUCTIONS_HEADER}{self.facets.instructions}\n"
            if self.facets and self.facets.instructions else ""
        )
        schema_block = (
            _SCHEMA_PROMPT.format(
                schema=json.dumps(self.facets.output_schema, ensure_ascii=False, indent=2),
            )
            if self.facets and self.facets.output_schema else ""
        )
        role_hint = (
            "5. 你是 orchestrator 角色：如子任务过于复杂，可调用 delegate_task 进一步拆分委托。"
            if self.role == _ROLE_ORCHESTRATOR
            else "5. 你是 leaf 角色：不可再委托，必须自己完成全部工作。"
        )
        history_block = (
            f"\n[主对话近期记录]（仅供参考，按时间顺序）\n{self.parent_history}\n"
            if self.parent_history else ""
        )
        has_schema = bool(self.facets and self.facets.output_schema)
        return _SUB_AGENT_PROMPT.format(
            goal=self.goal,
            context=self.context or "（无额外背景）",
            history_block=history_block,
            instructions_block=instructions_block,
            schema_block=schema_block,
            # 有输出契约时"总结"即那段 JSON——避免与 [输出契约] 的"只输出 JSON"互相矛盾
            summary_noun="用一段合法 JSON 格式" if has_schema else "用一段清晰的文字",
            schema_rule="，严格遵守上方 [输出契约] 的结构与字段" if has_schema else "",
            role_hint=role_hint,
        )

    # ------------------------------------------------------------------
    # 执行
    # ------------------------------------------------------------------

    async def run(self) -> SubAgentResult:
        """执行子任务并返回结果摘要。"""
        depth = current_depth()
        agent_tag = f", agent={self.agent_name}" if self.agent_name else ""
        continuation_tag = f", 续跑自={self.parent_delegation_id}" if self.parent_delegation_id else ""
        log(
            f"子代理启动 (depth={depth}, role={self.role}{agent_tag}{continuation_tag}, "
            f"预算={self.max_iterations}轮): {self.goal[:80]}",
            tag="委托",
        )

        # leaf 角色禁止再委托（orchestrator 保留 delegate_task，深度由工具自身硬限制）；
        # 档案执行面的 blocked_tools 叠加在角色屏蔽之上
        extra_blocked = {"delegate_task"} if self.role == _ROLE_LEAF else set()
        if self.facets and self.facets.blocked_tools:
            extra_blocked.update(self.facets.blocked_tools)
        extra_blocked = extra_blocked or None

        token = _delegate_depth.set(depth + 1)
        timeout = delegation_timeout_seconds()
        # 性价比模型：经已验证的 _model_id 覆盖管道注入（与 TaskExecutor 同路径）
        options = {"_model_id": self.model_id} if self.model_id else None
        tool_tags = list(self.facets.tool_tags) if self.facets and self.facets.tool_tags else None

        # steer 转向桥：把"按档位取走本委托消息"的闭包绑进当前任务上下文，
        # think_loop 轮顶 drain（steer 档）/ 收束边界 drain（after 档）；
        # 无 delegation_id 不可转向
        from agent.delegation.steer import bind_steer_drain, steer_inbox

        def _drain_noop(_mode: str) -> List[str]:
            return []

        def _drain(mode: str, delegation_id: str = self.delegation_id) -> List[str]:
            return steer_inbox.drain(delegation_id, mode)

        drain = _drain if self.delegation_id else _drain_noop
        completion: Dict[str, Any] = {}
        messages = self.base_messages
        if messages is None:
            messages = [{"role": "user", "content": self.build_prompt()}]
        try:
            with bind_steer_drain(drain):
                output = await asyncio.wait_for(
                    self._mind.reflect(
                        messages,
                        max_iterations=self.max_iterations,
                        allow_output_tools=False,
                        extra_blocked_tools=extra_blocked,
                        tool_tags=tool_tags,
                        options=options,
                        completion=completion,
                    ),
                    timeout=timeout,
                )
        except asyncio.TimeoutError:
            log(
                f"子代理整体超时（>{timeout:.0f}s）: {self.goal[:60]}",
                "WARNING", tag="委托",
            )
            return SubAgentResult(
                goal=self.goal, success=False,
                error=f"子代理执行超时（>{timeout:.0f}s），已中断",
                role=self.role, task_index=self.task_index,
            )
        except Exception as exc:
            log(f"子代理失败: {self.goal[:60]}: {type(exc).__name__}: {exc}", "WARNING", tag="委托")
            return SubAgentResult(
                goal=self.goal, success=False,
                error=f"{type(exc).__name__}: {exc}",
                role=self.role, task_index=self.task_index,
            )
        finally:
            # 委托执行结束（无论成败/超时）：清箱防残留指令误入后续同名委托
            if self.delegation_id:
                steer_inbox.clear(self.delegation_id)
            _delegate_depth.reset(token)
        output = (output or "").strip()
        reason = completion.get("reason", "completed")
        # reflect 的最终消息链（think_loop 收口写入 completion 容器；
        # 中断/异常路径容器无消息 → transcript 不可续跑）
        final_messages = completion.get("messages")
        schema_ok: Optional[bool] = None
        if self.facets and self.facets.output_schema:
            schema_ok = extract_json_output(output) is not None
        if not output:
            return SubAgentResult(
                goal=self.goal, success=False,
                error="子代理未产出任何结果",
                role=self.role, task_index=self.task_index,
                completed_reason="no_output" if reason == "completed" else reason,
                messages=final_messages, schema_ok=schema_ok,
            )
        log(
            f"子代理完成: {self.goal[:60]} -> {len(output)} 字 "
            f"(原因={reason}, schema={'✓' if schema_ok else '✗' if schema_ok is False else '-'})",
            tag="委托",
        )
        return SubAgentResult(
            goal=self.goal, success=True, output=output,
            role=self.role, task_index=self.task_index,
            completed_reason=reason,
            messages=final_messages, schema_ok=schema_ok,
        )
