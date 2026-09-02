"""飞书昵称缓存与发送辅助测试（不触网）。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from channels.feishu.send import _serialize_history_message, _should_attach_reply, resolve_emoji_type
from channels.feishu.users import UserNameCache


def _fake_client(code: int = 0, name: str = "张三") -> Any:
    """伪造 contact.v3.user.get 调用链。"""
    resp = SimpleNamespace(
        code=code, msg="" if code == 0 else "err",
        success=lambda: code == 0,
        data=SimpleNamespace(user=SimpleNamespace(name=name)) if code == 0 else None,
    )
    user_res = SimpleNamespace(get=lambda req: resp)
    v3 = SimpleNamespace(user=user_res)
    return SimpleNamespace(contact=SimpleNamespace(v3=v3))


class TestUserNameCache:
    def test_fetch_and_cache(self) -> None:
        cache = UserNameCache()
        client = _fake_client()
        assert asyncio.run(cache.get_name(client, "ou_1")) == "张三"
        # 第二次走缓存：换个"拿不到数据"的 client 也返回旧值
        assert asyncio.run(cache.get_name(SimpleNamespace(), "ou_1")) == "张三"

    def test_no_scope_denied_and_sticky(self) -> None:
        cache = UserNameCache()
        client = _fake_client(code=99991672)
        assert asyncio.run(cache.get_name(client, "ou_1")) == ""
        assert cache._denied is True
        # denied 后不再请求，直接返回空
        assert asyncio.run(cache.get_name(_fake_client(), "ou_2")) == ""

    def test_failure_fail_open(self) -> None:
        cache = UserNameCache()
        assert asyncio.run(cache.get_name(_fake_client(code=123), "ou_1")) == ""

    def test_empty_open_id(self) -> None:
        cache = UserNameCache()
        assert asyncio.run(cache.get_name(_fake_client(), "")) == ""


class TestResolveEmojiType:
    def test_char_alias(self) -> None:
        assert resolve_emoji_type("👍") == "THUMBSUP"
        assert resolve_emoji_type("✅") == "DONE"
        assert resolve_emoji_type("❤️") == "HEART"

    def test_name_alias_case_insensitive(self) -> None:
        assert resolve_emoji_type("thumbsup") == "THUMBSUP"
        assert resolve_emoji_type("Ok") == "OK"

    def test_uppercase_passthrough(self) -> None:
        assert resolve_emoji_type("FINGERHEART") == "FINGERHEART"

    def test_unknown_raises(self) -> None:
        try:
            resolve_emoji_type("???")
            raise AssertionError("should raise")
        except ValueError as exc:
            assert "无法识别" in str(exc)

    def test_empty_raises(self) -> None:
        try:
            resolve_emoji_type("  ")
            raise AssertionError("should raise")
        except ValueError:
            pass


class TestReplyAttachPolicy:
    def test_first(self) -> None:
        assert [_should_attach_reply("first", i) for i in range(3)] == [True, False, False]

    def test_all(self) -> None:
        assert [_should_attach_reply("all", i) for i in range(3)] == [True, True, True]

    def test_off(self) -> None:
        assert [_should_attach_reply("off", i) for i in range(3)] == [False, False, False]


class TestSerializeHistoryMessage:
    def test_text_message(self) -> None:
        item = SimpleNamespace(
            message_id="om_1", msg_type="text",
            body=SimpleNamespace(content='{"text": "hello"}'),
            sender=SimpleNamespace(id=SimpleNamespace(open_id="ou_1")),
            create_time="1735689600000",
        )
        entry = _serialize_history_message(item)
        assert entry["message_id"] == "om_1"
        assert entry["sender_open_id"] == "ou_1"
        assert entry["text"] == "hello"
        assert entry["time"]  # 已格式化为 %m-%d %H:%M

    def test_long_text_truncated(self) -> None:
        item = SimpleNamespace(
            message_id="om_2", msg_type="text",
            body=SimpleNamespace(content='{"text": "' + "a" * 600 + '"}'),
            sender=SimpleNamespace(id=SimpleNamespace(open_id="")),
            create_time="",
        )
        entry = _serialize_history_message(item)
        assert len(entry["text"]) == 501
        assert entry["text"].endswith("…")
        assert entry["time"] == ""
