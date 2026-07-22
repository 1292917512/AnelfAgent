"""edit_utils 纯函数测试（移植自 Claude Code FileEditTool/utils.ts 语义）。"""

from __future__ import annotations

from entities.filesystem import edit_utils


class TestNormalizeQuotes:
    def test_curly_to_straight(self):
        assert edit_utils.normalize_quotes("‘a’ “b”") == "'a' \"b\""

    def test_straight_unchanged(self):
        assert edit_utils.normalize_quotes("'a' \"b\"") == "'a' \"b\""


class TestStripTrailingWhitespace:
    def test_strips_per_line_keeps_eol(self):
        assert edit_utils.strip_trailing_whitespace("a  \nb\t\r\nc ") == "a\nb\r\nc"

    def test_no_trailing_newline(self):
        assert edit_utils.strip_trailing_whitespace("a  ") == "a"


class TestFindActualString:
    def test_exact_match(self):
        assert edit_utils.find_actual_string("hello world", "world") == "world"

    def test_curly_quote_match_returns_original(self):
        content = "say “hello” loudly"
        found = edit_utils.find_actual_string(content, '"hello"')
        assert found == "“hello”"

    def test_not_found(self):
        assert edit_utils.find_actual_string("abc", "xyz") is None


class TestPreserveQuoteStyle:
    def test_no_normalization_passthrough(self):
        assert edit_utils.preserve_quote_style("a", "a", "b") == "b"

    def test_double_quotes_preserved(self):
        result = edit_utils.preserve_quote_style('"x"', "“x”", '"y"')
        assert result == "“y”"

    def test_single_quote_open_close(self):
        result = edit_utils.preserve_quote_style("'x'", "‘x’", "'y'")
        assert result == "‘y’"

    def test_apostrophe_becomes_right_single(self):
        result = edit_utils.preserve_quote_style("'x'", "‘x’", "don't")
        assert result == "don’t"


class TestApplyEditToFile:
    def test_single_replace(self):
        assert edit_utils.apply_edit_to_file("a a a", "a", "b") == "b a a"

    def test_replace_all(self):
        assert edit_utils.apply_edit_to_file("a a a", "a", "b", replace_all=True) == "b b b"

    def test_delete_line_takes_newline(self):
        # 整行删除：old 不带尾换行但 old+\n 存在 → 连带删换行
        assert edit_utils.apply_edit_to_file("x\ny\nz", "y", "") == "x\nz"

    def test_delete_inline_no_newline_theft(self):
        assert edit_utils.apply_edit_to_file("xyz", "y", "") == "xz"


class TestCountOccurrences:
    def test_basic(self):
        assert edit_utils.count_occurrences("aaa", "a") == 3
        assert edit_utils.count_occurrences("abc", "x") == 0
        assert edit_utils.count_occurrences("abc", "") == 0


class TestDiff:
    def test_diff_stats(self):
        add, rem = edit_utils.diff_stats("a\nb\nc", "a\nx\nc")
        assert (add, rem) == (1, 1)

    def test_unified_diff_contains_paths(self):
        diff = edit_utils.unified_diff("f.txt", "a\n", "b\n")
        assert "a/f.txt" in diff and "b/f.txt" in diff
