"""wcauto 工具面测试：三档门控（读 AUTO_ALLOW / 写 sensitive+allow_write）与错误透传。"""

from __future__ import annotations

import json

from channels.wcauto.config import WcautoConfig
from channels.wcauto.tools import WcautoToolsMixin
from channels.wcauto.transport import WcautoRpcError


class _FakeTransport:
    def __init__(self, data=None, fail: bool = False) -> None:
        self.calls: list = []
        self._data = data if data is not None else {"status": "ok"}
        self._fail = fail

    async def call_rpc(self, method, params=None, timeout=None):
        self.calls.append((method, params or {}))
        if self._fail:
            raise WcautoRpcError("桥未连接（Windows 桥离线或重连中）")
        return self._data


class _ToolsHost(WcautoToolsMixin):
    """工具 mixin 测试宿主（模拟装配到频道后的属性面）。"""

    def __init__(self, allow_write: bool = False, transport=None) -> None:
        self.config = WcautoConfig(allow_write=allow_write)
        self._transport = transport or _FakeTransport()


def _result(coro) -> dict:
    return json.loads(coro)


class TestReadTools:
    async def test_get_sessions(self) -> None:
        host = _ToolsHost(transport=_FakeTransport(
            [{"username": "wxid_a", "chat_name": "朋友甲", "unread": 2,
              "summary": "你好", "is_group": False}]))
        result = _result(await host.get_sessions())
        assert result["success"] is True
        assert result["count"] == 1
        assert result["sessions"][0]["chat_name"] == "朋友甲"

    async def test_get_messages(self) -> None:
        host = _ToolsHost(transport=_FakeTransport(
            [{"local_id": 1, "type": "文本", "sender_username": "wxid_a",
              "content": "hi", "create_time": 1, "shard": "message_0.db"}]))
        result = _result(await host.get_messages("wxid_a"))
        assert result["success"] and result["count"] == 1

    async def test_search_contact_empty_keyword(self) -> None:
        host = _ToolsHost()
        result = _result(await host.search_contact("  "))
        assert result["success"] is False

    async def test_get_chat_members_rejects_private(self) -> None:
        host = _ToolsHost()
        result = _result(await host.get_chat_members("wxid_a"))
        assert result["success"] is False
        assert "非群聊" in result["error"]

    async def test_rpc_error_propagates(self) -> None:
        host = _ToolsHost(transport=_FakeTransport(fail=True))
        result = _result(await host.get_sessions())
        assert result["success"] is False
        assert "桥未连接" in result["error"]


class TestWriteGate:
    async def test_write_blocked_by_default(self) -> None:
        host = _ToolsHost(allow_write=False)
        for coro in (
            host.send_at("g@chatroom", "朋友甲", "hi"),
            host.recall_last("wxid_a"),
            host.forward_msg("wxid_a", "朋友甲"),
            host.moment_like(publisher="朋友甲"),
            host.moment_comment("赞", publisher="朋友甲"),
            host.label_send("同事", "通知"),
        ):
            result = _result(await coro)
            assert result["success"] is False
            assert "allow_write" in result["error"]

    async def test_write_passes_when_enabled(self) -> None:
        host = _ToolsHost(allow_write=True)
        result = _result(await host.recall_last("wxid_a"))
        assert result["success"] is True
        assert host._transport.calls[0][0] == "recall_last"

    async def test_send_at_group_only(self) -> None:
        host = _ToolsHost(allow_write=True)
        result = _result(await host.send_at("wxid_a", "朋友甲", "hi"))
        assert result["success"] is False
        assert "群聊" in result["error"]

    async def test_forward_targets_limit(self) -> None:
        host = _ToolsHost(allow_write=True)
        result = _result(await host.forward_msg(
            "wxid_a", ",".join(f"人{i}" for i in range(10))))
        assert result["success"] is False
        assert "9" in result["error"]

    async def test_moment_like_needs_locator(self) -> None:
        host = _ToolsHost(allow_write=True)
        result = _result(await host.moment_like())
        assert result["success"] is False

    async def test_label_send_dry_run_default(self) -> None:
        host = _ToolsHost(allow_write=True)
        result = _result(await host.label_send("同事", "通知"))
        assert result["success"] is True
        _, params = host._transport.calls[0]
        assert params["dry_run"] is True  # 默认只解析不发送
