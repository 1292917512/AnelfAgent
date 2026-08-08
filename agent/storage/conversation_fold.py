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
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Awaitable, Callable, Dict, List, Tuple

from core.log import log

if TYPE_CHECKING:
    from agent.storage.data_center import ConversationData

# 折叠失败后的重试退避（秒）：避免 LLM 故障时每条消息都重试
_FAILURE_BACKOFF = 60.0

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


def raw_min_messages() -> int:
    """原始窗口下限 x：折叠后保留的最近原始消息条数。"""
    from core.config import get_config_int
    return max(1, get_config_int("conversation_raw_min", 10))


def fold_hysteresis() -> int:
    """折叠滞回 H：窗口到达 M+H 才触发折叠（批量 M+H-x，折叠更少、缓存更稳）。"""
    from core.config import get_config_int
    return max(0, get_config_int("conversation_fold_hysteresis", 15))


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


def _format_fold_messages(rows: List[Dict]) -> str:
    """把待折叠的消息格式化为摘要输入文本。"""
    lines: List[str] = []
    for row in rows:
        role = "助手" if row.get("role") == "assistant" else "用户"
        content = str(row.get("content", ""))
        if len(content) > _FOLD_MSG_MAX_CHARS:
            content = content[:_FOLD_MSG_MAX_CHARS] + "…"
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


class ConversationFolder:
    """对话折叠调度器（每 scope 锁 + 失败退避 + LLM 增量摘要）。"""

    def __init__(self) -> None:
        self._locks: Dict[str, asyncio.Lock] = {}
        self._last_failure: Dict[str, float] = {}

    def maybe_schedule_fold(
        self,
        conv_data: "ConversationData",
        scope_type: str,
        scope_id: str,
        scopes: List[Tuple[str, str]],
        watermarks: Dict[str, int],
    ) -> bool:
        """窗口满时调度一次后台折叠（fire-and-forget）；已在做/退避中则跳过。"""
        if not is_summary_enabled():
            return False
        key = f"{scope_type}:{scope_id}"
        lock = self._locks.setdefault(key, asyncio.Lock())
        if lock.locked():
            return False
        last_fail = self._last_failure.get(key, 0.0)
        if time.monotonic() - last_fail < _FAILURE_BACKOFF:
            return False
        try:
            asyncio.get_running_loop().create_task(
                self._fold(conv_data, scope_type, scope_id, scopes, dict(watermarks))
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
    ) -> None:
        """执行一次折叠：最旧 M-x 条 → 增量摘要 → 推进水位线。"""
        key = f"{scope_type}:{scope_id}"
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            try:
                await self._fold_locked(
                    conv_data, scope_type, scope_id, scopes, watermarks,
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
    ) -> None:
        sqlite = conv_data.router.sqlite
        max_size = conv_data.max_size
        raw_min = min(raw_min_messages(), max_size - 1)
        trigger = max_size + fold_hysteresis()

        # 按实际余量决定批量：折到窗口回到 x 条（批量 = count - x ≥ trigger - x）
        count_after = await sqlite.count_after_watermarks(
            scopes=scopes, watermarks=watermarks,
        )
        if count_after < trigger:
            return
        fold_count = count_after - raw_min
        if fold_count <= 0:
            return

        folded_rows = await sqlite.fetch_oldest_after_watermarks(
            scopes=scopes, watermarks=watermarks, limit=fold_count,
        )
        if not folded_rows:
            return

        old = await sqlite.get_conversation_summary(
            scope_type=scope_type, scope_id=scope_id,
        )
        old_summary = (old or {}).get("summary", "") or ""
        old_folded = int((old or {}).get("folded_count", 0) or 0)
        old_dropped = int((old or {}).get("dropped_count", 0) or 0)
        # 并发守卫：若水位线已被其他折叠推进，放弃本次（下次触发会基于新水位线）
        current_marks = (old or {}).get("watermarks", {})
        if old is not None and current_marks != watermarks:
            return

        # 推进各成员 scope 水位线到本次折叠的最大 ts_ns（先算好，成败都用）
        new_marks = dict(watermarks)
        for row in folded_rows:
            mkey = f"{row['scope_type']}:{row['scope_id']}"
            ts = int(row.get("ts_ns", 0) or 0)
            if ts > int(new_marks.get(mkey, 0) or 0):
                new_marks[mkey] = ts

        succeeded = False
        try:
            prompt = _FOLD_PROMPT.format(
                max_chars=summary_max_chars(),
                old_summary=old_summary or "（无）",
                folded_text=_format_fold_messages(folded_rows),
            )
            summarizer = await self._resolve_summarizer()
            new_summary = (await summarizer(prompt)).strip()
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

        await sqlite.upsert_conversation_summary(
            scope_type=scope_type, scope_id=scope_id,
            summary=new_summary, watermarks=new_marks,
            folded_count=old_folded + (len(folded_rows) if succeeded else 0),
            dropped_count=old_dropped,
        )
        if succeeded:
            log(
                f"对话折叠完成: {scope_type}:{scope_id} 折叠 {len(folded_rows)} 条 "
                f"(累计 {old_folded + len(folded_rows)} 条)",
                tag="存储",
            )

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
