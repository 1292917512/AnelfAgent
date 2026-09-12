"""委托管理器 — 子代理的并发调度、预算控制与结果聚合。

- 并发上限：顶层与嵌套委托各一把 asyncio.Semaphore（默认各 3，可配置），
  嵌套委托不竞争顶层槽位以避免持槽等待死锁；获取槽位带超时
- 并行模式：tasks 数组 fan-out，asyncio.gather 并发执行
- 预算控制：每个子代理独立的迭代预算（默认 15 轮）
- 结果聚合：按 task_index 排序，摘要按父上下文剩余空间动态截断，
  每项附执行用量（turns/tokens/耗时）与输出契约校验结果
- 后台模式：登记 BackgroundTaskRegistry 后立即返回 delegation_id，
  结果按注册表路由（轮内会合注入 / 完成即新 turn 通知）
- 续跑：follow_up 以 transcript 消息链为 base_messages 无损续跑
  （follow_up_agent 工具）；send_to_agent 双档投递（steer 步骤边界 /
  after 收束边界追加）
- 运行日志（journal）：进度流接入注册表增量读取；ledger 崩溃账本供
  启动恢复；用量经 LLM 事件按 delegation_id 归集
- 事件发射：``EVENT_DELEGATION_STARTED`` / ``EVENT_DELEGATION_PROGRESS`` /
  ``EVENT_DELEGATION_RESOLVED`` —— 前端据此渲染 DelegationCard 实时进度。
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from agent.delegation import journal
from agent.delegation.profile import AgentFacets
from agent.delegation.sub_agent import (
    SubAgent,
    SubAgentResult,
    bind_delegation_id,
    current_delegation_id,
    current_depth,
    normalize_role,
    reset_delegation_id,
)
from agent.mind.scope_usage import bind_usage_scope, reset_usage_scope
from core.event_bus import (
    EVENT_DELEGATION_PROGRESS,
    EVENT_DELEGATION_RESOLVED,
    EVENT_DELEGATION_STARTED,
    event_bus,
)
from core.log import bind_log_actor, log, reset_log_actor

if TYPE_CHECKING:
    from agent.mind.mind import Mind

EVENT_DELEGATION_COMPLETED = "delegation.completed"

from agent.planning.tracker import (  # noqa: E402
    parse_scope_chat_id as _parse_scope_chat_id,
)

# 结果摘要预算（参考 hermes：父上下文剩余空间的 50% 均分给各子任务）
_SUMMARY_HEADROOM_FRACTION = 0.5
_MIN_SUMMARY_CHARS = 2_000
_MAX_SUMMARY_CHARS = 24_000
_CHARS_PER_TOKEN = 4
# 完成通知 / 事件中的摘要截断长度
_SUMMARY_NOTICE_MAX_CHARS = 1_500
_RESOLVED_OUTPUT_PREVIEW_CHARS = 2_000
# 摘要截断保留比例（头部 75% + 尾部 25%）
_TRIM_HEAD_FRACTION = 0.75
# 续跑指令注入的最大消息链长度（条数；防异常巨型 transcript 撑爆上下文）
_FOLLOWUP_MAX_MESSAGES = 400

# 用户取消的取消消息（写入工具结果，引导 AI 不要自动重试）
CANCELLED_MESSAGE = (
    "该子代理任务已被用户手动取消（cancelled_by_user）。"
    "这是用户的主动决定，请勿自动重试该任务；如需继续，等待用户进一步指示。"
)


def _cancelled_result(
        goal: str,
        *,
        role: str = "leaf",
        task_index: int = 0,
) -> SubAgentResult:
    """构造用户取消的子代理结果。"""
    return SubAgentResult(
        goal=goal, success=False, error=CANCELLED_MESSAGE,
        role=normalize_role(role), task_index=task_index, cancelled=True,
    )


def _actor_label(agent_name: str, role: str, delegation_id: str) -> str:
    """子代理日志 actor 标签：日志行据此区分主 AI（无前缀）与子代理执行。"""
    return f"子代理@{agent_name or normalize_role(role)}#{delegation_id[-6:]}"


def _max_concurrent() -> int:
    from core.config import get_config_int
    return max(1, get_config_int("delegation_max_concurrent", 3))


def _acquire_timeout_seconds() -> float:
    from core.config import get_config_float
    return max(1.0, get_config_float("delegation_acquire_timeout_seconds", 300.0))


def _owner_scope(scope_hint: str) -> str:
    """委托归属会话解析（与后台任务完成路由同链）。

    显式 scope_hint 优先（后台路径由工具传入 current_owner_scope()）；
    否则按 usage_scope 绑定 > 激活上下文解析——嵌套委托归属父会话而非
    reflect 一次性 scope，用量归属与通知路由才能对齐。
    """
    if scope_hint:
        return scope_hint
    from agent.mind.tool_activation import current_owner_scope
    return current_owner_scope()


def _usage_bucket() -> Dict[str, int]:
    return {"turns": 0, "input_tokens": 0, "output_tokens": 0, "duration_ms": 0}


def _adapter_key_of(mind: Any, scope: str) -> str:
    """scope 的回复路由 adapter（ledger 记录；pfc 未就绪时容错空串）。"""
    try:
        return str(mind.pfc.get_adapter_key(scope) or "")
    except Exception:
        return ""


class DelegationManager:
    """子代理委托管理器。"""

    def __init__(self, mind: "Mind") -> None:
        self._mind = mind
        self._semaphore = asyncio.Semaphore(_max_concurrent())
        # 嵌套委托（orchestrator 子代理内再委托）使用独立信号量，
        # 避免 orchestrator 持有顶层槽位等待同一把信号量造成死锁
        self._nested_semaphore = asyncio.Semaphore(_max_concurrent())
        self._background_tasks: Dict[str, asyncio.Task] = {}
        # 运行中委托的实时信息（进度事件归属 / 取消 / 运行快照）
        self._running: Dict[str, Dict[str, Any]] = {}
        # 已请求取消但尚未进入执行阶段的委托 ID（并发槽等待中取消的场景）
        self._cancel_marks: set[str] = set()
        # 并发槽等待中的委托（此阶段可被 cancel 标记，获取槽位后先生效标记）
        self._pending: set[str] = set()
        # 父子关系（父 delegation_id → 后代 id 集合）：取消级联用
        self._children: Dict[str, set[str]] = {}
        # 按委托归集的 LLM 用量（turns/tokens/耗时；完成时随结果带出后清理）
        self._usage: Dict[str, Dict[str, int]] = {}
        self._install_progress_hook()

    # ------------------------------------------------------------------
    # 进度事件（子代理运行期 → 前端 DelegationCard 实时进度 + 进度流/用量归集）
    # ------------------------------------------------------------------

    def _install_progress_hook(self) -> None:
        """订阅思维循环的轮次/工具/LLM 事件，转译为 delegation_progress
        并归集运行产物。

        event_bus 处理器在发射方上下文中内联执行，因此经 ContextVar 读取
        当前委托 ID 即可把子代理的内部活动归属到对应委托卡片、写入对应
        委托的进度流与用量桶。
        """
        from core.event_bus import (
            EVENT_THINKING_LLM_END,
            EVENT_THINKING_REPLY_ROUND,
            EVENT_THINKING_TOOL_END,
            EVENT_THINKING_TOOL_START,
        )

        async def _on_round(payload: Dict[str, Any]) -> None:
            iteration = int(payload.get("iteration", 0))
            await self._emit_progress("round", iteration=iteration)
            self._journal_progress(f"第 {iteration + 1} 轮开始")

        async def _on_tool_start(payload: Dict[str, Any]) -> None:
            tool = str(payload.get("tool_name", ""))
            await self._emit_progress("tool_start", tool=tool)
            self._journal_progress(f"工具 {tool} …")

        async def _on_tool_end(payload: Dict[str, Any]) -> None:
            tool = str(payload.get("tool_name", ""))
            success = bool(payload.get("success"))
            await self._emit_progress("tool_end", tool=tool, success=success)
            self._journal_progress(f"工具 {tool} {'完成' if success else '失败'}")

        async def _on_llm_end(payload: Dict[str, Any]) -> None:
            self._record_usage(payload)

        event_bus.on(EVENT_THINKING_REPLY_ROUND, _on_round, owner="delegation")
        event_bus.on(EVENT_THINKING_TOOL_START, _on_tool_start, owner="delegation")
        event_bus.on(EVENT_THINKING_TOOL_END, _on_tool_end, owner="delegation")
        event_bus.on(EVENT_THINKING_LLM_END, _on_llm_end, owner="delegation")

    def _journal_progress(self, line: str) -> None:
        """进度流写入（仅运行中的委托；fail-open）。"""
        delegation_id = current_delegation_id()
        if not delegation_id or delegation_id not in self._running:
            return
        journal.append_progress(delegation_id, line)

    def _record_usage(self, payload: Dict[str, Any]) -> None:
        """LLM 用量按当前委托归集（事件在发射方上下文执行，归属准确）。"""
        delegation_id = current_delegation_id()
        if not delegation_id:
            return
        bucket = self._usage.get(delegation_id)
        if bucket is None:
            return
        usage = payload.get("usage") or {}
        bucket["turns"] += 1
        bucket["input_tokens"] += int(usage.get("prompt_tokens") or 0)
        bucket["output_tokens"] += int(usage.get("completion_tokens") or 0)
        bucket["duration_ms"] += int(payload.get("duration_ms") or 0)

    async def _emit_progress(self, kind: str, **fields: Any) -> None:
        """子代理内部活动 → delegation_progress 事件（仅运行中的委托）。

        顺带把最新进度（当前轮次/正在执行的工具）写入运行条目，
        全局运行快照（running_snapshot_all）无需读日志即可展示实时进度。
        """
        delegation_id = current_delegation_id()
        if not delegation_id:
            return
        info = self._running.get(delegation_id)
        if info is None:
            return
        if kind == "round":
            # 事件 iteration 从 0 起，快照存展示轮次（从 1 起，对齐前端口径）
            info["iteration"] = int(fields.get("iteration", 0)) + 1
        elif kind == "tool_start":
            info["current_tool"] = str(fields.get("tool", ""))
        elif kind == "tool_end":
            info["current_tool"] = ""
        try:
            await event_bus.emit(EVENT_DELEGATION_PROGRESS, {
                "scope": info["scope"],
                "chat_id": info["chat_id"],
                "delegation_id": delegation_id,
                "kind": kind,
                "ts": time.time(),
                **fields,
            })
        except Exception:
            log("delegation_progress 发射异常已忽略", "DEBUG")

    # ------------------------------------------------------------------
    # 取消与运行快照（webui 停止按钮 / DelegationCard 取消）
    # ------------------------------------------------------------------

    def cancel(self, delegation_id: str) -> bool:
        """取消运行中的委托（用户主动触发），级联取消全部后代。返回是否找到该委托。"""
        found = False
        # 级联：后代委托（嵌套子代理）一并标记取消，
        # 否则父委托取消后嵌套任务空跑、resolved 事件永不发射
        stack = [delegation_id]
        descendants: List[str] = []
        while stack:
            did = stack.pop()
            for child in self._children.get(did, ()):
                descendants.append(child)
                stack.append(child)
        for did in [delegation_id, *descendants]:
            info = self._running.get(did)
            task = self._background_tasks.get(did)
            if info is None and task is None and did not in self._pending:
                continue
            found = True
            self._cancel_marks.add(did)
            if info is not None:
                run_task = info.get("task")
                if isinstance(run_task, asyncio.Task) and not run_task.done():
                    run_task.cancel()
            if task is not None and not task.done():
                task.cancel()
        if found:
            log(f"委托取消请求: {delegation_id}（级联 {len(descendants)} 个后代）", tag="委托")
        return found

    def cancel_scope(self, scope: str) -> int:
        """取消指定会话 scope 下所有运行中的委托，返回取消数量。"""
        targets = [
            did for did, info in self._running.items()
            if info.get("scope") == scope
        ]
        for did in targets:
            self.cancel(did)
        return len(targets)

    def _snapshot_item(self, did: str, info: Dict[str, Any], now: float) -> Dict[str, Any]:
        """运行快照条目构造（scope 过滤快照与全局快照共用）。"""
        return {
            "delegation_id": did,
            "goal": str(info.get("goal", "")),
            "role": str(info.get("role", "leaf")),
            "task_index": int(info.get("task_index", 0)),
            "background": bool(info.get("background")),
            "model": str(info.get("model", "")),
            "agent": str(info.get("agent", "")),
            "elapsed_seconds": int(now - float(info.get("started_at", now))),
            "usage": dict(self._usage.get(did) or {}),
        }

    def running_snapshot(self, scope: str) -> List[Dict[str, Any]]:
        """指定 scope 下运行中的委托快照（前端刷新后恢复卡片用）。"""
        now = time.time()
        return [
            self._snapshot_item(did, info, now)
            for did, info in self._running.items()
            if info.get("scope") == scope
        ]

    def running_snapshot_all(self) -> List[Dict[str, Any]]:
        """全 scope 运行中委托快照（Dashboard 全局总览面板用）。

        在 scope 快照字段之上附带归属维度（scope/chat_id/started_at）与
        实时进度（iteration 展示轮次 / current_tool 正在执行的工具）。
        """
        now = time.time()
        items: List[Dict[str, Any]] = []
        for did, info in self._running.items():
            item = self._snapshot_item(did, info, now)
            item.update({
                "scope": str(info.get("scope", "")),
                "chat_id": str(info.get("chat_id", "")),
                "started_at": float(info.get("started_at", now)),
                "iteration": int(info.get("iteration", 0)),
                "current_tool": str(info.get("current_tool", "")),
            })
            items.append(item)
        items.sort(key=lambda item: float(item["started_at"]))
        return items

    def is_running(self, delegation_id: str) -> bool:
        """委托是否仍在运行中（含并发槽等待阶段）。"""
        return delegation_id in self._running or delegation_id in self._pending

    def steer(self, delegation_id: str, message: str, mode: str = "steer") -> Dict[str, Any]:
        """向运行中的委托发送转向指令，返回结构化结果（含错误）。

        双档投递：steer = 步骤边界注入（改变进行中的工作）；after =
        收束边界注入（本要结束时追加续跑，不取消、已完成部分保留）。
        前台/后台委托统一支持：只要还在 _running 即可转向；消息经
        SteerInbox 按档位暂存。收件箱满（单委托 8 条，两档合并计）返回
        结构化错误——转向是纠偏不是聊天通道。
        """
        from agent.delegation.steer import MODE_STEER, steer_inbox
        if delegation_id not in self._running:
            hint = (
                "可先调用 check_background_tasks 查看运行中的任务"
                if not journal.load_transcript(delegation_id)
                else "该委托已结束，可用 follow_up_agent 无损续跑"
            )
            return {
                "error": f"委托不存在或已结束: {delegation_id}",
                "hint": hint,
            }
        if not (message or "").strip():
            return {"error": "message 不能为空"}
        if mode not in ("steer", "after"):
            return {"error": f"deliver_as 须为 steer 或 after（收到 {mode!r}）"}
        if not steer_inbox.push(delegation_id, message, mode):
            return {
                "error": f"委托 {delegation_id} 的转向消息已达上限，请等待其消化后再发",
                "queued": steer_inbox.pending_count(delegation_id),
            }
        goal = str(self._running[delegation_id].get("goal", ""))[:80]
        log(
            f"转向指令入箱 ({mode}): {delegation_id} ({goal}) msg={message[:60]}",
            tag="委托",
        )
        note = (
            "指令将在子代理当前步骤完成后注入（不取消、已完成部分保留）"
            if mode == MODE_STEER
            else "指令将在子代理本要结束时注入续跑（不取消、已完成部分保留）"
        )
        return {
            "ok": True,
            "delegation_id": delegation_id,
            "queued": steer_inbox.pending_count(delegation_id),
            "note": note,
        }

    async def follow_up(
            self,
            delegation_id: str,
            message: str,
            *,
            background: bool = False,
            max_iterations: int = 0,
    ) -> Dict[str, Any]:
        """续跑已结束的委托：以 transcript 消息链为 base_messages 无损续聊。

        语义对齐 dsh continuable subagents：上次执行的完整上下文（工具
        调用/中间结论）原样在场，追加指令后继续——替代"把有损总结当
        context 重新委托"。运行中的委托不可续跑（用 send_to_agent 转向）；
        transcript 缺失/超限/被清理时返回结构化错误。
        """
        if delegation_id in self._running or delegation_id in self._pending:
            return {
                "error": f"委托 {delegation_id} 仍在运行，请用 send_to_agent 转向而非续跑",
            }
        transcript = journal.load_transcript(delegation_id)
        if transcript is None:
            return {
                "error": f"委托 {delegation_id} 无可续跑 transcript（不存在/超限/已过期清理）",
                "hint": "请用 delegate_task 重新委托，并在 context 中带上此前的关键结论",
            }
        messages = list(transcript.get("messages"))[-_FOLLOWUP_MAX_MESSAGES:]
        goal = str(transcript.get("goal", "")) or delegation_id
        agent_name = str(transcript.get("agent", "") or "")
        facets = AgentFacets.from_dict(transcript.get("facets"))
        follow_message = {
            "role": "user",
            "content": (
                "[续跑指令] 该委托上次已结束（原因："
                f"{transcript.get('completed_reason', 'completed')}），"
                "以上是你的完整执行上下文，请在既有进展基础上继续：\n"
                f"{(message or '').strip()}"
            ),
            "_source": {"origin": "steer"},
        }
        base_messages = messages + [follow_message]
        budget = max_iterations or int(transcript.get("max_iterations") or 0)
        common = dict(
            role=str(transcript.get("role", "leaf")),
            max_iterations=budget,
            agent_name=agent_name,
            facets=facets if facets else None,
            base_messages=base_messages,
            parent_delegation_id=delegation_id,
        )
        if background:
            new_id = self.delegate_background(goal, "", **common)
            return {
                "ok": True, "mode": "background",
                "parent_delegation_id": delegation_id,
                "delegation_id": new_id,
                "message": "续跑已在后台执行，完成后系统会自动通知你"
                           "（可用 check_background_tasks 查询进度）。",
            }
        result = await self.delegate(goal, "", **common)
        payload: Dict[str, Any] = json.loads(self.aggregate_results([result]))
        payload["parent_delegation_id"] = delegation_id
        return payload

    def _resolve_model(self, agent_name: str, difficulty: Any) -> str:
        """子代理模型解析：命名档案 > 内置难度档 > 默认模型。

        统一注册表语义：agent_name 直指档案（含内置 easy/medium/hard）；
        difficulty 是内置难度档的语法糖。档案未命中/池不可用时降级到难度档
        （工具层已前置拦截未知名称，此处降级是直连调用方的防御路径）；
        未标注/非法/未配置时返回 ""（用默认模型）。
        """
        mgr = getattr(self._mind, "llm_manager", None)
        if mgr is None:
            return ""
        try:
            if agent_name:
                resolved = mgr.resolve_sub_agent_model(agent_name)
                if resolved:
                    return resolved
            if difficulty:
                return mgr.resolve_delegation_model(difficulty) or ""
        except Exception:
            log("子代理模型解析失败，使用默认模型", "DEBUG", tag="委托")
        return ""

    def _resolve_facets(self, agent_name: str) -> Optional[AgentFacets]:
        """命名档案的执行面（instructions/tool_tags/blocked/output_schema）。

        未指定档案 / 档案无执行面（含内置难度档）返回 None——子代理走
        默认模板与默认工具选择器。难度档刻意不携带执行面：难度语义只是
        换模型，行为契约归自定义档案。
        """
        if not agent_name:
            return None
        mgr = getattr(self._mind, "llm_manager", None)
        if mgr is None:
            return None
        try:
            profile = mgr.get_sub_agent_profile(agent_name)
        except Exception:
            return None
        if profile is None or not profile.facets:
            return None
        return profile.facets

    # ------------------------------------------------------------------
    # 同步委托
    # ------------------------------------------------------------------

    async def delegate(
            self,
            goal: str,
            context: str = "",
            *,
            role: str = "leaf",
            max_iterations: int = 0,
            task_index: int = 0,
            scope_hint: str = "",
            difficulty: int = 0,
            delegation_id: str = "",
            agent_name: str = "",
            emit_events: bool = True,
            fork_context: bool = False,
            facets: Optional[AgentFacets] = None,
            base_messages: Optional[List[Dict]] = None,
            parent_delegation_id: str = "",
    ) -> SubAgentResult:
        """委托单个子任务（阻塞至完成）。

        子代理在独立 asyncio.Task 中执行并登记到 _running：
        - 进度事件经 ContextVar 归属到本委托（前端实时进度 + 进度流落盘）
        - cancel() 取消该 Task 时转化为"用户取消"结果返回给调用方，
          而不是让 CancelledError 击穿父级思维循环
        delegation_id：外部预登记的 id（后台委托路径透传，全程单一 id）；
        agent_name：命名子代理档案（模型与执行面解析优先于 difficulty）；
        emit_events=False 时 started/resolved 事件由调用方负责（防重复发射）；
        facets/base_messages/parent_delegation_id 为续跑三元组（follow_up
        内部路径）：显式执行面覆盖档案解析，消息链跳过模板构建。
        """
        # 顶层委托（depth 0）与嵌套委托（depth>=1）分离并发槽，
        # 嵌套方持槽等待时不再竞争同一把信号量
        semaphore = self._semaphore if current_depth() < 1 else self._nested_semaphore
        timeout = _acquire_timeout_seconds()
        registry = getattr(self._mind, "background_tasks", None)
        owns_registry_entry = False
        # 归属会话与完成路由同链解析（usage_scope 绑定 > 激活上下文）：
        # 嵌套/前台委托都归属真实会话，check_background_tasks 可见
        scope = _owner_scope(scope_hint)
        if not delegation_id:
            if registry is not None:
                # 前台/嵌套委托同样登记注册表：check_background_tasks 可见
                # （含耗时）、terminate_background_task 可单独停止（无需中断
                # 整个回复）。完成时以 claimed=True 收尾——前台的通知语义
                # 就是工具结果本身，不走轮外完成回调
                delegation_id = registry.register(scope, "delegation", goal[:80])
                owns_registry_entry = True
            else:
                delegation_id = uuid.uuid4().hex[:8]
        _user_scope, chat_id = _parse_scope_chat_id(scope)
        model_id = self._resolve_model(agent_name, difficulty)
        if facets is None:
            facets = self._resolve_facets(agent_name)
        # 父子登记：嵌套委托归属当前委托，取消时级联
        parent_id = current_delegation_id()
        if parent_id:
            self._children.setdefault(parent_id, set()).add(delegation_id)

        # 发射 started 事件（前端 DelegationCard 渲染）
        if emit_events:
            try:
                await event_bus.emit(EVENT_DELEGATION_STARTED, {
                    "scope": scope,
                    "chat_id": chat_id,
                    "delegation_id": delegation_id,
                    "goal": goal,
                    "context_preview": context[:200],
                    "role": normalize_role(role),
                    "task_index": task_index,
                    "background": bool(scope_hint),
                    "depth": current_depth(),
                    "model": model_id,
                    "agent": agent_name,
                    "ts": asyncio.get_running_loop().time(),
                })
            except Exception:
                log("delegate 异常已忽略", "DEBUG")

        # 等槽期间登记 pending：此阶段 cancel 仅打标记，获取槽位后先生效
        self._pending.add(delegation_id)
        try:
            try:
                await asyncio.wait_for(semaphore.acquire(), timeout)
            except asyncio.TimeoutError:
                log(f"委托并发槽获取超时（>{timeout:.0f}s）: {goal[:60]}", "WARNING", tag="委托")
                fail_result = SubAgentResult(
                    goal=goal, success=False,
                    error=f"获取委托并发槽超时（>{timeout:.0f}s），子代理并发已满",
                    role=normalize_role(role), task_index=task_index,
                )
                if emit_events:
                    try:
                        await event_bus.emit(EVENT_DELEGATION_RESOLVED, {
                            "scope": scope, "chat_id": chat_id,
                            "delegation_id": delegation_id, "goal": goal,
                            "success": False, "error": fail_result.error,
                            "task_index": task_index,
                        })
                    except Exception:
                        log("delegate 异常已忽略", "DEBUG")
                self._detach_child(parent_id, delegation_id)
                return fail_result
            except asyncio.CancelledError:
                # 并发槽等待期间被取消（cancel 先于执行登记到达）
                self._detach_child(parent_id, delegation_id)
                if delegation_id in self._cancel_marks:
                    self._cancel_marks.discard(delegation_id)
                    return _cancelled_result(goal, role=role, task_index=task_index)
                raise
        finally:
            self._pending.discard(delegation_id)

        # 等槽期间被标记取消：获取槽位后先生效，不进入执行
        if delegation_id in self._cancel_marks:
            self._cancel_marks.discard(delegation_id)
            semaphore.release()
            if parent_id:
                self._detach_child(parent_id, delegation_id)
            return _cancelled_result(goal, role=role, task_index=task_index)

        id_token = bind_delegation_id(delegation_id)
        # 日志 actor 归因：子代理执行树内的全部日志行（think_loop/LLM/工具）
        # 自动带 [子代理@…#…] 前缀，与主 AI（无前缀）一眼可辨；ContextVar 经
        # create_task 复制进整个执行树，嵌套委托内层绑定覆盖外层（归因到最内层）
        actor_token = bind_log_actor(_actor_label(agent_name, role, delegation_id))
        # 用量归属：子代理 reflect 的一次性 scope 不建独立统计行，
        # 其 LLM 用量经此绑定归属父会话（/status/usage 可见委托成本）；
        # 委托维度的用量桶同步开账（事件归集，随结果带出）
        usage_token = bind_usage_scope(scope) if scope else None
        self._usage[delegation_id] = _usage_bucket()
        try:
            parent_history = (
                await self._load_parent_history(scope) if fork_context else ""
            )
            agent = SubAgent(
                self._mind, goal, context,
                role=role, max_iterations=max_iterations, task_index=task_index,
                model_id=model_id, agent_name=agent_name,
                delegation_id=delegation_id,
                parent_history=parent_history,
                facets=facets,
                base_messages=base_messages,
                parent_delegation_id=parent_delegation_id,
            )
            run_task = asyncio.create_task(
                agent.run(), name=f"delegation.run.{delegation_id}",
            )
            if owns_registry_entry and registry is not None:
                # 终止句柄：标记先行 + 桥回主循环走 cancel()（转"用户取消"结果）
                _loop = asyncio.get_running_loop()

                def _kill_front() -> bool:
                    self._cancel_marks.add(delegation_id)
                    try:
                        _loop.call_soon_threadsafe(self.cancel, delegation_id)
                        return True
                    except RuntimeError:
                        return False  # 循环已关闭（关停中）

                registry.attach_killer(delegation_id, _kill_front)
            # 进入执行：进度流接线（增量读取）+ 崩溃账本开账
            journal.append_progress(
                delegation_id,
                f"委托启动: {goal[:120]}"
                + (f"（续跑自 {parent_delegation_id}）" if parent_delegation_id else ""),
            )
            if registry is not None:
                registry.attach_output_file(
                    delegation_id, str(journal.progress_path(delegation_id)),
                )
            journal.append_ledger(
                journal.LEDGER_STARTED, delegation_id,
                goal=goal[:200], scope=scope, agent=agent_name, model=model_id,
                adapter_key=_adapter_key_of(self._mind, scope),
            )
            self._running[delegation_id] = {
                "task": run_task,
                "goal": goal,
                "scope": scope,
                "chat_id": chat_id,
                "role": normalize_role(role),
                "task_index": task_index,
                "background": bool(scope_hint),
                "model": model_id,
                "agent": agent_name,
                "started_at": time.time(),
            }
            result: Optional[SubAgentResult] = None
            try:
                result = await run_task
            except asyncio.CancelledError:
                # 用户取消：转化为取消结果返回（父级思维循环不受 CancelledError 冲击）；
                # 非用户取消（如服务关闭）继续向上传播
                if delegation_id in self._cancel_marks:
                    log(f"委托已被用户取消: {delegation_id} -> {goal[:60]}", tag="委托")
                    result = _cancelled_result(goal, role=role, task_index=task_index)
                else:
                    raise
            finally:
                self._cancel_marks.discard(delegation_id)
                self._running.pop(delegation_id, None)
                if result is not None:
                    result.usage = dict(self._usage.get(delegation_id) or {})
                    self._persist_transcript(
                        delegation_id, goal, scope, agent_name, model_id,
                        normalize_role(role), max_iterations, facets, result,
                    )
                # 注册表收尾：结果由工具返回值消费（claimed=True），异常路径
                # 也不例外——否则条目滞留 running 永不消失
                if owns_registry_entry and registry is not None:
                    try:
                        registry.complete(
                            delegation_id,
                            bool(result and result.success),
                            ((result.output if result else "")
                             or (result.error if result else "")
                             or "execution aborted")[:1500],
                            claimed=True,
                        )
                    except Exception as exc:
                        log(f"前台委托注册表收尾失败: {delegation_id}: {exc}", "DEBUG", tag="委托")
        finally:
            if usage_token is not None:
                reset_usage_scope(usage_token)
            reset_log_actor(actor_token)
            reset_delegation_id(id_token)
            self._usage.pop(delegation_id, None)
            semaphore.release()
            if parent_id:
                self._detach_child(parent_id, delegation_id)

        # 进度流与账本收尾（终态事实：取消/失败同样闭合）
        if result is not None:
            status = (
                "已取消" if result.cancelled
                else ("成功" if result.success else "失败")
            )
            tail = (result.output if result.success else result.error) or ""
            journal.append_progress(
                delegation_id, f"委托结束（{status}）: {tail[:400]}",
            )
            journal.append_ledger(
                journal.LEDGER_CLOSED, delegation_id, status=status,
            )

        # 发射 resolved 事件
        if emit_events:
            try:
                await event_bus.emit(EVENT_DELEGATION_RESOLVED, {
                    "scope": scope, "chat_id": chat_id,
                    "delegation_id": delegation_id, "goal": goal,
                    "success": result.success,
                    "output": result.output[:_RESOLVED_OUTPUT_PREVIEW_CHARS],
                    "error": result.error,
                    "task_index": task_index,
                    "usage": dict(result.usage),
                    **({"cancelled": True} if result.cancelled else {}),
                })
            except Exception:
                log("delegate 异常已忽略", "DEBUG")
        return result

    def _persist_transcript(
            self,
            delegation_id: str,
            goal: str,
            scope: str,
            agent_name: str,
            model_id: str,
            role: str,
            max_iterations: int,
            facets: Optional[AgentFacets],
            result: SubAgentResult,
    ) -> None:
        """transcript 落盘（follow_up 续跑数据源；消息链缺失则跳过）。"""
        if not result.messages:
            return
        try:
            from core.config import get_config_bool
            if not get_config_bool("delegation_transcript_enabled", True):
                return
        except Exception:
            pass
        journal.save_transcript({
            "delegation_id": delegation_id,
            "goal": goal,
            "scope": scope,
            "agent": agent_name,
            "model_id": model_id,
            "role": role,
            "max_iterations": max_iterations,
            "facets": facets.to_dict() if facets else None,
            "messages": result.messages[-_FOLLOWUP_MAX_MESSAGES:],
            "output": result.output,
            "success": result.success,
            "completed_reason": result.completed_reason,
            "finished_at": time.time(),
        })

    def _detach_child(self, parent_id: str, delegation_id: str) -> None:
        """解除父子登记（子委托结束后清理，空集即删）。"""
        children = self._children.get(parent_id)
        if children is not None:
            children.discard(delegation_id)
            if not children:
                self._children.pop(parent_id, None)

    async def _load_parent_history(self, scope: str) -> str:
        """fork_context 快照：父会话最近消息渲染为紧凑文本（只读参考）。

        上限 20 条 / 4000 字符 / 单条 300 字符；scope 非会话域或读取
        失败返回空串（不阻断委托）。媒体块等非文本内容跳过。
        """
        base, _chat_id = _parse_scope_chat_id(scope)
        if not base.startswith(("user_", "group_")) or "_" not in base:
            return ""
        scope_type, scope_id = base.split("_", 1)
        try:
            from agent.storage.storage_router import StorageDomain
            rows = await self._mind.conversation_data.router.fetch(
                StorageDomain.CONVERSATION,
                scope_type=scope_type, scope_id=scope_id, limit=20,
            )
        except Exception as e:
            log(f"fork_context 读取父会话失败: {e}", "DEBUG", tag="委托")
            return ""
        lines: List[str] = []
        total = 0
        for row in rows[-20:]:
            content = row.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            line = f"{row.get('role', '?')}: {content[:300]}"
            if total + len(line) > 4000:
                break
            lines.append(line)
            total += len(line)
        return "\n".join(lines)

    async def delegate_batch(
            self,
            tasks: List[Dict[str, Any]],
            *,
            role: str = "leaf",
            max_iterations: int = 0,
            difficulty: int = 0,
            agent_name: str = "",
    ) -> List[SubAgentResult]:
        """并行委托多个子任务，结果按 task_index 排序。

        单任务可携带自己的 difficulty / agent（缺省继承顶层参数）。
        """
        if len(tasks) > _max_concurrent() * 3:
            raise ValueError(
                f"并行子任务数量超限（{len(tasks)} > {_max_concurrent() * 3}），请拆分批次"
            )
        results = await asyncio.gather(
            *(
                self.delegate(
                    t.get("goal", ""), t.get("context", ""),
                    role=normalize_role(t.get("role") or role),
                    max_iterations=max_iterations,
                    task_index=i,
                    difficulty=t.get("difficulty", difficulty),
                    agent_name=str(t.get("agent") or agent_name),
                    fork_context=bool(t.get("fork_context", False)),
                )
                for i, t in enumerate(tasks)
            ),
            return_exceptions=True,
        )
        final: List[SubAgentResult] = []
        for i, r in enumerate(results):
            if isinstance(r, BaseException):
                final.append(SubAgentResult(
                    goal=tasks[i].get("goal", ""), success=False,
                    error=f"{type(r).__name__}: {r}", task_index=i,
                ))
            else:
                final.append(r)
        final.sort(key=lambda r: r.task_index)
        return final

    # ------------------------------------------------------------------
    # 后台委托
    # ------------------------------------------------------------------

    def delegate_background(
            self,
            goal: str,
            context: str = "",
            *,
            role: str = "leaf",
            max_iterations: int = 0,
            scope: str = "",
            difficulty: int = 0,
            agent_name: str = "",
            fork_context: bool = False,
            facets: Optional[AgentFacets] = None,
            base_messages: Optional[List[Dict]] = None,
            parent_delegation_id: str = "",
    ) -> str:
        """后台委托：登记注册表后立即返回 delegation_id，结果异步送达。

        送达路径（由 BackgroundTaskRegistry 路由）：
        - 父 Agent 正挂起等待 → 完成事件注入当前思考循环（轮内会合）；
        - 否则 → 完成事件排入回复队列触发新一轮 REPLY（完成即新 turn）。
        facets/base_messages/parent_delegation_id 为续跑三元组（follow_up
        的后台分支透传）。
        """
        registry = getattr(self._mind, "background_tasks", None)
        display_goal = f"[续跑] {goal[:72]}" if parent_delegation_id else goal[:80]
        if registry is not None:
            delegation_id = registry.register(scope or _owner_scope(""), "delegation", display_goal)
            # 终止句柄：AI 经 terminate_background_task 决策取消本委托。
            # cancel 内的 Task.cancel 非线程安全，killer 可能在线程池执行，
            # 经 call_soon_threadsafe 桥回主循环；标记先行保证按用户取消路由
            _loop = asyncio.get_running_loop()

            def _kill() -> bool:
                self._cancel_marks.add(delegation_id)
                try:
                    _loop.call_soon_threadsafe(self.cancel, delegation_id)
                    return True
                except RuntimeError:
                    return False  # 循环已关闭（关停中）

            registry.attach_killer(delegation_id, _kill)
        else:
            delegation_id = uuid.uuid4().hex[:8]

        # 发射 started 事件
        effective_scope = scope or _owner_scope("")
        _user_scope, chat_id = _parse_scope_chat_id(effective_scope)
        model_id = self._resolve_model(agent_name, difficulty)
        try:
            asyncio.create_task(event_bus.emit(EVENT_DELEGATION_STARTED, {
                "scope": effective_scope,
                "chat_id": chat_id,
                "delegation_id": delegation_id,
                "goal": goal,
                "context_preview": context[:200],
                "role": normalize_role(role),
                "task_index": 0,
                "background": True,
                "depth": current_depth(),
                "model": model_id,
                "agent": agent_name,
            }))
        except Exception:
            log("delegate_background 异常已忽略", "DEBUG")

        task = asyncio.create_task(
            self._run_background(
                delegation_id, goal, context, role, max_iterations, scope,
                difficulty=difficulty, agent_name=agent_name,
                fork_context=fork_context,
                facets=facets,
                base_messages=base_messages,
                parent_delegation_id=parent_delegation_id,
            ),
            name=f"delegation.{delegation_id}",
        )
        self._background_tasks[delegation_id] = task

        def _bg_done(t: "asyncio.Task") -> None:
            self._background_tasks.pop(delegation_id, None)
            if not t.cancelled():
                _ = t.exception()  # 取回异常防 never-retrieved 告警

        task.add_done_callback(_bg_done)
        log(f"后台委托已启动: {delegation_id} -> {goal[:60]}", tag="委托")
        return delegation_id

    async def _run_background(
            self,
            delegation_id: str,
            goal: str,
            context: str,
            role: str,
            max_iterations: int,
            scope: str = "",
            *,
            difficulty: int = 0,
            agent_name: str = "",
            fork_context: bool = False,
            facets: Optional[AgentFacets] = None,
            base_messages: Optional[List[Dict]] = None,
            parent_delegation_id: str = "",
    ) -> None:
        """后台执行委托并按注册表路由结果（轮内会合 / 完成即新 turn）。

        异常兜底：delegate 或事件发射的任何异常都必须转化为失败结果并
        完成 registry.complete() 登记，否则 delegation 会永久卡在 running。
        """
        try:
            result = await self.delegate(
                goal, context, role=role, max_iterations=max_iterations,
                scope_hint=scope, difficulty=difficulty, fork_context=fork_context,
                delegation_id=delegation_id, agent_name=agent_name,
                emit_events=False,
                facets=facets,
                base_messages=base_messages,
                parent_delegation_id=parent_delegation_id,
            )
        except asyncio.CancelledError:
            # 用户取消（含并发槽等待阶段）：转化为取消结果继续走正常路由；
            # 非用户取消（服务关闭等）先落终态再向上传播，防记录永久卡 running
            if delegation_id in self._cancel_marks:
                self._cancel_marks.discard(delegation_id)
                result = _cancelled_result(goal, role=role)
            else:
                try:
                    bg_registry = getattr(self._mind, "background_tasks", None)
                    if bg_registry is not None:
                        bg_registry.complete(
                            delegation_id, False, "后台委托被取消（非用户操作）",
                        )
                except Exception:
                    log(f"后台委托取消登记失败: {delegation_id}", "DEBUG", tag="委托")
                raise
        except Exception as exc:
            log(f"后台委托执行异常: {delegation_id}: {exc}", "ERROR", tag="委托")
            result = SubAgentResult(
                goal=goal, success=False,
                error=f"后台委托执行异常: {type(exc).__name__}: {exc}",
                role=normalize_role(role),
            )

        status = "已取消" if result.cancelled else ("成功" if result.success else "失败")
        summary = (result.output if result.success else result.error) or ""

        try:
            await event_bus.emit(EVENT_DELEGATION_COMPLETED, {
                "delegation_id": delegation_id,
                "goal": goal,
                "success": result.success,
                "output": result.output,
                "error": result.error,
                **({"cancelled": True} if result.cancelled else {}),
            })

            # 向 webui 前端推 resolved（DelegationCard 关闭/标完成）
            effective_scope = _owner_scope(scope)
            _user_scope, chat_id = _parse_scope_chat_id(effective_scope)
            try:
                await event_bus.emit(EVENT_DELEGATION_RESOLVED, {
                    "scope": effective_scope,
                    "chat_id": chat_id,
                    "delegation_id": delegation_id,
                    "goal": goal,
                    "success": result.success,
                    "output": result.output[:_RESOLVED_OUTPUT_PREVIEW_CHARS],
                    "error": result.error,
                    "task_index": 0,
                    "background": True,
                    **({"cancelled": True} if result.cancelled else {}),
                })
            except Exception:
                log("_run_background 异常已忽略", "DEBUG")
        except Exception as exc:
            log(f"后台委托事件发射失败: {delegation_id}: {exc}", "WARNING", tag="委托")

        note = (
            f"[后台委托完成] id={delegation_id} 状态={status}\n"
            f"目标: {goal[:200]}\n结果: {summary[:_SUMMARY_NOTICE_MAX_CHARS]}"
        )
        if getattr(result, "completed_reason", "completed") == "budget_exhausted":
            note += "\n（轮次预算用尽，结果可能不完整；需要更完整结论可拆小任务重新委托）"

        # 结果登记兜底：无论后续路由是否成功，registry 都必须完成，
        # 否则 delegation 永久卡 running、等待者永远收不到会合注入
        registry = getattr(self._mind, "background_tasks", None)
        claimed = False
        try:
            claimed = registry.complete(
                delegation_id, result.success, summary[:_SUMMARY_NOTICE_MAX_CHARS],
            ) if registry else False
        except Exception as exc:
            log(f"后台委托结果登记失败: {delegation_id}: {exc}", "ERROR", tag="委托")

        try:
            if claimed:
                # 轮内会合：等待者本轮已收到完成注入（ephemeral），完整详情
                # 固化到对话历史供后续轮次回溯（一次性事实，不驻留短期记忆）
                from agent.mind.tools.scheduler import _append_one_shot_history
                if not await _append_one_shot_history(
                        self._mind.pfc, scope,
                        self._mind.pfc.get_adapter_key(scope), note):
                    self._mind.pfc.add_temporary({"role": "user", "content": note}, scope=scope)
            elif registry is None:
                # 无注册表（极端降级路径）：自行写历史 + 入队 + 触发新 REPLY；
                # 非 conversation scope 无处写历史，全局短期记忆桶兜底
                if scope.startswith(("user_", "group_")):
                    from agent.mind.tools.scheduler import enqueue_scope_reply
                    await enqueue_scope_reply(
                        self._mind.pfc,
                        scope,
                        self._mind.pfc.get_adapter_key(scope),
                        f"后台委托完成: {goal[:60]}",
                        note + "\n请将结果告知用户，或根据结果继续未完成的操作。",
                    )
                    asyncio.create_task(self._mind.try_execute_mind())
                else:
                    self._mind.pfc.add_temporary({"role": "user", "content": note})
            # 轮外完成且有注册表：unclaimed 回调统一负责（写历史 + 入队 +
            # 唤醒；非 conversation scope 由回调侧全局桶兜底），此处不重复投递
        except Exception as exc:
            log(f"后台委托结果路由失败: {delegation_id}: {exc}", "ERROR", tag="委托")
        log(f"后台委托完成: {delegation_id} ({status})", tag="委托")

    def background_tasks_snapshot(self, scope: str) -> Dict[str, Any]:
        """当前 scope 后台任务状态快照（check_background_tasks 工具用）。"""
        registry = getattr(self._mind, "background_tasks", None)
        if registry is None:
            return {"running": [], "completed": []}
        return registry.snapshot(scope)

    # ------------------------------------------------------------------
    # 结果聚合
    # ------------------------------------------------------------------

    def aggregate_results(self, results: List[SubAgentResult]) -> str:
        """聚合子代理结果为工具返回（JSON），摘要按父上下文预算截断。"""
        budget = self._summary_char_budget(len(results))
        items: List[Dict[str, Any]] = []
        for r in results:
            output = r.output
            if len(output) > budget:
                output = self._trim_summary(output, budget)
            item: Dict[str, Any] = {
                "task_index": r.task_index,
                "goal": r.goal,
                "success": r.success,
                "output": output,
            }
            if r.error:
                item["error"] = r.error
            if r.usage:
                # 执行用量：父级可据此判断子代理是否烧了过多轮次（该拆任务了）
                item["usage"] = dict(r.usage)
            if r.cancelled:
                item["cause"] = "user_cancel"
                item["retryable"] = False
            if r.completed_reason == "budget_exhausted":
                # 轮次预算用尽：产出可能只是中途状态，父级可决策拆小重委托
                item["completed_reason"] = "budget_exhausted"
                item["hint"] = "轮次预算用尽，结果可能不完整；需要更完整结论可拆分为更小的子任务重新委托"
            if r.schema_ok is False:
                # 输出契约未满足：事实报告，父级决定重试还是将就用文本
                item["schema_ok"] = False
                item["hint"] = (item.get("hint", "") + " " if item.get("hint") else "") + \
                    "输出未满足档案 output_schema 契约（未解析出合法 JSON），如需结构化结果可重试或续跑补交"
            items.append(item)
        succeeded = sum(1 for r in results if r.success)
        return json.dumps({
            "ok": succeeded == len(results),
            "total": len(results),
            "succeeded": succeeded,
            "failed": len(results) - succeeded,
            "results": items,
        }, ensure_ascii=False)

    def _summary_char_budget(self, n_summaries: int) -> int:
        """每个子任务摘要的字符预算（父上下文剩余空间均分，参考 hermes）。"""
        context_length = self._mind.get_model_context_length()
        if context_length <= 0:
            return _MAX_SUMMARY_CHARS
        headroom_chars = context_length * _CHARS_PER_TOKEN
        per_summary = int(headroom_chars * _SUMMARY_HEADROOM_FRACTION) // max(1, n_summaries)
        return max(_MIN_SUMMARY_CHARS, min(per_summary, _MAX_SUMMARY_CHARS))

    @staticmethod
    def _trim_summary(text: str, budget: int) -> str:
        """摘要截断：保留头部 75% + 尾部 25% + 截断标记。"""
        head = int(budget * _TRIM_HEAD_FRACTION)
        tail = budget - head
        return (
            f"{text[:head]}\n"
            f"...[摘要过长已截断，原长度={len(text)} 字符]...\n"
            f"{text[-tail:]}"
        )


# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

_DELEGATION_CONFIGS = {
    "delegation/core": {
        "delegation_enabled": {
            "description": "是否启用子代理委托",
            "default": True,
        },
        "delegation_max_depth": {
            "description": "最大委托深度（orchestrator 可再委托的层数）",
            "default": 2,
            "advanced": True,
            "unit": "层",
        },
        "delegation_max_concurrent": {
            "description": "子代理并发上限",
            "default": 3,
            "advanced": True,
            "unit": "个",
        },
        "delegation_default_iterations": {
            "description": "子代理默认迭代预算",
            "default": 15,
            "advanced": True,
            "unit": "轮",
        },
        "delegation_max_iterations_cap": {
            "description": "子代理迭代预算硬上限",
            "default": 50,
            "advanced": True,
            "unit": "轮",
        },
        "delegation_timeout_seconds": {
            "description": "单个子代理整体执行超时",
            "default": 600,
            "advanced": True,
            "unit": "秒",
        },
        "delegation_acquire_timeout_seconds": {
            "description": "委托并发槽获取超时（超时返回失败而非永久阻塞）",
            "default": 300,
            "advanced": True,
            "unit": "秒",
        },
        "delegation_transcript_enabled": {
            "description": "委托 transcript 持久化（follow_up_agent 无损续跑的数据源）",
            "default": True,
            "advanced": True,
        },
        "delegation_journal_retention_days": {
            "description": "委托运行日志（进度流/transcript）保留天数",
            "default": 7,
            "advanced": True,
            "unit": "天",
        },
    },
}

from core.config import register_configs_safe  # noqa: E402

register_configs_safe(_DELEGATION_CONFIGS)
