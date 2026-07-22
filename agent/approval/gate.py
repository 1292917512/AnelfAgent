"""批准门 — 工具调用前的人工确认入口（统一权限引擎驱动）。

职责：
1. 经统一规则引擎求值（PermissionRuleSet.evaluate）
2. auto_allow / auto_deny 直接决策（deny 会通知用户原因）
3. ask → 创建批准会话 → 频道提示（WebUI 为 SSE 弹窗）→ 等待决策
4. 支持"记住决策"：本会话不再询问（内存规则）/ 永久放行（写入规则文件）
5. 决策结果（含命中规则）回写频道与日志，拒绝原因全链路可见

使用方式：
    gate = get_approval_gate()
    decision = await gate.request_approval(
        tool_name="write_file",
        tool_args={"path": "/tmp/x", "content": "..."},
        reason="high risk write",
        channel=current_channel,
        chat_id="...",
        user_id="...",
    )
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Dict, List, Optional

from core.log import log

from agent.channel.base import ApprovalPromptRenderContext, BaseChannel

from .manager import ApprovalManager, get_approval_manager
from .policy import ApprovalPolicySet
from .rules import (
    COMPOUND_CMD_RE,
    COMMAND_TOOLS,
    PermissionDecision,
    PermissionEffect,
    PermissionRule,
    PermissionRuleSet,
    PermissionVerdict,
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
        self._session_rules: List[PermissionRule] = []

    # ------------------------------------------------------------------
    # 规则管理
    # ------------------------------------------------------------------

    def get_rule_set(self) -> PermissionRuleSet:
        """获取当前规则集（含会话级规则）。"""
        return PermissionRuleSet(
            rules=[*self._session_rules, *self._rule_set.rules],
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
            self._session_rules.insert(0, rule)

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
        channel: BaseChannel,
        chat_id: str,
        user_id: str,
        timeout: Optional[float] = None,
    ) -> ApprovalDecision:
        """请求批准（核心入口）。

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
                return ApprovalDecision.APPROVED

        # ASK：走人工批准流程
        timeout_seconds = timeout or (rule.timeout_seconds if rule else 60.0)
        request = ApprovalRequest(
            tool_name=tool_name,
            tool_args=self._sanitize_args(tool_args),
            risk_level=rule.risk_level if rule else self._rule_set.default_risk,
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

        decision = await self._wait_for_decision(session, timeout_seconds)

        if decision == ApprovalDecision.EXPIRED:
            on_timeout = rule.on_timeout if rule else "deny"
            if on_timeout == "allow":
                log(f"批准超时但规则允许: {tool_name}", "WARNING", tag="权限")
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
        - 文件类：取 path 参数精确值（`write_file(/exact/path)`）
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
            path = str(tool_args.get("path") or "").strip()
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
            self._session_rules.insert(0, rule)
        log(f"放行规则已创建: [{rule.pattern}] scope={rule.scope} "
            f"({'会话级' if effective == 'session' else '永久'})", tag="权限")

    def _sanitize_args(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """脱敏工具参数（移除 API Key / Token / 密码等）。"""
        sensitive_keys = {"api_key", "token", "password", "secret", "key", "auth"}
        sanitized: Dict[str, Any] = {}
        for k, v in args.items():
            if any(s in k.lower() for s in sensitive_keys):
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

    async def _notify_outcome(self, channel: BaseChannel, chat_id: str, text: str) -> None:
        """把权限决策结果通知到频道（best-effort，拒绝原因对用户可见）。"""
        try:
            send_text = getattr(channel, "send_text", None)
            if callable(send_text):
                await send_text(chat_id, text)
        except Exception as exc:
            log(f"权限结果通知发送失败: {exc}", "DEBUG", tag="权限")

    async def _wait_for_decision(
        self,
        session: ApprovalSession,
        timeout: float,
    ) -> ApprovalDecision:
        """等待决策（轮询）。"""
        deadline = time.time() + timeout
        poll_interval = 0.5
        while time.time() < deadline:
            current = await self._manager.get_session(session.request.request_id)
            # 先检查决策：resolved 会话 status 已非 pending，但 decision 有效
            if current and current.decision:
                return current.decision
            if not current or current.status != "pending":
                break
            await asyncio.sleep(poll_interval)
            poll_interval = min(poll_interval * 1.2, 2.0)  # 渐进退避

        # 超时
        await self._manager.resolve(
            session.request.request_id,
            ApprovalDecision.EXPIRED,
            "system",
            "timeout",
        )
        return ApprovalDecision.EXPIRED


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
