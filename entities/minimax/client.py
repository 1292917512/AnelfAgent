"""MiniMax HTTP API 客户端。

封装 MiniMax 平台的语音合成、图片生成、音色管理等 API，
使用 httpx 异步客户端，配置从实体级 config.json 加载。
"""

from __future__ import annotations

import base64
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


def update_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    """更新配置并持久化到 config.json（进程级缓存同步刷新），返回更新后的完整配置。"""
    global _config_cache
    current = dict(_load_config())
    current.update(updates)
    with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(current, f, ensure_ascii=False, indent=4)
    _config_cache = current
    log("MiniMax 配置已更新", tag="MiniMax")
    return dict(current)


def get_config(key: str, default: Any = "") -> Any:
    return _load_config().get(key, default)


class MiniMaxError(Exception):
    """MiniMax API 错误，携带 status_code、status_msg 与 Trace-Id（排查用）。"""

    def __init__(self, status_code: int, status_msg: str, trace_id: str = "") -> None:
        self.status_code = status_code
        self.status_msg = status_msg
        self.trace_id = trace_id
        msg = f"MiniMax API 错误 [{status_code}]: {status_msg}"
        if trace_id:
            msg += f" (Trace-Id: {trace_id})"
        super().__init__(msg)


def normalize_search_results(data: Dict[str, Any], query: str, max_results: int) -> Dict[str, Any]:
    """将 Coding Plan 搜索响应归一化为与 web_search 一致的结构。

    web 实体的搜索兜底链路与媒体库 minimax provider 共用此归一化。
    """
    organic = data.get("organic", [])[:max_results]
    refs = [
        {
            "title": item.get("title", ""),
            "url": item.get("link", ""),
            "snippet": item.get("snippet", ""),
            **({"date": item["date"]} if item.get("date") else {}),
        }
        for item in organic
    ]
    output: Dict[str, Any] = {
        "query": query,
        "sources": len(refs),
        "references": refs,
    }
    related = [r.get("query", "") for r in data.get("related_searches", []) if r.get("query")]
    if related:
        output["related_searches"] = related[:5]
    if not refs:
        output["hint"] = "无结果，建议更换关键词重写 query 后重试"
    return output


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
    def _check_resp(data: Dict[str, Any], trace_id: str = "") -> None:
        """检查 base_resp，非 0 则抛出 MiniMaxError。"""
        base = data.get("base_resp", {})
        code = base.get("status_code", 0)
        if code != 0:
            raise MiniMaxError(code, base.get("status_msg", "unknown error"), trace_id)

    # ------------------------------------------------------------------
    # 通用请求助手（新端点统一走此方法）
    # ------------------------------------------------------------------

    async def _post_json(
        self,
        path: str,
        payload: Dict[str, Any],
        *,
        base_url: str = "",
        headers: Optional[Dict[str, str]] = None,
        timeout: float = _TIMEOUT,
    ) -> Dict[str, Any]:
        """POST JSON 并检查 base_resp，返回响应数据。"""
        async with self._http_client(timeout) as client:
            resp = await client.post(
                f"{base_url or _BASE_URL}{path}",
                headers=headers or self._json_headers(),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            self._check_resp(data, resp.headers.get("Trace-Id", ""))
        return data

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

    # ------------------------------------------------------------------
    # Coding Plan: 网页搜索 / 图片理解（Token Plan 订阅配额，独立凭据）
    # ------------------------------------------------------------------

    @property
    def coding_plan_configured(self) -> bool:
        return bool(self._coding_plan_key())

    @staticmethod
    def _coding_plan_key() -> str:
        """Coding Plan 凭据：coding_plan_api_key → api_key → MINIMAX_API_KEY 环境变量。"""
        return (
            get_config("coding_plan_api_key")
            or get_config("api_key")
            or os.environ.get("MINIMAX_API_KEY", "")
        )

    @staticmethod
    def _coding_plan_host() -> str:
        """Coding Plan 站点：coding_plan_api_host → MINIMAX_API_HOST 环境变量 → 国内站。"""
        return (
            get_config("coding_plan_api_host")
            or os.environ.get("MINIMAX_API_HOST", "")
            or _BASE_URL
        )

    def _coding_plan_headers(self, api_key: str = "") -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key or self._coding_plan_key()}",
            "MM-API-Source": "AnelfAgent",
            "Content-Type": "application/json",
        }

    async def coding_plan_search(
        self, query: str, timeout: float = _TIMEOUT, api_key: str = ""
    ) -> Dict[str, Any]:
        """Coding Plan 网页搜索 POST /v1/coding_plan/search，返回完整响应数据。

        Args:
            query: 搜索关键词（3-5 个关键词效果最佳）
            timeout: 超时秒数（兜底链路等场景可传更短超时）
            api_key: 显式凭据覆盖（如 LLM 供应商回退凭据），留空走配置解析链
        """
        return await self._post_json(
            "/v1/coding_plan/search",
            {"q": query},
            base_url=self._coding_plan_host(),
            headers=self._coding_plan_headers(api_key),
            timeout=timeout,
        )

    async def coding_plan_understand_image(self, prompt: str, image_data_url: str) -> str:
        """Coding Plan 图片理解 POST /v1/coding_plan/vlm，返回分析文本。

        Args:
            prompt: 分析指令（描述要提取/理解的内容）
            image_data_url: 图片的 base64 Data URL（仅支持 JPEG/PNG/WebP）
        """
        data = await self._post_json(
            "/v1/coding_plan/vlm",
            {"prompt": prompt, "image_url": image_data_url},
            base_url=self._coding_plan_host(),
            headers=self._coding_plan_headers(),
        )
        content = data.get("content", "")
        if not content:
            raise MiniMaxError(-1, "响应中无分析内容")
        return content


# ==================================================================
# 模块级共享设施
# ==================================================================

_IMAGE_MAX_BYTES = 10 * 1024 * 1024  # VLM 图片上限 10MB
_IMAGE_MIME_BY_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


async def image_to_data_url(image_source: str) -> str:
    """将图片输入统一转为 base64 Data URL（Coding Plan VLM 入参格式）。

    支持 data URL 直通、http(s) URL 下载、本地绝对路径读取，
    自动剥离路径前的 @ 前缀，并校验 JPEG/PNG/WebP 格式与大小上限。
    调用方负责本地路径的预解析与沙箱校验。
    """
    src = image_source.strip()
    if src.startswith("@"):
        src = src[1:]
    if src.startswith("data:image/"):
        return src

    if src.startswith(("http://", "https://")):
        async with httpx.AsyncClient(timeout=60.0) as hc:
            try:
                resp = await hc.get(src, follow_redirects=True)
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                reason = "链接已过期或失效" if 400 <= code < 500 else "服务异常"
                raise ValueError(f"图片下载失败（HTTP {code}），{reason}") from e
            if len(resp.content) > _IMAGE_MAX_BYTES:
                raise ValueError("图片超过 10MB 上限")
            mime = resp.headers.get("content-type", "").split(";")[0].strip()
            if mime not in ("image/jpeg", "image/png", "image/webp"):
                raise ValueError(f"仅支持 JPEG/PNG/WebP 格式，收到: {mime or '未知'}")
            return f"data:{mime};base64,{base64.b64encode(resp.content).decode()}"

    resolved = src if os.path.isabs(src) else os.path.abspath(src)
    if not os.path.exists(resolved):
        raise FileNotFoundError(f"图片文件不存在: {image_source}")
    ext = os.path.splitext(resolved)[1].lower()
    mime = _IMAGE_MIME_BY_EXT.get(ext)
    if mime is None:
        raise ValueError(f"仅支持 JPEG/PNG/WebP 格式，收到: {ext or '无扩展名'}")
    with open(resolved, "rb") as f:
        raw = f.read()
    if len(raw) > _IMAGE_MAX_BYTES:
        raise ValueError("图片超过 10MB 上限")
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


def minimax_error_response(exc: Exception, action: str, hint: str = "") -> str:
    """MiniMax 错误归因为结构化工具错误 JSON：鉴权/限流/服务端错误精细分类。"""
    from entities._sdk import ErrorCause, error_from_exception, tool_error
    if isinstance(exc, MiniMaxError):
        if exc.status_code == 1004:
            return tool_error(
                f"{action}鉴权失败: {exc.status_msg}",
                cause=ErrorCause.CONFIG, retryable=False,
                hint="检查 entities/minimax/config.json 的凭据是否与站点（国内/国际）匹配",
            )
        return tool_error(
            f"{action}失败: {exc}",
            cause=ErrorCause.INTERNAL, retryable=False,
            hint=hint or None,
        )
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in (401, 403):
            return tool_error(
                f"{action}鉴权被拒绝 (HTTP {code})",
                cause=ErrorCause.CONFIG, retryable=False,
                hint="检查 entities/minimax/config.json 的凭据是否与站点（国内/国际）匹配",
            )
        if code == 429:
            return tool_error(
                f"{action}触发限流 (HTTP 429)",
                cause=ErrorCause.NETWORK, retryable=True,
                hint="稍后重试" + (f"，{hint}" if hint else ""),
            )
        if code >= 500:
            return tool_error(
                f"{action}服务端错误 (HTTP {code})",
                cause=ErrorCause.NETWORK, retryable=True,
                hint=hint or None,
            )
    return error_from_exception(exc, action=action, hint=hint or None)
