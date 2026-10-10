"""裁决审计和 Web 管理契约。"""

import httpx
from fastapi import FastAPI

from agent.approval import audit
from web.routers.approvals import router


async def test_history_and_stats_contract(fake_audit_sink):
    for outcome in ["guardian_approved", "guardian_bypass", "guardian_denied", "denied", "permission_error"]:
        await audit.record_decision(tool_name="t", outcome=outcome, user_id="u", channel_id="qq")
    await audit.record_decision(tool_name="t", outcome="unknown")
    assert len(fake_audit_sink.rows) == 5
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        response = await client.get("/approvals/history", params={"limit":2,"offset":1})
        assert response.status_code == 200
        body = response.json()
        assert body["offset"] == 1 and len(body["history"]) == 2
        assert body["history"][0]["outcome"] == "denied"
        assert body["history"][0]["channel_id"] == "qq"
        stats = (await client.get("/approvals/stats")).json()
        assert stats["total"] == 5 and stats["by_outcome"]["guardian_bypass"] == 1
        for endpoint in ["pending", "policies"]:
            assert (await client.get(f"/approvals/{endpoint}")).status_code == 404
        for action in ["approve", "deny"]:
            assert (await client.post(f"/approvals/request/{action}", json={})).status_code == 404


async def test_audit_failure_does_not_change_decision(monkeypatch):
    class BrokenSink:
        async def append_approval_audit(self, record):
            raise OSError("disk unavailable")
    monkeypatch.setattr(audit, "_audit_sink", lambda: BrokenSink())
    await audit.record_decision(tool_name="t", outcome="denied")
