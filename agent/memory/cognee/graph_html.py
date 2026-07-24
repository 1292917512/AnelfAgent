"""Cognee 图谱 HTML 后处理：剥离外网 CDN，保证 WebUI iframe 可离线渲染。"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

_D3_SCRIPT_RE = re.compile(
    r'<script\s+src=["\']https?://d3js\.org/d3(?:\.v\d+)?\.min\.js["\']\s*>\s*</script>',
    re.IGNORECASE,
)
_D3_CDN_FALLBACK_RE = re.compile(
    r'<script\s+src=["\']https?://[^"\']*d3[^"\']*\.min\.js["\']\s*>\s*</script>',
    re.IGNORECASE,
)
_GOOGLE_FONT_LINK_RE = re.compile(
    r'<link[^>]+(?:fonts\.googleapis\.com|fonts\.gstatic\.com)[^>]*>\s*',
    re.IGNORECASE,
)

_VENDOR_D3 = (
    Path(__file__).resolve().parents[3] / "web" / "static" / "vendor" / "d3.v7.min.js"
)


@lru_cache(maxsize=1)
def _d3_source() -> str:
    if not _VENDOR_D3.is_file():
        raise FileNotFoundError(f"缺少本地 d3 资源: {_VENDOR_D3}")
    return _VENDOR_D3.read_text(encoding="utf-8")


def sanitize_cognee_graph_html(html: str) -> str:
    """将 Cognee 图谱 HTML 改为自包含：内联本地 d3，去掉 Google Fonts。

    Cognee 官方模板依赖 d3js.org / fonts.googleapis.com；在国内网络下 CDN
    常 403 或挂起，导致 iframe 一直等子资源、``onLoad`` 不触发，WebUI 卡在
    「图谱渲染中」。
    """
    text = str(html)
    inline = f"<script>{_d3_source()}</script>"

    def _inject(_: re.Match[str]) -> str:
        return inline

    replaced, count = _D3_SCRIPT_RE.subn(_inject, text, count=1)
    if count == 0:
        replaced, count = _D3_CDN_FALLBACK_RE.subn(_inject, text, count=1)
    if count == 0:
        raise RuntimeError("Cognee 图谱 HTML 未找到可替换的 d3 脚本标签")
    return _GOOGLE_FONT_LINK_RE.sub("", replaced)
