"""Timing joins must distinguish unrelated actions, cache observability and failed samples."""
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from channels.minecraft.scripts.benchmark_minecraft import TraceReader, reply_finished
from channels.minecraft.scripts.minecraft_benchmark_report import (
    first_cache_fraction,
    measure,
    percentile,
    summarize,
    summarize_runs,
)


def test_nearest_rank_keeps_missing_and_failure_denominators() -> None:
    assert percentile([], .95) is None
    assert percentile(list(range(1, 31)), .95) == 29
    rows = [{"scenario": "inventory", "cache": "unknown", "outcome": outcome,
             "timings": {"first_tool": value}, "model_calls": 1, "input_tokens": 4, "first_tool_count": 10}
            for outcome, value in [("verified", 20), ("failed", 80), ("timeout", None)]]
    report = summarize(rows)
    group = report["groups"]["inventory/unknown"]
    assert group["outcomes"] == {"verified": 1, "failed": 1, "timeout": 1}
    assert group["timings_all_observed_including_failures"]["first_tool"] == {
        "observed": 2, "missing": 1, "p50_ms": 20, "p95_ms": 80}
    assert not report["complete_30_per_cache"]


def test_correlates_identity_not_nearest_action_and_does_not_invent_cold_cache() -> None:
    origin = {"message_id": "mc-1", "request_id": "own", "scope": "group_minecraft:bench"}
    nodes: list[dict[str, Any]] = [
        {"label": "Minecraft ingress", "timestamp": 1.1, "data": {"message_id": "mc-1", "scope": origin["scope"]}},
        {"type": "llm_call", "timestamp": 1.2, "data": {"request": origin,
            "usage": {"cache_observable": False, "cache_read_input_tokens": 0, "total_input_tokens": 10}}},
        {"type": "tool_call", "timestamp": 1.3, "data": {"request": origin, "tool_name": "minecraft_get_inventory"}},
    ]
    state = {"events": [{"type": "action_progress", "data": {"origin": {"requestId": "other"}, "startedAt": 1301}}],
             "inventory": [], "chats": [{"at": 1600, "text": "result"}]}
    row = measure("inventory", 1000, nodes, state, scope=origin["scope"], timeout=False)
    assert row["outcome"] == "verified" and row["cache"] == "unknown"
    assert row["timings"]["action_started"] is None
    assert row["timings"]["first_tool"] == 300
    assert row["input_tokens"] == 10
    assert first_cache_fraction(row) is None


def test_background_models_and_tools_do_not_inflate_foreground_measurements() -> None:
    origin = {"message_id": "mc-1", "request_id": "own", "scope": "group_minecraft:bench"}
    nodes: list[dict[str, Any]] = [
        {"label": "Minecraft ingress", "timestamp": 1.1, "data": {"message_id": "mc-1", "scope": origin["scope"]}},
        {"type": "llm_call", "timestamp": 1.2, "data": {"request": origin, "purpose": "reply",
            "usage": {"cache_observable": True, "cache_read_input_tokens": 7, "total_input_tokens": 10}}},
        {"type": "llm_call", "timestamp": 1.3, "data": {"request": origin, "purpose": "reflect",
            "usage": {"cache_observable": True, "cache_read_input_tokens": 100, "total_input_tokens": 200}}},
        {"type": "tool_call", "timestamp": 1.4, "data": {"request": origin, "actor": "hook", "tool_name": "get_skill"}},
        {"type": "llm_call", "timestamp": 1.5, "data": {"request": {**origin, "scope": "group_qq:other"},
            "purpose": "reply", "usage": {"total_input_tokens": 300}}},
    ]
    row = measure("inventory", 1000, nodes, {"chats": [{"at": 900, "text": "old reply"}]},
                  scope=origin["scope"], timeout=False)
    assert row["model_calls"] == 1 and row["input_tokens"] == 10
    assert row["related_background_calls_observed"] == 1
    assert first_cache_fraction(row) == .7
    assert row["tool_names"] == [] and row["timings"]["first_tool"] is None
    assert row["timings"]["first_chat"] is None and row["outcome"] == "failed"


def test_sse_updates_preserve_node_start_and_drop_reasoning() -> None:
    trace = TraceReader()
    trace.accept("node_added", {"session_id": "s", "node": {
        "id": "n", "type": "llm_call", "timestamp": 1.2, "data": {"model": "m"}}})
    trace.accept("node_updated", {"node_id": "n", "updates": {"duration_ms": 100, "data": {
        "model": "m", "reasoning_content": "private", "usage": {"cache_observable": False}}}})
    trace.accept("session_end", {"session_id": "s"})
    assert trace.nodes["n"]["timestamp"] == 1.2
    assert trace.nodes["n"]["duration_ms"] == 100
    assert "private" not in str(trace.nodes)
    assert trace.ended == {"s"}


def test_completed_reply_does_not_wait_for_inherited_background_session() -> None:
    row = {"ingress": {"message_id": "mc-1"}, "nodes": [
        {"session_id": "reply", "type": "llm_call", "data": {"purpose": "reply"}},
        {"session_id": "hook", "type": "llm_call", "data": {"purpose": "reflect"}},
    ]}
    assert reply_finished(row, {"reply"})
    assert not reply_finished(row, {"hook"})
    assert not reply_finished({"ingress": row["ingress"], "nodes": []}, {"reply"})


async def test_sse_empty_heartbeat_does_not_interrupt_request_observation() -> None:
    payload = ('event: ping\r\ndata: \r\n\r\n: keep-alive\r\n\r\n'
               'event: session_end\r\ndata: {"session_id":"reply"}\r\n\r\n')
    transport = httpx.MockTransport(lambda _: httpx.Response(200, text=payload))
    trace = TraceReader()
    async with httpx.AsyncClient(base_url="http://fixture", transport=transport) as client:
        await trace.consume(client)
    assert trace.ended == {"reply"}
    assert trace.connected.is_set() and trace.closed


def test_missing_model_observation_does_not_become_zero_usage() -> None:
    row = measure("inventory", 1000, [], {}, scope="group_minecraft:bench", timeout=True)
    assert row["input_tokens"] is None and row["cache"] == "unknown"
    shortcut = measure("shortcut", 1000, [], {}, scope="group_minecraft:bench", timeout=True)
    assert shortcut["input_tokens"] == 0 and shortcut["cache"] == "not_applicable"


def test_combined_reports_reject_incompatible_runs_and_keep_failures(tmp_path: Path) -> None:
    manifest = {key: "same" for key in ("commit", "model_config_sha256", "prompts", "channel", "world", "timeout_seconds")}
    row = {"scenario": "stop", "cache": "warm", "outcome": "failed", "verified": False,
           "timings": {"actually_stopped": 10, "first_chat": None},
           "model_calls": 1, "input_tokens": 5, "first_tool_count": 20}
    runs = [tmp_path / name for name in ("first", "second")]
    for directory in runs:
        directory.mkdir()
        (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (directory / "sample-001.json").write_text(json.dumps(row), encoding="utf-8")
    report = summarize_runs(runs)
    assert report["samples"] == 2 and len(report["nonpassing_records"]) == 2
    assert report["groups"]["stop/warm"]["outcomes"] == {"failed": 2}
    assert report["by_first_tool_count"]["20"]["samples"] == 2
    with pytest.raises(ValueError, match="counted twice"):
        summarize_runs([runs[0], runs[0]])
    (runs[1] / "manifest.json").write_text(json.dumps({**manifest, "model_config_sha256": "changed"}), encoding="utf-8")
    with pytest.raises(ValueError, match="different"):
        summarize_runs(runs)
