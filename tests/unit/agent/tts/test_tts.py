"""TTS 核心层测试：断句 / 朗读清洗 / 提供者降级 / 句级管线。"""

from __future__ import annotations

import pytest

from agent.tts import SentenceSplitter, TtsPipeline, get_tts_registry, strip_for_speech
from agent.tts.providers import TtsStream, synthesize_sentence


class TestStripForSpeech:
    def test_markdown_stripped(self) -> None:
        text = "# 标题\n**重要** 看[链接](http://x) `code` 吧"
        out = strip_for_speech(text)
        assert "重要" in out and "链接" in out
        assert "*" not in out and "[" not in out and "`" not in out

    def test_code_fence_replaced(self) -> None:
        out = strip_for_speech("看这个```python\nprint(1)\n```就好")
        assert "print" not in out and "代码略" in out

    def test_narration_stripped(self) -> None:
        out = strip_for_speech("你好*微笑*（轻声）世界")
        assert "微笑" not in out and "轻声" not in out
        assert "你好" in out and "世界" in out

    def test_cjk_space_normalized(self) -> None:
        assert strip_for_speech("你 好 吗") == "你好吗"

    def test_emoji_removed(self) -> None:
        out = strip_for_speech("好的😊没问题👌")
        assert "😊" not in out and "👌" not in out

    def test_symbol_only_lines_dropped(self) -> None:
        assert strip_for_speech("---\n***") == ""


class TestSentenceSplitter:
    def test_basic_cjk_sentences(self) -> None:
        sp = SentenceSplitter()
        assert sp.feed("你好呀。世界呀！") == ["你好呀。", "世界呀！"]
        assert sp.flush() == []

    def test_short_sentences_merge_into_previous(self) -> None:
        """短句（<4 字）并入前句合成一次调用（听感停顿归并，内容不丢）。"""
        sp = SentenceSplitter()
        out = sp.feed("你好。世界！")
        assert out == ["你好。世界！"]

    def test_partial_buffer_kept(self) -> None:
        sp = SentenceSplitter()
        assert sp.feed("你") == []
        assert sp.feed("好。") == ["你好。"]

    def test_abbreviation_not_split(self) -> None:
        sp = SentenceSplitter()
        out = sp.feed("Version 1.5 is out. Done.")
        assert out == ["Version 1.5 is out.", "Done."]

    def test_decimal_not_split(self) -> None:
        sp = SentenceSplitter()
        assert sp.feed("价格是 3.14 元") == []

    def test_soft_break_on_long_sentence(self) -> None:
        sp = SentenceSplitter()
        long_text = "一" * 100 + "，" + "二" * 100
        out = sp.feed(long_text)
        assert out and out[0].endswith("，")

    def test_first_sentence_soft_cut_before_full_stop(self) -> None:
        """首句在窗口内软切（不等句末标点）——开声延迟压到首个分句。"""
        sp = SentenceSplitter()
        assert sp.feed("嗯，我看一下这个问题") == []
        assert sp.feed("，然后") == []
        out = sp.feed("回复你一下嘛")
        assert out == ["嗯，我看一下这个问题，"]
        # 首句已产出 → 后续回到常规规则（等句末标点）
        assert sp.feed("好。") == ["然后回复你一下嘛好。"]

    def test_first_sentence_without_soft_break_waits(self) -> None:
        """窗口内无软切点不硬切：短无标点串整段保持，等句末或常规超长规则。"""
        sp = SentenceSplitter()
        assert sp.feed("一" * 50) == []
        assert sp.flush() == ["一" * 50]

    def test_flush_takes_remainder(self) -> None:
        sp = SentenceSplitter()
        sp.feed("没有标点的尾巴")
        assert sp.flush() == ["没有标点的尾巴"]

    def test_short_tail_merged(self) -> None:
        sp = SentenceSplitter()
        out = sp.feed("今天天气很好。嗯。")
        assert out == ["今天天气很好。嗯。"]


class FakeProvider:
    def __init__(self, name: str, priority: int, *,
                 available: bool = True, fail_first: bool = False,
                 fail_mid: bool = False, rate: int = 24000):
        self.name = name
        self.priority = priority
        self._available = available
        self._fail_first = fail_first
        self._fail_mid = fail_mid
        self._rate = rate
        self.calls = 0

    async def check_available(self) -> bool:
        return self._available

    def stream_synthesize(self, text: str, *, voice: str = "", sample_rate: int = 24000):
        self.calls += 1

        async def _chunks():
            if self._fail_first:
                raise RuntimeError("首块即失败")
            yield b"\x00\x01" * 100
            if self._fail_mid:
                raise RuntimeError("中途失败")
            yield b"\x02\x03" * 100

        return TtsStream(_chunks(), self._rate)


@pytest.fixture(autouse=True)
def clean_tts_registry():
    saved = get_tts_registry().list()
    get_tts_registry().reset()
    yield
    get_tts_registry().reset()
    for p in saved:
        get_tts_registry().register(p)


class TestProviderFailover:
    async def test_first_chunk_failure_falls_back(self) -> None:
        bad = FakeProvider("bad", 10, fail_first=True)
        good = FakeProvider("good", 20)
        get_tts_registry().register(bad)
        get_tts_registry().register(good)
        stream = await synthesize_sentence("你好")
        assert stream is not None
        chunks = [c async for c in stream.chunks]
        assert len(chunks) == 2
        assert good.calls == 1

    async def test_all_providers_fail_returns_none(self) -> None:
        get_tts_registry().register(FakeProvider("bad", 10, fail_first=True))
        assert await synthesize_sentence("你好") is None

    async def test_unavailable_provider_skipped(self) -> None:
        down = FakeProvider("down", 10, available=False)
        up = FakeProvider("up", 20)
        get_tts_registry().register(down)
        get_tts_registry().register(up)
        stream = await synthesize_sentence("你好")
        assert stream is not None and up.calls == 1


class TestPipeline:
    async def test_feed_finish_stream_order(self) -> None:
        get_tts_registry().register(FakeProvider("p", 10))
        pipe = TtsPipeline()
        pipe.feed("第一句。第二句！")
        pipe.feed("第三句。")
        pipe.finish()
        chunks = [c async for c in pipe.stream()]
        # 3 句 × 每句 2 块（fake 产两块）
        assert len(chunks) == 6
        assert all(rate == 24000 for rate, _ in chunks)

    async def test_narration_filtered_before_synth(self) -> None:
        provider = FakeProvider("p", 10)
        get_tts_registry().register(provider)
        seen: list[str] = []
        original = provider.stream_synthesize

        def spy(text: str, *, voice: str = "", sample_rate: int = 24000):
            seen.append(text)
            return original(text, voice=voice, sample_rate=sample_rate)

        provider.stream_synthesize = spy  # type: ignore[method-assign]
        pipe = TtsPipeline()
        pipe.feed("你好*微笑*世界。")
        pipe.finish()
        _ = [c async for c in pipe.stream()]
        assert seen == ["你好世界。"]

    async def test_cancel_stops_stream(self) -> None:
        get_tts_registry().register(FakeProvider("p", 10))
        pipe = TtsPipeline()
        pipe.feed("一。二。三。四。五。")
        pipe.finish()
        pipe.cancel()
        chunks = [c async for c in pipe.stream()]
        assert chunks == []

    async def test_failed_sentence_skipped_others_continue(self) -> None:
        get_tts_registry().register(FakeProvider("p", 10, fail_first=True))
        pipe = TtsPipeline()
        pipe.feed("会失败的句子。")
        pipe.finish()
        chunks = [c async for c in pipe.stream()]
        assert chunks == []
