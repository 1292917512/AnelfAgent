"""MiniMax TTS 提供者的流式帧处理回归测试。

锁定根因：t2a_v2 流式响应 = 增量音频帧 + 终止帧（extra_info 用量汇总，
携带整句全量音频备份）——终止帧音频被产出时整句会被播放两遍。
"""

from __future__ import annotations

import json

import pytest

from entities.minimax.providers import MiniMaxTtsProvider


def _sse_line(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False)


class _FakeStreamResp:
    """模拟 httpx 流式响应（async with + aiter_lines）。"""

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def raise_for_status(self) -> None:
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeHttp:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def stream(self, method, url, headers=None, json=None):
        return _FakeStreamResp(self._lines)


def _patch_client(monkeypatch: pytest.MonkeyPatch, lines: list[str]) -> None:
    class _Client:
        configured = True

        def _json_headers(self):
            return {}

        def _http_client(self, timeout=60.0):
            return _FakeHttp(self._lines_holder)

    client = _Client()
    client._lines_holder = lines
    monkeypatch.setattr("entities.minimax.providers._client", lambda: client)


def _frames(*, tail_dump: bool) -> list[str]:
    lines = []
    for i in range(3):
        lines.append(_sse_line({"data": {"audio": (b"\x01\x02" * (100 + i)).hex()},
                                "base_resp": {"status_code": 0}}))
    if tail_dump:
        # 终止帧：extra_info + 整句全量音频（10000 字节）
        lines.append(_sse_line({
            "data": {"audio": (b"\x09" * 10000).hex()},
            "extra_info": {"audio_length": 1000, "usage_characters": 12},
            "base_resp": {"status_code": 0, "status_msg": "success"}}))
    return lines


class TestTerminalDumpSkipped:
    async def test_incremental_stream_then_dump_is_skipped(self, monkeypatch) -> None:
        """增量流已产出音频时，终止帧的全量音频不得再产出（防整句双播）。"""
        _patch_client(monkeypatch, _frames(tail_dump=True))
        provider = MiniMaxTtsProvider()
        stream = provider.stream_synthesize("测试一句话", sample_rate=24000)
        chunks = [chunk async for chunk in stream.chunks]
        total = sum(len(c) for c in chunks)
        assert total == 200 + 202 + 204, f"终止帧的全量音频泄漏进流: total={total}"

    async def test_dump_kept_when_incremental_empty(self, monkeypatch) -> None:
        """增量流为空（极短文本只有终止帧）时，终止帧音频是唯一来源，须保留。"""
        lines = [_sse_line({
            "data": {"audio": (b"\x09" * 500).hex()},
            "extra_info": {"audio_length": 100, "usage_characters": 1},
            "base_resp": {"status_code": 0, "status_msg": "success"}})]
        _patch_client(monkeypatch, lines)
        provider = MiniMaxTtsProvider()
        stream = provider.stream_synthesize("嗯", sample_rate=24000)
        chunks = [chunk async for chunk in stream.chunks]
        assert sum(len(c) for c in chunks) == 500
