"""实体推送中枢（PushHub）— 实体向 AI 主动推送系统通知的统一入口。

推送以 [push:来源] 标签写入目标会话历史，写入失败时回退短期记忆，
有效会话可按需唤醒思维循环；无有效会话的推送仅写入全局短期记忆。
对话进行中到达的推送由 think_loop 每轮经 drain_inflight 并入当前轮
工具链尾部，实现"轮内弹窗"。

entities 层经 entities._sdk.push_notify 桥接访问，不直接 import 本模块。

模型可见：通知经会话历史或轮内弹窗送达，标明来源并按水位去重。
Token 影响：单条正文上限 2000 字符，不新增模型调用。
缓存影响：追加会话消息或动态工具链，不修改稳定前缀。
"""

from __future__ import annotations

import asyncio
import re
import threading
from collections import deque
from typing import TYPE_CHECKING, Any, Callable, Coroutine, Deque, Dict, List, Tuple

from core.async_helper import spawn
from core.log import log
from core.tags import get_time_tag, tag_label

if TYPE_CHECKING:
    from agent.mind.prefrontal_cortex import PrefrontalCortex

# 单条推送内容上限（防止异常实体灌爆短期记忆桶）
_MAX_CONTENT_CHARS = 2000

_tag_unsafe_pattern = re.compile(r"[\[\]:\r\n]")


def _sanitize_source(source: str) -> str:
    """清洗推送来源名：剔除会破坏 [push:xxx] 标签语法的字符。"""
    return _tag_unsafe_pattern.sub("", source).strip() or "entity"


class PushHub:
    """实体通知的历史投递、会话唤醒与轮内注入队列。"""

    def __init__(
        self, pfc: PrefrontalCortex, wake: Callable[[], Coroutine[Any, Any, None]],
        *, loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._pfc = pfc
        self._wake = wake
        # scope -> 待轮内注入的推送（seq, text）；think_loop 每轮按水位 drain。
        # 已进入历史快照的推送（seq ≤ 水位）不再注入工具链。
        self._inflight: Dict[str, Deque[Tuple[int, str]]] = {}
        self._seq: Dict[str, int] = {}
        self._loop = loop
        self._delivery_tasks: set[asyncio.Task[None]] = set()
        self._wake_tasks: set[asyncio.Task[None]] = set()
        self._closing = False
        self._admission_lock = threading.Lock()

    async def close(self) -> None:
        """拒绝新推送，停止唤醒任务并等待已受理通知完成投递。"""
        with self._admission_lock:
            self._closing = True
        # 关停门关闭前受理的跨线程回调先进入投递队列。
        await asyncio.sleep(0)
        for task in tuple(self._wake_tasks):
            task.cancel()
        if self._wake_tasks:
            await asyncio.gather(*tuple(self._wake_tasks), return_exceptions=True)
        while self._delivery_tasks:
            await asyncio.gather(*tuple(self._delivery_tasks), return_exceptions=True)
        self._inflight.clear()
        self._seq.clear()

    def current_seq(self, scope: str) -> int:
        """该 scope 已到达的推送序号（think_loop 会话初始化时取作并入水位）。"""
        return self._seq.get(scope, 0)

    def push(
        self,
        scope: str,
        source: str,
        content: str,
        channel: str = "",
        trigger: bool = True,
    ) -> bool:
        """推送一条系统通知给 AI。

        Args:
            scope: 目标会话 scope（user_/group_ 前缀）；无效 scope 仅写全局短期记忆桶
            source: 推送来源标识（实体名），渲染为 [push:来源]
            content: 通知正文（超长截断）
            channel: 回复路由 adapter_key（trigger 时随入队登记）
            trigger: 是否主动唤醒思维；False 时仍登记通知和待处理状态
        """
        content = (content or "").strip()
        if not content:
            return False
        if len(content) > _MAX_CONTENT_CHARS:
            content = content[:_MAX_CONTENT_CHARS] + "…(截断)"

        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        with self._admission_lock:
            if self._closing or not self._loop.is_running():
                return False
            if current_loop is not self._loop:
                try:
                    self._loop.call_soon_threadsafe(
                        self._accept, scope, source, content, channel, trigger)
                except RuntimeError as exc:
                    log(f"实体推送无法受理: {exc}", "DEBUG", tag="推送")
                    return False
                return True
        return self._accept(scope, source, content, channel, trigger)

    def _accept(self, scope: str, source: str, content: str, channel: str, trigger: bool) -> bool:
        """在所属事件循环内投递已受理通知。"""
        text = f"{tag_label('push', _sanitize_source(source))}{get_time_tag()} {content}"
        valid_scope = scope.startswith(("user_", "group_"))

        if not valid_scope:
            self._pfc.add_temporary({"role": "system", "content": text})
            log(f"实体推送（无有效 scope，仅写全局桶）: source={source}", "DEBUG", tag="推送")
            return True

        # 历史写入与入队唤醒为异步投递：完成后才触发思维，回复周期拉取
        # 历史必含本条（无竞态）；推送不再驻留短期记忆（一次性事实固化历史）。
        # seq/inflight 也随投递完成后登记——水位只统计已落历史的推送，
        # 并发启动的回复不会把未落库的推送计入水位而误丢弃。
        preview = f"实体推送 {source}: {content[:60]}"
        task = spawn(self._deliver(scope, channel, preview, text, trigger), name="mind.push")
        self._delivery_tasks.add(task)
        task.add_done_callback(self._delivery_tasks.discard)
        log(f"实体推送: scope={scope} source={source} trigger={trigger}", tag="推送")
        return True

    async def _deliver(
            self, scope: str, channel: str,
            preview: str, text: str, trigger: bool) -> None:
        """推送投递：一次性事实写对话历史 + 入队 + seq/inflight 登记。"""
        await self._pfc.enqueue_reply(scope, channel, preview, text)
        seq = self._seq.get(scope, 0) + 1
        self._seq[scope] = seq
        self._inflight.setdefault(scope, deque()).append((seq, text))
        if trigger and not self._closing:
            task = spawn(self._wake(), name="mind.wake")
            self._wake_tasks.add(task)
            task.add_done_callback(self._wake_tasks.discard)

    def drain_inflight(self, scope: str, since: int = 0) -> List[str]:
        """取出该 scope 序号大于水位的推送并清空队列（think_loop 每轮调用）。

        序号 ≤ 水位的推送已随对话历史进入当前会话的 base 快照，直接丢弃防重复
        （seq 在历史落库后才分配，水位只统计已固化的事实）。
        """
        queue = self._inflight.pop(scope, None)
        if not queue:
            return []
        return [text for seq, text in queue if seq > since]
