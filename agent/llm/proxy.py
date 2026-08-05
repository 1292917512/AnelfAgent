"""代理支持：环境变量租约/上下文与可深拷贝的代理 HTTP 客户端。"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, Optional

import httpx

_PROXY_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
_SENTINEL = object()


class _ProxyEnvContext:
    """临时设置代理环境变量的上下文管理器，退出时还原原始值。"""

    def __init__(self, proxy_url: str) -> None:
        self._proxy_url = proxy_url
        self._saved: Dict[str, Any] = {}
        self._keys = (
            ("HTTP_PROXY", "HTTPS_PROXY")
            if os.name == "nt"
            else _PROXY_ENV_KEYS
        )

    def __enter__(self) -> "_ProxyEnvContext":
        for k in self._keys:
            self._saved[k] = os.environ.get(k, _SENTINEL)
            os.environ[k] = self._proxy_url
        return self

    def __exit__(self, *exc: Any) -> None:
        for k in self._keys:
            orig = self._saved.get(k, _SENTINEL)
            if orig is _SENTINEL:
                os.environ.pop(k, None)
            else:
                os.environ[k] = orig


class _ProxyEnvLease:
    """代理环境变量租约（读写分离）：同代理值的并发请求共享同一份环境变量，
    不同代理值的请求相互串行；最后一个请求离开时还原环境变量。

    替代全局 _ANTHROPIC_PROXY_LOCK：同一代理端点（绝大多数部署）的
    Anthropic 请求不再互斥，不同代理配置仍保证环境变量一致性。
    """

    _cond: Optional[asyncio.Condition] = None
    _current: Optional[str] = None
    _saved: Dict[str, Any] = {}
    _inflight: int = 0

    @classmethod
    def _get_cond(cls) -> asyncio.Condition:
        if cls._cond is None:
            cls._cond = asyncio.Condition()
        return cls._cond

    def __init__(self, proxy_url: str) -> None:
        self._url = proxy_url

    async def __aenter__(self) -> "_ProxyEnvLease":
        cond = self._get_cond()
        async with cond:
            while _ProxyEnvLease._current is not None and _ProxyEnvLease._current != self._url:
                await cond.wait()
            if _ProxyEnvLease._current is None:
                _ProxyEnvLease._saved = {
                    k: os.environ.get(k, _SENTINEL) for k in _PROXY_ENV_KEYS
                }
                for k in _PROXY_ENV_KEYS:
                    os.environ[k] = self._url
                _ProxyEnvLease._current = self._url
            _ProxyEnvLease._inflight += 1
        return self

    async def __aexit__(self, *exc: Any) -> None:
        cond = self._get_cond()
        async with cond:
            _ProxyEnvLease._inflight -= 1
            if _ProxyEnvLease._inflight <= 0:
                _ProxyEnvLease._inflight = 0
                # 最后一个请求离开：还原环境变量（httpx trust_env 会读取，
                # 残留会意外影响进程内其他 HTTP 调用）
                for k in _PROXY_ENV_KEYS:
                    orig = _ProxyEnvLease._saved.get(k, _SENTINEL)
                    if orig is _SENTINEL:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = orig
                _ProxyEnvLease._saved = {}
                _ProxyEnvLease._current = None
            cond.notify_all()


class _ProxyHttpClient(httpx.AsyncClient):
    """支持 deepcopy 的代理 HTTP 客户端（用于非 Anthropic Provider）。

    继承 httpx.AsyncClient 并覆写 __deepcopy__ 返回自身引用以共享连接池，
    规避 copy.deepcopy 时 _thread.RLock 无法序列化的问题。
    Anthropic 通道因 litellm 内部 JSON 序列化限制，改由环境变量传递代理。
    """

    def __init__(self, proxy_url: str) -> None:
        self._proxy_url = proxy_url
        super().__init__(proxy=proxy_url)

    def __deepcopy__(self, memo: dict) -> "_ProxyHttpClient":
        return self
