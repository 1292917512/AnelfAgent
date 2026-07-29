"""模型能力探测：实测 tools / vision 支持度（经 litellm 发真实请求）。"""

from __future__ import annotations

import os
from typing import Any, Dict

# 必须在 import litellm 之前设置，阻止启动时拉取远端模型价格表
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import litellm

from agent.llm.config import (
    _DEFAULT_API_KEY,
    _LITELLM_PREFIX_MAP,
    API_TYPE_ANTHROPIC,
    API_TYPE_AZURE,
    API_TYPE_GEMINI,
    API_TYPE_OLLAMA,
    API_TYPE_OPENAI,
)


@staticmethod
def _make_test_png(size: int = 64) -> bytes:
    import struct
    import zlib as _zlib

    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        crc = _zlib.crc32(chunk_type + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    scanline = b"\x00" + (b"\xff\x00\x00" * size)
    raw_data = scanline * size
    idat = _zlib.compress(raw_data)
    return (
            b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", idat)
            + _chunk(b"IEND", b"")
    )

@staticmethod
async def probe_capabilities(
        base_url: str,
        api_key: str,
        model: str,
        api_type: str = API_TYPE_OLLAMA,
        timeout: float = 120.0,
) -> Dict[str, Any]:
    """探测模型是否支持 tools 和 vision（通过 litellm）。"""
    prefix = _LITELLM_PREFIX_MAP.get(api_type, "openai")
    litellm_model = f"{prefix}/{model}"
    flat_url = (api_type == API_TYPE_OLLAMA)

    probe_kw: Dict[str, Any] = {
        "api_base": base_url,
        "api_key": api_key or _DEFAULT_API_KEY,
        "timeout": timeout,
        "temperature": 0.7,
    }

    result: Dict[str, Any] = {
        "supports_tools": False,
        "tools_detail": "",
        "supports_vision": False,
        "vision_detail": "",
    }

    result.update(await _probe_tools(litellm_model, probe_kw))
    result.update(await _probe_vision(litellm_model, probe_kw, flat_url, api_type))
    return result

@staticmethod
async def _probe_tools(
        litellm_model: str, probe_kw: Dict[str, Any],
) -> Dict[str, Any]:
    test_tool = [{
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "获取当前时间",
            "parameters": {"type": "object", "properties": {}},
        },
    }]
    try:
        resp = await litellm.acompletion(
            model=litellm_model,
            messages=[{"role": "user", "content": "现在几点了？请调用工具获取。"}],
            tools=test_tool,
            tool_choice="auto",
            max_tokens=2048,
            **probe_kw,
        )
        has_calls = bool(resp.choices[0].message.tool_calls)
        return {
            "supports_tools": True,
            "tools_detail": (
                "模型返回了 tool_calls，支持原生工具调用"
                if has_calls
                else "请求成功（模型接受了 tools 参数）"
            ),
        }
    except Exception as exc:
        status = getattr(exc, "status_code", "")
        detail = f"不支持 (HTTP {status})" if status else f"检测失败: {exc}"
        return {"supports_tools": False, "tools_detail": detail}

_BASE64_ONLY_TYPES = frozenset({API_TYPE_OLLAMA})
# 已知支持 URL 形式图片输入的官方端点类型（探测只实测 base64，URL 按供应商能力推定）
_URL_VISION_KNOWN_TYPES = frozenset({
    API_TYPE_OPENAI, API_TYPE_AZURE, API_TYPE_GEMINI, API_TYPE_ANTHROPIC,
})

@staticmethod
async def _probe_vision(
        litellm_model: str, probe_kw: Dict[str, Any],
        flat_url: bool, api_type: str,
) -> Dict[str, Any]:
    import base64

    b64_img = base64.b64encode(_make_test_png()).decode()
    data_uri = f"data:image/png;base64,{b64_img}"
    img_value: Any = data_uri if flat_url else {"url": data_uri}
    vision_content: list[dict] = [
        {"type": "text", "text": "这张图片是什么颜色？用一个词回答。"},
        {"type": "image_url", "image_url": img_value},
    ]
    try:
        resp = await litellm.acompletion(
            model=litellm_model,
            messages=[{"role": "user", "content": vision_content}],
            max_tokens=256,
            **probe_kw,
        )
        answer = resp.choices[0].message.content or ""
        # 探测仅实测了 base64（data URI）形式；URL 形式只对已知官方
        # OpenAI 兼容端点推定支持，自定义/代理端点保守按 base64，
        # 避免把 URL 图片直发给不支持的端点导致 400
        if api_type in _BASE64_ONLY_TYPES:
            fmt = "base64"
        elif api_type in _URL_VISION_KNOWN_TYPES:
            fmt = "both"
        else:
            fmt = "base64"
        return {
            "supports_vision": True,
            "vision_detail": f"模型正确处理了图片输入: \"{answer[:80]}\"",
            "vision_format": fmt,
        }
    except Exception as exc:
        status = getattr(exc, "status_code", "")
        detail = f"不支持 (HTTP {status})" if status else f"不支持: {exc}"
        return {"supports_vision": False, "vision_detail": detail}
