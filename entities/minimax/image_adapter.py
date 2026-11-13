"""MiniMax 图片协议组件：H3 文生图/多图编辑与 image-01 主体参考兼容。

模型可见：统一图片工具传入有序参考图和选项，结果含逐图诊断与用量。
Token：只增加调用参数与产物说明，不发起额外主模型推理。
缓存：独立媒体请求，结果仅进入动态工具区，不修改主会话前缀。
"""
from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any

from entities._sdk import (
    AdapterRequest,
    ImageGenAdapter,
    ImageGenParams,
    ImageResults,
    MediaOutcomeUnknown,
    OpenAIImagesAdapter,
    check_base_resp,
    host_root,
)


class MiniMaxImagesAdapter(ImageGenAdapter):
    """按模型名选择协议；同一供应商可同时配置 H3 与旧版图片模型。"""

    name = "minimax"
    reference_schemes = (*ImageGenAdapter.reference_schemes, "mm_file://")

    @staticmethod
    def _aspect_ratio(size: str) -> str:
        """保留旧版尺寸到最近画幅比的映射。"""
        candidates = {"21:9": 21 / 9, "16:9": 16 / 9, "4:3": 4 / 3, "3:2": 3 / 2,
                      "1:1": 1.0, "2:3": 2 / 3, "3:4": 3 / 4, "9:16": 9 / 16}
        if size in candidates:
            return size
        try:
            w, h = size.lower().split("x", 1)
            ratio = int(w) / int(h)
        except (ValueError, ZeroDivisionError):
            return "1:1"
        return min(candidates, key=lambda key: abs(candidates[key] - ratio))

    def build_request(self, base_url: str, params: ImageGenParams) -> AdapterRequest:
        if params.model.lower().startswith("minimax-h3-image"):
            payload = self._h3_payload(params)
            path = "/v1/images/edits" if params.images else "/v1/images/generations"
        else:
            payload = self._legacy_payload(params)
            path = "/v1/image_generation"
        if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > 64 * 1024 * 1024:
            raise ValueError("图片请求超过 64 MiB，请使用公网 URL 或已上传文件引用")
        return AdapterRequest(url=f"{host_root(base_url)}{path}", payload=payload)

    @staticmethod
    def _h3_payload(params: ImageGenParams) -> dict[str, Any]:
        allowed = {"aspect_ratio", "area", "output_format", "response_format", "output_compression",
                   "aigc_watermark", "disable_prompt_enhanced", "stream"}
        if unknown := params.options.keys() - allowed:
            raise ValueError(f"H3 图片协议不支持参数: {', '.join(sorted(unknown))}")
        if params.n > 10 or len(params.images) > 16:
            raise ValueError("H3 最多生成 10 张图片，最多接受 16 张参考图")
        if len(params.prompt.strip()) > 32000:
            raise ValueError("H3 图片提示词最多 32000 字符")
        payload: dict[str, Any] = {"model": params.model, "prompt": params.prompt, "n": params.n,
                                   "response_format": "url", **params.options}
        size = params.image_size or "auto"
        if ":" in size:
            if "aspect_ratio" in payload and payload["aspect_ratio"] != size:
                raise ValueError("image_size 与 aspect_ratio 冲突")
            payload["aspect_ratio"], size = size, "auto"
        payload["size"] = size
        if params.images:
            payload["images"] = [{"image_url": src} for src in params.images]
            if "disable_prompt_enhanced" in payload:
                raise ValueError("H3 图片编辑始终开启 Prompt 增强，不接受 disable_prompt_enhanced")
        for key in ("stream", "aigc_watermark", "disable_prompt_enhanced"):
            if key in payload and type(payload[key]) is not bool:
                raise ValueError(f"{key} 必须为布尔值")
        for key, choices in {
            "area": ("1K", "2K", "4K"), "output_format": ("png", "jpeg", "jpg", "webp"),
            "response_format": ("url", "b64_json"),
        }.items():
            if key in payload and payload[key] not in choices:
                raise ValueError(f"{key} 须为 {choices}")
        quality = payload.get("output_compression", 95)
        if type(quality) is not int or not 0 <= quality <= 100:
            raise ValueError("output_compression 必须为 0~100 的整数")
        if size != "auto":
            if "aspect_ratio" in payload or "area" in payload:
                raise ValueError("指定像素尺寸时不能同时指定 aspect_ratio 或 area")
            match = re.fullmatch(r"([1-9]\d*)x([1-9]\d*)", size)
            if match is None:
                raise ValueError("图片尺寸须为 auto、比例或 宽x高")
            width, height = map(int, match.groups())
            area = width * height
            if not 655360 <= area <= 20000000 or not 1 / 3 <= width / height <= 3:
                raise ValueError("H3 图片面积须在 655360~20000000，宽高比须在 1:3~3:1")
        else:
            ratio = payload.get("aspect_ratio", "auto")
            if ratio != "auto":
                match = re.fullmatch(r"([1-9]\d*):([1-9]\d*)", str(ratio))
                if match is None:
                    raise ValueError("aspect_ratio 须为 auto 或 X:Y")
                x, y = map(int, match.groups())
                if max(x, y) > 300 or not 1 / 3 <= x / y <= 3:
                    raise ValueError("比例分量须在 1~300，宽高比须在 1:3~3:1")
            elif payload.get("disable_prompt_enhanced"):
                raise ValueError("关闭 Prompt 增强时必须指定尺寸或固定比例")
            area = {"1K": 1024**2, "2K": 2048**2, "4K": 4096**2}[payload.get("area", "4K")]
        if payload["response_format"] == "b64_json":
            if payload.get("stream"):
                raise ValueError("b64_json 不支持流式输出")
            limit = 20000000 if payload.get("output_format") == "png" else 80000000
            if params.n * area > limit:
                raise ValueError("Base64 图片总面积超限，请使用 url 或减小尺寸/数量")
        return payload

    def _legacy_payload(self, params: ImageGenParams) -> dict[str, Any]:
        allowed = {"aspect_ratio", "response_format", "prompt_optimizer", "seed", "add_watermark"}
        if unknown := params.options.keys() - allowed:
            raise ValueError(f"旧版图片协议不支持参数: {', '.join(sorted(unknown))}")
        if params.n > 9 or len(params.images) > 1:
            raise ValueError("旧版图片协议最多生成 9 张，仅支持一张主体参考图")
        if any(src.startswith("mm_file://") for src in params.images):
            raise ValueError("旧版图片协议不支持 mm_file 引用")
        payload = {"model": params.model, "prompt": params.prompt, "n": params.n,
                   "response_format": "url", **params.options}
        if params.image_size or not params.images:
            payload.setdefault("aspect_ratio", self._aspect_ratio(params.image_size))
        if payload["response_format"] == "b64_json":
            payload["response_format"] = "base64"
        if payload["response_format"] not in ("url", "base64"):
            raise ValueError("旧版 response_format 须为 url 或 base64")
        if params.images:
            payload["subject_reference"] = [{"type": "character", "image_file": params.images[0]}]
        return payload

    def extract_urls(self, result: dict[str, Any]) -> ImageResults:
        check_base_resp(result)
        if isinstance(result.get("data"), list):
            return OpenAIImagesAdapter().extract_urls({"output_format": "jpeg", **result})
        data = result.get("data") or {}
        urls = [url for url in data.get("image_urls", []) if url]
        if not urls:
            urls = [f"data:image/png;base64,{value}" for value in data.get("image_base64", []) if value]
        metadata = result.get("metadata") or {}
        if not urls and metadata.get("failed_count"):
            raise RuntimeError("图片生成被内容安全拦截（failed_count>0）")
        return ImageResults(urls, details={"metadata": metadata} if metadata else {})

    async def read_stream(self, events: AsyncIterator[dict[str, Any]], *, expected_count: int = 1) -> ImageResults:
        """按 image_index 汇总 SSE；部分失败保留诊断，缺少结束事件不冒充完整成功。"""
        items: dict[int, dict[str, Any]] = {}
        result: dict[str, Any] = {}
        completed = False
        async for event in events:
            kind = str(event.get("type", ""))
            if kind == "error":
                raise RuntimeError(f"MiniMax 图片流失败: {event.get('error')}")
            if kind.endswith((".partial_succeeded", ".partial_failed")):
                index = event.get("image_index")
                if type(index) is not int or not 0 <= index < expected_count or index in items:
                    raise ValueError("图片流返回无效或重复 image_index")
                if kind.endswith(".partial_succeeded"):
                    if not event.get("url"):
                        raise ValueError("图片流成功事件缺少 URL")
                    items[index] = {key: event[key] for key in ("url", "size", "revised_prompt") if key in event}
                    result["output_format"] = event.get("output_format", "jpeg")
                else:
                    items[index] = {"error": {"code": event.get("code"), "message": event.get("message")}}
            elif kind.endswith(".completed"):
                result.update({key: event[key] for key in ("usage", "request_id") if key in event})
                completed = True
                break
        if not completed:
            raise MediaOutcomeUnknown("图片流未收到完成事件，远端结果未知，请勿自动重试")
        result["data"] = [items.get(index, {"error": {"message": "缺少图片结果"}})
                          for index in range(expected_count)]
        return self.extract_urls(result)
