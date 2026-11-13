"""图片协议契约与通用适配器；平台专属实现由组件通过 SDK 注册。

模型可见：生成参数、多参考图和逐图错误经工具调用与结果传递。
Token：仅所选工具的参数与结果增加少量文本，不增加主模型调用。
缓存：媒体请求独立执行，结果位于动态工具区，不修改稳定前缀。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from agent.llm.adapter_base import AdapterRegistry, AdapterRequest, host_root


@dataclass(frozen=True)
class ImageGenParams:
    """通用图片请求；images 保持调用方顺序，options 由协议实现解释。"""

    model: str
    prompt: str
    image_size: str = ""
    images: tuple[str, ...] = ()
    n: int = 1
    num_inference_steps: int = 20
    cfg: float | None = None
    options: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.prompt.strip():
            raise ValueError("图片提示词不能为空")
        if type(self.n) is not int or self.n < 1:
            raise ValueError("图片数量 n 必须为正整数")


class ImageResults(list[str]):
    """兼容图片地址列表，同时保留逐图诊断、尺寸和供应商用量。"""

    def __init__(self, urls: list[str], *, details: dict[str, Any] | None = None) -> None:
        super().__init__(urls)
        self.details = details or {}


class ImageGenAdapter(ABC):
    """组件构建请求并解析响应；HTTP、凭据、代理和超时由媒体客户端统一管理。"""

    name: str = ""
    reference_schemes: tuple[str, ...] = ("http://", "https://", "data:image/")

    @abstractmethod
    def build_request(self, base_url: str, params: ImageGenParams) -> AdapterRequest:
        """images 非空时构建编辑请求，否则构建文生图请求。"""

    @abstractmethod
    def extract_urls(self, result: dict[str, Any]) -> ImageResults:
        """提取成功产物和诊断；无成功产物时由调用方报错。"""

    async def read_stream(self, events: AsyncIterator[dict[str, Any]], *, expected_count: int = 1) -> ImageResults:
        """汇总协议事件；未声明支持时拒绝流式调用。"""
        raise NotImplementedError(f"图片协议 '{self.name}' 不支持流式响应")


def merge_image_options(payload: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    """扩展参数不得覆盖已经构建的模型、输入或数量字段。"""
    conflicts = payload.keys() & options.keys()
    if conflicts:
        raise ValueError(f"图片扩展参数与标准参数冲突: {', '.join(sorted(conflicts))}")
    return {**payload, **options}


class SiliconFlowAdapter(ImageGenAdapter):
    """SiliconFlow 图片接口；单图编辑沿用 image 字段。"""

    name = "siliconflow"

    def build_request(self, base_url: str, params: ImageGenParams) -> AdapterRequest:
        if len(params.images) > 1:
            raise ValueError("siliconflow 图片协议仅支持一张参考图")
        if params.options.get("stream"):
            raise ValueError("siliconflow 图片适配器暂不支持流式输出")
        payload: dict[str, Any] = {
            "model": params.model, "prompt": params.prompt,
            "num_inference_steps": params.num_inference_steps,
        }
        if params.images:
            payload.update(image=params.images[0], cfg=params.cfg if params.cfg is not None else 4.0)
        else:
            payload["image_size"] = params.image_size or "1024x1024"
            if params.cfg is not None:
                payload["cfg"] = params.cfg
        if params.n != 1:
            payload["batch_size"] = params.n
        return AdapterRequest(url=f"{base_url}/images/generations",
                              payload=merge_image_options(payload, params.options))

    def extract_urls(self, result: dict[str, Any]) -> ImageResults:
        urls = [item["url"] for item in result.get("images", [])
                if isinstance(item, dict) and item.get("url")]
        return ImageResults(urls) if urls else OpenAIImagesAdapter().extract_urls(result)


class OpenAIImagesAdapter(ImageGenAdapter):
    """OpenAI 兼容文生图；编辑的文件上传契约由支持该协议的组件实现。"""

    name = "openai"

    def build_request(self, base_url: str, params: ImageGenParams) -> AdapterRequest:
        if params.images:
            raise NotImplementedError("图片协议 'openai' 不支持图片编辑")
        payload = {"model": params.model, "prompt": params.prompt,
                   "size": params.image_size or "1024x1024", "n": params.n}
        if params.options.get("stream"):
            raise ValueError("openai 图片适配器暂不支持流式输出")
        return AdapterRequest(url=f"{base_url}/images/generations",
                              payload=merge_image_options(payload, params.options))

    def extract_urls(self, result: dict[str, Any]) -> ImageResults:
        urls: list[str] = []
        items: list[dict[str, Any]] = []
        fmt = result.get("output_format") or "png"
        mime = "jpeg" if fmt == "jpg" else fmt
        for index, item in enumerate(result.get("data", [])):
            if not isinstance(item, dict):
                continue
            if item.get("url"):
                urls.append(item["url"])
            elif item.get("b64_json"):
                urls.append(f"data:image/{mime};base64,{item['b64_json']}")
            items.append({"index": index, **{k: v for k, v in item.items() if k not in {"url", "b64_json"}}})
        details: dict[str, Any] = {key: result[key] for key in ("usage", "request_id", "output_format") if key in result}
        if items:
            details["image_details"] = items
        if not urls and items:
            errors = [str(item["error"]) for item in items if item.get("error")]
            if errors:
                raise RuntimeError("图片生成全部失败: " + "; ".join(errors))
        return ImageResults(urls, details=details)


class DashScopeImagesAdapter(ImageGenAdapter):
    """百炼原生同步多模态生成接口，图片与文本保持输入顺序。"""

    name = "dashscope"
    _PATH = "/api/v1/services/aigc/multimodal-generation/generation"

    def build_request(self, base_url: str, params: ImageGenParams) -> AdapterRequest:
        content = [{"image": value} for value in params.images] + [{"text": params.prompt}]
        parameters: dict[str, Any] = {"n": params.n}
        if params.image_size or not params.images:
            parameters["size"] = (params.image_size or "1024x1024").replace("x", "*")
        if params.options.get("stream"):
            raise ValueError("dashscope 图片适配器暂不支持流式输出")
        payload = {"model": params.model, "input": {"messages": [{"role": "user", "content": content}]},
                   "parameters": merge_image_options(parameters, params.options)}
        return AdapterRequest(url=f"{host_root(base_url)}{self._PATH}", payload=payload)

    def extract_urls(self, result: dict[str, Any]) -> ImageResults:
        urls: list[str] = []
        for choice in result.get("output", {}).get("choices", []):
            for item in (choice.get("message") or {}).get("content", []):
                if isinstance(item, dict) and item.get("image"):
                    urls.append(item["image"])
        return ImageResults(urls)


_REGISTRY: AdapterRegistry[ImageGenAdapter] = AdapterRegistry("图片")


def register_image_adapter(adapter: ImageGenAdapter, *, host_keywords: tuple[str, ...] = (),
                           default: bool = False) -> None:
    """注册组件图片协议，同名重载不重复累积主机规则。"""
    _REGISTRY.register(adapter, host_keywords=host_keywords, default=default)


def unregister_image_adapters_by_module(module: str) -> None:
    """实体热拔除时移除所属图片协议。"""
    _REGISTRY.unregister_by_module(module)


def resolve_image_adapter(base_url: str, protocol: str = "") -> ImageGenAdapter:
    """显式协议优先，其次主机匹配，最后使用默认协议。"""
    return _REGISTRY.resolve(base_url, protocol)


register_image_adapter(SiliconFlowAdapter(), default=True)
register_image_adapter(OpenAIImagesAdapter())
register_image_adapter(DashScopeImagesAdapter(), host_keywords=("aliyuncs.com",))
