"""图片生成协议适配器：收口不同供应商的文生图 API 差异。

各家图片生成接口没有统一标准（路径、请求体、响应格式均不同），
MediaClient 不感知具体差异，统一通过适配器构建请求、解析响应。

扩展新供应商：实现 ImageGenAdapter 并调用 register_image_adapter() 注册；
供应商配置可通过 media_protocol 显式指定适配器，未指定时按 host 规则自动匹配。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse


@dataclass(slots=True)
class ImageGenRequest:
    """一次图片生成 HTTP 请求（URL + JSON 请求体）。"""

    url: str
    payload: Dict[str, Any]


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
        return OpenAIImagesAdapter.extract_urls(self, result)


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
        parsed = urlparse(base_url)
        payload: Dict[str, Any] = {
            "model": model,
            "input": {"messages": [{"role": "user", "content": [{"text": prompt}]}]},
            "parameters": {"size": image_size.replace("x", "*"), "n": 1},
        }
        return ImageGenRequest(url=f"{parsed.scheme}://{parsed.netloc}{self._PATH}", payload=payload)

    def extract_urls(self, result: Dict[str, Any]) -> List[str]:
        out: List[str] = []
        for choice in result.get("output", {}).get("choices", []):
            content = choice.get("message", {}).get("content", [])
            for item in content:
                if isinstance(item, dict) and item.get("type") == "image" and item.get("image"):
                    out.append(item["image"])
        return out


_ADAPTERS: Dict[str, ImageGenAdapter] = {}
_HOST_RULES: List[Tuple[str, str]] = []
_default_adapter: str = ""


def register_image_adapter(
    adapter: ImageGenAdapter,
    *,
    host_keywords: Tuple[str, ...] = (),
    default: bool = False,
) -> None:
    """注册图片协议适配器。

    host_keywords: base_url 主机名包含任一关键字时自动匹配该适配器；
    default: 未命中任何规则时的兜底适配器。
    """
    global _default_adapter
    _ADAPTERS[adapter.name] = adapter
    for keyword in host_keywords:
        _HOST_RULES.append((keyword, adapter.name))
    if default or not _default_adapter:
        _default_adapter = adapter.name


def resolve_image_adapter(base_url: str, protocol: str = "") -> ImageGenAdapter:
    """解析图片协议适配器：显式 protocol 优先，其次 host 规则，最后兜底。"""
    if protocol:
        adapter = _ADAPTERS.get(protocol)
        if adapter is None:
            raise ValueError(f"未知的图片协议: {protocol}（可用: {sorted(_ADAPTERS)}）")
        return adapter
    host = urlparse(base_url).netloc
    for keyword, name in _HOST_RULES:
        if keyword in host:
            return _ADAPTERS[name]
    return _ADAPTERS[_default_adapter]


register_image_adapter(SiliconFlowAdapter(), default=True)
register_image_adapter(OpenAIImagesAdapter())
register_image_adapter(DashScopeImagesAdapter(), host_keywords=("aliyuncs.com",))
