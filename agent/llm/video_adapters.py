"""视频生成协议适配器：收口不同供应商的视频生成 API 差异。

各家视频生成接口没有统一标准（路径、请求体、任务状态机均不同），
MediaClient 不感知具体差异，统一通过适配器构建请求、解析响应。

扩展新供应商：实现 VideoGenAdapter 并调用 register_video_adapter() 注册；
供应商配置可通过 media_protocol 显式指定适配器，未指定时按 host 规则自动匹配。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from agent.llm.adapter_base import AdapterRequest


@dataclass(slots=True)
class VideoGenParams:
    """一次视频生成的参数集合（适配器按需取用，不支持的字段忽略）。"""

    model: str
    prompt: str
    first_frame_image: str = ""
    last_frame_image: str = ""
    subject_reference: List[str] = field(default_factory=list)
    duration: Optional[int] = None
    resolution: str = ""
    ratio: str = ""
    prompt_optimizer: Optional[bool] = None
    fast_pretreatment: Optional[bool] = None
    aigc_watermark: Optional[bool] = None


# 视频请求与共享请求类型同构，直接复用（保留别名以兼容既有引用）
VideoGenRequest = AdapterRequest


@dataclass(slots=True)
class VideoTaskState:
    """归一化的视频任务状态。"""

    status: str  # processing / succeeded / failed
    video_url: str = ""
    error: str = ""
    file_id: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


class VideoGenAdapter(ABC):
    """视频生成协议适配器基类。"""

    name: str = ""

    @abstractmethod
    def build_create_request(self, base_url: str, params: VideoGenParams) -> VideoGenRequest:
        """构建创建视频任务请求。"""

    @abstractmethod
    def extract_task_id(self, result: Dict[str, Any]) -> str:
        """从创建响应提取任务 ID（同步返回结果的协议可为空串）。"""

    def extract_sync_url(self, result: Dict[str, Any]) -> str:
        """从创建响应提取同步返回的视频 URL（无异步任务时使用）。"""
        return ""

    @abstractmethod
    def build_query_request(self, base_url: str, task_id: str) -> VideoGenRequest:
        """构建任务状态查询请求。"""

    @abstractmethod
    def parse_query_result(self, result: Dict[str, Any]) -> VideoTaskState:
        """解析任务状态查询响应为归一化状态。"""

    def build_retrieve_request(self, base_url: str, file_id: str) -> VideoGenRequest:
        """构建文件下载地址获取请求（按 file_id 换取下载 URL 的协议使用）。"""
        raise NotImplementedError(f"视频协议 '{self.name}' 不支持文件检索")

    def extract_download_url(self, result: Dict[str, Any]) -> str:
        """从文件检索响应提取下载 URL。"""
        raise NotImplementedError(f"视频协议 '{self.name}' 不支持文件检索")

    def build_list_request(
        self,
        base_url: str,
        *,
        page_num: int,
        page_size: int,
        status: str = "",
    ) -> VideoGenRequest:
        """构建任务列表查询请求；协议未实现时默认不支持。"""
        raise NotImplementedError(f"视频协议 '{self.name}' 不支持任务列表查询")

    def parse_list_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """解析任务列表响应；协议未实现时默认不支持。"""
        raise NotImplementedError(f"视频协议 '{self.name}' 不支持任务列表查询")

    def build_delete_request(self, base_url: str, task_id: str) -> VideoGenRequest:
        """构建取消/删除任务请求；协议未实现时默认不支持。"""
        raise NotImplementedError(f"视频协议 '{self.name}' 不支持取消/删除任务")

    def parse_delete_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """解析取消/删除任务响应；协议未实现时默认不支持。"""
        raise NotImplementedError(f"视频协议 '{self.name}' 不支持取消/删除任务")


def _host_root(base_url: str) -> str:
    """取 base_url 的 scheme://netloc 根（接口挂在网关机根路径的协议使用）。"""
    parsed = urlparse(base_url)
    return f"{parsed.scheme}://{parsed.netloc}"


class OpenAIVideoAdapter(VideoGenAdapter):
    """OpenAI/SiliconFlow 风格：POST {base_url}/videos/generations + GET 轮询。"""

    name = "openai"

    def build_create_request(self, base_url: str, params: VideoGenParams) -> VideoGenRequest:
        payload: Dict[str, Any] = {"model": params.model, "prompt": params.prompt}
        if params.first_frame_image:
            payload["image_url"] = params.first_frame_image
        return VideoGenRequest(url=f"{base_url}/videos/generations", payload=payload)

    def extract_task_id(self, result: Dict[str, Any]) -> str:
        return result.get("requestId") or result.get("id", "")

    def extract_sync_url(self, result: Dict[str, Any]) -> str:
        return _extract_openai_video_url(result)

    def build_query_request(self, base_url: str, task_id: str) -> VideoGenRequest:
        return VideoGenRequest(url=f"{base_url}/videos/generations/{task_id}", method="GET")

    def parse_query_result(self, result: Dict[str, Any]) -> VideoTaskState:
        status = result.get("status", "")
        if status in ("succeeded", "complete", "Succeed"):
            video_url = _extract_openai_video_url(result)
            if not video_url:
                return VideoTaskState(status="failed", error=f"任务完成但未返回视频地址: {result}", raw=result)
            return VideoTaskState(status="succeeded", video_url=video_url, raw=result)
        if status in ("failed", "error", "Failed"):
            return VideoTaskState(status="failed", error=f"视频生成失败: {result}", raw=result)
        return VideoTaskState(status="processing", raw=result)


def _extract_openai_video_url(data: Dict[str, Any]) -> str:
    """从 OpenAI 风格的多种响应格式中提取视频 URL。"""
    if "video" in data and isinstance(data["video"], dict):
        return data["video"].get("url", "")
    results = data.get("results", data.get("data", []))
    if isinstance(results, list):
        for item in results:
            if isinstance(item, dict) and "url" in item:
                return item["url"]
    return data.get("url", "")


class MiniMaxV1Adapter(VideoGenAdapter):
    """MiniMax v1 视频生成（Hailuo/01 系列）。

    文生视频 / 图生视频 / 首尾帧 / 主体参考统一走 POST /v1/video_generation，
    按参数组装 first_frame_image / last_frame_image / subject_reference。
    任务成功后需凭 file_id 经 /v1/files/retrieve 换取限时下载地址。
    接口挂在网关机根路径，始终从 host 根拼接。
    """

    name = "minimax"

    @staticmethod
    def _check_base_resp(result: Dict[str, Any]) -> None:
        base_resp = result.get("base_resp") or {}
        code = base_resp.get("status_code", 0)
        if code != 0:
            raise RuntimeError(f"MiniMax API 错误 ({code}): {base_resp.get('status_msg', '')}")

    def build_create_request(self, base_url: str, params: VideoGenParams) -> VideoGenRequest:
        payload: Dict[str, Any] = {"model": params.model}
        if params.prompt:
            payload["prompt"] = params.prompt
        if params.first_frame_image:
            payload["first_frame_image"] = params.first_frame_image
        if params.last_frame_image:
            payload["last_frame_image"] = params.last_frame_image
        if params.subject_reference:
            payload["subject_reference"] = [
                {"type": "character", "image": params.subject_reference}
            ]
        if params.prompt_optimizer is not None:
            payload["prompt_optimizer"] = params.prompt_optimizer
        if params.fast_pretreatment is not None:
            payload["fast_pretreatment"] = params.fast_pretreatment
        if params.duration is not None:
            payload["duration"] = params.duration
        if params.resolution:
            payload["resolution"] = params.resolution
        if params.aigc_watermark is not None:
            payload["aigc_watermark"] = params.aigc_watermark
        return VideoGenRequest(url=f"{_host_root(base_url)}/v1/video_generation", payload=payload)

    def extract_task_id(self, result: Dict[str, Any]) -> str:
        self._check_base_resp(result)
        return result.get("task_id", "")

    def build_query_request(self, base_url: str, task_id: str) -> VideoGenRequest:
        return VideoGenRequest(
            url=f"{_host_root(base_url)}/v1/query/video_generation",
            method="GET",
            params={"task_id": task_id},
        )

    def parse_query_result(self, result: Dict[str, Any]) -> VideoTaskState:
        status = result.get("status", "")
        if status == "Success":
            self._check_base_resp(result)
            return VideoTaskState(
                status="succeeded",
                file_id=str(result.get("file_id", "")),
                raw=result,
            )
        if status == "Fail":
            # 失败任务的 base_resp 携带失败原因（如 1027 敏感内容），不作为传输错误抛出
            base_resp = result.get("base_resp") or {}
            return VideoTaskState(
                status="failed",
                error=base_resp.get("status_msg", "") or f"视频生成失败: {result}",
                raw=result,
            )
        self._check_base_resp(result)
        return VideoTaskState(status="processing", raw=result)

    def build_retrieve_request(self, base_url: str, file_id: str) -> VideoGenRequest:
        return VideoGenRequest(
            url=f"{_host_root(base_url)}/v1/files/retrieve",
            method="GET",
            params={"file_id": file_id},
        )

    def extract_download_url(self, result: Dict[str, Any]) -> str:
        self._check_base_resp(result)
        file_obj = result.get("file") or {}
        return file_obj.get("download_url", "")


class MiniMaxV2Adapter(VideoGenAdapter):
    """MiniMax v2 视频生成（MiniMax-H3）。

    多模态 content 数组输入，支持首帧/尾帧/参考图；任务管理接口完备
    （单查 / 列表 / 取消删除）。接口挂在网关机根路径，始终从 host 根拼接。
    """

    name = "minimax_v2"

    @staticmethod
    def _check_error(result: Dict[str, Any]) -> None:
        if result.get("type") == "error" or "error" in result:
            error = result.get("error") or {}
            raise RuntimeError(
                f"MiniMax API 错误 ({error.get('type', '')}): {error.get('message', '')}"
            )

    def build_create_request(self, base_url: str, params: VideoGenParams) -> VideoGenRequest:
        content: List[Dict[str, Any]] = [{"type": "text", "text": params.prompt}]
        if params.first_frame_image:
            content.append({
                "type": "image_url",
                "image_url": params.first_frame_image,
                "role": "first_frame",
            })
        if params.last_frame_image:
            content.append({
                "type": "image_url",
                "image_url": params.last_frame_image,
                "role": "last_frame",
            })
        for image in params.subject_reference:
            content.append({
                "type": "image_url",
                "image_url": image,
                "role": "reference_image",
            })
        payload: Dict[str, Any] = {
            "model": params.model,
            "content": content,
            "resolution": params.resolution or "2K",
        }
        if params.duration is not None:
            payload["duration"] = params.duration
        has_frames = bool(params.first_frame_image or params.last_frame_image)
        # 图生视频比例恒为 adaptive；文生视频必须显式指定比例
        payload["ratio"] = "adaptive" if has_frames else (params.ratio or "16:9")
        if params.aigc_watermark is not None:
            payload["aigc_watermark"] = params.aigc_watermark
        return VideoGenRequest(url=f"{_host_root(base_url)}/v2/video_generation", payload=payload)

    def extract_task_id(self, result: Dict[str, Any]) -> str:
        self._check_error(result)
        return result.get("task_id", "")

    def build_query_request(self, base_url: str, task_id: str) -> VideoGenRequest:
        return VideoGenRequest(
            url=f"{_host_root(base_url)}/v2/query/video_generation/{task_id}",
            method="GET",
        )

    def parse_query_result(self, result: Dict[str, Any]) -> VideoTaskState:
        self._check_error(result)
        task = result.get("task") or {}
        status = task.get("status", "")
        if status == "succeeded":
            video_url = (task.get("content") or {}).get("url", "")
            if not video_url:
                return VideoTaskState(status="failed", error=f"任务完成但未返回视频地址: {result}", raw=result)
            return VideoTaskState(status="succeeded", video_url=video_url, raw=result)
        if status in ("failed", "cancelled", "expired"):
            error = task.get("error") or {}
            return VideoTaskState(
                status="failed",
                error=error.get("message", "") or f"视频任务 {status}",
                raw=result,
            )
        return VideoTaskState(status="processing", raw=result)

    def build_list_request(
        self,
        base_url: str,
        *,
        page_num: int,
        page_size: int,
        status: str = "",
    ) -> VideoGenRequest:
        params: Dict[str, Any] = {"page_num": page_num, "page_size": page_size}
        if status:
            params["filter.status"] = status
        return VideoGenRequest(
            url=f"{_host_root(base_url)}/v2/query/video_generation",
            method="GET",
            params=params,
        )

    def parse_list_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        self._check_error(result)
        return {"items": result.get("items", []), "total": result.get("total", 0)}

    def build_delete_request(self, base_url: str, task_id: str) -> VideoGenRequest:
        return VideoGenRequest(
            url=f"{_host_root(base_url)}/v2/video_generation/{task_id}",
            method="DELETE",
        )

    def parse_delete_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        self._check_error(result)
        return {
            "task_id": result.get("task_id", ""),
            "action": result.get("action", ""),
            "status": result.get("status", ""),
        }


_ADAPTERS: Dict[str, VideoGenAdapter] = {}
_HOST_RULES: List[Tuple[str, str]] = []
_default_adapter: str = ""


def register_video_adapter(
    adapter: VideoGenAdapter,
    *,
    host_keywords: Tuple[str, ...] = (),
    default: bool = False,
) -> None:
    """注册视频协议适配器。

    host_keywords: base_url 主机名包含任一关键字时自动匹配该适配器；
    default: 未命中任何规则时的兜底适配器。
    """
    global _default_adapter
    _ADAPTERS[adapter.name] = adapter
    for keyword in host_keywords:
        _HOST_RULES.append((keyword, adapter.name))
    if default or not _default_adapter:
        _default_adapter = adapter.name


def _resolve_minimax_version(model: str) -> str:
    """MiniMax 主机按模型名分流协议版本：MiniMax-H3 走 v2，其余走 v1。"""
    return "minimax_v2" if model.strip().lower() == "minimax-h3" else "minimax"


def resolve_video_adapter(base_url: str, protocol: str = "", model: str = "") -> VideoGenAdapter:
    """解析视频协议适配器：显式 protocol 优先，其次 host 规则，最后兜底。"""
    if protocol:
        if protocol == "minimax":
            return _ADAPTERS[_resolve_minimax_version(model)]
        adapter = _ADAPTERS.get(protocol)
        if adapter is None:
            raise ValueError(f"未知的视频协议: {protocol}（可用: {sorted(_ADAPTERS)}）")
        return adapter
    host = urlparse(base_url).netloc
    for keyword, name in _HOST_RULES:
        if keyword in host:
            if name == "minimax":
                return _ADAPTERS[_resolve_minimax_version(model)]
            return _ADAPTERS[name]
    return _ADAPTERS[_default_adapter]


register_video_adapter(OpenAIVideoAdapter(), default=True)
register_video_adapter(MiniMaxV1Adapter(), host_keywords=("minimaxi.com", "minimax.io"))
register_video_adapter(MiniMaxV2Adapter())
