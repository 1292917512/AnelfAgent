"""图片视觉前处理（optimize_for_vision）单元测试。

覆盖格式重编码：QQ 图片常见的 MPO 容器（.jpg 后缀、MPO 内容）会被视觉 API
拒绝（Unsupported image format: mpo），必须取首帧重编码为 JPEG；
标准 JPEG/PNG 小图原样透传，动图不重编码。
"""

from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from agent.llm.image_utils import load_image_from_bytes, optimize_for_vision


def _make_image_bytes(fmt: str, size: tuple[int, int] = (100, 100), *, animated: bool = False) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", size, (255, 0, 0))
    if fmt == "MPO":
        img.save(buf, format="MPO", save_all=True, append_images=[Image.new("RGB", (10, 10))])
    elif animated:
        img.save(buf, format=fmt, save_all=True, append_images=[Image.new("RGB", size, (0, 255, 0))])
    else:
        img.save(buf, format=fmt)
    return buf.getvalue()


def _format_of(b64_data: str) -> str:
    return Image.open(io.BytesIO(base64.b64decode(b64_data))).format or ""


class TestFormatReencode:
    def test_mpo_reencoded_to_jpeg(self) -> None:
        """MPO 容器（QQ 图片）取首帧重编码为 JPEG。"""
        mpo = _make_image_bytes("MPO", (800, 600))
        assert _format_of(base64.b64encode(mpo).decode()) == "MPO"

        out = optimize_for_vision(load_image_from_bytes(mpo, "image/jpeg"))
        assert _format_of(out.data) == "JPEG"
        assert out.mime_type == "image/jpeg"

    def test_bmp_reencoded_to_jpeg(self) -> None:
        """BMP 等视觉 API 不支持的格式重编码为 JPEG。"""
        bmp = _make_image_bytes("BMP")
        out = optimize_for_vision(load_image_from_bytes(bmp, "image/bmp"))
        assert _format_of(out.data) == "JPEG"

    def test_small_jpeg_passthrough(self) -> None:
        """标准小 JPEG 原样返回，不重复编码。"""
        raw = _make_image_bytes("JPEG")
        img = load_image_from_bytes(raw, "image/jpeg")
        assert optimize_for_vision(img).data == img.data

    def test_small_png_passthrough(self) -> None:
        raw = _make_image_bytes("PNG")
        img = load_image_from_bytes(raw, "image/png")
        assert optimize_for_vision(img).data == img.data

    def test_animated_gif_passthrough(self) -> None:
        """动图不重编码（避免丢帧）。"""
        raw = _make_image_bytes("GIF", animated=True)
        img = load_image_from_bytes(raw, "image/gif")
        assert optimize_for_vision(img).data == img.data

    def test_oversized_jpeg_resized(self) -> None:
        """超限 JPEG 仍按既有逻辑缩放。"""
        raw = _make_image_bytes("JPEG", (3000, 2000))
        out = optimize_for_vision(load_image_from_bytes(raw, "image/jpeg"))
        check = Image.open(io.BytesIO(base64.b64decode(out.data)))
        assert max(check.size) <= 1568
        assert check.format == "JPEG"


_TINY_B64 = base64.b64encode(_make_image_bytes("JPEG", (64, 64))).decode("utf-8")


class TestEnsureBase64Report:
    """ensure_base64_report：逐张状态报告（ok / url_fallback / failed）。"""

    async def test_base64_passthrough_ok(self) -> None:
        from agent.llm.image_utils import ensure_base64_report
        from agent.llm.types import ImageContent
        prepared, fallbacks, failed = await ensure_base64_report(
            [ImageContent(data=_TINY_B64, mime_type="image/jpeg")],
        )
        assert len(prepared) == 1 and not fallbacks and not failed

    async def test_url_download_failure_reported_as_fallback(
            self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from agent.llm import image_utils
        from agent.llm.types import ImageContent

        async def _fail(url: str, timeout: float = 30.0):
            return None

        monkeypatch.setattr(image_utils, "download_image_to_base64", _fail)
        prepared, fallbacks, failed = await image_utils.ensure_base64_report(
            [ImageContent(data="https://example.com/x.jpg", is_url=True)],
        )
        # 原 URL 保留注入（端点兜底），但必须出现在 fallbacks 里供调用方告知 Agent
        assert len(prepared) == 1 and prepared[0].data == "https://example.com/x.jpg"
        assert len(fallbacks) == 1 and not failed

    async def test_local_path_failure_reported_as_failed(self) -> None:
        from agent.llm.image_utils import ensure_base64_report
        from agent.llm.types import ImageContent
        prepared, fallbacks, failed = await ensure_base64_report(
            [ImageContent(data="/nonexistent/dir/missing.jpg")],
        )
        assert not prepared and not fallbacks and len(failed) == 1

    async def test_mixed_images_parallel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """多图混合：成功/回退/失败分别归类，且互不阻塞。"""
        from agent.llm import image_utils
        from agent.llm.types import ImageContent

        async def _ok(url: str, timeout: float = 30.0):
            return ImageContent(data=_TINY_B64, mime_type="image/jpeg")

        monkeypatch.setattr(image_utils, "download_image_to_base64", _ok)
        prepared, fallbacks, failed = await image_utils.ensure_base64_report([
            ImageContent(data=_TINY_B64, mime_type="image/jpeg"),
            ImageContent(data="https://example.com/a.jpg", is_url=True),
            ImageContent(data="/nonexistent/missing.jpg"),
        ])
        assert len(prepared) == 2 and not fallbacks and len(failed) == 1

