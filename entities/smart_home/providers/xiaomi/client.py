"""小米云客户端 — miservice 封装（小爱音箱 MiNA 通道）。

登录态由 miservice 的 MiAccount 托管（token 文件缓存 + 401 自动重登）；
本层只做会话装配与异常收口。MiOT 全屋设备（spec 驱动映射）是文档化的
后续扩展点，v1 只覆盖小爱音箱。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import aiohttp
from miservice import MiAccount, MiNAService

_TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".mi.token")


class XiaomiClient:
    """小米云连接（小爱音箱设备列表 / 播放控制 / TTS 播报）。"""

    def __init__(self) -> None:
        self._session: Optional[aiohttp.ClientSession] = None
        self._account: Optional[MiAccount] = None
        self._mina: Optional[MiNAService] = None

    async def open(self, username: str, password: str) -> None:
        """建立会话并完成登录（token 缓存有效时直接复用）。"""
        await self.close()
        self._session = aiohttp.ClientSession()
        self._account = MiAccount(self._session, username, password, _TOKEN_FILE)
        # 触发一次真实请求验证登录态（miservice 请求侧惰性登录 + 401 重登）
        self._mina = MiNAService(self._account)
        await self._mina.device_list()

    async def close(self) -> None:
        """释放会话（幂等）。"""
        session, self._session = self._session, None
        self._account = None
        self._mina = None
        if session is not None:
            try:
                await session.close()
            except Exception:
                pass

    def _require_mina(self) -> MiNAService:
        if self._mina is None:
            raise ConnectionError("小米云未连接")
        return self._mina

    async def speakers(self) -> List[Dict[str, Any]]:
        """小爱音箱列表（MiNA 设备清单原始条目）。"""
        devices = await self._require_mina().device_list()
        return devices or []

    async def speaker_status(self, device_id: str) -> Optional[Dict[str, Any]]:
        """单台音箱播放状态（status 1=播放中 2=暂停，volume 0-100）。"""
        return await self._require_mina().player_get_status(device_id)

    async def play(self, device_id: str) -> None:
        await self._require_mina().player_play(device_id)

    async def pause(self, device_id: str) -> None:
        await self._require_mina().player_pause(device_id)

    async def stop(self, device_id: str) -> None:
        await self._require_mina().player_stop(device_id)

    async def set_volume(self, device_id: str, volume: int) -> None:
        await self._require_mina().player_set_volume(device_id, volume)

    async def speak(self, device_id: str, text: str) -> None:
        """TTS 播报（小米云 MiNA 通道）。"""
        ok = await self._require_mina().text_to_speech(device_id, text)
        if not ok:
            raise RuntimeError("小米云 TTS 播报失败")
