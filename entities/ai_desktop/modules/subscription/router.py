"""额度组件的授权端点，继承实体路由鉴权。"""

from typing import Any

from fastapi import APIRouter, HTTPException, Response

from ... import framework
from .bailian import cli_credential, console_authorization_present
from .login import get_console_login
from .module import SubscriptionModule
from .providers import PROVIDERS


def build_router() -> APIRouter:
    """仅显式登录申请二维码，其余端点只操作已有会话。"""
    router = APIRouter(prefix="/modules/subscription/login")

    async def refresh() -> None:
        await framework.force_refresh("subscription")

    service = get_console_login(refresh)

    @router.get("")
    async def credential() -> dict[str, Any]:
        module = framework.get_module("subscription")
        spec = next(spec for spec in PROVIDERS if spec.key == "bailian")
        enabled = isinstance(module, SubscriptionModule) and module.is_enabled() and module.provider_enabled(spec)
        return {"available": bool(cli_credential()[0]), "configured": console_authorization_present(),
                "monitoring": enabled}

    @router.post("")
    async def start() -> dict[str, Any]:
        try:
            return await service.start()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/{session_id}")
    async def status(session_id: str) -> dict[str, Any]:
        try:
            return service.status(session_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.get("/{session_id}/image")
    async def image(session_id: str) -> Response:
        try:
            return Response(await service.image(session_id), media_type="image/png",
                            headers={"Cache-Control": "no-store"})
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/{session_id}/confirm")
    async def confirm(session_id: str) -> dict[str, bool]:
        try:
            await service.confirm(session_id)
            return {"success": True}
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.delete("/{session_id}")
    async def cancel(session_id: str) -> dict[str, bool]:
        try:
            await service.close(session_id)
            return {"success": True}
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc

    return router
