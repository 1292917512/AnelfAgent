"""QQ API 失败原因透传测试：retcode/wording 进工具错误消息（不触网）。"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple
from unittest.mock import AsyncMock

from channels.qq.tools import QQToolsMixin
from channels.qq.transport import QQTransport, _api_failure_text


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

    def __init__(self, detail_result: Tuple[Optional[Any], str]) -> None:
        self._detail_result = detail_result

    async def _call_api_detail(self, action: str, params: Dict[str, Any]) -> Tuple[Optional[Any], str]:
        return self._detail_result


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
