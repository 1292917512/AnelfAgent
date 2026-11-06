"""GitHub webhook 验签、载荷限制和持久接收。"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Dict

from fastapi.responses import JSONResponse

from agent.channel import get_provider_key

from .events import from_webhook
from .journal import InboxFull
from .pipeline import EventPipeline

MAX_WEBHOOK_BYTES = 25 * 1024 * 1024


def verify_signature(secret: str, body: bytes, signature_header: str) -> bool:
    """使用恒定时间比较验证原始请求体的 HMAC-SHA256 签名。"""
    if not secret or not signature_header:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


class WebhookIngress:
    """将已验证事件交给持久收件箱。"""

    def __init__(self, pipeline: EventPipeline) -> None:
        self._pipeline = pipeline
        self.running = False
        self.accepted_count = 0
        self.rejected_count = 0

    async def start(self) -> None:
        """允许接收新事件。"""
        self.running = True

    async def stop(self) -> None:
        """停止接收，已接受事件由管线保存和恢复。"""
        self.running = False

    async def enqueue(self, delivery_id: str, event_name: str, payload: dict[str, Any], *, account: str = "") -> str:
        """持久接收事件，队列满时保留已有记录并拒收新事件。"""
        if not self.running:
            raise RuntimeError("Webhook 接收器未运行")
        raw = from_webhook(event_name, payload, delivery_id)
        raw.account = account
        try:
            status = await self._pipeline.accept_raw(raw)
        except InboxFull:
            self.rejected_count += 1
            raise
        if status == "accepted":
            self.accepted_count += 1
        return status


async def handle_webhook(channel: Any, request: Any, account: str = "") -> JSONResponse:
    """验证账户签名并在持久接收完成后返回回执。"""
    cfg = channel.config
    if not cfg.webhook_enabled:
        return JSONResponse({"error": "Webhook 模式未启用"}, status_code=404)
    if channel.ingress is None or not channel.ingress.running:
        return JSONResponse({"error": "Webhook 接收器未运行"}, status_code=503)
    secret = cfg.webhook_secret
    if account:
        settings = next((entry for entry in cfg.accounts if entry.name == account), None)
        if settings is None or not settings.webhook_secret_ref:
            return JSONResponse({"error": "Webhook 账户未配置"}, status_code=404)
        secret = get_provider_key(settings.webhook_secret_ref)
    delivery_id = request.headers.get("x-github-delivery", "").strip()
    event_name = request.headers.get("x-github-event", "").strip().lower()
    signature = request.headers.get("x-hub-signature-256", "")
    body = await request.body()
    if len(body) > MAX_WEBHOOK_BYTES:
        return JSONResponse({"error": "Webhook 载荷超出上限"}, status_code=413)
    if not delivery_id or not event_name or not body:
        return JSONResponse({"error": "缺少 delivery、event 或请求体"}, status_code=400)
    if not verify_signature(secret, body, signature):
        return JSONResponse({"error": "签名校验失败"}, status_code=401)
    try:
        payload = json.loads(body)
    except ValueError:
        return JSONResponse({"error": "请求体必须是 JSON"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"error": "载荷必须是对象"}, status_code=400)
    if event_name == "ping":
        return JSONResponse({"status": "pong"})
    try:
        status = await channel.ingress.enqueue(delivery_id, event_name, payload, account=account)
    except InboxFull as exc:
        return JSONResponse({"error": str(exc), "accepted": False}, status_code=503, headers={"Retry-After": "60"})
    return JSONResponse({"status": status})


async def handle_test_inject(channel: Any, body: Dict[str, Any]) -> Dict[str, Any]:
    """POST /test-inject:本地演练注入(webui 密码保护;不出网)。

    body: {"event_name": "issues", "payload": {...}}——走完整管线(渲染/分级/聚合),
    NORMAL 级事件会进防抖窗(窗口到期才派发),返回渲染结果供立即确认。
    """
    event_name = str(body.get("event_name") or "").strip().lower()
    payload = body.get("payload")
    if not event_name or not isinstance(payload, dict):
        return {"success": False, "error": "需要 event_name 与 dict 形态 payload"}
    raw = from_webhook(event_name, payload)
    raw.source = "test"
    event = await channel.pipeline.submit_raw(raw)
    if event is None:
        return {"success": True, "rendered": False,
                "hint": "事件被过滤(未订阅该事件族/分支不匹配/优先级 IGNORE)"}
    return {
        "success": True, "rendered": True,
        "title": event.title, "priority": event.priority.value,
        "hint": ("IMMEDIATE 已立即派发" if event.priority.value == "immediate"
                 else "NORMAL 已入防抖窗 / DIGEST 已入汇总缓冲"),
    }
