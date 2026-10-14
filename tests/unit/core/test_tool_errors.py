"""工具错误统一设施（core.tool_errors）单元测试。"""

from __future__ import annotations

import asyncio
import json

import pytest

from core.tool_errors import ErrorCause, error_from_exception, tool_error
from core.tool_results import extract_error_text


def _parse(result: str) -> dict:
    return json.loads(result)


@pytest.mark.parametrize(("payload", "expected"), [
    ({"ok": False, "exit_code": 1, "stdout": "NO_SUDO\n/proc/example/cwd", "stderr": ""},
     "命令退出码 1；stdout: NO_SUDO /proc/example/cwd"),
    ({"ok": False, "exit_code": 127, "stderr": "command not found"},
     "命令退出码 127；stderr: command not found"),
    ({"ok": False, "returncode": 2, "stdout": "", "stderr": " "}, "命令退出码 2（无错误输出）"),
    ({"ok": False, "exit_code": 1, "notes": ["搜索没有匹配"]}, "搜索没有匹配"),
    ({"ok": True, "exit_code": 0, "stdout": "NO_SUDO", "stderr": "warning"}, ""),
    ({"success": False, "error": "explicit failure", "exit_code": 1}, "explicit failure"),
])
def test_command_error_evidence(payload: dict, expected: str) -> None:
    assert extract_error_text(payload) == expected


def test_command_stdout_summary_is_bounded() -> None:
    summary = extract_error_text({"ok": False, "exit_code": 1, "stdout": "NO_SUDO\n" + "x" * 10000})
    assert summary.startswith("命令退出码 1；stdout: NO_SUDO ")
    assert len(summary) <= 151


class TestToolError:
    def test_minimal_envelope(self) -> None:
        payload = _parse(tool_error("目标不存在"))
        assert payload == {"error": "目标不存在"}

    def test_full_envelope(self) -> None:
        payload = _parse(tool_error(
            "频道不存在", cause=ErrorCause.NOT_FOUND,
            hint="使用 list_channels 获取可用频道", retryable=False,
            available_channels=["webui"],
        ))
        assert payload["error"] == "频道不存在"
        assert payload["cause"] == "not_found"
        assert payload["hint"] == "使用 list_channels 获取可用频道"
        assert payload["retryable"] is False
        assert payload["available_channels"] == ["webui"]

    def test_none_context_omitted(self) -> None:
        payload = _parse(tool_error("失败", detail=None))
        assert "detail" not in payload

    def test_error_key_is_primary_signal(self) -> None:
        # 框架检测逻辑（round_helpers._extract_error_text）依赖 error 键识别失败
        payload = _parse(tool_error("x", cause=ErrorCause.INTERNAL))
        assert payload.get("error")


class TestErrorFromException:
    def test_timeout(self) -> None:
        payload = _parse(error_from_exception(TimeoutError("timed out"), action="请求接口"))
        assert payload["cause"] == "timeout"
        assert payload["retryable"] is False
        assert payload["outcome"] == "unknown"
        assert payload["error"].startswith("请求接口失败")

    def test_asyncio_timeout(self) -> None:
        payload = _parse(error_from_exception(asyncio.TimeoutError()))
        assert payload["cause"] == "timeout"

    def test_httpx_timeout_by_name(self) -> None:
        class ConnectTimeout(Exception):
            pass

        payload = _parse(error_from_exception(ConnectTimeout("pool timeout")))
        assert payload["cause"] == "timeout"
        assert payload["retryable"] is False

    def test_permission(self) -> None:
        payload = _parse(error_from_exception(PermissionError("/root/secret"), action="读取文件"))
        assert payload["cause"] == "permission"
        assert payload["retryable"] is False

    def test_file_not_found(self) -> None:
        payload = _parse(error_from_exception(FileNotFoundError("/tmp/x.txt"), action="读取文件"))
        assert payload["cause"] == "not_found"
        assert payload["retryable"] is False

    def test_json_decode(self) -> None:
        exc = json.JSONDecodeError("Expecting value", "doc", 0)
        payload = _parse(error_from_exception(exc))
        assert payload["cause"] == "param"

    def test_connection_error(self) -> None:
        payload = _parse(error_from_exception(ConnectionRefusedError("refused"), action="请求 https://a.b"))
        assert payload["cause"] == "network"
        assert payload["retryable"] is False

    def test_declared_safe_retry_preserves_uncertain_outcome(self) -> None:
        for exc in (TimeoutError(), ConnectionResetError()):
            payload = _parse(error_from_exception(exc, retry_safe=True))
            assert payload["retryable"] is True
            assert payload["outcome"] == "unknown"

    def test_value_error_is_param(self) -> None:
        payload = _parse(error_from_exception(ValueError("bad value")))
        assert payload["cause"] == "param"

    def test_unknown_is_internal(self) -> None:
        payload = _parse(error_from_exception(RuntimeError("boom")))
        assert payload["cause"] == "internal"
        assert payload["retryable"] is False
        assert "RuntimeError" in payload["error"]

    def test_detail_truncated(self) -> None:
        payload = _parse(error_from_exception(RuntimeError("x" * 1000)))
        assert len(payload["error"]) < 400
        assert payload["error"].endswith("…")

    def test_custom_hint_overrides_default(self) -> None:
        payload = _parse(error_from_exception(TimeoutError("t"), hint="自定义建议"))
        assert payload["hint"] == "自定义建议"

    def test_no_action_no_prefix(self) -> None:
        payload = _parse(error_from_exception(PermissionError("denied")))
        assert payload["error"].startswith("权限不足")

    def test_framework_detection_compatible(self) -> None:
        # 模拟 round_helpers._extract_error_text 的判定:error 键非空即识别为失败
        for exc in (TimeoutError(), PermissionError(), RuntimeError("boom")):
            payload = _parse(error_from_exception(exc))
            assert payload.get("error")
