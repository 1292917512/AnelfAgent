"""按会话水位并入实时收发消息、推送与子代理转向。"""


from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Dict, List

from agent.context.message_schema import is_genuine_user_message
from agent.mind.execution.state import ExecutionContext, RoundState, ThinkMode
from agent.mind.execution.vision import apply_vision
from core.log import log

if TYPE_CHECKING:
    from agent.messages import Everything
    from agent.mind.mind import Mind
    from agent.storage.sqlite_backend import ConversationRow


def snapshot_watermark(messages: List[Dict]) -> tuple[int, int]:
    """读取本次历史快照的水位，其他读者不改变本轮已读范围。"""
    history = [m["_source"] for m in messages if m.get("_source", {}).get("origin") == "conversation"]
    return (
        max((int(row.get("ts_ns", 0)) for row in history), default=0),
        max((int(row.get("id", 0)) for row in history), default=0),
    )


def _user_initiated(messages: List[Dict]) -> bool:
    """以当前会话最新输入判定用户轮次；系统唤醒和动态注入不冒充用户。"""
    for message in reversed(messages):
        source = message.get("_source") or {}
        if source.get("origin") == "conversation" and message.get("role") in {"user", "system"}:
            return message["role"] == "user"
        if not source and not message.get("_layer") and message.get("role") == "user":
            return True
    return False


def _consume_pending_for_scope(mind: "Mind", anything: "Everything", through_ts: int) -> None:
    """消费当前 scope 的待处理队列条目（新消息已并入当前循环，无需另起周期）。"""
    try:
        mind.pfc.consume_scope_task(anything.entity_scope, through_ts=through_ts)
    except Exception as exc:
        log(f"消费待处理队列失败: {exc}", "DEBUG", tag="思维")


async def _fetch_new_messages(
        mind: "Mind",
        anything: "Everything",
        since_ts: int,
        since_id: int = 0,
) -> list[ConversationRow]:
    """从实时会话窗口读取当前历史快照之后的收发消息与系统事实。"""
    try:
        return await mind.conversation_data.router.sqlite.fetch_live_conversation(
            scope_type=anything.scope_type, scope_id=anything.scope_id, after_id=since_id, after_ts=since_ts,
            limit=_MAX_MERGED_MESSAGES + 1,
        )
    except Exception as exc:
        log(f"获取新消息失败: {exc}", "DEBUG", tag="思维")
        return []


def _maybe_reset_guardrail_for_interjection(guardrail, new_msgs: Sequence[Mapping[str, object]]) -> bool:
    """用户插话时重置工具守卫链。

    仅认真实渠道到达的用户消息（带到达元数据标签）为插话；机器生成的
    user 角色消息（proactive 指令/prefill 独白）不触发重置。返回是否重置。
    """
    if any(
        m.get("role", "user") == "user" and is_genuine_user_message({"role": "user", "content": m.get("content")})
        for m in new_msgs
    ):
        guardrail.reset()
        return True
    return False


_MAX_MERGED_MESSAGES = 20


_MAX_MERGED_CHARS = 20000


async def _merge_new_messages(ctx: ExecutionContext, state: RoundState) -> bool:
    """并入循环期间到达的收发消息与系统事实；返回是否接收了新的用户消息。"""
    from core.sanitizer import clean_surrogates, has_surrogates

    mind = ctx.mind
    if not (ctx.mode == ThinkMode.REPLY and ctx.anything):
        return False
    new_msgs = await _fetch_new_messages(
        mind, ctx.anything, state.last_merged_ts, state.last_merged_id,
    )
    if not new_msgs:
        return False
    # 有界并入：首条强制并入保证进度；达到条数/字符上限即停，留存消息
    # 不推进水位（下轮再取），且保留待处理队列条目（循环结束另起周期接续）
    merged: list[ConversationRow] = []
    merged_chars = 0
    for m in new_msgs:
        content_len = len(m["content"]) if isinstance(m.get("content"), str) else 0
        if merged and (len(merged) >= _MAX_MERGED_MESSAGES
                       or merged_chars + content_len > _MAX_MERGED_CHARS):
            break
        merged.append(m)
        merged_chars += content_len
    deferred = len(new_msgs) - len(merged)
    receipts = {message["content"] for message in ctx.tool_chain
                if message.get("_source", {}).get("origin") == "reply_delivery"
                and isinstance(message.get("content"), str)}
    updates = [message for message in merged
               if not (message["role"] == "system" and isinstance(message.get("content"), str)
                       and message["content"] in receipts)]
    for m in updates:
        content = m["content"]
        # 孤代理字符在并入入口清洗一次（发送边界的全量扫描是兜底；
        # 入口清洗同时保证 FTS/embedding/摘要链路拿到干净文本）
        if isinstance(content, str) and has_surrogates(content):
            content = clean_surrogates(content)
        ctx.tool_chain.append({"role": m["role"], "content": content,
                               "_source": {"origin": "conversation", "id": m.get("id", 0), "ts_ns": m["ts_ns"]}})
    # 用户插话重置守卫链：用户新消息改变了语境，
    # 跨插话的"重复调用"不再构成死循环
    if _maybe_reset_guardrail_for_interjection(ctx.guardrail, merged):
        state.consecutive_tool_errors = 0
        log("用户插话，重置工具守卫链", "DEBUG", tag="守卫")
    state.last_merged_ts = max(state.last_merged_ts, *(m["ts_ns"] for m in merged))
    if merged[-1].get("id"):
        state.last_merged_id = int(merged[-1]["id"])
    # 全部并入才消费待处理队列条目；有留存时保留，避免留存消息无人回复
    if not deferred:
        _consume_pending_for_scope(mind, ctx.anything, through_ts=state.last_merged_ts)
    # 新消息携带的媒体：激活对应媒体工具并重建工具集供后续轮次使用
    # （媒体标签已随内容并入上下文，待处理媒体不留存到后续周期）
    try:
        merged_images = mind.pfc.collect_images(scope=ctx.current_scope)
        merged_media = mind.pfc.collect_media(scope=ctx.current_scope)
    except TypeError:
        merged_images = mind.pfc.collect_images()
        merged_media = mind.pfc.collect_media()
    if merged_images:
        # 视觉模型直传图片 block；非视觉模型转存超大 base64 为文件路径
        ctx.tool_chain = await apply_vision(mind, ctx.tool_chain, merged_images)
    if merged_images or merged_media:
        mind.pfc.activate_media_tools(merged_images, merged_media)
        ctx.active_tools = await mind.pfc.get_active_tool_schemas(
            ctx.adapter_key, scope=ctx.anything.entity_scope if ctx.anything else "",
        )
    deferred_note = f"，{deferred} 条留存下轮接续" if deferred else ""
    log(f"并入 {len(merged)} 条循环期间新消息到当前上下文{deferred_note}", tag="思维")
    if updates:
        ctx.execution_steps.append(
            f"→ 第{state.iteration + 1}轮前: 并入 {len(updates)} 条新消息{deferred_note}"
        )
    from agent.context.observation import emit_context_update
    await emit_context_update([message for message in updates if message["role"] != "assistant"],
                              origin="conversation", scope=ctx.current_scope,
                              iteration=state.iteration + 1, deferred=deferred)

    new_user = any(m["role"] == "user" for m in merged)
    if new_user:
        state.auto_reply_eligible = True
        state.auto_reply_blocked = False
    return new_user


async def _merge_pushes(ctx: ExecutionContext, state: RoundState) -> None:
    """并入循环期间到达的实体推送（[push:] 系统通知），实现轮内弹窗。

    推送到达时已写入短期记忆（后续会话可见）；此处将快照后新到的推送
    直接注入当前轮工具链尾部，AI 无需等会话结束即可感知，与手机弹窗同语义。
    """
    mind = ctx.mind
    if not (ctx.mode == ThinkMode.REPLY and ctx.anything):
        return
    push_hub = getattr(mind, "push_hub", None)
    if push_hub is None:
        return
    texts = push_hub.drain_inflight(ctx.current_scope, since=state.push_watermark)
    if not texts:
        return
    known = {m["content"] for m in ctx.base_messages + ctx.tool_chain
             if m.get("role") == "system" and isinstance(m.get("content"), str)}
    texts = [text for text in texts if text not in known]
    if not texts:
        return
    lines = ["[实体推送] 以下系统通知在你回复过程中到达（[push:来源] 标签，非用户消息）："]
    lines.extend(f"- {text}" for text in texts)
    lines.append("可按需响应，或继续完成当前对话；无需处理则忽略并继续。")
    ctx.tool_chain.append({"role": "system", "content": "\n".join(lines),
                           "_source": {"origin": "push"}})
    # 消费对应的待处理队列条目，避免当前轮结束后又空转一个周期
    try:
        mind.pfc.consume_scope_task(ctx.current_scope, through_ts=state.last_merged_ts)
    except Exception as exc:
        log(f"消费推送待处理队列失败: {exc}", "DEBUG", tag="思维")
    log(f"并入 {len(texts)} 条实体推送到当前上下文", tag="思维")
    ctx.execution_steps.append(f"→ 第{state.iteration + 1}轮前: 并入 {len(texts)} 条实体推送")
    from agent.context.observation import emit_context_update
    await emit_context_update([{"role": "system", "content": text} for text in texts],
                              origin="push", scope=ctx.current_scope, iteration=state.iteration + 1)


def _merge_steered_messages(ctx: ExecutionContext, state: RoundState) -> None:
    """并入转向指令（steer 档）：运行中委托在步骤边界收到的追加指示。

    委托方向运行中的子代理发送的转向消息暂存于 SteerInbox（按
    delegation_id），SubAgent 经 ContextVar 绑定 drain 闭包——此处轮顶
    取走并注入工具链尾部，当前轮 LLM 即见。主会话未绑定 drain 时恒空
    （零开销）；注入为 user 角色（带 [转向指令] 标记），消息在最近的
    步骤边界生效，可改变进行中的工作。
    """
    from agent.delegation.steer import drain_steered_messages
    messages = drain_steered_messages("steer")
    if not messages:
        return
    ctx.summary_only = False
    lines = ["[转向指令] 委托方在你执行期间发来新指令，请据此调整当前工作"
             "（不否定已完成部分，按需修正方向）："]
    lines.extend(f"- {msg}" for msg in messages)
    ctx.tool_chain.append({"role": "user", "content": "\n".join(lines),
                           "_source": {"origin": "steer"}})
    log(f"并入 {len(messages)} 条转向指令（步骤边界）", tag="委托")
    ctx.execution_steps.append(f"→ 第{state.iteration + 1}轮前: 并入 {len(messages)} 条转向指令")


def merge_after_messages(ctx: ExecutionContext, state: RoundState) -> bool:
    """收束边界消费 after 档指令：子代理本要结束时注入追加指示续跑。

    对齐 pi 的 followUp 档（仅当代理即将停止时投递）：与 steer 档的差别
    在时机——steer 改变进行中的工作，after 在"这批做完后"追加。注入后
    重置反思纯文本计数，循环继续；主会话未绑定 drain 时恒空（返回
    False，走原收束路径）。
    """
    from agent.delegation.steer import drain_steered_messages
    messages = drain_steered_messages("after")
    if not messages:
        return False
    ctx.summary_only = False
    lines = ["[追加指令] 委托方在你即将收束时发来追加要求，请在既有进展上继续完成："]
    lines.extend(f"- {msg}" for msg in messages)
    ctx.tool_chain.append({"role": "user", "content": "\n".join(lines),
                           "_source": {"origin": "steer"}})
    log(f"并入 {len(messages)} 条追加指令（收束边界续跑）", tag="委托")
    ctx.execution_steps.append(f"→ 第{state.iteration + 1}轮: 并入 {len(messages)} 条追加指令，续跑")
    return True
