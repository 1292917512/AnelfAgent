"""MediaClient: async client for SiliconFlow non-chat APIs (ASR, TTS, Rerank, Video).

Uses httpx for HTTP requests. Each method is stateless and independently callable.
Constructed from LLMClientConfig by LLMManager.
"""

from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from core.log import log

_TIMEOUT = 120.0
_VIDEO_POLL_INTERVAL = 5.0
_VIDEO_MAX_POLL = 120


class MediaClient:
    """SiliconFlow media API client."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = _TIMEOUT,
        proxy_url: str = "",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._proxy_url = proxy_url

    def _http_client(self, timeout: Optional[float] = None) -> httpx.AsyncClient:
        """创建 httpx 异步客户端（若配置了代理则自动应用）。"""
        kw: Dict[str, Any] = {"timeout": timeout or self._timeout}
        if self._proxy_url:
            kw["proxy"] = self._proxy_url
        return httpx.AsyncClient(**kw)

    def _headers(self) -> Dict[str, str]:
        h: Dict[str, str] = {"Authorization": f"Bearer {self._api_key}"}
        return h

    # ------------------------------------------------------------------
    # ASR: audio -> text
    # ------------------------------------------------------------------

    async def transcribe(
        self,
        audio_data: bytes,
        *,
        model: str = "",
        file_name: str = "audio.mp3",
        mime_type: str = "",
    ) -> str:
        """Transcribe audio bytes to text via /audio/transcriptions."""
        if not mime_type:
            ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else "mp3"
            mime_map = {"mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg",
                        "oga": "audio/ogg", "opus": "audio/opus", "m4a": "audio/mp4",
                        "amr": "audio/amr", "flac": "audio/flac"}
            mime_type = mime_map.get(ext, "audio/mpeg")

        url = f"{self._base_url}/audio/transcriptions"
        async with self._http_client() as client:
            files = {"file": (file_name, audio_data, mime_type)}
            data = {"model": model}
            resp = await client.post(url, headers=self._headers(), files=files, data=data)
            resp.raise_for_status()
            result = resp.json()
            return result.get("text", "")

    async def transcribe_url(
        self,
        audio_url: str,
        *,
        model: str = "",
    ) -> str:
        """Download audio from URL then transcribe."""
        async with self._http_client(timeout=60.0) as client:
            resp = await client.get(audio_url, follow_redirects=True)
            resp.raise_for_status()
            audio_data = resp.content

        file_name = audio_url.rsplit("/", 1)[-1].split("?")[0] or "audio.mp3"
        return await self.transcribe(audio_data, model=model, file_name=file_name)

    # ------------------------------------------------------------------
    # TTS: text -> audio bytes
    # ------------------------------------------------------------------

    async def text_to_speech(
        self,
        text: str,
        *,
        model: str = "",
        voice: str = "",
        response_format: str = "mp3",
        references: Optional[List[Dict[str, str]]] = None,
    ) -> bytes:
        """Convert text to speech via /audio/speech. Returns audio bytes.

        ``voice`` 与 ``references`` 互斥：
        - voice: 预置音色，格式 ``{model}:{speaker}``，speaker 可选
          alex/anna/bella/benjamin/charles/claire/david/diana。
        - references: 声音克隆，传入参考音频列表，每项含 audio（URL 或 base64）和
          text（音频对应文字）。使用 references 时忽略 voice。
        """
        url = f"{self._base_url}/audio/speech"
        payload: Dict[str, Any] = {
            "model": model,
            "input": text,
            "response_format": response_format,
        }
        if references:
            payload["references"] = references
        else:
            if not voice:
                voice = f"{model}:alex" if model else "alex"
            payload["voice"] = voice
        async with self._http_client() as client:
            resp = await client.post(
                url, headers={**self._headers(), "Content-Type": "application/json"},
                json=payload,
            )
            resp.raise_for_status()
            return resp.content

    # ------------------------------------------------------------------
    # Rerank
    # ------------------------------------------------------------------

    async def rerank(
        self,
        query: str,
        documents: List[str],
        *,
        model: str = "",
        top_n: int = 5,
    ) -> List[Dict[str, Any]]:
        """Rerank documents by relevance. Returns [{index, relevance_score, document}]."""
        url = f"{self._base_url}/rerank"
        payload: Dict[str, Any] = {
            "model": model,
            "query": query,
            "documents": documents,
            "top_n": min(top_n, len(documents)),
        }
        async with self._http_client() as client:
            resp = await client.post(
                url, headers={**self._headers(), "Content-Type": "application/json"},
                json=payload,
            )
            resp.raise_for_status()
            result = resp.json()
            return result.get("results", [])

    # ------------------------------------------------------------------
    # Video generation (async with polling)
    # ------------------------------------------------------------------

    async def generate_video(
        self,
        prompt: str,
        *,
        model: str = "",
        image_url: str = "",
    ) -> str:
        """Submit video generation and poll until complete. Returns video URL."""
        url = f"{self._base_url}/videos/generations"
        payload: Dict[str, Any] = {"model": model, "prompt": prompt}
        if image_url:
            payload["image_url"] = image_url

        async with self._http_client() as client:
            resp = await client.post(
                url, headers={**self._headers(), "Content-Type": "application/json"},
                json=payload,
            )
            resp.raise_for_status()
            submit_result = resp.json()

        request_id = submit_result.get("requestId") or submit_result.get("id", "")
        if not request_id:
            video_url = self._extract_video_url(submit_result)
            if video_url:
                return video_url
            raise ValueError(f"No requestId in video submit response: {submit_result}")

        return await self._poll_video(request_id)

    async def _poll_video(self, request_id: str) -> str:
        """Poll video generation status until done."""
        url = f"{self._base_url}/videos/generations/{request_id}"
        for _ in range(_VIDEO_MAX_POLL):
            await asyncio.sleep(_VIDEO_POLL_INTERVAL)
            async with self._http_client(timeout=30.0) as client:
                resp = await client.get(url, headers=self._headers())
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()
                data = resp.json()

            status = data.get("status", "")
            if status in ("succeeded", "complete", "Succeed"):
                video_url = self._extract_video_url(data)
                if video_url:
                    return video_url
                raise ValueError(f"Video done but no URL: {data}")
            if status in ("failed", "error", "Failed"):
                raise RuntimeError(f"Video generation failed: {data}")
            log(f"video poll: status={status}", "DEBUG", tag="媒体")

        raise TimeoutError(f"Video generation timed out after {_VIDEO_MAX_POLL * _VIDEO_POLL_INTERVAL}s")

    @staticmethod
    def _extract_video_url(data: Dict[str, Any]) -> str:
        """Extract video URL from various response formats."""
        if "video" in data and isinstance(data["video"], dict):
            return data["video"].get("url", "")
        results = data.get("results", data.get("data", []))
        if isinstance(results, list):
            for item in results:
                if isinstance(item, dict) and "url" in item:
                    return item["url"]
        return data.get("url", "")

    # ------------------------------------------------------------------
    # Image generation / editing (SiliconFlow /images/generations API)
    # ------------------------------------------------------------------

    async def generate_image(
        self,
        prompt: str,
        *,
        model: str = "",
        image_size: str = "1024x1024",
        num_inference_steps: int = 20,
        cfg: Optional[float] = None,
    ) -> List[str]:
        """文生图：POST /images/generations，返回图片 URL 列表。

        SiliconFlow 参数：image_size、num_inference_steps、cfg（Qwen 系列）。
        """
        url = f"{self._base_url}/images/generations"
        payload: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "image_size": image_size,
            "num_inference_steps": num_inference_steps,
        }
        if cfg is not None:
            payload["cfg"] = cfg
        async with self._http_client() as client:
            resp = await client.post(
                url,
                headers={**self._headers(), "Content-Type": "application/json"},
                json=payload,
            )
            resp.raise_for_status()
        return self._extract_image_results(resp.json())

    async def edit_image(
        self,
        prompt: str,
        *,
        model: str = "",
        image_path: str = "",
        num_inference_steps: int = 20,
        cfg: float = 4.0,
    ) -> List[str]:
        """图片编辑：POST /images/generations，image 字段传 base64 或 URL。

        SiliconFlow Qwen-Image-Edit-2509 不支持 image_size 字段，
        image 可为 URL 或 "data:image/png;base64,XXX" 格式。
        """
        if image_path.startswith(("http://", "https://")):
            image_content: str = image_path
        else:
            with open(image_path, "rb") as f:
                raw = f.read()
            mime_type, _ = mimetypes.guess_type(os.path.basename(image_path))
            mime_type = mime_type or "image/png"
            image_content = f"data:{mime_type};base64,{base64.b64encode(raw).decode()}"

        url = f"{self._base_url}/images/generations"
        payload: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "image": image_content,
            "num_inference_steps": num_inference_steps,
            "cfg": cfg,
        }
        async with self._http_client() as client:
            resp = await client.post(
                url,
                headers={**self._headers(), "Content-Type": "application/json"},
                json=payload,
            )
            resp.raise_for_status()
        return self._extract_image_results(resp.json())

    @staticmethod
    def _extract_image_results(result: Dict[str, Any]) -> List[str]:
        """从响应中提取图片 URL，兼容 SiliconFlow 和 OpenAI 格式。

        SiliconFlow: {"images": [{"url": "..."}]}
        OpenAI:      {"data": [{"url": "...", "b64_json": "..."}]}
        """
        out: List[str] = []
        # SiliconFlow 格式优先
        for item in result.get("images", []):
            if isinstance(item, dict) and item.get("url"):
                out.append(item["url"])
        if out:
            return out
        # OpenAI 格式兜底
        for item in result.get("data", []):
            if item.get("url"):
                out.append(item["url"])
            elif item.get("b64_json"):
                out.append(f"data:image/png;base64,{item['b64_json']}")
        return out

    async def download_and_save_images(
        self,
        image_results: List[str],
        save_dir: str,
    ) -> List[str]:
        """下载图片 URL 或解码 base64，保存到 save_dir，返回相对路径列表。"""
        os.makedirs(save_dir, exist_ok=True)
        saved: List[str] = []
        for i, src in enumerate(image_results):
            ts = int(time.time() * 1000)
            if src.startswith("data:image/"):
                header, b64 = src.split(",", 1)
                img_bytes = base64.b64decode(b64)
                ext = ".png" if "png" in header else ".jpg"
            else:
                async with self._http_client(timeout=60.0) as client:
                    resp = await client.get(src, follow_redirects=True)
                    resp.raise_for_status()
                    img_bytes = resp.content
                    ct = resp.headers.get("content-type", "image/png")
                    ext = ".png" if "png" in ct else ".jpg"
            fname = f"gen_{ts}_{i}{ext}"
            fpath = os.path.join(save_dir, fname)
            with open(fpath, "wb") as f:
                f.write(img_bytes)
            rel = os.path.relpath(fpath, os.getcwd()).replace("\\", "/")
            saved.append(rel)
            log(f"图片已保存: {rel} ({len(img_bytes)} bytes)", "DEBUG", tag="媒体")
        return saved

    # ------------------------------------------------------------------
    # Utility: save audio to temp file
    # ------------------------------------------------------------------

    @staticmethod
    def save_audio_temp(audio_bytes: bytes, suffix: str = ".mp3") -> str:
        """Save audio bytes to a temp file, return path."""
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, prefix="anelf_tts_")
        tmp.write(audio_bytes)
        tmp.close()
        return tmp.name

    @staticmethod
    async def download_to_bytes(url: str) -> bytes:
        """Download URL content to bytes."""
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content
