"""在途中断贯通测试：注册表事件原语、流式调用中止（不回退非流式）、非流式竞争取消。

语义锚点：
- InterruptRegistry 的 event 与请求同生命周期（登记置位、消费换新）；
- _llm_chat_stream_once 与 abort_event 竞争，中止时关闭生成器并抛
  LLMCallAborted；
- _invoke_llm_unified 收到 LLMCallAborted 不走"流式失败回退非流式"
  （中断不是故障）；
- 非流式路径同样可中断（在途请求被取消）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import agent.mind.llm_invoker as li
from agent.llm import LLMCallAborted
from agent.llm.types import ChatStreamDelta
from agent.mind.interrupt import InterruptRegistry


class TestRegistryEvent:
    def test_request_sets_event(self):
        reg = InterruptRegistry()
        evt = reg.event("s")
        assert not evt.is_set()
        reg.request("s", "用户刹车")
        assert evt.is_set()
        assert reg.is_requested("s")

    async def test_waiter_wakes_on_request(self):
        reg = InterruptRegistry()
        evt = reg.event("s")

        async def waiter():
            await evt.wait()
            return "woken"

        task = asyncio.get_running_loop().create_task(waiter())
        await asyncio.sleep(0.01)
        assert not task.done()
        reg.request("s")
        assert await asyncio.wait_for(task, timeout=1) == "woken"

    def test_consume_swaps_fresh_event(self):
        reg = InterruptRegistry()
        reg.request("s", "刹车")
        old = reg.event("s")
        assert reg.consume("s") == "刹车"
        assert reg.event("s") is not old
        assert not reg.event("s").is_set()  # 残留置位不误杀下一轮

    def test_clear_before_removes_stale_event(self):
        reg = InterruptRegistry()
        reg.request("s")
        req = reg._requests["s"]
        # cutoff 早于请求时间 → 回复启动窗口内的新中断，保留（连同事件）
        reg.clear_before("s", cutoff=req.requested_at - 10)
        assert reg.is_requested("s")
        assert "s" in reg._events
        # cutoff 晚于请求时间 → 历史旧信号清理（事件一并移除）
        reg.clear_before("s", cutoff=req.requested_at + 10)
        assert not reg.is_requested("s")
        assert "s" not in reg._events


class _ClientBase:
    """替换 li.LLMClient 的测试基类（绕过 isinstance 客户端检查）。"""


class _FakeStreamClient(_ClientBase):
    """chat_stream 产出若干 delta 后挂住，等中断；closed 记录生成器是否被关闭。"""

    def __init__(self, deltas, *, hang: bool = True):
        self.config = SimpleNamespace(model="fake-model")
        self._deltas = deltas
        self._hang = hang
        self.closed = False

    def chat_stream(self, *args, **kwargs):
        return self._stream()

    async def _stream(self):
        try:
            for d in self._deltas:
                yield d
            if self._hang:
                await asyncio.sleep(30)
        finally:
            self.closed = True


def _stream_mind(client) -> SimpleNamespace:
    return SimpleNamespace(
        llm=client,
        session_llm_params=None,
        _get_mind_config=lambda: SimpleNamespace(llm_timeout=5.0),
    )


class TestStreamAbort:
    async def test_abort_closes_stream_and_raises(self, monkeypatch):
        monkeypatch.setattr(li, "LLMClient", _ClientBase)
        client = _FakeStreamClient([ChatStreamDelta(content="部分输出")])
        mind = _stream_mind(client)
        abort = asyncio.Event()

        async def trigger():
            await asyncio.sleep(0.05)
            abort.set()

        trig = asyncio.get_running_loop().create_task(trigger())
        with pytest.raises(LLMCallAborted):
            await li._llm_chat_stream_once(
                mind, [], None, abort_event=abort,
            )
        await trig
        assert client.closed  # 底层流被关闭（连接释放）

    async def test_normal_completion_unaffected(self, monkeypatch):
        monkeypatch.setattr(li, "LLMClient", _ClientBase)
        client = _FakeStreamClient([], hang=False)
        mind = _stream_mind(client)
        result = await li._llm_chat_stream_once(mind, [], None)
        assert result.content == ""

    async def test_race_abort_cancels_awaited_coro(self):
        flag = {"cancelled": False}

        async def slow():
            try:
                await asyncio.sleep(30)
                return "done"
            finally:
                flag["cancelled"] = True

        abort = asyncio.Event()

        async def trigger():
            await asyncio.sleep(0.05)
            abort.set()

        trig = asyncio.get_running_loop().create_task(trigger())
        with pytest.raises(LLMCallAborted):
            await li._race_abort(slow(), abort)
        await trig
        assert flag["cancelled"]

    async def test_race_abort_passthrough_without_event(self):
        async def quick():
            return 42

        assert await li._race_abort(quick(), None) == 42


class TestUnifiedNoFallbackOnAbort:
    def _mind(self, *, stream_fn=None, retry_fn=None):
        calls = {"fallback": 0}

        async def default_retry(messages, tools, **kwargs):
            calls["fallback"] += 1
            return SimpleNamespace(
                content="fallback", tool_calls=[], usage=None, model="m",
            )

        mind = SimpleNamespace(
            llm=SimpleNamespace(config=SimpleNamespace(
                litellm_model="fake-model", api_type="openai",
            )),
            _get_mind_config=lambda: SimpleNamespace(log_ai_output=False),
            _llm_chat_stream_once=stream_fn,
            _llm_chat_with_retry=retry_fn or default_retry,
        )
        return mind, calls

    async def test_stream_abort_does_not_fallback(self):
        async def aborting_stream(*args, **kwargs):
            raise LLMCallAborted("中断事件到达")

        mind, calls = self._mind(stream_fn=aborting_stream)
        abort = asyncio.Event()
        abort.set()
        with pytest.raises(LLMCallAborted):
            await li._invoke_llm_unified(
                mind, [], None, stream=True, abort_event=abort,
            )
        assert calls["fallback"] == 0  # 中断不是故障：绝不回退非流式重试

    async def test_nonstream_abort_cancels_request(self):
        started = asyncio.Event()
        cancelled = {"hit": False}

        async def slow_retry(messages, tools, **kwargs):
            started.set()
            try:
                await asyncio.sleep(30)
                return SimpleNamespace(
                    content="done", tool_calls=[], usage=None, model="m",
                )
            finally:
                cancelled["hit"] = True

        mind, _ = self._mind(retry_fn=slow_retry)
        abort = asyncio.Event()

        async def trigger():
            await started.wait()
            abort.set()

        trig = asyncio.get_running_loop().create_task(trigger())
        with pytest.raises(LLMCallAborted):
            await li._invoke_llm_unified(
                mind, [], None, stream=False, abort_event=abort,
            )
        await trig
        assert cancelled["hit"]
