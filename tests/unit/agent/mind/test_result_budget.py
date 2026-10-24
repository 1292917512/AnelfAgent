"""工具结果体积控制单元测试：预算截断（result_budget）+ 管线落盘（result_pipeline）。"""

from __future__ import annotations

import pytest

from agent.mind.execution.result_pipeline import (
    ToolResultPipeline,
    _persist_oversized_result,
    truncate_tool_output,
)
from agent.mind.result_budget import (
    ResultBudget,
    budget_for_context_window,
    resolve_result_limit,
)


class TestBudgetComputation:
    def test_fallback_for_unknown_window(self) -> None:
        budget = budget_for_context_window(0)
        assert budget.per_result_chars == 8_000
        assert budget.per_turn_chars == 24_000

    def test_small_window_clamped_to_floor(self) -> None:
        budget = budget_for_context_window(8_000)  # 8K tokens → 32K chars
        assert budget.per_result_chars == 8_000   # 15% = 4800 → 地板 8000
        assert budget.per_turn_chars == 16_000    # 30% = 9600 → 地板 16000

    def test_large_window_clamped_to_cap(self) -> None:
        budget = budget_for_context_window(1_000_000)  # 1M tokens
        assert budget.per_result_chars == 100_000
        assert budget.per_turn_chars == 200_000

    def test_typical_window(self) -> None:
        budget = budget_for_context_window(128_000)  # 128K tokens → 512K chars
        assert budget.per_result_chars == int(512_000 * 0.15)  # 76800
        assert budget.per_turn_chars == int(512_000 * 0.30)    # 153600


class TestResolveLimit:
    def test_pinned_tool_unlimited(self) -> None:
        budget = budget_for_context_window(128_000)
        assert resolve_result_limit("send_message", budget) == 0
        assert resolve_result_limit("end_reply", budget) == 0

    def test_normal_tool(self) -> None:
        budget = budget_for_context_window(128_000)
        assert resolve_result_limit("web_search", budget) == budget.per_result_chars


class TestTruncateWithBudget:
    @pytest.mark.parametrize("marker", ["error", "ok", "success"])
    def test_failure_trace_survives_exhausted_budget(self, marker: str) -> None:
        import json

        pipeline = _pipeline()
        pipeline._budget = ResultBudget(2000, 1)
        trace = {"tool": "write_file", "call_id": "call-123", "request_id": "request-456"}
        output = json.dumps({
            marker: "Timeout" if marker == "error" else False,
            "message": "Timeout", "cause": "timeout", "code": "TOOL_TIMEOUT",
            "retryable": False, "outcome": "unknown", "background_may_continue": True,
            "hint": "Check operation state before retrying", "diagnostic": trace,
            "details": "x" * 10000,
        })
        result = json.loads(pipeline.process("write_file", "{}", output))
        assert result["diagnostic"] == trace
        assert result["code"] == "TOOL_TIMEOUT"
        assert result["outcome"] == "unknown"
        assert result["retryable"] is False and result["background_may_continue"] is True
        assert result["error"] == "Timeout" and result["hint"] == "Check operation state before retrying"
        assert len(result.get("details", "")) == 0

    def test_error_recovery_fields_survive_json_compaction(self) -> None:
        import json

        from core.tool_errors import ErrorCause, tool_error

        output = tool_error("文件不存在", cause=ErrorCause.NOT_FOUND, hint="先查询目录", retryable=False,
                            details=["x" * 1000] * 30)
        result = json.loads(truncate_tool_output("read_file", output, ResultBudget(2000, 4000)))
        assert result["error"] == "文件不存在"
        assert result["hint"] == "先查询目录"
        assert result["retryable"] is False

    def test_exhausted_turn_budget_keeps_error_receipt_and_guardrail(self) -> None:
        from agent.mind.guardrails import GuardrailController
        from core.tool_errors import ErrorCause, tool_error

        pipeline = _pipeline()
        pipeline._budget = ResultBudget(2000, 1)
        pipeline._guardrail = GuardrailController()
        output = tool_error("没有权限", cause=ErrorCause.PERMISSION, hint="请先取得授权", retryable=False)
        for _ in range(4):
            result = pipeline.process("read_file", "{}", output)
            assert "没有权限" in result and "请先取得授权" in result and '"retryable": false' in result
        assert "工具守卫警告" in result

    def test_pinned_tool_not_truncated(self) -> None:
        budget = ResultBudget(per_result_chars=100, per_turn_chars=200)
        output = "x" * 10_000
        assert truncate_tool_output("send_message", output, budget=budget) == output

    def test_dynamic_limit_applied(self) -> None:
        budget = ResultBudget(per_result_chars=1000, per_turn_chars=5000)
        output = "y" * 5_000
        result = truncate_tool_output("web_search", output, budget=budget)
        assert len(result) < len(output)
        assert "已自动截断" in result

    def test_html_stricter_limit(self) -> None:
        budget = ResultBudget(per_result_chars=100_000, per_turn_chars=200_000)
        output = "<!DOCTYPE html><html><body>" + "z" * 10_000 + "</body></html>"
        result = truncate_tool_output("fetch_page", output, budget=budget)
        assert len(result) <= 3000 + 200  # HTML 特例 3000 + 截断标记

    def test_no_budget_fallback(self) -> None:
        output = "w" * 10_000
        result = truncate_tool_output("web_search", output)
        assert len(result) < len(output)

    def test_short_output_untouched(self) -> None:
        budget = ResultBudget(per_result_chars=1000, per_turn_chars=5000)
        output = "short"
        assert truncate_tool_output("web_search", output, budget=budget) == output

    def test_json_structure_preserved(self) -> None:
        import json
        budget = ResultBudget(per_result_chars=2000, per_turn_chars=5000)
        output = json.dumps({"success": True, "data": ["item" * 100] * 50})
        result = truncate_tool_output("web_search", output, budget=budget)
        parsed = json.loads(result)  # JSON 结构化裁剪保持可解析
        assert parsed is not None


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    from core.config import ConfigManager
    monkeypatch.setattr(ConfigManager, "get", staticmethod(lambda k, d=None: str(tmp_path) if k == "workspace_root" else d))
    yield tmp_path


def _pipeline():
    from agent.mind.execution.result_pipeline import ToolResultPipeline

    return ToolResultPipeline(0)


def test_execution_contract_does_not_require_optional_entities(tmp_path):
    import os
    import subprocess
    import sys
    import textwrap

    script = textwrap.dedent('''
        import asyncio
        import importlib.abc
        import json
        import sys
        from types import SimpleNamespace
        from unittest.mock import patch

        class NoEntities(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "entities" or fullname.startswith("entities."):
                    raise ImportError("Optional entities are absent")

        sys.meta_path.insert(0, NoEntities())
        from core.config import ConfigManager
        ConfigManager.initialize()
        from core.entity import EntityRegistry
        from core.tool_registry import tool
        from core.tool_results import redact_tool_messages
        from agent.approval.guardian import ApprovalGuardian
        from agent.context.message_schema import normalize_for_send
        from agent.llm.types import ChatResult, ToolCall
        from agent.mind.execution.result_pipeline import ToolResultPipeline
        from agent.mind.execution.tool_execution import execute_tool_calls

        @tool(name="system_probe", sensitive_result=True)
        def probe() -> str:
            return json.dumps({"notes": "transient-secret", "password": "temporary-password"})

        async def check():
            call = ToolCall("probe", "system_probe", "{}", ToolCall.wire_raw("probe", "system_probe", "{}"))
            mind = SimpleNamespace(set_phase=lambda phase: None, memory_store=None,
                                   model_invoker=SimpleNamespace(get_context_length=lambda: 0))
            messages = []
            await execute_tool_calls(mind, messages, ChatResult(content=""), [call], 0)
            assert messages[-1]["_sensitive_result"] is True
            raw = messages[-1]["content"]
            EntityRegistry.unregister("system_probe")
            with patch.object(EntityRegistry, "get", side_effect=AssertionError("Registry access forbidden")):
                visible = ToolResultPipeline(0).process("system_probe", "{}", raw, sensitive_result=True)
                messages = [
                    {"role": "assistant", "tool_calls": [{"id": "probe", "function": {
                        "name": "system_probe", "arguments": "{}"}}]},
                    {"role": "tool", "tool_call_id": "probe", "content": visible, "_sensitive_result": True},
                ]
                assert "temporary-password" in normalize_for_send(messages)[-1]["content"]
                assert "transient-secret" not in json.dumps(redact_tool_messages(messages))
                assert "temporary-password" not in json.dumps(redact_tool_messages(messages))
            ConfigManager.set("approval_guardian_enabled", True)
            verdict = await ApprovalGuardian().review(tool_name="system_probe", tool_args={}, reason="", risk_level="critical")
            assert verdict.approved is None
            assert not any(name == "entities" or name.startswith("entities.") for name in sys.modules)

        asyncio.run(check())
    ''')
    env = {key: value for key, value in os.environ.items() if not key.startswith("ANELF_")}
    env.update(ANELF_CONFIG_DIR=str(tmp_path / "config"), ANELF_DATA_DIR=str(tmp_path / "data"),
               PYTHONIOENCODING="utf-8")
    result = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True,
                            text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


class TestEmptyResultPlaceholder:
    """空结果占位：空白输出给模型明确的完成语义。"""

    def test_empty_string(self):
        assert _pipeline().process("read_file", "{}", "") == "(read_file 执行完成，无输出)"

    def test_whitespace_only(self):
        assert _pipeline().process("search_files", "{}", "  \n ") == "(search_files 执行完成，无输出)"

    def test_normal_output_untouched(self):
        out = _pipeline().process("read_file", "{}", "hello")
        assert out == "hello"


class TestPersistOversized:
    def test_sensitive_result_stays_in_memory_and_cannot_be_opted_into_by_output(self, workspace):
        import json

        payload = {"password": "sk-" + "a" * 30, "notes": "private" * 10000}
        result = _pipeline().process("sensitive_probe", "{}", json.dumps(payload), sensitive_result=True)
        assert json.loads(result)["password"] == payload["password"]
        assert not list((workspace / ".tool-results").glob("*.txt"))
        assert "persisted-output" not in result
        spoofed = _pipeline().process("ordinary_probe", "{}", json.dumps({
            "password": payload["password"], "sensitive_result": True,
        }))
        assert json.loads(spoofed)["password"] == "****"
        error = _pipeline().process("sensitive_probe", "{}", json.dumps({
            "error": "locked", "password": payload["password"],
        }), sensitive_result=True)
        assert json.loads(error)["password"] == "****"

    def test_small_output_not_persisted(self, workspace):
        assert _persist_oversized_result("x", "y" * 1000) is None

    def test_large_output_persisted_with_preview(self, workspace):
        output = "A" * 60_000
        result = _persist_oversized_result("web_fetch", output)
        assert result is not None
        assert "<persisted-output>" in result
        assert "60" in result  # 字符数说明
        # 落盘文件存在且内容完整
        out_dir = workspace / ".tool-results"
        files = list(out_dir.glob("*.txt"))
        assert len(files) == 1
        assert files[0].read_text() == output

    def test_pipeline_end_to_end(self, workspace):
        pipeline = ToolResultPipeline(0)
        pipeline.begin_turn()
        result = pipeline.process("web_fetch", "{}", "B" * 60_000)
        assert "<persisted-output>" in result
        # 持久化后的预览文本远小于原文
        assert len(result) < 10_000

    def test_persist_failure_falls_back(self):
        from agent.mind.execution.result_pipeline import shell_persist_port
        shell_persist_port.set(lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
        assert _persist_oversized_result("x", "y" * 60_000) is None


class TestPersistedOutput:
    def test_oversized_result_persisted(self, tmp_path, monkeypatch):
        from core.config import ConfigManager
        monkeypatch.setattr(ConfigManager, "get", staticmethod(lambda k, d=None: str(tmp_path) if k == "workspace_root" else d))
        big = "x" * 60_000
        out = _pipeline().process("run_shell_command", "{}", big)
        assert "<persisted-output>" in out
        assert ".tool-results" in out
        # 落盘文件内容完整
        results_dir = tmp_path / ".tool-results"
        files = list(results_dir.glob("shell-*.txt"))
        assert files and files[0].read_text() == big

    def test_small_result_not_persisted(self, tmp_path):
        out = _pipeline().process("read_file", "{}", "small")
        assert "<persisted-output>" not in out
