"""BaseChannel v2 抽象基类测试。"""

import json

import pytest

from agent.channel.base import BaseChannel, ChannelConfig, ChannelMetadata
from agent.channel.channel_types import ChannelCapability, ChannelStatus
from agent.channel.schemas import (
    AdapterChannel,
    ChannelInfo,
    ChannelType,
    ChannelUser,
    HealthStatus,
    SendRequest,
    SendResponse,
    SendSegment,
)


class DummyConfig(ChannelConfig):
    """测试用配置。"""


class DummyChannel(BaseChannel[DummyConfig]):
    """测试用频道（最简实现）。"""

    channel_id = "dummy"
    display_name = "Dummy"
    capabilities = {ChannelCapability.SEND_TEXT}
    metadata = ChannelMetadata(name="Dummy", description="Test", version="1.0.0")
    _Configs = DummyConfig

    async def start(self) -> None:
        self._status = ChannelStatus.RUNNING

    async def stop(self) -> None:
        self._status = ChannelStatus.STOPPED

    async def forward_message(self, request: SendRequest) -> SendResponse:
        return SendResponse(success=True, message_id="test-123")

    async def get_self_info(self) -> ChannelUser:
        return ChannelUser(
            platform=self.channel_id,
            user_id="dummy_bot",
            user_name="Dummy Bot",
            is_bot=True,
        )

    async def get_user_info(self, user_id: str, channel_id: str) -> ChannelUser:
        return ChannelUser(platform=self.channel_id, user_id=user_id, user_name=user_id)

    async def get_channel_info(self, channel_id: str) -> ChannelInfo:
        return ChannelInfo(
            channel_id=channel_id,
            channel_name="Dummy Channel",
            channel_type=ChannelType.PRIVATE,
        )

    async def health_check(self) -> HealthStatus:
        return HealthStatus(healthy=True, detail="OK")


@pytest.mark.asyncio
async def test_channel_instantiation():
    """测试频道实例化。"""
    ch = DummyChannel()
    assert ch.channel_id == "dummy"
    assert ch.display_name == "Dummy"
    assert ChannelCapability.SEND_TEXT in ch.capabilities
    assert ch.metadata.name == "Dummy"
    assert ch.metadata.version == "1.0.0"


@pytest.mark.asyncio
async def test_channel_lifecycle():
    """测试频道生命周期。"""
    ch = DummyChannel()
    assert ch.status == ChannelStatus.STOPPED

    await ch.start()
    assert ch.status == ChannelStatus.RUNNING

    await ch.stop()
    assert ch.status == ChannelStatus.STOPPED


@pytest.mark.asyncio
async def test_forward_message():
    """测试统一发送入口。"""
    ch = DummyChannel()
    req = SendRequest(
        adapter_key="dummy",
        channel=AdapterChannel(channel_id="test", channel_type=ChannelType.PRIVATE),
        segments=[SendSegment(type="text", content="Hello")],
    )
    resp = await ch.forward_message(req)
    assert resp.success
    assert resp.message_id == "test-123"


@pytest.mark.asyncio
async def test_send_text_convenience():
    """测试 send_text 便捷方法（内部走 forward_message）。"""
    ch = DummyChannel()
    result = await ch.send_text("test", "Hello")
    import json
    data = json.loads(result)
    assert data["success"] is True


@pytest.mark.asyncio
async def test_info_queries():
    """测试信息查询。"""
    ch = DummyChannel()

    me = await ch.get_self_info()
    assert me.platform == "dummy"
    assert me.is_bot is True

    user = await ch.get_user_info("user_1", "channel_1")
    assert user.user_id == "user_1"

    channel = await ch.get_channel_info("channel_1")
    assert channel.channel_id == "channel_1"
    assert channel.channel_type == ChannelType.PRIVATE


@pytest.mark.asyncio
async def test_health_check():
    """测试健康探针。"""
    ch = DummyChannel()
    health = await ch.check_health()
    assert health.healthy is True
    assert health.latency_ms is not None


@pytest.mark.asyncio
async def test_config_loading():
    """测试配置加载。"""
    ch = DummyChannel()
    cfg = ch.get_config()
    assert isinstance(cfg, DummyConfig)
    assert cfg.enabled is True  # 默认值


@pytest.mark.asyncio
async def test_get_status_info():
    """测试状态信息。"""
    ch = DummyChannel()
    info = ch.get_status_info()
    assert info["key"] == "dummy"
    assert info["name"] == "Dummy"
    assert info["status"] == "stopped"
    assert "capabilities" in info
    assert "metadata" in info


class SegmentMapChannel(BaseChannel[DummyConfig]):
    """走 _forward_via_segment_map 模板的测试频道（scripted 结果可控）。"""

    channel_id = "segmap"
    display_name = "SegMap"
    capabilities = {ChannelCapability.SEND_TEXT, ChannelCapability.SEND_PHOTO}
    metadata = ChannelMetadata(name="SegMap", description="Test", version="1.0.0")
    _Configs = DummyConfig
    _SEGMENT_SENDERS = {"text": "send_text", "image": "send_photo"}

    fail_on: set = set()

    async def start(self) -> None:
        self._status = ChannelStatus.RUNNING

    async def stop(self) -> None:
        self._status = ChannelStatus.STOPPED

    async def forward_message(self, request: SendRequest) -> SendResponse:
        return await self._forward_via_segment_map(request)

    async def send_text(self, chat_id: str, text: str, **kwargs):
        if "text" in self.fail_on:
            return json.dumps({"success": False, "error": "网络不可达"}, ensure_ascii=False)
        return json.dumps({"success": True, "message_id": f"mid-{text[:4]}"}, ensure_ascii=False)

    async def send_photo(self, chat_id: str, photo: str, caption: str = "", **kwargs):
        if "image" in self.fail_on:
            return json.dumps({"success": False, "error": "文件过大"}, ensure_ascii=False)
        return json.dumps({"success": True, "message_id": "mid-img"}, ensure_ascii=False)

    async def send_voice(self, chat_id: str, voice: str, **kwargs):
        """无平台消息 ID 的频道形态（成功但无 message_id）。"""
        return json.dumps({"success": True}, ensure_ascii=False)

    async def get_self_info(self) -> ChannelUser:
        return ChannelUser(platform="segmap", user_id="bot", user_name="Bot")

    async def get_channel_info(self, channel_id: str) -> ChannelInfo:
        return ChannelInfo(channel_id=channel_id, channel_name="t", channel_type=ChannelType.PRIVATE)

    async def health_check(self) -> HealthStatus:
        return HealthStatus(healthy=True)


def _seg_request(*seg_types: str) -> SendRequest:
    segments = [
        SendSegment(type=t, content="正文" if t == "text" else "", file_path="/tmp/x" if t != "text" else "")
        for t in seg_types
    ]
    return SendRequest(
        adapter_key="segmap",
        channel=AdapterChannel(channel_id="c1", channel_type=ChannelType.PRIVATE),
        segments=segments,
    )


class TestSegmentMapFailures:
    """段分发模板的失败显式化契约（不静默跳过、不静默吞错）。"""

    async def test_unmapped_segment_type_explicitly_fails(self):
        """频道未声明支持的段类型：显式失败并列明，而非静默跳过。"""
        ch = SegmentMapChannel()
        ch.fail_on = set()
        resp = await ch.forward_message(_seg_request("text", "video"))
        assert resp.success is False
        assert "video" in resp.error
        assert resp.message_ids  # 已成功的 text 段 id 仍随响应返回

    async def test_send_failure_reported_with_reason(self):
        ch = SegmentMapChannel()
        ch.fail_on = {"image"}
        resp = await ch.forward_message(_seg_request("text", "image"))
        assert resp.success is False
        assert "文件过大" in resp.error
        assert resp.message_ids == ["mid-正文"]

    async def test_all_segments_success(self):
        ch = SegmentMapChannel()
        ch.fail_on = set()
        resp = await ch.forward_message(_seg_request("text", "image"))
        assert resp.success is True
        assert resp.message_ids == ["mid-正文", "mid-img"]
        assert resp.message_id == "mid-正文"

    async def test_empty_segments_still_ok(self):
        """空段请求（如审批提示已分流场景）保持成功空响应。"""
        ch = SegmentMapChannel()
        ch.fail_on = set()
        resp = await ch.forward_message(_seg_request())
        assert resp.success is True
        assert resp.message_id == "empty"

    async def test_success_without_message_id_is_success(self):
        """无平台 ID 的频道：success 即送达，不因缺 message_id 误判失败。"""
        ch = SegmentMapChannel()
        ch.fail_on = set()
        ch._SEGMENT_SENDERS = {**ch._SEGMENT_SENDERS, "voice": "send_voice"}
        resp = await ch.forward_message(_seg_request("text", "voice"))
        assert resp.success is True
        assert resp.message_ids == ["mid-正文"]  # 有 ID 的段照常收集

    async def test_invalid_json_result_is_failure(self):
        ch = SegmentMapChannel()
        ch.fail_on = set()
        async def _bad(chat_id, text, **kwargs):
            return "not-json"
        ch.send_text = _bad
        resp = await ch.forward_message(_seg_request("text"))
        assert resp.success is False
        assert "返回格式异常" in resp.error
