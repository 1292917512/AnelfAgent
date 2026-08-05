"""图片生成协议适配器：收口不同供应商的文生图 API 差异。

各家图片生成接口没有统一标准（路径、请求体、响应格式均不同），
MediaClient 不感知具体差异，统一通过适配器构建请求、解析响应。

扩展新供应商：实现 ImageGenAdapter 并调用 register_image_adapter() 注册；
供应商配置可通过 media_protocol 显式指定适配器，未指定时按 host 规则自动匹配。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple

from agent.llm.adapter_base import AdapterRegistry, AdapterRequest, check_base_resp, host_root

# 图片请求与共享请求类型同构，直接复用（保留别名以兼容既有引用）
ImageGenRequest = AdapterRequest


class ImageGenAdapter(ABC):
    """图片生成协议适配器基类。"""

    name: str = ""

    @abstractmethod
    def build_generate_request(
        self,
        base_url: str,
        *,
        model: str,
        prompt: str,
        image_size: str,
        num_inference_steps: int,
        cfg: Optional[float],
    ) -> ImageGenRequest:
        """构建文生图请求。"""

    def build_edit_request(
        self,
        base_url: str,
        *,
        model: str,
        prompt: str,
        image_content: str,
        num_inference_steps: int,
        cfg: float,
    ) -> ImageGenRequest:
        """构建图片编辑请求；协议未实现时默认不支持。"""
        raise NotImplementedError(f"图片协议 '{self.name}' 不支持图片编辑")

    @abstractmethod
    def extract_urls(self, result: Dict[str, Any]) -> List[str]:
        """从响应 JSON 提取图片 URL（或 data:base64）列表。"""


class SiliconFlowAdapter(ImageGenAdapter):
    """SiliconFlow 风格：POST {base_url}/images/generations（image_size/num_inference_steps/cfg）。"""

    name = "siliconflow"

    def build_generate_request(
        self,
        base_url: str,
        *,
        model: str,
        prompt: str,
        image_size: str,
        num_inference_steps: int,
        cfg: Optional[float],
    ) -> ImageGenRequest:
        payload: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "image_size": image_size,
            "num_inference_steps": num_inference_steps,
        }
        if cfg is not None:
            payload["cfg"] = cfg
        return ImageGenRequest(url=f"{base_url}/images/generations", payload=payload)

    def build_edit_request(
        self,
        base_url: str,
        *,
        model: str,
        prompt: str,
        image_content: str,
        num_inference_steps: int,
        cfg: float,
    ) -> ImageGenRequest:
        payload: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "image": image_content,
            "num_inference_steps": num_inference_steps,
            "cfg": cfg,
        }
        return ImageGenRequest(url=f"{base_url}/images/generations", payload=payload)

    def extract_urls(self, result: Dict[str, Any]) -> List[str]:
        """SiliconFlow 格式优先，OpenAI 格式兜底（部分网关混用）。"""
        out = [
            item["url"]
            for item in result.get("images", [])
            if isinstance(item, dict) and item.get("url")
        ]
        if out:
            return out
        return OpenAIImagesAdapter().extract_urls(result)


class OpenAIImagesAdapter(ImageGenAdapter):
    """OpenAI 风格：POST {base_url}/images/generations（size/n）。"""

    name = "openai"

    def build_generate_request(
        self,
        base_url: str,
        *,
        model: str,
        prompt: str,
        image_size: str,
        num_inference_steps: int,
        cfg: Optional[float],
    ) -> ImageGenRequest:
        payload: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "size": image_size,
            "n": 1,
        }
        return ImageGenRequest(url=f"{base_url}/images/generations", payload=payload)

    def extract_urls(self, result: Dict[str, Any]) -> List[str]:
        out: List[str] = []
        for item in result.get("data", []):
            if not isinstance(item, dict):
                continue
            if item.get("url"):
                out.append(item["url"])
            elif item.get("b64_json"):
                out.append(f"data:image/png;base64,{item['b64_json']}")
        return out


class DashScopeImagesAdapter(ImageGenAdapter):
    """阿里云百炼原生同步多模态生成接口（万相 wan 系列）。

    接口挂在网关机根路径（dashscope.aliyuncs.com、token-plan.*.maas.aliyuncs.com 等），
    与 base_url 中的聊天协议路径（/compatible-mode/v1、/apps/anthropic）无关，
    因此始终从 host 根路径拼接。
    """

    name = "dashscope"
    _PATH = "/api/v1/services/aigc/multimodal-generation/generation"

    def build_generate_request(
        self,
        base_url: str,
        *,
        model: str,
        prompt: str,
        image_size: str,
        num_inference_steps: int,
        cfg: Optional[float],
    ) -> ImageGenRequest:
        payload: Dict[str, Any] = {
            "model": model,
            "input": {"messages": [{"role": "user", "content": [{"text": prompt}]}]},
            "parameters": {"size": image_size.replace("x", "*"), "n": 1},
        }
        return ImageGenRequest(url=f"{host_root(base_url)}{self._PATH}", payload=payload)

    def extract_urls(self, result: Dict[str, Any]) -> List[str]:
        out: List[str] = []
        for choice in result.get("output", {}).get("choices", []):
            content = (choice.get("message") or {}).get("content", [])
            for item in content:
                if isinstance(item, dict) and item.get("type") == "image" and item.get("image"):
                    out.append(item["image"])
        return out


class MiniMaxImagesAdapter(ImageGenAdapter):
    """MiniMax 图片生成（image-01 / image-01-live）：POST /v1/image_generation。

    文生图直接走 prompt；图片编辑映射为主体参考（subject_reference character）。
    接口挂在网关机根路径，始终从 host 根拼接。
    """

    name = "minimax"

    @staticmethod
    def _aspect_ratio(image_size: str) -> str:
        """将 "WxH" 尺寸换算为最近的 MiniMax 画幅比。"""
        try:
            w, h = image_size.lower().split("x", 1)
            ratio = int(w) / int(h)
        except (ValueError, ZeroDivisionError):
            return "1:1"
        candidates = {
            "21:9": 21 / 9, "16:9": 16 / 9, "4:3": 4 / 3, "3:2": 3 / 2,
            "1:1": 1.0, "2:3": 2 / 3, "3:4": 3 / 4, "9:16": 9 / 16,
        }
        return min(candidates, key=lambda k: abs(candidates[k] - ratio))

    def build_generate_request(
        self,
        base_url: str,
        *,
        model: str,
        prompt: str,
        image_size: str,
        num_inference_steps: int,
        cfg: Optional[float],
    ) -> ImageGenRequest:
        payload: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "aspect_ratio": self._aspect_ratio(image_size),
            "response_format": "url",
            "n": 1,
        }
        return ImageGenRequest(
            url=f"{host_root(base_url)}/v1/image_generation", payload=payload,
        )

    def build_edit_request(
        self,
        base_url: str,
        *,
        model: str,
        prompt: str,
        image_content: str,
        num_inference_steps: int,
        cfg: float,
    ) -> ImageGenRequest:
        payload: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "subject_reference": [{"type": "character", "image_file": image_content}],
            "response_format": "url",
            "n": 1,
        }
        return ImageGenRequest(
            url=f"{host_root(base_url)}/v1/image_generation", payload=payload,
        )

    def extract_urls(self, result: Dict[str, Any]) -> List[str]:
        check_base_resp(result)
        data = result.get("data") or {}
        out: List[str] = [u for u in data.get("image_urls", []) if u]
        if not out:
            out = [f"data:image/png;base64,{b}" for b in data.get("image_base64", []) if b]
        metadata = result.get("metadata") or {}
        if not out and metadata.get("failed_count"):
            raise RuntimeError("图片生成被内容安全拦截（failed_count>0）")
        return out


_REGISTRY: AdapterRegistry[ImageGenAdapter] = AdapterRegistry("图片")


def register_image_adapter(
    adapter: ImageGenAdapter,
    *,
    host_keywords: Tuple[str, ...] = (),
    default: bool = False,
) -> None:
    """注册图片协议适配器（语义见 AdapterRegistry.register）。"""
    _REGISTRY.register(adapter, host_keywords=host_keywords, default=default)


def resolve_image_adapter(base_url: str, protocol: str = "") -> ImageGenAdapter:
    """解析图片协议适配器（语义见 AdapterRegistry.resolve）。"""
    return _REGISTRY.resolve(base_url, protocol)


register_image_adapter(SiliconFlowAdapter(), default=True)
register_image_adapter(OpenAIImagesAdapter())
register_image_adapter(DashScopeImagesAdapter(), host_keywords=("aliyuncs.com",))
register_image_adapter(MiniMaxImagesAdapter(), host_keywords=("minimaxi.com", "minimax.io"))
