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
        assert "## Active file: [file:" in block
        assert "workspace/notes/a.md]" in block

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
        msg = inject_workspace_context("用户原文含 ## 也行", _state())
        assert strip_workspace_context(msg) == "用户原文含 ## 也行"

    def test_delimiters_and_tags_in_both_selection_and_request_are_preserved(self):
        from services.chat import clean_message_for_display

        request = "\n用户原文\n\n## My request:\n仍然是原文"
        state = _state(selection={
            "path": "note.txt", "ranges": [],
            "content": "```\n[uid:123]\n\n## My request:\n被引用的代码",
        })
        message = inject_workspace_context(request, state)
        assert strip_workspace_context(message) == request
        assert clean_message_for_display({"content": "[time:today][uid:web_user] " + message})["content"] == request.strip()

    def test_quoted_context_header_in_regular_message_is_not_removed(self):
        text = f"解释以下格式：\n{CONTEXT_HEADER}\n\n内容\n\n{REQUEST_DELIMITER}\n用户正文"
        assert strip_workspace_context(text) == text

    def test_invalid_context_length_does_not_erase_message(self):
        text = f"{CONTEXT_HEADER} (9999 chars)\n\n正文\n\n{REQUEST_DELIMITER}\n请求"
        assert strip_workspace_context(text) == text

    def test_no_context_no_injection(self):
        assert inject_workspace_context("hi", {}) == "hi"
