"""GitHub webhook 入口 — 验签 / 去重 / 有界队列单 worker(可选接收模式,需公网)。

修复 nonebot-adapter-github 的三个工程缺陷:
1. **验签 fail-closed**:secret 未配置时频道拒绝启动(adapter.start 把关),
   不提供"可不验签"的松默认;
2. **delivery 去重**:GitHub 超时/失败会重投同一 x-github-delivery,去重环幂等;
3. **保序背压**:裸 create_task 改为有界队列 + 单 worker——同仓事件按到达序消费,
   队列满丢最旧并计数(丢弃量进 /status 与 digest 可见)。

端点经 ``adapter.build_router()`` 挂到 ``/api/channels/github``;
webhook 端点声明 ``@self_authenticated``(自带 HMAC 验签,不走 webui 密码),
管理端点(status/events/subscriptions/test-inject)由 webui 密码体系默认保护。
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from typing import Any, Dict, Optional

from core.log import log

from .events import from_webhook
from .pipeline import EventPipeline
from .state import DeliveryDedup

_LOG = "GitHub"


def verify_signature(secret: str, body: bytes, signature_header: str) -> bool:
    """HMAC-SHA256 验签(原始 bytes + 恒定时间比较,GitHub 官方语义)。"""
    if not secret or not signature_header:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


class WebhookIngress:
    """webhook 事件入口:去重 → 有界队列 → 单 worker 顺序消费。"""

    def __init__(self, channel: Any, pipeline: EventPipeline, dedup: DeliveryDedup,
                 *, queue_size: int = 500) -> None:
        self._channel = channel
        self._pipeline = pipeline
        self._dedup = dedup
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max(queue_size, 10))
        self._worker: Optional[asyncio.Task] = None
        self.dropped_count = 0
        self.accepted_count = 0

    @property
    def running(self) -> bool:
        return self._worker is not None and not self._worker.done()

    @property
    def backlog(self) -> int:
        return self._queue.qsize()

    async def start(self) -> None:
        if self.running:
            return
        self._dedup.load()
        self._worker = asyncio.create_task(self._work_loop(), name="github-webhook-worker")
        log(f"{_LOG}: webhook 接收器已启动(队列容量 {self._queue.maxsize})", tag="通道")

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except (asyncio.CancelledError, Exception):
                pass
            self._worker = None
        self._dedup.save()

    # ------------------------------------------------------------------

    def enqueue(self, delivery_id: str, event_name: str, payload: Dict[str, Any]) -> str:
        """去重并入队,返回 'duplicate' / 'accepted' / 'overflow_accepted'(丢最旧)。"""
        if self._dedup.seen_or_add(delivery_id):
            return "duplicate"
        status = "accepted"
        if self._queue.full():
            try:
                self._queue.get_nowait()  # 丢最旧保新鲜(背压策略)
            except asyncio.QueueEmpty:
                pass
            self.dropped_count += 1
            status = "overflow_accepted"
            log(f"{_LOG}: webhook 队列满,丢弃最旧事件(累计 {self.dropped_count})",
                "WARNING", tag="通道")
        self._queue.put_nowait((event_name, payload))
        self.accepted_count += 1
        if self.accepted_count % 50 == 0:
            self._dedup.save()  # 周期性落盘,防崩溃重放窗口过大
        return status

    async def _work_loop(self) -> None:
        while True:
            event_name, payload = await self._queue.get()
            try:
                await self._pipeline.submit_raw(from_webhook(event_name, payload))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # 单事件处理异常绝不炸 worker
                log(f"{_LOG}: webhook 事件处理异常 ({event_name}): {exc}", "WARNING", tag="通道")


# ----------------------------------------------------------------------
# 端点处理器(由 adapter.build_router 接线;均为模块函数便于测试)
# ----------------------------------------------------------------------


async def handle_webhook(channel: Any, request: Any) -> Any:
    """POST /webhook:验签 → 去重 → 入队 → 立即 200(规避 GitHub 10s 超时重投)。

    返回 JSONResponse(路由层直通)。验签失败 401、缺头/空 body 400、
    模式未启用 404、接收器未运行 503;ping 直接 pong 不进管线。
    """
    from fastapi.responses import JSONResponse

    cfg = channel.config
    if not cfg.webhook_enabled:
        return JSONResponse({"error": "webhook 模式未启用(mode 配置)"}, status_code=404)
    if channel.ingress is None or not channel.ingress.running:
        return JSONResponse({"error": "webhook 接收器未运行"}, status_code=503)

    delivery_id = request.headers.get("x-github-delivery", "")
    event_name = request.headers.get("x-github-event", "").strip().lower()
    signature = request.headers.get("x-hub-signature-256", "")
    body = await request.body()
    if not delivery_id or not event_name or not body:
        return JSONResponse({"error": "缺少 x-github-delivery / x-github-event 或 body 为空"},
                            status_code=400)
    if not verify_signature(cfg.webhook_secret, body, signature):
        return JSONResponse({"error": "签名校验失败"}, status_code=401)
    if event_name == "ping":
        return JSONResponse({"status": "pong"})

    try:
        payload = json.loads(body)
    except ValueError:
        return JSONResponse({"error": "body 不是合法 JSON"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"error": "payload 必须是 JSON 对象"}, status_code=400)

    status = channel.ingress.enqueue(delivery_id, event_name, payload)
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
