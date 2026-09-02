"""飞书 helpers 纯函数测试（不触网）。"""

from __future__ import annotations

import json

from channels.feishu.helpers import (
    check_bot_mentioned,
    chunk_text,
    extract_media_key,
    looks_like_markdown,
    parse_message_content,
    parse_post_content,
)
from channels.feishu.types import FeishuMention, FeishuSenderId


class TestParsePostContent:
    def test_text_and_title(self) -> None:
        raw = json.dumps({"zh_cn": {"title": "标题", "content": [[{"tag": "text", "text": "hello"}]]}})
        result = parse_post_content(raw)
        assert result.text == "标题\nhello"

    def test_at_and_img(self) -> None:
        raw = json.dumps({"zh_cn": {"content": [[
            {"tag": "at", "user_id": "ou_abc", "user_name": "Tom"},
            {"tag": "img", "image_key": "img_123"},
            {"tag": "text", "text": "看图"},
        ]]}})
        result = parse_post_content(raw)
        assert result.at_open_ids == ["ou_abc"]
        assert result.image_keys == ["img_123"]
        assert "[at_uid:ou_abc]" in result.text
        assert "看图" in result.text

    def test_link_and_md(self) -> None:
        raw = json.dumps({"en_us": {"content": [[
            {"tag": "a", "text": "文档", "href": "https://x.com"},
            {"tag": "md", "text": "**加粗**"},
        ]]}})
        result = parse_post_content(raw)
        assert "文档(https://x.com)" in result.text
        assert "**加粗**" in result.text

    def test_invalid_json_fallback(self) -> None:
        assert parse_post_content("not json").text == "not json"

    def test_media_file_key(self) -> None:
        raw = json.dumps({"zh_cn": {"content": [[{"tag": "media", "file_key": "fk_1"}]]}})
        assert parse_post_content(raw).file_keys == ["fk_1"]


class TestParseMessageContent:
    def test_text(self) -> None:
        assert parse_message_content(json.dumps({"text": "你好"}), "text") == "你好"

    def test_invalid_text_returns_raw(self) -> None:
        assert parse_message_content("plain", "text") == "plain"

    def test_file_name(self) -> None:
        raw = json.dumps({"file_name": "报告.pdf", "file_key": "fk"})
        assert parse_message_content(raw, "file") == "[文件: 报告.pdf]"

    def test_media_placeholders(self) -> None:
        assert parse_message_content("{}", "image") == "[图片]"
        assert parse_message_content("{}", "audio") == "[语音]"
        assert parse_message_content("{}", "sticker") == "[表情]"
        assert "合并转发" in parse_message_content("{}", "merge_forward")

    def test_interactive_card(self) -> None:
        raw = json.dumps({"elements": [
            {"tag": "markdown", "content": "卡片正文"},
            {"tag": "div", "text": {"content": "说明"}},
        ]})
        text = parse_message_content(raw, "interactive")
        assert "卡片正文" in text and "说明" in text

    def test_interactive_fallback(self) -> None:
        assert parse_message_content("bad", "interactive") == "[卡片消息]"


class TestCheckBotMentioned:
    def _mention(self, open_id: str) -> FeishuMention:
        return FeishuMention(id=FeishuSenderId(open_id=open_id))

    def test_hit(self) -> None:
        assert check_bot_mentioned([self._mention("ou_bot")], "ou_bot") is True

    def test_miss_and_empty(self) -> None:
        assert check_bot_mentioned([self._mention("ou_a")], "ou_bot") is False
        assert check_bot_mentioned(None, "ou_bot") is False
        assert check_bot_mentioned([self._mention("ou_bot")], "") is False


class TestChunkText:
    def test_short_text_single_chunk(self) -> None:
        assert chunk_text("abc", 10) == ["abc"]

    def test_splits_at_newline(self) -> None:
        text = "aaaa\nbbbb\ncccc"
        chunks = chunk_text(text, 9)
        assert chunks == ["aaaa", "bbbb\ncccc"]

    def test_hard_cut_without_newline(self) -> None:
        chunks = chunk_text("a" * 25, 10)
        assert [len(c) for c in chunks] == [10, 10, 5]


class TestLooksLikeMarkdown:
    def test_plain_text_is_not_markdown(self) -> None:
        assert looks_like_markdown("今天天气不错") is False

    def test_markdown_features(self) -> None:
        assert looks_like_markdown("# 标题") is True
        assert looks_like_markdown("**加粗**") is True
        assert looks_like_markdown("```py\npass\n```") is True
        assert looks_like_markdown("- 第一项") is True
        assert looks_like_markdown("| a | b |\n|---|---|") is True
        assert looks_like_markdown("[链接](https://x.com)") is True
        assert looks_like_markdown("> 引用") is True


class TestExtractMediaKey:
    def test_image(self) -> None:
        assert extract_media_key(json.dumps({"image_key": "ik"}), "image") == {"image_key": "ik"}

    def test_file_with_name(self) -> None:
        keys = extract_media_key(json.dumps({"file_key": "fk", "file_name": "a.pdf"}), "file")
        assert keys == {"file_key": "fk", "file_name": "a.pdf"}

    def test_invalid_json(self) -> None:
        assert extract_media_key("bad", "image") == {}
