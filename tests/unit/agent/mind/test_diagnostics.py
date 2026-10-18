"""诊断观测的并发归属、内存边界与模型可见事实。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.channel.delivery import delivery_tags
from agent.mind.diagnostic_query import recent_diagnostics
from agent.mind.diagnostics import MAX_RUNS, MAX_STEPS, execution_diagnostics
from agent.mind.tools import session_tools
from core.activity import activity_scope
from core.event_bus import event_bus
from core.tool_context import tool_request


@pytest.fixture
def diagnostics():
    store = execution_diagnostics
    store.stop()
    store.start()
    yield store
    store.stop()


async def test_parallel_calls_are_correlated_and_duration_is_wall_time(diagnostics, monkeypatch):
    clock = [10.0]
    monkeypatch.setattr("agent.mind.diagnostics.time", SimpleNamespace(monotonic=lambda: clock[0], time=lambda: 1000 + clock[0]))
    async with activity_scope("main", "user_webui:me#one", kind="conversation"):
        for call_id in ("a", "b"):
            await event_bus.emit("thinking_llm_start", {"call_id": call_id, "model": "model"})
        clock[0] += 2
        await event_bus.emit("thinking_llm_end", {"call_id": "b", "model": "model-b", "duration_ms": 2000, "usage": {}})
        await event_bus.emit("thinking_llm_end", {"call_id": "a", "model": "model-a", "duration_ms": 2000, "ttft_ms": 500,
                                                   "usage": {"total_input_tokens": 1000, "cache_read_input_tokens": 900, "cache_observable": True}})
        await event_bus.emit("thinking_tool_start", {"tool_id": "a", "tool_name": "ssh_exec"})
        clock[0] += 3
        live = diagnostics.snapshot()["runs"][0]
        assert live["duration_ms"] == 5000
        assert live["active_steps"][0]["duration_ms"] == 3000
    result = diagnostics.snapshot()["runs"][0]
    assert result["duration_ms"] == 5000
    assert [(s["id"], s["name"]) for s in result["steps"][:2]] == [("a", "model-a"), ("b", "model-b")]
    assert result["steps"][0]["usage"]["cache_read_input_tokens"] == 900
    assert result["steps"][1]["usage"] == {}
    assert result["steps"][2]["status"] == "interrupted"
    assert not result["active_steps"]


async def test_nested_and_concurrent_scopes_do_not_mix(diagnostics):
    async def run(turn, scope):
        with tool_request(scope=scope):
            async with activity_scope(turn, scope):
                await asyncio.sleep(0)
                async with activity_scope(turn + "-child", "reflect:child"):
                    await event_bus.emit("thinking_tool_start", {"tool_id": "same-id", "tool_name": turn})
                    await event_bus.emit("thinking_tool_end", {"tool_id": "same-id", "success": True, "duration_ms": 20, "result": "ok"})
    await asyncio.gather(run("a", "user_webui:me#a"), run("b", "user_qq:me"))
    records = diagnostics.snapshot(scope="user_qq:me")["runs"]
    assert {record["id"] for record in records} == {"b", "b-child"}
    assert records[0]["parent_id"] == "b"
    assert records[0]["steps"][0]["name"] == "b"

    async with activity_scope("current", "user_qq:me"):
        with tool_request(scope="user_qq:me"):
            for i in range(5):
                async with activity_scope(f"child-{i}", "reflect:child"):
                    pass
        result = diagnostics.snapshot(scope="user_qq:me")
        assert result["current_turn_id"] == "current"
        assert result["runs"][0]["id"] == "current"


async def test_receipts_are_facts_and_generated_text_is_not_delivery(diagnostics):
    async with activity_scope("run", "user_qq:me"):
        await event_bus.emit("thinking_llm_start", {"call_id": "model"})
        await event_bus.emit("thinking_llm_end", {"call_id": "model", "content_preview": "I sent it"})
        await event_bus.emit("thinking_tool_start", {"tool_id": "send", "tool_name": "send_message"})
        receipt = delivery_tags("channel", "sent", "user_webui:me#a")
        await event_bus.emit("thinking_tool_end", {"tool_id": "send", "success": True, "result": json.dumps({"delivery": receipt})})
        await event_bus.emit("activity_context_update", {"origin": "delivery", "items": [{"content": delivery_tags("auto", "skipped", "user_qq:me")}]})
        await event_bus.emit("thinking_tool_start", {"tool_id": "self", "tool_name": "get_conversation_diagnostics"})
        await event_bus.emit("thinking_tool_end", {"tool_id": "self", "success": True, "duration_ms": 2, "result": '{"diagnostics":"must not recurse"}'})
    steps = diagnostics.snapshot()["runs"][0]["steps"]
    assert "delivery" not in steps[0]
    assert steps[1]["delivery"] == {"mode": "channel", "status": "sent", "scope": "user_webui:me#a"}
    assert steps[2]["delivery"]["status"] == "skipped"
    assert steps[3]["preview"] == ""
    assert steps[3]["duration_ms"] == 2


async def test_bounded_records_redaction_late_hooks_and_stop(diagnostics):
    async with activity_scope("root", "user_qq:me"):
        for i in range(MAX_STEPS + 4):
            await event_bus.emit("thinking_tool_start", {"tool_id": str(i), "tool_name": "inspect", "arguments": 'password="sensitive secret"'})
            await event_bus.emit("thinking_tool_end", {"tool_id": str(i), "success": False, "error": "x" * 20000})
        await event_bus.emit("hook_execution", {"id": "hook", "name": "review", "status": "running"})
    run = diagnostics.snapshot(steps=MAX_STEPS)["runs"][0]
    assert len(run["steps"]) == MAX_STEPS
    assert run["omitted_steps"] == 5
    assert run["active_steps"][0]["name"] == "review"
    assert "sensitive secret" not in json.dumps(run)
    assert len(run["steps"][0]["error"]) < 600
    for i in range(MAX_RUNS):
        async with activity_scope(str(i), "user_qq:me"):
            pass
    assert "root" in diagnostics.snapshot()["active_run_ids"]
    await event_bus.emit("hook_execution", {"turn_id": "root", "id": "hook", "name": "review", "status": "done", "duration_ms": 4500})
    assert not diagnostics.snapshot()["runs"][0]["active_steps"]
    for i in range(MAX_RUNS):
        async with activity_scope(f"finished-{i}", "user_qq:me"):
            pass
    assert diagnostics.snapshot()["retention"]["evicted_runs"] == MAX_RUNS + 1
    assert diagnostics.snapshot(turn_id="root")["runs"] == []
    diagnostics.stop()
    assert not diagnostics.snapshot()["collecting"]
    assert not diagnostics.scopes()


async def test_message_query_is_read_only_memory_and_ai_uses_same_contract(diagnostics, sqlite, monkeypatch):
    await sqlite.fetch_live_conversation(scope_type="user", scope_id="webui:me#a", limit=8)
    for role, content, ts in [("user", "question", 200), ("assistant", 'password="private value"', 100)]:
        await sqlite.append_conversation(scope_type="user", scope_id="webui:me#a", role=role, content=content, ts_ns=ts)
    monkeypatch.setattr(sqlite, "_get_db", AsyncMock(side_effect=AssertionError("diagnostics must not access database")))
    async with activity_scope("run", "user_webui:me#a"):
        result = recent_diagnostics(sqlite, scope="user_webui:me#a", store=diagnostics)
        excerpts = result["conversations"][0]
        assert excerpts["loaded"]
        assert [row["role"] for row in excerpts["messages"]] == ["user", "assistant"]
        assert "private value" not in json.dumps(result)
        assert recent_diagnostics(sqlite, scope="user_webui:me#b", store=diagnostics)["conversations"][0] == {"scope": "user_webui:me#b", "loaded": False, "messages": []}
        from core.conversation_scope import bind_scope, reset_scope
        token = bind_scope("user_webui:me#a")
        monkeypatch.setattr(session_tools, "mind_port", SimpleNamespace(bound=True, get=lambda: SimpleNamespace(conversation_data=SimpleNamespace(router=SimpleNamespace(sqlite=sqlite)))))
        try:
            response = json.loads(await session_tools.get_conversation_diagnostics())
            assert response["conversations"] == result["conversations"]
            assert response["runs"][0]["id"] == "run"
            assert response["current_turn_id"] == "run"
        finally:
            reset_scope(token)
        assert sqlite.peek_live_conversation(scope_type="user", scope_id="webui:me#a")[0][1]["content"] == 'password="private value"'
