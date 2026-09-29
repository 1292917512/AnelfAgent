"""守护进程 http_api 频道的轻量客户端 — ACP shim 与守护进程的唯一通道。

连接配置解析优先级：``ANELF_ACP_URL`` / ``ANELF_ACP_TOKEN`` 环境变量 >
项目根 ``channels/http_api/channel_config.json`` > 模块默认（回环 8091）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8091


class DaemonConfig:
    """守护进程 http_api 连接参数。"""

    __slots__ = ("base_url", "token")

    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    @property
    def auth_headers(self) -> Dict[str, str]:
        return {"x-api-token": self.token} if self.token else {}


def _project_root() -> Path:
    """经本文件位置定位项目根（acp/daemon.py → 上一级）。"""
    return Path(__file__).resolve().parents[1]


def resolve_config() -> DaemonConfig:
    """解析守护进程连接配置（env 优先，缺省回读频道配置文件）。"""
    base_url = os.environ.get("ANELF_ACP_URL", "")
    token = os.environ.get("ANELF_ACP_TOKEN", "")
    if not base_url or not token:
        host, port = _DEFAULT_HOST, _DEFAULT_PORT
        config_path = _project_root() / "channels" / "http_api" / "channel_config.json"
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            host = str(raw.get("host") or _DEFAULT_HOST)
            port = int(raw.get("port") or _DEFAULT_PORT)
            token = token or str(raw.get("api_token") or "")
        except (OSError, ValueError, TypeError):
            pass
        if not base_url:
            base_url = f"http://{host}:{port}"
    return DaemonConfig(base_url, token)


class DaemonClient:
    """http_api 频道的异步客户端（发送 + SSE 流消费）。"""

    def __init__(self, config: Optional[DaemonConfig] = None) -> None:
        self._config = config or resolve_config()

    @property
    def config(self) -> DaemonConfig:
        return self._config

    def _client(self, timeout: httpx.Timeout) -> httpx.AsyncClient:
        # trust_env=False：守护进程恒为本机/显式配置直连，绕开系统代理
        # （macOS 系统代理会把 127.0.0.1 请求路由进代理导致 502）
        return httpx.AsyncClient(
            base_url=self._config.base_url,
            headers=self._config.auth_headers,
            timeout=timeout,
            trust_env=False,
        )

    async def health(self) -> Dict[str, Any]:
        """健康探测（不可达抛 httpx 异常，由调用方归因）。"""
        async with self._client(httpx.Timeout(10.0)) as client:
            resp = await client.get("/health")
            resp.raise_for_status()
            return {"url": self._config.base_url}

    async def send_message(
        self,
        user_id: str,
        text: str,
        images: Optional[List[Dict[str, str]]] = None,
    ) -> None:
        """async 模式投递消息（立即返回，回复经流式帧投递）。"""
        payload: Dict[str, Any] = {
            "message": text,
            "user_id": user_id,
            "user_name": user_id,
            "async_mode": True,
        }
        if images:
            payload["images"] = images
        async with self._client(httpx.Timeout(30.0)) as client:
            resp = await client.post("/api/chat", json=payload)
            resp.raise_for_status()

    async def stream_frames(self, user_id: str) -> AsyncIterator[Dict[str, Any]]:
        """订阅该 user 的 SSE 流式帧（delta / tool_call / reply / turn_end）。

        连接建立后先 yield ``{"type": "open"}`` 哨兵帧（供调用方确认订阅
        通道就绪后再投递消息），随后逐行解析 SSE；连接断开正常结束迭代。
        读超时 120s——服务端 30s ping 保活，超时视为链路死亡。
        """
        timeout = httpx.Timeout(30.0, read=120.0)
        async with self._client(timeout) as client:
            async with client.stream(
                "GET", "/api/chat/stream", params={"user_id": user_id}
            ) as resp:
                resp.raise_for_status()
                yield {"type": "open"}
                event = "message"
                async for line in resp.aiter_lines():
                    if line.startswith("event:"):
                        event = line[len("event:"):].strip()
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if not data:
                        continue
                    if event == "ping":
                        continue
                    try:
                        frame = json.loads(data)
                    except ValueError:
                        continue
                    if isinstance(frame, dict):
                        yield frame
