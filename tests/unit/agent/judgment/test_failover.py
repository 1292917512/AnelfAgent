"""优先级回退、连接恢复与无推理探测的完整调用链。"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from agent.judgment import client as client_module
from agent.judgment.catalog import load_catalog, update_catalog
from agent.judgment.client import SystemOneClient
from agent.judgment.diagnostics import check_connection
from agent.judgment.engine import get_judgment_engine, load_config
from agent.judgment.gate import assess_optional_work
from agent.judgment.health import judgment_health
from agent.judgment.tools import configure_judgment
from agent.judgment.tools import test_judgment_model as diagnose_model
from agent.judgment.types import JudgmentError, NoulQuestion
from agent.mind.execution.reply_delivery import assess_reply
from core.config import ConfigManager
from core.tool_errors import ErrorCause


@pytest.fixture
def chain(monkeypatch):
    for name in ["local", "cloud", "unused"]:
        update_catalog("save_provider", name, {"name": name, "base_url": f"http://{name}.test",
            "api_key": "test-key-not-for-probes", "models": {name: {"model": name}}})
    update_catalog("set_priority", "", {"model_ids": ["local", "cloud"]})
    requests: list[httpx.Request] = []
    offline = {"local"}

    def respond(request):
        requests.append(request)
        name = request.url.host.split(".")[0]
        if name in offline:
            raise httpx.ConnectError("connection refused")
        if request.method == "HEAD":
            return httpx.Response(405)
        body = json.loads(request.content)
        return httpx.Response(200, json={"model": name, "answers": {qid: {"type": "noul", "noul": 0}
            if q["type"] == "noul" else {"type": "choice", "choice": "internal" if qid == "intent" else "uncertain", "confidence": 1,
                "probabilities": {key: float(key == ("internal" if qid == "intent" else "uncertain")) for key in q["criteria"]}}
            for qid, q in body["questions"].items()}})

    monkeypatch.setattr(client_module, "async_network_client",
        lambda proxy_mode, **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(respond), **kwargs))
    return requests, offline


async def test_offline_fails_over_and_recovery_restores_priority_without_inference_probe(chain):
    requests, offline = chain
    engine = get_judgment_engine()
    questions = {"q": NoulQuestion(instructions="needed?")}
    assert (await engine.judge("facts", questions, native_only=True)).model == "cloud"
    assert [r.url.host for r in requests] == ["local.test", "cloud.test"]
    for _ in range(3):
        assert (await engine.judge("facts", questions, native_only=True)).model == "cloud"
    assert sum(r.url.host == "local.test" for r in requests) == 1
    offline.clear()
    await judgment_health._recheck("local", judgment_health._states["local"])
    assert requests[-1].method == "HEAD" and not requests[-1].content and "authorization" not in requests[-1].headers
    assert judgment_health.snapshot()["local"]["check_kind"] == "connectivity"
    assert (await engine.judge("facts", questions, native_only=True)).model == "local"
    judgment_health.start()
    await asyncio.sleep(0)
    assert sum(r.method == "HEAD" for r in requests) == 1
    assert load_catalog().default_model == "local"
    assert not any(r.url.host == "unused.test" for r in requests)


async def test_review_and_optional_work_use_fallback_but_rejection_is_final(chain):
    requests, offline = chain
    result = await assess_reply("answer", "user_webui:1#a", [{"role": "user", "content": "question"}])
    assert not result.approved and result.model == "cloud"
    assert [r.url.host for r in requests] == ["local.test", "cloud.test"]
    assert (await assess_optional_work("work", "needed?", {})).run
    assert requests[-1].url.host == "cloud.test"
    offline.clear()
    await judgment_health.check_connectivity(load_config("local"))
    requests.clear()
    assert not (await assess_reply("answer", "user_qq:1", [])).approved
    assert [r.url.host for r in requests] == ["local.test"]


async def test_explicit_model_does_not_use_other_native_models_and_manual_check_costs_no_tokens(chain):
    requests, offline = chain
    with pytest.raises(JudgmentError):
        await get_judgment_engine().judge("facts", {"q": NoulQuestion(instructions="?")}, model_id="local", native_only=True)
    assert [r.url.host for r in requests] == ["local.test"]
    offline.clear()
    result = json.loads(await diagnose_model("local"))
    assert result["ok"] and result["kind"] == "connectivity" and "usage" not in result
    assert requests[-1].method == "HEAD"


async def test_unavailable_chain_skips_delivery_and_preserves_total_timeout(chain, monkeypatch):
    requests, offline = chain
    offline.add("cloud")
    assert not (await assess_reply("answer", "user_qq:1", [])).approved
    assert not (await assess_reply("answer", "user_qq:1", [])).approved
    assert len(requests) == 2
    await judgment_health.close()
    called = []

    async def stuck(cfg, *args):
        called.append(cfg.model_id)
        await asyncio.Event().wait()

    monkeypatch.setattr(get_judgment_engine(), "_judge_native", stuck)
    with pytest.raises(JudgmentError) as caught:
        await asyncio.wait_for(get_judgment_engine().judge("facts", {"q": NoulQuestion(instructions="?")}, native_only=True, timeout=0.02), 1)
    assert caught.value.cause == ErrorCause.TIMEOUT and called == ["local"]
    assert judgment_health.snapshot()["local"]["status"] == "offline"
    assert "cloud" not in judgment_health.snapshot()


@pytest.mark.parametrize("status,ok", [(200, True), (401, True), (403, True), (404, True), (405, True), (429, False), (451, False), (502, False)])
async def test_connectivity_head_never_submits_inference_or_credentials(chain, monkeypatch, status, ok):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status)

    monkeypatch.setattr(client_module, "async_network_client",
        lambda proxy_mode, **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(respond), **kwargs))
    result = await check_connection("local")
    assert result["ok"] is ok
    assert len(requests) == 1 and requests[0].method == "HEAD"
    assert "authorization" not in requests[0].headers and not requests[0].content


async def test_reachable_endpoint_does_not_clear_known_credential_failure(chain):
    _, offline = chain
    offline.clear()
    cfg = load_config("local")
    judgment_health.begin(cfg, probe=False)
    judgment_health.failure(cfg, JudgmentError("invalid credentials", cause=ErrorCause.CONFIG, retryable=False), 0)
    offline.add("local")
    assert not (await check_connection("local"))["ok"]
    offline.clear()
    assert not (await check_connection("local"))["ok"]
    assert judgment_health.snapshot()["local"]["retry_at"] == 0
    update_catalog("save_provider", "local", {"name": "renamed"})
    assert judgment_health.snapshot()["local"]["status"] == "offline"
    update_catalog("save_provider", "local", {"api_key": "new-test-key"})
    assert "local" not in judgment_health.snapshot()


async def test_ai_priority_is_validated_and_pruned_on_removal(chain):
    result = json.loads(await configure_judgment("set_priority", "", {"model_ids": ["cloud", "local"]}))
    assert result["default_model"] == "cloud" and result["fallback_models"] == ["local"]
    for order in [["cloud", "cloud"], ["unknown"], [""], "cloud"]:
        with pytest.raises(ValueError):
            update_catalog("set_priority", "", {"model_ids": order})
        assert load_catalog().default_model == "cloud"
    update_catalog("delete_provider", "cloud")
    assert load_catalog().default_model == "local" and not load_catalog().fallback_models
    update_catalog("set_priority", "", {"model_ids": []})
    assert not load_config().native_configured


async def test_head_deadline_and_cancellation_do_not_leave_checking_state(chain, monkeypatch):
    async def stuck(*args):
        await asyncio.Event().wait()
    monkeypatch.setattr(SystemOneClient, "check_connectivity", stuck)
    ConfigManager.set("judgment_probe_timeout", 0.01)
    result = await check_connection("local")
    assert result["cause"] == "timeout"
    assert judgment_health.snapshot()["local"]["status"] == "offline"
    task = asyncio.create_task(check_connection("cloud"))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert judgment_health.snapshot()["cloud"]["status"] == "unknown"


async def test_real_http_disconnect_and_recovery():
    requests: list[tuple[str, int]] = []

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            header = await reader.readuntil(b"\r\n\r\n")
            method = header.split(b" ", 1)[0].decode()
            size = next((int(line.split(b":", 1)[1]) for line in header.split(b"\r\n")
                         if line.lower().startswith(b"content-length:")), 0)
            body = await reader.readexactly(size)
            requests.append((method, writer.get_extra_info("sockname")[1]))
            data = json.dumps({"answers": {"q": {"type": "noul", "noul": 1}},
                               "model": json.loads(body)["model"]}).encode() if method == "POST" else b""
            writer.write(f"HTTP/1.1 200 OK\r\nContent-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode() + data)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    local = await asyncio.start_server(serve, "127.0.0.1", 0)
    cloud = await asyncio.start_server(serve, "127.0.0.1", 0)
    local_port, cloud_port = local.sockets[0].getsockname()[1], cloud.sockets[0].getsockname()[1]
    try:
        for name, port in [("local", local_port), ("cloud", cloud_port)]:
            update_catalog("save_provider", name, {"name": name, "base_url": f"http://127.0.0.1:{port}",
                "models": {name: {"model": name}}})
        update_catalog("set_priority", "", {"model_ids": ["local", "cloud"]})
        local.close()
        await local.wait_closed()
        engine = get_judgment_engine()
        questions = {"q": NoulQuestion(instructions="is ready?")}
        assert (await engine.judge("facts", questions, native_only=True)).model == "cloud"
        local = await asyncio.start_server(serve, "127.0.0.1", local_port)
        assert (await check_connection("local"))["ok"]
        assert (await engine.judge("facts", questions, native_only=True)).model == "local"
        assert requests == [("POST", cloud_port), ("HEAD", local_port), ("POST", local_port)]
    finally:
        local.close()
        cloud.close()
        await local.wait_closed()
        await cloud.wait_closed()
