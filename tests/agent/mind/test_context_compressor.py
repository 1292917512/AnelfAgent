"""上下文压缩管线（agent.mind.context_compressor）单元测试。"""

from __future__ import annotations

from typing import List
from unittest.mock import AsyncMock

from agent.mind.context_compressor import (
    CompressionConfig,
    ContextCompressor,
)


class _FakeMind:
    """最小 Mind 替身：提供窗口查询与摘要生成。"""

    def __init__(self, context_length: int = 10_000) -> None:
        self._context_length = context_length
        self.summarize_text = AsyncMock(return_value="【摘要】早期对话要点")

    def get_model_context_length(self) -> int:
        return self._context_length


def _make_messages(n: int, prefix: str = "消息") -> List[dict]:
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"{prefix}{i}"} for i in range(n)]


def _compressor(context_length: int = 10_000, **cfg) -> ContextCompressor:
    config = CompressionConfig(enabled=True, **cfg)
    return ContextCompressor(_FakeMind(context_length), config)


class TestThreshold:
    def test_threshold_from_window(self) -> None:
        c = _compressor(10_000, threshold_percent=0.75)
        assert c.threshold_tokens() == 7_500

    def test_unknown_window_no_threshold(self) -> None:
        c = _compressor(0)
        assert c.threshold_tokens() == 0

    def test_estimate_tokens(self) -> None:
        # tiktoken 可用时按真实分词计数（含每条消息结构开销），不可用时 chars/4
        msgs = [{"role": "user", "content": "a" * 400}]
        estimated = ContextCompressor.estimate_tokens(msgs)
        assert 50 <= estimated <= 500, "计数应在合理数量级内"

    def test_estimate_tokens_chinese_not_underestimated(self) -> None:
        """chars/4 对中文严重低估；tiktoken 下 100 字中文应明显超过 25 token。"""
        msgs = [{"role": "user", "content": "你好世界" * 25}]
        assert ContextCompressor.estimate_tokens(msgs) > 50

    def test_estimate_tokens_fallback(self, monkeypatch) -> None:
        """tiktoken 缺失时回退 chars/4 估算。"""
        import agent.mind.context_compressor as cc
        monkeypatch.setattr(cc, "_get_tiktoken_encoding", lambda: None)
        msgs = [{"role": "user", "content": "a" * 400}]
        assert ContextCompressor.estimate_tokens(msgs) == 100


class TestShouldCompress:
    def test_disabled(self) -> None:
        c = _compressor(10_000)
        c.config = CompressionConfig(enabled=False)
        assert not c.should_compress(_make_messages(100), last_prompt_tokens=999_999)

    def test_real_usage_triggers(self) -> None:
        c = _compressor(10_000, threshold_percent=0.75)
        assert c.should_compress([], last_prompt_tokens=8_000)
        assert not c.should_compress([], last_prompt_tokens=5_000)

    def test_estimated_fallback(self) -> None:
        c = _compressor(1_000, threshold_percent=0.75)
        big = [{"role": "user", "content": "x" * 8000}]  # tiktoken ~1000 / chars4 2000 tokens
        assert c.should_compress(big)

    def test_manual_request(self) -> None:
        c = _compressor(10_000)
        c.request_manual("scope1")
        assert c.should_compress([], scope="scope1")
        assert not c.should_compress([], scope="scope2")


class TestCompressMessages:
    async def test_compress_middle(self) -> None:
        c = _compressor(10_000, protect_first_n=2, protect_last_n=3)
        base = [{"role": "system", "content": "系统提示"}] + [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"历史{i} " + "内容" * 200}
            for i in range(10)
        ]
        chain = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"工具链{i} " + "数据" * 200}
            for i in range(6)
        ]

        new_base, new_chain = await c.compress_messages(base, chain, scope="s")

        # 头部 system 保留
        assert new_base[0]["content"] == "系统提示"
        # 保首轮 2 条
        assert new_base[1]["content"].startswith("历史0")
        # 压缩反馈注入
        feedback = [m for m in new_base if "上下文压缩" in str(m.get("content", ""))]
        assert feedback and "【摘要】" in feedback[0]["content"]
        # 保尾轮
        assert len(new_chain) <= 3 + 6 - 3  # 尾部来自 compressible 末尾
        # 指标记录
        assert c.metrics.total_compressions == 1
        assert c.metrics.last_after_tokens < c.metrics.last_before_tokens

    async def test_manual_request_consumed(self) -> None:
        c = _compressor(10_000)
        c.request_manual("s")
        base = [{"role": "system", "content": "sys"}] + _make_messages(12)
        await c.compress_messages(base, [], scope="s")
        assert not c.should_compress([], scope="s")

    async def test_too_few_messages_skipped(self) -> None:
        c = _compressor(10_000)
        base = [{"role": "system", "content": "sys"}] + _make_messages(3)
        new_base, new_chain = await c.compress_messages(base, [], scope="s")
        assert new_base == base and new_chain == []
        assert c.metrics.total_compressions == 0

    async def test_orphan_tool_messages_removed(self) -> None:
        c = _compressor(10_000, protect_first_n=1, protect_last_n=3)
        base = [{"role": "system", "content": "sys"}] + _make_messages(8)
        chain = [
            {"role": "tool", "tool_call_id": "x", "content": "孤儿结果"},
            {"role": "assistant", "content": "最近回复"},
            {"role": "user", "content": "最新消息"},
        ]
        _, new_chain = await c.compress_messages(base, chain, scope="s")
        assert new_chain[0]["role"] != "tool"

    async def test_fallback_summary_on_llm_failure(self) -> None:
        mind = _FakeMind(10_000)
        mind.summarize_text = AsyncMock(side_effect=RuntimeError("LLM 不可用"))
        c = ContextCompressor(mind, CompressionConfig(enabled=True, protect_first_n=1, protect_last_n=2))
        base = [{"role": "system", "content": "sys"}] + _make_messages(10)
        new_base, _ = await c.compress_messages(base, [], scope="s")
        feedback = [m for m in new_base if "上下文压缩" in str(m.get("content", ""))]
        assert feedback, "摘要失败时应回退确定性摘要"
        assert c.metrics.failures == 1


class TestUserMessagePreservation:
    """用户原话保护：中间段真 user 消息原文保留，仅执行块进摘要（参考 Mini-Agent）。

    真用户消息 = 渠道到达、content 前缀带元数据标签（[time:…][uid:…] 等）；
    机器生成的 user 角色消息（proactive 指令/prefill 修复的独白）随摘要压缩。
    """

    @staticmethod
    def _user(text: str) -> dict:
        """模拟真实入库的用户消息（到达标签前缀 + 正文）。"""
        return {"role": "user", "content": f"[time:2026年07月21日23时][uid:10001] {text}"}

    async def test_user_messages_preserved_verbatim(self) -> None:
        mind = _FakeMind(10_000)
        c = ContextCompressor(mind, CompressionConfig(
            enabled=True, protect_first_n=1, protect_last_n=2, min_compressible=4,
        ))
        base = [{"role": "system", "content": "sys"}]
        genuine = self._user("我喜欢在周五晚上看电影，记住哦")
        compressible = [
            self._user("首轮问题"),
            {"role": "assistant", "content": "首轮回答 " + "内容" * 100},
            genuine,
            {"role": "assistant", "content": "好的已记住 " + "内容" * 100},
            self._user("最近问题"),
            {"role": "assistant", "content": "最近回答"},
        ]
        new_base, _ = await c.compress_messages(base, compressible, scope="s")
        # 中间段用户原话完整保留在 new_base 中
        assert any(m.get("content") == genuine["content"] for m in new_base)
        # 摘要 prompt 不再包含用户原话（仅执行块进摘要）
        prompt = mind.summarize_text.call_args[0][0]
        conversation_part = prompt.split("[待压缩对话]")[-1]
        assert "我喜欢在周五晚上看电影" not in conversation_part
        # 压缩反馈提示用户原话已保留
        feedback = [m for m in new_base if "上下文压缩" in str(m.get("content", ""))]
        assert feedback and "完整保留" in feedback[0]["content"]

    async def test_machine_user_messages_compressed(self) -> None:
        """机器生成的 user 角色消息（无到达标签）不属于用户原话，随摘要正常压缩。"""
        mind = _FakeMind(10_000)
        c = ContextCompressor(mind, CompressionConfig(
            enabled=True, protect_first_n=1, protect_last_n=2, min_compressible=4,
        ))
        base = [{"role": "system", "content": "sys"}]
        proactive = {"role": "user", "content": "你要主动联系 user_1。原因：主动关心。请自然地说话。"}
        compressible = [
            self._user("首轮问题"),
            {"role": "assistant", "content": "首轮回答 " + "内容" * 100},
            proactive,
            {"role": "assistant", "content": "执行过程 " + "内容" * 100},
            self._user("最近问题"),
            {"role": "assistant", "content": "最近回答"},
        ]
        new_base, _ = await c.compress_messages(base, compressible, scope="s")
        # proactive 指令不被原文保留
        assert not any(m.get("content") == proactive["content"] for m in new_base)
        # 而是进入摘要输入
        prompt = mind.summarize_text.call_args[0][0]
        assert "你要主动联系" in prompt

    async def test_prefill_fixed_monologue_compressed(self) -> None:
        """prefill 修复被改写为 user 的 assistant 独白（无到达标签）随摘要压缩。"""
        mind = _FakeMind(10_000)
        c = ContextCompressor(mind, CompressionConfig(
            enabled=True, protect_first_n=1, protect_last_n=2, min_compressible=4,
        ))
        base = [{"role": "system", "content": "sys"}]
        monologue = {"role": "user", "content": "[内心独白] 我应该先查一下资料再回复"}
        compressible = [
            self._user("首轮问题"),
            {"role": "assistant", "content": "首轮回答 " + "内容" * 100},
            monologue,
            {"role": "assistant", "content": "执行过程 " + "内容" * 100},
            self._user("最近问题"),
            {"role": "assistant", "content": "最近回答"},
        ]
        new_base, _ = await c.compress_messages(base, compressible, scope="s")
        assert not any(m.get("content") == monologue["content"] for m in new_base)
        prompt = mind.summarize_text.call_args[0][0]
        assert "内心独白" in prompt

    async def test_user_max_chars_truncation(self) -> None:
        """超长用户消息截断兜底，防超大粘贴常驻上下文。"""
        mind = _FakeMind(10_000)
        c = ContextCompressor(mind, CompressionConfig(
            enabled=True, protect_first_n=1, protect_last_n=2,
            min_compressible=4, user_max_chars=80,
        ))
        base = [{"role": "system", "content": "sys"}]
        compressible = [
            self._user("首轮"),
            {"role": "assistant", "content": "回答 " + "内容" * 100},
            self._user("粘贴" * 100),
            {"role": "assistant", "content": "回答2 " + "内容" * 100},
            self._user("最近"),
            {"role": "assistant", "content": "回答3"},
        ]
        new_base, _ = await c.compress_messages(base, compressible, scope="s")
        preserved = [m for m in new_base if m.get("role") == "user" and "已截断" in str(m.get("content", ""))]
        assert preserved and len(preserved[0]["content"]) < 130

    async def test_disabled_falls_back_to_summary(self) -> None:
        """keep_user_messages=False 时退回旧行为：user 消息一并进摘要。"""
        mind = _FakeMind(10_000)
        c = ContextCompressor(mind, CompressionConfig(
            enabled=True, protect_first_n=1, protect_last_n=2,
            min_compressible=4, keep_user_messages=False,
        ))
        base = [{"role": "system", "content": "sys"}]
        genuine = self._user("中期追问的细节")
        compressible = [
            self._user("首轮"),
            {"role": "assistant", "content": "回答 " + "内容" * 100},
            genuine,
            {"role": "assistant", "content": "回答2 " + "内容" * 100},
            self._user("最近"),
            {"role": "assistant", "content": "回答3"},
        ]
        new_base, _ = await c.compress_messages(base, compressible, scope="s")
        assert not any(m.get("content") == genuine["content"] for m in new_base)
        prompt = mind.summarize_text.call_args[0][0]
        assert "中期追问的细节" in prompt

    async def test_only_user_messages_no_summary_call(self) -> None:
        """中间段只剩用户原话时无需摘要（原文保留即无损），不调用 LLM。"""
        mind = _FakeMind(10_000)
        c = ContextCompressor(mind, CompressionConfig(
            enabled=True, protect_first_n=1, protect_last_n=1, min_compressible=3,
        ))
        base = [
            {"role": "system", "content": "sys"},
            self._user("首条"),
            self._user("中期原话一"),
            self._user("中期原话二"),
            {"role": "assistant", "content": "尾部回答"},
        ]
        new_base, _ = await c.compress_messages(base, [], scope="s")
        mind.summarize_text.assert_not_called()
        assert any("中期原话一" in str(m.get("content", "")) for m in new_base)
        assert any("中期原话二" in str(m.get("content", "")) for m in new_base)


class TestIterativeSummary:
    """迭代压缩：前次摘要作为输入更新，而非混在对话里被二次摘要。"""

    async def test_previous_summary_iterated_not_re_summarized(self) -> None:
        mind = _FakeMind(10_000)
        c = ContextCompressor(mind, CompressionConfig(enabled=True, protect_first_n=1, protect_last_n=2))
        base1 = [{"role": "system", "content": "sys"}] + _make_messages(12, "第一轮")
        new_base, _ = await c.compress_messages(base1, [], scope="s")
        feedback = [m for m in new_base if str(m.get("content", "")).startswith("[上下文压缩]")]
        assert feedback

        # 第二轮：前次反馈消息 + 更多新消息
        base2 = [{"role": "system", "content": "sys"}] + new_base[1:] + _make_messages(12, "第二轮")
        await c.compress_messages(base2, [], scope="s")

        prompt = mind.summarize_text.call_args_list[-1].args[0]
        # 前次摘要以迭代块形式传入
        assert "前次摘要" in prompt
        assert "【摘要】早期对话要点" in prompt
        # 前次反馈消息不再作为普通对话进入待压缩片段
        conversation_part = prompt.split("[待压缩对话]")[-1]
        assert "上下文压缩" not in conversation_part

    async def test_previous_summary_reused_without_fresh(self) -> None:
        """中间段只剩前次摘要时：直接沿用，不再调用 LLM。"""
        mind = _FakeMind(10_000)
        c = ContextCompressor(mind, CompressionConfig(
            enabled=True, protect_first_n=1, protect_last_n=2, min_compressible=4,
        ))
        previous_feedback = {
            "role": "system",
            "content": "[上下文压缩] 为节省上下文空间，之前 8 条对话已压缩为以下摘要。"
                       "其中包含未完成任务与关键信息，请基于摘要继续：\n【摘要】既有要点",
        }
        base = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "首条"},
            previous_feedback,
            {"role": "user", "content": "尾一"},
            {"role": "assistant", "content": "尾二"},
        ]
        new_base, _ = await c.compress_messages(base, [], scope="s")
        mind.summarize_text.assert_not_called()
        feedback = [m for m in new_base if "上下文压缩" in str(m.get("content", ""))]
        assert feedback and "【摘要】既有要点" in feedback[0]["content"]


class TestToolResultFolding:
    """工具结果规则折叠（摘要输入 + 尾部常驻）。"""

    def test_render_folds_tool_results(self) -> None:
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "t1", "function": {"name": "web_search", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "t1", "content": "很长的搜索结果" * 50},
        ]
        text = ContextCompressor._render_for_summary(msgs)
        assert "[工具结果] web_search:" in text
        assert "原文" in text
        assert len(text) < 500

    def test_render_preserves_error_reason(self) -> None:
        import json
        msgs = [{"role": "tool", "tool_call_id": "t1", "content": json.dumps({"error": "连接超时"})}]
        text = ContextCompressor._render_for_summary(msgs)
        assert "执行失败" in text and "连接超时" in text

    def test_render_dedupes_identical_results(self) -> None:
        msgs = [
            {"role": "tool", "tool_call_id": "t1", "content": "相同结果"},
            {"role": "tool", "tool_call_id": "t2", "content": "相同结果"},
        ]
        text = ContextCompressor._render_for_summary(msgs)
        assert "与上文重复" in text

    async def test_tail_folding_keeps_recent_full(self) -> None:
        c = _compressor(10_000, protect_first_n=1, protect_last_n=8, tool_result_fold_keep=2)
        base = [{"role": "system", "content": "sys"}] + _make_messages(10)
        chain: List[dict] = []
        for i in range(6):
            chain.append({"role": "assistant", "content": "", "tool_calls": [
                {"id": f"t{i}", "function": {"name": "web_search", "arguments": "{}"}},
            ]})
            chain.append({"role": "tool", "tool_call_id": f"t{i}", "content": f"结果{i}-" + "数据" * 100})

        _, new_chain = await c.compress_messages(base, chain, scope="s")
        tool_msgs = [m for m in new_chain if m.get("role") == "tool"]
        assert len(tool_msgs) == 4
        # 较早的 2 条被折叠为单行摘要，最新 2 条保留完整原文
        assert tool_msgs[0]["content"].startswith("[工具结果]")
        assert tool_msgs[1]["content"].startswith("[工具结果]")
        assert tool_msgs[-1]["content"] == "结果5-" + "数据" * 100
        assert tool_msgs[-2]["content"] == "结果4-" + "数据" * 100


class TestFocusTopic:
    async def test_focus_directive_in_summary_prompt(self) -> None:
        """手动压缩带焦点时，摘要 prompt 包含焦点指令。"""
        mind = _FakeMind(10_000)
        c = ContextCompressor(mind, CompressionConfig(enabled=True, protect_first_n=1, protect_last_n=2))
        c.request_manual("s", focus_topic="发布计划")
        base = [{"role": "system", "content": "sys"}] + _make_messages(12)
        await c.compress_messages(base, [], scope="s")
        prompt = mind.summarize_text.call_args[0][0]
        assert "发布计划" in prompt
        assert "压缩焦点" in prompt

    async def test_no_focus_no_directive(self) -> None:
        mind = _FakeMind(10_000)
        c = ContextCompressor(mind, CompressionConfig(enabled=True, protect_first_n=1, protect_last_n=2))
        base = [{"role": "system", "content": "sys"}] + _make_messages(12)
        await c.compress_messages(base, [], scope="s")
        prompt = mind.summarize_text.call_args[0][0]
        assert "压缩焦点" not in prompt

    async def test_manual_focus_consumed_once(self) -> None:
        """焦点随手动请求消费一次，后续自动压缩不带焦点。"""
        mind = _FakeMind(10_000)
        c = ContextCompressor(mind, CompressionConfig(enabled=True, protect_first_n=1, protect_last_n=2))
        c.request_manual("s", focus_topic="预算")
        base = [{"role": "system", "content": "sys"}] + _make_messages(12)
        await c.compress_messages(base, [], scope="s")
        await c.compress_messages(base, [], scope="s")
        assert "预算" not in mind.summarize_text.call_args[0][0]

    def test_explicit_focus_param(self) -> None:
        """focus_topic 也可作为参数直接传入（非手动请求路径）。"""
        c = _compressor(10_000)
        c.request_manual("s")  # 无焦点手动请求
        assert c.pop_manual_focus("s") == ""
        assert c.pop_manual_focus("s") is None  # 已消费


class TestScopeLock:
    async def test_same_scope_serialized(self) -> None:
        """同一 scope 的压缩经锁串行，锁对象按 scope 独立。"""
        c = _compressor(10_000)
        lock_a1 = c.scope_lock("a")
        lock_a2 = c.scope_lock("a")
        lock_b = c.scope_lock("b")
        assert lock_a1 is lock_a2, "同 scope 应返回同一把锁"
        assert lock_a1 is not lock_b, "不同 scope 锁应独立"

    async def test_lock_actually_excludes(self) -> None:
        import asyncio
        c = _compressor(10_000)
        lock = c.scope_lock("x")
        order: list[str] = []

        async def worker(tag: str) -> None:
            async with lock:
                order.append(f"{tag}-in")
                await asyncio.sleep(0.01)
                order.append(f"{tag}-out")

        await asyncio.gather(worker("1"), worker("2"))
        # 串行：一个完整进出后另一个才进入
        assert order in (
            ["1-in", "1-out", "2-in", "2-out"],
            ["2-in", "2-out", "1-in", "1-out"],
        )


class TestHeadOrphanRepair:
    async def test_dangling_tool_calls_repaired(self) -> None:
        """head 末尾 assistant 的 tool_calls 结果若落入 middle，
        压缩后序列不得残留悬空调用。"""
        mind = _FakeMind(10_000)
        c = ContextCompressor(mind, CompressionConfig(
            enabled=True, protect_first_n=2, protect_last_n=2, min_compressible=4,
        ))
        base = [{"role": "system", "content": "sys"}]
        compressible = [
            {"role": "user", "content": "早期问题"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call_1", "function": {"name": "web_search", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": "搜索结果"},
            {"role": "assistant", "content": "搜索后的回答"},
            {"role": "user", "content": "中期追问"},
            {"role": "assistant", "content": "中期回答"},
            {"role": "user", "content": "最近问题"},
            {"role": "assistant", "content": "最近回答"},
        ]
        new_base, new_chain = await c.compress_messages(base, compressible, scope="s")
        seq = new_base + new_chain
        for i, msg in enumerate(seq):
            if msg.get("tool_calls"):
                # 该 assistant 之后必须紧跟 tool 结果，不得悬空
                assert i + 1 < len(seq) and seq[i + 1].get("role") == "tool", \
                    f"发现悬空 tool_calls: index={i}"

    def test_repair_moves_matching_tool_into_head(self) -> None:
        """middle 开头恰为对应 tool 结果时并入 head。"""
        head = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
        ]
        middle = [
            {"role": "tool", "tool_call_id": "c1", "content": "r"},
            {"role": "assistant", "content": "a"},
        ]
        new_head, new_middle = ContextCompressor._repair_head_orphans(head, middle)
        assert len(new_head) == 3 and new_head[-1]["role"] == "tool"
        assert len(new_middle) == 1

    def test_repair_demotes_unmatched_assistant(self) -> None:
        """tool 结果不在 middle 开头时，悬空 assistant 下放进 middle。"""
        head = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
        ]
        middle = [{"role": "assistant", "content": "其他内容"}]
        new_head, new_middle = ContextCompressor._repair_head_orphans(head, middle)
        assert not new_head[-1].get("tool_calls")
        assert new_middle[0].get("tool_calls"), "被下放的 assistant 应在 middle 开头"
