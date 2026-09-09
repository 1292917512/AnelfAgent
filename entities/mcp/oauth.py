"""MCP OAuth 授权 — 基于 MCP SDK OAuthClientProvider 的本地授权流。

组件：
- ``FileTokenStorage``：token / 动态注册客户端信息的文件持久化
  （config/mcp_oauth.json，原子写 + 0600 权限，经 TokenStorage 协议接入 SDK）
- ``LoopbackCallbackServer``：一次性回环回调服务（临时端口，RFC 8252 本地
  重定向），授权码经 asyncio.Future 桥回运行中的事件循环
- ``make_oauth_provider``：为 HTTP 传输的 server 构造 SDK OAuth 提供者；
  SDK 在 401 时自动完成 discovery / 动态注册 / PKCE / 刷新，本模块只提供
  重定向展示（事件 + 待授权登记表）与回调接收

待授权状态经 ``pending_auth`` 登记表暴露：AI 工具与 Web 界面据此把
授权链接呈现给用户；授权完成自动触发该 server 重连。
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from core.file_utils import atomic_write_text
from core.log import log
from core.path import ConfigPaths

_TAG = "MCP-OAuth"

# 回环回调等待授权码的超时（秒）
_CALLBACK_TIMEOUT = 300.0
# 待授权登记的有效窗口（秒）：超时后允许重新发起授权流
_PENDING_TTL = _CALLBACK_TIMEOUT


# ==================================================================
# Token 存储
# ==================================================================

class FileTokenStorage:
    """MCP SDK TokenStorage 协议实现：按 server 名持久化到 mcp_oauth.json。

    文件结构：{server_name: {"tokens": {...}, "client_info": {...}}}。
    写盘原子化 + 0600 权限；读取失败按无凭据处理（触发重新授权）。
    """

    def __init__(self, server_name: str, path: Optional[Path] = None) -> None:
        self._server = server_name
        self._path = path or Path(ConfigPaths.MCP_OAUTH_TOKENS)
        self._lock = threading.RLock()

    def _read_all(self) -> Dict[str, Any]:
        try:
            with self._lock:
                if not self._path.is_file():
                    return {}
                return json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log(f"OAuth 凭据读取失败（按无凭据处理）: {e}", "WARNING", tag=_TAG)
            return {}

    def _write_entry(self, key: str, value: Any) -> None:
        with self._lock:
            data = self._read_all()
            entry = data.setdefault(self._server, {})
            entry[key] = value
            atomic_write_text(self._path, json.dumps(data, ensure_ascii=False, indent=2))
            try:
                self._path.chmod(0o600)
            except OSError:
                pass

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken

        raw = self._read_all().get(self._server, {}).get("tokens")
        if not isinstance(raw, dict):
            return None
        try:
            return OAuthToken.model_validate(raw)
        except Exception:
            return None

    async def set_tokens(self, tokens) -> None:
        self._write_entry("tokens", tokens.model_dump(mode="json"))
        log(f"OAuth token 已保存: {self._server}", tag=_TAG)

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull

        raw = self._read_all().get(self._server, {}).get("client_info")
        if not isinstance(raw, dict):
            return None
        try:
            return OAuthClientInformationFull.model_validate(raw)
        except Exception:
            return None

    async def set_client_info(self, client_info) -> None:
        self._write_entry("client_info", client_info.model_dump(mode="json"))


def delete_credentials(server_name: str, path: Optional[Path] = None) -> bool:
    """删除指定 server 的 OAuth 凭据（tokens + 动态注册客户端信息）。"""
    store_path = path or Path(ConfigPaths.MCP_OAUTH_TOKENS)
    try:
        if not store_path.is_file():
            return False
        data = json.loads(store_path.read_text(encoding="utf-8"))
        if server_name not in data:
            return False
        del data[server_name]
        atomic_write_text(store_path, json.dumps(data, ensure_ascii=False, indent=2))
        return True
    except (OSError, json.JSONDecodeError) as e:
        log(f"OAuth 凭据删除失败: {e}", "WARNING", tag=_TAG)
        return False


def has_credentials(server_name: str, path: Optional[Path] = None) -> bool:
    """是否已有该 server 的 OAuth token。"""
    store_path = path or Path(ConfigPaths.MCP_OAUTH_TOKENS)
    try:
        if not store_path.is_file():
            return False
        data = json.loads(store_path.read_text(encoding="utf-8"))
        return bool(data.get(server_name, {}).get("tokens"))
    except (OSError, json.JSONDecodeError):
        return False


# ==================================================================
# 待授权登记（AI 工具 / Web 界面读取授权链接）
# ==================================================================

_pending: Dict[str, Dict[str, Any]] = {}
_pending_lock = threading.Lock()


def pending_auth(server_name: str = "") -> Dict[str, Dict[str, Any]]:
    """当前待授权登记（server → {url, started_at}）；过期项自动清理。"""
    now = time.time()
    with _pending_lock:
        stale = [k for k, v in _pending.items() if now - v["started_at"] > _PENDING_TTL]
        for k in stale:
            _pending.pop(k, None)
        if server_name:
            entry = _pending.get(server_name)
            return {server_name: entry} if entry else {}
        return {k: dict(v) for k, v in _pending.items()}


def clear_pending_auth(server_name: str) -> None:
    with _pending_lock:
        _pending.pop(server_name, None)


# ==================================================================
# 回环回调服务
# ==================================================================

class LoopbackCallbackServer:
    """一次性 OAuth 回环回调服务（临时端口，收到一次回调即关闭）。"""

    def __init__(self) -> None:
        # 先绑端口拿端口号（redirect_uri 须在构造 provider 前确定）
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        self.port = probe.getsockname()[1]
        probe.close()
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._result: Optional[Tuple[str, Optional[str]]] = None
        self._ready = threading.Event()

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.port}/callback"

    def start(self) -> None:
        """启动监听（守护线程，不阻塞调用方）。"""
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802（http.server 约定）
                from urllib.parse import parse_qs, urlparse
                query = parse_qs(urlparse(self.path).query)
                code = (query.get("code") or [""])[0]
                state = (query.get("state") or [None])[0]
                error = (query.get("error") or [""])[0]
                outer._result = (code, state)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                message = (
                    "授权失败，可关闭本页。" if error or not code
                    else "授权完成，可关闭本页返回对话。"
                )
                self.wfile.write(f"<html><body><p>{message}</p></body></html>".encode("utf-8"))
                outer._ready.set()

            def log_message(self, *args: Any) -> None:
                pass  # 回调访问日志静默

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), _Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="mcp-oauth-callback", daemon=True,
        )
        self._thread.start()

    async def wait_callback(self, timeout: float = _CALLBACK_TIMEOUT) -> Tuple[str, Optional[str]]:
        """等待授权回调，返回 (code, state)；超时抛 TimeoutError。"""
        loop = asyncio.get_running_loop()
        ok = await loop.run_in_executor(None, self._ready.wait, timeout)
        self.close()
        if not ok:
            raise TimeoutError(f"OAuth 回调等待超时（{timeout:.0f}s）")
        if not self._result or not self._result[0]:
            raise ValueError("OAuth 回调缺少授权码（用户可能在授权页拒绝了访问）")
        return self._result

    def close(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


# ==================================================================
# OAuth 提供者装配
# ==================================================================

def oauth_eligible(srv) -> bool:
    """该 server 是否可启用 OAuth（HTTP 传输且 mcp_oauth_enabled 开启）。"""
    transport = srv.transport or ("stdio" if srv.command else "streamable_http")
    if transport == "stdio":
        return False
    try:
        from core.config import get_config_bool
        return get_config_bool("mcp_oauth_enabled", True)
    except Exception:
        return True


def make_oauth_provider(srv) -> Optional[Any]:
    """为 HTTP 传输的 MCP server 构造 SDK OAuth 提供者（不适用时返回 None）。

    SDK 在 401 时自动发起授权流：discovery → 动态注册（server 配置
    oauth.client_id 时预置客户端信息跳过注册）→ 浏览器授权 → 回调换
    token → 持久化。
    """
    if not oauth_eligible(srv):
        return None

    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthClientMetadata

    oauth_cfg = srv.oauth if isinstance(getattr(srv, "oauth", None), dict) else {}
    callback = LoopbackCallbackServer()
    storage = FileTokenStorage(srv.name)

    client_id = str(oauth_cfg.get("client_id") or oauth_cfg.get("clientId") or "").strip()
    if client_id:
        _seed_client_id(storage, client_id, callback.redirect_uri, oauth_cfg)

    async def _redirect(authorize_url: str) -> None:
        with _pending_lock:
            _pending[srv.name] = {"url": authorize_url, "started_at": time.time()}
        log(f"MCP server '{srv.name}' 需要 OAuth 授权: {authorize_url}", "WARNING", tag=_TAG)
        try:
            from core.async_helper import spawn
            from core.event_bus import event_bus
            spawn(event_bus.emit("mcp_auth_required", {
                "server": srv.name, "url": authorize_url,
            }), name="mcp-auth-required")
        except RuntimeError:
            pass
        # 有桌面环境时直接拉起浏览器（headless 场景静默失败，链接已登记）
        try:
            webbrowser.open(authorize_url)
        except Exception:
            pass

    async def _callback() -> Tuple[str, Optional[str]]:
        callback.start()
        try:
            return await callback.wait_callback()
        finally:
            callback.close()

    scopes = str(oauth_cfg.get("scopes") or oauth_cfg.get("scope") or "").strip()
    metadata = OAuthClientMetadata(
        client_name="AnelfAgent",
        redirect_uris=[callback.redirect_uri],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
        **({"scope": scopes} if scopes else {}),
    )
    return OAuthClientProvider(
        server_url=srv.url,
        client_metadata=metadata,
        storage=storage,
        redirect_handler=_redirect,
        callback_handler=_callback,
        timeout=_CALLBACK_TIMEOUT,
    )


def _seed_client_id(storage: FileTokenStorage, client_id: str,
                    redirect_uri: str, oauth_cfg: Dict[str, Any]) -> None:
    """把预注册的 client_id 写入存储（SDK 检测到已有客户端信息即跳过动态注册）。

    已有存储的客户端信息时不覆盖（动态注册结果优先，含 client_secret 等）。
    """
    import asyncio as _asyncio

    from mcp.shared.auth import OAuthClientInformationFull

    async def _seed() -> None:
        if await storage.get_client_info() is not None:
            return
        scopes = str(oauth_cfg.get("scopes") or oauth_cfg.get("scope") or "").strip()
        await storage.set_client_info(OAuthClientInformationFull(
            client_id=client_id,
            redirect_uris=[redirect_uri],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
            **({"scope": scopes} if scopes else {}),
        ))

    try:
        _asyncio.get_running_loop()
    except RuntimeError:
        _asyncio.run(_seed())
    else:
        from core.async_helper import spawn
        spawn(_seed(), name="mcp-oauth-seed")
