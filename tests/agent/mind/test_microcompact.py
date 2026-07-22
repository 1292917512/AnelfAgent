"""Microcompact / 压缩熔断 / rehydration 测试。"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Dict, List

import pytest

from agent.mind.context_compressor import CompressionConfig, ContextCompressor


def _compressor(**cfg) -> ContextCompressor:
    mind = SimpleNamespace(get_model_context_length=lambda: 100000)
    return ContextCompressor(mind, CompressionConfig(**cfg))


def _chain(pairs: int, tool_name: str = "read_file", content_len: int = 500) -> List[Dict]:
    chain: List[Dict] = []
    for i in range(pairs):
        chain.append({
            "role": "assistant", "content": None,
            "tool_calls": [{"id": f"c{i}", "type": "function",
                            "function": {"name": tool_name, "arguments": "{}"}}],
        })
        chain.append({"role": "tool", "tool_call_id": f"c{i}", "content": "x" * content_len})
    return chain


class TestMicrocompact:
    def test_old_readonly_results_cleared(self):
        c = _compressor(microcompact_chain_threshold=10, microcompact_keep_recent=2)
        chain = _chain(6)  # 12 条消息
        cleared = c.microcompact(chain)
        assert cleared == 4  # 6 个结果 - 保留最新 2 个
        tool_msgs = [m for m in chain if m["role"] == "tool"]
        assert tool_msgs[-1]["content"] == "x" * 500
        assert tool_msgs[-2]["content"] == "x" * 500
        for m in tool_msgs[:-2]:
            assert m["content"] == ContextCompressor._MICROCOMPACT_PLACEHOLDER

    def test_write_tool_results_preserved(self):
        c = _compressor(microcompact_chain_threshold=4, microcompact_keep_recent=1)
        chain = _chain(4, tool_name="write_file")
        assert c.microcompact(chain) == 0

    def test_short_chain_untouched(self):
        c = _compressor(microcompact_chain_threshold=40)
        chain = _chain(5)
        assert c.microcompact(chain) == 0

    def test_short_results_not_cleared(self):
        c = _compressor(microcompact_chain_threshold=4, microcompact_keep_recent=1)
        chain = _chain(4, content_len=50)
        assert c.microcompact(chain) == 0

    def test_disabled_with_zero_threshold(self):
        c = _compressor(microcompact_chain_threshold=0)
        assert c.microcompact(_chain(30)) == 0


class TestCircuitBreaker:
    def test_broken_after_consecutive_failures(self):
        c = _compressor(max_consecutive_failures=3)
        assert c.should_compress([], last_prompt_tokens=80000)
        for _ in range(3):
            c._record_compress_result(False)
        assert c._broken
        assert not c.should_compress([], last_prompt_tokens=80000)

    def test_success_resets_counter(self):
        c = _compressor(max_consecutive_failures=3)
        c._record_compress_result(False)
        c._record_compress_result(False)
        c._record_compress_result(True)
        c._record_compress_result(False)
        assert not c._broken


class TestRehydration:
    def test_rehydrate_recent_files(self, tmp_path, monkeypatch):
        from agent.mind.tools.think_loop import _rehydrate_recent_files
        from entities.filesystem import file_state

        monkeypatch.setattr(file_state, "get_current_scope", lambda: "_rehy")
        file_state.clear_scope("_rehy")
        fp = tmp_path / "work.py"
        fp.write_text("print('hello')")
        file_state.record_read(str(fp), "print('hello')", os.path.getmtime(fp))

        out = _rehydrate_recent_files("_rehy")
        assert "work.py" in out
        assert "print('hello')" in out
        file_state.clear_scope("_rehy")

    def test_rehydrate_empty_cache(self):
        from agent.mind.tools.think_loop import _rehydrate_recent_files
        from entities.filesystem import file_state
        file_state.clear_scope("_rehy_empty")
        # 未绑定 scope 时落入 _global，临时清空避免串扰
        file_state.clear_scope("_global")
        assert _rehydrate_recent_files("_rehy_empty") == ""
