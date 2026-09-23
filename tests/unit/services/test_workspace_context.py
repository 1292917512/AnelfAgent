"""工作区上下文注入测试（渲染/注入/清洗，纯函数）。"""

from services.workspace_context import (
    CONTEXT_HEADER,
    REQUEST_DELIMITER,
    inject_workspace_context,
    render_workspace_context,
    strip_workspace_context,
)


def _state(**over):
    return {
        "active_file": "workspace/notes/a.md",
        "selection": None,
        "open_tabs": [],
        **over,
    }


class TestRender:
    def test_empty_state_no_block(self):
        assert render_workspace_context({}) == ""
        assert render_workspace_context({"active_file": "", "open_tabs": []}) == ""

    def test_active_file_only(self):
        block = render_workspace_context(_state())
        assert block.startswith(CONTEXT_HEADER)
        assert "## Active file: workspace/notes/a.md" in block

    def test_selection_rendered_with_truncation(self):
        block = render_workspace_context(_state(selection={
            "path": "workspace/notes/a.md",
            "ranges": [{"start_line": 3, "end_line": 7}],
            "content": "x" * 50_000,
        }))
        assert "## Active selection ranges:" in block
        assert "line 3 to line 7" in block
        assert "## Active selection of the file:" in block
        assert "[selection truncated]" in block

    def test_open_tabs_budget(self):
        tabs = [{"label": f"f{i}", "path": f"p/{i}"} for i in range(120)]
        block = render_workspace_context(_state(open_tabs=tabs))
        assert "[20 open tabs omitted.]" in block

    def test_no_path_no_selection_block(self):
        block = render_workspace_context({"selection": None, "open_tabs": None})
        assert block == ""


class TestInjectAndStrip:
    def test_roundtrip(self):
        msg = inject_workspace_context("改一下这里", _state())
        assert msg.startswith(CONTEXT_HEADER)
        assert REQUEST_DELIMITER in msg
        assert strip_workspace_context(msg) == "改一下这里"

    def test_strip_without_block_identity(self):
        assert strip_workspace_context("普通消息") == "普通消息"

    def test_strip_takes_tail_after_delimiter(self):
        # 注入块内可能含 ## 标题，rpartition 取最后一个分隔符后的用户原文
        msg = inject_workspace_context("用户原文含 ## 也行", _state())
        assert strip_workspace_context(msg) == "用户原文含 ## 也行"

    def test_no_context_no_injection(self):
        assert inject_workspace_context("hi", {}) == "hi"
