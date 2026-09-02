"""飞书入站消息处理测试（不触网，fake 事件对象）。"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from typing import Any, List, Optional

from channels.feishu.handlers import (
    _fetch_parent_preview,
    _handle_message_event,
    _is_duplicate,
    _log_coro_exception,
)


def _make_event(
    message_id: str,
    *,
    content: str = '{"text": "你好"}',
    msg_type: str = "text",
    chat_id: str = "oc_chat1",
    chat_type: str = "group",
    sender_open_id: str = "ou_user1",
    mentions: Optional[list] = None,
    parent_id: str = "",
) -> Any:
    def _mention(open_id: str, key: str = "@_user_1", name: str = "Bot") -> Any:
        return SimpleNamespace(
            key=key, name=name,
            id=SimpleNamespace(open_id=open_id, user_id="", union_id=""),
            tenant_key="",
        )

    message = SimpleNamespace(
        message_id=message_id, content=content, message_type=msg_type,
        chat_id=chat_id, chat_type=chat_type, parent_id=parent_id,
        mentions=[_mention(m) for m in (mentions or [])],
    )
    sender = SimpleNamespace(sender_id=SimpleNamespace(open_id=sender_open_id))
    return SimpleNamespace(event=SimpleNamespace(message=message, sender=sender))


async def _dispatch(
    data: Any,
    *,
    bot_open_id: str = "ou_bot",
    require_mention: bool = True,
    resolve_name: Any = None,
    on_chat_seen: Any = None,
    client: Any = None,
) -> List[Any]:
    collected: List[Any] = []

    async def on_message(msg: Any) -> None:
        collected.append(msg)

    await _handle_message_event(
        data, client=client, bot_open_id=bot_open_id,
        require_mention=require_mention, on_message=on_message,
        resolve_name=resolve_name, on_chat_seen=on_chat_seen,
    )
    return collected


class TestDeduplicate:
    def test_duplicate_blocked(self) -> None:
        mid = f"m_dup_{time.time_ns()}"
        assert _is_duplicate(mid) is False
        assert _is_duplicate(mid) is True


class TestHandleMessageEvent:
    def test_group_without_mention_not_triggered(self) -> None:
        data = _make_event(f"m1_{time.time_ns()}")
        [msg] = asyncio.run(_dispatch(data))  # type: ignore[misc]
        assert msg.is_to_me is False
        assert msg.trigger_mind is False
        assert msg.content == "你好"

    def test_group_with_mention_triggered_and_tagged(self) -> None:
        data = _make_event(
            f"m2_{time.time_ns()}",
            content='{"text": "@_user_1 在吗"}',
            mentions=["ou_bot"],
        )
        [msg] = asyncio.run(_dispatch(data))  # type: ignore[misc]
        assert msg.is_to_me is True
        assert msg.trigger_mind is True
        assert "[at_uid:ou_bot]" in msg.content

    def test_group_no_require_mention_always_triggers(self) -> None:
        data = _make_event(f"m3_{time.time_ns()}")
        [msg] = asyncio.run(_dispatch(data, require_mention=False))  # type: ignore[misc]
        assert msg.trigger_mind is True

    def test_p2p_always_to_me(self) -> None:
        data = _make_event(f"m4_{time.time_ns()}", chat_type="p2p", chat_id="oc_p2p")
        [msg] = asyncio.run(_dispatch(data))  # type: ignore[misc]
        assert msg.is_to_me is True
        assert msg.trigger_mind is True
        assert msg.channel.channel_type.value == "private"

    def test_bot_self_message_ignored(self) -> None:
        data = _make_event(f"m5_{time.time_ns()}", sender_open_id="ou_bot")
        assert asyncio.run(_dispatch(data)) == []  # type: ignore[misc]

    def test_duplicate_event_ignored(self) -> None:
        mid = f"m6_{time.time_ns()}"
        assert len(asyncio.run(_dispatch(_make_event(mid)))) == 1  # type: ignore[misc]
        assert asyncio.run(_dispatch(_make_event(mid))) == []  # type: ignore[misc]

    def test_reply_parent_id_mapped(self) -> None:
        data = _make_event(f"m7_{time.time_ns()}", parent_id="om_parent")
        [msg] = asyncio.run(_dispatch(data))  # type: ignore[misc]
        assert msg.reply_to_id == "om_parent"

    def test_sender_name_resolved(self) -> None:
        async def resolve(open_id: str) -> str:
            return "张三" if open_id == "ou_user1" else ""

        data = _make_event(f"m8_{time.time_ns()}")
        [msg] = asyncio.run(_dispatch(data, resolve_name=resolve))  # type: ignore[misc]
        assert msg.sender.user_name == "张三"

    def test_sender_name_fallback_open_id(self) -> None:
        data = _make_event(f"m9_{time.time_ns()}")
        [msg] = asyncio.run(_dispatch(data))  # type: ignore[misc]
        assert msg.sender.user_name == "ou_user1"

    def test_chat_seen_recorded(self) -> None:
        seen: List[tuple] = []
        data = _make_event(f"m10_{time.time_ns()}", chat_id="oc_new", chat_type="group")
        asyncio.run(_dispatch(data, on_chat_seen=lambda cid, ct: seen.append((cid, ct))))  # type: ignore[misc]
        assert seen == [("oc_new", "group")]

    def test_post_at_replaced_by_name(self) -> None:
        raw = json.dumps({"zh_cn": {"content": [[
            {"tag": "at", "user_id": "ou_bot", "user_name": "Bot"},
            {"tag": "text", "text": " 看看"},
        ]]}})
        data = _make_event(f"m11_{time.time_ns()}", content=raw, msg_type="post", mentions=["ou_bot"])
        [msg] = asyncio.run(_dispatch(data))  # type: ignore[misc]
        assert "[at_uid:ou_bot]" in msg.content
        assert "看看" in msg.content

    def test_reply_content_preview_fetched(self) -> None:
        """被回复消息的原文预览随 reply_content 注入（AI 可见 [reply_to:xxx]预览）。"""
        parent = SimpleNamespace(
            message_id="om_parent", msg_type="text",
            body=SimpleNamespace(content='{"text": "之前的讨论内容"}'),
            sender=SimpleNamespace(id=SimpleNamespace(open_id="ou_other")),
            create_time="",
        )
        resp = SimpleNamespace(success=lambda: True, code=0, msg="", data=SimpleNamespace(items=[parent]))
        client = SimpleNamespace(im=SimpleNamespace(v1=SimpleNamespace(
            message=SimpleNamespace(get=lambda req: resp),
        )))
        data = _make_event(f"m12_{time.time_ns()}", parent_id="om_parent")
        [msg] = asyncio.run(_dispatch(data, client=client))  # type: ignore[misc]
        assert msg.reply_to_id == "om_parent"
        assert msg.reply_content == "之前的讨论内容"

    def test_reply_preview_failure_fail_open(self) -> None:
        """引用预览拉取失败不阻塞消息分发。"""
        client = SimpleNamespace(im=SimpleNamespace(v1=SimpleNamespace(
            message=SimpleNamespace(get=lambda req: (_ for _ in ()).throw(RuntimeError("boom"))),
        )))
        data = _make_event(f"m13_{time.time_ns()}", parent_id="om_parent")
        [msg] = asyncio.run(_dispatch(data, client=client))  # type: ignore[misc]
        assert msg.reply_to_id == "om_parent"
        assert msg.reply_content == ""

    def test_no_parent_no_fetch(self) -> None:
        assert asyncio.run(_fetch_parent_preview(SimpleNamespace(), "")) == ""


class TestCoroExceptionLogging:
    def test_log_coro_exception_swallows_and_logs(self) -> None:
        async def boom() -> None:
            raise RuntimeError("handler exploded")

        async def run() -> None:
            task = asyncio.ensure_future(boom())
            try:
                await task
            except RuntimeError:
                pass
            # 已结束的 future 调用 result() 会重抛 → 回调内捕获记日志
            _log_coro_exception(task)  # type: ignore[arg-type]

        asyncio.run(run())

    def test_log_coro_exception_success_noop(self) -> None:
        async def ok() -> None:
            return None

        async def run() -> None:
            task = asyncio.ensure_future(ok())
            await task
            _log_coro_exception(task)  # type: ignore[arg-type]

        asyncio.run(run())
