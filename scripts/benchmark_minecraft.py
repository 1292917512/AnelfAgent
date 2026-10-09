"""Run correlated real-model measurements against a dedicated Minecraft fixture, restoring live settings."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import platform
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
import psutil

from scripts.minecraft_benchmark_report import measure, summarize

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = {
    "inventory": "查一下你现在背包里的橡木原木和木镐分别有多少，只报实际数量，不要制作、采集或移动。",
    "production": "用背包里的材料额外新做一把木镐，完成后按实际入包数量告诉我，不要采集或丢东西。",
    "stop": "先停止你正在做的事情，留在原地等我，不要继续跟随。",
    "shortcut": "!stop",
}
DATA_KEYS = {"request", "model", "duration_ms", "ttft_ms", "usage", "tool_count", "message_count", "purpose", "actor",
             "tool_name", "name", "tool_id", "call_id", "success", "error", "message_id", "scope", "event_ts_ms", "shortcut"}


def write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


class TraceReader:
    """Consume the existing SSE stream; retain timings and identities, excluding reasoning and prompts."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.ended: set[str] = set()
        self.connected = asyncio.Event()
        self.closed = False

    async def consume(self, client: httpx.AsyncClient) -> None:
        try:
            async with client.stream("GET", "/api/thinking/stream", timeout=None) as response:
                response.raise_for_status()
                self.connected.set()
                event = ""
                async for line in response.aiter_lines():
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        payload = line[5:].strip()
                        if payload and event != "ping":
                            self.accept(event, json.loads(payload))
        finally:
            self.closed = True

    def accept(self, event: str, data: dict[str, Any]) -> None:
        if event == "session_end":
            self.ended.add(data["session_id"])
        if "node" in data:
            node = dict(data["node"])
            node["session_id"] = data.get("session_id", data.get("session", {}).get("id"))
            self.nodes[node["id"]] = node
        elif "node_id" in data and data["node_id"] in self.nodes:
            self.nodes[data["node_id"]].update(data.get("updates", {}))
        for node in self.nodes.values():
            node["data"] = {k: v for k, v in node.get("data", {}).items() if k in DATA_KEYS}
            if node.get("label") != "Minecraft ingress":
                node.pop("label", None)


async def request(client: httpx.AsyncClient, route: str, method: str = "GET", body: Any = None) -> Any:
    response = await client.request(method, route, json=body)
    response.raise_for_status()
    return response.json()


async def idle(client: httpx.AsyncClient) -> bool:
    status, pfc, delegates = await asyncio.gather(
        request(client, "/api/status/"), request(client, "/api/status/pfc"), request(client, "/api/delegations/overview"))
    return bool(status["ready"] and status["status"]["queue_size"] == 0 and not pfc["pending_messages"]
                and not pfc["general_tasks"] and not delegates["running"])


async def wait_idle(client: httpx.AsyncClient, timeout: float = 180) -> float:
    """Drain unrelated queued work between samples, outside the measured request latency."""
    started = time.monotonic()
    while not await idle(client):
        if time.monotonic() - started >= timeout:
            raise TimeoutError("Application queue did not drain between samples; refusing to interleave requests")
        await asyncio.sleep(1)
    return round((time.monotonic() - started) * 1000, 1)


def reply_finished(row: dict[str, Any], ended: set[str]) -> bool:
    """Only foreground reply sessions determine completion; inherited hook sessions do not."""
    sessions = {node["session_id"] for node in row["nodes"]
                if node.get("session_id") and node["type"] == "llm_call"
                and node["data"].get("purpose", "reply") == "reply"}
    return bool(row["ingress"] and sessions and sessions <= ended)


async def run_sample(client: httpx.AsyncClient, fixture: httpx.AsyncClient, trace: TraceReader,
                     scenario: str, scope_id: str, timeout: float) -> dict[str, Any]:
    idle_wait_ms = await wait_idle(client)
    initial = await request(fixture, "/reset", "POST", {"mode": scenario})
    host_load = {"cpu_percent": await asyncio.to_thread(psutil.cpu_percent, .2),
                 "memory_percent": psutil.virtual_memory().percent, "logical_cpus": psutil.cpu_count()}
    await asyncio.sleep(1)
    # Only the newly created benchmark conversation is reset. Existing player history is never deleted.
    assert scope_id.startswith("m0_bench_")
    await request(client, "/api/memory/conversations/clear", "POST",
                  {"scope_type": "group", "scope_id": f"minecraft:{scope_id}"})
    trace.nodes.clear(); trace.ended.clear()
    sent = await request(fixture, "/say", "POST", {"text": PROMPTS[scenario]})
    deadline = time.monotonic() + timeout
    scope = f"group_minecraft:{scope_id}"
    while True:
        await asyncio.sleep(.5)
        state = await request(fixture, "/state")
        assert not state["failure"], state["failure"]
        nodes = list(trace.nodes.values())
        row = measure(scenario, sent["sentAt"], nodes, state, scope=scope, timeout=False)
        if trace.closed:
            row["outcome"] = "trace_error"
            break
        finished = reply_finished(row, trace.ended)
        if scenario == "shortcut":
            finished = row["verified"]
        awaiting_announcement = (scenario == "production" and not row["verified"]
                                and any(e["type"] == "production_progress" and e["data"]["phase"] == "completed"
                                        for e in state["events"]))
        if finished and not awaiting_announcement and not state["action"]["active"]:
            break
        if time.monotonic() >= deadline:
            row["outcome"] = "timeout"
            break
    row["initial"] = initial
    row["pre_sample_idle_wait_ms"] = idle_wait_ms
    row["host_load_before"] = host_load
    row["prompt"] = PROMPTS[scenario]
    if row["outcome"] != "verified":
        await request(fixture, "/say", "POST", {"text": "!stop"})
        await asyncio.sleep(2)
    return row


async def main(args: argparse.Namespace) -> None:
    output: Path = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    assert not (output / "backup.json").exists(), "Use a fresh result directory; never overwrite evidence or recovery settings"
    world = output / "world-server"
    world.mkdir()
    shutil.copy2(args.server_jar, world / "server.jar")
    config_path = ROOT / "config/mcp_servers.json"
    channel_path = ROOT / "channels/minecraft/channel_config.json"
    original_mcp = json.loads(config_path.read_text(encoding="utf-8"))
    original_channel = json.loads(channel_path.read_text(encoding="utf-8"))
    scope_id = "m0_bench_" + str(int(time.time()))
    edits = {"auto_connect": False, "server_id": scope_id, "allowed_players": ["heiyue"], "require_mention": False}
    original_values = {key: original_channel.get(key) for key in edits}
    replacement = json.loads(json.dumps(original_mcp))
    minecraft = replacement["mcpServers"]["minecraft"]
    minecraft.update({"command": str(args.node.resolve()), "args": [str(ROOT / "plugins/minecraft/tests/benchmark-executor.cjs"),
                       str(args.payload.resolve()), str(world), str(args.java.resolve())], "enabled": True})
    model_path = ROOT / "config/llm_clients.json"
    model_hash = hashlib.sha256(model_path.read_bytes()).hexdigest()
    samples: list[dict[str, Any]] = []
    async with httpx.AsyncClient(base_url=args.api, timeout=30) as client:
        assert await idle(client), "Application must be idle before changing the benchmark connection"
        old_trace = await request(client, "/api/thinking/status")
        write(output / "backup.json", {"mcp": original_mcp, "channel_values": original_values,
                                      "trace_enabled": old_trace["enabled"], "scope_id": scope_id,
                                      "benchmark_mcp": minecraft, "channel_edits": edits})
        write(output / "manifest.json", {"commit": subprocess.check_output(
            ["git", "-c", f"safe.directory={ROOT.as_posix()}", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "model_config_sha256": model_hash, "channel": {k: original_channel.get(k) for k in (
                "poll_interval_seconds", "reflex_interval_seconds", "reflexes_enabled")},
            "host": platform.platform(), "world": "isolated flat vanilla 26.1; fixture reset each sample",
            "prompts": PROMPTS, "repeats": args.repeats, "timeout_seconds": args.timeout,
            "cache": "observed provider usage, no forced cold claim", "scope_id": scope_id,
            "completion": "foreground reply ended and executor inactive; queue drain measured separately"})
        manifest_path = output / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["source_hashes"] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                     for p in [ROOT / "scripts/benchmark_minecraft.py", ROOT / "scripts/minecraft_benchmark_report.py",
                                               ROOT / "plugins/minecraft/tests/benchmark-executor.cjs",
                                               ROOT / "plugins/minecraft/package-lock.json",
                                               ROOT / "agent/channel/reply_policy.py", ROOT / "agent/mind/tool_assembly.py",
                                               ROOT / "agent/mind/prefrontal_cortex.py", ROOT / "agent/mind/tools/think_loop.py",
                                               ROOT / "channels/minecraft/adapter.py", ROOT / "channels/minecraft/reply_policy.py",
                                               ROOT / "channels/minecraft/receipts.py",
                                               *sorted((ROOT / "plugins/minecraft/runtime").glob("*.mjs"))]}
        model_data = json.loads(model_path.read_text(encoding="utf-8"))
        manifest["default_model"] = model_data.get("default_chat")
        manifest["model_parameters"] = [
            {key: model[key] for key in ("id", "name", "model", "temperature", "max_tokens", "reasoning_effort", "api_type") if key in model}
            for provider in model_data.get("providers", []) for model in provider.get("models", [])
            if model.get("name") == model_data.get("default_chat") or model.get("id") == model_data.get("default_chat")]
        write(manifest_path, manifest)
        trace = TraceReader()
        stream: asyncio.Task[None] | None = None
        fixture: httpx.AsyncClient | None = None
        try:
            for key, value in edits.items():
                await request(client, f"/api/config/meta/minecraft_{key}", "PUT", {"value": value})
            await request(client, "/api/mcp/config", "PUT", {"content": json.dumps(replacement)})
            deadline = time.monotonic() + 120
            control_path = world / "control.json"
            while not control_path.exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError("Benchmark executor did not publish its local control address")
                await asyncio.sleep(1)
            control = json.loads(control_path.read_text(encoding="utf-8"))
            fixture = httpx.AsyncClient(base_url=f"http://127.0.0.1:{control['port']}", timeout=30,
                                       headers={"Authorization": f"Bearer {control['token']}"})
            while not (await request(fixture, "/state"))["ready"]:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Isolated world did not become ready")
                await asyncio.sleep(1)
            await request(client, "/api/thinking/toggle", "PUT", {"enabled": True})
            stream = asyncio.create_task(trace.consume(client))
            await asyncio.wait_for(trace.connected.wait(), 10)
            await asyncio.sleep(3)
            for index in range(args.repeats):
                for scenario in args.scenarios.split(","):
                    if stream.done():
                        await stream
                        raise RuntimeError("Trace stream ended; refusing unobserved samples")
                    row = await run_sample(client, fixture, trace, scenario, scope_id, args.timeout)
                    row["index"] = index
                    samples.append(row)
                    write(output / f"sample-{len(samples):03}.json", row)
                    write(output / "summary.json", summarize(samples))
                    print(json.dumps({k: row[k] for k in ("scenario", "outcome", "cache", "timings", "model_calls")}), flush=True)
                    if hashlib.sha256(model_path.read_bytes()).hexdigest() != model_hash:
                        raise RuntimeError("Model settings changed during benchmark; stop mixing conditions")
                    if row["outcome"] != "verified" and args.fail_fast:
                        return
        finally:
            if fixture:
                with contextlib.suppress(Exception):
                    await request(fixture, "/say", "POST", {"text": "!stop"})
                    await asyncio.sleep(2)
                with contextlib.suppress(Exception):
                    await request(fixture, "/drain", "POST", {})
                await fixture.aclose()
            if stream:
                stream.cancel()
                await asyncio.gather(stream, return_exceptions=True)
            # Restore only our changed fields; preserve concurrent edits to other servers/settings.
            errors: list[str] = []
            current_mcp = json.loads(config_path.read_text(encoding="utf-8"))
            if current_mcp["mcpServers"]["minecraft"] == minecraft:
                current_mcp["mcpServers"]["minecraft"] = original_mcp["mcpServers"]["minecraft"]
                try:
                    await request(client, "/api/mcp/config", "PUT", {"content": json.dumps(current_mcp)})
                except Exception as exc:
                    errors.append(f"MCP restore: {exc}")
            elif current_mcp["mcpServers"]["minecraft"] != original_mcp["mcpServers"]["minecraft"]:
                errors.append("Minecraft MCP was concurrently edited; preserved that edit")
            for key, value in original_values.items():
                try:
                    current = json.loads(channel_path.read_text(encoding="utf-8")).get(key)
                    if current == edits[key]:
                        await request(client, f"/api/config/meta/minecraft_{key}", "PUT", {"value": value})
                    elif current != value:
                        errors.append(f"Channel {key} was concurrently edited; preserved that edit")
                except Exception as exc:
                    errors.append(f"Channel {key} restore: {exc}")
            try:
                await request(client, "/api/thinking/toggle", "PUT", {"enabled": old_trace["enabled"]})
            except Exception as exc:
                errors.append(f"Trace restore: {exc}")
            restored_channel = json.loads(channel_path.read_text(encoding="utf-8"))
            write(output / "restored.json", {"at": time.time(), "errors": errors,
                  "channel_values_restored": all(restored_channel.get(k) == v for k, v in original_values.items()),
                  "mcp_restored": json.loads(config_path.read_text(encoding="utf-8"))["mcpServers"]["minecraft"] == original_mcp["mcpServers"]["minecraft"],
                  "model_unchanged": hashlib.sha256(model_path.read_bytes()).hexdigest() == model_hash})
            if errors:
                raise RuntimeError(f"Restore needs attention; see {output / 'restored.json'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8092")
    for name in ("output", "server-jar", "java", "node", "payload"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--scenarios", default=",".join(PROMPTS))
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--fail-fast", action="store_true")
    options = parser.parse_args()
    if options.repeats < 1 or options.timeout <= 0 or any(s not in PROMPTS for s in options.scenarios.split(",")):
        parser.error("Positive bounds and known scenarios are required")
    asyncio.run(main(options))
