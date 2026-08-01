"""协议适配器共享类型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(slots=True)
class AdapterRequest:
    """一次媒体 API HTTP 请求（方法 + URL + 查询参数 + JSON 请求体）。"""

    url: str
    method: str = "POST"
    payload: Optional[Dict[str, Any]] = None
    params: Optional[Dict[str, Any]] = None


def host_root(base_url: str) -> str:
    """取 base_url 的 scheme://netloc 根（接口挂在网关机根路径的协议使用）。"""
    from urllib.parse import urlparse
    parsed = urlparse(base_url)
    return f"{parsed.scheme}://{parsed.netloc}"
