"""SillyTavern HTTP API 桥接客户端。

无用户系统（默认）下酒馆的唯一门槛是 CSRF：先 GET /csrf-token 领取
token（session cookie 由 httpx cookie jar 维持），此后所有 POST 携带
X-CSRF-Token 头。若服务以 --disableCsrf 启动则 token 为 "disabled"，
同一流程天然兼容。

所有端点均为 POST（除 GET /version），超时与错误统一抛 STError，
由调用方（tools.py / router.py）转成工具错误或 HTTP 错误。
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

import httpx


class STError(RuntimeError):
    """酒馆 API 调用失败（网络不可达 / 非 2xx / 响应异常）。"""


class STClient:
    """单例式酒馆 API 客户端（懒连接，端口变更后自动重建）。"""

    def __init__(self) -> None:
        self._client: Optional[httpx.Client] = None
        self._base_url: str = ""
        self._token: Optional[str] = None
        self._lock = threading.Lock()

    def reset(self) -> None:
        """酒馆重启/换端口后调用，丢弃旧 session。"""
        with self._lock:
            self._close()
            self._token = None

    def _close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def _ensure(self, base_url: str) -> httpx.Client:
        with self._lock:
            if self._client is not None and self._base_url != base_url:
                self._close()
                self._token = None
            if self._client is None:
                # trust_env=False：本机服务绝不走系统代理（HTTP_PROXY 会导致 502）
                self._client = httpx.Client(base_url=base_url, timeout=60, trust_env=False)
                self._base_url = base_url
            return self._client

    # ------------------------------------------------------------------
    # 底层请求
    # ------------------------------------------------------------------

    def _ensure_token(self, base_url: str) -> str:
        if self._token is not None:
            return self._token
        client = self._ensure(base_url)
        try:
            resp = client.get("/csrf-token")
            resp.raise_for_status()
            data = resp.json()
            token = data.get("token")
            if not token:
                raise STError("csrf-token 响应缺少 token 字段")
        except httpx.HTTPError as e:
            raise STError(f"获取 CSRF token 失败（酒馆是否在运行？）: {e}") from e
        except ValueError as e:
            raise STError(f"csrf-token 响应解析失败: {e}") from e
        self._token = token
        return token

    def post(self, base_url: str, path: str, payload: Dict[str, Any] | None = None) -> Any:
        """POST JSON 到酒馆 API，返回解析后的 JSON（204 返回 None）。"""
        token = self._ensure_token(base_url)
        client = self._ensure(base_url)
        try:
            resp = client.post(path, json=payload or {}, headers={"X-CSRF-Token": token})
        except httpx.HTTPError as e:
            self._token = None  # session 可能已随重启失效，下次重领
            raise STError(f"请求 {path} 失败（酒馆是否在运行？）: {e}") from e
        if resp.status_code == 403:
            # CSRF 过期：重领一次后重试
            self._token = None
            token = self._ensure_token(base_url)
            try:
                resp = client.post(path, json=payload or {}, headers={"X-CSRF-Token": token})
            except httpx.HTTPError as e:
                raise STError(f"请求 {path} 重试失败: {e}") from e
        if resp.status_code == 204 or not resp.content:
            return None
        if resp.status_code >= 400:
            detail = ""
            try:
                detail = str(resp.json().get("error", ""))
            except Exception:
                detail = resp.text[:200]
            raise STError(f"{path} 返回 {resp.status_code}: {detail}".strip())
        try:
            return resp.json()
        except ValueError as e:
            raise STError(f"{path} 响应非 JSON: {e}") from e

    def post_text(self, base_url: str, path: str, payload: Dict[str, Any] | None = None) -> str:
        """POST 并返回纯文本响应体（部分端点如 characters/create 直接返回文件名）。"""
        token = self._ensure_token(base_url)
        client = self._ensure(base_url)
        try:
            resp = client.post(path, json=payload or {}, headers={"X-CSRF-Token": token})
        except httpx.HTTPError as e:
            self._token = None
            raise STError(f"请求 {path} 失败（酒馆是否在运行？）: {e}") from e
        if resp.status_code >= 400:
            detail = ""
            try:
                detail = str(resp.json().get("error", ""))
            except Exception:
                detail = resp.text[:200]
            raise STError(f"{path} 返回 {resp.status_code}: {detail}".strip())
        return resp.text.strip().strip('"')

    def get(self, base_url: str, path: str) -> Any:
        """GET 请求（插件 /health 等），返回解析后的 JSON。"""
        client = self._ensure(base_url)
        try:
            resp = client.get(path)
        except httpx.HTTPError as e:
            raise STError(f"请求 {path} 失败（酒馆是否在运行？）: {e}") from e
        if resp.status_code >= 400:
            raise STError(f"{path} 返回 {resp.status_code}")
        try:
            return resp.json()
        except ValueError as e:
            raise STError(f"{path} 响应非 JSON: {e}") from e

    # ------------------------------------------------------------------
    # 元信息
    # ------------------------------------------------------------------

    def version(self, base_url: str, timeout: float = 60.0) -> Dict[str, Any]:
        """GET /version（免 CSRF），用于健康探测。"""
        client = self._ensure(base_url)
        try:
            resp = client.get("/version", timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            raise STError(f"探测 /version 失败: {e}") from e
        except ValueError as e:
            raise STError(f"/version 响应解析失败: {e}") from e

    # ------------------------------------------------------------------
    # 角色管理
    # ------------------------------------------------------------------

    def characters_all(self, base_url: str) -> List[Dict[str, Any]]:
        return self.post(base_url, "/api/characters/all") or []

    @staticmethod
    def character_brief(c: Dict[str, Any]) -> Dict[str, Any]:
        """角色卡的精简摘要（列表/上下文注入用）。"""
        return {
            "name": c.get("name", ""),
            "avatar": c.get("avatar", ""),
            "description": (c.get("description") or "")[:200],
            "personality": (c.get("personality") or "")[:120],
            "first_mes": (c.get("first_mes") or "")[:120],
            "tags": c.get("tags") or [],
            "fav": bool(c.get("fav")),
            "talkativeness": c.get("talkativeness", 0),
        }

    def get_character(self, base_url: str, avatar: str) -> Dict[str, Any]:
        return self.post(base_url, "/api/characters/get", {"avatar_url": avatar})

    def create_character(self, base_url: str, fields: Dict[str, Any]) -> str:
        """创建角色，返回 avatar 标识（如 "Name.png"）。"""
        payload = {"ch_name": fields["name"]}
        for key in ("description", "personality", "scenario", "first_mes",
                    "mes_example", "system_prompt", "post_history_instructions",
                    "creator_notes", "tags", "creator", "character_version",
                    "alternate_greetings"):
            if fields.get(key):
                payload[key] = fields[key]
        avatar = self.post_text(base_url, "/api/characters/create", payload)
        if not avatar:
            raise STError("创建角色成功但未返回 avatar 标识")
        return avatar

    def edit_character_field(self, base_url: str, avatar: str, name: str,
                             field: str, value: Any) -> Dict[str, Any]:
        """单字段编辑（同时写入顶层与 data.*），返回响应文本（通常为 OK）。"""
        return self.post_text(base_url, "/api/characters/edit-attribute", {
            "avatar_url": avatar,
            "ch_name": name,
            "field": field,
            "value": value,
        })

    def delete_character(self, base_url: str, avatar: str, delete_chats: bool = False) -> str:
        return self.post_text(base_url, "/api/characters/delete", {
            "avatar_url": avatar,
            "delete_chats": delete_chats,
        })

    # ------------------------------------------------------------------
    # 设置 / 模型配置
    # ------------------------------------------------------------------

    def get_settings(self, base_url: str) -> Dict[str, Any]:
        """返回 settings.json 解析后的 dict。"""
        raw = self.post(base_url, "/api/settings/get") or {}
        settings = raw.get("settings")
        if isinstance(settings, str):
            import json
            settings = json.loads(settings)
        if not isinstance(settings, dict):
            raise STError("酒馆 settings 响应异常：缺少 settings 字段")
        out = {"settings": settings}
        for key in ("world_names", "enable_extensions", "enable_accounts"):
            if key in raw:
                out[key] = raw[key]
        return out

    def save_settings(self, base_url: str, settings: Dict[str, Any]) -> Any:
        """整文件覆盖写 settings.json（调用方须先 get 再改再 save）。"""
        return self.post(base_url, "/api/settings/save", settings)

    # ------------------------------------------------------------------
    # 密钥
    # ------------------------------------------------------------------

    def write_secret(self, base_url: str, key: str, value: str) -> Any:
        """写入密钥（如 api_key_custom），酒馆 generate 时从 secrets 读取。"""
        return self.post(base_url, "/api/secrets/write", {"key": key, "value": value})

    def read_secrets(self, base_url: str) -> Dict[str, Any]:
        """列出已配置的密钥（不返回值，仅元数据）。"""
        return self.post(base_url, "/api/secrets/read") or {}

    def save_chat(self, base_url: str, avatar: str, file_name: str,
                  chat: List[Dict[str, Any]], force: bool = True) -> Any:
        """整份保存聊天（messages 数组），force 覆盖完整性冲突。"""
        return self.post(base_url, "/api/chats/save", {
            "avatar_url": avatar, "file_name": file_name,
            "chat": chat, "force": force,
        })

    # ------------------------------------------------------------------
    # 聊天记录
    # ------------------------------------------------------------------

    def character_chats(self, base_url: str, avatar: str) -> List[Dict[str, Any]]:
        return self.post(base_url, "/api/characters/chats",
                         {"avatar_url": avatar, "simple": True}) or []

    def get_chat(self, base_url: str, avatar: str, file_name: str) -> List[Dict[str, Any]]:
        return self.post(base_url, "/api/chats/get",
                         {"avatar_url": avatar, "file_name": file_name}) or []

    def recent_chats(self, base_url: str) -> List[Dict[str, Any]]:
        return self.post(base_url, "/api/chats/recent") or []


_client: Optional[STClient] = None


def get_st_client() -> STClient:
    global _client
    if _client is None:
        _client = STClient()
    return _client
