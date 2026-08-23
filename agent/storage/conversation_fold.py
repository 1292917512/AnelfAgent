"""对话历史摘要折叠 — 固定摘要块 + 纯追加原始窗口。

动机（Prompt Caching 前缀稳定性）：
传统滑动窗口满窗后每条新消息都让历史头部移位，供应商前缀缓存从第 1 条就断裂。
本模块把对话历史拆为两段：

- 固定摘要块：水位线之前的旧消息经 LLM 增量摘要浓缩，持久化在
  conversation_summary 表；两次折叠之间字节完全不变，成为可缓存的大前缀。
- 原始窗口：水位线之后的消息（最近 x 条起），随新消息纯追加增长，
  周期内前缀字节稳定；到达上限 M 时触发折叠，最旧 M-x 条并入摘要，
  窗口重置为 x 条。

折叠在后台异步执行（每 scope 一把锁防并发）；折叠中/失败时窗口短暂超窗
（宽限到 M+x），持续失败则硬降级为原来的"最后 M 条滑动"行为，功能无损。

超时架构（分段设防，替代旧的整体看门狗）：摘要调用走流式空闲超时——思考/
输出期间不计时，完全无响应才判死（summarize_text → chat_with_fallback 流式
通道），总时长护栏 summary_llm_timeout 仅兜底"无限流"；护栏超时以普通异常
进入丢批路径推进水位线，不会以取消形式绕过降级。DB 读写段各带短护栏
（_DB_OP_TIMEOUT）防 sqlite 锁等待悬挂占用 scope 锁。

Model Experience:
- 模型看到什么：摘要提示词内容不变；仅调用通道（流式）与可选专用模型/
  思考档位（conversation_summary_model / conversation_summary_reasoning_effort）。
- token 影响：指定低思考档可显著省 token；流式本身不改变用量。
- 缓存影响：摘要为独立小请求，不共享主对话前缀，不触碰任何前缀层。
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Awaitable, Callable, Dict, List, Optional, Tuple

from core.log import log
from entities._sdk import activate_group, deferred_tool

if TYPE_CHECKING:
    from agent.storage.data_center import ConversationData

# 折叠失败后的重试退避（秒）：避免 LLM 故障时每条消息都重试
_FAILURE_BACKOFF = 60.0

# DB 段看门狗（秒）：sqlite 读写无自身超时，短护栏防锁等待悬挂占用 scope 锁
_DB_OP_TIMEOUT = 60.0

# 折叠片段中单条消息的字符上限（防止超长消息撑爆摘要提示词）
_FOLD_MSG_MAX_CHARS = 500

_FOLD_PROMPT = """你是对话摘要助手。请把「新折叠的对话片段」合并进「已有摘要」，输出更新后的摘要。

要求：
- 保留关键事实、决定、约定、人物信息、未完成的待办；丢弃寒暄与冗余过程
- 按时间脉络组织，语言简洁，使用第三人称
- 总长度不超过 {max_chars} 字符
- 只输出摘要正文，不要任何解释

【已有摘要】
{old_summary}

【新折叠的对话片段】
{folded_text}"""


def is_summary_enabled() -> bool:
    """对话摘要窗口总开关。"""
    from core.config import get_config_bool
    return get_config_bool("conversation_summary_enabled", True)


def _window_size() -> int:
    """对话窗口总条数 M（实时读配置）。"""
    from core.config import get_config_int
    return max(2, get_config_int("max_conversation_size", 30))


def raw_min_messages() -> int:
    """折叠后保留的原文条数 x：由保留百分比派生（总窗口 M × 百分比）。

    单一配置语义：用户只配总窗口与保留比例，x 无需单独维护。
    """
    from core.config import get_config_int
    pct = min(90, max(5, get_config_int("conversation_raw_keep_percent", 33)))
    m = _window_size()
    return max(1, min(m - 1, round(m * pct / 100)))


def fold_hysteresis() -> int:
    """折叠滞回 H（派生 = x）：窗口涨到 M+x 才折叠，每批折 M 条——
    批量大、折叠少、缓存重写频率低，无需单独配置。"""
    return raw_min_messages()


def drop_on_failure() -> bool:
    """折叠失败时是否丢弃该批（仍推进水位线）。

    开启（默认）：失败批次不生成摘要但水位线照样前移——窗口头部不滑动，
    缓存前缀稳定；内容仍完整存于 DB，可用 recall_conversation 检索。
    关闭：保持滑动窗口直到折叠成功（每条新消息都改写历史前缀）。
    """
    from core.config import get_config_bool
    return get_config_bool("conversation_fold_drop_on_failure", True)


def summary_max_chars() -> int:
    """摘要文本字符上限。"""
    from core.config import get_config_int
    return max(500, get_config_int("conversation_summary_max_chars", 4000))


def fold_batch_max() -> int:
    """单次折叠批量上限（积压恢复分批消化；日常批量由窗口参数决定，远低于此）。"""
    from core.config import get_config_int
    return max(10, get_config_int("conversation_fold_batch_max", 100))


def fold_idle_beats() -> int:
    """空闲折叠的心跳阈值：连续 N 个心跳无外部新消息视为空闲。"""
    from core.config import get_config_int
    return max(1, get_config_int("conversation_fold_idle_beats", 6))


def fold_prewarm_enabled() -> bool:
    """折叠完成后是否主动预热缓存（一次 1-token 轻调用写热新前缀）。"""
    from core.config import get_config_bool
    return get_config_bool("conversation_fold_prewarm", True)


def summary_llm_timeout() -> float:
    """摘要 LLM 调用总时长护栏（秒）。

    摘要走流式空闲超时（思考/输出中不计时），本护栏纯兜底防"无限流"
    病理长期占用 scope 锁；超时以普通异常进入丢批路径推进水位线，
    不会以取消形式绕过降级。
    """
    from core.config import get_config_int
    return max(60.0, float(get_config_int("conversation_summary_llm_timeout", 900)))


def _format_fold_messages(rows: List[Dict]) -> str:
    """把待折叠的消息格式化为摘要输入文本。"""
    lines: List[str] = []
    role_labels = {"assistant": "助手", "system": "系统"}
    for row in rows:
        role = role_labels.get(str(row.get("role", "")), "用户")
        content = str(row.get("content", ""))
        if len(content) > _FOLD_MSG_MAX_CHARS:
            content = content[:_FOLD_MSG_MAX_CHARS] + "…"
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


class ConversationFolder:
    """对话折叠调度器（每 scope 锁 + 失败退避 + LLM 增量摘要 + 折后预热钩子）。"""

    def __init__(self) -> None:
        self._locks: Dict[str, asyncio.Lock] = {}
        self._last_failure: Dict[str, float] = {}
        # 折后预热钩子（mind 层注册：用新前缀发一次轻调用写热缓存；
        # storage 层不反向依赖 mind，经注入解耦）
        self._prewarm_hook: Optional[Callable[[str, str], Awaitable[None]]] = None

    def set_prewarm_hook(self, hook: "Callable[[str, str], Awaitable[None]]") -> None:
        """注册折后预热钩子（scope_type, scope_id）。"""
        self._prewarm_hook = hook

    def maybe_schedule_fold(
        self,
        conv_data: "ConversationData",
        scope_type: str,
        scope_id: str,
        scopes: List[Tuple[str, str]],
        watermarks: Dict[str, int],
        watermark_ids: Optional[Dict[str, int]] = None,
    ) -> bool:
        """窗口满时调度一次后台折叠（fire-and-forget）；已在做/退避中则跳过。"""
        if not is_summary_enabled():
            return False
        key = f"{scope_type}:{scope_id}"
        lock = self._locks.setdefault(key, asyncio.Lock())
        if lock.locked():
            log(f"折叠调度跳过（已在折叠中）: {key}", "DEBUG", tag="存储")
            return False
        last_fail = self._last_failure.get(key, 0.0)
        if time.monotonic() - last_fail < _FAILURE_BACKOFF:
            log(f"折叠调度跳过（失败退避中）: {key}", "DEBUG", tag="存储")
            return False
        try:
            asyncio.get_running_loop().create_task(
                self._fold(
                    conv_data, scope_type, scope_id, scopes, dict(watermarks),
                    dict(watermark_ids or {}),
                )
            )
            return True
        except RuntimeError:
            # 无运行中的事件循环（测试/关闭路径）：不折叠，保持滑动行为
            return False

    async def _fold(
        self,
        conv_data: "ConversationData",
        scope_type: str,
        scope_id: str,
        scopes: List[Tuple[str, str]],
        watermarks: Dict[str, int],
        watermark_ids: Optional[Dict[str, int]] = None,
    ) -> None:
        """执行一次折叠：最旧 M-x 条 → 增量摘要 → 推进水位线。

        分段设防（替代旧的整体看门狗）：DB 读写段短护栏防锁悬挂，摘要段由
        流式空闲语义与总时长护栏自理——摘要超时以普通异常进入丢批路径
        （drop_on_failure）推进水位线，不再以取消形式绕过降级导致失败循环。
        """
        key = f"{scope_type}:{scope_id}"
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            try:
                await self._fold_locked(
                    conv_data, scope_type, scope_id, scopes, watermarks, watermark_ids,
                )
                self._last_failure.pop(key, None)
            except Exception as exc:
                self._last_failure[key] = time.monotonic()
                # TimeoutError 等异常的 str() 为空，补类型名保证日志可诊断
                exc_desc = str(exc) or type(exc).__name__
                log(f"对话折叠失败（窗口暂保持滑动行为）: {type(exc).__name__}: {exc_desc}",
                    "WARNING", tag="存储")

    async def _fold_locked(
        self,
        conv_data: "ConversationData",
        scope_type: str,
        scope_id: str,
        scopes: List[Tuple[str, str]],
        watermarks: Dict[str, int],
        watermark_ids: Optional[Dict[str, int]] = None,
    ) -> None:
        sqlite = conv_data.router.sqlite
        max_size = conv_data.max_size
        raw_min = min(raw_min_messages(), max_size - 1)
        trigger = max_size + fold_hysteresis()

        # 按实际余量决定批量：折到窗口回到 x 条（批量 = count - x ≥ trigger - x）
        count_after = await asyncio.wait_for(
            sqlite.count_after_watermarks(
                scopes=scopes, watermarks=watermarks, watermark_ids=watermark_ids,
            ),
            timeout=_DB_OP_TIMEOUT,
        )
        if count_after < trigger:
            return
        fold_count = min(count_after - raw_min, fold_batch_max())
        if fold_count <= 0:
            return
        log(
            f"对话折叠开始: {scope_type}:{scope_id} 水位后 {count_after} 条，"
            f"本次折叠 {fold_count} 条",
            "DEBUG", tag="存储",
        )

        folded_rows = await asyncio.wait_for(
            sqlite.fetch_oldest_after_watermarks(
                scopes=scopes, watermarks=watermarks, limit=fold_count,
                watermark_ids=watermark_ids,
            ),
            timeout=_DB_OP_TIMEOUT,
        )
        if not folded_rows:
            return

        old = await asyncio.wait_for(
            sqlite.get_conversation_summary(
                scope_type=scope_type, scope_id=scope_id,
            ),
            timeout=_DB_OP_TIMEOUT,
        )
        old_summary = (old or {}).get("summary", "") or ""
        old_folded = int((old or {}).get("folded_count", 0) or 0)
        old_dropped = int((old or {}).get("dropped_count", 0) or 0)
        # 并发守卫：若水位线已被其他折叠推进，放弃本次（下次触发会基于新水位线）
        current_marks = (old or {}).get("watermarks", {})
        if old is not None and current_marks != watermarks:
            return

        # 推进各成员 scope 水位线到本次折叠的最大 ts_ns / 最大 id（先算好，成败都用）
        new_marks = dict(watermarks)
        new_id_marks = dict(watermark_ids or (old or {}).get("watermark_ids", {}))
        for row in folded_rows:
            mkey = f"{row['scope_type']}:{row['scope_id']}"
            ts = int(row.get("ts_ns", 0) or 0)
            if ts > int(new_marks.get(mkey, 0) or 0):
                new_marks[mkey] = ts
            rid = int(row.get("id", 0) or 0)
            if rid > int(new_id_marks.get(mkey, 0) or 0):
                new_id_marks[mkey] = rid

        succeeded = False
        try:
            prompt = _FOLD_PROMPT.format(
                max_chars=summary_max_chars(),
                old_summary=old_summary or "（无）",
                folded_text=_format_fold_messages(folded_rows),
            )
            summarizer = await self._resolve_summarizer()
            # 总时长护栏：流式空闲语义由调用链自理，此处仅兜底"无限流"；
            # 超时以 TimeoutError（Exception 子类）进入下方丢批路径，推进水位线
            new_summary = (await asyncio.wait_for(
                summarizer(prompt), timeout=summary_llm_timeout(),
            )).strip()
            if not new_summary:
                raise RuntimeError("摘要生成返回空内容")
            if len(new_summary) > summary_max_chars():
                new_summary = new_summary[: summary_max_chars()]
            succeeded = True
        except Exception:
            if not drop_on_failure():
                raise
            # 失败丢批：不生成摘要但水位线照样前移——窗口头部不滑动、
            # 缓存前缀稳定；被丢批次仍完整存于 DB，可用 recall_conversation 检索
            old_dropped += len(folded_rows)
            new_summary = old_summary
            log(
                f"对话折叠失败，丢弃本批 {len(folded_rows)} 条以保持窗口稳定"
                f"（内容仍在 DB，可 recall_conversation 检索）: {scope_type}:{scope_id}",
                "WARNING", tag="存储",
            )

        await asyncio.wait_for(
            sqlite.upsert_conversation_summary(
                scope_type=scope_type, scope_id=scope_id,
                summary=new_summary, watermarks=new_marks,
                folded_count=old_folded + (len(folded_rows) if succeeded else 0),
                dropped_count=old_dropped,
                watermark_ids=new_id_marks,
            ),
            timeout=_DB_OP_TIMEOUT,
        )
        if succeeded:
            log(
                f"对话折叠完成: {scope_type}:{scope_id} 折叠 {len(folded_rows)} 条 "
                f"(累计 {old_folded + len(folded_rows)} 条)",
                tag="存储",
            )
            self._dispatch_prewarm(scope_type, scope_id)

    def _dispatch_prewarm(self, scope_type: str, scope_id: str) -> None:
        """折叠成功后异步预热新前缀（把缓存断点代价转移到空闲后台）。"""
        if not fold_prewarm_enabled() or self._prewarm_hook is None:
            return
        try:
            asyncio.get_running_loop().create_task(
                self._run_prewarm(scope_type, scope_id)
            )
        except RuntimeError:
            pass  # 无运行中的事件循环（测试/关闭路径）

    async def _run_prewarm(self, scope_type: str, scope_id: str) -> None:
        try:
            assert self._prewarm_hook is not None
            await self._prewarm_hook(scope_type, scope_id)
            log(f"折叠后缓存预热完成: {scope_type}:{scope_id}", "DEBUG", tag="存储")
        except Exception as exc:
            log(f"折叠后缓存预热失败（不影响功能）: {type(exc).__name__}: {exc}", "DEBUG", tag="存储")

    @staticmethod
    async def _resolve_summarizer() -> Callable[[str], Awaitable[str]]:
        """解析摘要函数：优先运行时 Mind.summarize_text（主模型纯文本调用）。"""
        from agent.runtime.singleton import get_runtime
        mind = get_runtime().mind
        summarizer = getattr(mind, "summarize_text", None)
        if summarizer is None:
            raise RuntimeError("运行时 Mind 不具备摘要能力")
        return summarizer


# 全局单例
conversation_folder = ConversationFolder()


# ------------------------------------------------------------------
# AI 工具：主动整理对话历史
# ------------------------------------------------------------------

# 会话数据引用（register_fold_tools 注入）
_conv_data: Optional["ConversationData"] = None


def register_fold_tools(conv_data: "ConversationData") -> None:
    """注入会话数据并注册对话整理工具（memory 组，幂等）。"""
    global _conv_data
    _conv_data = conv_data
    activate_group("memory")


def _resolve_target_scope(scope: str) -> Tuple[str, str]:
    """解析工具入参 scope：空 = 当前会话；否则按实体 scope 文本解析。

    Returns:
        (scope_type, scope_id)；解析失败返回 ("", "")。
    """
    if not scope:
        from agent.mind.tool_activation import ToolActivationManager
        scope = ToolActivationManager.current_scope()
    if not scope or scope == "_global":
        return "", ""
    from agent.messages import parse_entity_scope
    scope_type, adapter, base_id, _session_id = parse_entity_scope(scope)
    if not scope_type or not base_id:
        return "", ""
    return scope_type, f"{adapter}:{base_id}" if adapter else base_id


@deferred_tool(
    name="fold_conversations",
    group="memory", tags=["core"], source="mind.storage",
    description="整理对话历史：把较早的消息折叠进固定摘要块（保持上下文前缀稳定、提升缓存命中、控制上下文体积）。"
    "长时间聊完一个话题或对话窗口显得臃肿时调用。scope 不传默认整理当前会话，传 all 整理全部有待整理的会话。",
)
async def fold_conversations(scope: str = "") -> str:
    """折叠对话历史到摘要块。

    Args:
        scope: 目标会话。空 = 当前会话；"all" = 全部有积压的会话；
            也接受完整实体 scope（如 user_qq:123 / group_qq:456）
    """
    from core.tool_errors import ErrorCause, tool_error
    if _conv_data is None:
        return tool_error(
            "会话存储未初始化", cause=ErrorCause.STATE, retryable=True,
            hint="系统组件尚未完成初始化，请稍后重试",
        )
    try:
        if scope.strip().lower() == "all":
            activity = await _conv_data.list_scope_activity()
            scheduled, backlogs = [], {}
            for st, sid, _max_ts in activity:
                backlog = await _conv_data.scope_backlog(st, sid)
                if backlog >= _conv_data.fold_idle_min:
                    backlogs[f"{st}:{sid}"] = backlog
                    if await _conv_data.schedule_fold(st, sid):
                        scheduled.append(f"{st}:{sid}")
            import json as _json
            return _json.dumps({
                "scheduled": scheduled,
                "backlogs": backlogs,
                "note": "折叠在后台异步执行，完成后自动预热缓存",
            }, ensure_ascii=False)

        scope_type, scope_id = _resolve_target_scope(scope.strip())
        if not scope_type:
            return tool_error(
                "无法确定目标会话", cause=ErrorCause.PARAM, retryable=False,
                hint="在对话中调用时留空即可；或传 all 整理全部会话",
            )
        backlog = await _conv_data.scope_backlog(scope_type, scope_id)
        if backlog < _conv_data.fold_idle_min:
            import json as _json
            return _json.dumps({
                "scheduled": [], "backlog": backlog,
                "note": f"当前积压 {backlog} 条，低于整理阈值，无需折叠",
            }, ensure_ascii=False)
        ok = await _conv_data.schedule_fold(scope_type, scope_id)
        import json as _json
        return _json.dumps({
            "scheduled": [f"{scope_type}:{scope_id}"] if ok else [],
            "backlog": backlog,
            "note": "折叠在后台异步执行，完成后自动预热缓存" if ok else "该会话正在折叠中或刚失败退避，稍后再试",
        }, ensure_ascii=False)
    except Exception as exc:
        from core.tool_errors import error_from_exception
        return error_from_exception(exc, action="折叠调度")
