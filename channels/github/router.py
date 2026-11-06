"""GitHub 频道的认证回调、状态与订阅管理路由。"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

from fastapi import APIRouter, Request

from agent.channel.channel_types import ChannelStatus
from core.log import log

if TYPE_CHECKING:
    from .adapter import GitHubChannel

def _get_github_channel() -> Optional[GitHubChannel]:
    """取已注册的 GitHub 频道实例(未注册返回 None)。"""
    try:
        from agent.channel import get_channel_manager

        from .adapter import GitHubChannel

        channel = get_channel_manager().get("github")
        if isinstance(channel, GitHubChannel):
            return channel
    except Exception as exc:
        log(f"GitHub: 频道实例尚不可用: {exc}", "DEBUG", tag="通道")
    return None


def build_router() -> Any:
    """GitHub 频道 HTTP 路由:webhook 入口 + 状态/事件/订阅管理 + 演练注入。

    webhook 端点自带 HMAC 验签(@self_authenticated);
    其余管理端点由 webui 密码体系默认保护。
    """
    from fastapi.responses import JSONResponse

    from core.http_endpoints import self_authenticated

    from .webhook import handle_test_inject, handle_webhook

    router = APIRouter()

    @router.post("/oauth/start")
    async def oauth_start(request: Request) -> Any:
        channel = _get_github_channel()
        if channel is None or channel._auth is None:
            return JSONResponse({"error": "请先启动 GitHub 频道"}, status_code=503)
        try:
            body = await request.json()
            account = str(body.get("account") or "")
            callback = str(request.url_for("github_oauth_callback"))
            return channel._auth.oauth.begin(account, callback)
        except (ValueError, AttributeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @router.get("/oauth/callback", name="github_oauth_callback")
    @self_authenticated
    async def oauth_callback(state: str = "", code: str = "") -> Any:
        channel = _get_github_channel()
        if channel is None or channel._auth is None:
            return JSONResponse({"error": "GitHub 频道未运行"}, status_code=503)
        try:
            account = await channel._auth.oauth.finish(state, code)
            return {"success": True, "account": account}
        except Exception:
            return JSONResponse({"error": "授权未完成，请返回频道页面重新授权"}, status_code=400)

    @router.post("/webhook")
    @self_authenticated
    async def webhook(request: Request) -> Any:
        channel = _get_github_channel()
        if channel is None or channel.status != ChannelStatus.RUNNING:
            return JSONResponse({"error": "GitHub 频道未运行"}, status_code=503)
        return await handle_webhook(channel, request)

    @router.post("/webhook/{account}")
    @self_authenticated
    async def account_webhook(account: str, request: Request) -> Any:
        channel = _get_github_channel()
        if channel is None or channel.status != ChannelStatus.RUNNING:
            return JSONResponse({"error": "GitHub 频道未运行"}, status_code=503)
        return await handle_webhook(channel, request, account)

    @router.get("/schema")
    async def schema() -> Dict[str, Any]:
        from .config import GitHubConfig
        return GitHubConfig.model_json_schema()

    @router.post("/retry")
    async def retry(request: Request) -> Any:
        channel = _get_github_channel()
        if channel is None:
            return JSONResponse({"error": "GitHub 频道未运行"}, status_code=503)
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("请求体必须是对象")
            import json
            result = json.loads(await channel.retry_delivery(str(body.get("event_id") or ""), str(body.get("repo") or "")))
            return JSONResponse(result, status_code=400 if result.get("error") else 200)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @router.get("/status")
    async def status() -> Dict[str, Any]:
        channel = _get_github_channel()
        if channel is None:
            return {"enabled": False, "hint": "GitHub 频道未注册(github_enabled=false)"}
        info = channel.get_status_info()
        gh = info.get("github", {})
        gh["enabled"] = True
        gh["status"] = info.get("status")
        gh["repo_states"] = channel._poller.repo_states() if channel._poller else {}
        if channel._journal:
            gh["inbox"] = await channel._journal.snapshot()
        gh["webhook_url_hint"] = "/api/channels/github/webhook"
        return gh

    @router.get("/events")
    async def events(repo: str = "", limit: int = 50) -> Dict[str, Any]:
        channel = _get_github_channel()
        if channel is None:
            return {"events": [], "hint": "GitHub 频道未注册"}
        return {"events": channel._stats.recent_events(repo=repo, limit=min(max(limit, 1), 200))}

    @router.get("/subscriptions")
    async def list_subs() -> Dict[str, Any]:
        channel = _get_github_channel()
        if channel is None:
            return {"repos": [], "hint": "GitHub 频道未注册"}
        return {"repos": [sub.model_dump() for sub in channel.config.repos]}

    @router.put("/subscriptions")
    async def put_subs(request: Request) -> Any:
        channel = _get_github_channel()
        if channel is None:
            return JSONResponse({"error": "GitHub 频道未注册"}, status_code=503)
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "body 必须是 JSON"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "请求体必须是对象"}, status_code=400)
        repos = body.get("repos")
        if not isinstance(repos, list):
            return JSONResponse({"error": "需要 repos 数组"}, status_code=400)
        try:
            from .config import RepoSubscription
            validated = [RepoSubscription.model_validate(x).model_dump() for x in repos]
        except Exception as exc:
            return JSONResponse({"error": f"订阅配置非法: {exc}"}, status_code=400)
        from agent.channel.config import set_channel_config
        try:
            set_channel_config("github", repos=validated)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"success": True, "count": len(validated)}

    @router.post("/test-inject")
    async def test_inject(request: Request) -> Any:
        channel = _get_github_channel()
        if channel is None or channel.status != ChannelStatus.RUNNING:
            return JSONResponse({"error": "GitHub 频道未运行"}, status_code=503)
        if channel.pipeline is None:
            return JSONResponse({"error": "事件管线未初始化"}, status_code=503)
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "body 必须是 JSON"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "请求体必须是对象"}, status_code=400)
        return await handle_test_inject(channel, body)

    return router
