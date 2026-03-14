"""Telegram Webhook 模式支持 -- 参照 openclaw webhook.ts。

使用 aiohttp 启动 HTTP 服务器接收 Telegram Webhook 推送。
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from core.log import log


async def start_webhook(
    app: Any,
    *,
    url: str,
    secret: str,
    port: int = 8443,
    host: str = "0.0.0.0",
    listen_path: str = "/telegram-webhook",
) -> Optional[Any]:
    """启动 Webhook 模式。

    Args:
        app: python-telegram-bot Application 实例
        url: 公开 URL（Telegram 推送到这个地址）
        secret: Secret Token（验证请求来源）
        port: 监听端口
        host: 监听地址
        listen_path: Webhook 路径

    Returns:
        aiohttp web.AppRunner 实例（用于后续关闭）
    """
    if not url:
        raise ValueError("Webhook URL 不能为空")
    if not secret:
        raise ValueError("Webhook Secret Token 不能为空")

    try:
        from aiohttp import web
    except ImportError:
        log("Webhook 模式需要 aiohttp，请安装: pip install aiohttp", "ERROR")
        raise RuntimeError("缺少 aiohttp 依赖")

    webhook_url = f"{url.rstrip('/')}{listen_path}"

    await app.bot.set_webhook(
        url=webhook_url,
        secret_token=secret,
        allowed_updates=[
            "message", "edited_message", "channel_post",
            "callback_query", "message_reaction",
        ],
    )
    log(f"Telegram Webhook 已注册: {webhook_url}")

    async def _handle_webhook(request: web.Request) -> web.Response:
        token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if token != secret:
            return web.Response(status=403, text="Forbidden")

        try:
            data = await request.json()
            from telegram import Update
            update = Update.de_json(data, app.bot)
            if update:
                await app.process_update(update)
            return web.Response(status=200, text="OK")
        except Exception as exc:
            log(f"Webhook 处理失败: {exc}", "ERROR")
            return web.Response(status=500, text="Internal Server Error")

    async def _health(request: web.Request) -> web.Response:
        return web.Response(status=200, text="OK")

    web_app = web.Application()
    web_app.router.add_post(listen_path, _handle_webhook)
    web_app.router.add_get("/healthz", _health)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log(f"Telegram Webhook 服务器已启动: {host}:{port}{listen_path}")

    return runner


async def stop_webhook(app: Any, runner: Any) -> None:
    """关闭 Webhook 模式。"""
    try:
        await app.bot.delete_webhook()
        log("Telegram Webhook 已注销")
    except Exception as exc:
        log(f"注销 Webhook 失败: {exc}", "WARNING")

    if runner:
        try:
            await runner.cleanup()
            log("Webhook 服务器已关闭")
        except Exception as exc:
            log(f"关闭 Webhook 服务器失败: {exc}", "WARNING")
