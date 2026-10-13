"""wcauto 出站发送测试：文本分块、@转换、媒体预检、RPC 失败显式化（假传输层）。"""

from __future__ import annotations

import json

from channels.wcauto.send import WcautoSender


class _FakeTransport:
    """记录 RPC 调用；按方法名注入失败。"""

    def __init__(self, fail_methods: frozenset = frozenset()) -> None:
        self.calls: list = []
        self._fail = fail_methods

    async def call_rpc(self, method, params=None, timeout=None):
        self.calls.append((method, params or {}))
        if method in self._fail:
            from channels.wcauto.transport import WcautoRpcError
            raise WcautoRpcError("微信窗口不可见（可能锁屏/会话断开）")
        return {"status": "ok", "data": {"local_id": 9001}}


class _FakeChannel:
    def __init__(self, transport=None, chunk_len: int = 1500) -> None:
        self._transport = transport or _FakeTransport()
        self._chunk_len = chunk_len

    def _cfg(self, key: str, default=None):
        return {"text_chunk_length": self._chunk_len,
                "send_verify": True}.get(key, default)

    def _self_names(self):
        return ["梦璃"]


def _sender(**kw) -> tuple:
    ch = _FakeChannel(**kw)
    return WcautoSender(ch), ch


def _ok_of(result_json: str) -> dict:
    return json.loads(result_json)


class TestText:
    async def test_simple_send(self) -> None:
        sender, ch = _sender()
        result = _ok_of(await sender.send_text("wxid_a", "你好"))
        assert result["success"] is True
        method, params = ch._transport.calls[0]
        assert method == "send_msg"
        assert params["chat"] == "wxid_a" and params["text"] == "你好"
        assert params["verify"] is True

    async def test_long_text_chunked(self) -> None:
        sender, ch = _sender(chunk_len=100)
        text = "\n".join(f"第{i}行" + "字" * 60 for i in range(10))
        result = _ok_of(await sender.send_text("wxid_a", text))
        assert result["success"] is True
        assert len(ch._transport.calls) >= 2  # 分块
        for _, params in ch._transport.calls:
            assert len(params["text"]) <= 120

    async def test_send_failure_explicit(self) -> None:
        sender, _ = _sender(transport=_FakeTransport(fail_methods={"send_msg"}))
        result = _ok_of(await sender.send_text("wxid_a", "你好"))
        assert result["success"] is False
        assert "失败" in result["error"]

    async def test_empty_rejected(self) -> None:
        sender, _ = _sender()
        result = _ok_of(await sender.send_text("wxid_a", "   "))
        assert result["success"] is False


class TestAtConversion:
    async def test_group_at_tag(self) -> None:
        sender, ch = _sender()
        result = _ok_of(await sender.send_text(
            "g@chatroom", "[at_uid:朋友甲] 看一下这个", channel_type="group"))
        assert result["success"] is True
        method, params = ch._transport.calls[0]
        assert method == "send_at"
        assert params["member"] == "朋友甲"
        assert "[at_uid:" not in params["text"]
        assert "看一下这个" in params["text"]

    async def test_private_at_tag_dropped_as_text(self) -> None:
        sender, ch = _sender()
        await sender.send_text("wxid_a", "[at_uid:朋友甲] 你好", channel_type="private")
        method, params = ch._transport.calls[0]
        assert method == "send_msg"  # 私聊不走 send_at

    async def test_at_self_skipped(self) -> None:
        sender, ch = _sender()
        await sender.send_text("g@chatroom", "[at_uid:梦璃]  hi", channel_type="group")
        method, _ = ch._transport.calls[0]
        assert method == "send_msg"  # @自己 不转换


class TestMedia:
    async def test_missing_file_rejected(self) -> None:
        sender, _ = _sender()
        result = _ok_of(await sender.send_photo("wxid_a", "/no/such/image.png"))
        assert result["success"] is False
        assert "不存在" in result["error"]

    async def test_photo_sent(self, tmp_path) -> None:
        img = tmp_path / "a.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        sender, ch = _sender()
        result = _ok_of(await sender.send_photo("wxid_a", str(img), caption="看图"))
        assert result["success"] is True
        calls = ch._transport.calls
        assert calls[0][0] == "send_image"
        assert calls[0][1]["filename"] == "a.png"
        assert calls[0][1]["data_b64"]
        # caption 作为后续文本消息
        assert calls[1][0] == "send_msg" and calls[1][1]["text"] == "看图"

    async def test_file_sent(self, tmp_path) -> None:
        f = tmp_path / "报告.pdf"
        f.write_bytes(b"%PDF-1.4 fake")
        sender, ch = _sender()
        result = _ok_of(await sender.send_file("wxid_a", str(f)))
        assert result["success"] is True
        assert ch._transport.calls[0][0] == "send_file"

    async def test_media_rpc_failure(self, tmp_path) -> None:
        img = tmp_path / "a.png"
        img.write_bytes(b"\x89PNG" + b"0" * 8)
        sender, _ = _sender(transport=_FakeTransport(fail_methods={"send_image"}))
        result = _ok_of(await sender.send_photo("wxid_a", str(img)))
        assert result["success"] is False
