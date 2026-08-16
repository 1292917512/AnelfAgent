"""Anthropic 缓存断点设施单元测试（agent/llm/prompt_cache.py）。

覆盖：marker TTL、strip/count、工具链尾移动断点（先剥后注）、
wire tools 断点（不改写入参）、llm_client 自限额补位。
"""

from __future__ import annotations

from types import SimpleNamespace

from agent.llm.llm_client import LLMClient, LLMClientConfig
from agent.llm.prompt_cache import (
    MAX_BREAKPOINTS,
    anthropic_ttl_beta_headers,
    apply_tools_breakpoint,
    cache_marker,
    count_breakpoints,
    decorate_messages,
    is_anthropic_wire,
    strip_cache_control_copy,
)
from core.config import ConfigManager


class TestCacheMarker:
    def test_default_5m_no_ttl_field(self) -> None:
        assert cache_marker() == {"type": "ephemeral"}

    def test_1h_ttl_carries_field(self) -> None:
        ConfigManager.set("prompt_cache_anthropic_ttl", "1h")
        try:
            # ttl 标记仅真 Anthropic 线注入（与 beta 头判定口径一致）
            assert cache_marker("anthropic") == {"type": "ephemeral", "ttl": "1h"}
            # 网关 claude（api_type 非 anthropic）不带 ttl，避免缺 beta 头 400
            assert cache_marker("openai") == {"type": "ephemeral"}
            assert cache_marker() == {"type": "ephemeral"}
        finally:
            ConfigManager.set("prompt_cache_anthropic_ttl", "5m")

    def test_unknown_value_falls_back_to_5m(self) -> None:
        ConfigManager.set("prompt_cache_anthropic_ttl", "forever")
        try:
            assert cache_marker() == {"type": "ephemeral"}
        finally:
            ConfigManager.set("prompt_cache_anthropic_ttl", "5m")


class TestAnthropicWire:
    def test_api_type_detection(self) -> None:
        assert is_anthropic_wire("k3", "anthropic")
        assert is_anthropic_wire("claude-sonnet-4", "openai")  # 模型名推断
        assert not is_anthropic_wire("qwen3", "openai")
        assert not is_anthropic_wire("", "")

    def test_master_switch_off(self) -> None:
        ConfigManager.set("prompt_cache_anthropic_breakpoint", False)
        try:
            assert not is_anthropic_wire("k3", "anthropic")
        finally:
            ConfigManager.set("prompt_cache_anthropic_breakpoint", True)


def _layered_messages() -> list[dict]:
    """模拟真实管线输出 + think_loop 追加（带层标签，尾部动态区布局）。"""
    return [
        {"role": "system", "content": "人设", "_layer": "stable"},
        {"role": "system", "content": "工具目录", "_layer": "stable"},
        {"role": "system", "content": "摘要", "_layer": "summary"},
        {"role": "user", "content": "历史1", "_layer": "conversation"},
        {"role": "assistant", "content": "历史2", "_layer": "conversation"},
        {"role": "system", "content": "便签", "_layer": "context"},
        {"role": "system", "content": "画像", "_layer": "profile"},
        {"role": "assistant", "content": "调用工具"},              # 工具链（无标签）
        {"role": "tool", "tool_call_id": "1", "content": "结果"},  # 链尾
        {"role": "system", "content": "exec", "_layer": "exec_context"},
    ]


class TestDecorateMessages:
    def test_anchor_layout(self) -> None:
        """锚点：stable 末 / 历史末 / 链尾 = 3 个（便签在尾部动态区，不占断点）。"""
        msgs = decorate_messages(_layered_messages(), anthropic=True)
        bp = [m for m in msgs if m.get("cache_control")]
        assert len(bp) == 3
        assert bp[0]["content"] == "工具目录"    # stable 层末（人设不单独占额）
        assert bp[1]["content"] == "历史2"       # conversation 末
        assert bp[2]["content"] == "结果"        # 链尾（exec_context 无断点）

    def test_chain_tail_moves_forward(self) -> None:
        """链增长后重新装饰：链尾锚点前移，旧位置无残留（幂等）。"""
        round1 = _layered_messages()
        decorated1 = decorate_messages(round1, anthropic=True)
        # 第 2 轮：链追加（共享原 dict，装饰产出新列表）
        round2 = round1[:-1] + [
            {"role": "assistant", "content": "再调用"},
            {"role": "tool", "tool_call_id": "2", "content": "结果2"},
            round1[-1],
        ]
        decorated2 = decorate_messages(round2, anthropic=True)
        bp = [m for m in decorated2 if m.get("cache_control")]
        assert len(bp) == 3
        assert bp[-1]["content"] == "结果2"
        # 第 1 轮的装饰不污染共享原消息
        assert count_breakpoints(round1) == 0
        assert decorated1[-2]["content"] == "结果"  # 第 1 轮链尾快照不受影响

    def test_empty_chain_round1_two_anchors(self) -> None:
        """无工具链（首轮）：2 个锚点（stable 末 + 历史末），余量留给 tools 断点。"""
        msgs = [m for m in _layered_messages() if m.get("_layer") is not None]
        decorated = decorate_messages(msgs, anthropic=True)
        assert count_breakpoints(decorated) == 2

    def test_no_history_falls_back_to_summary(self) -> None:
        msgs = [m for m in _layered_messages() if m.get("_layer") != "conversation"]
        decorated = decorate_messages(msgs, anthropic=True)
        summary = next(m for m in decorated if m.get("_layer") == "summary")
        assert summary.get("cache_control") == {"type": "ephemeral"}

    def test_history_anchor_config_off(self) -> None:
        ConfigManager.set("prompt_cache_summary_breakpoint", False)
        try:
            decorated = decorate_messages(_layered_messages(), anthropic=True)
            layers = [m.get("_layer") for m in decorated if m.get("cache_control")]
            assert "conversation" not in layers and "summary" not in layers
        finally:
            ConfigManager.set("prompt_cache_summary_breakpoint", True)

    def test_non_anthropic_passthrough_zero_copy(self) -> None:
        msgs = _layered_messages()
        result = decorate_messages(msgs, anthropic=False)
        assert result is msgs  # 无断点零拷贝直通

    def test_non_anthropic_strips_defensively(self) -> None:
        msgs = [{"role": "user", "content": "a", "cache_control": cache_marker()}]
        result = decorate_messages(msgs, anthropic=False)
        assert count_breakpoints(result) == 0
        assert count_breakpoints(msgs) == 1  # 原列表不改写

    def test_idempotent_redecoration(self) -> None:
        """已装饰消息再次装饰：剥旧放新，总数不累积。"""
        once = decorate_messages(_layered_messages(), anthropic=True)
        twice = decorate_messages(once, anthropic=True)
        assert count_breakpoints(twice) == 3

    def test_unlayered_aux_call_marks_last_message(self) -> None:
        """无层标签的辅助调用（折叠/评审）：末消息锚点兜底。"""
        msgs = [{"role": "user", "content": "评审这段对话"}]
        decorated = decorate_messages(msgs, anthropic=True)
        assert count_breakpoints(decorated) == 1
        assert decorated[0].get("cache_control")


class TestToolsBreakpoint:
    def test_marks_last_tool_without_mutating_input(self) -> None:
        tools = [
            {"type": "function", "function": {"name": "a"}},
            {"type": "function", "function": {"name": "b"}},
        ]
        result = apply_tools_breakpoint(tools)
        assert result is not None
        assert result[-1]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in result[0]
        # 入参不被改写（tools 列表跨调用共享）
        assert all("cache_control" not in t for t in tools)

    def test_empty_tools_passthrough(self) -> None:
        assert apply_tools_breakpoint(None) is None
        assert apply_tools_breakpoint([]) == []


def _anthropic_client() -> LLMClient:
    return LLMClient(LLMClientConfig(model="claude-sonnet-4", api_type="anthropic"))


def _openai_client() -> LLMClient:
    return LLMClient(LLMClientConfig(model="gpt-4.1", api_type="openai"))


def _tools() -> list[dict]:
    return [{"type": "function", "function": {"name": "t1", "parameters": {}}}]


class TestClientToolsBreakpoint:
    def test_anthropic_round1_gets_tools_breakpoint(self) -> None:
        """首轮（消息侧断点 ≤3）补位 tools 断点，总数不超上限。"""
        client = _anthropic_client()
        messages = [
            {"role": "system", "content": "人设", "cache_control": cache_marker()},
            {"role": "user", "content": "你好", "cache_control": cache_marker()},
        ]
        kwargs = client._build_kwargs(messages, None, _tools(), None)
        wire_tools = kwargs["tools"]
        assert wire_tools[-1].get("cache_control") == {"type": "ephemeral"}
        # 总断点数（消息侧 + tools）不超过 Anthropic 上限
        from agent.llm.prompt_cache import count_breakpoints as count
        assert count(kwargs["messages"]) + 1 <= MAX_BREAKPOINTS

    def test_skips_when_message_side_budget_full(self) -> None:
        """工具链轮次（消息侧已 4 断点）让位，不再追加 tools 断点。"""
        client = _anthropic_client()
        marker = cache_marker()
        messages = [
            {"role": "system", "content": "人设", "cache_control": marker},
            {"role": "system", "content": "便签", "cache_control": marker},
            {"role": "user", "content": "历史", "cache_control": marker},
            {"role": "tool", "tool_call_id": "1", "content": "r", "cache_control": marker},
        ]
        kwargs = client._build_kwargs(messages, None, _tools(), None)
        assert all("cache_control" not in t for t in kwargs["tools"])

    def test_non_anthropic_wire_untouched(self) -> None:
        client = _openai_client()
        messages = [{"role": "user", "content": "你好"}]
        kwargs = client._build_kwargs(messages, None, _tools(), None)
        assert all("cache_control" not in t for t in kwargs["tools"])

    def test_config_off_disables(self) -> None:
        ConfigManager.set("prompt_cache_tools_breakpoint", False)
        try:
            client = _anthropic_client()
            messages = [{"role": "user", "content": "你好"}]
            kwargs = client._build_kwargs(messages, None, _tools(), None)
            assert all("cache_control" not in t for t in kwargs["tools"])
        finally:
            ConfigManager.set("prompt_cache_tools_breakpoint", True)


class TestStripCopy:
    def test_copy_is_non_mutating_and_shares_clean_messages(self) -> None:
        """剥离只发生在副本上：原列表（与 think_loop 上下文共享）不被改写，
        无断点消息零拷贝共享同一 dict。"""
        marker = cache_marker()
        clean = {"role": "user", "content": "干净消息"}
        dirty = {"role": "tool", "tool_call_id": "1", "content": "r", "cache_control": marker}
        dirty_block = {"role": "user", "content": [
            {"type": "text", "text": "a", "cache_control": marker},
        ]}
        msgs = [clean, dirty, dirty_block]
        stripped = strip_cache_control_copy(msgs)
        # 原列表不被改写
        assert count_breakpoints(msgs) == 2
        # 副本无断点
        assert count_breakpoints(stripped) == 0
        # 无断点消息零拷贝共享
        assert stripped[0] is clean
        # 含断点消息被拷贝
        assert stripped[1] is not dirty
        assert stripped[2]["content"][0]["text"] == "a"


class TestTtlBetaHeaders:
    def test_5m_no_beta_header(self) -> None:
        assert anthropic_ttl_beta_headers("anthropic") == {}

    def test_1h_anthropic_carries_beta_header(self) -> None:
        ConfigManager.set("prompt_cache_anthropic_ttl", "1h")
        try:
            assert anthropic_ttl_beta_headers("anthropic") == {
                "anthropic-beta": "extended-cache-ttl-2025-04-11",
            }
        finally:
            ConfigManager.set("prompt_cache_anthropic_ttl", "5m")

    def test_1h_non_anthropic_no_header(self) -> None:
        ConfigManager.set("prompt_cache_anthropic_ttl", "1h")
        try:
            assert anthropic_ttl_beta_headers("openai") == {}
        finally:
            ConfigManager.set("prompt_cache_anthropic_ttl", "5m")

    def test_build_kwargs_merges_beta_header_with_user_headers(self) -> None:
        """1h TTL 时 _build_kwargs 自动携带 beta 头，用户自定义头仍可覆盖。"""
        ConfigManager.set("prompt_cache_anthropic_ttl", "1h")
        try:
            client = LLMClient(LLMClientConfig(
                model="claude-sonnet-4", api_type="anthropic",
                extra_headers={"X-Custom": "1"},
            ))
            kwargs = client._build_kwargs([{"role": "user", "content": "hi"}], None)
            headers = kwargs["extra_headers"]
            assert headers["anthropic-beta"] == "extended-cache-ttl-2025-04-11"
            assert headers["X-Custom"] == "1"
        finally:
            ConfigManager.set("prompt_cache_anthropic_ttl", "5m")


class TestStreamUsageTap:
    """流式 usage 旁路：litellm 丢弃的供应商缓存字段从原始 chunk 救援。"""

    @staticmethod
    def _raw_chunk(usage: object = None, content: str = "") -> SimpleNamespace:
        choice = SimpleNamespace(
            delta=SimpleNamespace(content=content, reasoning_content=None,
                                  reasoning_details=None, tool_calls=None),
            finish_reason=None,
        )
        return SimpleNamespace(usage=usage, choices=[choice])

    async def test_tap_recovers_deepseek_fields(self) -> None:
        """DeepSeek 模式：litellm chunk 无缓存字段，原始 chunk 有 → 合并补全。"""
        from agent.llm.response_parsing import _iter_stream, install_usage_tap

        raw_usage = SimpleNamespace(
            prompt_tokens=17623, completion_tokens=8, total_tokens=17631,
            prompt_cache_hit_tokens=16000, prompt_cache_miss_tokens=1623,
        )
        raw_chunks = [
            self._raw_chunk(content="好"),
            self._raw_chunk(usage=raw_usage),
        ]

        async def raw_stream():
            for c in raw_chunks:
                yield c

        # 模拟 litellm CustomStreamWrapper：completion_stream 是原始流，
        # 自身迭代产出转换后 chunk（usage 丢字段）
        class _FakeWrapper:
            def __init__(self) -> None:
                self.completion_stream = raw_stream()

            def __aiter__(self):
                return self._gen()

            async def _gen(self):
                async for raw in self.completion_stream:
                    converted = SimpleNamespace(
                        choices=getattr(raw, "choices", []),
                        usage=SimpleNamespace(  # litellm 转换后丢扩展字段
                            prompt_tokens=17623, completion_tokens=8,
                            total_tokens=17631,
                        ) if raw.usage is not None else None,
                    )
                    yield converted

        wrapper = _FakeWrapper()
        sink = install_usage_tap(wrapper)
        assert sink is not None
        usages = []
        async for delta, _buf in _iter_stream(wrapper, "", {}, sink):
            if delta.usage:
                usages.append(delta.usage)
        assert usages, "应有 usage delta"
        final = usages[-1]
        assert final.cache_read_input_tokens == 16000
        assert final.cache_observable is True

    async def test_tap_marks_unobservable_when_fields_absent(self) -> None:
        """端点流式不回报缓存字段：动态标记不可观测（显示—而非谎报 0%）。"""
        from agent.llm.response_parsing import _iter_stream, install_usage_tap

        raw_usage = SimpleNamespace(
            prompt_tokens=100, completion_tokens=5, total_tokens=105,
        )

        async def raw_stream():
            yield self._raw_chunk(usage=raw_usage)

        class _FakeWrapper:
            def __init__(self) -> None:
                self.completion_stream = raw_stream()

            def __aiter__(self):
                return self._gen()

            async def _gen(self):
                async for raw in self.completion_stream:
                    yield SimpleNamespace(
                        choices=getattr(raw, "choices", []),
                        usage=SimpleNamespace(
                            prompt_tokens=100, completion_tokens=5, total_tokens=105,
                        ) if raw.usage is not None else None,
                    )

        wrapper = _FakeWrapper()
        sink = install_usage_tap(wrapper)
        usages = []
        async for delta, _buf in _iter_stream(wrapper, "", {}, sink):
            if delta.usage:
                usages.append(delta.usage)
        assert usages and usages[-1].cache_observable is False

    def test_install_tap_graceful_without_attr(self) -> None:
        """litellm 内部结构变化（无 completion_stream）：不装旁路，返回 None。"""
        from agent.llm.response_parsing import install_usage_tap
        assert install_usage_tap(object()) is None


class TestCacheAffinityHandler:
    def test_anthropic_gets_pool_limited_handler(self) -> None:
        """Anthropic 线注入小连接池 handler（节点级缓存亲和）。"""
        client = _anthropic_client()
        kwargs = client._build_kwargs([{"role": "user", "content": "hi"}], None)
        handler = kwargs.get("client")
        assert handler is not None
        pool = handler.client._transport._pool  # httpx 内部，仅测试断言用
        assert pool._max_connections == 4
        assert pool._max_keepalive_connections == 4

    def test_non_anthropic_no_handler(self) -> None:
        client = _openai_client()
        kwargs = client._build_kwargs([{"role": "user", "content": "hi"}], None)
        assert "client" not in kwargs

    def test_proxy_anthropic_skips_handler(self) -> None:
        """代理场景沿用 env lease，不注入 handler（避免代理配置被绕过）。"""
        client = LLMClient(LLMClientConfig(
            model="claude-sonnet-4", api_type="anthropic", proxy_url="http://127.0.0.1:7890",
        ))
        kwargs = client._build_kwargs([{"role": "user", "content": "hi"}], None)
        assert "client" not in kwargs

    def test_handler_reused_and_closed(self) -> None:
        """handler 按客户端实例复用；close 释放底层连接池。"""
        import asyncio

        client = _anthropic_client()
        h1 = client._get_cache_affinity_handler()
        h2 = client._get_cache_affinity_handler()
        assert h1 is h2
        asyncio.run(client.close())
        assert h1.client.is_closed
        assert client._cache_affinity_handler is None


class TestFallbackStrip:
    async def test_non_anthropic_fallback_receives_stripped_copy(
        self, tmp_path, monkeypatch,
    ) -> None:
        """Anthropic 主模型失败回退 OpenAI 候选：候选收到无断点副本，
        原消息列表（与 think_loop 共享）不被改写。"""
        from unittest.mock import AsyncMock

        from agent.llm.llm_manager import LLMManager
        from agent.llm.types import ChatResult

        manager = LLMManager(str(tmp_path / "llm.json"))
        primary = LLMClient(LLMClientConfig(
            name="p", model="claude-sonnet-4", api_type="anthropic", provider_id="p",
        ))
        fallback = LLMClient(LLMClientConfig(
            name="f", model="qwen3", api_type="openai", provider_id="f",
        ))
        primary.chat = AsyncMock(side_effect=RuntimeError("boom"))
        fallback.chat = AsyncMock(return_value=ChatResult(content="ok"))
        monkeypatch.setattr(manager, "get_fallback_chat_clients", lambda **kw: [fallback])
        monkeypatch.setattr("agent.llm.llm_manager.asyncio.sleep", AsyncMock())

        marker = cache_marker()
        messages = [
            {"role": "system", "content": "人设", "cache_control": marker},
            {"role": "user", "content": "你好", "cache_control": marker},
        ]
        result = await manager.chat_with_fallback(
            messages, client=primary, max_retries=0, timeout=10,
        )
        assert result.content == "ok"
        # 主模型收到原始断点消息
        primary_msgs = primary.chat.await_args.args[0]
        assert count_breakpoints(primary_msgs) == 2
        # 回退候选收到剥离副本
        fallback_msgs = fallback.chat.await_args.args[0]
        assert count_breakpoints(fallback_msgs) == 0
        # 原列表不被改写
        assert count_breakpoints(messages) == 2

    async def test_anthropic_fallback_keeps_breakpoints(
        self, tmp_path, monkeypatch,
    ) -> None:
        """回退到另一个 Anthropic 候选：断点原样保留（同协议缓存设施）。"""
        from unittest.mock import AsyncMock

        from agent.llm.llm_manager import LLMManager
        from agent.llm.types import ChatResult

        manager = LLMManager(str(tmp_path / "llm.json"))
        primary = LLMClient(LLMClientConfig(
            name="p", model="claude-a", api_type="anthropic", provider_id="p",
        ))
        fallback = LLMClient(LLMClientConfig(
            name="f", model="claude-b", api_type="anthropic", provider_id="f",
        ))
        primary.chat = AsyncMock(side_effect=RuntimeError("boom"))
        fallback.chat = AsyncMock(return_value=ChatResult(content="ok"))
        monkeypatch.setattr(manager, "get_fallback_chat_clients", lambda **kw: [fallback])
        monkeypatch.setattr("agent.llm.llm_manager.asyncio.sleep", AsyncMock())

        messages = [
            {"role": "system", "content": "人设", "cache_control": cache_marker()},
            {"role": "user", "content": "你好"},
        ]
        result = await manager.chat_with_fallback(
            messages, client=primary, max_retries=0, timeout=10,
        )
        assert result.content == "ok"
        fallback_msgs = fallback.chat.await_args.args[0]
        assert count_breakpoints(fallback_msgs) == 1
