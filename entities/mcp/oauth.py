"""MCP OAuth 2.1 授权 — 自研 httpx.Auth 提供者（发现/注册/PKCE/刷新全链路）。

两阶段结构：

- **运行期**（``McpOAuthProvider``）：挂在 HTTP 传输上的凭据提供者。取
  token 时临期 30s 预刷新；请求 401 且服务器发 ``WWW-Authenticate: Bearer``
  挑战时，先刷新、刷新不可行或确定性失败才走交互授权——无 Bearer 挑战
  的 401（key 在 URL/headers 的静态凭据服务器）不做任何 OAuth 动作，
  连接按普通失败重连；403 insufficient_scope 以并集 scope 重新授权
  （step-up）。单飞纪律：每 server 一把 asyncio.Lock，并发请求共享同
  一次刷新/授权，避免 refresh token 轮转撞车。
- **交互事务**（``AuthorizationSession``）：RFC 9728 保护资源元数据发现
  （路径插入形 + 根回落）→ RFC 8414 授权服务器元数据（OIDC 回落 + issuer
  校验）→ RFC 7591 动态注册（或静态 client_id）→ PKCE S256 + state 回环
  授权 → 授权码换 token。prepare（发现/注册/出链接，秒级）与
  wait_and_exchange（等人点授权，分钟级）两段分离，供传输内联路径与
  Web 主动授权路径复用。

动态注册客户端随 token 一并持久化（刷新需要 client_id/secret），但下一次
交互授权总是重新注册——回环端口随事务变化，复用旧注册会把 redirect_uri
锁死在首次授权的端口（RFC 8252 端口弹性并非所有 AS 实现正确）。

凭据持久化：config/mcp_oauth.json（0600 + 原子写），条目含 tokens /
client_info / server_meta（发现的端点与 resource）/ 有效期。

待授权登记（``pending_auth``）：授权 URL 的唯一展示通道——AI 工具、Web
界面与 bridge 的连接等待延长共用；事务结束即清理。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote, urlencode, urlparse, urlsplit, urlunsplit

import httpx
from mcp.shared.auth import OAuthMetadata, OAuthToken, ProtectedResourceMetadata
from pydantic import ValidationError

from core.file_utils import atomic_write_text
from core.log import log
from core.path import ConfigPaths

_TAG = "MCP-OAuth"

# 回环回调等待授权码的超时（秒）
_CALLBACK_TIMEOUT = 300.0
# 待授权登记的有效窗口（秒）：超时后允许重新发起授权流
_PENDING_TTL = _CALLBACK_TIMEOUT
# token 临期判定提前量（秒）：进入窗口即预刷新
_EXPIRY_SKEW = 30.0
# 发现/注册/换 token 请求的超时（秒）
_HTTP_TIMEOUT = 15.0

_WWW_AUTH_HEADER = "WWW-Authenticate"


class OAuthTemporaryError(Exception):
    """授权服务暂时不可用（网络/5xx）：保留凭据原样失败，等待重试自愈。"""


class OAuthConfigurationError(Exception):
    """静态客户端配置无效：交互授权复用同一客户端无法自愈，需人工改配置。"""


# ==================================================================
# 凭据存储（config/mcp_oauth.json，条目 = tokens + client_info + server_meta）
# ==================================================================

_store_lock = threading.RLock()


def _read_all() -> Dict[str, Any]:
    store_path = Path(ConfigPaths.MCP_OAUTH_TOKENS)
    try:
        if not store_path.is_file():
            return {}
        data = json.loads(store_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as e:
        log(f"OAuth 凭据读取失败（按无凭据处理）: {e}", "WARNING", tag=_TAG)
        return {}


def _write_entry(server_name: str, entry: Dict[str, Any]) -> None:
    store_path = Path(ConfigPaths.MCP_OAUTH_TOKENS)
    with _store_lock:
        data = _read_all()
        data[server_name] = entry
        try:
            atomic_write_text(store_path, json.dumps(data, ensure_ascii=False, indent=2))
            store_path.chmod(0o600)
        except OSError as e:
            log(f"OAuth 凭据写入失败: {e}", "ERROR", tag=_TAG)


def _load_entry(server_name: str) -> Dict[str, Any]:
    entry = _read_all().get(server_name)
    return dict(entry) if isinstance(entry, dict) else {}


def _entry_tokens(entry: Dict[str, Any]) -> Optional[OAuthToken]:
    raw = entry.get("tokens")
    if not isinstance(raw, dict):
        return None
    try:
        return OAuthToken.model_validate(raw)
    except Exception:
        return None


def _entry_near_expiry(entry: Dict[str, Any]) -> bool:
    """token 是否临期/已过期（无有效期信息按临期处理，交由刷新裁决）。"""
    expires_at = entry.get("expires_at")
    if not isinstance(expires_at, (int, float)):
        return True
    return time.time() >= float(expires_at) - _EXPIRY_SKEW


def _save_tokens(
        server_name: str,
        tokens,
        *,
        client_info: Optional[Dict[str, Any]] = None,
        server_meta: Optional[Dict[str, Any]] = None,
) -> None:
    """写入 token（保留既有 client_info / server_meta，缺省不触碰）。"""
    entry = _load_entry(server_name)
    entry["tokens"] = tokens.model_dump(mode="json")
    if client_info is not None:
        entry["client_info"] = client_info
    if server_meta is not None:
        entry["server_meta"] = server_meta
    entry["obtained_at"] = time.time()
    entry["expires_at"] = (
        time.time() + float(tokens.expires_in)
        if isinstance(tokens.expires_in, (int, float)) else None
    )
    _write_entry(server_name, entry)


def _clear_tokens(server_name: str, *, keep_client: bool = True) -> None:
    """清除凭据（刷新确定性失败时调用）：token 必删，客户端按需保留。

    keep_client=True（invalid_grant）：client 留作重授权种子；
    keep_client=False（invalid_client）：整体删除。
    """
    entry = _load_entry(server_name)
    if not entry:
        return
    entry.pop("tokens", None)
    entry.pop("expires_at", None)
    entry.pop("obtained_at", None)
    if not keep_client:
        entry.pop("client_info", None)
    _write_entry(server_name, entry)


def delete_credentials(server_name: str) -> bool:
    """删除指定 server 的 OAuth 凭据（tokens + 客户端 + 服务元数据）。"""
    store_path = Path(ConfigPaths.MCP_OAUTH_TOKENS)
    try:
        with _store_lock:
            data = _read_all()
            if server_name not in data:
                return False
            del data[server_name]
            atomic_write_text(store_path, json.dumps(data, ensure_ascii=False, indent=2))
        return True
    except (OSError, ValueError) as e:
        log(f"OAuth 凭据删除失败: {e}", "WARNING", tag=_TAG)
        return False


def credential_snapshot(server_name: str) -> Dict[str, Any]:
    """凭据快照（状态展示用）：是否存在 / 静态客户端 / 过期时间。"""
    entry = _load_entry(server_name)
    tokens = _entry_tokens(entry)
    expires_at = entry.get("expires_at")
    return {
        "authorized": tokens is not None,
        "has_refresh_token": bool(tokens and tokens.refresh_token),
        "static_client": bool(entry.get("client_info")),
        "expires_at": float(expires_at) if isinstance(expires_at, (int, float)) else None,
    }


# ==================================================================
# 待授权登记（AI 工具 / Web 界面读取授权链接）
# ==================================================================

_pending: Dict[str, Dict[str, Any]] = {}
_pending_lock = threading.Lock()


def pending_auth(server_name: str = "") -> Dict[str, Dict[str, Any]]:
    """当前待授权登记（server → {url, started_at, expires_at}）；过期项自动清理。"""
    now = time.time()
    with _pending_lock:
        stale = [k for k, v in _pending.items() if now - v["started_at"] > _PENDING_TTL]
        for k in stale:
            _pending.pop(k, None)
        if server_name:
            entry = _pending.get(server_name)
            return {server_name: dict(entry)} if entry else {}
        return {k: dict(v) for k, v in _pending.items()}


def _register_pending(server_name: str, url: str, ttl: float = _PENDING_TTL) -> None:
    with _pending_lock:
        _pending[server_name] = {
            "url": url, "started_at": time.time(),
            "expires_at": time.time() + ttl,
        }


def clear_pending_auth(server_name: str) -> None:
    with _pending_lock:
        _pending.pop(server_name, None)


# ==================================================================
# 回环回调服务（RFC 8252）
# ==================================================================

class LoopbackCallbackServer:
    """一次性 OAuth 回环回调服务（端口由内核分配，收到回调即结束）。

    先监听后取端口：绑定动作本身完成端口分配，消除「探测-关闭-再绑定」
    的竞态窗口。state 不匹配的回调回 400 并继续等待（并发事务/浏览器残留
    旧标签页都会打到本 listener，一个陌生 state 就终止会让正确回调永不到达）。
    """

    def __init__(self, path: str = "/callback") -> None:
        self._path = path
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._result: Optional[Dict[str, Optional[str]]] = None
        self._done = threading.Event()
        self._expected_state: Optional[str] = None
        self.port = 0

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.port}{self._path}"

    def start(self) -> None:
        """启动监听（守护线程，不阻塞调用方）。"""
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                from urllib.parse import parse_qs

                query = parse_qs(urlparse(self.path).query)
                code = (query.get("code") or [""])[0]
                state = (query.get("state") or [None])[0]
                error = (query.get("error") or [""])[0]
                if not error and not code:
                    error = "access_denied"
                if not error and (outer._expected_state is None
                                  or not secrets.compare_digest(state or "", outer._expected_state)):
                    # 陌生/未登记 state：并发事务或浏览器残留标签页，继续等正确回调
                    self.send_response(400)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.end_headers()
                    self.wfile.write("state mismatch".encode("utf-8"))
                    return
                outer._result = {"code": code or None, "error": error or None}
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                message = (
                    "授权失败，可关闭本页。" if error else "授权完成，可关闭本页返回。"
                )
                self.wfile.write(f"<html><body><p>{message}</p></body></html>".encode("utf-8"))
                outer._done.set()

            def log_message(self, *args: Any) -> None:
                pass  # 回调访问日志静默

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="mcp-oauth-callback", daemon=True,
        )
        self._thread.start()

    def expect_state(self, state: str) -> None:
        """登记本轮授权的 state（授权 URL 构造后、用户点击前调用）。"""
        self._expected_state = state

    async def wait_callback(self, timeout: float = _CALLBACK_TIMEOUT) -> str:
        """等待授权回调并返回授权码（state 已在回调线程校验）；超时/拒绝抛异常。"""
        loop = asyncio.get_running_loop()
        ok = await loop.run_in_executor(None, self._done.wait, timeout)
        if not ok:
            raise TimeoutError(f"OAuth 回调等待超时（{timeout:.0f}s）")
        result = self._result or {}
        if result.get("error"):
            raise PermissionError(f"OAuth 授权被拒绝: {result['error']}")
        if not result.get("code"):
            raise ValueError("OAuth 回调缺少授权码")
        return str(result["code"])

    def close(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


# ==================================================================
# 发现（RFC 9728 / RFC 8414）
# ==================================================================

def _origin(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), "", "", ""))


def _canonical_resource(url: str) -> str:
    """RFC 8707 规范 resource（去 fragment、scheme/host 小写）。"""
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, parsed.query, ""))


def _well_known_candidates(base_url: str, well_known: str) -> list:
    """well-known 候选 URL：路径插入形在前、根形在后（RFC 8414 §3.1）。"""
    parsed = urlsplit(base_url)
    path = parsed.path.rstrip("/")
    candidates = []
    if path:
        candidates.append(urlunsplit((parsed.scheme, parsed.netloc, f"/.well-known/{well_known}{path}", "", "")))
    candidates.append(urlunsplit((parsed.scheme, parsed.netloc, f"/.well-known/{well_known}", "", "")))
    return candidates


async def _get_json(http: httpx.AsyncClient, url: str) -> Optional[Dict[str, Any]]:
    """GET JSON 候选；404 或非对象 JSON 返回 None（候选链继续），5xx 抛临时错误。

    网关型服务器对一切路径回 200+业务错误 JSON/HTML——按「该候选不是
    元数据文档」处理而不是当故障：非 OAuth 服务器的发现链应在候选耗尽
    后得出「未声明 OAuth」的结论，绝不因此去发明端点。
    """
    response = await http.get(url, headers={"Accept": "application/json"})
    if response.status_code == 404:
        return None
    if response.status_code >= 500:
        raise OAuthTemporaryError(f"元数据请求失败 ({response.status_code}): {url}")
    if response.status_code != 200:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _issuer_matches(claimed: Any, base_url: str) -> bool:
    """RFC 8414 §3.3 issuer 校验（容忍尾斜杠差）。"""
    if not claimed:
        return False
    return str(claimed).rstrip("/") == base_url.rstrip("/")


async def _discover_server_meta(server_url: str) -> Optional[Dict[str, Any]]:
    """发现授权服务器元数据，返回标准化 server_meta；未声明 OAuth 返回 None。

    server_meta = {issuer, authorization_endpoint, token_endpoint,
    registration_endpoint, resource}。解析失败的候选文档按不可用处理
    （候选链继续）；issuer 校验不过的元数据整体拒绝（RFC 8414 §3.3），
    绝不把 code 发给另一个端点。**PRM 与 AS 元数据文档均未找到 → None**：
    非 OAuth 服务器的 401 是静态凭据问题，发明 /register 端点只会打出
    莫名其妙的注册错误（网关对一切路径回 200+业务 JSON）。
    """
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True) as http:
        # 1) 保护资源元数据（RFC 9728）：定位授权服务器 + resource（RFC 8707）
        auth_server = _origin(server_url)
        resource = _canonical_resource(server_url)
        prm_found = False
        for url in _well_known_candidates(server_url, "oauth-protected-resource"):
            data = await _get_json(http, url)
            if data is None:
                continue
            try:
                prm = ProtectedResourceMetadata.model_validate(data)
            except ValidationError:
                continue  # 无效文档按该候选不可用处理
            if prm.resource:
                prm_resource = str(prm.resource)
                parsed_prm = urlparse(prm_resource)
                parsed_server = urlparse(_origin(server_url))
                if (parsed_prm.scheme.lower(), parsed_prm.netloc.lower()) == \
                        (parsed_server.scheme.lower(), parsed_server.netloc.lower()):
                    resource = prm_resource
            if prm.authorization_servers:
                auth_server = str(prm.authorization_servers[0]).rstrip("/")
            prm_found = True
            break

        # 2) 授权服务器元数据（RFC 8414，OIDC 回落）：先 PRM 指定的 AS，
        #    再回落 server 同源根（两类候选都试）
        server_origin = _origin(server_url)
        bases = [auth_server]
        if auth_server != server_origin:
            bases.append(server_origin)
        metadata: Optional[OAuthMetadata] = None
        mismatched: Optional[str] = None
        for base in bases:
            for url in _well_known_candidates(base, "oauth-authorization-server") + \
                    _well_known_candidates(base, "openid-configuration"):
                data = await _get_json(http, url)
                if data is None:
                    continue
                try:
                    candidate = OAuthMetadata.model_validate(data)
                except ValidationError:
                    continue
                if _issuer_matches(candidate.issuer, base):
                    metadata = candidate
                    break
                mismatched = str(candidate.issuer)
            if metadata is not None:
                break

    if mismatched is not None and metadata is None:
        raise OAuthConfigurationError(
            f"授权服务器元数据 issuer 校验失败（文档声明 {mismatched}），拒绝使用其端点"
        )
    if metadata is None and not prm_found:
        return None  # 未声明 OAuth：PRM 与 AS/OIDC 元数据文档均不存在
    if metadata is not None:
        if metadata.response_types_supported is not None and "code" not in metadata.response_types_supported:
            raise OAuthConfigurationError("授权服务器不支持 authorization_code 流程")
        if metadata.code_challenge_methods_supported is not None \
                and "S256" not in metadata.code_challenge_methods_supported:
            raise OAuthConfigurationError("授权服务器不支持 PKCE S256")
        return {
            "issuer": str(metadata.issuer).rstrip("/"),
            "authorization_endpoint": str(metadata.authorization_endpoint or f"{auth_server}/authorize"),
            "token_endpoint": str(metadata.token_endpoint or f"{auth_server}/token"),
            "registration_endpoint": (
                str(metadata.registration_endpoint) if metadata.registration_endpoint
                else f"{auth_server}/register"
            ),
            "resource": resource,
        }
    # PRM 存在但 AS 元数据文档缺失（RFC 8414 遗留形态）：
    # authorize/token 按 AS 根推导，**不发明注册端点**（无文档支持 DCR 证据）
    return {
        "issuer": auth_server,
        "authorization_endpoint": f"{auth_server}/authorize",
        "token_endpoint": f"{auth_server}/token",
        "registration_endpoint": None,
        "resource": resource,
    }


# ==================================================================
# 交互授权事务（prepare 秒级 / wait_and_exchange 分钟级）
# ==================================================================

def _static_client(oauth_cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """静态配置的客户端（oauth.client_id；可选 client_secret 走 basic 认证）。"""
    client_id = str(oauth_cfg.get("client_id") or oauth_cfg.get("clientId") or "").strip()
    if not client_id:
        return None
    secret = str(oauth_cfg.get("client_secret") or "").strip()
    return {"client_id": client_id, **({"client_secret": secret} if secret else {})}


def _configured_scopes(oauth_cfg: Dict[str, Any]) -> str:
    return str(oauth_cfg.get("scopes") or oauth_cfg.get("scope") or "").strip()


def _union_scopes(*parts: str) -> str:
    """scope 并集（保序去重；空段忽略）。"""
    seen: list = []
    for part in parts:
        for scope in (part or "").split():
            if scope not in seen:
                seen.append(scope)
    return " ".join(seen)


def _apply_client_auth(data: Dict[str, str], headers: Dict[str, str],
                       client: Optional[Dict[str, Any]]) -> Tuple[Dict[str, str], Dict[str, str]]:
    """token 请求的客户端认证：有 secret 走 client_secret_basic，否则公共客户端。"""
    if client and client.get("client_secret"):
        credentials = f"{quote(client['client_id'], safe='')}:{quote(client['client_secret'], safe='')}"
        encoded = base64.b64encode(credentials.encode()).decode()
        headers["Authorization"] = f"Basic {encoded}"
    return data, headers


class AuthorizationSession:
    """一次完整的交互授权事务（发现→注册→授权链接→回调→换 token）。

    prepare 构造授权 URL 并登记待授权表（秒级）；wait_and_exchange 等待
    用户在浏览器完成授权并换 token（分钟级）。动态注册的客户端随 token
    一并持久化（刷新需要），但任何下一次交互授权都重新注册。
    """

    def __init__(self, server_name: str, server_url: str, oauth_cfg: Dict[str, Any]) -> None:
        self._server = server_name
        self._server_url = server_url
        self._oauth_cfg = oauth_cfg
        self._http: Optional[httpx.AsyncClient] = None
        self._callback: Optional[LoopbackCallbackServer] = None
        self._client: Optional[Dict[str, Any]] = None
        self._server_meta: Optional[Dict[str, Any]] = None
        self._pkce_verifier = ""
        self._state = ""
        self.authorize_url = ""

    @property
    def server_name(self) -> str:
        return self._server

    async def prepare(self, extra_scopes: str = "") -> str:
        """发现 + 客户端解析 + 授权 URL 构造 + 待授权登记。"""
        self._http = httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True)
        entry = _load_entry(self._server)
        self._server_meta = entry.get("server_meta")
        if not isinstance(self._server_meta, dict) or not self._server_meta.get("issuer"):
            self._server_meta = await _discover_server_meta(self._server_url)
        if self._server_meta is None:
            raise OAuthConfigurationError(
                "服务器未声明 OAuth 支持（无保护资源/授权服务器元数据文档）；"
                "401 多为静态凭据问题，请核对 url 中的 key 或 headers 配置"
            )
        meta = self._server_meta

        self._callback = LoopbackCallbackServer()
        self._callback.start()

        static = _static_client(self._oauth_cfg)
        if static is not None:
            self._client = static
        else:
            self._client = await self._register_client(meta)

        self._pkce_verifier = secrets.token_urlsafe(64)
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(self._pkce_verifier.encode()).digest()
        ).decode().rstrip("=")
        self._state = secrets.token_urlsafe(32)
        self._callback.expect_state(self._state)

        scopes = _union_scopes(_configured_scopes(self._oauth_cfg), extra_scopes)
        params: Dict[str, str] = {
            "response_type": "code",
            "client_id": self._client["client_id"],
            "redirect_uri": self._callback.redirect_uri,
            "state": self._state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if scopes:
            params["scope"] = scopes
        if meta.get("resource"):
            params["resource"] = str(meta["resource"])

        self.authorize_url = f"{meta['authorization_endpoint']}?{urlencode(params)}"
        _register_pending(self._server, self.authorize_url)
        log(f"MCP server '{self._server}' 需要 OAuth 授权: {self.authorize_url}", "WARNING", tag=_TAG)
        # 有桌面环境时直接拉起浏览器（headless 场景静默失败，链接已登记）
        try:
            webbrowser.open(self.authorize_url)
        except Exception:
            pass
        return self.authorize_url

    async def _register_client(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        """RFC 7591 动态客户端注册（每次授权重新注册，不复用旧注册）。"""
        body: Dict[str, Any] = {
            "client_name": "AnelfAgent",
            "redirect_uris": [self._callback.redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }
        registration_endpoint = meta.get("registration_endpoint")
        if not registration_endpoint:
            raise OAuthConfigurationError(
                "授权服务器未声明动态注册端点（不支持 DCR）；"
                "请在其控制台注册客户端后于配置 oauth.client_id 填写"
            )
        scopes = _configured_scopes(self._oauth_cfg)
        if scopes:
            body["scope"] = scopes
        response = await self._http.post(str(registration_endpoint), json=body)
        if response.status_code not in (200, 201):
            raise OAuthConfigurationError(
                f"动态客户端注册失败 ({response.status_code}): {response.text[:200]}"
            )
        data = response.json()
        client: Dict[str, Any] = {"client_id": str(data.get("client_id") or "")}
        if not client["client_id"]:
            # 200 但不是注册文档（网关业务错误 JSON 等）：该端点不是 DCR
            raise OAuthConfigurationError(
                f"动态注册响应缺少 client_id（响应: {response.text[:200]}）；"
                "该服务器可能不支持动态注册，需静态客户端时请在配置 oauth.client_id 填写"
            )
        if data.get("client_secret"):
            client["client_secret"] = str(data["client_secret"])
        return client

    async def wait_and_exchange(self):
        """等待回调并完成授权码换 token（成功即持久化并清理待授权登记）。"""

        assert self._callback is not None and self._client is not None
        try:
            code = await self._callback.wait_callback(_CALLBACK_TIMEOUT)
            tokens = await self._exchange_code(code)
        except Exception:
            clear_pending_auth(self._server)
            raise
        _save_tokens(
            self._server, tokens,
            client_info=self._client, server_meta=self._server_meta,
        )
        clear_pending_auth(self._server)
        log(f"OAuth token 已保存: {self._server}", tag=_TAG)
        return tokens

    async def _exchange_code(self, code: str) -> OAuthToken:
        meta = self._server_meta or {}
        data: Dict[str, str] = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._callback.redirect_uri,
            "client_id": self._client["client_id"],
            "code_verifier": self._pkce_verifier,
        }
        if meta.get("resource"):
            data["resource"] = str(meta["resource"])
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
        data, headers = _apply_client_auth(data, headers, self._client)
        response = await self._http.post(str(meta["token_endpoint"]), data=data, headers=headers)
        if response.status_code != 200:
            raise PermissionError(
                f"授权码换 token 失败 ({response.status_code}): {response.text[:200]}"
            )
        return OAuthToken.model_validate(response.json())

    async def aclose(self) -> None:
        """事务收尾：关回调服务与 HTTP 客户端（幂等；失败路径也清待授权登记）。"""
        if self._callback is not None:
            self._callback.close()
            self._callback = None
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        clear_pending_auth(self._server)


# ==================================================================
# 运行期提供者（httpx.Auth）
# ==================================================================

_BEARER_CHALLENGE_RE = re.compile(r"^\s*Bearer(\s|$)", re.IGNORECASE)
_SCOPE_PARAM_RE = re.compile(r'scope="([^"]*)"')


def _is_bearer_challenge(response: httpx.Response) -> bool:
    """401 是否携带 WWW-Authenticate: Bearer 挑战。

    只有服务器明确发 Bearer 挑战才说明支持 OAuth 授权码流程（RFC 9728）；
    带静态凭据（key 在 URL/headers）的服务器 401 是凭据问题，不是授权信号。
    """
    return bool(_BEARER_CHALLENGE_RE.match(response.headers.get(_WWW_AUTH_HEADER, "")))


def _www_authenticate_scopes(response: httpx.Response) -> str:
    """403 insufficient_scope 挑战里要求的 scope（WWW-Authenticate 的 scope 参数）。"""
    value = response.headers.get(_WWW_AUTH_HEADER, "")
    if "insufficient_scope" not in value.lower():
        return ""
    matched = _SCOPE_PARAM_RE.search(value)
    return matched.group(1) if matched else ""


class McpOAuthProvider(httpx.Auth):
    """挂在 MCP HTTP 传输上的 OAuth 凭据提供者。

    每实例一把 asyncio.Lock（单飞）：同 server 的并发请求在取凭据阶段
    串行化，共享同一次刷新/授权；请求本身不持锁。
    """

    def __init__(self, server_name: str, server_url: str, oauth_cfg: Dict[str, Any]) -> None:
        self._server = server_name
        self._server_url = server_url
        self._oauth_cfg = oauth_cfg if isinstance(oauth_cfg, dict) else {}
        self._lock = asyncio.Lock()
        # 最近一次成功刷新的时刻：AS 未回 expires_in 时凭据无法判期，
        # 以此在窗口内视作新鲜（单飞合并——并发的后来者直接用赢家结果）
        self._last_refresh_at = 0.0

    async def async_auth_flow(self, request: httpx.Request):
        token = await self._access_token()
        if token:
            request.headers["Authorization"] = f"Bearer {token}"
        response = yield request

        if response.status_code == 401 and _is_bearer_challenge(response):
            # 刷新优先：先试 refresh token，不可行/确定性失败才交互授权；
            # 无 Bearer 挑战的 401（静态凭据服务器）不做任何 OAuth 动作
            token = await self._access_token(force=True)
            if token:
                request.headers["Authorization"] = f"Bearer {token}"
            yield request
        elif response.status_code == 403 and _www_authenticate_scopes(response):
            # step-up：并集 scope 重新授权（refresh 无法扩权，RFC 6749 §6）
            token = await self._access_token(
                step_up=_union_scopes(
                    _configured_scopes(self._oauth_cfg),
                    self._entry_scope(), _www_authenticate_scopes(response),
                ),
            )
            if token:
                request.headers["Authorization"] = f"Bearer {token}"
            yield request

    def _entry_scope(self) -> str:
        tokens = _entry_tokens(_load_entry(self._server))
        return str(tokens.scope or "") if tokens is not None else ""

    async def _access_token(self, *, force: bool = False, step_up: str = "") -> str:
        """解析可用 access token（持锁单飞）。

        无凭据返回空串（首轮发裸请求）——只有服务器回 401+Bearer 挑战才进
        授权流（force=True 路径），绝不在请求发出前主动拉起授权事务；
        step_up 非空表示权限不足（403）：直接以并集 scope 重新授权。
        刷新不可行时用现值撑到 401（裁决权在服务器），绝不主动打断用户
        去点浏览器。
        """
        if step_up:
            async with self._lock:
                return (await self._run_authorization(extra_scopes=step_up)).access_token

        async with self._lock:
            entry = _load_entry(self._server)
            tokens = _entry_tokens(entry)
            if tokens is None:
                if not force:
                    return ""  # 无凭据首轮裸发；401 挑战路径（force）必须走授权
                return (await self._run_authorization()).access_token

            freshly_refreshed = time.time() - self._last_refresh_at < _EXPIRY_SKEW
            if not force and (not _entry_near_expiry(entry) or freshly_refreshed):
                return tokens.access_token

            meta = entry.get("server_meta") or {}
            if tokens.refresh_token and meta.get("token_endpoint"):
                try:
                    refreshed = await self._refresh(entry, tokens)
                except OAuthTemporaryError:
                    if force:
                        raise  # 现值刚被 401 拒绝，返回它必然二次 401
                    return tokens.access_token  # 临时故障 fail-soft：撑到 401
                if refreshed is not None:
                    self._last_refresh_at = time.time()
                    return refreshed.access_token
                # 刷新确定性失败（invalid_grant/invalid_client，凭据已清理）→ 交互授权
            elif not force:
                # 无刷新手段：现值撑到 401，由服务器裁决
                return tokens.access_token

            # 有凭据但已失效且无法恢复（force 路径或 step-up 后的裁决）
            return (await self._run_authorization()).access_token

    async def _refresh(self, entry: Dict[str, Any], tokens):
        """刷新 access token；确定性失败返回 None（凭据已清理）。"""
        meta = entry.get("server_meta") or {}
        client = entry.get("client_info") if isinstance(entry.get("client_info"), dict) else None
        data: Dict[str, str] = {"grant_type": "refresh_token"}
        if tokens.refresh_token:
            data["refresh_token"] = tokens.refresh_token
        if client and client.get("client_id"):
            data["client_id"] = str(client["client_id"])
        if meta.get("resource"):
            data["resource"] = str(meta["resource"])
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
        data, headers = _apply_client_auth(data, headers, client)

        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as http:
                response = await http.post(str(meta["token_endpoint"]), data=data, headers=headers)
        except httpx.HTTPError as exc:
            raise OAuthTemporaryError(f"token 刷新请求失败: {exc}") from exc

        if response.status_code == 200:
            fresh = OAuthToken.model_validate(response.json())
            if not fresh.refresh_token and tokens.refresh_token:
                # 部分 AS 不轮转 refresh token：沿用旧值
                fresh = fresh.model_copy(update={"refresh_token": tokens.refresh_token})
            _save_tokens(self._server, fresh)
            log(f"OAuth token 已刷新: {self._server}", tag=_TAG)
            return fresh
        if response.status_code in (400, 401):
            error = ""
            try:
                error = str(response.json().get("error") or "")
            except ValueError:
                pass
            if error == "invalid_grant":
                _clear_tokens(self._server, keep_client=True)
                log(f"OAuth refresh token 已失效，转入重新授权: {self._server}", "WARNING", tag=_TAG)
                return None
            if error in ("invalid_client", "unauthorized_client"):
                if _static_client(self._oauth_cfg) is not None:
                    # 静态客户端配置无效：交互授权复用同一客户端，无法自愈
                    raise OAuthConfigurationError(
                        f"静态 OAuth 客户端被拒绝（{error}），请检查 oauth.client_id/client_secret 配置"
                    )
                _clear_tokens(self._server, keep_client=False)
                return None
        raise OAuthTemporaryError(
            f"token 刷新失败 ({response.status_code}): {response.text[:200]}"
        )

    async def _run_authorization(self, extra_scopes: str = ""):
        """完整交互授权事务（内联在 401/step-up 路径上，bridge 会延长就绪等待）。"""
        session = AuthorizationSession(self._server, self._server_url, self._oauth_cfg)
        try:
            await session.prepare(extra_scopes=extra_scopes)
            return await session.wait_and_exchange()
        finally:
            await session.aclose()


# ==================================================================
# 提供者装配（transport 消费）
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


def make_oauth_provider(srv) -> Optional[McpOAuthProvider]:
    """为 HTTP 传输的 MCP server 构造 OAuth 提供者（不适用时返回 None）。"""
    if not oauth_eligible(srv):
        return None
    return McpOAuthProvider(srv.name, srv.url, getattr(srv, "oauth", None) or {})


# ==================================================================
# WebUI 主动授权入口（bridge 消费）
# ==================================================================

async def prepare_authorization(srv) -> AuthorizationSession:
    """发起交互授权事务的前半程（发现/注册/授权 URL），供 bridge 在专用循环执行。"""
    session = AuthorizationSession(srv.name, srv.url, getattr(srv, "oauth", None) or {})
    await session.prepare()
    return session


async def finish_authorization(session: AuthorizationSession) -> bool:
    """交互授权事务后半程：等回调换 token；返回是否取得新凭据。"""
    try:
        await session.wait_and_exchange()
    except Exception as exc:
        log(f"MCP server '{session.server_name}' OAuth 授权失败: {exc}", "ERROR", tag=_TAG)
        return False
    finally:
        await session.aclose()
    return True
