"""LLM 出向边界清洗（core.sanitizer 扩展原语）单元测试。"""

from __future__ import annotations

from core.sanitizer import (
    clean_surrogates,
    has_surrogates,
    sanitize_for_context,
    sanitize_text,
    truncate_middle,
)


class TestUrlCredential:
    def test_http_basic_auth_masked(self) -> None:
        text = "连接 https://admin:s3cretP4ss@db.internal:5432/app 成功"
        result = sanitize_text(text)
        assert "s3cretP4ss" not in result
        assert "db.internal" in result
        assert "https://" in result

    def test_redis_url_masked(self) -> None:
        text = "redis://default:akkp123456@redis-123.cloud.com:6379"
        result = sanitize_text(text)
        assert "akkp123456" not in result
        assert "redis-123.cloud.com" in result

    def test_url_without_credential_untouched(self) -> None:
        text = "文档见 https://example.com/path?q=1"
        assert sanitize_text(text) == text


class TestSurrogates:
    def test_lone_surrogate_removed(self) -> None:
        text = "正常文本\ud800异常字符\udfff结束"
        result = clean_surrogates(text)
        assert result == "正常文本异常字符结束"

    def test_clean_text_untouched(self) -> None:
        text = "包含 emoji 🎉 和中文的正常文本"
        assert clean_surrogates(text) == text
        assert not has_surrogates(text)

    def test_has_surrogates(self) -> None:
        assert has_surrogates("abc\ud834def")
        assert not has_surrogates("")
        assert not has_surrogates("普通文本")


class TestTruncateMiddle:
    def test_short_text_untouched(self) -> None:
        text = "短文本"
        assert truncate_middle(text, 100) == text

    def test_head_and_tail_preserved(self) -> None:
        text = "头" * 500 + "中" * 500 + "尾" * 500
        result = truncate_middle(text, 400)
        assert len(result) <= 400
        assert result.startswith("头" * 100)
        assert result.endswith("尾" * 100)
        assert "中部已省略" in result

    def test_tail_ratio(self) -> None:
        text = "H" * 700 + "T" * 300
        result = truncate_middle(text, 200, head_ratio=0.5)
        # 头尾各占一半预算，尾部内容（T）必须出现
        assert "T" in result
        assert "H" in result

    def test_zero_budget_fallback(self) -> None:
        text = "x" * 100
        result = truncate_middle(text, 5)
        assert len(result) <= 5


class TestSanitizeForContext:
    def test_pipeline_combined(self) -> None:
        text = (
            "记忆内容: sk-abcdefghijklmnop1234567890abcd "
            "与 https://user:pass@host.com/db \ud800"
        )
        result = sanitize_for_context(text)
        assert "sk-abcdefghijklmnop" not in result
        assert "pass" not in result
        assert "\ud800" not in result
        assert "host.com" in result

    def test_long_text_truncated_with_tail(self) -> None:
        tail_secret = "结尾要点"
        text = "填" * 8000 + tail_secret
        result = sanitize_for_context(text, max_chars=1000)
        assert len(result) <= 1000
        assert result.endswith(tail_secret)

    def test_empty_and_none_safe(self) -> None:
        assert sanitize_for_context("") == ""
