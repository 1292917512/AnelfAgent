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

    # 第 3 级：行尾空白归一（模型看不到 read_file 输出中的行尾空格/Tab）
    def test_trailing_ws_match_returns_original(self):
        content = "def f():  \n    return 1\n"
        found = edit_utils.find_actual_string(content, "def f():\n    return 1")
        assert found == "def f():  \n    return 1"

    def test_trailing_tab_match(self):
        content = "a\t\nb"
        assert edit_utils.find_actual_string(content, "a\nb") == "a\t\nb"

    def test_trailing_ws_no_indent_relaxation(self):
        # 行尾归一只容忍行尾空白：needle 与文件行在"内容"上不同（缩进不同）时，
        # 不得以行对齐方式命中。（Level 1 的整行子串语义允许 needle 为行内片段，
        # 此处验证多级回退不会额外放松缩进）
        content = "  x = 1 \n"
        # needle 无行尾空格但缩进与文件行不同 → Level 1（子串）不命中整行 span；
        # Level 3 行对齐时 rstrip 后仍因缩进不同而不等
        assert edit_utils.find_actual_string(content, " x = 1\n") is None

    # 第 4 级：Unicode 标点/空白归一
    def test_nbsp_match(self):
        content = "a b"
        assert edit_utils.find_actual_string(content, "a b") == "a b"

    def test_em_dash_match(self):
        content = "x — y"
        assert edit_utils.find_actual_string(content, "x - y") == "x — y"

    def test_fullwidth_space_match(self):
        content = "甲　乙"
        assert edit_utils.find_actual_string(content, "甲 乙") == "甲　乙"

    def test_unicode_no_indent_relaxation(self):
        # Unicode 归一同样不放松缩进：needle 跨两行（Level 1 子串必须逐字符
        # 连续），首行缩进不同 → Level 1 不成立；Level 4 行对齐时缩进不等 → 不命中
        content = "     x = 1\n     y = 2\n"
        assert edit_utils.find_actual_string(content, "   x = 1\n   y = 2\n") is None

    def test_level_priority_exact_over_unicode(self):
        # 同时存在精确与 Unicode 变体时，精确命中优先（返回精确原文）
        content = "a - b\na — b"
        assert edit_utils.find_actual_string(content, "a - b") == "a - b"


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
