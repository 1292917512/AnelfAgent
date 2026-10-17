"""QQ API 参数契约与失败原因透传测试（不触网）。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any, Dict, Optional, Tuple
from unittest.mock import AsyncMock

import pytest

from agent.channel.tool_bridge import register_channel_tools, unregister_channel_tools
from channels.qq.tools import QQToolsMixin
from channels.qq.transport import QQTransport, _api_failure_text
from core.entity import EntityRegistry


class _ChannelStub:
    """QQTransport 的最小频道替身（API 调用路径不触其属性）。"""


def _make_transport(raw_response: Optional[Dict[str, Any]]) -> QQTransport:
    transport = QQTransport(_ChannelStub())  # type: ignore[arg-type]
    transport.call_api_raw = AsyncMock(return_value=raw_response)  # type: ignore[method-assign]
    return transport


class TestApiFailureText:
    """失败响应原因文本提取。"""

    def test_retcode_with_wording(self) -> None:
        text = _api_failure_text({"retcode": 1200, "wording": "群不存在", "message": ""})
        assert text == "retcode=1200 群不存在"

    def test_message_fallback(self) -> None:
        text = _api_failure_text({"retcode": 1404, "message": "unsupported action"})
        assert text == "retcode=1404 unsupported action"

    def test_retcode_only(self) -> None:
        assert _api_failure_text({"retcode": 100}) == "retcode=100"


class TestCallApiDataDetail:
    """call_api_data_detail 的成败三态。"""

    async def test_success_returns_data_with_empty_detail(self) -> None:
        transport = _make_transport({"retcode": 0, "data": {"messages": [1, 2]}})
        data, detail = await transport.call_api_data_detail("get_group_msg_history", {})
        assert data == {"messages": [1, 2]}
        assert detail == ""

    async def test_retcode_failure_returns_reason(self) -> None:
        transport = _make_transport(
            {"status": "failed", "retcode": 1200, "wording": "消息undefined不存在"}
        )
        data, detail = await transport.call_api_data_detail("get_group_msg_history", {})
        assert data is None
        assert "1200" in detail
        assert "消息undefined不存在" in detail

    async def test_no_response_returns_generic_reason(self) -> None:
        transport = _make_transport(None)
        data, detail = await transport.call_api_data_detail("get_group_msg_history", {})
        assert data is None
        assert detail

    async def test_call_api_data_keeps_legacy_contract(self) -> None:
        transport = _make_transport({"retcode": 1200, "wording": "x"})
        assert await transport.call_api_data("any", {}) is None
        transport = _make_transport({"retcode": 0, "data": [1]})
        assert await transport.call_api_data("any", {}) == [1]


class _ToolStub(QQToolsMixin):
    """表驱动工具调用替身：_call_api_detail 返回预置结果。"""

    channel_id = "qq_schema_test"

    def __init__(self, detail_result: Tuple[Optional[Any], str]) -> None:
        self._detail_result = detail_result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def _call_api_detail(self, action: str, params: Dict[str, Any]) -> Tuple[Optional[Any], str]:
        self.calls.append((action, params))
        return self._detail_result

    async def _call_api(self, action: str, params: Dict[str, Any]) -> bool:
        self.calls.append((action, params))
        return True


@pytest.fixture
def registered_qq_tools() -> Iterator[_ToolStub]:
    stub = _ToolStub(({"messages": [{"message_id": 1}]}, ""))
    register_channel_tools(stub)
    try:
        yield stub
    finally:
        unregister_channel_tools(stub.channel_id)


@pytest.mark.parametrize("action,target_key,api_key", [
    ("get_group_msg_history", "target_id", "group_id"),
    ("get_friend_msg_history", "user_id", "user_id"),
])
@pytest.mark.parametrize("arguments,expected_count", [
    ({}, 20), ({"count": 20}, 20), ({"count": "20"}, 20), ({"count": 20.0}, 20), ({"count": "500"}, 200),
])
async def test_registered_history_tool_sends_numeric_count(
    registered_qq_tools: _ToolStub, action: str, target_key: str, api_key: str,
    arguments: dict[str, Any], expected_count: int,
) -> None:
    name = f"{registered_qq_tools.channel_id}_{action}"
    schema = EntityRegistry.get_tool_schema_by_names([name])[0]["function"]["parameters"]
    assert schema["properties"]["count"]["type"] == "integer"
    payload = json.loads(await EntityRegistry.execute_tool(
        name, json.dumps({target_key: "110", **arguments}),
    ))
    assert payload["success"] is True
    assert payload["messages"] == [{"message_id": 1}]
    assert registered_qq_tools.calls == [(action, {api_key: 110, "count": expected_count})]
    assert type(registered_qq_tools.calls[0][1]["count"]) is int


@pytest.mark.parametrize("count", ["invalid", "1.5", 1.5, True, False, None])
async def test_registered_history_tool_rejects_invalid_count(
    registered_qq_tools: _ToolStub, count: object,
) -> None:
    payload = json.loads(await EntityRegistry.execute_tool(
        f"{registered_qq_tools.channel_id}_get_group_msg_history",
        json.dumps({"target_id": "110", "count": count}),
    ))
    assert payload["cause"] == "param"
    assert "count" in payload["error"]
    assert registered_qq_tools.calls == []


@pytest.mark.parametrize("arguments,expected", [
    ({}, True), ({"approve": True}, True), ({"approve": False}, False),
    ({"approve": "true"}, True), ({"approve": "false"}, False),
    ({"approve": 1}, True), ({"approve": 0}, False),
    ({"approve": "1"}, True), ({"approve": "0"}, False),
])
async def test_registered_table_tool_sends_boolean(
    registered_qq_tools: _ToolStub, arguments: dict[str, Any], expected: bool,
) -> None:
    payload = json.loads(await EntityRegistry.execute_tool(
        f"{registered_qq_tools.channel_id}_set_group_add_request",
        json.dumps({"flag": "request-1", **arguments}),
    ))
    assert payload["success"] is True
    assert registered_qq_tools.calls == [(
        "set_group_add_request", {"flag": "request-1", "approve": expected, "reason": ""},
    )]
    assert registered_qq_tools.calls[0][1]["approve"] is expected


@pytest.mark.parametrize("approve", ["invalid", 2, None])
async def test_registered_table_tool_rejects_invalid_boolean(
    registered_qq_tools: _ToolStub, approve: object,
) -> None:
    payload = json.loads(await EntityRegistry.execute_tool(
        f"{registered_qq_tools.channel_id}_set_group_add_request",
        json.dumps({"flag": "request-1", "approve": approve}),
    ))
    assert payload["cause"] == "param"
    assert "approve" in payload["error"]
    assert registered_qq_tools.calls == []


class TestTableToolErrorDetail:
    """表驱动工具失败时错误消息携带 OneBot 原因。"""

    async def test_error_carries_wording(self) -> None:
        stub = _ToolStub((None, "retcode=1200 消息undefined不存在"))
        raw = await stub.get_group_msg_history(chat_id="110", count=20)
        payload = json.loads(raw)
        assert payload["success"] is False
        assert "获取群消息历史失败" in payload["error"]
        assert "消息undefined不存在" in payload["error"]

    async def test_error_without_detail_keeps_base_message(self) -> None:
        stub = _ToolStub((None, ""))
        raw = await stub.get_group_msg_history(chat_id="110", count=20)
        payload = json.loads(raw)
        assert payload["success"] is False
        assert payload["error"] == "获取群消息历史失败"

    async def test_success_unaffected(self) -> None:
        stub = _ToolStub(({"messages": [{"message_id": 1}]}, ""))
        raw = await stub.get_group_msg_history(chat_id="1104224649", count=20)
        payload = json.loads(raw)
        assert payload["success"] is True
        assert payload["messages"] == [{"message_id": 1}]
        assert payload["count"] == 1

    async def test_invalid_id_still_uses_param_error(self) -> None:
        stub = _ToolStub((None, "unreachable"))
        raw = await stub.get_group_msg_history(chat_id="not-a-number", count=20)
        payload = json.loads(raw)
        assert payload["success"] is False
        assert payload["error"] == "无效的群 ID: not-a-number"
