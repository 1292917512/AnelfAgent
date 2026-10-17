"""外部消息感知与技能手势；先提交历史，再决定是否排队。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent.messages import (
    Everything,
    EverythingGroup,
)
from agent.mind import tool_activation as _tool_activation  # noqa: F401  # 注册 activate_tool_group 延迟工具
from agent.mind.autonomous import (
    MindPhase,
)
from agent.mind.interrupt import (
    is_interrupt_enabled,
    match_interrupt_keyword,
)
from core.log import log

if TYPE_CHECKING:
    from agent.mind.mind import Mind


async def accept_feel(self: Mind, anything: Everything) -> None:
    """接收外部消息：到达即写入对话历史（保证时序）；仅在 trigger_mind 时加入 PFC 任务队列。"""
    try:
        # 思维工作运行中不覆盖当前阶段，避免 UI 阶段抖动
        if (not self._active_scopes and not self._cycle_lock.locked()
                and self._reflect_depth == 0):
            self._set_phase(MindPhase.ACCEPTING)
        preview = str(anything)[:80] if anything else ""
        log(f"感知输入: {preview}", tag="思维")
        if not await self.add_conversation(anything):
            return
        self._update_channel_snapshot(anything)

        # 用户话题指令抽取（ban-topic）：所有用户消息必经点，纯正则零 LLM，
        # 失败不影响消息主流程
        try:
            from agent.messages import CharType
            if getattr(anything, "char_type", None) in (None, CharType.USER):
                from agent.memory.user_directives import observe_message
                await observe_message(
                    anything.entity_scope, anything.get_text_content() or "",
                )
        except Exception:
            pass

        # 中断指令优先：整条消息精确匹配中断关键词且该 scope 正在回复时，
        # 请求中断进行中的会话，而非作为新消息入队（用户意图是"刹车"而非对话）
        scope = anything.entity_scope
        if (
            is_interrupt_enabled()
            and scope in self._active_scopes
            and match_interrupt_keyword(anything.get_text_content() or "")
        ):
            self.interrupts.request(scope, reason="用户发送中断指令")
            log(f"识别到中断指令，已请求中断: {scope}", tag="中断")
            return

        should_enqueue = self.should_enqueue_external_message(anything)
        if (
            not should_enqueue
            and bool(getattr(anything, "trigger_mind", True))
            and self._reflecting
            and isinstance(anything, EverythingGroup)
            and not bool(getattr(anything, "to_me", False))
        ):
            log(f"反思中忽略非 @ 群消息: {anything.entity_scope}", "DEBUG", tag="思维")
        if should_enqueue:
            # 真实外部事件：重置自动续轮退避 + 后台任务唤醒预算（真人参与
            # 后自动唤醒链路重新合法）
            self._auto_cycle_retry = 0
            self.wake_budget.reset(scope)
            # /name 技能手势：真实外部消息正文以 /技能名 开头 → 确定性触发
            # （防伪造：仅此路径检测，工具结果/子代理输出里的 "/x" 不构成手势）
            self._detect_skill_gesture(anything, scope)
            await self.pfc.add_task(anything)
    finally:
        # 感知处理完成边界：未入队/中断/异常路径不会有后续周期覆盖
        # ACCEPTING；已入队时周期未抢锁则先归零、周期启动再覆盖
        self._reset_phase_if_idle()


def _detect_skill_gesture(self: Mind, anything: Everything, scope: str) -> None:
    """检测 /name 技能手势并登记待注入（recollection 消费后清空）。"""
    try:
        from core.config import get_config_bool
        if not get_config_bool("skill_user_gesture_enabled", True):
            return
        from agent.skills.gesture import parse_skill_gesture
        from core.tags import strip_message_meta_tags
        text = strip_message_meta_tags(anything.get_text_content() or "").strip()
        name = parse_skill_gesture(text)
        if not name:
            return
        pending = self._pending_skill_gestures.setdefault(scope, [])
        if len(pending) < 3 and name not in pending:
            pending.append(name)
            log(f"识别到技能手势 /{name}，将在召回时确定性注入: {scope}", "DEBUG", tag="技能")
    except Exception:
        pass  # 手势检测失败不影响消息入队


def should_enqueue_external_message(self: Mind, anything: Everything) -> bool:
    """判断外部消息是否应进入待回复队列。"""
    if not bool(getattr(anything, "trigger_mind", True)):
        return False
    text = (anything.get_text_content() or "").strip()
    has_media = bool(getattr(anything, "images", None) or getattr(anything, "media_segments", None))
    if not text and not has_media:
        log(f"忽略空消息入队: {anything.entity_scope}", "DEBUG", tag="思维")
        return False
    if (
        self._reflecting
        and isinstance(anything, EverythingGroup)
        and not bool(getattr(anything, "to_me", False))
    ):
        return False
    return True
