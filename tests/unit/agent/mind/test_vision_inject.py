"""视觉模型图片直传（apply_vision / _inject_image_blocks / 媒体规则）单元测试。"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Dict, List

import pytest

from agent.llm.llm_client import LLMClientConfig
from agent.llm.types import ImageContent
from agent.mind.prefrontal_cortex import PrefrontalCortex
from agent.mind.tools.think_loop import apply_vision

_TINY_B64 = base64.b64encode(b"tiny-image-bytes").decode("utf-8")


def _make_mind(config: LLMClientConfig) -> SimpleNamespace:
    return SimpleNamespace(llm=SimpleNamespace(config=config))


def _make_messages() -> List[Dict]:
    return [
        {"role": "system", "content": "stable"},
        {"role": "user", "content": "看图 [media_type:image][media_path:/tmp/a.jpg]"},
    ]


class TestApplyVisionDirectInject:
    """视觉模型：图片以多模态 block 注入最后一条 user 消息。"""

    async def test_base64_image_injected_as_block(self) -> None:
        config = LLMClientConfig(name="v", supports_vision=True, vision_format="base64")
        messages = _make_messages()
        result = await apply_vision(
            _make_mind(config), messages, [ImageContent(data=_TINY_B64)],
        )

        content = result[-1]["content"]
        assert isinstance(content, list)
        assert content[0] == {
            "type": "text",
            "text": "看图 [media_type:image][media_path:/tmp/a.jpg]",
        }
        assert content[1] == {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{_TINY_B64}"},
        }
        # 不污染原列表
        assert isinstance(messages[-1]["content"], str)

    async def test_url_image_downloaded_first(
            self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """URL 图片下载优先：即使模型支持 url 视觉，也先下载转 base64。"""

        async def _fake_download(url: str, timeout: float = 30.0) -> ImageContent:
            return ImageContent(data=_TINY_B64, mime_type="image/jpeg")

        monkeypatch.setattr(
            "agent.llm.image_utils.download_image_to_base64", _fake_download,
        )
        config = LLMClientConfig(name="v", supports_vision=True, vision_format="both")
        result = await apply_vision(
            _make_mind(config), _make_messages(),
            [ImageContent(data="https://example.com/a.jpg", is_url=True)],
        )

        block = result[-1]["content"][1]
        assert block["image_url"]["url"].startswith("data:image/jpeg;base64,")

    async def test_url_image_kept_as_fallback_when_download_fails(
            self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """下载失败时保留原 URL 交由端点兜底。"""

        async def _fail_download(url: str, timeout: float = 30.0) -> None:
            return None

        monkeypatch.setattr(
            "agent.llm.image_utils.download_image_to_base64", _fail_download,
        )
        config = LLMClientConfig(name="v", supports_vision=True, vision_format="both")
        result = await apply_vision(
            _make_mind(config), _make_messages(),
            [ImageContent(data="https://example.com/a.jpg", is_url=True)],
        )

        block = result[-1]["content"][1]
        assert block == {
            "type": "image_url",
            "image_url": {"url": "https://example.com/a.jpg"},
        }

    async def test_url_image_downloaded_for_base64_only_model(
            self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def _fake_download(url: str, timeout: float = 30.0) -> ImageContent:
            return ImageContent(data=_TINY_B64, mime_type="image/jpeg")

        monkeypatch.setattr(
            "agent.llm.image_utils.download_image_to_base64", _fake_download,
        )
        config = LLMClientConfig(name="v", supports_vision=True, vision_format="base64")
        result = await apply_vision(
            _make_mind(config), _make_messages(),
            [ImageContent(data="https://example.com/a.jpg", is_url=True)],
        )

        block = result[-1]["content"][1]
        assert block["image_url"]["url"].startswith("data:image/jpeg;base64,")

    async def test_flat_url_for_ollama(self) -> None:
        config = LLMClientConfig(
            name="v", api_type="ollama", supports_vision=True, vision_format="base64",
        )
        result = await apply_vision(
            _make_mind(config), _make_messages(), [ImageContent(data=_TINY_B64)],
        )

        block = result[-1]["content"][1]
        assert block == {
            "type": "image_url",
            "image_url": f"data:image/jpeg;base64,{_TINY_B64}",
        }

    async def test_appends_to_existing_list_content(self) -> None:
        config = LLMClientConfig(name="v", supports_vision=True, vision_format="base64")
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "已有多模态"}]},
        ]
        result = await apply_vision(
            _make_mind(config), messages, [ImageContent(data=_TINY_B64)],
        )

        content = result[-1]["content"]
        assert content[0] == {"type": "text", "text": "已有多模态"}
        assert content[1]["type"] == "image_url"

    async def test_no_user_message_returns_unchanged(self) -> None:
        config = LLMClientConfig(name="v", supports_vision=True, vision_format="base64")
        messages = [{"role": "system", "content": "only system"}]
        result = await apply_vision(
            _make_mind(config), messages, [ImageContent(data=_TINY_B64)],
        )
        assert result[0]["content"] == "only system"


class TestApplyVisionNonVisionModel:
    """非视觉模型：维持原有 base64 转存文件路径行为。"""

    async def test_large_base64_saved_to_file(
            self, monkeypatch: pytest.MonkeyPatch, tmp_path,
    ) -> None:
        saved = str(tmp_path / "vision_saved.jpg")
        monkeypatch.setattr(
            "agent.mind.tools.think_loop.save_base64_image",
            lambda data, mime_type="image/jpeg": saved,
        )
        big_b64 = base64.b64encode(b"x" * 600).decode("utf-8")
        config = LLMClientConfig(name="t", supports_vision=False)
        messages = [
            {"role": "user", "content": f"看图 [media_path:{big_b64}]"},
        ]
        result = await apply_vision(
            _make_mind(config), messages, [ImageContent(data=big_b64)],
        )

        assert result[-1]["content"] == f"看图 [media_path:{saved}]"

    async def test_small_images_untouched(self) -> None:
        config = LLMClientConfig(name="t", supports_vision=False)
        messages = _make_messages()
        result = await apply_vision(
            _make_mind(config), messages,
            [ImageContent(data="/tmp/a.jpg"), ImageContent(data="https://x.com/a.jpg", is_url=True)],
        )
        assert result[-1]["content"] == messages[-1]["content"]


class TestMediaRulesDirectVision:
    """媒体处理规则文案静态一致（覆盖视觉/非视觉两种情形，不随模型切换变化）。"""

    @pytest.fixture(autouse=True)
    def _fake_registry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        entity = SimpleNamespace(
            entity_type=SimpleNamespace(value="tool"),
            enabled=True,
            tags=["media:image"],
            name="recognize_image",
        )
        monkeypatch.setattr(
            "agent.mind.context_assembly.EntityRegistry.get_all",
            staticmethod(lambda: [entity]),
        )

    def test_rules_static_across_vision_capability(self) -> None:
        """direct_vision 取值不影响文案（stable 层字节稳定）。"""
        rules_off = PrefrontalCortex._build_media_rules(direct_vision=False)
        rules_on = PrefrontalCortex._build_media_rules(direct_vision=True)
        assert rules_off == rules_on

    def test_static_text_covers_both_cases(self) -> None:
        """静态文案同时覆盖：已视觉呈现免工具 + 未呈现/深入分析用工具。"""
        rules = PrefrontalCortex._build_media_rules(direct_vision=True)
        assert "已直接以视觉形式呈现" in rules
        assert "recognize_image" in rules
        assert "必须优先使用" in rules


class TestUrlFallbackNotification:
    """URL 下载失败回退为 URL 直传时，必须注入文本通知 Agent（防幻觉描述）。"""

    async def test_fallback_notice_injected(
            self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def _fail_download(url: str, timeout: float = 30.0) -> None:
            return None

        monkeypatch.setattr(
            "agent.llm.image_utils.download_image_to_base64", _fail_download,
        )
        config = LLMClientConfig(name="v", supports_vision=True, vision_format="both")
        result = await apply_vision(
            _make_mind(config), _make_messages(),
            [ImageContent(data="https://example.com/a.jpg", is_url=True)],
        )

        content = result[-1]["content"]
        assert isinstance(content, list)
        # 图片 block 仍是 URL 直传
        assert content[1] == {
            "type": "image_url",
            "image_url": {"url": "https://example.com/a.jpg"},
        }
        # 尾部追加回退通知
        notice = content[-1]
        assert notice["type"] == "text"
        assert "下载失败" in notice["text"] and "URL" in notice["text"]

    async def test_no_notice_when_download_ok(
            self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def _fake_download(url: str, timeout: float = 30.0) -> ImageContent:
            return ImageContent(data=_TINY_B64, mime_type="image/jpeg")

        monkeypatch.setattr(
            "agent.llm.image_utils.download_image_to_base64", _fake_download,
        )
        config = LLMClientConfig(name="v", supports_vision=True, vision_format="both")
        result = await apply_vision(
            _make_mind(config), _make_messages(),
            [ImageContent(data="https://example.com/a.jpg", is_url=True)],
        )
        content = result[-1]["content"]
        assert all(b.get("type") != "text" or "下载失败" not in b.get("text", "")
                   for b in content)
