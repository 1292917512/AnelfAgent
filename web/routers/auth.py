"""认证 API 路由 — 登录 / 状态检查 / 登出 / API Key。"""

from __future__ import annotations

import hmac
import time
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter(prefix="/auth", tags=["auth"])

# 登录失败速率限制（内存态，按客户端 IP 计数）
_LOGIN_MAX_FAILURES = 5
_LOGIN_LOCKOUT_SECONDS = 60
_login_failures: Dict[str, list] = {}


def _check_login_allowed(client: str) -> bool:
    """检查客户端是否处于锁定状态，并清理过期记录。"""
    now = time.time()
    fails = [t for t in _login_failures.get(client, []) if now - t < _LOGIN_LOCKOUT_SECONDS]
    _login_failures[client] = fails
    return len(fails) < _LOGIN_MAX_FAILURES


def _record_login_result(client: str, success: bool) -> None:
    if success:
        _login_failures.pop(client, None)
    else:
        _login_failures.setdefault(client, []).append(time.time())


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
    expected = _make_token(password)
    authenticated = bool(token) and hmac.compare_digest(token, expected)
    return {"required": True, "authenticated": authenticated}


@router.post("/login")
async def login(body: LoginRequest, request: Request) -> JSONResponse:
    """验证密码并设置认证 cookie。连续失败触发临时锁定。"""
    from web.server import _load_auth_password, _make_token

    client = request.client.host if request.client else "unknown"
    if not _check_login_allowed(client):
        return JSONResponse(
            {"error": "失败次数过多，请稍后再试"}, status_code=429,
        )

    password = _load_auth_password()
    if not password:
        return JSONResponse({"status": "ok", "message": "无需密码"})

    if not hmac.compare_digest(body.password.encode("utf-8"), password.encode("utf-8")):
        _record_login_result(client, False)
        return JSONResponse({"error": "密码错误"}, status_code=403)

    _record_login_result(client, True)
    token = _make_token(password)
    resp = JSONResponse({"status": "ok"})
    resp.set_cookie(
        "_anelf_token", token,
        httponly=True, samesite="lax", max_age=30 * 86400,
        secure=request.url.scheme == "https",
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
async def update_password(body: PasswordUpdate, request: Request) -> JSONResponse:
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
            secure=request.url.scheme == "https",
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
