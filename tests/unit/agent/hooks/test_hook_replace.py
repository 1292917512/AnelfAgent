"""hook REPLACE 输出解析（agent.hooks.runner._extract_replace）单元测试。"""

from __future__ import annotations

from agent.hooks.runner import _extract_replace


def test_simple_json_string() -> None:
    stdout = 'REPLACE:"脱敏后的内容"'
    assert _extract_replace(stdout) == "脱敏后的内容"


def test_last_line_wins() -> None:
    stdout = 'REPLACE:"第一版"\n其他输出\nREPLACE:"最终版"'
    assert _extract_replace(stdout) == "最终版"


def test_non_replace_stdout_ignored() -> None:
    assert _extract_replace("普通日志输出") is None
    assert _extract_replace("") is None


def test_invalid_json_returns_none() -> None:
    assert _extract_replace("REPLACE:not-json") is None


def test_non_string_json_returns_none() -> None:
    # REPLACE 必须是 JSON 字符串（数字/对象都不是有效的替换文本）
    assert _extract_replace("REPLACE:42") is None
    assert _extract_replace('REPLACE:{"text": "对象而非字符串"}') is None


def test_multiline_escaped_string() -> None:
    stdout = 'REPLACE:"第一行\\n第二行\\n含特殊字符 \\"引号\\""'
    result = _extract_replace(stdout)
    assert result is not None
    assert "\n" in result
    assert '"引号"' in result
