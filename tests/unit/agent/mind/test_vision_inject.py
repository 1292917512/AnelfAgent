"""视觉模型图片直传（apply_vision / _inject_image_blocks / 媒体规则）单元测试。"""

from __future__ import annotations

import base64
import copy
import json
from types import SimpleNamespace
from typing import Dict, List

import pytest

from agent.context import providers
from agent.context.assembly import ContextAssembly
from agent.llm.llm_client import LLMClientConfig
from agent.llm.types import ImageContent
from agent.mind.execution.vision import _append_multimodal_result, apply_vision, split_multimodal_result

_TINY_B64 = base64.b64encode(b"tiny-image-bytes").decode("utf-8")


def _make_mind(config: LLMClientConfig) -> SimpleNamespace:
    return SimpleNamespace(models=SimpleNamespace(llm=SimpleNamespace(config=config)))


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
            "agent.mind.execution.vision.save_base64_image",
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
            "agent.context.assembly.EntityRegistry.get_all",
            staticmethod(lambda: [entity]),
        )

    def test_rules_static_across_vision_capability(self) -> None:
        """direct_vision 取值不影响文案（stable 层字节稳定）。"""
        rules_off = ContextAssembly._build_media_rules(direct_vision=False)
        rules_on = ContextAssembly._build_media_rules(direct_vision=True)
        assert rules_off == rules_on

    def test_static_text_covers_both_cases(self) -> None:
        """已有原图直接分析，缺图才加载，文案不随模型能力变化。"""
        rules = ContextAssembly._build_media_rules(direct_vision=True)
        assert "已直接以视觉形式呈现" in rules
        assert "recognize_image" in rules
        assert "必须优先使用" in rules
        assert "OCR、文字提取与细节分析也直接基于已有原图完成" in rules


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


class TestProviderMediaMessage:
    """provider 快照画面的 user 角色多模态注入消息构建（build_provider_media_message）。"""

    def _config(self) -> LLMClientConfig:
        return LLMClientConfig(name="v", supports_vision=True, vision_format="base64")

    def _img(self, tag: str) -> ImageContent:
        return ImageContent(data=base64.b64encode(tag.encode()).decode(), mime_type="image/jpeg")

    async def test_sampling_keeps_head_middle_tail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """多于上限时等距抽样保头/中/尾（信息量最高的是两端与中点）。"""

        async def _fake_report(images, *a, **kw):
            return images, [], []

        monkeypatch.setattr("agent.llm.image_utils.ensure_base64_report", _fake_report)
        images = [self._img(f"img{i}") for i in range(8)]
        msg = await providers.build_provider_media_message(images, self._config())

        assert msg is not None and msg["role"] == "user"
        assert msg["_layer"] == "provider"
        blocks = msg["content"]
        urls = [b["image_url"]["url"] for b in blocks if b["type"] == "image_url"]
        # 8 张 → 抽样 3 张：首(img0)/中(img4)/尾(img7)
        expected = [f"data:image/jpeg;base64,{base64.b64encode(f'img{i}'.encode()).decode()}"
                    for i in (0, 4, 7)]
        assert urls == expected
        caption = next(b["text"] for b in blocks if b["type"] == "text")
        assert "抽样展示 3 张" in caption

    async def test_url_fallback_becomes_url_block(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """下载失败的 URL 图以原 URL block 注入（端点兜底），caption 说明数量。"""

        async def _fake_report(images, *a, **kw):
            return [images[0], ImageContent(data="https://x.com/fail.png", is_url=True)], ["https://x.com/fail.png"], ["broken-local"]

        monkeypatch.setattr("agent.llm.image_utils.ensure_base64_report", _fake_report)
        msg = await providers.build_provider_media_message(
            [self._img("a"), self._img("b")], LLMClientConfig(name="v", supports_vision=True, vision_format="both"),
        )
        assert msg is not None
        urls = [b["image_url"]["url"] for b in msg["content"] if b["type"] == "image_url"]
        assert urls[-1] == "https://x.com/fail.png"
        assert urls.count("https://x.com/fail.png") == 1
        assert any("加载失败" in b.get("text", "") for b in msg["content"])

    async def test_all_failed_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """全部加载失败时不追加消息（调用方不注入空块）。"""

        async def _fake_report(images, *a, **kw):
            return [], [], ["x", "y"]

        monkeypatch.setattr("agent.llm.image_utils.ensure_base64_report", _fake_report)
        msg = await providers.build_provider_media_message([self._img("a")], self._config())
        assert msg is None


class TestToolImageInjection:
    @pytest.mark.parametrize("source_kind", ["path", "url", "data"])
    @pytest.mark.parametrize("api_type", ["openai", "ollama"])
    async def test_source_forms_share_loading_and_context_dedup(self, tmp_path, monkeypatch, source_kind, api_type):
        config = LLMClientConfig(name="v", supports_vision=True, api_type=api_type)
        mind = _make_mind(config)
        path = tmp_path / "image.jpg"
        path.write_bytes(base64.b64decode(_TINY_B64))
        sources = {"path": str(path), "url": "https://example.com/image.jpg",
                   "data": f"data:image/jpeg;base64,{_TINY_B64}"}
        async def download(url, timeout=30):
            return ImageContent(data=_TINY_B64)
        monkeypatch.setattr("agent.llm.image_utils.download_image_to_base64", download)
        output = json.dumps({"_multimodal": True, "images": [sources[source_kind]]})
        receipt, images = split_multimodal_result(output)
        assert _TINY_B64 not in receipt
        base = await apply_vision(mind, _make_messages(), [ImageContent(data=_TINY_B64)])
        original = copy.deepcopy(base)
        chain = []
        await _append_multimodal_result(mind, chain, images, existing_messages=base)
        assert base == original
        assert not any(b["type"] == "image_url" for m in chain for b in m["content"])
        assert "已在当前上下文中" in str(chain)
        chain.clear()
        await _append_multimodal_result(mind, chain, images, existing_messages=_make_messages())
        assert sum(b["type"] == "image_url" for m in chain for b in m["content"]) == 1
        assert chain[0]["_source"]["origin"] == "tool_media"
        if api_type == "ollama":
            assert isinstance(chain[0]["content"][1]["image_url"], str)

    async def test_repeated_source_loads_once_but_changed_file_is_presented(self, tmp_path):
        config = LLMClientConfig(name="v", supports_vision=True)
        mind = _make_mind(config)
        path = tmp_path / "screen.jpg"
        path.write_bytes(base64.b64decode(_TINY_B64))
        images = [ImageContent(data=str(path))] * 2
        chain = []
        await _append_multimodal_result(mind, chain, images)
        assert sum(b["type"] == "image_url" for b in chain[0]["content"]) == 1
        saved = copy.deepcopy(chain)
        path.write_bytes(b"changed-image-bytes")
        await _append_multimodal_result(mind, chain, images)
        assert chain[:1] == saved
        assert sum(b["type"] == "image_url" for b in chain[-1]["content"]) == 1

    @pytest.mark.parametrize("vision_format", ["base64", "both", "url"])
    async def test_download_failure_respects_transport_and_retains_full_url(self, monkeypatch, vision_format):
        url = "https://example.com/image?signature=" + "x" * 200
        calls = []
        async def download(source, timeout=30):
            calls.append(source)
            return None
        monkeypatch.setattr("agent.llm.image_utils.download_image_to_base64", download)
        config = LLMClientConfig(name="v", supports_vision=True, vision_format=vision_format)
        chain = []
        await _append_multimodal_result(_make_mind(config), chain, [ImageContent(data=url, is_url=True)])
        blocks = [b for m in chain for b in m["content"] if b["type"] == "image_url"]
        if vision_format == "base64":
            assert not blocks
            assert "未包含在消息中" in str(chain)
        else:
            assert [b["image_url"]["url"] for b in blocks] == [url]
        assert len(calls) == (0 if vision_format == "url" else 1)

    @pytest.mark.parametrize("source", ["data:image/png;base64,not-base64!", "/missing/unknown-image.jpg"])
    async def test_invalid_image_is_reported_without_an_image_block(self, source):
        chain = []
        await _append_multimodal_result(
            _make_mind(LLMClientConfig(name="v", supports_vision=True)), chain, [ImageContent(data=source)],
        )
        assert "加载失败" in str(chain)
        assert not any(b["type"] == "image_url" for m in chain for b in m["content"])

    async def test_nonvision_model_does_not_receive_image_blocks(self):
        chain = []
        await _append_multimodal_result(
            _make_mind(LLMClientConfig(name="t", supports_vision=False)), chain, [ImageContent(data=_TINY_B64)],
        )
        assert chain == []
