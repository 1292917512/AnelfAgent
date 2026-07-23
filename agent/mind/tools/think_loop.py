"""统一思维循环：多轮 LLM 调用 + 原生工具编排。

函数以 mind 实例为第一参数，由 Mind 方法委托调用。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from core.stream_events import EVENT_ASSISTANT_DELTA, EVENT_CONTEXT_USAGE
from core.event_bus import (
    event_bus,
    EVENT_BEFORE_REPLY,
    EVENT_AFTER_REPLY,
    EVENT_TOOL_EXECUTED,
    EVENT_THINKING_TOOL_START,
    EVENT_THINKING_TOOL_END,
    EVENT_THINKING_REPLY_ROUND,
    EVENT_THINKING_FAKE_TOOL_CALL,
)
from core.log import log

from agent.mind.tools.result_pipeline import (
    ToolResultPipeline,
    truncate_tool_output as _truncate_tool_output,
)

if TYPE_CHECKING:
    from agent.llm import ChatResult, ImageContent, ToolCall
    from agent.llm.llm_client import LLMClientConfig
    from agent.messages import Everything
    from agent.mind.background_tasks import (
        BackgroundTaskInfo,
        BackgroundTaskRegistry,
        TaskCompletion,
    )
    from agent.mind.guardrails import GuardrailController
    from agent.mind.mind import Mind

_END_REPLY_TOOL_NAME = "end_reply"

# ------------------------------------------------------------------
# 思维循环系统提示常量
# ------------------------------------------------------------------

_PROMPT_TIMEOUT = (
    "[系统通知] 本次 LLM 调用已超时（>{timeout}s），模型可能响应过慢或不可用。\n"
    "请选择以下操作之一：\n"
    "1. 调用 switch_model 切换到响应更快的模型后继续处理\n"
    "2. 调用 end_reply 结束本轮\n"
    "请立即做出选择，不要重复刚才超时的操作。"
)

_PROMPT_FAKE_TOOL_CALL = (
    "[系统拦截] 你上一条回复被拦截，因为你在文本中伪造了工具调用结果。"
    "这些文本不会被执行。你必须通过 function calling 接口发起真正的工具调用。"
    "请立刻使用真正的工具而不是伪造假工具。"
)

_PROMPT_CONTINUE = (
    "[系统提示] 继续执行，若已完成所有操作请调用 end_reply 结束。"
)

# 提示词输出纪律约束：与 API 级强制 tool_choice 并行注入，
# 覆盖端点静默忽略强制值（如 MiniMax 仅支持 auto/none）的失效场景
_PROMPT_TOOL_OUTPUT_DISCIPLINE = (
    "[输出纪律] 你必须严格遵守：\n"
    "1. 回复用户的内容一律调用 send_message 工具发出，直接输出的文字用户完全看不到\n"
    "2. 需要执行动作时立即调用对应工具，禁止只用文字描述动作\n"
    "3. 全部完成后调用 end_reply 结束本轮"
)

# 反思模式的输出纪律（无 send_message，产出文本即反思结果，但动作必须走工具）
_PROMPT_REFLECT_OUTPUT_DISCIPLINE = (
    "[输出纪律] 你必须严格遵守：\n"
    "1. 需要执行动作（检索/查询/分析）时立即调用对应工具，禁止只用文字描述动作\n"
    "2. 文字输出只是思考草稿，不会执行任何操作\n"
    "3. 完成分析后调用 end_reply 结束本轮反思"
)

_PROMPT_INNER_MONOLOGUE = (
    "[系统提示] 你刚才的文字输出是内心独白，用户看不到！"
    "要回复用户必须调用 send_message 工具。"
    "若已完成所有操作请调用 end_reply 结束。"
)

_PROMPT_INNER_MONOLOGUE_STRICT = (
    "[严重警告] 你已连续多次只输出文字而不调用工具！"
    "文字输出用户完全看不到，等于什么都没做。\n"
    "下一轮你必须二选一，禁止再输出任何普通文字：\n"
    "1. 要回复用户 → 立即调用 send_message\n"
    "2. 没有要说的 → 立即调用 end_reply\n"
    "再次只输出文字将被系统强制结束本轮。"
)

# 独白提示的情境化补充：有后台任务运行中时，给 AI 指出正确的查询路径
_PROMPT_MONOLOGUE_TASKS_HINT = (
    "\n当前有 {count} 个后台任务运行中（{tasks}）。"
    "想查进度 → 调用 check_background_tasks；"
    "想等结果 → 调用 end_reply 结束本轮，任务完成时系统会自动通知你。"
)

# 挂起等待超时后的降级提示（任务仍在运行，决策权交还 AI）
_PROMPT_TASKS_STILL_RUNNING = (
    "[系统提示] 你等待的后台任务仍未完成（运行中：{tasks}）。\n"
    "请选择：\n"
    "1. 调用 check_background_tasks 查看最新进度\n"
    "2. 调用 end_reply 结束本轮（任务完成时系统会自动通知你并触发新一轮回复）\n"
    "3. 继续处理其他事务\n"
    "禁止反复输出「任务还在运行」之类的文字——这些文字用户完全看不到。"
)

# 连续内心独白上限：达到后强制结束（防模型陷入叙事模式死循环）
_MAX_CONSECUTIVE_MONOLOGUES = 3

_PROMPT_EMPTY_OUTPUT = (
    "[系统提示] 你刚才没有执行任何操作也没有输出任何内容。"
    "如果你已完成所有任务，请立即调用 end_reply 结束；"
    "如果还有待处理的事情，请立即使用工具继续操作。"
    "禁止再次输出空内容。"
)

_PROMPT_TOOL_ERROR_ESCALATION = (
    "[严重警告] 工具调用连续返回错误，你可能陷入了参数格式错误的循环。"
    "请立即停止重试，改用以下策略之一：\n"
    "1. 调用 end_reply 结束本轮\n"
    "2. 换用完全不同的工具或不同的参数格式\n"
    "禁止继续以相同方式调用正在报错的工具。"
)

_PROMPT_END_BLOCKED_FAILURE = (
    "[系统拦截] 结束请求未生效：本轮以下工具执行失败，相关操作未完成：\n"
    "{failures}\n"
    "请根据错误原因修正后重新调用失败的工具"
    "（注意：target_id 等 ID 类参数必须按 schema 声明传字符串类型，不要传数字），"
    "全部成功后再调用 end_reply 结束。若确认无法修复，可再次调用 end_reply 强制结束。"
)

_PROMPT_END_BLOCKED_PREMATURE = (
    "[系统拦截] 结束请求未生效：你本轮没有执行任何实际操作，"
    "只留下一段用户完全看不到的文字就结束了。\n"
    "注意：end_reply 会彻底结束本轮对话，不存在「下一轮再继续」——"
    "计划中的操作必须在结束前实际发起工具调用，只说不做等于放弃。\n"
    "有未完成的操作 → 立刻调用对应工具；确认无事可做 → 再次调用 end_reply 即可结束。"
)

# 提前结束判定：end_reply 是本轮唯一工具调用且附带超过此长度的文本时，
# 判定为「用大段不可见文字代替实际行动后提前结束」（结构性判定，不解析文本语义）
_PREMATURE_END_MIN_TEXT = 40

_PROMPT_SECURITY_LEAK = (
    "[系统安全检测] 你的上一条回复中包含了会话安全标记（一次性令牌）。"
    "该标记仅用于标识可信历史，严禁复述。"
    "请不要给出额外解释或道歉，保持原有回复格式重新输出。"
)

# max_output_tokens 截断恢复（对齐 Claude Code，最多 3 次）
_MAX_OUTPUT_RECOVERY_LIMIT = 3
_PROMPT_MAX_OUTPUT_CONTINUE = (
    "[系统] 你的上一条输出达到了长度上限被截断。"
    "请直接从中断处继续，不要道歉、不要复述之前的内容；"
    "如果剩余工作较多，请拆分为更小的步骤逐步完成。"
)


class ThinkMode(str, Enum):
    """思维循环模式。"""

    REPLY = "reply"
    """对话模式：处理用户消息，通过工具发送回复。"""

    REFLECT = "reflect"
    """反思模式：内省思考，收集文本输出，不发送消息。"""


# ==================================================================
# 公共入口
# ==================================================================

async def reply_entry(
        mind: Mind,
        anything: Everything,
        images: Optional[List[ImageContent]] = None,
        *,
        adapter_key: str = "",
) -> None:
    """执行回复，异常时发送错误提示。"""
    await event_bus.emit(EVENT_BEFORE_REPLY, {"phase": "llm_calling"})
    try:
        await reply_loop(mind, anything, images or [], adapter_key=adapter_key)
    except Exception as exc:
        log(f"reply 异常: {type(exc).__name__}: {exc}", "ERROR", tag="思维")
        error_msg = f"抱歉，处理消息时出错了: {type(exc).__name__}: {exc}"
        await _send_reply_error(anything, error_msg)
        await complete_reply(mind, anything, error_msg, 0, error=True)


async def _send_reply_error(anything: Everything, error_msg: str) -> None:
    """reply 异常时主动把错误提示发送到来源频道（避免用户端无反馈地空等）。"""
    adapter_key = getattr(anything, "adapter_key", "")
    if not adapter_key:
        return
    try:
        from agent.channel.manager import get_channel_manager
        from agent.messages import EverythingGroup
        channel = get_channel_manager().get(adapter_key)
        if channel is None:
            return
        # 按 is_group_scope 选 group_id/uid（参考 mind.py _resolve_target_id）
        if isinstance(anything, EverythingGroup) and anything.is_group_scope:
            target_id = str(anything.group_id)
        else:
            target_id = str(anything.uid) if anything.uid else ""
        if not target_id:
            return
        raw = await channel.send_text(target_id, error_msg)
        sent_message_id = ""
        try:
            sent_message_id = str(json.loads(raw).get("message_id") or "")
        except (json.JSONDecodeError, TypeError):
            pass
        from agent.channel.output_tools import _record_sent_reply, _resolve_channel_type
        await _record_sent_reply(
            target_id, error_msg, _resolve_channel_type(adapter_key, target_id),
            message_id=sent_message_id,
        )
    except Exception as exc:
        log(f"错误提示发送失败: {exc}", "DEBUG", tag="思维")


def collect_pending_images(mind: Mind, scope: str = "") -> List[ImageContent]:
    return mind.pfc.collect_images(scope=scope)


def save_base64_image(b64_data: str, mime_type: str = "image/jpeg") -> str:
    """将 base64 图片数据保存为文件，返回路径。"""
    import base64
    import os
    import time as _time
    ext = "jpg" if "jpeg" in mime_type else mime_type.split("/")[-1] if "/" in mime_type else "jpg"
    upload_dir = os.path.abspath(os.path.join("workspace", "uploads", "image"))
    os.makedirs(upload_dir, exist_ok=True)
    fname = f"vision_{int(_time.time() * 1000)}_{uuid.uuid4().hex[:6]}.{ext}"
    fpath = os.path.join(upload_dir, fname)
    with open(fpath, "wb") as f:
        f.write(base64.b64decode(b64_data))
    return fpath


async def apply_vision(
        mind: Mind,
        messages: List[Dict],
        images: List[ImageContent],
        anything: Optional[Everything] = None,
) -> List[Dict]:
    """处理图片：视觉模型直接注入图片 block，其余大 base64 图转存为文件路径。

    视觉模型（supports_vision）：图片以多模态 content block 附着到最后一条
    user 消息，LLM 直接"看到"图片，无需 recognize_image 工具中转；
    消息含图片 block 时 chat_with_fallback 的回退链自动收敛到视觉候选。

    非视觉模型：图片标签已由 add_conversation_record_by_everything 写入用户
    消息（持久化），此处不再重复写 system 消息或追加标签（避免 user/system/
    内存三处重复）。仅当图片是超大 base64 数据时转存为文件，并更新用户消息
    中的标签路径。
    """
    if not images:
        return messages

    log(f"processing {len(images)} image(s)", tag="思维")

    config = getattr(getattr(mind, "llm", None), "config", None)
    if config is not None and getattr(config, "supports_vision", False):
        return await _inject_image_blocks(messages, images, config)

    # 仅处理需要转存的超大 base64 图片（QQ/Telegram 通常是 URL/文件路径，无需处理）
    path_map: Dict[str, str] = {}
    for img in images:
        path = img.data
        if not img.is_url and len(path) > 500:
            path_map[path] = save_base64_image(path, img.mime_type)

    if not path_map:
        return messages

    # 将用户消息中的 base64 标签路径替换为转存后的文件路径
    result = list(messages)
    for i in range(len(result) - 1, -1, -1):
        if result[i].get("role") == "user":
            c = result[i].get("content", "")
            if isinstance(c, str):
                for old_path, new_path in path_map.items():
                    c = c.replace(f"[media_path:{old_path}]", f"[media_path:{new_path}]")
                result[i] = {**result[i], "content": c}
            break
    return result


async def _inject_image_blocks(
        messages: List[Dict],
        images: List[ImageContent],
        config: "LLMClientConfig",
) -> List[Dict]:
    """将图片以多模态 content block 注入到最后一条 user 消息（视觉模型直传）。

    按模型 vision_format 逐张协商图片形式：
    - URL 且模型支持 url 视觉：原样引用，不下载
    - 其余（本地路径 / base64 / 模型仅支持 base64）：统一归一为压缩后的 base64

    注入位置在对话尾部，stable/volatile 前缀字节不变，Prompt Caching 不受影响。
    """
    from agent.llm.image_utils import ensure_base64

    prepared: List[ImageContent] = []
    failed: List[str] = []
    for img in images:
        if img.is_url and config.supports_url_vision:
            prepared.append(img)
        else:
            converted = await ensure_base64([img])
            if converted:
                prepared.extend(converted)
            else:
                failed.append(img.data[:80])
    blocks = [img.to_openai_block(flat_url=config.use_flat_image_url) for img in prepared]
    if failed:
        blocks.append({
            "type": "text",
            "text": f"[系统提示] {len(failed)} 张图片加载失败（{'; '.join(failed)}），未包含在消息中，请告知用户。",
        })

    result = list(messages)
    for i in range(len(result) - 1, -1, -1):
        if result[i].get("role") != "user":
            continue
        content = result[i].get("content", "")
        if isinstance(content, str):
            parts: List[Dict] = [{"type": "text", "text": content}] if content else []
        elif isinstance(content, list):
            parts = list(content)
        else:
            parts = []
        result[i] = {**result[i], "content": parts + blocks}
        log(f"图片直传: {len(prepared)} 张注入到最后一条 user 消息", "DEBUG", tag="思维")
        break
    return result


# ==================================================================
# 循环主体
# ==================================================================

def _consume_pending_for_scope(mind: Mind, anything: Everything) -> None:
    """消费当前 scope 的待处理队列条目（新消息已并入当前循环，无需另起周期）。"""
    try:
        from agent.messages import EverythingGroup
        if isinstance(anything, EverythingGroup) and anything.is_group_scope:
            mind.pfc.consume_group_task(anything.group_id)
        else:
            mind.pfc.consume_user_task(anything.uid)
    except Exception as exc:
        log(f"消费待处理队列失败: {exc}", "DEBUG", tag="思维")


async def _fetch_new_user_messages(
        mind: Mind,
        anything: Everything,
        since_ts: int,
) -> List[Dict]:
    """获取循环期间到达的新用户消息（role=user，按时间升序）。

    用于将新消息并入当前 think_loop 上下文，避免图片+文字等连续消息
    被拆成独立周期导致 AI 忘记已回复/丢失上下文关联。
    """
    try:
        scope_type, scope_id = mind._resolve_scope(anything)
        sqlite = mind.conversation_data.router.sqlite
        db = await sqlite._get_db()
        cursor = await db.execute(
            "SELECT role, content, ts_ns FROM conversation_messages "
            "WHERE scope_type=? AND scope_id=? AND ts_ns > ? AND role = 'user' "
            "ORDER BY ts_ns ASC",
            (scope_type, scope_id, int(since_ts)),
        )
        rows = await cursor.fetchall()
        return [{"role": r[0], "content": r[1], "ts_ns": r[2]} for r in rows]
    except Exception as exc:
        log(f"获取新消息失败: {exc}", "DEBUG", tag="思维")
        return []


async def _compress_context(
        mind: Mind,
        base_messages: List[Dict],
        tool_chain: List[Dict],
        scope: str,
) -> tuple[List[Dict], List[Dict]]:
    """执行上下文压缩（保头保尾 + 中间摘要），返回新的 (base_messages, tool_chain)。

    压缩成败记录到熔断器（连续失败 3 次停止尝试）；
    成功后执行 rehydration：重读压缩前正在处理的文件，恢复工作现场
    （对齐 Claude Code post-compact rehydration，消费 file_state 缓存）。
    """
    try:
        new_base, new_chain = await mind.compressor.compress_messages(
            base_messages, tool_chain,
            scope=scope,
            summarizer=mind.summarize_text,
        )
    except Exception as exc:
        mind.compressor._record_compress_result(False)
        log(f"上下文压缩失败: {exc}", "WARNING", tag="压缩")
        raise
    mind.compressor._record_compress_result(True)
    rehydrated = _rehydrate_recent_files(scope)
    if rehydrated:
        new_chain = [*new_chain, {"role": "system", "content": rehydrated}]
    return new_base, new_chain


# rehydration 单文件字符上限与总预算（对齐 Claude Code 5K/文件、50K 总量）
_REHYDRATE_MAX_FILES = 5
_REHYDRATE_PER_FILE_CHARS = 5000
_REHYDRATE_TOTAL_CHARS = 50000


def _rehydrate_recent_files(scope: str) -> str:
    """压缩后重读最近读取/编辑过的文件（≤5 个），生成恢复上下文。"""
    try:
        from entities.filesystem import file_state
        cache = file_state.get_cache(scope)
        entries = cache.recent_entries(_REHYDRATE_MAX_FILES)
    except Exception:
        return ""
    if not entries:
        return ""
    import os
    sections: List[str] = []
    total = 0
    for path, _state in entries:  # 最近使用的优先
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(_REHYDRATE_PER_FILE_CHARS + 1)
        except OSError:
            continue
        if len(content) > _REHYDRATE_PER_FILE_CHARS:
            content = content[:_REHYDRATE_PER_FILE_CHARS] + "\n... (截断)"
        block = f"### {os.path.basename(path)} ({path})\n```\n{content}\n```"
        if total + len(block) > _REHYDRATE_TOTAL_CHARS:
            break
        sections.append(block)
        total += len(block)
    if not sections:
        return ""
    return (
        "[系统] 上下文已压缩。以下是你压缩前正在处理的文件的最新内容"
        "（自动恢复，供继续工作参考；如需编辑请遵循 read-before-write 流程）：\n"
        + "\n\n".join(sections)
    )


def _format_running_tasks(tasks: List["BackgroundTaskInfo"]) -> str:
    """运行中任务的一行式摘要（注入提示词用）。"""
    return "、".join(f"{t.description[:30]}({t.task_id})" for t in tasks) or "无"


def _format_task_completions(
        completions: List["TaskCompletion"],
        running: List["BackgroundTaskInfo"],
) -> str:
    """后台任务完成事件注入文本（system 角色，保持消息交替规范）。"""
    lines = ["[后台任务完成] 你等待的后台任务已结束："]
    for c in completions:
        status = "成功" if c.success else "失败"
        lines.append(f"- {c.description[:60]} ({c.task_id})：{status}")
        if c.summary:
            lines.append(f"  结果：{c.summary[:800]}")
    if running:
        lines.append(f"仍有 {len(running)} 个任务运行中：{_format_running_tasks(running)}")
    lines.append("请根据结果继续处理（回复用户请调用 send_message，完成请调用 end_reply）。")
    return "\n".join(lines)


async def _suspend_for_background(
        mind: Mind,
        anything: Everything,
        registry: "BackgroundTaskRegistry",
        scope: str,
        since_ts: int,
        timeout: float,
        interrupts,
) -> tuple[str, List["TaskCompletion"], float]:
    """挂起等待后台任务完成，返回 (reason, completions, elapsed)。

    挂起期间新消息照常实时入库（accept_feel 不经思考循环）；
    should_abort 轮询中断信号与新消息水位，被打断时立即返回，
    由循环顶部统一并入新消息 / 处理中断，时序不受挂起影响。
    """
    log(f"检测到等待意图，挂起等待后台任务 (scope={scope}, 上限 {timeout:.0f}s)", tag="思维")

    async def _aborted() -> bool:
        if interrupts is not None and interrupts.is_requested(scope):
            return True
        return bool(await _fetch_new_user_messages(mind, anything, since_ts))

    t0 = time.monotonic()
    result = await registry.wait_any(scope, timeout=timeout, should_abort=_aborted)
    return result.reason, result.completions, time.monotonic() - t0

async def reply_loop(
        mind: Mind,
        anything: Everything,
        images: Optional[List[ImageContent]] = None,
        *,
        adapter_key: str = "",
) -> None:
    """多轮对话循环入口：处理图片，委托给统一思维循环。"""
    from agent.mind.think_session import think_session

    mc = mind._get_mind_config()
    # adapter_key 优先使用调用方传入（按 scope 隔离），回退到共享状态（兼容旧路径）
    if not adapter_key:
        adapter_key = mind._resolve_adapter_key()
    scope = mind._resolve_entity_scope(anything) if anything else ""
    with think_session(mind, scope):
        # 会话开始清理历史中断信号，避免上一轮遗留请求误杀新会话
        _interrupts = getattr(mind, "interrupts", None)
        if scope and _interrupts is not None:
            _interrupts.clear(scope)
        active_tools = await mind.pfc.get_active_tool_schemas(adapter_key, scope=scope)
        base_messages = await mind.get_recollection(anything=anything)
        # 历史快照已覆盖该 scope 当前全部消息：消费到达时入队的待处理条目，
        # 避免快照内消息在周期结束后另起周期导致重复回复
        if anything:
            _consume_pending_for_scope(mind, anything)
        if images:
            base_messages = await apply_vision(mind, base_messages, images, anything)

        await think_loop(
            mind,
            mode=ThinkMode.REPLY,
            tool_chain=[],
            execution_steps=[],
            start_time=time.time(),
            safety_limit=mc.max_tool_iterations,
            collected_text=[],
            active_tools=active_tools,
            anything=anything,
            base_messages=base_messages,
            adapter_key=adapter_key,
        )


async def think_loop(
        mind: Mind,
        mode: ThinkMode,
        tool_chain: List[Dict],
        execution_steps: List[str],
        start_time: float,
        safety_limit: int,
        collected_text: List[str],
        active_tools: List[Dict],
        anything: Optional[Everything] = None,
        base_messages: Optional[List[Dict]] = None,
        options: Optional[Dict] = None,
        *,
        adapter_key: str = "",
) -> None:
    """统一思维循环：对话和反思共享同一流程。

    通过 mode 参数区分行为：
    - REPLY：处理用户消息，通过工具发送回复，写入对话历史
    - REFLECT：内省思考，收集文本输出到 collected_text，不发送消息

    base_messages 仅首轮获取，后续轮次复用缓存。
    工具集由调用方构建并传入，确保模式差异在入口处理。
    """
    from agent.mind.autonomous import MindPhase
    from agent.mind.guardrails import GuardrailController
    from agent.mind.tool_activation import ToolActivationManager

    mode_label = "反思" if mode == ThinkMode.REFLECT else "对话"
    # adapter_key 优先使用调用方传入（按 scope 隔离），回退到共享状态（兼容旧路径）
    if not adapter_key and mode == ThinkMode.REPLY:
        adapter_key = mind._resolve_adapter_key()
    if base_messages is None:
        if mode == ThinkMode.REPLY and anything:
            base_messages = await mind.get_recollection(anything=anything)
        else:
            base_messages = []

    iteration = 0
    consecutive_fake_calls = 0
    consecutive_empty_calls = 0
    consecutive_tool_errors = 0
    consecutive_security_leaks = 0
    consecutive_overflow_compressions = 0
    consecutive_monologues = 0
    end_reply_interceptions = 0
    max_output_recoveries = 0
    last_prompt_tokens = 0
    # 首次独白后升级为 API 级强制工具调用（tool_choice="required"）
    force_tool_choice = False
    # 纯工具模式：本 Agent 的合法动作（send_message/end_reply/工具）全部是工具调用，
    # 纯文本输出本就不该存在——LLM 调用默认强制工具选择（范式级约束，非事后拦截）
    mc = mind._get_mind_config()
    pure_tool_mode = bool(getattr(mc, "force_tool_use", True))
    # 后台任务等待：等待意图挂起的单次上限与本轮回复累计预算（秒）
    wait_per_round = float(getattr(mc, "background_wait_timeout", 30.0))
    wait_budget = float(getattr(mc, "background_wait_budget", 120.0))
    background = getattr(mind, "background_tasks", None)
    # 工具调用守卫：跟踪本次会话的调用历史，检测死循环
    guardrail = GuardrailController()
    # 工具结果加工管线：脱敏 → 扫描 → 守卫 → 截断（整轮预算在会话内累计）
    pipeline = ToolResultPipeline(mind, guardrail)
    # 会话期间 scope 不变，循环外解析一次
    current_scope = ToolActivationManager.current_scope()
    # 新消息并入基线：以历史快照水位（快照内最大 ts_ns）为起点，
    # 循环期间到达的用户消息（到达时已实时入库）将并入当前上下文，而非另起周期
    last_merged_ts = time.time_ns()
    if mode == ThinkMode.REPLY and anything:
        try:
            scope_type, scope_id = mind._resolve_scope(anything)
            watermark = mind.conversation_data.get_fetch_watermark(scope_type, scope_id)
            if watermark is not None:
                last_merged_ts = watermark
        except Exception as exc:
            log(f"快照水位获取失败，按当前时间并入: {exc}", "DEBUG", tag="思维")

    # 中断注册表（协作式刹车信号；替身 Mind 可能不具备，容忍缺省）
    interrupts = getattr(mind, "interrupts", None)

    # 流式过程事件：turn_id 标识本轮思维会话，增量事件供通道订阅（webui 流式渲染）
    turn_id = uuid.uuid4().hex[:8]
    _delta_accumulated = {"text": "", "reasoning": ""}

    async def delta_emitter(delta: str, reasoning: bool) -> None:
        key = "reasoning" if reasoning else "text"
        _delta_accumulated[key] += delta
        try:
            await event_bus.emit(EVENT_ASSISTANT_DELTA, {
                "scope": current_scope,
                "turn_id": turn_id,
                "delta": delta,
                "accumulated": _delta_accumulated[key],
                "reasoning": reasoning,
            })
        except Exception:
            pass  # 过程事件失败不影响主流程

    # 替身 Mind（测试/子代理）可能仍是非流式签名：探测后按需传参
    try:
        import inspect as _inspect
        _invoke_params = _inspect.signature(mind._invoke_llm_unified).parameters
        _supports_stream = "stream" in _invoke_params or any(
            p.kind == _inspect.Parameter.VAR_KEYWORD for p in _invoke_params.values()
        )
    except (TypeError, ValueError):
        _supports_stream = False

    # 工具集版本快照：每轮检查版本变化，变了就重建 active_tools（保持 prefix 缓存友好）
    from agent.mind.tool_activation import tool_activation as _tool_act_mgr
    _last_tools_version = (
        getattr(mind.pfc, "tools_version", 0),
        _tool_act_mgr.version,
    )

    while iteration < safety_limit:
        # 中断检查点（协作式）：用户/守卫请求中断时安全收束，
        # 不发半截消息、不写残缺工具链、历史留中断元消息
        if current_scope and interrupts is not None and interrupts.is_requested(current_scope):
            reason = interrupts.consume(current_scope) or "未说明"
            log(f"会话被中断 (轮次 {iteration + 1}): scope={current_scope} reason={reason}", tag="中断")
            execution_steps.append(f"→ 第{iteration + 1}轮前: 会话被中断 ({reason})")
            if mode == ThinkMode.REPLY and anything:
                await mind._add_system_context(
                    anything,
                    f"[系统] 本次回复在执行中被中断（{reason}），"
                    "未完成的操作已放弃，如需继续请重新发起。",
                    role="system",
                )
                await finish_think(mind, anything, execution_steps, iteration, tool_chain)
            return

        # 工具集版本检查：激活/发现变化时重建 active_tools（字节稳定时不重建，prefix 缓存友好）
        _cur_tools_version = (getattr(mind.pfc, "tools_version", 0), _tool_act_mgr.version)
        if _cur_tools_version != _last_tools_version:
            _last_tools_version = _cur_tools_version
            active_tools = await mind.pfc.get_active_tool_schemas(
                adapter_key, scope=current_scope,
            )
            log(f"工具集版本变化，重建 active_tools: {len(active_tools)} 个", "DEBUG", tag="思维")

        await event_bus.emit(EVENT_THINKING_REPLY_ROUND, {
            "iteration": iteration,
            "safety_limit": safety_limit,
            "elapsed": time.time() - start_time,
            "steps_so_far": len(execution_steps),
            "mode": mode.value,
        })

        # Microcompact：完整压缩前的轻量清理（旧只读工具结果 → 占位符）
        if mind.compressor is not None:
            mind.compressor.microcompact(tool_chain)

        # 上下文压缩：溢出风险（或手动请求）时压缩中间轮次
        if mind.compressor is not None and mind.compressor.should_compress(
            base_messages + tool_chain,
            last_prompt_tokens=last_prompt_tokens,
            scope=current_scope,
        ):
            async with mind.compressor.scope_lock(current_scope):
                base_messages, tool_chain = await _compress_context(
                    mind, base_messages, tool_chain, current_scope,
                )
            # 压缩后旧真用量已失真，清零避免下轮以过期值重复触发压缩
            last_prompt_tokens = 0
            execution_steps.append(f"→ 第{iteration + 1}轮前: 上下文已压缩")

        # 并入循环期间到达的新用户消息（让 AI 在当前回复中一并处理，
        # 而非另起周期导致上下文断裂/忘记已回复）
        if mode == ThinkMode.REPLY and anything:
            new_msgs = await _fetch_new_user_messages(mind, anything, last_merged_ts)
            if new_msgs:
                for m in new_msgs:
                    tool_chain.append({"role": "user", "content": m["content"]})
                last_merged_ts = new_msgs[-1]["ts_ns"]
                # 消费掉对应的待处理队列条目，避免该消息之后另起独立周期
                _consume_pending_for_scope(mind, anything)
                # 新消息携带的媒体：激活对应媒体工具并重建工具集供后续轮次使用
                # （媒体标签已随内容并入上下文，待处理媒体不留存到后续周期）
                try:
                    merged_images = mind.pfc.collect_images(scope=current_scope)
                    merged_media = mind.pfc.collect_media(scope=current_scope)
                except TypeError:
                    merged_images = mind.pfc.collect_images()
                    merged_media = mind.pfc.collect_media()
                if merged_images:
                    # 视觉模型直传图片 block；非视觉模型转存超大 base64 为文件路径
                    tool_chain = await apply_vision(mind, tool_chain, merged_images)
                if merged_images or merged_media:
                    mind.pfc.activate_media_tools(merged_images, merged_media)
                    active_tools = await mind.pfc.get_active_tool_schemas(
                        adapter_key, scope=mind._resolve_entity_scope(anything),
                    )
                log(f"并入 {len(new_msgs)} 条循环期间新消息到当前上下文", tag="思维")
                execution_steps.append(f"→ 第{iteration + 1}轮前: 并入 {len(new_msgs)} 条新消息")

        exec_context = mind.pfc.build_execution_context(
            execution_steps, start_time, iteration,
            adapter_key=adapter_key, safety_limit=safety_limit,
            anything=anything,
        )
        # 纯工具模式（或独白升级）且有可用工具时，API 级强制工具选择
        require_tools = bool(active_tools) and (pure_tool_mode or force_tool_choice)
        if require_tools:
            # 输出纪律提示与 API 级强制并行注入：强制 tool_choice 可能被
            # 端点静默忽略（如 MiniMax 仅支持 auto/none），提示词是
            # 跨端点可靠的兜底约束（每轮注入末尾位置）
            exec_context["content"] += "\n" + (
                _PROMPT_TOOL_OUTPUT_DISCIPLINE if mode == ThinkMode.REPLY
                else _PROMPT_REFLECT_OUTPUT_DISCIPLINE
            )
        # exec_context（每轮动态）置于末尾：保持 stable/context/volatile/历史前缀
        # 字节稳定供 Prompt Caching 复用，且当前轮状态在模型注意力最强的末尾位置
        llm_messages = base_messages + tool_chain + [exec_context]

        mind._set_phase(MindPhase.LLM_CALLING)
        try:
            _stream_kwargs = (
                {"stream": _streaming_enabled(), "on_delta": delta_emitter}
                if _supports_stream else {}
            )
            result = await mind._invoke_llm_unified(
                llm_messages, active_tools or None, anything,
                tool_choice="required" if require_tools else None,
                options=options,
                **_stream_kwargs,
            )
        except asyncio.TimeoutError:
            timeout_val = mind._get_mind_config().llm_timeout
            log(f"LLM 调用超时 ({timeout_val}s)，注入恢复提示继续循环", "WARNING", tag="思维")
            execution_steps.append(f"→ 第{iteration + 1}轮: LLM 调用超时 ({timeout_val}s)")
            tool_chain.append({
                "role": "system",
                "content": _PROMPT_TIMEOUT.format(timeout=timeout_val),
            })
            iteration += 1
            continue
        except Exception as exc:
            # 上下文超限：立即压缩后重试（连续压缩无效时放弃，防止死循环）
            from agent.llm.resilience import classify_llm_error
            classified = classify_llm_error(exc)
            if (
                classified.should_compress
                and mind.compressor is not None
                and consecutive_overflow_compressions < 2
            ):
                consecutive_overflow_compressions += 1
                log(
                    f"LLM 上下文超限，执行紧急压缩 (第 {consecutive_overflow_compressions} 次)",
                    "WARNING", tag="压缩",
                )
                async with mind.compressor.scope_lock(current_scope):
                    base_messages, tool_chain = await _compress_context(
                        mind, base_messages, tool_chain, current_scope,
                    )
                # 紧急压缩后旧真用量已失真，清零防止下轮误判再次溢出
                last_prompt_tokens = 0
                execution_steps.append(f"→ 第{iteration + 1}轮: 上下文超限，已紧急压缩")
                iteration += 1
                continue
            raise

        consecutive_overflow_compressions = 0
        if result.usage and result.usage.prompt_tokens:
            last_prompt_tokens = result.usage.prompt_tokens

        # 上下文用量快照（usage 锚定：API 真实用量优先；供 webui 状态栏显示）
        if mind.compressor is not None:
            try:
                _tokens = last_prompt_tokens or mind.compressor.estimate_tokens(
                    base_messages + tool_chain)
                _threshold = mind.compressor.threshold_tokens()
                _window = mind.get_model_context_length()
                if _threshold > 0:
                    await event_bus.emit(EVENT_CONTEXT_USAGE, {
                        "scope": current_scope,
                        "tokens": _tokens,
                        "threshold": _threshold,
                        "window": _window,
                        "percent": round(_tokens / _threshold * 100, 1),
                    })
            except Exception:
                pass  # 状态事件失败不影响主流程

        # max_output_tokens 截断恢复：输出被长度截断时续写（对齐 Claude Code 两级恢复，
        # Anelf 端点上限自适应已在 LLMClient 处理，这里做注入续写层，最多 3 次）。
        # 截断轮的 tool_calls 参数可能不完整（JSON 断裂），一律丢弃不执行。
        if getattr(result, "finish_reason", "") == "length" \
                and max_output_recoveries < _MAX_OUTPUT_RECOVERY_LIMIT:
            max_output_recoveries += 1
            log(f"输出被 max_tokens 截断，注入续写提示 (第 {max_output_recoveries} 次)",
                "WARNING", tag="思维")
            partial_text = _strip_think_blocks(result.content or "").strip()
            if partial_text or result.tool_calls:
                truncated_msg: Dict[str, Any] = {"role": "assistant", "content": partial_text}
                preserve_reasoning_fields(truncated_msg, result)
                tool_chain.append(truncated_msg)
            tool_chain.append({"role": "system", "content": _PROMPT_MAX_OUTPUT_CONTINUE})
            execution_steps.append(f"→ 第{iteration + 1}轮: 输出截断，已注入续写提示")
            iteration += 1
            continue

        # 恢复次数耗尽时同样跳过本轮 tool_calls（参数可能不完整，执行会出错）
        if getattr(result, "finish_reason", "") == "length" \
                and max_output_recoveries >= _MAX_OUTPUT_RECOVERY_LIMIT:
            log("输出截断恢复次数耗尽，跳过本轮 tool_calls 并结束", "WARNING", tag="思维")
            partial_text = _strip_think_blocks(result.content or "").strip()
            if partial_text:
                truncated_msg = {"role": "assistant", "content": partial_text}
                preserve_reasoning_fields(truncated_msg, result)
                tool_chain.append(truncated_msg)
            tool_chain.append({
                "role": "system",
                "content": "[系统] 输出多次被截断，本轮工具调用参数可能不完整，已跳过执行。"
                           "请拆分为更小的步骤或调用 end_reply 结束。",
            })
            execution_steps.append(f"→ 第{iteration + 1}轮: 截断恢复耗尽，跳过 tool_calls")
            iteration += 1
            continue

        tool_calls = resolve_tool_calls(result)

        # 安全检测：AI 输出复述了会话令牌 → SECURITY 停止，注入纠正提示重试
        if _detect_token_leak(result, tool_calls):
            consecutive_security_leaks += 1
            log(
                f"检测到会话令牌泄露 (轮次 {iteration + 1}, 连续 {consecutive_security_leaks} 次)",
                "WARNING", tag="安全",
            )
            if consecutive_security_leaks >= 2:
                log("连续令牌泄露，强制结束本轮", "WARNING", tag="安全")
                execution_steps.append(f"→ 第{iteration + 1}轮: 连续安全泄露，强制结束")
                if mode == ThinkMode.REPLY and anything:
                    await finish_think(mind, anything, execution_steps, iteration + 1, tool_chain)
                return
            tool_chain.append({"role": "system", "content": _PROMPT_SECURITY_LEAK})
            execution_steps.append(f"→ 第{iteration + 1}轮: 安全泄露已拦截并纠正")
            iteration += 1
            continue
        consecutive_security_leaks = 0

        if not tool_calls:
            raw_text = _strip_think_blocks(result.content or "").strip()
            is_fake = bool(
                raw_text
                and (
                    raw_text.startswith("[工具执行记录]")
                    or raw_text.startswith("[已执行操作摘要]")
                    or raw_text.startswith("call_function")
                    or ('"success"' in raw_text[:200] and '"action"' in raw_text[:500])
                )
            )

            if is_fake:
                consecutive_fake_calls += 1
                log(
                    f"过滤假工具执行记录 (轮次 {iteration + 1}, "
                    f"连续 {consecutive_fake_calls} 次)",
                    "WARNING", tag="思维",
                )
                await event_bus.emit(
                    EVENT_THINKING_FAKE_TOOL_CALL, {
                        "iteration": iteration + 1,
                        "consecutive": consecutive_fake_calls,
                        "content_preview": raw_text[:200],
                    },
                )
                if consecutive_fake_calls >= 2:
                    log("连续假工具调用过多，强制结束本轮", "WARNING", tag="思维")
                    execution_steps.append(
                        f"→ 第{iteration + 1}轮: 连续假工具调用 {consecutive_fake_calls} 次，强制结束"
                    )
                    if mode == ThinkMode.REPLY and anything:
                        await finish_think(mind, anything, execution_steps, iteration + 1, tool_chain)
                    return

                assistant_msg: Dict[str, Any] = {"role": "assistant", "content": raw_text}
                preserve_reasoning_fields(assistant_msg, result)
                tool_chain.append(assistant_msg)
                tool_chain.append({
                    "role": "system",
                    "content": _PROMPT_FAKE_TOOL_CALL,
                })
                execution_steps.append(f"→ 第{iteration + 1}轮: 假工具调用已拦截并纠正")
            elif raw_text:
                consecutive_fake_calls = 0
                consecutive_empty_calls = 0
                assistant_msg = {"role": "assistant", "content": raw_text}
                preserve_reasoning_fields(assistant_msg, result)
                tool_chain.append(assistant_msg)
                collected_text.append(raw_text)

                running_bg: List[BackgroundTaskInfo] = []
                if mode == ThinkMode.REPLY and anything and background is not None:
                    running_bg = background.running(current_scope)

                if running_bg and wait_budget > 0:
                    # 等待挂起：后台任务运行中时的纯文本一律视为等待——挂起会合
                    # 而非计入独白熔断（结构性判定，不解析文本语义）。
                    # 挂起期间新消息照常实时入库，中断/新消息/完成/超时都会安全唤醒；
                    # 超时说明等待无望，清零预算，后续纯文本回落到普通独白路径。
                    reason, completions, elapsed = await _suspend_for_background(
                        mind, anything, background, current_scope,
                        last_merged_ts, min(wait_per_round, wait_budget), interrupts,
                    )
                    execution_steps.append(
                        f"→ 第{iteration + 1}轮: 等待后台任务（{reason}，{elapsed:.0f}s）"
                    )
                    if reason == "completed":
                        wait_budget -= elapsed
                        tool_chain.append({
                            "role": "system",
                            "content": _format_task_completions(
                                completions, background.running(current_scope),
                            ),
                        })
                    elif reason == "timeout":
                        wait_budget = 0.0
                        tool_chain.append({
                            "role": "system",
                            "content": _PROMPT_TASKS_STILL_RUNNING.format(
                                tasks=_format_running_tasks(running_bg),
                            ),
                        })
                    # interrupted：不追加提示，循环顶部统一并入新消息 / 处理中断
                    iteration += 1
                    continue

                if mode == ThinkMode.REPLY and anything:
                    # 内心独白计数：连续只说不做达到上限时强制结束（防叙事模式死循环）
                    consecutive_monologues += 1
                    log(
                        f"内心独白 (连续 {consecutive_monologues} 次): {raw_text[:100]}",
                        "DEBUG", tag="思维",
                    )
                    # 仅首次独白写入历史（assistant 角色）；
                    # 重复独白不入库，避免历史中的独白模式被模型模仿强化
                    if consecutive_monologues == 1:
                        await save_ai_thought(mind, anything, raw_text)
                    if consecutive_monologues >= _MAX_CONSECUTIVE_MONOLOGUES:
                        log(
                            f"连续内心独白 {consecutive_monologues} 次（只说不做），强制结束本轮",
                            "WARNING", tag="思维",
                        )
                        execution_steps.append(
                            f"→ 第{iteration + 1}轮: 连续内心独白 {consecutive_monologues} 次，强制结束"
                        )
                        await finish_think(mind, anything, execution_steps, iteration + 1, tool_chain)
                        return
                    # 首次独白后升级：下一轮 API 级强制工具调用（不靠劝）
                    force_tool_choice = True
                    feedback = (
                        _PROMPT_INNER_MONOLOGUE_STRICT
                        if consecutive_monologues >= 2
                        else _PROMPT_INNER_MONOLOGUE
                    )
                    if running_bg:
                        # 情境化：指出后台任务的正确查询/等待路径，而非只堵不疏
                        feedback += _PROMPT_MONOLOGUE_TASKS_HINT.format(
                            count=len(running_bg),
                            tasks=_format_running_tasks(running_bg),
                        )
                else:
                    # 反思模式：连续纯文本达到上限即收束（产出已累积在 collected_text）
                    consecutive_monologues += 1
                    if consecutive_monologues >= _MAX_CONSECUTIVE_MONOLOGUES:
                        log(
                            f"反思连续纯文本 {consecutive_monologues} 次，结束本轮反思",
                            "WARNING", tag="思维",
                        )
                        execution_steps.append(
                            f"→ 第{iteration + 1}轮: 反思连续纯文本 {consecutive_monologues} 次，结束"
                        )
                        return
                    feedback = _PROMPT_CONTINUE
                # 反馈消息追加在 assistant 之后，保证下一轮上下文不以 assistant 结尾，
                # 避免违反 OpenAI/Anthropic 的消息交替规范，防止连续 assistant 消息。
                # （发送边界统一归一为 user 角色，确保 anthropic 端点位置正确）
                tool_chain.append({"role": "system", "content": feedback})
                execution_steps.append(f"→ 第{iteration + 1}轮: {mode_label}中")
            else:
                consecutive_fake_calls = 0
                consecutive_empty_calls += 1
                if result.reasoning_content:
                    assistant_msg = {"role": "assistant", "content": ""}
                    preserve_reasoning_fields(assistant_msg, result)
                    tool_chain.append(assistant_msg)
                execution_steps.append(f"→ 第{iteration + 1}轮: 空输出（思考中）")
                log(f"空输出，继续循环 (轮次 {iteration + 1}, 连续 {consecutive_empty_calls} 次)", "DEBUG", tag="思维")
                if consecutive_empty_calls >= 2:
                    log(f"连续空输出 {consecutive_empty_calls} 次，强制结束本轮", "WARNING", tag="思维")
                    execution_steps.append(f"→ 第{iteration + 1}轮: 连续空输出 {consecutive_empty_calls} 次，强制结束")
                    if mode == ThinkMode.REPLY and anything:
                        await finish_think(mind, anything, execution_steps, iteration + 1, tool_chain)
                    return
                tool_chain.append({
                    "role": "system",
                    "content": _PROMPT_EMPTY_OUTPUT,
                })

            iteration += 1
            continue

        # 有工具调用
        mind._set_phase(MindPhase.TOOL_EXECUTING)
        consecutive_fake_calls = 0
        consecutive_empty_calls = 0
        consecutive_monologues = 0
        force_tool_choice = False
        await execute_tool_calls(
            mind, tool_chain, result, tool_calls, iteration, anything,
            guardrail=guardrail, pipeline=pipeline,
        )

        # 记录目标工具使用（goal nag 提醒的计数依据）
        try:
            from agent.planning.nag import note_tools_used
            note_tools_used(current_scope, [tc.name for tc in tool_calls])
        except Exception:
            pass

        # 守卫 halt：同工具连续失败达到上限，强制结束本轮
        if guardrail.halt_decision is not None:
            halt = guardrail.halt_decision
            log(f"工具守卫强制结束: {halt.message}", "WARNING", tag="思维")
            execution_steps.append(f"→ 第{iteration + 1}轮: {halt.message}")
            if mode == ThinkMode.REPLY and anything:
                await finish_think(mind, anything, execution_steps, iteration + 1, tool_chain)
            return

        # 检测本轮工具结果是否全部为错误
        all_errors = _check_tool_results_all_errors(tool_chain, tool_calls)
        if all_errors:
            consecutive_tool_errors += 1
            log(
                f"全部工具调用返回错误 (轮次 {iteration + 1}, "
                f"连续 {consecutive_tool_errors} 次)",
                "WARNING", tag="思维",
            )
        else:
            consecutive_tool_errors = 0

        for tc in tool_calls:
            mind.pfc.record_tool_use(tc.name)
        mind.pfc.expand_discovered_tools(tool_calls)

        tool_names = ", ".join(tc.name for tc in tool_calls)
        execution_steps.append(f"→ 第{iteration + 1}轮: 调用工具 [{tool_names}]")

        if consecutive_tool_errors >= 3:
            log(
                f"连续 {consecutive_tool_errors} 轮工具全部报错，强制结束本轮",
                "WARNING", tag="思维",
            )
            execution_steps.append(
                f"→ 第{iteration + 1}轮: 连续工具错误 {consecutive_tool_errors} 次，强制结束"
            )
            if mode == ThinkMode.REPLY and anything:
                await finish_think(mind, anything, execution_steps, iteration + 1, tool_chain)
            return

        # 连续错误达到阈值时注入警告
        if consecutive_tool_errors >= 2:
            tool_chain.append({
                "role": "system",
                "content": _PROMPT_TOOL_ERROR_ESCALATION,
            })

        if should_end_reply(tool_calls, tool_chain):
            # 结束拦截：本轮存在失败工具、或文字声明了动作却未实际发起调用时，
            # 注入反馈给 AI 修正机会（两类合计最多 2 次防死循环）
            if mode == ThinkMode.REPLY and end_reply_interceptions < 2:
                feedback = _collect_round_failures(tool_chain, tool_calls) \
                    or _collect_premature_end(result, tool_calls)
                if feedback:
                    end_reply_interceptions += 1
                    log(
                        f"结束请求被拦截: 存在未完成操作 (轮次 {iteration + 1}, "
                        f"第 {end_reply_interceptions} 次拦截)",
                        "WARNING", tag="思维",
                    )
                    tool_chain.append({"role": "system", "content": feedback})
                    execution_steps.append(
                        f"→ 第{iteration + 1}轮: 结束被拦截（存在未完成操作），已反馈 AI 修正"
                    )
                    iteration += 1
                    continue
            log(f"AI 主动结束{mode_label} (轮次 {iteration + 1})", tag="思维")
            if mode == ThinkMode.REPLY and anything:
                await finish_think(mind, anything, execution_steps, iteration + 1, tool_chain)
            return

        iteration += 1

    # 达到安全上限
    log(f"达到安全上限 ({safety_limit} 轮)，强制结束", "WARNING", tag="思维")
    if mode == ThinkMode.REPLY and anything:
        await finish_think(mind, anything, execution_steps, safety_limit, tool_chain)


# ==================================================================
# 思维循环辅助方法
# ==================================================================

def should_end_reply(tool_calls: List[ToolCall], tool_chain: List[Dict]) -> bool:
    """检测本轮是否应结束：AI 调用了 end_reply。"""
    return any(tc.name == _END_REPLY_TOOL_NAME for tc in tool_calls)


_THINK_BLOCK_RE = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.DOTALL | re.IGNORECASE)


def _strip_think_blocks(text: str) -> str:
    """剥离 content 中内联的 <think>/<thinking> 推理块（参考 hermes）。

    推理内容应只走 reasoning_content 独立字段，内联 think 块若留在 content
    会泄漏到对话记录与频道消息中，并膨胀上下文。
    """
    if not text or "<think" not in text.lower():
        return text
    return _THINK_BLOCK_RE.sub("", text).strip()


def resolve_tool_calls(result: ChatResult) -> List[ToolCall]:
    """从 LLM 回复中提取工具调用。"""
    if result.tool_calls:
        log(
            f"原生工具调用 {len(result.tool_calls)} 个: "
            f"{', '.join(tc.name for tc in result.tool_calls)}",
            tag="思维",
        )
        return result.tool_calls
    return []


def _detect_token_leak(result: ChatResult, tool_calls: List[ToolCall]) -> bool:
    """检测 AI 输出（文本或工具调用参数）是否复述了会话令牌。"""
    from agent.security.session_token import detect_leak
    if result.content and detect_leak(result.content):
        return True
    for tc in tool_calls:
        if tc.arguments and detect_leak(tc.arguments):
            return True
    return False


def _parse_tool_result_json(text: str) -> Optional[Any]:
    """宽松解析工具结果 JSON。

    结果经加工管线后可能带威胁扫描前缀（[安全警告] ...\\n）或
    守卫警告后缀（\\n\\n[工具守卫警告: ...]），整体 json.loads 会失败；
    此处定位首个 '{' 起解析首个完整 JSON 值，容忍前后附加文本。
    """
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    start = text.find("{")
    if start < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text, start)
        return obj
    except (json.JSONDecodeError, ValueError):
        return None


def _check_tool_results_all_errors(
        tool_chain: List[Dict],
        tool_calls: List[ToolCall],
) -> bool:
    """检测最近一批工具调用结果是否全部为错误。

    从 tool_chain 末尾查找与本轮 tool_calls 对应的 role=tool 消息，
    判断每条结果是否包含 error 关键信号。全部为错误时返回 True。
    """
    tc_ids = {tc.id for tc in tool_calls}
    if not tc_ids:
        return False

    results: List[str] = []
    for msg in reversed(tool_chain):
        if msg.get("role") != "tool":
            break
        if msg.get("tool_call_id") in tc_ids:
            content = msg.get("content", "")
            results.append(content if isinstance(content, str) else "")

    if not results:
        return False

    for r in results:
        parsed = _parse_tool_result_json(r)
        if not isinstance(parsed, dict):
            # 非 JSON dict 内容视为非错误（纯文本结果）
            return False
        # 有 error 键 → 错误，继续检查下一个
        if "error" in parsed:
            continue
        # success=false / ok=false → 错误，继续检查下一个
        if parsed.get("success") is False or parsed.get("ok") is False:
            continue
        # 无错误信号，至少一个成功
        return False
    # 全部都是错误结果
    return True


def _extract_error_text(payload: Any) -> str:
    """从工具结果 payload（dict 或 JSON 字符串）中提取错误文本，无错误返回空串。"""
    if isinstance(payload, str):
        payload = _parse_tool_result_json(payload)
    if not isinstance(payload, dict):
        return ""
    if payload.get("success") is False or payload.get("ok") is False:
        return str(payload.get("error", "") or "未知错误")
    if payload.get("error"):
        return str(payload["error"])
    return ""


def _collect_round_failures(tool_chain: List[Dict], tool_calls: List[ToolCall]) -> str:
    """收集本轮工具结果中的失败项，生成结束拦截反馈。无失败时返回空串。"""
    tc_ids = {tc.id for tc in tool_calls}
    if not tc_ids:
        return ""
    tc_names = {tc.id: tc.name for tc in tool_calls}

    failures: List[str] = []
    for msg in reversed(tool_chain):
        if msg.get("role") != "tool":
            break
        tc_id = msg.get("tool_call_id")
        if tc_id not in tc_ids:
            continue
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue
        parsed = _parse_tool_result_json(content)
        if not isinstance(parsed, dict):
            continue

        err = _extract_error_text(parsed)
        if err:
            failures.append(f"{tc_names.get(tc_id, '?')}: {err}")

    if not failures:
        return ""
    lines = "\n".join(f"- {f}" for f in failures)
    return _PROMPT_END_BLOCKED_FAILURE.format(failures=lines)


def _collect_premature_end(result: ChatResult, tool_calls: List[ToolCall]) -> str:
    """检测"只说不做就结束"：end_reply 是本轮唯一工具调用，且附带大段不可见文本。

    纯工具模式下文本对用户不可见，唯一动作就是结束——大段文字几乎必然是
    「用文字描述计划/承诺代替实际工具调用」（说要做就必须在同一响应里调用，
    参考 hermes intent-ack，但用结构性信号判定，不解析文本语义）。
    正常收尾（send_message + end_reply、工作工具 + end_reply、简短/无文本）不拦截。

    返回拦截反馈文本；非提前结束时返回空串。
    """
    if any(tc.name != _END_REPLY_TOOL_NAME for tc in tool_calls):
        return ""
    text = _strip_think_blocks(result.content or "").strip()
    if len(text) < _PREMATURE_END_MIN_TEXT:
        return ""
    return _PROMPT_END_BLOCKED_PREMATURE


def _streaming_enabled() -> bool:
    """流式内核开关（配置 mind_streaming_enabled，默认开）。

    流式只产生过程事件（assistant_delta），回复出口仍是
    send_message/end_reply —— 多频道语义不受影响。
    """
    try:
        from core.config import get_config_bool
        return get_config_bool("mind_streaming_enabled", True)
    except Exception:
        return True


# 并行执行上限（对齐 Claude Code CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY 默认 10）
_MAX_TOOL_CONCURRENCY = 10


def _partition_tool_calls(tool_calls: List[ToolCall]) -> List[tuple]:
    """按并发安全性把工具调用切分为连续批次（对齐 Claude Code toolOrchestration）。

    连续的并发安全调用组成并行批次，其余各自串行。
    安全判定 fail-closed：查询失败一律视为不安全。
    """
    from core.entity import EntityRegistry

    def _is_safe(tc: ToolCall) -> bool:
        try:
            entity = EntityRegistry.get(tc.name)
            return bool(entity and entity.meta.get("concurrency_safe"))
        except Exception:
            return False

    partitions: List[tuple] = []
    for tc in tool_calls:
        safe = _is_safe(tc)
        if safe and partitions and partitions[-1][0]:
            partitions[-1][1].append(tc)
        else:
            partitions.append((safe, [tc]))
    return partitions


async def execute_tool_calls(
        mind: Mind,
        tool_chain: List[Dict],
        result: ChatResult,
        tool_calls: List[ToolCall],
        iteration: int,
        anything: Optional[Everything] = None,
        *,
        guardrail: Optional["GuardrailController"] = None,
        pipeline: Optional["ToolResultPipeline"] = None,
) -> None:
    """执行工具调用并将 assistant + tool 消息追加到 tool_chain。

    保留 content 和推理字段以维持多轮思维链连续性。
    实际发送内容由工具（如 send_message）的 _record_to_context 负责写入 DB。
    结果加工（脱敏/扫描/守卫/截断）由 ToolResultPipeline 统一处理。
    """
    from agent.mind.guardrails import synthetic_block_result

    if pipeline is None:
        pipeline = ToolResultPipeline(mind, guardrail)
    pipeline.begin_turn()

    assistant_msg: Dict[str, Any] = {
        "role": "assistant",
        "content": _strip_think_blocks(result.content or ""),
        "tool_calls": [tc.raw for tc in tool_calls],
    }
    preserve_reasoning_fields(assistant_msg, result)
    tool_chain.append(assistant_msg)

    # 守卫执行前检查：已知必败/无进展的调用直接返回合成结果，不执行真实工具
    blocked_results: Dict[str, str] = {}
    if guardrail is not None:
        for tc in tool_calls:
            decision = guardrail.before_call(tc.name, tc.arguments or "")
            if decision.should_block:
                blocked_results[tc.id] = synthetic_block_result(decision)
                log(f"工具守卫拦截: {tc.name} ({decision.reason})", "WARNING", tag="思维")

    async def _run_one(tc: ToolCall) -> str:
        if tc.id in blocked_results:
            return blocked_results[tc.id]
        return await execute_one_tool(mind, tc, iteration, anything)

    # 并发安全分级（对齐 Claude Code）：连续只读调用并行（上限 10），写操作严格串行。
    # 无论哪条路径，tool 消息都按 tool_calls 原始顺序追加，保证配对完整。
    semaphore = asyncio.Semaphore(_MAX_TOOL_CONCURRENCY)

    async def _run_guarded(tc: ToolCall):
        async with semaphore:
            try:
                return await _run_one(tc)
            except asyncio.CancelledError:
                raise
            except BaseException as e:
                return e

    for is_parallel, batch in _partition_tool_calls(tool_calls):
        if is_parallel and len(batch) > 1:
            outputs = await asyncio.gather(*[_run_guarded(tc) for tc in batch])
        else:
            outputs = [await _run_guarded(tc) for tc in batch]
        for tc, output in zip(batch, outputs):
            if isinstance(output, BaseException):
                output = json.dumps({"error": str(output)}, ensure_ascii=False)
            output_str = output if isinstance(output, str) else str(output)
            try:
                final_output = pipeline.process(
                    tc.name, tc.arguments or "", output_str,
                    skip_guardrail=tc.id in blocked_results,
                )
            except Exception as e:
                # 配对铁律：结果加工失败也要保证 tool 消息落链
                final_output = json.dumps({"error": f"结果加工异常: {e}"}, ensure_ascii=False)
            tool_chain.append({"role": "tool", "tool_call_id": tc.id, "content": final_output})
            # 多模态工具结果：候选图片注入上下文，让视觉模型直接看到（如表情包检索）
            try:
                await _append_multimodal_result(mind, tool_chain, final_output)
            except Exception as exc:
                log(f"多模态工具结果展开失败（不影响主流程）: {exc}", "DEBUG", tag="思维")
    log_tool_round(iteration, tool_calls)


# 单个工具结果允许附带的最大图片数（防上下文膨胀）
_MAX_TOOL_RESULT_IMAGES = 6


async def _append_multimodal_result(
        mind: Mind,
        tool_chain: List[Dict],
        output: str,
) -> None:
    """展开多模态工具结果约定，把候选图片以 user 消息注入上下文。

    工具返回 JSON 含 ``{"_multimodal": true, "text": ..., "images": [路径...]}``
    时（如 search_sticker / search_image / find_similar_image），将图片加载
    压缩后以 image_url block 注入，视觉模型即可"亲眼看到"候选再做选择
    （借鉴 nekro-agent MULTIMODAL_AGENT 的检索体验）。非视觉模型跳过，
    文本摘要（text/results 字段）已随 tool 消息提供全部信息。
    """
    if '"_multimodal"' not in output:
        return
    parsed = _parse_tool_result_json(output)
    if not isinstance(parsed, dict) or not parsed.get("_multimodal"):
        return
    images = [p for p in (parsed.get("images") or []) if isinstance(p, str) and p]
    if not images:
        return
    config = getattr(getattr(mind, "llm", None), "config", None)
    if config is None or not getattr(config, "supports_vision", False):
        return

    from agent.llm.image_utils import ensure_base64, load_image_from_path
    from agent.llm.types import ImageContent

    candidates: List[ImageContent] = []
    for path in images[:_MAX_TOOL_RESULT_IMAGES]:
        try:
            candidates.append(load_image_from_path(path))
        except Exception:
            continue
    if not candidates:
        return
    prepared = await ensure_base64(candidates)
    if not prepared:
        return

    text = parsed.get("text") or "[系统] 上方工具返回了候选图片，请查看后继续。"
    blocks: List[Dict] = [{"type": "text", "text": text}]
    blocks.extend(img.to_openai_block(flat_url=config.use_flat_image_url) for img in prepared)
    tool_chain.append({"role": "user", "content": blocks})
    log(f"多模态工具结果: 注入 {len(prepared)} 张候选图片", "DEBUG", tag="思维")


async def execute_one_tool(
        mind: Mind,
        tc: ToolCall,
        iteration: int,
        anything: Optional[Everything] = None,
) -> str:
    """执行单个工具调用。"""
    from agent.mind.autonomous import MindPhase

    mind._set_phase(MindPhase.TOOL_EXECUTING)
    await event_bus.emit(EVENT_TOOL_EXECUTED, {"tool": tc.name, "iteration": iteration})
    await event_bus.emit(EVENT_THINKING_TOOL_START, {
        "tool_name": tc.name,
        "tool_id": tc.id,
        "arguments_preview": tc.arguments[:300] if tc.arguments else "",
        "iteration": iteration,
    })
    log(f"执行工具: {tc.name}", tag="思维")

    # ------------------------------------------------------------------
    # 批准机制：在执行前检查是否需要人工批准
    # ------------------------------------------------------------------
    if anything is not None:
        try:
            from agent.approval import ApprovalDecision, get_approval_gate

            gate = get_approval_gate()
            # 从 anything 提取上下文
            adapter_key = getattr(anything, "adapter_key", "") or "unknown"
            user_id = str(getattr(anything, "uid", "") or getattr(anything, "user_id", "") or "unknown")
            group_id = str(getattr(anything, "group_id", "") or "")
            chat_id = group_id if group_id not in ("", "0") else user_id

            # 获取频道实例
            from agent.channel.manager import get_channel_manager
            channel = get_channel_manager().get(adapter_key)

            if channel:
                # 解析工具参数
                try:
                    tool_args = json.loads(tc.arguments) if tc.arguments else {}
                except (json.JSONDecodeError, TypeError):
                    tool_args = {"_raw": tc.arguments or ""}

                decision = await gate.request_approval(
                    tool_name=tc.name,
                    tool_args=tool_args,
                    reason=f"AI 请求调用工具 {tc.name}",
                    channel=channel,
                    chat_id=chat_id,
                    user_id=user_id,
                )
                if decision != ApprovalDecision.APPROVED:
                    log(
                        f"工具 {tc.name} 未获批准: {decision.value}",
                        "WARNING",
                        tag="批准",
                    )
                    return json.dumps({
                        "error": f"工具调用未获批准: {decision.value}。"
                                 "用户已通过频道收到拒绝原因；请勿重试相同的调用，"
                                 "可向用户说明情况或改用其他方式完成任务。",
                        "approval_decision": decision.value,
                    }, ensure_ascii=False)
        except ImportError:
            # approval 模块未安装，跳过
            pass
        except Exception as exc:
            log(f"批准机制异常（继续执行）: {exc}", "WARNING", tag="批准")

    t0 = time.time()
    try:
        result = await mind.tool_executor(tc)  # type: ignore[misc]
        elapsed_ms = (time.time() - t0) * 1000
        await event_bus.emit(EVENT_THINKING_TOOL_END, {
            "tool_name": tc.name,
            "tool_id": tc.id,
            "duration_ms": round(elapsed_ms),
            "result_preview": result[:300] if result else "",
            "success": True,
        })
        return result
    except Exception as exc:
        elapsed_ms = (time.time() - t0) * 1000
        await event_bus.emit(EVENT_THINKING_TOOL_END, {
            "tool_name": tc.name,
            "tool_id": tc.id,
            "duration_ms": round(elapsed_ms),
            "error": str(exc),
            "success": False,
        })
        log(f"工具 {tc.name} 执行失败: {exc}", "WARNING", tag="思维")
        if mind.memory_store:
            try:
                await mind.memory_store.record_tool_error(
                    tool_name=tc.name,
                    error_type=type(exc).__name__,
                    error_msg=str(exc),
                    args_json=(tc.arguments or "")[:500],
                )
            except Exception:
                pass
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)


def preserve_reasoning_fields(msg: Dict[str, Any], result: ChatResult) -> None:
    """从 ChatResult.raw 中提取推理字段到 assistant 消息，维持多轮思维链。

    litellm 统一返回 OpenAI 格式，按协议覆盖两种载体：
    - reasoning_details：OpenRouter 风格，litellm 请求侧原样回传
    - thinking_blocks：Anthropic 协议 thinking 块（含 signature/redacted），
      litellm 请求侧据此重构 thinking 块（交错思考 + tool_use 场景必需）
    均以响应实际存在为条件，不返回推理字段的模型行为不变。
    """
    if not result.raw or not result.reasoning_content:
        return
    try:
        choices = result.raw.get("choices")
        if not choices or not isinstance(choices, list):
            return
        message = choices[0].get("message", {})
        if not isinstance(message, dict):
            return
        rd = message.get("reasoning_details")
        if rd:
            msg["reasoning_details"] = rd
        tb = message.get("thinking_blocks")
        if tb:
            msg["thinking_blocks"] = tb
    except (IndexError, AttributeError, TypeError):
        pass


async def save_ai_thought(mind: Mind, anything: Optional[Everything], text: str) -> None:
    """保存 AI 内心独白到对话历史。

    以 role="assistant" 写入——独白是 AI 自己的输出，用 user 角色会让模型
    误以为独白是用户发来的对话模式并加以模仿（模式强化死循环的根源）。
    末尾 assistant 残留由 build_llm_context 的 prefill 修正统一处理。
    """
    if not anything or not text:
        return
    tagged = f"[内心独白] {text}"
    await mind._add_system_context(anything, tagged, role="assistant")


def _summarize_tool_result_for_log(call_sig: str, result: str) -> str:
    """生成操作摘要中的工具结果预览。

    send_message 的结果 JSON 含完整回复 content（已记录为 assistant），
    此处只保留发送状态，避免与 assistant 记录重复。
    """
    if call_sig.startswith("send_message"):
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict):
                ok = parsed.get("success") is not False
                target = parsed.get("target_id", "")
                return f"{'已发送' if ok else '发送失败'}" + (f" -> {target}" if target else "")
        except (json.JSONDecodeError, TypeError):
            pass
        return "已发送"
    return result[:200]


async def finish_think(
        mind: Mind,
        anything: Everything,
        execution_steps: List[str],
        iterations: int,
        tool_chain: Optional[List[Dict]] = None,
) -> None:
    """思维循环结束处理：工具结果持久化 + 执行摘要写入短期记忆。"""
    if tool_chain:
        call_map: Dict[str, str] = {}  # tool_call_id → "name(args_preview)"
        for msg in tool_chain:
            if msg.get("role") == "assistant":
                for tc in msg.get("tool_calls") or []:
                    tc_id = tc.get("id", "")
                    fn = tc.get("function", {})
                    name = fn.get("name", "?")
                    args_raw = fn.get("arguments", "") or ""
                    try:
                        args_obj = json.loads(args_raw)
                        # send_message 的 content 是 AI 回复本体（已记录为 assistant），
                        # 摘要中剔除避免与 assistant 记录重复
                        if name == "send_message":
                            args_obj = {k: v for k, v in args_obj.items() if k != "content"}
                        args_preview = ", ".join(
                            f"{k}={v}" for k, v in args_obj.items()
                        )
                    except Exception:
                        args_preview = args_raw
                    call_map[tc_id] = f"{name}({args_preview})"

        result_lines: List[str] = []
        tool_idx = 0
        for msg in tool_chain:
            if msg.get("role") == "tool":
                tool_idx += 1
                tc_id = msg.get("tool_call_id", "")
                call_sig = call_map.get(tc_id, f"tool#{tool_idx}")
                result = _summarize_tool_result_for_log(
                    call_map.get(tc_id, ""), msg.get("content") or "",
                )
                result_lines.append(f"  #{tool_idx} {call_sig} → {result}")

        if result_lines:
            # 工具执行记录持久化到对话历史（system 角色），
            # 等价于主流 function calling 历史中的 assistant(tool_calls) + tool results。
            # 不再重复写入短期记忆（DB 历史每轮都会加载，避免双重注入）。
            await mind._add_system_context(
                anything,
                f"[已执行操作摘要] 本轮共执行 {len(result_lines)} 次工具\n"
                + "\n".join(result_lines),
                role="system",
            )

    await complete_reply(mind, anything, "", iterations, tool_chain=tool_chain)


# ==================================================================
# 回复完成与状态清理
# ==================================================================

async def complete_reply(
        mind: Mind,
        anything: Everything,
        content: str,
        iterations: int,
        *,
        error: bool = False,
        tool_chain: Optional[List[Dict]] = None,
) -> None:
    """记录 AI 最终输出，清理回复状态。"""
    from agent.mind.autonomous import MindPhase

    mind._set_phase(MindPhase.REPLYING)
    content = (content or "").strip()

    if content:
        adapter_key = getattr(anything, "adapter_key", "") or "unknown"
        log(f"轮次结束: [{adapter_key}] {len(content)} 字符内心独白, 工具轮次={iterations}", "DEBUG", tag="思维")
        await save_ai_thought(mind, anything, content)

    mind._reply_adapter_key = ""

    await event_bus.emit(EVENT_AFTER_REPLY, {
        "content": content[:100] if content else "",
        "iterations": iterations,
        "error": error,
    })


def log_tool_round(iteration: int, tool_calls: List[ToolCall]) -> None:
    log(
        f"第 {iteration + 1} 轮工具调用: "
        f"{', '.join(tc.name for tc in tool_calls)}",
        tag="思维",
    )
