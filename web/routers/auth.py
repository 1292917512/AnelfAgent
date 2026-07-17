"""认证 API 路由 — 登录 / 状态检查 / 登出 / API Key。"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    password: str


@router.get("/check")
async def check_auth(request: Request) -> Dict[str, Any]:
    """检查当前认证状态。返回是否需要密码及是否已认证。"""
    from web.server import _load_auth_password, _make_token

    password = _load_auth_password()
    if not password:
        return {"required": False, "authenticated": True}

    token = request.cookies.get("_anelf_token", "")
    return {"required": True, "authenticated": token == _make_token(password)}


@router.post("/login")
async def login(body: LoginRequest) -> JSONResponse:
    """验证密码并设置认证 cookie。"""
    from web.server import _load_auth_password, _make_token

    password = _load_auth_password()
    if not password:
        return JSONResponse({"status": "ok", "message": "无需密码"})

    if body.password != password:
        return JSONResponse({"error": "密码错误"}, status_code=403)

    token = _make_token(password)
    resp = JSONResponse({"status": "ok"})
    resp.set_cookie(
        "_anelf_token", token,
        httponly=True, samesite="lax", max_age=30 * 86400,
    )
    return resp


@router.post("/logout")
async def logout() -> JSONResponse:
    """清除认证 cookie。"""
    resp = JSONResponse({"status": "ok"})
    resp.delete_cookie("_anelf_token")
    return resp


class PasswordUpdate(BaseModel):
    new_password: str


@router.put("/password")
async def update_password(body: PasswordUpdate) -> JSONResponse:
    """修改访问密码。空字符串表示取消密码保护。修改后需重新登录。"""
    from web.auth_keys import load_webui_config, save_webui_config
    from web.server import _make_token

    cfg = load_webui_config()
    cfg.setdefault("auth", {})["password"] = body.new_password
    save_webui_config(cfg)

    resp = JSONResponse({"status": "ok"})
    if body.new_password:
        resp.set_cookie(
            "_anelf_token", _make_token(body.new_password),
            httponly=True, samesite="lax", max_age=30 * 86400,
        )
    else:
        resp.delete_cookie("_anelf_token")
    return resp


class ApiKeyCreateReq(BaseModel):
    name: str = "default"


@router.get("/api-keys")
async def list_api_keys() -> Dict[str, Any]:
    from web.auth_keys import list_api_keys as _list

    return {"keys": _list()}


@router.post("/api-keys")
async def create_api_key(req: ApiKeyCreateReq) -> Dict[str, Any]:
    from web.auth_keys import create_api_key as _create

    return _create(name=req.name)


@router.post("/api-keys/{key_id}/rotate")
async def rotate_api_key(key_id: str) -> Dict[str, Any]:
    from web.auth_keys import rotate_api_key as _rotate

    result = _rotate(key_id)
    if result is None:
        raise HTTPException(404, f"API Key '{key_id}' 不存在")
    return result


@router.delete("/api-keys/{key_id}")
async def delete_api_key(key_id: str) -> Dict[str, str]:
    from web.auth_keys import delete_api_key as _delete

    if not _delete(key_id):
        raise HTTPException(404, f"API Key '{key_id}' 不存在")
    return {"status": "ok"}
