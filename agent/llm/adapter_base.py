"""协议适配器共享类型、工具与注册表。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Generic, List, Optional, Protocol, Tuple, TypeVar
from urllib.parse import urlparse

from core.log import log


@dataclass(slots=True)
class AdapterRequest:
    """一次媒体 API HTTP 请求（方法 + URL + 查询参数 + JSON 请求体 + 附加请求头）。"""

    url: str
    method: str = "POST"
    payload: Optional[Dict[str, Any]] = None
    params: Optional[Dict[str, Any]] = None
    # 协议要求的附加请求头（如 DashScope 异步任务的 X-DashScope-Async）
    headers: Optional[Dict[str, str]] = None


def host_root(base_url: str) -> str:
    """取 base_url 的 scheme://netloc 根（接口挂在网关机根路径的协议使用）。"""
    parsed = urlparse(base_url)
    return f"{parsed.scheme}://{parsed.netloc}"


def check_base_resp(result: Dict[str, Any], *, provider: str = "MiniMax") -> None:
    """检查 base_resp 错误信封（MiniMax 系 API 的 200-body 错误），非零即抛。"""
    base_resp = result.get("base_resp") or {}
    code = base_resp.get("status_code", 0)
    if code != 0:
        raise RuntimeError(f"{provider} API 错误 ({code}): {base_resp.get('status_msg', '')}")


class NamedAdapter(Protocol):
    """适配器公共特征：协议名。"""

    name: str


AdapterT = TypeVar("AdapterT", bound=NamedAdapter)


class AdapterRegistry(Generic[AdapterT]):
    """媒体协议适配器注册表：register + resolve。

    resolve 语义（image/speech/video/music 四路统一）：显式 protocol 命中即用；
    未命中（media_protocol 字段为多类媒体协议共用，可能填的是其他类的协议名）
    记 DEBUG 后回退 host 规则；全部未命中时返回默认适配器，未注册默认
    （能力非通用，如音乐）则抛 NotImplementedError。
    model_dispatch 用于同 host 按模型名分流协议版本（如 MiniMax 视频 v1/v2）。
    """

    def __init__(self, kind: str) -> None:
        self._kind = kind
        self._adapters: Dict[str, AdapterT] = {}
        self._host_rules: List[Tuple[str, str]] = []
        self._default: str = ""
        self._model_dispatch: Dict[str, Callable[[str], str]] = {}

    def register(
        self,
        adapter: AdapterT,
        *,
        host_keywords: Tuple[str, ...] = (),
        default: bool = False,
        model_dispatch: Optional[Callable[[str], str]] = None,
    ) -> None:
        """注册适配器。

        host_keywords: base_url 主机名包含任一关键字时自动匹配该适配器；
        default: 设为未命中任何规则时的兜底适配器；
        model_dispatch: 命中该适配器名时按模型名再分流（返回最终适配器名）。
        """
        self._adapters[adapter.name] = adapter
        for keyword in host_keywords:
            self._host_rules.append((keyword, adapter.name))
        if default:
            self._default = adapter.name
        if model_dispatch is not None:
            self._model_dispatch[adapter.name] = model_dispatch

    def resolve(self, base_url: str, protocol: str = "", model: str = "") -> AdapterT:
        """解析适配器：显式 protocol → host 规则 → 默认兜底 → 抛 NotImplementedError。"""
        if protocol:
            if protocol in self._adapters:
                return self._get(protocol, model)
            log(
                f"media_protocol '{protocol}' 不是{self._kind}协议，按 host 规则自动匹配",
                "DEBUG", tag="媒体",
            )
        host = urlparse(base_url).netloc
        for keyword, name in self._host_rules:
            if keyword in host:
                return self._get(name, model)
        if self._default:
            return self._get(self._default, model)
        raise NotImplementedError(f"当前供应商不支持{self._kind}协议（未命中任何适配器规则）")

    def _get(self, name: str, model: str) -> AdapterT:
        dispatch = self._model_dispatch.get(name)
        if dispatch is not None:
            name = dispatch(model)
        return self._adapters[name]
