"""WebUI API 冒烟管线（env-gated 默认 skip）。

对一台运行中的 WebUI 服务做只读端点全量探测，守护三类不变量：

1. **文档端点健康**：/openapi.json、/docs、/redoc 可用且 schema 完整
   （历史缺陷：build_router() 局部类型在 postponed annotations 下无法解析，
   曾把 /openapi.json 与 /docs 整体打成 500，本管线做回归门）；
2. **无裸 5xx**：任何 GET 端点允许优雅降级（未配置外部服务时返回
   带 ``detail`` 的结构化 JSON 错误），但不允许裸 "Internal Server Error"——
   未捕获异常直穿 ASGI 即为本管线要抓的缺陷类别；
3. **路由可达**：schema 中不带路径参数的 GET 端点全部可请求、单请求
   15s 内有响应。

运行（需要先启动服务，如 ``uv run python launch.py``）：

    WEBUI_SMOKE=1 uv run pytest tests/integration/test_webui_api_smoke.py
    # 自定义地址：
    WEBUI_SMOKE=1 WEBUI_SMOKE_URL=http://127.0.0.1:8092 uv run pytest ...

不做 POST/写操作（服务可能是生产实例）；认证场景（webui.json 设密码时）
同样支持——未配置密码时直接探测，CI 无运行服务默认跳过。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Tuple

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("WEBUI_SMOKE") != "1",
    reason="需要运行中的 WebUI 服务，WEBUI_SMOKE=1 显式开启",
)

# P1 回归门：这些路由曾因局部类型无法解析而从 schema 中"崩掉"整份 openapi
_REGRESSION_ROUTES = (
    "/api/channels/github/webhook",
    "/api/channels/github/subscriptions",
    "/api/entity/sillytavern/git/checkout",
    "/api/entity/sillytavern/characters/create",
    "/api/entity/sillytavern/chat/send",
)

# SSE/WebSocket 等流式端点不适合短超时探测
_SKIP_PATTERNS = ("stream", "/sse", "/events", "/ws")

_REQUEST_TIMEOUT = 15.0
# openapi.json 约 450KB，错误体远小于此；上限只为防御异常超大响应
_MAX_BODY_BYTES = 8 * 1024 * 1024


def _base_url() -> str:
    return os.environ.get("WEBUI_SMOKE_URL", "http://127.0.0.1:8092").rstrip("/")


def _get(path: str) -> Tuple[Any, int, str]:
    """GET 一个端点，返回 (解析后的 body, 状态码, 原始文本前 256 字符)。"""
    req = urllib.request.Request(_base_url() + path, headers={"User-Agent": "webui-api-smoke"})
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            text = resp.read(_MAX_BODY_BYTES).decode("utf-8", "replace")
            return _try_json(text), resp.status, text[:256]
    except urllib.error.HTTPError as exc:
        text = exc.read(_MAX_BODY_BYTES).decode("utf-8", "replace")
        return _try_json(text), exc.code, text[:256]


def _try_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _is_clean_error(body: Any) -> bool:
    """判断错误响应是否为结构化领域错误（FastAPI detail / JSON dict）。"""
    return isinstance(body, dict) and bool(body)


def test_health_endpoint() -> None:
    body, status, _ = _get("/health")
    assert status == 200, f"/health 状态码 {status}"
    assert body == {"status": "ok"}


def test_docs_and_schema_available() -> None:
    for path in ("/docs", "/redoc"):
        _, status, _ = _get(path)
        assert status == 200, f"{path} 状态码 {status}（/openapi.json 生成失败会连带 500）"


def test_openapi_schema_complete() -> None:
    body, status, raw = _get("/openapi.json")
    assert status == 200, f"/openapi.json 状态码 {status}: {raw[:120]}"
    assert isinstance(body, dict), "openapi.json 必须是 JSON 对象"
    paths = body.get("paths", {})
    assert len(paths) >= 300, f"schema 路径数异常偏少: {len(paths)}"
    missing = [r for r in _REGRESSION_ROUTES if r not in paths]
    assert not missing, f"P1 回归：以下路由未出现在 schema 中: {missing}"


def test_all_param_free_get_endpoints() -> None:
    """全量 GET 冒烟：禁止裸 5xx，结构化降级放行。"""
    schema, status, raw = _get("/openapi.json")
    assert status == 200, f"无法获取路由清单: {raw[:120]}"
    paths: Dict[str, Any] = schema.get("paths", {})
    targets = [
        p for p, ops in paths.items()
        if "get" in ops and "{" not in p
        and not any(k in p.lower() for k in _SKIP_PATTERNS)
    ]
    assert len(targets) >= 100, f"可探测 GET 端点过少: {len(targets)}"

    def probe(path: str) -> Tuple[str, int, Any, str, float]:
        t0 = time.monotonic()
        body, code, raw_text = _get(path)
        return path, code, body, raw_text, time.monotonic() - t0

    results: List[Tuple[str, int, Any, str, float]] = []
    with ThreadPoolExecutor(max_workers=12) as pool:
        for item in pool.map(probe, targets):
            results.append(item)

    dirty = [
        (p, code, raw)
        for p, code, body, raw, _dt in results
        if code >= 500 and not _is_clean_error(body)
    ]
    summary = "\n".join(
        f"  [{code}] {p}  body={raw[:80]!r}" for p, code, raw in sorted(dirty)
    )
    assert not dirty, (
        f"{len(dirty)} 个端点出现裸 5xx（未捕获异常直穿 ASGI）：\n{summary}"
    )

    degraded = [p for p, code, body, _r, _d in results if code >= 400]
    slow = [(p, round(dt, 1)) for p, _c, _b, _r, dt in results if dt > 10.0]
    print(f"\n[smoke] GET {len(results)} 个端点: "
          f"{len(results) - len(degraded)} 成功, {len(degraded)} 结构化降级, "
          f"{len(slow)} 个慢端点(>10s): {slow}")
