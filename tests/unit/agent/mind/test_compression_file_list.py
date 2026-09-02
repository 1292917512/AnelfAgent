"""压缩文件清单结构化累积（context_compressor 第九轮）单元测试。"""

from __future__ import annotations

from agent.mind.context_compressor import (
    _extract_file_operations,
    _extract_file_operations_from_summary,
    _merge_list,
    _render_file_list_message,
)


def _tc(name: str, args: str) -> dict:
    return {"id": f"c_{name}", "function": {"name": name, "arguments": args}}


class TestFileOpsExtraction:
    def test_extracts_read_and_write_separately(self) -> None:
        msgs = [{"role": "assistant", "tool_calls": [
            _tc("read_file", '{"file_path": "src/a.py"}'),
            _tc("edit_file", '{"file_path": "docs/b.md"}'),
            _tc("write_file", '{"file_path": "out/c.json"}'),
        ]}]
        ops = _extract_file_operations(msgs)
        assert ops["read"] == ["src/a.py"]
        assert ops["written"] == ["docs/b.md", "out/c.json"]

    def test_dedupes_and_ignores_non_file_tools(self) -> None:
        msgs = [{"role": "assistant", "tool_calls": [
            _tc("read_file", '{"file_path": "x.py"}'),
            _tc("read_file", '{"file_path": "x.py"}'),
            _tc("web_search", "{}"),
            _tc("read_file", "not-json"),
        ]}]
        ops = _extract_file_operations(msgs)
        assert ops["read"] == ["x.py"]

    def test_move_and_copy_both_paths(self) -> None:
        msgs = [{"role": "assistant", "tool_calls": [
            _tc("move_file", '{"src": "a.txt", "dst": "b.txt"}'),
        ]}]
        ops = _extract_file_operations(msgs)
        assert ops["written"] == ["a.txt", "b.txt"]


class TestRenderAndRoundtrip:
    def test_render_and_parse_back(self) -> None:
        ops = {"read": ["a.py", "b.py"], "written": ["c.md"]}
        rendered = _render_file_list_message(ops)
        assert rendered.startswith("[已操作文件]")
        back = _extract_file_operations_from_summary(rendered)
        assert back["read"] == ["a.py", "b.py"]
        assert back["written"] == ["c.md"]

    def test_empty_renders_nothing(self) -> None:
        assert _render_file_list_message({"read": [], "written": []}) == ""

    def test_parse_missing_list_returns_empty(self) -> None:
        back = _extract_file_operations_from_summary("没有清单行的普通摘要")
        assert back == {"read": [], "written": []}


class TestMerge:
    def test_merge_preserves_order_and_dedupes(self) -> None:
        merged = _merge_list(["a", "b"], ["b", "c"])
        assert merged == ["a", "b", "c"]

    def test_merge_into_empty(self) -> None:
        assert _merge_list([], ["x"]) == ["x"]
