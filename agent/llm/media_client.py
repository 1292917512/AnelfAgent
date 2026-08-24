"""MediaClient: async client for non-chat media APIs (ASR, TTS, Rerank, Video, Image).

Uses httpx for HTTP requests. Each method is stateless and independently callable.
Constructed from LLMClientConfig by LLMManager.
图片生成/编辑的协议差异由 agent.llm.image_adapters 中的适配器收口，
视频生成的协议差异由 agent.llm.video_adapters 中的适配器收口，
语音识别的协议差异由 agent.llm.asr_adapters 中的适配器收口。
"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import os
import time
from typing import Any, Dict, List, Optional

import httpx

from agent.llm.adapter_base import AdapterRequest
from agent.llm.asr_adapters import AsrAdapter, resolve_asr_adapter
from agent.llm.image_adapters import ImageGenAdapter, resolve_image_adapter
from agent.llm.music_adapters import MusicAdapter, MusicParams, MusicResult, resolve_music_adapter
from agent.llm.speech_adapters import (
    SpeechAdapter,
    SpeechParams,
    resolve_speech_adapter,
)
from agent.llm.video_adapters import (
    VideoGenAdapter,
    VideoGenParams,
    VideoTaskState,
    resolve_video_adapter,
)
from core.log import log

_TIMEOUT = 120.0
_VIDEO_POLL_INTERVAL = 5.0
_VIDEO_MAX_POLL = 120
_TTS_ASYNC_THRESHOLD = 3000
_TTS_ASYNC_POLL_INTERVAL = 3.0
_TTS_ASYNC_MAX_POLL = 100


class MediaClient:
    """媒体 API 客户端（语音/视频/图片等非聊天接口）。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = _TIMEOUT,
        proxy_url: str = "",
        media_protocol: str = "",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        # host 规则匹配的适配器依赖 netloc 非空；缺 scheme 会静默落到兜底适配器
        if self._base_url and not self._base_url.startswith(("http://", "https://")):
            raise ValueError(f"媒体端点 base_url 缺少 http(s):// 前缀: {base_url!r}")
        self._api_key = api_key
        self._timeout = timeout
        self._proxy_url = proxy_url
        self._media_protocol = media_protocol

    def _http_client(self, timeout: Optional[float] = None) -> httpx.AsyncClient:
        """创建 httpx 异步客户端（若配置了代理则自动应用）。"""
        kw: Dict[str, Any] = {"timeout": timeout or self._timeout}
        if self._proxy_url:
            kw["proxy"] = self._proxy_url
        return httpx.AsyncClient(**kw)

    def _headers(self) -> Dict[str, str]:
        h: Dict[str, str] = {"Authorization": f"Bearer {self._api_key}"}
        return h

    @staticmethod
    def _check_resp(resp: httpx.Response) -> None:
        """HTTP 状态校验：出错时提取响应体中的供应商错误信息再抛出。

        httpx 的 raise_for_status 只带状态行，会丢弃响应体里的真实错误原因
        （如 MiniMax 的 error.message / base_resp.status_msg），导致日志与
        返回给 AI 的错误都没有具体原因。
        """
        if resp.status_code < 400:
            return
        detail = ""
        try:
            body = resp.json()
        except Exception:
            body = None
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict):
                detail = err.get("message") or err.get("type") or ""
            elif isinstance(err, str):
                detail = err
            if not detail:
                base_resp = body.get("base_resp")
                if isinstance(base_resp, dict) and base_resp.get("status_code"):
                    detail = f"[{base_resp.get('status_code')}] {base_resp.get('status_msg', '')}"
            if not detail and isinstance(body.get("message"), str):
                detail = body["message"]
        if not detail:
            detail = resp.text[:200].strip()
        raise RuntimeError(f"HTTP {resp.status_code}: {detail or resp.reason_phrase}")

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
        """Transcribe audio bytes to text（协议差异由 AsrAdapter 收口）。"""
        if not mime_type:
            ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else "mp3"
            mime_map = {"mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg",
                        "oga": "audio/ogg", "opus": "audio/opus", "m4a": "audio/mp4",
                        "amr": "audio/amr", "flac": "audio/flac"}
            mime_type = mime_map.get(ext, "audio/mpeg")

        adapter = self._asr_adapter()
        req = adapter.build_transcribe_request(
            self._base_url,
            model=model,
            audio_data=audio_data,
            file_name=file_name,
            mime_type=mime_type,
        )
        headers = {**self._headers(), **(req.headers or {})}
        async with self._http_client() as client:
            if req.files:
                resp = await client.post(req.url, headers=headers, files=req.files,
                                         data=req.payload or {})
            else:
                resp = await client.post(req.url, headers=headers, json=req.payload,
                                         params=req.params)
            self._check_resp(resp)
            return adapter.extract_text(resp.json())

    def _asr_adapter(self) -> AsrAdapter:
        """解析当前凭据对应的 ASR 协议适配器。"""
        return resolve_asr_adapter(self._base_url, self._media_protocol)

    async def transcribe_url(
        self,
        audio_url: str,
        *,
        model: str = "",
    ) -> str:
        """Download audio from URL then transcribe."""
        async with self._http_client(timeout=60.0) as client:
            resp = await client.get(audio_url, follow_redirects=True)
            self._check_resp(resp)
            audio_data = resp.content

        file_name = audio_url.rsplit("/", 1)[-1].split("?")[0] or "audio.mp3"
        return await self.transcribe(audio_data, model=model, file_name=file_name)

    # ------------------------------------------------------------------
    # TTS: text -> audio bytes（协议差异由 SpeechAdapter 收口）
    # ------------------------------------------------------------------

    def _speech_adapter(self) -> SpeechAdapter:
        """解析当前凭据对应的语音协议适配器。"""
        return resolve_speech_adapter(self._base_url, self._media_protocol)

    async def text_to_speech(
        self,
        text: str,
        *,
        model: str = "",
        voice: str = "",
        response_format: str = "mp3",
        references: Optional[List[Dict[str, str]]] = None,
        emotion: str = "",
        speed: Optional[float] = None,
        vol: Optional[float] = None,
        pitch: Optional[int] = None,
        language_boost: str = "",
    ) -> bytes:
        """文字转语音，返回音频字节。

        长文本（超过 3000 字符）且协议支持时自动切换异步任务流程。
        ``voice``/``emotion``/``speed``/``vol``/``pitch``/``language_boost``
        由语音协议适配器按需取用；``references``（声音克隆参考音频）仅 OpenAI 风格协议支持。
        """
        adapter = self._speech_adapter()
        params = SpeechParams(
            model=model,
            text=text,
            voice=voice,
            response_format=response_format,
            emotion=emotion,
            speed=speed,
            vol=vol,
            pitch=pitch,
            language_boost=language_boost,
            references=references,
        )
        if adapter.supports_async and len(text) > _TTS_ASYNC_THRESHOLD:
            return await self._tts_async(adapter, params)

        req = adapter.build_tts_request(self._base_url, params)
        if adapter.binary_response:
            async with self._http_client() as client:
                resp = await client.post(
                    req.url,
                    headers={**self._headers(), **(req.headers or {})},
                    json=req.payload,
                )
                self._check_resp(resp)
                return resp.content
        return adapter.extract_audio(await self._send(req))

    async def _tts_async(self, adapter: SpeechAdapter, params: SpeechParams) -> bytes:
        """长文本异步语音合成：创建任务 → 轮询 → 文件检索 → 下载音频。"""
        result = await self._send(adapter.build_async_create_request(self._base_url, params))
        task_id = adapter.extract_async_task_id(result)
        if not task_id:
            raise ValueError(f"异步语音任务创建响应中无任务 ID: {result}")

        async with self._http_client(timeout=30.0) as client:
            for _ in range(_TTS_ASYNC_MAX_POLL):
                await asyncio.sleep(_TTS_ASYNC_POLL_INTERVAL)
                req = adapter.build_async_query_request(self._base_url, task_id)
                try:
                    resp = await client.get(
                        req.url,
                        headers={**self._headers(), **(req.headers or {})},
                        params=req.params,
                    )
                except httpx.TransportError as exc:
                    log(f"tts async poll transport error: {exc}", "DEBUG", tag="媒体")
                    continue
                if resp.status_code == 404 or resp.status_code == 429 or resp.status_code >= 500:
                    continue
                self._check_resp(resp)
                state = adapter.parse_async_query(resp.json())
                if state.status == "succeeded":
                    retrieve = await self._send(
                        adapter.build_retrieve_request(self._base_url, state.file_id)
                    )
                    download_url = adapter.extract_download_url(retrieve)
                    if not download_url:
                        raise ValueError(f"文件检索响应中无下载地址: {retrieve}")
                    return await self.download_to_bytes(download_url)
                if state.status == "failed":
                    raise RuntimeError(state.error or "语音合成任务失败")
                log(f"tts async poll: task={task_id} processing", "DEBUG", tag="媒体")

        raise TimeoutError(
            f"异步语音合成超时（{_TTS_ASYNC_MAX_POLL * _TTS_ASYNC_POLL_INTERVAL}s 内未完成）"
        )

    # ------------------------------------------------------------------
    # 音色管理（仅部分语音协议支持）
    # ------------------------------------------------------------------

    async def upload_voice_file(self, file_path: str, purpose: str) -> int:
        """上传音频文件（voice_clone / prompt_audio），返回 file_id。

        端点与响应解析由语音协议适配器收口；此处仅负责 multipart 发送。
        """
        adapter = self._speech_adapter()
        req = adapter.build_upload_request(self._base_url, purpose=purpose)
        async with self._http_client(timeout=180.0) as client:
            with open(file_path, "rb") as f:
                resp = await client.post(
                    req.url,
                    headers={**self._headers(), **(req.headers or {})},
                    files={"file": (os.path.basename(file_path), f)},
                    data=req.params,
                )
            self._check_resp(resp)
        return adapter.parse_upload_file_id(resp.json())

    async def voice_clone(
        self,
        file_path: str,
        *,
        voice_id: str,
        preview_text: str = "",
        model: str = "",
        need_noise_reduction: bool = False,
        need_volume_normalization: bool = False,
    ) -> Dict[str, Any]:
        """音色复刻：上传音频 → 提交复刻 → 返回结果（含可选试听地址）。"""
        adapter = self._speech_adapter()
        if not adapter.supports_voice_mgmt:
            raise NotImplementedError(f"语音协议 '{adapter.name}' 不支持音色复刻")
        file_id = await self.upload_voice_file(file_path, "voice_clone")
        req = adapter.build_voice_clone_request(
            self._base_url,
            file_id=file_id,
            voice_id=voice_id,
            preview_text=preview_text,
            model=model,
            need_noise_reduction=need_noise_reduction,
            need_volume_normalization=need_volume_normalization,
        )
        return adapter.parse_voice_clone(await self._send(req))

    async def voice_design(
        self,
        *,
        prompt: str,
        preview_text: str,
        voice_id: str = "",
    ) -> Dict[str, Any]:
        """音色设计：按文字描述生成音色，返回 voice_id 与试听音频字节。"""
        adapter = self._speech_adapter()
        req = adapter.build_voice_design_request(
            self._base_url, prompt=prompt, preview_text=preview_text, voice_id=voice_id,
        )
        return adapter.parse_voice_design(await self._send(req))

    async def list_voices(self, voice_type: str = "all") -> Dict[str, Any]:
        """查询音色列表（system / voice_cloning / voice_generation / all）。"""
        adapter = self._speech_adapter()
        req = adapter.build_get_voice_request(self._base_url, voice_type=voice_type)
        return adapter.parse_get_voice(await self._send(req))

    async def delete_voice(self, voice_id: str, voice_type: str = "voice_cloning") -> Dict[str, Any]:
        """删除复刻/设计的音色（不可恢复）。"""
        adapter = self._speech_adapter()
        req = adapter.build_delete_voice_request(
            self._base_url, voice_type=voice_type, voice_id=voice_id,
        )
        return adapter.parse_delete_voice(await self._send(req))

    # ------------------------------------------------------------------
    # 音乐生成（协议差异由 MusicAdapter 收口，仅部分供应商支持）
    # ------------------------------------------------------------------

    def _music_adapter(self) -> MusicAdapter:
        """解析当前凭据对应的音乐协议适配器。"""
        return resolve_music_adapter(self._base_url, self._media_protocol)

    async def generate_music(
        self,
        *,
        model: str = "",
        prompt: str = "",
        lyrics: str = "",
        is_instrumental: bool = False,
        audio_url: str = "",
        audio_base64: str = "",
        cover_feature_id: str = "",
    ) -> MusicResult:
        """音乐生成：文生音乐 / 带歌词歌曲 / 纯音乐 / 翻唱（cover 参数）。"""
        adapter = self._music_adapter()
        params = MusicParams(
            model=model,
            prompt=prompt,
            lyrics=lyrics,
            is_instrumental=is_instrumental,
            audio_url=audio_url,
            audio_base64=audio_base64,
            cover_feature_id=cover_feature_id,
        )
        result = await self._send(adapter.build_music_request(self._base_url, params))
        return adapter.extract_music(result)

    async def generate_lyrics(
        self,
        *,
        mode: str = "write_full_song",
        prompt: str = "",
        lyrics: str = "",
        title: str = "",
    ) -> Dict[str, Any]:
        """歌词生成：write_full_song 写整首 / edit 基于已有歌词修改。"""
        adapter = self._music_adapter()
        req = adapter.build_lyrics_request(
            self._base_url, mode=mode, prompt=prompt, lyrics=lyrics, title=title,
        )
        return adapter.parse_lyrics(await self._send(req))

    async def music_cover_preprocess(
        self,
        *,
        audio_url: str = "",
        audio_base64: str = "",
    ) -> Dict[str, Any]:
        """翻唱预处理：解析参考音频，返回 cover_feature_id 与提取的歌词/结构。"""
        adapter = self._music_adapter()
        req = adapter.build_cover_preprocess_request(
            self._base_url, audio_url=audio_url, audio_base64=audio_base64,
        )
        return adapter.parse_cover_preprocess(await self._send(req))

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
            resp = await client.post(url, headers=self._headers(), json=payload)
            self._check_resp(resp)
            result = resp.json()
            return result.get("results", [])

    # ------------------------------------------------------------------
    # Video generation (async with polling，协议差异由 VideoGenAdapter 收口)
    # ------------------------------------------------------------------

    def _video_adapter(self, model: str = "") -> VideoGenAdapter:
        """解析当前凭据对应的视频协议适配器。"""
        return resolve_video_adapter(self._base_url, self._media_protocol, model)

    async def _send(self, req: AdapterRequest) -> Dict[str, Any]:
        """按适配器请求描述发送 HTTP 请求并返回响应 JSON。"""
        headers = {**self._headers(), **(req.headers or {})}
        async with self._http_client() as client:
            if req.method == "GET":
                resp = await client.get(req.url, headers=headers, params=req.params)
            elif req.method == "DELETE":
                resp = await client.delete(req.url, headers=headers, params=req.params)
            else:
                resp = await client.post(req.url, headers=headers, json=req.payload)
            self._check_resp(resp)
        return resp.json() if resp.content else {}

    async def generate_video(
        self,
        prompt: str,
        *,
        model: str = "",
        image_url: str = "",
        first_frame_image: str = "",
        last_frame_image: str = "",
        subject_reference: Optional[List[str]] = None,
        duration: Optional[int] = None,
        resolution: str = "",
        ratio: str = "",
        prompt_optimizer: Optional[bool] = None,
        fast_pretreatment: Optional[bool] = None,
        aigc_watermark: Optional[bool] = None,
    ) -> str:
        """提交视频生成任务并轮询直至完成，返回视频 URL。

        ``image_url`` 为 ``first_frame_image`` 的兼容别名。各参数由视频协议
        适配器按需取用，当前协议不支持的字段会被忽略。
        """
        adapter = self._video_adapter(model)
        params = VideoGenParams(
            model=model,
            prompt=prompt,
            first_frame_image=first_frame_image or image_url,
            last_frame_image=last_frame_image,
            subject_reference=subject_reference or [],
            duration=duration,
            resolution=resolution,
            ratio=ratio,
            prompt_optimizer=prompt_optimizer,
            fast_pretreatment=fast_pretreatment,
            aigc_watermark=aigc_watermark,
        )
        submit_result = await self._send(adapter.build_create_request(self._base_url, params))

        task_id = adapter.extract_task_id(submit_result)
        if not task_id:
            video_url = adapter.extract_sync_url(submit_result)
            if video_url:
                return video_url
            raise ValueError(f"视频任务创建响应中无任务 ID: {submit_result}")

        state = await self._poll_video(adapter, task_id)
        return await self._resolve_state_url(adapter, state)

    async def _poll_video(self, adapter: VideoGenAdapter, task_id: str) -> VideoTaskState:
        """轮询视频生成状态直至成功或失败。"""
        async with self._http_client(timeout=30.0) as client:
            for _ in range(_VIDEO_MAX_POLL):
                await asyncio.sleep(_VIDEO_POLL_INTERVAL)
                req = adapter.build_query_request(self._base_url, task_id)
                try:
                    resp = await client.get(
                        req.url,
                        headers={**self._headers(), **(req.headers or {})},
                        params=req.params,
                    )
                except httpx.TransportError as exc:
                    log(f"video poll transport error: {exc}", "DEBUG", tag="媒体")
                    continue
                if resp.status_code == 404:
                    continue
                if resp.status_code == 429 or resp.status_code >= 500:
                    log(f"video poll transient HTTP {resp.status_code}", "DEBUG", tag="媒体")
                    continue
                self._check_resp(resp)
                state = adapter.parse_query_result(resp.json())
                if state.status == "succeeded":
                    return state
                if state.status == "failed":
                    raise RuntimeError(state.error or "视频生成失败")
                log(f"video poll: task={task_id} processing", "DEBUG", tag="媒体")

        raise TimeoutError(f"视频生成超时（{_VIDEO_MAX_POLL * _VIDEO_POLL_INTERVAL}s 内未完成）")

    async def _resolve_state_url(self, adapter: VideoGenAdapter, state: VideoTaskState) -> str:
        """从成功状态提取视频 URL；按 file_id 换取下载地址的协议在此追加检索步骤。"""
        if state.video_url:
            return state.video_url
        if state.file_id:
            result = await self._send(adapter.build_retrieve_request(self._base_url, state.file_id))
            download_url = adapter.extract_download_url(result)
            if download_url:
                return download_url
            raise ValueError(f"文件检索响应中无下载地址: {result}")
        raise ValueError(f"任务完成但未返回视频地址: {state.raw}")

    async def query_video_task(self, task_id: str, *, model: str = "") -> Dict[str, Any]:
        """查询一次视频任务状态，返回归一化结果（成功时含视频 URL）。"""
        adapter = self._video_adapter(model)
        result = await self._send(adapter.build_query_request(self._base_url, task_id))
        state = adapter.parse_query_result(result)
        out: Dict[str, Any] = {"status": state.status, "task_id": task_id}
        if state.status == "succeeded":
            out["video_url"] = await self._resolve_state_url(adapter, state)
        if state.status == "failed":
            out["error"] = state.error
        return out

    async def list_video_tasks(
        self,
        *,
        model: str = "",
        page_num: int = 1,
        page_size: int = 20,
        status: str = "",
    ) -> Dict[str, Any]:
        """分页查询视频任务列表（仅部分协议支持）。"""
        adapter = self._video_adapter(model)
        req = adapter.build_list_request(
            self._base_url, page_num=page_num, page_size=page_size, status=status,
        )
        return adapter.parse_list_result(await self._send(req))

    async def cancel_or_delete_video_task(self, task_id: str, *, model: str = "") -> Dict[str, Any]:
        """取消排队中的任务或删除已终结的任务记录（仅部分协议支持）。"""
        adapter = self._video_adapter(model)
        result = await self._send(adapter.build_delete_request(self._base_url, task_id))
        return adapter.parse_delete_result(result)

    # ------------------------------------------------------------------
    # Image generation / editing（协议差异由 ImageGenAdapter 收口）
    # ------------------------------------------------------------------

    def _image_adapter(self) -> ImageGenAdapter:
        """解析当前凭据对应的图片协议适配器。"""
        return resolve_image_adapter(self._base_url, self._media_protocol)

    async def generate_image(
        self,
        prompt: str,
        *,
        model: str = "",
        image_size: str = "1024x1024",
        num_inference_steps: int = 20,
        cfg: Optional[float] = None,
    ) -> List[str]:
        """文生图：由图片协议适配器构建请求并解析响应，返回图片 URL 列表。"""
        adapter = self._image_adapter()
        req = adapter.build_generate_request(
            self._base_url,
            model=model,
            prompt=prompt,
            image_size=image_size,
            num_inference_steps=num_inference_steps,
            cfg=cfg,
        )
        urls = adapter.extract_urls(await self._send(req))
        if not urls:
            raise RuntimeError(f"图片生成响应中无图片（模型 {model}）")
        return urls

    async def edit_image(
        self,
        prompt: str,
        *,
        model: str = "",
        image_path: str = "",
        num_inference_steps: int = 20,
        cfg: float = 4.0,
    ) -> List[str]:
        """图片编辑：image 可为 URL 或本地路径（转 "data:image/...;base64,XXX"）。"""
        if image_path.startswith(("http://", "https://")):
            image_content: str = image_path
        else:
            with open(image_path, "rb") as f:
                raw = f.read()
            mime_type, _ = mimetypes.guess_type(os.path.basename(image_path))
            mime_type = mime_type or "image/png"
            image_content = f"data:{mime_type};base64,{base64.b64encode(raw).decode()}"

        adapter = self._image_adapter()
        req = adapter.build_edit_request(
            self._base_url,
            model=model,
            prompt=prompt,
            image_content=image_content,
            num_inference_steps=num_inference_steps,
            cfg=cfg,
        )
        urls = adapter.extract_urls(await self._send(req))
        if not urls:
            raise RuntimeError(f"图片编辑响应中无图片（模型 {model}）")
        return urls

    async def download_and_save_images(
        self,
        image_results: List[str],
        save_dir: str,
    ) -> List[str]:
        """下载图片 URL 或解码 base64，保存到 save_dir，返回相对路径列表。"""
        os.makedirs(save_dir, exist_ok=True)
        saved: List[str] = []
        async with self._http_client(timeout=60.0) as client:
            for i, src in enumerate(image_results):
                ts = int(time.time() * 1000)
                if src.startswith("data:image/"):
                    header, b64 = src.split(",", 1)
                    img_bytes = base64.b64decode(b64)
                    ext = ".png" if "png" in header else ".jpg"
                else:
                    resp = await client.get(src, follow_redirects=True)
                    self._check_resp(resp)
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

    async def download_and_save_video(self, video_url: str, save_dir: str) -> str:
        """下载视频 URL 保存到 save_dir，返回相对路径。"""
        os.makedirs(save_dir, exist_ok=True)
        async with self._http_client(timeout=300.0) as client:
            resp = await client.get(video_url, follow_redirects=True)
            self._check_resp(resp)
            video_bytes = resp.content
        fname = f"gen_{int(time.time() * 1000)}.mp4"
        fpath = os.path.join(save_dir, fname)
        with open(fpath, "wb") as f:
            f.write(video_bytes)
        rel = os.path.relpath(fpath, os.getcwd()).replace("\\", "/")
        log(f"视频已保存: {rel} ({len(video_bytes)} bytes)", "DEBUG", tag="媒体")
        return rel

    # ------------------------------------------------------------------
    # Utility: save audio to workspace
    # ------------------------------------------------------------------
    @staticmethod
    def save_audio_file(audio_bytes: bytes, save_dir: str, suffix: str = ".mp3") -> str:
        """保存音频字节到 save_dir，返回相对路径。"""
        os.makedirs(save_dir, exist_ok=True)
        fpath = os.path.join(save_dir, f"gen_{int(time.time() * 1000)}{suffix}")
        with open(fpath, "wb") as f:
            f.write(audio_bytes)
        rel = os.path.relpath(fpath, os.getcwd()).replace("\\", "/")
        log(f"音频已保存: {rel} ({len(audio_bytes)} bytes)", "DEBUG", tag="媒体")
        return rel

    async def download_to_bytes(self, url: str) -> bytes:
        """Download URL content to bytes（走客户端代理/超时配置）。"""
        async with self._http_client(timeout=60.0) as client:
            resp = await client.get(url, follow_redirects=True)
            self._check_resp(resp)
            return resp.content
