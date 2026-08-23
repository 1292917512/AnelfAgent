"""实体推送中枢（PushHub）— 实体向 AI 主动推送系统通知的统一入口。

语义对齐手机弹窗：推送以 [push:来源] 标签文本写入目标 scope 的短期记忆
（volatile 层随下一轮上下文带出），需要立即响应时入队唤醒思维循环；
对话进行中到达的推送由 think_loop 每轮经 drain_inflight 并入当前轮
工具链尾部，实现"轮内弹窗"。

entities 层经 entities._sdk.push_notify 桥接访问，不直接 import 本模块。
"""

from __future__ import annotations

import asyncio
import re
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

from core.log import log
from core.tags import get_time_tag, tag_label

# 单条推送内容上限（防止异常实体灌爆短期记忆桶）
_MAX_CONTENT_CHARS = 2000

_tag_unsafe_pattern = re.compile(r"[\[\]:\r\n]")


def _sanitize_source(source: str) -> str:
    """清洗推送来源名：剔除会破坏 [push:xxx] 标签语法的字符。"""
    return _tag_unsafe_pattern.sub("", source).strip() or "entity"


class PushHub:
    """实体推送中枢：包装推送文本、写短期记忆、入队唤醒、轮内注入队列。"""

    def __init__(self, mind: Any) -> None:
        self._mind = mind
        # scope -> 待轮内注入的推送（seq, text）；think_loop 每轮按水位 drain。
        # 已随 volatile 短期记忆进入 base 快照的推送（seq ≤ 水位）drain 时直接丢弃，
        # 避免同一条推送在新会话里既出现在短期记忆又被注入工具链。
        self._inflight: Dict[str, Deque[Tuple[int, str]]] = {}
        self._seq: Dict[str, int] = {}
        # 事件循环引用（跨线程推送时回主循环，与 BackgroundTaskRegistry 同策略）
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """绑定主事件循环（Mind 构造时调用）。"""
        self._loop = loop

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
            trigger: 是否入队唤醒一轮思维（False 则仅留待后续轮次看到）
        """
        content = (content or "").strip()
        if not content:
            return False
        if len(content) > _MAX_CONTENT_CHARS:
            content = content[:_MAX_CONTENT_CHARS] + "…(截断)"

        if self._loop is not None:
            try:
                if asyncio.get_running_loop() is not self._loop:
                    self._loop.call_soon_threadsafe(
                        self.push, scope, source, content, channel, trigger)
                    return True
            except RuntimeError:
                pass  # 无运行中循环（工作线程）：直接回主循环执行
                self._loop.call_soon_threadsafe(
                    self.push, scope, source, content, channel, trigger)
                return True

        text = f"{tag_label('push', _sanitize_source(source))}{get_time_tag()} {content}"
        valid_scope = scope.startswith(("user_", "group_"))
        pfc = self._mind.pfc

        if not valid_scope:
            pfc.add_temporary({"role": "system", "content": text})
            log(f"实体推送（无有效 scope，仅写全局桶）: source={source}", "DEBUG", tag="推送")
            return True

        # 历史写入与入队唤醒为异步投递：完成后才触发思维，回复周期拉取
        # 历史必含本条（无竞态）；推送不再驻留短期记忆（一次性事实固化历史）。
        # seq/inflight 也随投递完成后登记——水位只统计已落历史的推送，
        # 并发启动的回复不会把未落库的推送计入水位而误丢弃。
        preview = f"实体推送 {source}: {content[:60]}"
        try:
            asyncio.get_running_loop().create_task(
                self._deliver(pfc, scope, channel, preview, text, trigger),
            )
        except RuntimeError:
            log("实体推送无法投递（无运行中事件循环）", "DEBUG", tag="推送")
            return False
        log(f"实体推送: scope={scope} source={source} trigger={trigger}", tag="推送")
        return True

    async def _deliver(
            self, pfc: Any, scope: str, channel: str,
            preview: str, text: str, trigger: bool) -> None:
        """推送投递：一次性事实写对话历史 + 入队 + seq/inflight 登记。"""
        from agent.mind.tools.scheduler import enqueue_scope_reply
        await enqueue_scope_reply(pfc, scope, channel, preview, text)
        seq = self._seq.get(scope, 0) + 1
        self._seq[scope] = seq
        self._inflight.setdefault(scope, deque()).append((seq, text))
        if trigger:
            asyncio.create_task(self._mind.try_execute_mind())

    def drain_inflight(self, scope: str, since: int = 0) -> List[str]:
        """取出该 scope 序号大于水位的推送并清空队列（think_loop 每轮调用）。

        序号 ≤ 水位的推送已随对话历史进入当前会话的 base 快照，直接丢弃防重复
        （seq 在历史落库后才分配，水位只统计已固化的事实）。
        """
        queue = self._inflight.pop(scope, None)
        if not queue:
            return []
        return [text for seq, text in queue if seq > since]
