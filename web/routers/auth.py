"""认证 API 路由 — 登录 / 状态检查 / 登出。"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Request
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
    import json as _json
    from pathlib import Path
    from core.path import ConfigPaths

    p = Path(ConfigPaths.WEBUI_CONFIG)
    cfg: Dict[str, Any] = {}
    if p.exists():
        try:
            cfg = _json.loads(p.read_text("utf-8"))
        except Exception:
            pass

    cfg.setdefault("auth", {})["password"] = body.new_password
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    from web.server import _make_token
    resp = JSONResponse({"status": "ok"})
    if body.new_password:
        resp.set_cookie(
            "_anelf_token", _make_token(body.new_password),
            httponly=True, samesite="lax", max_age=30 * 86400,
        )
    else:
        resp.delete_cookie("_anelf_token")
    return resp
