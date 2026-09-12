"""供应商侧缓存 token 解析单元测试（UsageInfo 扩展字段）。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.llm.response_parsing import _usage_from_object
from agent.llm.responses.types import ResponseUsage
from agent.llm.types import UsageInfo


class TestChatCompletionsUsage:
    def test_anthropic_native_excludes_cache(self) -> None:
        """原生 Anthropic 记账（直出 cache 字段、无 details 包装）：
        prompt_tokens 不含缓存，命中率分母须补回 read+creation。"""
        usage = SimpleNamespace(
            prompt_tokens=1000,
            completion_tokens=50,
            total_tokens=1050,
            cache_read_input_tokens=800,
            cache_creation_input_tokens=150,
        )
        result = _usage_from_object(usage)
        assert result is not None
        assert result.cache_read_input_tokens == 800
        assert result.cache_creation_input_tokens == 150
        assert result.prompt_includes_cache is False
        assert result.total_input_tokens == 1950
        # 800 / (1000 + 800 + 150)，而非 800/1000
        assert result.cache_hit_rate == pytest.approx(800 / 1950)

    def test_anthropic_litellm_transformed_includes_cache(self) -> None:
        """经 litellm 变换的 Anthropic usage：缓存已加回 prompt_tokens
        且附 details 包装——分母不再补回（防双重计数）。"""
        usage = SimpleNamespace(
            prompt_tokens=1950,
            completion_tokens=50,
            total_tokens=2000,
            cache_read_input_tokens=800,
            cache_creation_input_tokens=150,
            prompt_tokens_details=SimpleNamespace(cached_tokens=800),
        )
        result = _usage_from_object(usage)
        assert result is not None
        assert result.prompt_includes_cache is True
        assert result.total_input_tokens == 1950
        assert result.cache_hit_rate == pytest.approx(800 / 1950)

    def test_deepseek_hit_rate(self) -> None:
        """DeepSeek 磁盘缓存：prompt_cache_hit_tokens，prompt 含命中。"""
        usage = SimpleNamespace(
            prompt_tokens=2000,
            completion_tokens=100,
            total_tokens=2100,
            prompt_cache_hit_tokens=500,
        )
        result = _usage_from_object(usage)
        assert result is not None
        assert result.prompt_includes_cache is True
        assert result.cache_hit_rate == pytest.approx(0.25)

    def test_openai_cached_tokens_mapping(self) -> None:
        """OpenAI prompt_tokens_details.cached_tokens 映射到 cache_read。"""
        usage = SimpleNamespace(
            prompt_tokens=2000,
            completion_tokens=100,
            total_tokens=2100,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
            prompt_tokens_details=SimpleNamespace(cached_tokens=1536),
        )
        result = _usage_from_object(usage)
        assert result is not None
        assert result.cache_read_input_tokens == 1536
        assert result.cache_creation_input_tokens == 0

    def test_dict_usage_with_details(self) -> None:
        """dict 形态 usage（部分 provider 透传 dict）也能解析。"""
        usage = {
            "prompt_tokens": 500,
            "completion_tokens": 20,
            "total_tokens": 520,
            "prompt_tokens_details": {"cached_tokens": 256},
        }
        result = _usage_from_object(usage)
        assert result is not None
        assert result.cache_read_input_tokens == 256

    def test_no_cache_fields_defaults_zero(self) -> None:
        """无缓存协议的供应商：缓存字段为 0，不影响原有字段。"""
        usage = SimpleNamespace(prompt_tokens=100, completion_tokens=10, total_tokens=110)
        result = _usage_from_object(usage)
        assert result is not None
        assert result.cache_read_input_tokens == 0
        assert result.cache_creation_input_tokens == 0
        assert result.cache_hit_rate == 0.0


class TestResponsesUsage:
    def test_input_tokens_details(self) -> None:
        """Responses 协议 input_tokens_details.cached_tokens。"""
        usage = ResponseUsage(
            input_tokens=1000,
            output_tokens=50,
            total_tokens=1050,
            raw={"input_tokens": 1000, "input_tokens_details": {"cached_tokens": 700}},
        )
        info = usage.to_usage_info()
        assert info.cache_read_input_tokens == 700
        assert info.prompt_tokens == 1000

    def test_anthropic_style_raw(self) -> None:
        """Responses 协议下 Anthropic 风格直出字段。"""
        usage = ResponseUsage(
            input_tokens=1000,
            output_tokens=50,
            total_tokens=1050,
            raw={"cache_read_input_tokens": 300, "cache_creation_input_tokens": 100},
        )
        info = usage.to_usage_info()
        assert info.cache_read_input_tokens == 300
        assert info.cache_creation_input_tokens == 100


class TestCacheHitRate:
    def test_zero_prompt_tokens(self) -> None:
        assert UsageInfo().cache_hit_rate == 0.0

    def test_capped_at_one(self) -> None:
        """read + creation ≤ prompt 的边界数据不触发口径校正。"""
        usage = UsageInfo(prompt_tokens=100, cache_read_input_tokens=100)
        assert usage.prompt_includes_cache is True
        assert usage.cache_hit_rate == 1.0


class TestCaliberConflictGuard:
    """记账口径自洽校验：read+creation > prompt ⇒ prompt 不可能含缓存，
    强制归为不含口径（GLM/DeepSeek 部分端点实测 read 超 prompt 1.9 倍，
    不校正则命中率被钳成 100% 虚报）。"""

    def test_conflict_flips_to_exclusive(self) -> None:
        usage = UsageInfo(prompt_tokens=56265, cache_read_input_tokens=73216)
        assert usage.prompt_includes_cache is False
        assert usage.total_input_tokens == 56265 + 73216
        assert usage.cache_hit_rate == pytest.approx(73216 / (56265 + 73216))

    def test_conflict_via_details_wrapper(self) -> None:
        """GLM 形态：带 details 包装被字段规则判为含缓存，但 read > prompt
        暴露端点实际按不含记账——构造时即校正。"""
        usage = SimpleNamespace(
            prompt_tokens=56265,
            completion_tokens=500,
            total_tokens=56765,
            prompt_tokens_details=SimpleNamespace(cached_tokens=73216),
        )
        result = _usage_from_object(usage)
        assert result is not None
        assert result.prompt_includes_cache is False
        assert result.cache_hit_rate == pytest.approx(73216 / 129481)

    def test_legit_includes_not_flipped(self) -> None:
        """含缓存口径下 read+creation ≤ prompt 是合法常态，不得误校正。"""
        usage = UsageInfo(
            prompt_tokens=1950,
            cache_read_input_tokens=800,
            cache_creation_input_tokens=150,
        )
        assert usage.prompt_includes_cache is True
        assert usage.total_input_tokens == 1950


class TestMergeSinkRawPreferred:
    """旁路原始 usage 优先（litellm 对未收录模型伪造流式 usage 的根治）。

    2026-09 实证：litellm 1.100 对 openai/glm-5.3 的流式 chunk 用本地
    tiktoken 估算伪造 usage（prompt 虚高 1.8 倍、completion 清零、缓存
    details 丢弃），旁路只补缓存字段会造成真实 read ÷ 伪造 prompt 的
    尺度混血，命中率被拉向 ~50% 的数学假象。
    """

    def test_raw_usage_preferred_over_fabricated(self) -> None:
        """主路为 litellm 伪造值、旁路有原始真实值：全字段以原始值为准。"""
        from agent.llm.response_parsing import _merge_sink
        fabricated = UsageInfo(
            prompt_tokens=41428, completion_tokens=0, total_tokens=41428,
            cache_observable=False,
        )
        sink = {
            "prompt": 22819, "completion": 100, "total": 22919,
            "read": 22784, "fields": True, "includes": True,
        }
        merged = _merge_sink(fabricated, sink)
        assert merged is not None
        assert merged.prompt_tokens == 22819
        assert merged.completion_tokens == 100
        assert merged.total_tokens == 22919
        assert merged.cache_read_input_tokens == 22784
        assert merged.prompt_includes_cache is True
        assert merged.cache_observable is True
        assert merged.cache_hit_rate == pytest.approx(22784 / 22819, abs=1e-4)

    def test_raw_cold_miss_is_real_zero(self) -> None:
        """原始 usage 有缓存字段但命中为 0：真实未命中（0%），非不可观测。"""
        from agent.llm.response_parsing import _merge_sink
        fabricated = UsageInfo(
            prompt_tokens=41428, completion_tokens=0, total_tokens=41428,
            cache_observable=False,
        )
        sink = {"prompt": 22819, "completion": 100, "total": 22919,
                "fields": True, "includes": True}
        merged = _merge_sink(fabricated, sink)
        assert merged is not None
        assert merged.cache_read_input_tokens == 0
        assert merged.cache_observable is True
        assert merged.cache_hit_rate == 0.0

    def test_main_missing_constructed_from_sink(self) -> None:
        """主路 usage 缺失而旁路见过原始 usage：以旁路值构造，不丢真实用量。"""
        from agent.llm.response_parsing import _merge_sink
        sink = {"prompt": 22819, "completion": 100, "total": 22919,
                "read": 22784, "fields": True, "includes": True}
        merged = _merge_sink(None, sink)
        assert merged is not None
        assert merged.prompt_tokens == 22819
        assert merged.cache_read_input_tokens == 22784
        assert merged.cache_hit_rate == pytest.approx(22784 / 22819, abs=1e-4)

    def test_main_missing_without_sink_stays_none(self) -> None:
        """主路缺失且旁路未见原始 usage：保持 None（不可观测）。"""
        from agent.llm.response_parsing import _merge_sink
        assert _merge_sink(None, None) is None
        assert _merge_sink(None, {"read": 100}) is None

    def test_anthropic_native_caliber_from_raw(self) -> None:
        """原始 usage 为 Anthropic 原生形态（无 details 包装）：
        口径随原始对象判定为 prompt 不含缓存，无需数值守卫翻转。"""
        from agent.llm.response_parsing import _merge_sink
        fabricated = UsageInfo(
            prompt_tokens=500, completion_tokens=0, total_tokens=500,
            cache_observable=False,
        )
        sink = {"prompt": 300, "completion": 20, "read": 700, "fields": True,
                "includes": False}
        merged = _merge_sink(fabricated, sink)
        assert merged is not None
        assert merged.prompt_tokens == 300
        assert merged.cache_read_input_tokens == 700
        assert merged.prompt_includes_cache is False
        assert merged.total_input_tokens == 300 + 700


class TestInstallUsageTap:
    async def test_tap_captures_full_raw_usage(self) -> None:
        """旁路从原始 chunk 捕获全量真实字段（prompt/completion/total/缓存/口径）。"""
        from agent.llm.response_parsing import install_usage_tap

        raw_usage = SimpleNamespace(
            prompt_tokens=22819, completion_tokens=100, total_tokens=22919,
            prompt_tokens_details=SimpleNamespace(cached_tokens=22784),
        )

        async def raw_stream():
            yield SimpleNamespace(usage=None)
            yield SimpleNamespace(usage=raw_usage)

        stream = SimpleNamespace(completion_stream=raw_stream())
        sink = install_usage_tap(stream)
        assert sink is not None
        async for _ in stream.completion_stream:
            pass
        assert sink == {
            "seen": True, "prompt": 22819, "completion": 100, "total": 22919,
            "includes": True, "fields": True, "read": 22784,
        }

    def test_tap_not_installed_without_completion_stream(self) -> None:
        """litellm 内部结构变化（无 completion_stream）时不装旁路，优雅降级。"""
        from agent.llm.response_parsing import install_usage_tap
        assert install_usage_tap(SimpleNamespace()) is None


class TestStreamUsageChunk:
    async def test_usage_chunk_with_empty_choice(self) -> None:
        """阿里 anthropic 网关形态：finish chunk 之后再发一个带空 choice、
        finish=None 的 usage chunk——usage 必须透传（回归：此前被静默丢弃）。"""
        from agent.llm.response_parsing import _iter_stream

        def _chunk(content="", finish=None, usage=None):
            delta = SimpleNamespace(
                content=content, reasoning_content=None,
                reasoning_details=None, tool_calls=None,
            )
            return SimpleNamespace(
                choices=[SimpleNamespace(delta=delta, finish_reason=finish)],
                usage=usage,
            )

        usage = SimpleNamespace(
            prompt_tokens=67, completion_tokens=18, total_tokens=85,
            cache_read_input_tokens=40, cache_creation_input_tokens=0,
        )
        chunks = [
            _chunk(content="好"),
            _chunk(content="", finish="stop"),
            _chunk(content="", finish=None, usage=usage),  # 空 choice + finish=None + usage
        ]

        async def fake_stream():
            for c in chunks:
                yield c

        collected = []
        async for delta, _buf in _iter_stream(fake_stream(), "", {}):
            collected.append(delta)

        final_usage = [d.usage for d in collected if d.usage is not None]
        assert final_usage, "usage chunk 必须透传"
        assert final_usage[-1].cache_read_input_tokens == 40
        assert final_usage[-1].prompt_tokens == 67


class TestDeclarativeFieldTable:
    def test_deepseek_fields(self) -> None:
        """DeepSeek prompt_cache_hit_tokens 经注册表解析（零分支扩展）。"""
        from agent.llm.types import cache_tokens_from_usage

        read, creation = cache_tokens_from_usage(
            SimpleNamespace(prompt_cache_hit_tokens=5120)
        )
        assert read == 5120 and creation == 0

    def test_field_path_priority(self) -> None:
        """多字段同时存在时按注册表声明顺序取第一个非零值。"""
        from agent.llm.types import cache_tokens_from_usage

        read, _ = cache_tokens_from_usage({
            "cache_read_input_tokens": 100,
            "prompt_cache_hit_tokens": 200,
        })
        assert read == 100

    def test_nested_details_path(self) -> None:
        from agent.llm.types import cache_tokens_from_usage

        read, _ = cache_tokens_from_usage({
            "prompt_tokens_details": {"cached_tokens": 256},
        })
        assert read == 256
