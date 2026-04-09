"""MiniMax HTTP API 客户端。

封装 MiniMax 平台的语音合成、图片生成、音色管理等 API，
使用 httpx 异步客户端，配置从实体级 config.json 加载。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import httpx

from core.log import log

_BASE_URL = "https://api.minimaxi.com"
_TIMEOUT = 120.0
_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")

_config_cache: Optional[Dict[str, Any]] = None


def _load_config() -> Dict[str, Any]:
    """加载配置文件（进程级缓存）。"""
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    try:
        with open(_CONFIG_FILE, encoding="utf-8") as f:
            _config_cache = json.load(f)
    except FileNotFoundError:
        _config_cache = {}
        log("MiniMax 配置文件不存在，请复制 config.example.json 为 config.json 并填写 api_key", "WARNING", tag="MiniMax")
    except Exception as e:
        _config_cache = {}
        log(f"MiniMax 配置加载失败: {e}", "ERROR", tag="MiniMax")
    return _config_cache


def reload_config() -> None:
    """强制重新加载配置（热更新场景）。"""
    global _config_cache
    _config_cache = None
    _load_config()


def get_config(key: str, default: Any = "") -> Any:
    return _load_config().get(key, default)


class MiniMaxError(Exception):
    """MiniMax API 错误，携带 status_code 和 status_msg。"""

    def __init__(self, status_code: int, status_msg: str) -> None:
        self.status_code = status_code
        self.status_msg = status_msg
        super().__init__(f"MiniMax API 错误 [{status_code}]: {status_msg}")


class MiniMaxClient:
    """MiniMax 平台 API 客户端。"""

    def __init__(self, api_key: str = "", proxy_url: str = "") -> None:
        self._api_key = api_key or get_config("api_key")
        self._proxy_url = proxy_url or get_config("proxy")

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def _http_client(self, timeout: float = _TIMEOUT) -> httpx.AsyncClient:
        kw: Dict[str, Any] = {"timeout": timeout}
        if self._proxy_url:
            kw["proxy"] = self._proxy_url
        return httpx.AsyncClient(**kw)

    def _auth_headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def _json_headers(self) -> Dict[str, str]:
        return {
            **self._auth_headers(),
            "Content-Type": "application/json",
        }

    @staticmethod
    def _check_resp(data: Dict[str, Any]) -> None:
        """检查 base_resp，非 0 则抛出 MiniMaxError。"""
        base = data.get("base_resp", {})
        code = base.get("status_code", 0)
        if code != 0:
            raise MiniMaxError(code, base.get("status_msg", "unknown error"))

    # ------------------------------------------------------------------
    # TTS: 同步语音合成 POST /v1/t2a_v2
    # ------------------------------------------------------------------

    async def text_to_speech(
        self,
        text: str,
        *,
        model: str = "",
        voice_id: str = "",
        speed: float = 1.0,
        vol: float = 1.0,
        pitch: int = 0,
        emotion: str = "",
        audio_format: str = "mp3",
        sample_rate: int = 32000,
        language_boost: str = "",
    ) -> bytes:
        """同步语音合成，返回音频字节。

        Args:
            text: 待合成文本，上限 10000 字符
            model: 模型版本，默认从配置读取
            voice_id: 音色 ID，默认从配置读取
            speed: 语速 [0.5, 2]
            vol: 音量 (0, 10]
            pitch: 语调 [-12, 12]
            emotion: 情绪控制 happy/sad/angry/fearful/disgusted/surprised/calm
            audio_format: 音频格式 mp3/pcm/flac/wav
            sample_rate: 采样率
            language_boost: 语种增强 Chinese/English/auto 等
        """
        model = model or get_config("default_tts_model", "speech-2.8-hd")
        voice_id = voice_id or get_config("default_voice_id", "male-qn-qingse")

        voice_setting: Dict[str, Any] = {
            "voice_id": voice_id,
            "speed": speed,
            "vol": vol,
            "pitch": pitch,
        }
        if emotion:
            voice_setting["emotion"] = emotion

        payload: Dict[str, Any] = {
            "model": model,
            "text": text,
            "stream": False,
            "voice_setting": voice_setting,
            "audio_setting": {
                "format": audio_format,
                "sample_rate": sample_rate,
            },
        }
        if language_boost:
            payload["language_boost"] = language_boost

        async with self._http_client() as client:
            resp = await client.post(
                f"{_BASE_URL}/v1/t2a_v2",
                headers=self._json_headers(),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        self._check_resp(data)
        audio_hex = data.get("data", {}).get("audio", "")
        if not audio_hex:
            raise MiniMaxError(-1, "响应中无音频数据")
        return bytes.fromhex(audio_hex)

    # ------------------------------------------------------------------
    # Image Generation: POST /v1/image_generation
    # ------------------------------------------------------------------

    async def generate_image(
        self,
        prompt: str,
        *,
        model: str = "",
        aspect_ratio: str = "1:1",
        n: int = 1,
        response_format: str = "url",
        prompt_optimizer: bool = False,
    ) -> List[str]:
        """文生图，返回图片 URL 或 base64 列表。

        Args:
            prompt: 图片描述，上限 1500 字符
            model: 模型 image-01 或 image-01-live
            aspect_ratio: 宽高比 1:1/16:9/4:3/3:2/2:3/3:4/9:16/21:9
            n: 生成数量 [1, 9]
            response_format: url 或 base64
            prompt_optimizer: 是否自动优化 prompt
        """
        model = model or get_config("default_image_model", "image-01")
        payload: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "n": n,
            "response_format": response_format,
            "prompt_optimizer": prompt_optimizer,
        }
        async with self._http_client() as client:
            resp = await client.post(
                f"{_BASE_URL}/v1/image_generation",
                headers=self._json_headers(),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        self._check_resp(data)
        img_data = data.get("data", {})
        if response_format == "url":
            return img_data.get("image_urls", [])
        return [f"data:image/png;base64,{b}" for b in img_data.get("image_base64", [])]

    # ------------------------------------------------------------------
    # Image-to-Image: POST /v1/image_generation + subject_reference
    # ------------------------------------------------------------------

    async def image_to_image(
        self,
        prompt: str,
        reference_image: str,
        *,
        model: str = "",
        aspect_ratio: str = "1:1",
        n: int = 1,
    ) -> List[str]:
        """图生图（人物参考），返回图片 URL 列表。

        Args:
            prompt: 图片描述
            reference_image: 参考图的 URL 或 base64 Data URL
            model: 模型 image-01 或 image-01-live
            aspect_ratio: 宽高比
            n: 生成数量
        """
        model = model or get_config("default_image_model", "image-01")
        payload: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "n": n,
            "response_format": "url",
            "subject_reference": [
                {"type": "character", "image_file": reference_image},
            ],
        }
        async with self._http_client() as client:
            resp = await client.post(
                f"{_BASE_URL}/v1/image_generation",
                headers=self._json_headers(),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        self._check_resp(data)
        return data.get("data", {}).get("image_urls", [])

    # ------------------------------------------------------------------
    # File Upload: POST /v1/files/upload (multipart/form-data)
    # ------------------------------------------------------------------

    async def upload_file(
        self,
        file_data: bytes,
        filename: str,
        purpose: str = "voice_clone",
    ) -> int:
        """上传文件，返回 file_id。

        Args:
            file_data: 文件字节内容
            filename: 文件名（含扩展名）
            purpose: 用途 voice_clone 或 prompt_audio
        """
        async with self._http_client() as client:
            resp = await client.post(
                f"{_BASE_URL}/v1/files/upload",
                headers=self._auth_headers(),
                files={"file": (filename, file_data)},
                data={"purpose": purpose},
            )
            resp.raise_for_status()
            data = resp.json()

        base = data.get("base_resp", {})
        if base.get("status_code", 0) != 0:
            raise MiniMaxError(base["status_code"], base.get("status_msg", "upload failed"))
        return data.get("file", {}).get("file_id", 0)

    # ------------------------------------------------------------------
    # Voice Clone: POST /v1/voice_clone
    # ------------------------------------------------------------------

    async def voice_clone(
        self,
        file_id: int,
        voice_id: str,
        *,
        preview_text: str = "",
        preview_model: str = "",
        language_boost: str = "",
        need_noise_reduction: bool = False,
    ) -> Dict[str, Any]:
        """音色快速复刻。

        Args:
            file_id: 通过 upload_file 获得的文件 ID
            voice_id: 自定义音色 ID（8-256字符，字母开头）
            preview_text: 试听文本（可选）
            preview_model: 试听模型（提供 preview_text 时必填）
            language_boost: 语种增强
            need_noise_reduction: 是否降噪
        """
        payload: Dict[str, Any] = {
            "file_id": file_id,
            "voice_id": voice_id,
            "need_noise_reduction": need_noise_reduction,
        }
        if preview_text:
            payload["text"] = preview_text
            payload["model"] = preview_model or get_config("default_tts_model", "speech-2.8-hd")
        if language_boost:
            payload["language_boost"] = language_boost

        async with self._http_client() as client:
            resp = await client.post(
                f"{_BASE_URL}/v1/voice_clone",
                headers=self._json_headers(),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        self._check_resp(data)
        return {
            "voice_id": voice_id,
            "demo_audio": data.get("demo_audio", ""),
            "input_sensitive": data.get("input_sensitive"),
        }

    # ------------------------------------------------------------------
    # Voice Design: POST /v1/voice_design
    # ------------------------------------------------------------------

    async def voice_design(
        self,
        prompt: str,
        preview_text: str,
        *,
        voice_id: str = "",
    ) -> Dict[str, Any]:
        """文生音色：用文字描述生成新音色。

        Args:
            prompt: 音色描述
            preview_text: 试听文本（上限 500 字符）
            voice_id: 可选自定义 voice_id
        """
        payload: Dict[str, Any] = {
            "prompt": prompt,
            "preview_text": preview_text,
        }
        if voice_id:
            payload["voice_id"] = voice_id

        async with self._http_client() as client:
            resp = await client.post(
                f"{_BASE_URL}/v1/voice_design",
                headers=self._json_headers(),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        self._check_resp(data)
        result: Dict[str, Any] = {"voice_id": data.get("voice_id", "")}
        trial_hex = data.get("trial_audio", "")
        if trial_hex:
            result["trial_audio_size"] = len(trial_hex) // 2
        return result

    # ------------------------------------------------------------------
    # Get Voices: POST /v1/get_voice
    # ------------------------------------------------------------------

    async def get_voices(
        self,
        voice_type: str = "all",
    ) -> Dict[str, Any]:
        """查询可用音色列表。

        Args:
            voice_type: system/voice_cloning/voice_generation/all
        """
        async with self._http_client() as client:
            resp = await client.post(
                f"{_BASE_URL}/v1/get_voice",
                headers=self._json_headers(),
                json={"voice_type": voice_type},
            )
            resp.raise_for_status()
            data = resp.json()

        self._check_resp(data)
        result: Dict[str, Any] = {}
        if "system_voice" in data:
            result["system_voice"] = data["system_voice"]
        if "voice_cloning" in data:
            result["voice_cloning"] = data["voice_cloning"]
        if "voice_generation" in data:
            result["voice_generation"] = data["voice_generation"]
        return result

    # ------------------------------------------------------------------
    # Delete Voice: POST /v1/delete_voice
    # ------------------------------------------------------------------

    async def delete_voice(
        self,
        voice_id: str,
        voice_type: str = "voice_cloning",
    ) -> Dict[str, Any]:
        """删除指定音色。

        Args:
            voice_id: 要删除的音色 ID
            voice_type: voice_cloning 或 voice_generation
        """
        async with self._http_client() as client:
            resp = await client.post(
                f"{_BASE_URL}/v1/delete_voice",
                headers=self._json_headers(),
                json={"voice_type": voice_type, "voice_id": voice_id},
            )
            resp.raise_for_status()
            data = resp.json()

        self._check_resp(data)
        return {
            "voice_id": data.get("voice_id", voice_id),
            "created_time": data.get("created_time", ""),
        }
