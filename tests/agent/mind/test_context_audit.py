"""请求级全量审计日志（agent.mind.context_audit）单元测试。

审计文件包含完整对话内容，默认关闭；开启后每次 LLM 交换以 JSONL
追加到日轮转文件，写失败静默降级绝不影响主流程。
"""

from __future__ import annotations

import json
import os

import agent.mind.context_audit as audit


def _enable(monkeypatch, tmp_path, *, log_tools: bool = False) -> str:
    """开启审计并指向临时目录，返回审计目录路径。"""
    monkeypatch.setattr(audit, "is_enabled", lambda: True)
    monkeypatch.setattr(audit, "_audit_dir", lambda: str(tmp_path))
    monkeypatch.setattr(audit, "_log_full_tools", lambda: log_tools)
    return str(tmp_path)


def _read_records(audit_dir: str) -> list[dict]:
    files = [f for f in os.listdir(audit_dir) if f.endswith(".jsonl")]
    assert len(files) == 1, "应只产生一个日轮转文件"
    with open(os.path.join(audit_dir, files[0]), encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class _FakeUsage:
    prompt_tokens = 100
    completion_tokens = 20
    total_tokens = 120


class _FakeToolCall:
    id = "call_1"
    name = "web_search"
    arguments = "{}"


class _FakeResult:
    model = "test-model"
    finish_reason = "stop"
    content = "回复内容"
    reasoning_content = "推理过程"
    tool_calls = [_FakeToolCall()]
    usage = _FakeUsage()


class TestRecordExchange:
    async def test_disabled_no_write(self, monkeypatch, tmp_path) -> None:
        """默认关闭：零开销直接返回，不产生文件。"""
        monkeypatch.setattr(audit, "is_enabled", lambda: False)
        monkeypatch.setattr(audit, "_audit_dir", lambda: str(tmp_path))
        await audit.record_exchange(model="m", messages=[{"role": "user", "content": "hi"}], tools=None)
        assert not os.listdir(tmp_path)

    async def test_full_exchange_recorded(self, monkeypatch, tmp_path) -> None:
        audit_dir = _enable(monkeypatch, tmp_path)
        messages = [
            {"role": "system", "content": "人设"},
            {"role": "user", "content": "你好"},
        ]
        tools = [{"type": "function", "function": {"name": "web_search", "parameters": {}}}]
        await audit.record_exchange(
            model="test-model", messages=messages, tools=tools,
            result=_FakeResult(), duration_ms=123.4, scope="user_1",
        )
        (record,) = _read_records(audit_dir)
        assert record["model"] == "test-model"
        assert record["scope"] == "user_1"
        assert record["duration_ms"] == 123.4
        assert record["messages"] == messages, "最终发送的 messages 应完整落盘"
        # 默认只记工具名，不记完整 schema
        assert record["tools"] == ["web_search"]
        resp = record["response"]
        assert resp["content"] == "回复内容"
        assert resp["reasoning_content"] == "推理过程"
        assert resp["tool_calls"][0]["name"] == "web_search"
        assert resp["usage"]["total_tokens"] == 120
        assert "error" not in record

    async def test_full_tools_when_enabled(self, monkeypatch, tmp_path) -> None:
        audit_dir = _enable(monkeypatch, tmp_path, log_tools=True)
        tools = [{"type": "function", "function": {"name": "web_search", "parameters": {"type": "object"}}}]
        await audit.record_exchange(model="m", messages=[], tools=tools)
        (record,) = _read_records(audit_dir)
        assert record["tools"] == tools

    async def test_error_exchange_recorded(self, monkeypatch, tmp_path) -> None:
        audit_dir = _enable(monkeypatch, tmp_path)
        await audit.record_exchange(
            model="m", messages=[{"role": "user", "content": "hi"}], tools=None,
            error=RuntimeError("连接超时"), duration_ms=5.0,
        )
        (record,) = _read_records(audit_dir)
        assert "RuntimeError" in record["error"]
        assert "连接超时" in record["error"]
        assert record["response"] is None

    async def test_write_failure_never_raises(self, monkeypatch, tmp_path) -> None:
        """写文件失败静默降级为调试日志，绝不影响主流程。"""
        _enable(monkeypatch, tmp_path)

        def _boom(path, record):
            raise OSError("磁盘只读")

        monkeypatch.setattr(audit, "_append_jsonl", _boom)
        # 不应抛出
        await audit.record_exchange(model="m", messages=[], tools=None, result=_FakeResult())
