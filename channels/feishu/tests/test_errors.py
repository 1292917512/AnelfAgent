"""飞书错误归因测试（不触网）。"""

from __future__ import annotations

import json

from channels.feishu.errors import FeishuApiError, not_ready_json, raise_for_fail, to_error_json
from core.tool_errors import ErrorCause


class _FakeResp:
    def __init__(self, code: int = 0, msg: str = "ok") -> None:
        self.code = code
        self.msg = msg

    def success(self) -> bool:
        return self.code == 0


class TestRaiseForFail:
    def test_success_passes(self) -> None:
        raise_for_fail(_FakeResp(0), "发送消息")

    def test_failure_raises_with_code(self) -> None:
        try:
            raise_for_fail(_FakeResp(230002, "bot not in chat"), "发送消息")
            raise AssertionError("should raise")
        except FeishuApiError as exc:
            assert exc.code == 230002
            assert exc.api_msg == "bot not in chat"


class TestToErrorJson:
    def test_known_code_mapped(self) -> None:
        exc = FeishuApiError("发送消息", 230002, "bot not in the chat")
        payload = json.loads(to_error_json(exc, "发送消息"))
        assert payload["success"] is False
        assert payload["cause"] == ErrorCause.PERMISSION.value
        assert payload["retryable"] is False
        assert "邀请" in payload["hint"]
        assert payload["code"] == 230002

    def test_rate_limit_retryable(self) -> None:
        exc = FeishuApiError("发送消息", 99991400, "frequency limited")
        payload = json.loads(to_error_json(exc, "发送消息"))
        assert payload["cause"] == ErrorCause.NETWORK.value
        assert payload["retryable"] is True

    def test_unknown_code_internal(self) -> None:
        exc = FeishuApiError("置顶消息", 12345, "whatever")
        payload = json.loads(to_error_json(exc, "置顶消息"))
        assert payload["cause"] == ErrorCause.INTERNAL.value

    def test_generic_exception_attributed(self) -> None:
        payload = json.loads(to_error_json(TimeoutError("slow"), "读取会话历史"))
        assert payload["success"] is False
        assert payload["cause"] == ErrorCause.TIMEOUT.value


class TestNotReadyJson:
    def test_state_cause(self) -> None:
        payload = json.loads(not_ready_json())
        assert payload["success"] is False
        assert payload["cause"] == ErrorCause.STATE.value
