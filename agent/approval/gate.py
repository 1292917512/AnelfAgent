"""批准门 — 工具调用前的确认入口（统一权限引擎 + Guardian 自动评审）。

流程：规则引擎求值 → auto_allow/auto_deny 直接决策（deny 通知用户原因）→
ask 先经 Guardian 自动评审（放行即执行），guardian 拒绝或不可用且有频道时
才创建批准会话、发频道提示、事件驱动等待人工决策；无频道时由 guardian 独立
裁决，评审不可用则放行。

放行不打折、但要留痕：guardian 放行但风险 high、或无人可问路径上评审
不可用放行时，经 notify 回调给 AI 本身发一条提醒（提醒而非拒绝——自主
路径人不在场，由模型复核后自行决定是否告知用户）；审计表同步记录。

支持"记住决策"：本会话不再询问（内存规则）/ 永久放行（写入规则文件）。

使用方式：
    gate = get_approval_gate()
    decision = await gate.request_approval(
        tool_name="write_file",
        tool_args={"file_path": "/tmp/x", "content": "..."},
        reason="high risk write",
        channel=current_channel,  # 无人可问路径传 None
        chat_id="...",
        user_id="...",
    )
"""

from __future__ import annotations

import asyncio
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from agent.channel.base import ApprovalPromptRenderContext, BaseChannel
from core.log import log

from . import audit
from .guardian import get_approval_guardian
from .manager import ApprovalManager, get_approval_manager
from .policy import ApprovalPolicySet
from .rules import (
    PermissionDecision,
    PermissionEffect,
    PermissionRule,
    PermissionRuleSet,
    from_legacy_policyset,
    load_rules,
    save_rules,
)
from .session import ApprovalDecision, ApprovalRequest, ApprovalSession


class ApprovalDenied(Exception):
    """批准被拒绝。"""

    def __init__(self, decision: ApprovalDecision, reason: str = "") -> None:
        self.decision = decision
        self.reason = reason
        super().__init__(f"Approval {decision.value}: {reason}")


class ApprovalGate:
    """批准门（单例）。"""

    def __init__(
        self,
        manager: Optional[ApprovalManager] = None,
        rule_set: Optional[PermissionRuleSet] = None,
    ) -> None:
        self._manager = manager or get_approval_manager()
        self._rule_set = rule_set if rule_set is not None else load_rules()
        # 会话级放行规则（重启失效）：(scope, tool_name) → rule
        # 由 _session_rules_lock 保护（web 层等外部线程也会读写）
        self._session_rules: List[PermissionRule] = []
        self._session_rules_lock = threading.Lock()

    # ------------------------------------------------------------------
    # 规则管理
    # ------------------------------------------------------------------

    def get_rule_set(self) -> PermissionRuleSet:
        """获取当前规则集（含会话级规则）。"""
        with self._session_rules_lock:
            session_rules = list(self._session_rules)
        return PermissionRuleSet(
            rules=[*session_rules, *self._rule_set.rules],
            default_effect=self._rule_set.default_effect,
            default_risk=self._rule_set.default_risk,
        )

    def set_rule_set(self, rule_set: PermissionRuleSet, *, persist: bool = False) -> None:
        """替换规则集（不含会话级规则）。"""
        self._rule_set = rule_set
        if persist:
            save_rules(rule_set)

    def reload_rules(self, path: str = "") -> None:
        """从文件重载规则（热更新入口）。"""
        self._rule_set = load_rules() if not path else load_rules(path)
        log(f"权限规则已重载 ({len(self._rule_set.rules)} 条)", tag="权限")

    def add_rule(self, rule: PermissionRule, *, persist: bool = True) -> None:
        """添加规则；persist 时写入规则文件。"""
        if persist:
            self._rule_set.rules.append(rule)
            save_rules(self._rule_set)
        else:
            self.set_session_rule(rule)

    def session_rule_count(self) -> int:
        """会话级规则数量（用于区分持久规则与会话规则）。"""
        with self._session_rules_lock:
            return len(self._session_rules)

    def list_session_rules(self) -> List[PermissionRule]:
        """列出全部会话级放行规则（返回副本，供 web 层展示）。"""
        with self._session_rules_lock:
            return list(self._session_rules)

    def set_session_rule(self, rule: PermissionRule) -> None:
        """新增一条会话级放行规则（置于最前，优先生效；重启失效）。"""
        with self._session_rules_lock:
            self._session_rules.insert(0, rule)
        log(f"会话级放行规则已设置: [{rule.pattern}] scope={rule.scope}", tag="权限")

    def delete_session_rule(self, pattern: str, scope: str = "") -> int:
        """按 pattern（可选叠加 scope）删除会话级规则，返回删除条数。"""
        with self._session_rules_lock:
            before = len(self._session_rules)
            self._session_rules = [
                r for r in self._session_rules
                if not (r.pattern == pattern and (not scope or r.scope == scope))
            ]
            removed = before - len(self._session_rules)
        if removed:
            log(f"会话级放行规则已删除: [{pattern}] scope={scope or '*'} x{removed}", tag="权限")
        return removed

    def delete_rule(self, rule_id: str) -> bool:
        """按 ID 删除规则（先持久规则、后会话规则），返回是否删除成功。"""
        before = len(self._rule_set.rules)
        self._rule_set.rules = [r for r in self._rule_set.rules if r.id != rule_id]
        if len(self._rule_set.rules) != before:
            save_rules(self._rule_set)
            return True
        with self._session_rules_lock:
            before_session = len(self._session_rules)
            self._session_rules = [r for r in self._session_rules if r.id != rule_id]
            return len(self._session_rules) != before_session

    # ---- 旧接口兼容（ApprovalPolicySet） ----

    def set_policy_set(self, policy_set: ApprovalPolicySet) -> None:
        """旧接口：替换策略集（内部转换为统一规则）。"""
        self._rule_set = from_legacy_policyset(policy_set)

    def get_policy_set(self) -> ApprovalPolicySet:
        """旧接口：获取策略集（由统一规则近似转换，仅供旧 API 展示）。"""
        return from_legacy_policyset_to_policies(self._rule_set)

    def reload_policies(self, path: str) -> None:
        """旧接口：从文件重载（自动识别新旧格式）。"""
        self.reload_rules(path)

    # ------------------------------------------------------------------
    # 批准请求
    # ------------------------------------------------------------------

    async def request_approval(
        self,
        *,
        tool_name: str,
        tool_args: Dict[str, Any],
        reason: str,
        channel: Optional[BaseChannel] = None,
        chat_id: str,
        user_id: str,
        timeout: Optional[float] = None,
        abort_check: Optional[Callable[[], bool]] = None,
        notify: Optional[Callable[[str], Any]] = None,
    ) -> ApprovalDecision:
        """请求批准（核心入口）。

        channel 为 None 表示无人可问的路径（reflect/心跳/子代理）：ask 由
        Guardian 独立裁决，评审不可用则放行。abort_check 命中时等待立即收束
        为 CANCELLED。notify 是可选的异步提醒回调（接收提醒文本）：guardian
        放行但风险偏高、或评审不可用放行时调用——放行不打折，提醒的读者是
        AI 本身（如经 PushHub 写入当前 scope，模型当轮/下轮可见后自行复核）。

        Returns:
            ApprovalDecision.APPROVED / DENIED / EXPIRED / CANCELLED
        """
        channel_id = getattr(channel, "channel_id", "") or ""
        verdict = self.get_rule_set().evaluate(tool_name, tool_args, channel_id, user_id)

        if verdict.decision == PermissionDecision.AUTO_ALLOW:
            if verdict.rule is not None:
                log(f"权限放行: {tool_name} — {verdict.reason}", "DEBUG", tag="权限")
            return ApprovalDecision.APPROVED

        if verdict.decision == PermissionDecision.AUTO_DENY:
            log(f"权限拒绝: {tool_name} — {verdict.reason} (user={user_id})", "WARNING", tag="权限")
            audit.record_decision_bg(
                tool_name=tool_name, outcome="denied", decided_by="rule",
                reason=verdict.reason, channel_id=channel_id, chat_id=chat_id,
                user_id=user_id, matched_rule=verdict.matched_pattern or "",
            )
            await self._notify_outcome(
                channel, chat_id,
                f"⛔ 已拒绝执行 {tool_name}\n原因: {verdict.reason}",
            )
            return ApprovalDecision.DENIED

        # ASK：命中规则的信任阈值达成时自动放行（trust_after_n_approvals）
        rule = verdict.rule
        if rule is not None and rule.trust_after_n_approvals > 0:
            if await self._manager.is_trusted(tool_name, user_id, rule):
                log(f"信任阈值达成，自动放行: {tool_name} "
                    f"(规则 [{rule.pattern}]，{rule.trust_after_n_approvals} 次批准)",
                    tag="权限")
                audit.record_decision_bg(
                    tool_name=tool_name, outcome="trusted", decided_by="trust",
                    reason=f"累计批准达阈值 {rule.trust_after_n_approvals}",
                    channel_id=channel_id, chat_id=chat_id, user_id=user_id,
                    matched_rule=rule.pattern,
                )
                return ApprovalDecision.APPROVED

        risk_level = rule.risk_level if rule else self._rule_set.default_risk

        # ASK：Guardian 先行评审，放行即不再打扰用户
        guardian_verdict = await get_approval_guardian().review(
            tool_name=tool_name,
            tool_args=self._sanitize_args(tool_args),
            reason=reason,
            risk_level=risk_level.value,
            channel_id=channel_id,
            user_id=user_id,
        )
        if guardian_verdict is not None and guardian_verdict.approved:
            audit.record_decision_bg(
                tool_name=tool_name, outcome="guardian_approved", decided_by="guardian",
                reason=guardian_verdict.rationale or "guardian 自动放行",
                channel_id=channel_id, chat_id=chat_id, user_id=user_id,
                risk_level=risk_level.value, matched_rule=verdict.matched_pattern or "*",
                tool_args=self._sanitize_args(tool_args),
            )
            # 放行不打折，但 guardian 标了高风险的给 AI 本身留一条提醒
            # （提醒而非拒绝——自主路径人不在场，提醒的读者是模型自己：
            # 自行复核，酌情决定是否告知用户）。
            if str(guardian_verdict.risk).lower() == "high":
                await self._safe_notify(
                    notify,
                    f"guardian 放行了 {tool_name} 但标注风险 high"
                    f"（{guardian_verdict.rationale or '未给出理由'}）。"
                    "请复核该操作是否确属当前任务所需、影响是否可控；"
                    "若涉及用户数据或不可逆变更，先向用户说明再继续。",
                )
            return ApprovalDecision.APPROVED

        if channel is None:
            # 无人可问：guardian 拒绝即拦截；评审不可用则放行
            if guardian_verdict is not None:
                log(f"Guardian 拒绝（无人工可达）: {tool_name} — {guardian_verdict.rationale}",
                    "WARNING", tag="权限")
                audit.record_decision_bg(
                    tool_name=tool_name, outcome="guardian_denied", decided_by="guardian",
                    reason=guardian_verdict.rationale or "guardian 判定危险",
                    channel_id=channel_id, chat_id=chat_id, user_id=user_id,
                    risk_level=risk_level.value, matched_rule=verdict.matched_pattern or "*",
                    tool_args=self._sanitize_args(tool_args),
                )
                return ApprovalDecision.DENIED
            log(
                f"Guardian 不可用（无人可问路径，放行并提醒）: {tool_name} risk={risk_level.value}",
                "WARNING", tag="权限",
            )
            audit.record_decision_bg(
                tool_name=tool_name, outcome="guardian_bypass", decided_by="system",
                reason="guardian 不可用，无人可问路径按自主性放行",
                channel_id=channel_id, chat_id=chat_id, user_id=user_id,
                risk_level=risk_level.value, matched_rule=verdict.matched_pattern or "*",
                tool_args=self._sanitize_args(tool_args),
            )
            await self._safe_notify(
                notify,
                f"guardian 评审不可用，{tool_name} 已按默认放行（风险 {risk_level.value}）——"
                "该操作未经安全评审，请自行评估后果；"
                "若涉及用户数据、凭据或不可逆变更，向用户说明后再执行类似操作。",
            )
            return ApprovalDecision.APPROVED

        # ASK：guardian 判定危险或不可用 → 走人工批准流程
        timeout_seconds = timeout or (rule.timeout_seconds if rule else 60.0)
        request = ApprovalRequest(
            tool_name=tool_name,
            tool_args=self._sanitize_args(tool_args),
            risk_level=risk_level,
            reason=reason,
            requester_channel=channel_id,
            requester_chat_id=chat_id,
            requester_user_id=user_id,
            expires_at=time.time() + timeout_seconds,
            matched_rule=verdict.matched_pattern or "*",
        )
        session = await self._manager.create_session(request)

        try:
            await self._send_approval_prompt(channel, chat_id, session)
        except Exception as exc:
            log(f"发送批准提示失败: {exc}", "ERROR", tag="权限")
            await self._manager.cancel(request.request_id, "send_prompt_failed")
            return ApprovalDecision.CANCELLED

        decision = await self._manager.wait_decision(
            request.request_id, timeout_seconds, abort_check=abort_check,
        )

        if decision == ApprovalDecision.EXPIRED:
            on_timeout = rule.on_timeout if rule else "deny"
            if on_timeout == "allow":
                log(f"批准超时但规则允许: {tool_name}", "WARNING", tag="权限")
                # 超时事实已由 resolve 记为 expired，此处补记最终放行处置
                audit.record_decision_bg(
                    tool_name=tool_name, outcome="timeout_allow", decided_by="rule",
                    reason="on_timeout=allow", channel_id=channel_id, chat_id=chat_id,
                    user_id=user_id, matched_rule=request.matched_rule,
                )
                return ApprovalDecision.APPROVED
            if on_timeout == "halt":
                raise ApprovalDenied(ApprovalDecision.EXPIRED, "timeout halt")
            await self._notify_outcome(
                channel, chat_id,
                f"⏰ 批准请求超时，已拒绝执行 {tool_name}（规则: {request.matched_rule}）",
            )
            return ApprovalDecision.DENIED

        if decision == ApprovalDecision.DENIED:
            await self._notify_outcome(
                channel, chat_id,
                f"🚫 已拒绝执行 {tool_name}（规则: {request.matched_rule}）",
            )
        return decision

    async def approve(self, request_id: str, decided_by: str = "", reason: str = "",
                      remember: str = "once") -> bool:
        """批准；remember: once / session（本会话不再询问）/ always（永久放行）。

        remember=always 时按本次调用参数收窄放行范围（命令头 glob / 文件路径），
        无法安全收窄的（复合 shell 命令）自动降级为 session 并告知原因。
        """
        ok = await self._manager.approve(request_id, decided_by, reason)
        if ok and remember in ("session", "always"):
            session = await self._manager.get_session(request_id)
            if session is not None:
                await self._remember_rule(session, remember, decided_by)
        return ok

    async def deny(self, request_id: str, decided_by: str = "", reason: str = "") -> bool:
        """拒绝。"""
        return await self._manager.deny(request_id, decided_by, reason)

    async def cancel(self, request_id: str, reason: str = "") -> bool:
        """取消。"""
        return await self._manager.cancel(request_id, reason)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    # 命令执行类工具：remember=always 时取命令头 glob，复合命令拒绝永久化
    _COMMAND_TOOLS = frozenset({"run_shell_command", "python_exec"})
    # 文件类工具：remember=always 时按 path 参数收窄
    _FILE_ARG_TOOLS = frozenset({
        "write_file", "edit_file", "append_file", "delete_file", "move_file", "copy_file",
    })
    # 复合命令特征（含任一即不生成永久规则——信任只给"这件事"，不给"所有事"）
    _COMPOUND_CMD_RE = re.compile(r"&&|\|\||[;|]|`\s*[^`]|\$\(")

    @classmethod
    def _build_remember_pattern(cls, tool_name: str, tool_args: Dict[str, Any]) -> Optional[str]:
        """按本次调用参数生成收窄的放行 pattern；无法安全收窄返回 None。

        - 命令类：取命令首 token 作 glob（`npm test` → `run_shell_command(npm *)`）；
          含 &&/|;/$()/反引号 的复合命令返回 None（降级为会话级）
        - 文件类：取路径参数精确值（`write_file(/exact/path)`）
        - 其他工具：裸工具名
        """
        if tool_name in cls._COMMAND_TOOLS:
            command = str(tool_args.get("command") or tool_args.get("code") or "").strip()
            if not command or cls._COMPOUND_CMD_RE.search(command):
                return None
            try:
                import shlex
                head = shlex.split(command, posix=True)[0]
            except (ValueError, IndexError):
                return None
            if not head:
                return None
            return f"{tool_name}({head} *)"
        if tool_name in cls._FILE_ARG_TOOLS:
            path = str(tool_args.get("file_path") or tool_args.get("path") or "").strip()
            if not path or any(ch in path for ch in "()*"):
                return None
            return f"{tool_name}({path})"
        return tool_name

    async def _remember_rule(self, session: ApprovalSession, remember: str,
                             decided_by: str) -> None:
        """把批准决策固化为放行规则（会话级或永久）。

        仅 remember=always 按参数收窄（命令头 glob / 文件路径）——永久规则
        长期生效，"批一次≠全放行"；会话级规则随进程消亡且用户在场，保持
        裸工具名的宽松语义。
        """
        req = session.request
        effective = remember
        pattern = req.tool_name
        if remember == "always":
            narrowed = self._build_remember_pattern(req.tool_name, req.tool_args or {})
            if narrowed is None:
                # 复合命令等无法安全收窄：降级为会话级放行
                effective = "session"
                log(f"永久放行已降级为会话级: {req.tool_name}（参数无法安全收窄）",
                    "WARNING", tag="权限")
            else:
                pattern = narrowed
        rule = PermissionRule(
            pattern=pattern,
            effect=PermissionEffect.ALLOW,
            scope=req.requester_channel or "global",
            users=[req.requester_user_id] if req.requester_user_id not in ("", "unknown") else [],
            description=f"批准时选择{'本会话' if effective == 'session' else '永久'}放行",
            created_by=f"approve:{decided_by or 'unknown'}",
        )
        if effective == "always":
            self._rule_set.rules.append(rule)
            try:
                save_rules(self._rule_set)
            except Exception as exc:
                log(f"永久放行规则写入失败: {exc}", "ERROR", tag="权限")
        else:
            with self._session_rules_lock:
                self._session_rules.insert(0, rule)
        log(f"放行规则已创建: [{rule.pattern}] scope={rule.scope} "
            f"({'会话级' if effective == 'session' else '永久'})", tag="权限")

    # 敏感参数名：按 `_`/`-` 分词边界匹配，避免子串误伤（monkey/keyboard）
    _SENSITIVE_RE = re.compile(
        r"(?:^|[_-])(?:api_key|token|password|secret|key|auth)(?:$|[_-])",
        re.IGNORECASE,
    )

    def _sanitize_args(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """脱敏工具参数（移除 API Key / Token / 密码等）。

        敏感判定按边界匹配：参数名精确等于敏感词，或以 ``_``/``-`` 分隔的
        边界包含敏感词（``api_key``/``my-key``/``key_id`` 命中，``monkey``/
        ``keyboard`` 不命中）。
        """
        sanitized: Dict[str, Any] = {}
        for k, v in args.items():
            if self._SENSITIVE_RE.search(k):
                sanitized[k] = "***REDACTED***"
            elif isinstance(v, str) and len(v) > 2000:
                sanitized[k] = v[:2000] + "..."
            else:
                sanitized[k] = v
        return sanitized

    async def _send_approval_prompt(
        self,
        channel: BaseChannel,
        chat_id: str,
        session: ApprovalSession,
    ) -> None:
        """发送批准提示到频道。"""
        ctx = ApprovalPromptRenderContext(
            request_id=session.request.request_id,
            tool_name=session.request.tool_name,
            tool_args_summary=str(session.request.tool_args),
            risk_level=session.request.risk_level.value,
            reason=session.request.reason,
            timeout_seconds=session.request.expires_at - time.time(),
        )
        request = await channel.render_approval_prompt(ctx)
        # 填充 chat_id（render_approval_prompt 返回的 SendRequest 可能 chat_id 为空）
        request.channel.channel_id = chat_id
        response = await channel.forward_message(request)
        if not response.success:
            raise RuntimeError(f"批准提示发送失败: {response.error}")

    async def _safe_notify(self, notify: Callable[[str], Any], text: str) -> None:
        """best-effort 调用提醒回调（提醒失败不影响放行决策）。"""
        try:
            result = notify(text)
            if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                await result
        except Exception as exc:
            log(f"权限提醒回调失败: {exc}", "DEBUG", tag="权限")

    async def _notify_outcome(self, channel: Optional[BaseChannel], chat_id: str, text: str) -> None:
        """把权限决策结果通知到频道（best-effort，拒绝原因对用户可见）。"""
        if channel is None:
            return
        try:
            send_text = getattr(channel, "send_text", None)
            if callable(send_text):
                await send_text(chat_id, text)
        except Exception as exc:
            log(f"权限结果通知发送失败: {exc}", "DEBUG", tag="权限")


def from_legacy_policyset_to_policies(rule_set: PermissionRuleSet) -> ApprovalPolicySet:
    """统一规则集 → 旧策略集近似转换（仅供旧 API 读取展示）。"""
    from .policy import ApprovalPolicy

    policies: List[ApprovalPolicy] = []
    for r in rule_set.rules:
        policies.append(ApprovalPolicy(
            tool_name_pattern=r.pattern,
            risk_level=r.risk_level,
            requires_approval=r.effect == PermissionEffect.ASK,
            timeout_seconds=r.timeout_seconds,
            on_timeout=r.on_timeout,
            trust_after_n_approvals=r.trust_after_n_approvals,
            description=f"[{r.effect.value}@{r.scope}] {r.description}".strip(),
        ))
    return ApprovalPolicySet(policies=policies)


# ======================================================================
# 全局单例
# ======================================================================

_gate: Optional[ApprovalGate] = None


def get_approval_gate() -> ApprovalGate:
    """获取全局批准门。"""
    global _gate
    if _gate is None:
        _gate = ApprovalGate()
    return _gate
