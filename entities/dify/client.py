"""Dify HTTP 客户端 — Console 管理面（JWT cookie + CSRF）与 Service 运行面（Bearer Key）。

Console API 鉴权（dify api/libs/token.py + libs/login.py 求值链）：
- POST /console/api/login 响应经 Set-Cookie 写入 access_token / refresh_token / csrf_token
- 后续请求携带 cookie + ``X-CSRF-Token`` 头（值须等于 csrf_token cookie，见 check_csrf_token）
- access_token 过期（401）时 POST /console/api/refresh-token（refresh 走 cookie）换新；
  刷新失败则凭管理员凭据重新登录；登录也失败抛 DifyAuthError

Service API 鉴权：``Authorization: Bearer app-xxx``（api/controllers/service_api/wraps.py）。

两个客户端均为无连接池的短生命周期封装，由 service.py 按需创建；
cookie 会话状态保存在实例内存中，不落盘（重启进程后自动重新登录）。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

import httpx

from core.log import log


class DifyApiError(Exception):
    """Dify API 错误（含 HTTP 状态码与响应摘要）。"""

    def __init__(self, message: str, *, status: int = 0, detail: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.detail = detail


class DifyAuthError(DifyApiError):
    """认证失败（凭据缺失/无效且重登失败）。"""


class DifyNotFoundError(DifyApiError):
    """目标资源不存在（404）。"""


def _extract_error(resp: httpx.Response) -> str:
    """从错误响应提取摘要（优先 Dify 的 message/error 字段，截断防爆量）。"""
    try:
        data = resp.json()
        if isinstance(data, dict):
            for key in ("message", "error", "detail"):
                if data.get(key):
                    return str(data[key])[:300]
    except Exception:
        pass
    return (resp.text or "")[:300]


def _raise_for(resp: httpx.Response, action: str) -> None:
    if resp.status_code < 400:
        return
    detail = _extract_error(resp)
    if resp.status_code == 404:
        raise DifyNotFoundError(f"{action}失败: 资源不存在 ({detail})",
                                status=404, detail=detail)
    raise DifyApiError(f"{action}失败 (HTTP {resp.status_code}): {detail}",
                       status=resp.status_code, detail=detail)


# ------------------------------------------------------------------
# Console 客户端（管理面）
# ------------------------------------------------------------------


class DifyConsoleClient:
    """Dify Console API 客户端（管理员会话，cookie + CSRF）。

    使用方式::

        client = DifyConsoleClient("http://127.0.0.1:8899")
        await client.setup_status()
        await client.login(email, password)
        apps = await client.list_apps()
    """

    def __init__(self, base_url: str, *, timeout: float = 30.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None
        self._credentials: Dict[str, str] = {}
        self._login_lock = asyncio.Lock()

    async def __aenter__(self) -> "DifyConsoleClient":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base,
                timeout=self._timeout,
                follow_redirects=False,
            )
        return self._client

    # ---- 鉴权 ----

    def set_credentials(self, email: str, password: str) -> None:
        self._credentials = {"email": email, "password": password}

    def _csrf_token(self) -> str:
        client = self._ensure_client()
        return client.cookies.get("csrf_token") or ""

    async def setup_status(self) -> Dict[str, Any]:
        """GET /console/api/setup（无鉴权）：{"step": "not_started"|"finished"}。"""
        client = self._ensure_client()
        resp = await client.get("/console/api/setup")
        _raise_for(resp, "查询初始化状态")
        data = resp.json()
        return data if isinstance(data, dict) else {}

    async def probe(self) -> Optional[Dict[str, Any]]:
        """连通性探测：可达返回 setup 状态，不可达返回 None（不抛异常）。"""
        try:
            return await self.setup_status()
        except Exception:
            return None

    async def fetch_version(self) -> str:
        """GET /console/api/version 取 Dify 版本号（不可达/无权限返回空串）。"""
        try:
            client = self._ensure_client()
            resp = await client.get("/console/api/version")
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict):
                    return str(data.get("version") or "")
        except Exception:
            pass
        return ""

    async def setup_admin(self, email: str, name: str, password: str,
                          language: str = "zh-Hans") -> None:
        """POST /console/api/setup 创建管理员（一次性，自托管专属）。"""
        client = self._ensure_client()
        resp = await client.post("/console/api/setup", json={
            "email": email, "name": name, "password": password, "language": language,
        })
        if resp.status_code in (400, 409):
            detail = _extract_error(resp)
            if "already" in detail.lower() or "exist" in detail.lower():
                raise DifyApiError("Dify 已完成初始化（无法重复创建管理员）",
                                   status=resp.status_code, detail=detail)
        _raise_for(resp, "初始化管理员")

    async def login(self, email: str, password: str) -> None:
        """POST /console/api/login，成功后会话 cookie 存入客户端。"""
        client = self._ensure_client()
        resp = await client.post("/console/api/login", json={
            "email": email, "password": password, "remember_me": True,
        })
        if resp.status_code == 401:
            raise DifyAuthError("Dify 管理员登录失败：邮箱或密码错误",
                                status=401, detail=_extract_error(resp))
        _raise_for(resp, "登录 Dify 控制台")
        data = resp.json() if resp.content else {}
        if isinstance(data, dict) and data.get("result") == "fail":
            raise DifyAuthError(f"Dify 登录被拒: {data.get('data') or '未知原因'}", status=401)
        if not self._csrf_token():
            raise DifyAuthError("Dify 登录成功但未获得 csrf_token cookie", status=401)
        self._credentials = {"email": email, "password": password}

    async def _refresh(self) -> bool:
        """用 refresh_token cookie 换新 access_token，成功返回 True。"""
        client = self._ensure_client()
        try:
            resp = await client.post("/console/api/refresh-token")
            return resp.status_code == 200
        except Exception:
            return False

    async def _relogin(self) -> None:
        """refresh 失败后凭保存的凭据重登；无凭据则抛 DifyAuthError。"""
        email = self._credentials.get("email", "")
        password = self._credentials.get("password", "")
        if not email or not password:
            raise DifyAuthError("Dify 管理员凭据缺失：请先部署并初始化，或在面板填写凭据", status=401)
        await self.login(email, password)

    async def ensure_session(self) -> None:
        """确保已登录（无 csrf cookie 时自动用已存凭据登录）。"""
        async with self._login_lock:
            if self._csrf_token():
                return
            await self._relogin()

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Any] = None,
        params: Optional[Dict[str, Any]] = None,
        action: str = "Dify 管理操作",
        _retried: bool = False,
    ) -> Any:
        """Console API 通用请求：自动携带 CSRF 头，401 自动刷新/重登后重试一次。"""
        await self.ensure_session()
        client = self._ensure_client()
        headers = {"X-CSRF-Token": self._csrf_token()}
        try:
            resp = await client.request(
                method, f"/console/api{path}",
                json=json_body, params=params, headers=headers,
            )
        except httpx.HTTPError as exc:
            raise DifyApiError(f"{action}失败: 网络错误 ({exc})") from exc

        if resp.status_code == 401 and not _retried:
            async with self._login_lock:
                if not await self._refresh():
                    await self._relogin()
            return await self.request(
                method, path, json_body=json_body, params=params,
                action=action, _retried=True,
            )
        if resp.status_code == 401:
            raise DifyAuthError("Dify 会话失效且重登失败：请核对管理员凭据", status=401)
        _raise_for(resp, action)
        if not resp.content:
            return {}
        try:
            return resp.json()
        except json.JSONDecodeError:
            return {"raw": resp.text[:2000]}

    # ---- 应用 ----

    async def list_apps(self, page: int = 1, limit: int = 100, name: str = "") -> Dict[str, Any]:
        params: Dict[str, Any] = {"page": page, "limit": limit}
        if name:
            params["name"] = name
        return await self.request("GET", "/apps", params=params, action="列出应用")

    async def get_app(self, app_id: str) -> Dict[str, Any]:
        return await self.request("GET", f"/apps/{app_id}", action="查询应用")

    async def create_app(self, name: str, mode: str, description: str = "") -> Dict[str, Any]:
        return await self.request("POST", "/apps", json_body={
            "name": name, "mode": mode, "description": description,
        }, action="创建应用")

    async def delete_app(self, app_id: str) -> None:
        await self.request("DELETE", f"/apps/{app_id}", action="删除应用")

    async def copy_app(self, app_id: str, name: str = "") -> Dict[str, Any]:
        body: Dict[str, Any] = {}
        if name:
            body["name"] = name
        return await self.request("POST", f"/apps/{app_id}/copy",
                                  json_body=body, action="复制应用")

    async def export_dsl(self, app_id: str, include_secret: bool = False) -> str:
        data = await self.request(
            "GET", f"/apps/{app_id}/export",
            params={"include_secret": str(include_secret).lower()},
            action="导出应用 DSL",
        )
        if isinstance(data, dict) and isinstance(data.get("data"), str):
            return data["data"]
        raise DifyApiError("导出 DSL 响应格式异常")

    async def import_dsl(self, yaml_content: str) -> Dict[str, Any]:
        """POST /apps/imports（mode=yaml-content），返回含 app_id 的结果。"""
        return await self.request("POST", "/apps/imports", json_body={
            "mode": "yaml-content", "yaml_content": yaml_content,
        }, action="导入应用 DSL")

    # ---- 工作流 ----

    async def publish_workflow(self, app_id: str, marked_name: str = "",
                               marked_comment: str = "") -> Dict[str, Any]:
        return await self.request("POST", f"/apps/{app_id}/workflows/publish", json_body={
            "marked_name": marked_name, "marked_comment": marked_comment,
        }, action="发布工作流")

    # ---- API Key ----

    async def list_api_keys(self, app_id: str) -> Dict[str, Any]:
        return await self.request("GET", f"/apps/{app_id}/api-keys", action="列出 API Key")

    async def create_api_key(self, app_id: str) -> Dict[str, Any]:
        return await self.request("POST", f"/apps/{app_id}/api-keys", action="创建 API Key")

    async def delete_api_key(self, app_id: str, api_key_id: str) -> None:
        await self.request("DELETE", f"/apps/{app_id}/api-keys/{api_key_id}",
                           action="删除 API Key")

    async def set_api_enabled(self, app_id: str, enabled: bool = True) -> None:
        await self.request("POST", f"/apps/{app_id}/api-enable",
                           json_body={"status": "enabled" if enabled else "disabled"},
                           action="切换 API 访问")

    # ---- 模型供应商 ----

    async def list_model_providers(self) -> Any:
        return await self.request("GET", "/workspaces/current/model-providers",
                                  action="列出模型供应商")

    async def set_model_credential(self, provider: str, credentials: Dict[str, Any],
                                   name: str = "") -> None:
        body: Dict[str, Any] = {"credentials": credentials}
        if name:
            body["name"] = name
        await self.request(
            "POST", f"/workspaces/current/model-providers/{provider}/credentials",
            json_body=body, action="配置模型供应商凭据",
        )

    async def validate_model_credential(self, provider: str, credentials: Dict[str, Any]) -> Any:
        return await self.request(
            "POST", f"/workspaces/current/model-providers/{provider}/credentials/validate",
            json_body={"credentials": credentials}, action="校验模型凭据",
        )

    # ---- 数据集 ----

    async def list_datasets(self, page: int = 1, limit: int = 50) -> Dict[str, Any]:
        return await self.request("GET", "/datasets",
                                  params={"page": page, "limit": limit}, action="列出数据集")

    async def create_dataset(self, name: str, description: str = "") -> Dict[str, Any]:
        return await self.request("POST", "/datasets",
                                  json_body={"name": name, "description": description},
                                  action="创建数据集")

    # ---- MCP Server 暴露 ----

    async def get_mcp_server(self, app_id: str) -> Any:
        return await self.request("GET", f"/apps/{app_id}/server", action="查询 MCP Server")

    async def enable_mcp_server(self, app_id: str, description: str = "",
                                parameters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return await self.request("POST", f"/apps/{app_id}/server", json_body={
            "description": description, "parameters": parameters or {},
        }, action="启用 MCP Server")

    async def disable_mcp_server(self, app_id: str) -> None:
        await self.request("PUT", f"/apps/{app_id}/server",
                           json_body={"status": "inactive"}, action="停用 MCP Server")


# ------------------------------------------------------------------
# Service 客户端（运行面，Bearer app key）
# ------------------------------------------------------------------


class DifyServiceClient:
    """Dify Service API 客户端（/v1，Bearer app-xxx Key）。"""

    def __init__(self, base_url: str, api_key: str, *, timeout: float = 120.0) -> None:
        self._base = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    async def run_workflow(self, inputs: Dict[str, Any], user: str = "anelf-agent",
                           response_mode: str = "blocking") -> Dict[str, Any]:
        """POST /v1/workflows/run（blocking 同步等待结果）。"""
        payload = {
            "inputs": inputs,
            "response_mode": response_mode,
            "user": user,
        }
        async with httpx.AsyncClient(base_url=self._base, timeout=self._timeout) as client:
            try:
                resp = await client.post("/v1/workflows/run", json=payload,
                                         headers=self._headers())
            except httpx.HTTPError as exc:
                raise DifyApiError(f"运行工作流失败: 网络错误 ({exc})") from exc
            _raise_for(resp, "运行工作流")
            data = resp.json()
            return data if isinstance(data, dict) else {"raw": str(data)[:2000]}

    async def chat(self, query: str, inputs: Optional[Dict[str, Any]] = None,
                   conversation_id: str = "", user: str = "anelf-agent") -> Dict[str, Any]:
        """POST /v1/chat-messages（blocking，返回含 answer / conversation_id）。"""
        payload: Dict[str, Any] = {
            "inputs": inputs or {},
            "query": query,
            "response_mode": "blocking",
            "user": user,
        }
        if conversation_id:
            payload["conversation_id"] = conversation_id
        async with httpx.AsyncClient(base_url=self._base, timeout=self._timeout) as client:
            try:
                resp = await client.post("/v1/chat-messages", json=payload,
                                         headers=self._headers())
            except httpx.HTTPError as exc:
                raise DifyApiError(f"发送对话消息失败: 网络错误 ({exc})") from exc
            _raise_for(resp, "发送对话消息")
            data = resp.json()
            return data if isinstance(data, dict) else {"raw": str(data)[:2000]}

    async def stop_task(self, task_id: str, user: str = "anelf-agent") -> None:
        """POST /v1/chat-messages/<task_id>/stop。"""
        async with httpx.AsyncClient(base_url=self._base, timeout=30.0) as client:
            resp = await client.post(f"/v1/chat-messages/{task_id}/stop",
                                     json={"user": user}, headers=self._headers())
            _raise_for(resp, "停止任务")
