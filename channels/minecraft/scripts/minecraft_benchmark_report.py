"""Summarize correlated Minecraft samples without hiding missing timings or failed requests."""
from __future__ import annotations

import math
from collections import Counter
from pathlib import Path
from typing import Any


def percentile(values: list[float], quantile: float) -> float | None:
    """Nearest-rank percentiles; absent observations remain absent."""
    return sorted(values)[max(0, math.ceil(len(values) * quantile) - 1)] if values else None


def first_cache_fraction(sample: dict[str, Any]) -> float | None:
    """Nonzero provider cache reads can still cover only a tiny fraction of the first prompt."""
    models = [node for node in sample.get("nodes", []) if node["type"] == "llm_call"
              and node["data"].get("purpose", "reply") == "reply"]
    usage = min(models, key=lambda node: node["timestamp"])["data"].get("usage", {}) if models else {}
    total = usage.get("total_input_tokens", 0)
    if not usage.get("cache_observable") or not total:
        return None
    return float(usage.get("cache_read_input_tokens", 0)) / float(total)


def summarize(samples: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, Any] = {}
    for scenario, cache in sorted({(s["scenario"], s["cache"]) for s in samples}):
        rows = [s for s in samples if s["scenario"] == scenario and s["cache"] == cache]
        metrics: dict[str, Any] = {}
        for key in sorted({key for row in rows for key in row["timings"]}):
            values = [float(row["timings"][key]) for row in rows if row["timings"].get(key) is not None]
            metrics[key] = {"observed": len(values), "missing": len(rows) - len(values),
                            "p50_ms": percentile(values, .5), "p95_ms": percentile(values, .95)}
        groups[f"{scenario}/{cache}"] = {
            "n": len(rows), "outcomes": dict(Counter(row["outcome"] for row in rows)),
            "timings_all_observed_including_failures": metrics,
            "model_calls": [row["model_calls"] for row in rows],
            "input_tokens": [row["input_tokens"] for row in rows],
            "first_tool_counts": [row["first_tool_count"] for row in rows],
            "first_cache_read_fractions": [first_cache_fraction(row) for row in rows],
        }
    return {"groups": groups, "samples": len(samples), "percentile": "nearest-rank",
            "cache_rule": "First model call: observable read=0 is cold; read>0 is warm; absent/unobservable is unknown; shortcuts are not applicable.",
            "complete_30_per_cache": all(
                groups.get(f"{scenario}/{cache}", {}).get("n", 0) >= 30
                for scenario in ("inventory", "production", "stop") for cache in ("cold", "warm")),
            "warm_sample_target_reached": all(groups.get(f"{scenario}/warm", {}).get("n", 0) >= 30
                                              for scenario in ("inventory", "production", "stop")),
            "limitations": ["Observed cache state is not forced by restarting the application.",
                            "Warm means nonzero reads, not a fully warmed prompt; compare the observed cache fractions too.",
                            "Timeouts retain missing terminal timestamps, never zero or a fabricated latency.",
                            "Game-client FPS is not measured; executor loop latency is not server TPS."]}


def measure(scenario: str, sent_at: float, nodes: list[dict[str, Any]], state: dict[str, Any],
            *, scope: str, timeout: bool) -> dict[str, Any]:
    """Join message -> request -> model/tools -> real executor action, preserving failed evidence."""
    ingress = next((node for node in nodes if node.get("label") == "Minecraft ingress"
                    and node.get("timestamp", 0) * 1000 >= sent_at
                    and node.get("data", {}).get("scope") == scope), None)
    message_id = ingress["data"]["message_id"] if ingress else None
    linked = [node for node in nodes if message_id and node.get("data", {}).get("request", {}).get("message_id") == message_id
              and node.get("data", {}).get("request", {}).get("scope") == scope]
    models = sorted((n for n in linked if n["type"] == "llm_call" and n["data"].get("purpose", "reply") == "reply"), key=lambda n: n["timestamp"])
    background = [n for n in linked if n["type"] == "llm_call" and n["data"].get("purpose", "reply") != "reply"]
    tools = sorted((n for n in linked if n["type"] in {"tool_call", "entity_call"} and not n["data"].get("actor")), key=lambda n: n["timestamp"])
    ids = {node["data"]["request"]["request_id"] for node in linked}
    actions = [e["data"] for e in state.get("events", []) if e["type"] == "action_progress"
               and (e["data"].get("origin") or {}).get("requestId") in ids]
    # Stop's target belongs to the earlier follow request. The stop timestamp must be after this message.
    stopped = [e["data"] for e in state.get("events", []) if e["type"] == "action_progress"
               and (e["data"].get("stopRequestedAt") or 0) >= sent_at]
    productions = [e["data"] for e in state.get("events", []) if e["type"] == "production_progress"
                   and (e["data"].get("origin") or {}).get("requestId") in ids]
    chats = [chat for chat in state.get("chats", []) if chat["at"] >= sent_at]
    usage = models[0]["data"].get("usage", {}) if models else {}
    cache = ("not_applicable" if scenario == "shortcut" else "unknown" if not usage.get("cache_observable")
             else "warm" if usage.get("cache_read_input_tokens", 0) > 0 else "cold")
    timings: dict[str, float | None] = {}

    def elapsed(name: str, timestamps: list[float]) -> None:
        timings[name] = round(min(timestamps) - sent_at, 1) if timestamps else None

    elapsed("channel_ingress", [ingress["timestamp"] * 1000] if ingress else [])
    elapsed("first_model", [n["timestamp"] * 1000 for n in models])
    elapsed("first_model_end", [n["timestamp"] * 1000 + n["duration_ms"] for n in models if n.get("duration_ms") is not None])
    elapsed("first_tool", [n["timestamp"] * 1000 for n in tools])
    elapsed("action_started", [a["startedAt"] for a in actions])
    elapsed("stop_requested", [a["stopRequestedAt"] for a in stopped])
    elapsed("actually_stopped", [a["finishedAt"] for a in stopped if a.get("finishedAt")])
    elapsed("task_terminal", [a["finishedAt"] for a in productions if a.get("finishedAt")])
    elapsed("first_chat", [c["at"] for c in chats])
    elapsed("terminal_chat", [c["at"] for c in chats if "制作任务已完成" in c["text"]])
    timings["ingress_to_model"] = (round(timings["first_model"] - timings["channel_ingress"], 1)
                                   if timings["first_model"] is not None and timings["channel_ingress"] is not None else None)
    inventory = {item["name"]: sum(i["count"] for i in state.get("inventory", []) if i["name"] == item["name"])
                 for item in state.get("inventory", [])}
    names = [n["data"].get("tool_name", n["data"].get("name", "")) for n in tools]
    verified = (bool(chats) and any(name.endswith("get_inventory") for name in names)) if scenario == "inventory" else (
        inventory.get("wooden_pickaxe", 0) == 1 and any(p["phase"] == "completed" and p["created"] == 1
                                                       and p["inventoryClean"] for p in productions)
        and timings["terminal_chat"] is not None) if scenario == "production" else (
        bool(stopped) and timings["actually_stopped"] is not None and not state.get("action", {}).get("active") and bool(chats))
    return {"scenario": scenario, "cache": cache, "sent_at": sent_at, "message_id": message_id,
            "outcome": "timeout" if timeout else "verified" if verified else "failed", "verified": verified,
            "timings": timings, "model_calls": len(models),
            "input_tokens": (sum(n["data"]["usage"]["total_input_tokens"] for n in models)
                             if (models or scenario == "shortcut")
                             and all("total_input_tokens" in n["data"].get("usage", {}) for n in models) else None),
            "related_background_calls_observed": len(background),
            "first_tool_count": models[0]["data"].get("tool_count") if models else None,
            "model_durations_ms": [n.get("duration_ms") for n in models], "tool_names": names,
            "nodes": linked, "ingress": ingress, "executor": state}


def summarize_runs(directories: list[Path]) -> dict[str, Any]:
    """Combine immutable runs with matching scenarios/configuration, retaining run and tool-count strata."""
    import json

    manifests: dict[str, Any] = {}
    batches: dict[str, list[dict[str, Any]]] = {}
    sample_paths: dict[str, list[Path]] = {}
    conditions: list[dict[str, Any]] = []
    for directory in directories:
        key = str(directory.resolve())
        if key in batches:
            raise ValueError("A run cannot be counted twice")
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        manifests[key] = manifest
        condition = {name: manifest[name] for name in (
            "commit", "model_config_sha256", "prompts", "channel", "world", "timeout_seconds")}
        condition["executor_sources"] = {str(path).replace("\\", "/"): digest
                                         for path, digest in manifest.get("source_hashes", {}).items()
                                         if str(path).replace("\\", "/").startswith(
                                             ("channels/minecraft/mcp/", "plugins/minecraft/"))}
        conditions.append(condition)
        sample_paths[key] = sorted(directory.glob("sample-*.json"))
        rows = [json.loads(path.read_text(encoding="utf-8")) for path in sample_paths[key]]
        if not rows:
            raise ValueError(f"No samples in {directory}")
        batches[key] = rows
    if not conditions or any(value != conditions[0] for value in conditions[1:]):
        raise ValueError("Runs have different application/configuration conditions; report them separately")
    samples = [row for rows in batches.values() for row in rows]
    return {**summarize(samples), "manifests": manifests,
            "by_run": {key: summarize(rows) for key, rows in batches.items()},
            "by_first_tool_count": {str(count): summarize([row for row in samples if row["first_tool_count"] == count])
                                    for count in sorted({row["first_tool_count"] for row in samples}, key=str)},
            "nonpassing_records": [
                {"run": key, "sample": path.name, "scenario": row["scenario"],
                 "outcome": row["outcome"], "facts_verified": row["verified"]}
                for key, rows in batches.items() for path, row in zip(sample_paths[key], rows, strict=True)
                if row["outcome"] != "verified"]}


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(summarize_runs(args.runs), ensure_ascii=False, indent=2), encoding="utf-8")
