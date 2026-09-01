"""worker 出站纯函数层测试：at 段解析 / 媒体源解析 / 回复文本提取 / 昵称缓存。"""

from __future__ import annotations

from channels.nonebot_bridge.worker.segments import (
    MAX_INLINE_BYTES,
    file_display_name,
    plain_at_text,
    resolve_media_source,
    split_at_segments,
)
from channels.nonebot_bridge.worker.wire_out import (
    cache_nickname,
    extract_message_text,
    get_cached_nickname,
)


class TestSplitAtSegments:
    """[at_uid:x] → at/text 有序段。"""

    def test_mixed_text_and_ats(self) -> None:
        assert split_at_segments("看这里[at_uid:123] 和 [at_uid:all]") == [
            ("text", "看这里"),
            ("at", "123"),
            ("text", " 和 "),
            ("at", "all"),
        ]

    def test_plain_text_only(self) -> None:
        assert split_at_segments("没有标签") == [("text", "没有标签")]

    def test_at_only(self) -> None:
        assert split_at_segments("[at_uid:42]") == [("at", "42")]

    def test_empty(self) -> None:
        assert split_at_segments("") == []


class TestPlainAtText:
    """非 OneBot 平台的 at 降级文本。"""

    def test_replaces_tags(self) -> None:
        assert plain_at_text("嘿 [at_uid:123] 看看") == "嘿 @123 看看"

    def test_all_mentions_everyone(self) -> None:
        assert plain_at_text("[at_uid:all]") == "@全体成员"

    def test_no_tags_passthrough(self) -> None:
        assert plain_at_text("纯文本") == "纯文本"


class TestResolveMediaSource:
    """媒体源解析：URL 直传 / file_id 直传 / 本地 base64 内联 / 错误路径。"""

    def test_url_passthrough(self) -> None:
        url = "https://example.com/a.jpg"
        assert resolve_media_source(url) == url

    def test_base64_prefix_passthrough(self) -> None:
        assert resolve_media_source("base64://abc") == "base64://abc"

    def test_bare_file_id_passthrough(self) -> None:
        assert resolve_media_source("ABC123.image") == "ABC123.image"

    def test_local_file_inlined(self, tmp_path) -> None:
        media = tmp_path / "a.png"
        media.write_bytes(b"\x89PNG")
        resolved = resolve_media_source(str(media))
        assert resolved.startswith("base64://")
        import base64

        assert base64.b64decode(resolved[len("base64://"):]) == b"\x89PNG"

    def test_missing_path_with_separator_raises(self, tmp_path) -> None:
        import pytest

        with pytest.raises(FileNotFoundError):
            resolve_media_source(str(tmp_path / "nope" / "a.jpg"))

    def test_oversize_rejected(self, tmp_path) -> None:
        import pytest

        big = tmp_path / "big.bin"
        big.write_bytes(b"\x00" * 64)
        import channels.nonebot_bridge.worker.segments as seg_mod

        original = seg_mod.MAX_INLINE_BYTES
        seg_mod.MAX_INLINE_BYTES = 32  # 临时压低上限
        try:
            with pytest.raises(ValueError):
                resolve_media_source(str(big))
        finally:
            seg_mod.MAX_INLINE_BYTES = original
        assert MAX_INLINE_BYTES == 100 * 1024 * 1024


class TestFileDisplayName:
    """文件展示名。"""

    def test_override_wins(self) -> None:
        assert file_display_name("/x/y.bin", "报表.xlsx") == "报表.xlsx"

    def test_basename(self) -> None:
        assert file_display_name("/tmp/dir/voice.silk") == "voice.silk"

    def test_fallback_to_source(self) -> None:
        assert file_display_name("ABC.image") == "ABC.image"


class TestExtractMessageText:
    """OneBot get_msg 回捞数据 → 纯文本。"""

    def test_raw_message_preferred(self) -> None:
        data = {
            "raw_message": "原始文本 [CQ:image,file=x.jpg]",
            "message": [{"type": "text", "data": {"text": "原始文本 "}}],
        }
        assert extract_message_text(data) == "原始文本 [CQ:image,file=x.jpg]"

    def test_text_segments(self) -> None:
        data = {
            "message": [
                {"type": "text", "data": {"text": "你好 "}},
                {"type": "image", "data": {"url": "http://x"}},
                {"type": "text", "data": {"text": "世界"}},
            ]
        }
        assert extract_message_text(data) == "你好 世界"

    def test_string_message(self) -> None:
        assert extract_message_text({"message": "纯字符串"}) == "纯字符串"

    def test_bare_string(self) -> None:
        assert extract_message_text("直接文本") == "直接文本"

    def test_empty_dict(self) -> None:
        assert extract_message_text({}) == ""


class TestNicknameCache:
    """群昵称缓存往返与淘汰。"""

    def test_roundtrip(self) -> None:
        cache_nickname("g1", "u1", "小明")
        assert get_cached_nickname("g1", "u1") == "小明"
        assert get_cached_nickname("g1", "u2") is None
        assert get_cached_nickname("g2", "u1") is None

    def test_overwrite(self) -> None:
        cache_nickname("g1", "u1", "旧名")
        cache_nickname("g1", "u1", "新名")
        assert get_cached_nickname("g1", "u1") == "新名"
