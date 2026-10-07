"""流中早发射测试：_iter_stream 在新更高 index 出现时发射低位完整缓冲。

约束：参数必须通过 JSON 自证完整；缓冲不消费（最终批次仍含全量）；
发射去重；无更高 index（单调用流）不发射。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import List

import agent.llm.response_parsing as rp


def _frag(idx: int, call_id: str = "", name: str = "", args: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        index=idx, id=call_id,
        function=SimpleNamespace(name=name, arguments=args),
    )


def _chunk(frags: List[SimpleNamespace] = None, content: str = "", finish: str = "") -> SimpleNamespace:
    delta = SimpleNamespace(
        content=content, tool_calls=frags or [],
        reasoning_content=None, reasoning_details=None, thinking_blocks=None,
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=finish or None)],
        usage=None,
    )


def _finish_chunk() -> SimpleNamespace:
    """finish 落在无 delta 的 chunk 上（工具缓冲收尾分支）。"""
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=None, finish_reason="tool_calls")],
        usage=None,
    )


async def _gen(chunks: List[SimpleNamespace]):
    for c in chunks:
        yield c


def _early_ids(deltas) -> List[str]:
    return [c.id for d in deltas for c in d.early_tool_calls]


class TestEarlyEmission:
    async def test_new_higher_index_emits_lower_complete_call(self):
        chunks = [
            _chunk([_frag(0, "c0", "read_a", '{"a"')]),
            _chunk([_frag(0, args=': 1}')]),
            _chunk([_frag(1, "c1", "read_b", "")]),
            _chunk([_frag(1, args='{"b": 2}')]),
            _finish_chunk(),
        ]
        bufs: dict = {}
        deltas = [d async for d in rp._iter_stream(_gen(chunks), bufs)]
        early_ids = _early_ids(deltas)
        assert early_ids == ["c0"]  # idx1 出现即发射低位 c0，且只发一次
        early = [c for d in deltas for c in d.early_tool_calls][0]
        assert early.name == "read_a"
        assert early.arguments == '{"a": 1}'
        # 缓冲不消费：最终批次仍含全量（含已发射的 c0）
        final = [d for d in deltas if d.tool_calls][-1]
        assert [c.id for c in final.tool_calls] == ["c0", "c1"]
        assert final.tool_calls[0].arguments == '{"a": 1}'

    async def test_incomplete_json_not_emitted(self):
        chunks = [
            _chunk([_frag(0, "c0", "read_a", '{"a"')]),
            _chunk([_frag(1, "c1", "read_b", "{}")]),
            _finish_chunk(),
        ]
        bufs: dict = {}
        deltas = [d async for d in rp._iter_stream(_gen(chunks), bufs)]
        assert _early_ids(deltas) == []
        final = [d for d in deltas if d.tool_calls][-1]
        assert len(final.tool_calls) == 2  # 无效缓冲仍进最终批次（原始形态）

    async def test_missing_id_or_name_not_emitted(self):
        chunks = [
            _chunk([_frag(0, "", "read_a", "{}")]),   # 无 id
            _chunk([_frag(1, "c1", "read_b", "{}")]),
            _finish_chunk(),
        ]
        bufs: dict = {}
        deltas = [d async for d in rp._iter_stream(_gen(chunks), bufs)]
        assert _early_ids(deltas) == []

    async def test_single_call_stream_no_early(self):
        chunks = [
            _chunk([_frag(0, "c0", "read_a", '{"q"')]),
            _chunk([_frag(0, args=': 1}')]),
            _finish_chunk(),
        ]
        bufs: dict = {}
        deltas = [d async for d in rp._iter_stream(_gen(chunks), bufs)]
        assert _early_ids(deltas) == []
        final = [d for d in deltas if d.tool_calls][-1]
        assert [c.id for c in final.tool_calls] == ["c0"]

    async def test_three_calls_emit_in_order_as_indices_advance(self):
        chunks = [
            _chunk([_frag(0, "c0", "read_a", "{}")]),
            _chunk([_frag(1, "c1", "read_b", "{}")]),
            _chunk([_frag(2, "c2", "read_c", "{}")]),
            _finish_chunk(),
        ]
        bufs: dict = {}
        deltas = [d async for d in rp._iter_stream(_gen(chunks), bufs)]
        # idx1 出现发 c0；idx2 出现发 c1（c0 已发过不再重发）
        assert _early_ids(deltas) == ["c0", "c1"]
