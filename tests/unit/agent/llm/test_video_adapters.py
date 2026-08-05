"""视频生成协议适配器（agent.llm.video_adapters）单元测试。"""

from __future__ import annotations

import pytest

from agent.llm.video_adapters import (
    DashScopeVideoAdapter,
    MiniMaxV1Adapter,
    MiniMaxV2Adapter,
    OpenAIVideoAdapter,
    VideoGenParams,
    resolve_video_adapter,
)


def _params(**overrides: object) -> VideoGenParams:
    base: dict = {"model": "m", "prompt": "p"}
    base.update(overrides)
    return VideoGenParams(**base)  # type: ignore[arg-type]


class TestOpenAIVideoAdapter:
    def test_create_request(self) -> None:
        adapter = OpenAIVideoAdapter()
        req = adapter.build_create_request(
            "https://api.siliconflow.cn/v1",
            _params(first_frame_image="https://x/a.png"),
        )
        assert req.url == "https://api.siliconflow.cn/v1/videos/generations"
        assert req.method == "POST"
        assert req.payload == {
            "model": "m", "prompt": "p", "image_url": "https://x/a.png",
        }

    def test_extract_task_id_and_sync_url(self) -> None:
        adapter = OpenAIVideoAdapter()
        assert adapter.extract_task_id({"requestId": "r1"}) == "r1"
        assert adapter.extract_task_id({"id": "r2"}) == "r2"
        assert adapter.extract_task_id({"video": {"url": "https://x/v.mp4"}}) == ""
        assert adapter.extract_sync_url({"video": {"url": "https://x/v.mp4"}}) == "https://x/v.mp4"

    def test_parse_query_result(self) -> None:
        adapter = OpenAIVideoAdapter()
        ok = adapter.parse_query_result({"status": "succeeded", "video": {"url": "https://x/v.mp4"}})
        assert ok.status == "succeeded"
        assert ok.video_url == "https://x/v.mp4"
        fail = adapter.parse_query_result({"status": "failed"})
        assert fail.status == "failed"
        running = adapter.parse_query_result({"status": "running"})
        assert running.status == "processing"

    def test_task_management_unsupported(self) -> None:
        adapter = OpenAIVideoAdapter()
        with pytest.raises(NotImplementedError):
            adapter.build_list_request("https://x/v1", page_num=1, page_size=20)
        with pytest.raises(NotImplementedError):
            adapter.build_delete_request("https://x/v1", "t1")


class TestMiniMaxV1Adapter:
    def test_create_text_to_video(self) -> None:
        adapter = MiniMaxV1Adapter()
        req = adapter.build_create_request(
            "https://api.minimaxi.com/v1",
            _params(model="MiniMax-Hailuo-02", duration=6, resolution="768P"),
        )
        assert req.url == "https://api.minimaxi.com/v1/video_generation"
        assert req.payload == {
            "model": "MiniMax-Hailuo-02", "prompt": "p",
            "duration": 6, "resolution": "768P",
        }

    def test_create_with_frames_and_subject(self) -> None:
        """v2 协议整体支持首尾帧+主体参考，组装 payload 字段应正确。"""
        adapter = MiniMaxV2Adapter()
        req = adapter.build_create_request(
            "https://api.minimaxi.com",
            _params(
                model="MiniMax-H3",
                first_frame_image="data:image/png;base64,AAA",
                last_frame_image="https://x/b.png",
                subject_reference=["https://x/c.png"],
                duration=6,
            ),
        )
        payload = req.payload or {}
        assert payload["content"][0] == {"type": "text", "text": "p"}
        items = payload["content"][1:]
        assert [item["role"] for item in items] == ["first_frame", "last_frame", "reference_image"]
        for item in items:
            assert isinstance(item["image_url"], dict)
            assert item["image_url"].get("url")

    def test_extract_task_id_checks_base_resp(self) -> None:
        adapter = MiniMaxV1Adapter()
        ok = {"task_id": "t1", "base_resp": {"status_code": 0, "status_msg": "success"}}
        assert adapter.extract_task_id(ok) == "t1"
        err = {"base_resp": {"status_code": 1008, "status_msg": "余额不足"}}
        with pytest.raises(RuntimeError, match="1008"):
            adapter.extract_task_id(err)

    def test_parse_query_result(self) -> None:
        adapter = MiniMaxV1Adapter()
        processing = adapter.parse_query_result(
            {"status": "Processing", "base_resp": {"status_code": 0}}
        )
        assert processing.status == "processing"
        success = adapter.parse_query_result(
            {"status": "Success", "file_id": 123, "base_resp": {"status_code": 0}}
        )
        assert success.status == "succeeded"
        assert success.file_id == "123"
        fail = adapter.parse_query_result(
            {"status": "Fail", "base_resp": {"status_code": 1027, "status_msg": "敏感内容"}}
        )
        assert fail.status == "failed"
        assert "敏感内容" in fail.error

    def test_retrieve_request_and_download_url(self) -> None:
        adapter = MiniMaxV1Adapter()
        req = adapter.build_retrieve_request("https://api.minimaxi.com/v1", "123")
        assert req.url == "https://api.minimaxi.com/v1/files/retrieve"
        assert req.method == "GET"
        assert req.params == {"file_id": "123"}
        result = {
            "file": {"file_id": 123, "download_url": "https://cdn/x.mp4"},
            "base_resp": {"status_code": 0},
        }
        assert adapter.extract_download_url(result) == "https://cdn/x.mp4"

    def test_first_last_frame_rejected_by_v1(self) -> None:
        """v1 协议类属性 supports_first_last_frame=False，传 last_frame 直接拒收。"""
        adapter = MiniMaxV1Adapter()
        assert adapter.supports_first_last_frame is False
        with pytest.raises(RuntimeError, match="当前视频协议不支持首尾帧"):
            adapter.build_create_request(
                "https://api.minimaxi.com/v1",
                _params(
                    model="MiniMax-Hailuo-2.3",
                    first_frame_image="https://x/a.png",
                    last_frame_image="https://x/b.png",
                ),
            )


class TestMiniMaxV2Adapter:
    def test_first_last_frame_accepted_by_v2(self) -> None:
        """v2 协议类属性 supports_first_last_frame=True，正常构建。"""
        adapter = MiniMaxV2Adapter()
        assert adapter.supports_first_last_frame is True
        req = adapter.build_create_request(
            "https://api.minimaxi.com/v1",
            _params(
                model="MiniMax-H3",
                first_frame_image="https://x/a.png",
                last_frame_image="https://x/b.png",
                duration=6,
            ),
        )
        # v2 用 content 数组组装多模态，验证对象结构
        assert req.payload["content"][0] == {"type": "text", "text": "p"}
        items = req.payload["content"][1:]
        assert [item["role"] for item in items] == ["first_frame", "last_frame"]
        for item in items:
            assert isinstance(item["image_url"], dict)
            assert item["image_url"].get("url")

    def test_create_text_to_video(self) -> None:
        adapter = MiniMaxV2Adapter()
        req = adapter.build_create_request(
            "https://api.minimaxi.com/v1",
            _params(model="MiniMax-H3", duration=8, ratio="9:16"),
        )
        assert req.url == "https://api.minimaxi.com/v2/video_generation"
        payload = req.payload or {}
        assert payload["model"] == "MiniMax-H3"
        assert payload["content"] == [{"type": "text", "text": "p"}]
        assert payload["resolution"] == "2K"
        assert payload["duration"] == 8
        assert payload["ratio"] == "9:16"

    def test_image_url_must_be_object(self) -> None:
        """v2 的 content[].image_url 必须是对象 {url: ...}，不能是字符串。"""
        adapter = MiniMaxV2Adapter()
        req = adapter.build_create_request(
            "https://api.minimaxi.com/v1",
            _params(model="MiniMax-H3", first_frame_image="https://cdn/hero.png", duration=6),
        )
        item = req.payload["content"][1]
        assert item["type"] == "image_url"
        assert item["image_url"] == {"url": "https://cdn/hero.png"}
        assert item["role"] == "first_frame"
        # 有首帧 → 图生视频，比例自动 adaptive
        assert req.payload["ratio"] == "adaptive"
        assert req.payload["duration"] == 6

    def test_create_image_to_video_forces_adaptive_ratio(self) -> None:
        adapter = MiniMaxV2Adapter()
        req = adapter.build_create_request(
            "https://api.minimaxi.com",
            _params(
                model="MiniMax-H3",
                first_frame_image="https://x/a.png",
                last_frame_image="https://x/b.png",
                subject_reference=["https://x/c.png"],
                ratio="16:9",
                duration=6,
            ),
        )
        payload = req.payload or {}
        assert payload["ratio"] == "adaptive"
        # v2 content[].image_url 必须是对象 {url: ...}，不是字符串
        frames = payload["content"][1:]
        assert [item["role"] for item in frames] == ["first_frame", "last_frame", "reference_image"]
        for item in frames:
            assert isinstance(item["image_url"], dict)
            assert item["image_url"].get("url")

    def test_parse_query_result(self) -> None:
        adapter = MiniMaxV2Adapter()
        queued = adapter.parse_query_result({"task": {"status": "queued"}})
        assert queued.status == "processing"
        ok = adapter.parse_query_result(
            {"task": {"status": "succeeded", "content": {"url": "https://cdn/x.mp4"}}}
        )
        assert ok.status == "succeeded"
        assert ok.video_url == "https://cdn/x.mp4"
        fail = adapter.parse_query_result(
            {"task": {"status": "failed", "error": {"code": "1026", "message": "敏感内容"}}}
        )
        assert fail.status == "failed"
        assert "敏感内容" in fail.error

    def test_error_envelope_raises(self) -> None:
        adapter = MiniMaxV2Adapter()
        err = {"type": "error", "error": {"type": "authorized_error", "message": "invalid key"}}
        with pytest.raises(RuntimeError, match="authorized_error"):
            adapter.extract_task_id(err)

    def test_duration_required(self) -> None:
        """H3 v2 必填 duration，未传应明确报错。"""
        adapter = MiniMaxV2Adapter()
        with pytest.raises(ValueError, match="duration 必填"):
            adapter.build_create_request(
                "https://api.minimaxi.com/v1",
                _params(model="MiniMax-H3", prompt="p"),
            )

    def test_duration_out_of_range(self) -> None:
        """H3 v2 限定 4~15 秒，越界直接报错。"""
        adapter = MiniMaxV2Adapter()
        with pytest.raises(ValueError, match="超出.*允许范围"):
            adapter.build_create_request(
                "https://api.minimaxi.com/v1",
                _params(model="MiniMax-H3", duration=3),
            )
        with pytest.raises(ValueError, match="超出.*允许范围"):
            adapter.build_create_request(
                "https://api.minimaxi.com/v1",
                _params(model="MiniMax-H3", duration=30),
            )

    def test_list_request_and_result(self) -> None:
        adapter = MiniMaxV2Adapter()
        req = adapter.build_list_request(
            "https://api.minimaxi.com/v1", page_num=2, page_size=10, status="succeeded",
        )
        assert req.url == "https://api.minimaxi.com/v2/query/video_generation"
        assert req.method == "GET"
        assert req.params == {"page_num": 2, "page_size": 10, "filter.status": "succeeded"}
        parsed = adapter.parse_list_result({"items": [{"id": "t1"}], "total": 1})
        assert parsed["total"] == 1
        assert parsed["items"][0]["id"] == "t1"

    def test_delete_request_and_result(self) -> None:
        adapter = MiniMaxV2Adapter()
        req = adapter.build_delete_request("https://api.minimaxi.com", "t1")
        assert req.url == "https://api.minimaxi.com/v2/video_generation/t1"
        assert req.method == "DELETE"
        parsed = adapter.parse_delete_result(
            {"task_id": "t1", "action": "cancel", "status": "cancelled"}
        )
        assert parsed == {"task_id": "t1", "action": "cancel", "status": "cancelled"}


class TestResolveVideoAdapter:
    def test_explicit_protocol(self) -> None:
        assert resolve_video_adapter("https://x/v1", "minimax_v2").name == "minimax_v2"
        assert resolve_video_adapter("https://x/v1", "openai").name == "openai"

    def test_unknown_protocol_falls_back_to_host_and_default(self) -> None:
        """media_protocol 为多类媒体协议共用：非视频协议名不视为错误，回退 host/兜底。"""
        # host 命中 minimax → 按模型分流
        assert resolve_video_adapter(
            "https://api.minimaxi.com", "siliconflow", "MiniMax-H3",
        ).name == "minimax_v2"
        # host 也未命中 → 默认适配器
        assert resolve_video_adapter("https://x/v1", "nope").name == "openai"

    def test_minimax_protocol_split_by_model(self) -> None:
        assert resolve_video_adapter("https://x/v1", "minimax", "MiniMax-H3").name == "minimax_v2"
        assert resolve_video_adapter("https://x/v1", "minimax", "MiniMax-Hailuo-02").name == "minimax"

    def test_host_rule_split_by_model(self) -> None:
        h3 = resolve_video_adapter("https://api.minimaxi.com/v1", model="MiniMax-H3")
        assert h3.name == "minimax_v2"
        hailuo = resolve_video_adapter("https://api.minimaxi.com/v1", model="T2V-01")
        assert hailuo.name == "minimax"

    def test_default_fallback(self) -> None:
        assert resolve_video_adapter("https://api.siliconflow.cn/v1").name == "openai"


class TestDashScopeVideoAdapter:
    def test_create_text_to_video(self) -> None:
        adapter = DashScopeVideoAdapter()
        req = adapter.build_create_request(
            "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            _params(model="happyhorse-1.1-t2v", resolution="720P", ratio="16:9", duration=5),
        )
        assert req.url == (
            "https://token-plan.cn-beijing.maas.aliyuncs.com"
            "/api/v1/services/aigc/video-generation/video-synthesis"
        )
        assert req.method == "POST"
        assert req.headers == {"X-DashScope-Async": "enable"}
        assert req.payload == {
            "model": "happyhorse-1.1-t2v",
            "input": {"prompt": "p"},
            "parameters": {"resolution": "720P", "duration": 5, "ratio": "16:9"},
        }

    def test_create_image_to_video_omits_ratio(self) -> None:
        """i2v 输出比例跟随首帧图，协议无 ratio 参数，首帧经 media.first_frame 传入。"""
        adapter = DashScopeVideoAdapter()
        req = adapter.build_create_request(
            "https://dashscope.aliyuncs.com/api/v1",
            _params(
                model="happyhorse-1.1-i2v",
                first_frame_image="data:image/png;base64,AAA",
                ratio="16:9",
            ),
        )
        assert req.payload == {
            "model": "happyhorse-1.1-i2v",
            "input": {
                "prompt": "p",
                "media": [{"type": "first_frame", "url": "data:image/png;base64,AAA"}],
            },
        }

    def test_create_reference_to_video(self) -> None:
        """r2v 参考图经 media.reference_image 传入，支持多张。"""
        adapter = DashScopeVideoAdapter()
        req = adapter.build_create_request(
            "https://dashscope.aliyuncs.com/api/v1",
            _params(
                model="happyhorse-1.1-r2v",
                subject_reference=["https://x/a.png", "https://x/b.png"],
            ),
        )
        assert req.payload is not None
        assert req.payload["input"]["media"] == [
            {"type": "reference_image", "url": "https://x/a.png"},
            {"type": "reference_image", "url": "https://x/b.png"},
        ]

    def test_extract_task_id(self) -> None:
        adapter = DashScopeVideoAdapter()
        assert adapter.extract_task_id({"output": {"task_id": "t1", "task_status": "PENDING"}}) == "t1"
        assert adapter.extract_task_id({"output": {}}) == ""

    def test_query_request_and_parse(self) -> None:
        adapter = DashScopeVideoAdapter()
        req = adapter.build_query_request("https://dashscope.aliyuncs.com/api/v1", "t1")
        assert req.url == "https://dashscope.aliyuncs.com/api/v1/tasks/t1"
        assert req.method == "GET"

        ok = adapter.parse_query_result(
            {"output": {"task_status": "SUCCEEDED", "video_url": "https://x/v.mp4"}}
        )
        assert ok.status == "succeeded"
        assert ok.video_url == "https://x/v.mp4"

        running = adapter.parse_query_result({"output": {"task_status": "RUNNING"}})
        assert running.status == "processing"

        failed = adapter.parse_query_result(
            {"output": {"task_status": "FAILED", "message": "content filter"}}
        )
        assert failed.status == "failed"
        assert failed.error == "content filter"

        no_url = adapter.parse_query_result({"output": {"task_status": "SUCCEEDED"}})
        assert no_url.status == "failed"

    def test_host_rule(self) -> None:
        adapter = resolve_video_adapter(
            "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            model="happyhorse-1.1-t2v",
        )
        assert adapter.name == "dashscope"
