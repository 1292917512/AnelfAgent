"""小米云客户端 — miservice 封装（小爱音箱 MiNA 通道）。

登录态由 miservice 的 MiAccount 托管（token 文件缓存 + 401 自动重登）；
本层只做会话装配与异常收口。MiOT 全屋设备（spec 驱动映射）是文档化的
后续扩展点，v1 只覆盖小爱音箱。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp
from miservice import MiAccount, MiNAService

from core.log import log
from core.path import ConfigPaths


async def _reject_otp(method: str) -> str:
    """后台服务不读取终端验证码，显式报告需要账号验证。"""
    raise PermissionError(f"小米账号需要 {method} 验证，请先完成账号验证后重连")


class XiaomiClient:
    """小米云连接（小爱音箱设备列表 / 播放控制 / TTS 播报）。"""

    def __init__(self) -> None:
        self._session: Optional[aiohttp.ClientSession] = None
        self._account: Optional[MiAccount] = None
        self._mina: Optional[MiNAService] = None

    async def open(self, username: str, password: str) -> None:
        """建立会话并完成登录（token 缓存有效时直接复用）。"""
        await self.close()
        account_key = hashlib.sha256(username.strip().encode()).hexdigest()[:24]
        token_file = Path(ConfigPaths.MEMORY_DIR) / "smart_home" / "xiaomi" / f"{account_key}.json"
        token_file.parent.mkdir(parents=True, exist_ok=True)
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        self._account = MiAccount(self._session, username, password, str(token_file), otp_callback=_reject_otp)
        self._mina = MiNAService(self._account)

    async def close(self) -> None:
        """释放会话（幂等）。"""
        session, self._session = self._session, None
        self._account = None
        self._mina = None
        if session is not None:
            try:
                await session.close()
            except Exception as exc:
                log(f"小米云会话关闭失败: {exc}", "DEBUG")

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
        self._require_success(await self._require_mina().player_play(device_id))

    async def pause(self, device_id: str) -> None:
        self._require_success(await self._require_mina().player_pause(device_id))

    async def stop(self, device_id: str) -> None:
        self._require_success(await self._require_mina().player_stop(device_id))

    async def set_volume(self, device_id: str, volume: int) -> None:
        self._require_success(await self._require_mina().player_set_volume(device_id, volume))

    @staticmethod
    def _require_success(result: bool) -> None:
        if not result:
            raise RuntimeError("小米云未接受控制命令")

    async def speak(self, device_id: str, text: str) -> None:
        """TTS 播报（小米云 MiNA 通道）。"""
        ok = await self._require_mina().text_to_speech(device_id, text)
        if not ok:
            raise RuntimeError("小米云 TTS 播报失败")
