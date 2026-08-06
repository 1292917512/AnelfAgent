"""供应商侧缓存 token 解析单元测试（UsageInfo 扩展字段）。"""

from __future__ import annotations

from types import SimpleNamespace

from agent.llm.response_parsing import _usage_from_object
from agent.llm.responses.types import ResponseUsage
from agent.llm.types import UsageInfo


class TestChatCompletionsUsage:
    def test_anthropic_cache_fields(self) -> None:
        """Anthropic 直出 cache_read/cache_creation 字段。"""
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
        assert result.cache_hit_rate == 0.8

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
        """异常数据（read > prompt）时命中率截断到 1。"""
        usage = UsageInfo(prompt_tokens=10, cache_read_input_tokens=50)
        assert usage.cache_hit_rate == 1.0


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
