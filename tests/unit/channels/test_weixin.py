"""微信频道单元测试 — 协议层纯函数 + 状态持久化 + 策略过滤（不触网）。"""

from __future__ import annotations

import base64
import json

import pytest

from channels.weixin import ilink_client as ilink
from channels.weixin.state import (
    ContextTokenStore,
    MessageDeduplicator,
    TypingTicketCache,
    atomic_json_write,
)


# ======================================================================
# AES 加解密
# ======================================================================

class TestAes:
    def test_roundtrip(self):
        key = bytes(range(16))
        plaintext = b"hello weixin media \xe4\xbd\xa0\xe5\xa5\xbd" * 7
        ciphertext = ilink._aes128_ecb_encrypt(plaintext, key)
        assert len(ciphertext) == ilink._aes_padded_size(len(plaintext))
        assert len(ciphertext) % 16 == 0
        assert ilink._aes128_ecb_decrypt(ciphertext, key) == plaintext

    def test_roundtrip_block_aligned(self):
        key = b"k" * 16
        plaintext = b"x" * 32  # 整块对齐也要补一个完整 padding 块
        ciphertext = ilink._aes128_ecb_encrypt(plaintext, key)
        assert len(ciphertext) == 48
        assert ilink._aes128_ecb_decrypt(ciphertext, key) == plaintext

    def test_padded_size(self):
        assert ilink._aes_padded_size(0) == 16
        assert ilink._aes_padded_size(15) == 16
        assert ilink._aes_padded_size(16) == 32
        assert ilink._aes_padded_size(17) == 32

    def test_parse_aes_key_raw16(self):
        raw = bytes(range(16))
        assert ilink._parse_aes_key(base64.b64encode(raw).decode()) == raw

    def test_parse_aes_key_hex32(self):
        raw = bytes(range(16))
        hex_str = raw.hex().encode("ascii")  # 32 字符 hex
        assert ilink._parse_aes_key(base64.b64encode(hex_str).decode()) == raw

    def test_parse_aes_key_invalid(self):
        with pytest.raises(ValueError):
            ilink._parse_aes_key(base64.b64encode(b"too-short").decode())


# ======================================================================
# 会话过期 / 限频判定
# ======================================================================

class TestStaleSession:
    def test_minus14(self):
        assert -14 == ilink.SESSION_EXPIRED_ERRCODE

    def test_unknown_error_is_stale(self):
        assert ilink._is_stale_session_ret(-2, None, "unknown error")
        assert ilink._is_stale_session_ret(None, -2, "Unknown Error")

    def test_real_rate_limit_not_stale(self):
        assert not ilink._is_stale_session_ret(-2, None, "freq limited")
        assert not ilink._is_stale_session_ret(0, 0, "unknown error")


# ======================================================================
# 文本提取（引用 / 语音转写）
# ======================================================================

class TestExtractText:
    def test_plain_text(self):
        items = [{"type": ilink.ITEM_TEXT, "text_item": {"text": "你好"}}]
        assert ilink.extract_text(items) == "你好"

    def test_ref_media_prefix(self):
        items = [{
            "type": ilink.ITEM_TEXT,
            "text_item": {"text": "看这个"},
            "ref_msg": {"title": "照片", "message_item": {"type": ilink.ITEM_IMAGE}},
        }]
        assert ilink.extract_text(items) == "[引用媒体: 照片]\n看这个"

    def test_ref_text_prefix(self):
        items = [{
            "type": ilink.ITEM_TEXT,
            "text_item": {"text": "同意"},
            "ref_msg": {
                "title": "张三",
                "message_item": {"type": ilink.ITEM_TEXT, "text_item": {"text": "原消息"}},
            },
        }]
        assert ilink.extract_text(items) == "[引用: 张三 | 原消息]\n同意"

    def test_voice_transcript_fallback(self):
        items = [{"type": ilink.ITEM_VOICE, "voice_item": {"text": "语音转写内容"}}]
        assert ilink.extract_text(items) == "语音转写内容"

    def test_empty(self):
        assert ilink.extract_text([]) == ""
        assert ilink.extract_text([{"type": ilink.ITEM_IMAGE}]) == ""


# ======================================================================
# 文本分块
# ======================================================================

class TestSplitText:
    def test_short_single_message(self):
        assert ilink.split_text_for_weixin_delivery("你好", 2000) == ["你好"]

    def test_compact_keeps_multiline_together(self):
        # 每行 >48 字符且非列表/标题，不像闲聊 → compact 模式整条发送
        content = "第一行" + "内" * 60 + "\n第二行" + "容" * 60
        assert ilink.split_text_for_weixin_delivery(content, 2000) == [content]

    def test_compact_splits_chatty_short_lines(self):
        content = "在吗\n看到了吗\n回复我一下"
        chunks = ilink.split_text_for_weixin_delivery(content, 2000)
        assert chunks == ["在吗", "看到了吗", "回复我一下"]

    def test_per_line_mode(self):
        content = "标题：总结\n- 要点一\n- 要点二"
        chunks = ilink.split_text_for_weixin_delivery(content, 2000, split_per_line=True)
        assert chunks == ["标题：总结", "- 要点一", "- 要点二"]

    def test_long_content_packed_by_blocks(self):
        blocks = [f"段落{i} " + "字" * 900 for i in range(5)]
        content = "\n\n".join(blocks)
        chunks = ilink.split_text_for_weixin_delivery(content, 2000)
        assert len(chunks) >= 3
        assert all(len(c) <= 2100 for c in chunks)  # 允许 (n/m) 指示符少量超出
        # 内容完整性：拼接后包含所有段落
        joined = "\n".join(chunks)
        for i in range(5):
            assert f"段落{i}" in joined

    def test_code_fence_preserved_in_truncation(self):
        code = "```python\n" + ("print('x')\n" * 200) + "```"
        chunks = ilink.truncate_message(code, 500)
        assert len(chunks) > 1
        for chunk in chunks:
            assert chunk.count("```") % 2 == 0  # 每段栅栏配对

    def test_empty(self):
        assert ilink.split_text_for_weixin_delivery("", 2000) == []


class TestFormatMessage:
    def test_blank_run_compressed(self):
        assert ilink.format_message("a\n\n\n\nb") == "a\n\nb"

    def test_code_block_untouched(self):
        content = "```\na\n\n\n\nb\n```"
        assert ilink.format_message(content) == content

    def test_long_line_soft_wrapped(self):
        # 含空格的长行按 120 列软换行
        line = "word " * 50
        out = ilink.format_message(line)
        assert all(len(l) <= 120 for l in out.splitlines())
        # 无空格的 CJK 长行保持原样（break_long_words=False，与 hermes 一致）
        cjk = "字" * 200
        assert ilink.format_message(cjk) == cjk


# ======================================================================
# 去重 / typing ticket
# ======================================================================

class TestDedup:
    def test_duplicate_within_ttl(self):
        dedup = MessageDeduplicator(ttl_seconds=300)
        assert not dedup.is_duplicate("m1")
        assert dedup.is_duplicate("m1")
        assert not dedup.is_duplicate("m2")

    def test_expire(self):
        dedup = MessageDeduplicator(ttl_seconds=-1)  # 立即过期
        assert not dedup.is_duplicate("m1")
        assert not dedup.is_duplicate("m1")


class TestTypingTicketCache:
    def test_get_set(self):
        cache = TypingTicketCache(ttl_seconds=600)
        assert cache.get("u1") is None
        cache.set("u1", "ticket-1")
        assert cache.get("u1") == "ticket-1"

    def test_expire(self):
        cache = TypingTicketCache(ttl_seconds=-1)
        cache.set("u1", "ticket-1")
        assert cache.get("u1") is None


# ======================================================================
# ContextTokenStore 持久化
# ======================================================================

class TestContextTokenStore:
    def test_persist_and_restore(self, tmp_path, monkeypatch):
        monkeypatch.setattr("channels.weixin.state._PROJECT_ROOT", tmp_path)
        store = ContextTokenStore()
        store.set("acc1", "user-a", "token-a")
        store.set("acc1", "user-b", "token-b")
        store.set("acc2", "user-a", "token-c")

        # 文件已落盘
        data = json.loads((tmp_path / "workspace/weixin/accounts/acc1.context-tokens.json").read_text())
        assert data == {"user-a": "token-a", "user-b": "token-b"}

        # 新实例恢复
        store2 = ContextTokenStore()
        store2.restore("acc1")
        assert store2.get("acc1", "user-a") == "token-a"
        assert store2.get("acc1", "user-b") == "token-b"
        assert store2.get("acc2", "user-a") is None  # 未 restore acc2

    def test_pop(self, tmp_path, monkeypatch):
        monkeypatch.setattr("channels.weixin.state._PROJECT_ROOT", tmp_path)
        store = ContextTokenStore()
        store.set("acc1", "user-a", "token-a")
        store.pop("acc1", "user-a")
        assert store.get("acc1", "user-a") is None
        data = json.loads((tmp_path / "workspace/weixin/accounts/acc1.context-tokens.json").read_text())
        assert data == {}


# ======================================================================
# 账号凭据 / 游标
# ======================================================================

class TestAccountAndSyncBuf:
    def test_account_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr("channels.weixin.state._PROJECT_ROOT", tmp_path)
        from channels.weixin.state import load_weixin_account, save_weixin_account

        save_weixin_account(account_id="bot@im.bot", token="tok", base_url="https://x", user_id="u")
        loaded = load_weixin_account("bot@im.bot")
        assert loaded["token"] == "tok"
        assert loaded["base_url"] == "https://x"
        assert load_weixin_account("nonexist") is None

    def test_sync_buf_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr("channels.weixin.state._PROJECT_ROOT", tmp_path)
        from channels.weixin.state import load_sync_buf, save_sync_buf

        assert load_sync_buf("acc") == ""
        save_sync_buf("acc", "cursor-123")
        assert load_sync_buf("acc") == "cursor-123"


# ======================================================================
# SSRF 防护 / 聊天类型判断
# ======================================================================

class TestSsrf:
    def test_allowlist_host(self):
        ilink.assert_weixin_cdn_url("https://novac2c.cdn.weixin.qq.com/c2c/download?x=1")
        ilink.assert_weixin_cdn_url("http://mmbiz.qpic.cn/xx")

    def test_reject_unknown_host(self):
        with pytest.raises(ValueError):
            ilink.assert_weixin_cdn_url("https://evil.example.com/x")
        with pytest.raises(ValueError):
            ilink.assert_weixin_cdn_url("ftp://novac2c.cdn.weixin.qq.com/x")


class TestGuessChatType:
    def test_dm(self):
        msg = {"from_user_id": "user1", "to_user_id": "bot@im.bot", "msg_type": 1}
        assert ilink.guess_chat_type(msg, "bot@im.bot") == ("dm", "user1")

    def test_group_by_room_id(self):
        msg = {"from_user_id": "user1", "room_id": "room@chatroom"}
        assert ilink.guess_chat_type(msg, "bot@im.bot") == ("group", "room@chatroom")


# ======================================================================
# 频道策略过滤（不启动网络）
# ======================================================================

class TestChannelPolicy:
    def _make_channel(self, **cfg_overrides):
        from channels.weixin.adapter import WeixinChannel

        ch = WeixinChannel()
        for key, value in cfg_overrides.items():
            setattr(ch._config, key, value)
        return ch

    def test_dm_open(self):
        ch = self._make_channel(dm_policy="open")
        assert ch._is_dm_allowed("anyone")

    def test_dm_disabled(self):
        ch = self._make_channel(dm_policy="disabled")
        assert not ch._is_dm_allowed("anyone")

    def test_dm_allowlist(self):
        ch = self._make_channel(dm_policy="allowlist", allow_from="u1, u2")
        assert ch._is_dm_allowed("u1")
        assert ch._is_dm_allowed("u2")
        assert not ch._is_dm_allowed("u3")

    def test_group_default_disabled(self):
        ch = self._make_channel()
        assert not ch._is_group_allowed("room@chatroom")

    def test_group_allowlist(self):
        ch = self._make_channel(group_policy="allowlist", group_allow_from="r1,r2")
        assert ch._is_group_allowed("r2")
        assert not ch._is_group_allowed("r3")

    def test_known_group(self):
        ch = self._make_channel(group_allow_from="")
        assert ch.is_known_group("xxx@chatroom")
        assert not ch.is_known_group("some-user")
        ch._known_groups.add("room1")
        assert ch.is_known_group("room1")


# ======================================================================
# 出站媒体 item 构造
# ======================================================================

class TestOutboundMediaBuilder:
    def test_image(self):
        from channels.weixin.adapter import WeixinChannel

        media_type, builder = WeixinChannel._outbound_media_builder("a.jpg")
        item = builder(
            encrypt_query_param="eqp", aes_key_for_api="key",
            ciphertext_size=100, plaintext_size=90, filename="a.jpg", rawfilemd5="md5",
        )
        assert media_type == ilink.MEDIA_IMAGE
        assert item["type"] == ilink.ITEM_IMAGE
        assert item["image_item"]["media"]["aes_key"] == "key"
        assert item["image_item"]["media"]["encrypt_type"] == 1

    def test_silk_voice(self):
        from channels.weixin.adapter import WeixinChannel

        media_type, builder = WeixinChannel._outbound_media_builder("v.silk")
        item = builder(
            encrypt_query_param="eqp", aes_key_for_api="key",
            ciphertext_size=100, plaintext_size=90, filename="v.silk",
            rawfilemd5="md5", encode_type=6, sample_rate=24000, bits_per_sample=16,
        )
        assert media_type == ilink.MEDIA_VOICE
        assert item["type"] == ilink.ITEM_VOICE
        assert item["voice_item"]["encode_type"] == 6

    def test_silk_forced_file_attachment(self):
        from channels.weixin.adapter import WeixinChannel

        media_type, builder = WeixinChannel._outbound_media_builder("v.silk", force_file_attachment=True)
        assert media_type == ilink.MEDIA_FILE

    def test_generic_file(self):
        from channels.weixin.adapter import WeixinChannel

        media_type, builder = WeixinChannel._outbound_media_builder("doc.pdf")
        item = builder(
            encrypt_query_param="eqp", aes_key_for_api="key",
            ciphertext_size=100, plaintext_size=90, filename="doc.pdf", rawfilemd5="md5",
        )
        assert media_type == ilink.MEDIA_FILE
        assert item["file_item"]["file_name"] == "doc.pdf"
        assert item["file_item"]["len"] == "90"


# ======================================================================
# 出站发送管线（mock iLink API，验证 -14 降级 / 限频熔断 / 分块）
# ======================================================================

def _make_started_channel(**cfg_overrides):
    """构造一个绕过网络、可直接调用发送路径的频道实例。"""
    from channels.weixin.adapter import WeixinChannel

    ch = WeixinChannel()
    for key, value in cfg_overrides.items():
        setattr(ch._config, key, value)
    ch._account_id = "bot@im.bot"
    ch._token = "tok"
    ch._send_session = object()  # 仅占位，网络调用已被 mock
    ch._running = True
    return ch


class TestSendTextPipeline:
    async def test_send_message_fields(self, monkeypatch):
        from agent.channel.schemas import AdapterChannel, ChannelType, SendSegment, SendRequest

        sent = []

        async def fake_send_message(session, **kwargs):
            sent.append(kwargs)
            return {"ret": 0}

        monkeypatch.setattr(ilink, "send_message", fake_send_message)
        ch = _make_started_channel(send_chunk_delay_seconds=0)
        ch._token_store.set("bot@im.bot", "user1", "ctx-1")

        resp = await ch.forward_message(SendRequest(
            adapter_key="weixin",
            channel=AdapterChannel(channel_id="user1", channel_type=ChannelType.PRIVATE),
            segments=[SendSegment(type="text", content="你好")],
        ))
        assert resp.success and resp.message_id
        assert sent[0]["to"] == "user1"
        assert sent[0]["text"] == "你好"
        assert sent[0]["context_token"] == "ctx-1"  # 回显最新 context_token

    async def test_session_expired_retries_without_token(self, monkeypatch):
        from agent.channel.schemas import AdapterChannel, ChannelType, SendSegment, SendRequest

        calls = []

        async def fake_send_message(session, **kwargs):
            calls.append(kwargs.get("context_token"))
            # 第一次带 token → -14 会话过期；去掉 token 后成功
            return {"errcode": -14} if kwargs.get("context_token") else {"ret": 0}

        monkeypatch.setattr(ilink, "send_message", fake_send_message)
        ch = _make_started_channel(send_chunk_delay_seconds=0)
        ch._token_store.set("bot@im.bot", "user1", "ctx-stale")

        resp = await ch.forward_message(SendRequest(
            adapter_key="weixin",
            channel=AdapterChannel(channel_id="user1", channel_type=ChannelType.PRIVATE),
            segments=[SendSegment(type="text", content="测试")],
        ))
        assert resp.success
        assert calls == ["ctx-stale", None]
        # token 已从 store 清除
        assert ch._token_store.get("bot@im.bot", "user1") is None

    async def test_rate_limit_circuit_breaker(self, monkeypatch):
        ch = _make_started_channel(
            send_chunk_retries=0,
            rate_limit_circuit_threshold=1,
            rate_limit_circuit_open_seconds=30.0,
        )

        async def fake_send_message(session, **kwargs):
            return {"errcode": -2, "errmsg": "freq limited"}

        monkeypatch.setattr(ilink, "send_message", fake_send_message)
        # 第一次：限频 → 熔断断开
        with pytest.raises(RuntimeError, match="限频"):
            await ch._send_text_chunk(chat_id="u", chunk="x", context_token=None, client_id="c1")
        assert ch._rate_limit_cooldown_remaining() > 0
        # 熔断期间直接抛错，不再请求
        with pytest.raises(RuntimeError, match="熔断冷却"):
            await ch._send_text_chunk(chat_id="u", chunk="x", context_token=None, client_id="c2")

    async def test_long_text_chunked_in_order(self, monkeypatch):
        from agent.channel.schemas import AdapterChannel, ChannelType, SendSegment, SendRequest

        sent = []

        async def fake_send_message(session, **kwargs):
            sent.append(kwargs["text"])
            return {"ret": 0}

        monkeypatch.setattr(ilink, "send_message", fake_send_message)
        ch = _make_started_channel(send_chunk_delay_seconds=0)
        long_text = "\n\n".join(f"段落{i} " + "字" * 900 for i in range(5))
        resp = await ch.forward_message(SendRequest(
            adapter_key="weixin",
            channel=AdapterChannel(channel_id="user1", channel_type=ChannelType.PRIVATE),
            segments=[SendSegment(type="text", content=long_text)],
        ))
        assert resp.success
        assert len(sent) >= 3  # 超 2000 字符被分块
        joined = "\n".join(sent)
        for i in range(5):
            assert f"段落{i}" in joined


# ======================================================================
# 入站消息处理（mock 网络，验证去重 / 策略 / context_token / 合批）
# ======================================================================

def _inbound_msg(**overrides):
    msg = {
        "from_user_id": "user1",
        "message_id": "mid-1",
        "context_token": "ctx-new",
        "item_list": [{"type": ilink.ITEM_TEXT, "text_item": {"text": "在吗"}}],
    }
    msg.update(overrides)
    return msg


class TestInboundPipeline:
    def _prepare(self, monkeypatch, **cfg):
        ch = _make_started_channel(**cfg)
        ch._poll_session = object()
        ch.config.text_batch_delay_seconds = 0.01

        dispatched = []

        async def fake_on_message(message):
            dispatched.append(message)

        monkeypatch.setattr(ch, "on_message", fake_on_message)

        async def fake_ticket(user_id, context_token):
            return None

        monkeypatch.setattr(ch, "_maybe_fetch_typing_ticket", fake_ticket)
        return ch, dispatched

    async def test_dm_text_dispatched_with_context_token(self, monkeypatch):
        ch, dispatched = self._prepare(monkeypatch)
        await ch._process_message(_inbound_msg())
        import asyncio as _aio
        await _aio.sleep(0.05)  # 等合批静默期
        assert len(dispatched) == 1
        msg = dispatched[0]
        assert msg.content == "在吗"
        assert msg.sender.user_id == "user1"
        assert msg.channel.channel_id == "user1"
        assert msg.is_to_me is True
        # 入站 context_token 已存
        assert ch._token_store.get("bot@im.bot", "user1") == "ctx-new"

    async def test_self_message_skipped(self, monkeypatch):
        ch, dispatched = self._prepare(monkeypatch)
        await ch._process_message(_inbound_msg(from_user_id="bot@im.bot"))
        assert not dispatched

    async def test_dedup_by_message_id(self, monkeypatch):
        ch, dispatched = self._prepare(monkeypatch)
        await ch._process_message(_inbound_msg())
        await ch._process_message(_inbound_msg())  # 相同 message_id
        import asyncio as _aio
        await _aio.sleep(0.05)
        assert len(dispatched) == 1

    async def test_dm_policy_blocks(self, monkeypatch):
        ch, dispatched = self._prepare(monkeypatch, dm_policy="allowlist", allow_from="u9")
        await ch._process_message(_inbound_msg())
        import asyncio as _aio
        await _aio.sleep(0.05)
        assert not dispatched

    async def test_group_default_blocked(self, monkeypatch):
        ch, dispatched = self._prepare(monkeypatch)
        await ch._process_message(_inbound_msg(room_id="room@chatroom"))
        import asyncio as _aio
        await _aio.sleep(0.05)
        assert not dispatched

    async def test_text_batch_merges_bursts(self, monkeypatch):
        ch, dispatched = self._prepare(monkeypatch)
        await ch._process_message(_inbound_msg(message_id="m1"))
        await ch._process_message(_inbound_msg(
            message_id="m2",
            item_list=[{"type": ilink.ITEM_TEXT, "text_item": {"text": "看到了吗"}}],
        ))
        import asyncio as _aio
        await _aio.sleep(0.05)
        assert len(dispatched) == 1  # 合批为一条
        assert dispatched[0].content == "在吗\n看到了吗"


# ======================================================================
# WebUI 扫码登录（QrLoginManager 状态机，mock iLink API）
# ======================================================================

class TestQrLoginManager:
    def _make_manager(self, monkeypatch, qr_responses, status_responses):
        """qr_responses/status_responses: 依次返回的响应队列。"""
        from channels.weixin import qr_login as qr_mod

        qr_queue = list(qr_responses)
        status_queue = list(status_responses)

        async def fake_api_get(session, *, base_url, endpoint, timeout_ms):
            if "get_bot_qrcode" in endpoint:
                assert qr_queue, "get_bot_qrcode 被过多调用"
                return qr_queue.pop(0)
            assert "get_qrcode_status" in endpoint
            assert status_queue, "get_qrcode_status 被过多调用"
            return status_queue.pop(0)

        monkeypatch.setattr(qr_mod, "_api_get", fake_api_get)
        monkeypatch.setattr(qr_mod, "_qr_png_data_url", lambda data: f"data:png,{data[:8]}")
        saved = []
        monkeypatch.setattr(qr_mod, "save_weixin_account", lambda **kw: saved.append(kw))
        return qr_mod.QrLoginManager(), saved

    _QR_RESP = {"qrcode": "hex-token", "qrcode_img_content": "https://liteapp/qr"}

    async def test_full_flow(self, monkeypatch):
        mgr, saved = self._make_manager(
            monkeypatch,
            [self._QR_RESP],
            [{"status": "wait"}, {"status": "scaned"}, {
                "status": "confirmed",
                "ilink_bot_id": "bot@im.bot",
                "bot_token": "tok-1",
                "baseurl": "https://ilinkai.weixin.qq.com",
                "ilink_user_id": "user-x",
            }],
        )
        start = await mgr.start()
        assert start["session_id"]
        assert start["qr_png"].startswith("data:png,")
        assert start["qr_url"] == "https://liteapp/qr"

        sid = start["session_id"]
        assert (await mgr.poll(sid))["status"] == "wait"
        assert (await mgr.poll(sid))["status"] == "scaned"
        result = await mgr.poll(sid)
        assert result["status"] == "confirmed"
        assert result["account_id"] == "bot@im.bot"
        assert result["credential"]["token"] == "tok-1"
        # 凭据已落盘
        assert saved and saved[0]["token"] == "tok-1"
        # 重复轮询保持 confirmed
        assert (await mgr.poll(sid))["status"] == "confirmed"

    async def test_expired_auto_refresh(self, monkeypatch):
        mgr, _ = self._make_manager(
            monkeypatch,
            [self._QR_RESP, {"qrcode": "hex-2", "qrcode_img_content": "https://liteapp/qr2"}],
            [{"status": "expired"}],
        )
        start = await mgr.start()
        result = await mgr.poll(start["session_id"])
        assert result["status"] == "wait"
        assert result["refreshed"] is True
        assert result["qr_url"] == "https://liteapp/qr2"

    async def test_refresh_limit_exceeded(self, monkeypatch):
        mgr, _ = self._make_manager(
            monkeypatch,
            [self._QR_RESP] * 4,
            [{"status": "expired"}] * 4,
        )
        start = await mgr.start()
        sid = start["session_id"]
        for _ in range(3):
            assert (await mgr.poll(sid))["status"] == "wait"
        assert (await mgr.poll(sid))["status"] == "error"

    async def test_redirect_host(self, monkeypatch):
        mgr, _ = self._make_manager(
            monkeypatch,
            [self._QR_RESP],
            [{"status": "scaned_but_redirect", "redirect_host": "sz.weixin.qq.com"}],
        )
        start = await mgr.start()
        session = mgr._sessions[start["session_id"]]
        await mgr.poll(start["session_id"])
        assert session.current_base_url == "https://sz.weixin.qq.com"

    async def test_unknown_session(self):
        from channels.weixin.qr_login import QrLoginManager

        mgr = QrLoginManager()
        result = await mgr.poll("nonexistent")
        assert result["status"] == "error"

    async def test_confirmed_incomplete_credential(self, monkeypatch):
        mgr, _ = self._make_manager(
            monkeypatch,
            [self._QR_RESP],
            [{"status": "confirmed", "ilink_bot_id": "", "bot_token": ""}],
        )
        start = await mgr.start()
        result = await mgr.poll(start["session_id"])
        assert result["status"] == "error"


# ======================================================================
# WebUI 路由挂载
# ======================================================================

class TestChannelRouterMount:
    def test_weixin_build_router(self):
        from channels.weixin.adapter import build_router

        router = build_router()
        paths = {r.path for r in router.routes}
        assert "/qr/start" in paths
        assert "/qr/{session_id}/status" in paths

    def test_server_mounts_channel_routers(self, monkeypatch):
        """create_app() 挂载微信扫码路由，端到端走通（mock QR 管理器）。"""
        from unittest.mock import AsyncMock

        import channels.weixin.qr_login as qr_mod

        mock_manager = AsyncMock()
        mock_manager.start.return_value = {
            "session_id": "s1", "qr_png": "data:png,x", "qr_url": "u",
        }
        mock_manager.poll.return_value = {"status": "wait"}
        monkeypatch.setattr(qr_mod, "get_qr_manager", lambda: mock_manager)

        import web.server as server_mod

        monkeypatch.setattr(server_mod, "_load_auth_password", lambda: "")

        from fastapi.testclient import TestClient

        client = TestClient(server_mod.create_app())
        resp = client.post("/api/channels/weixin/qr/start")
        assert resp.status_code == 200
        assert resp.json()["session_id"] == "s1"
        resp = client.get("/api/channels/weixin/qr/s1/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "wait"

    def test_channel_get_router_hook(self):
        from channels.weixin.adapter import WeixinChannel

        ch = WeixinChannel()
        assert ch.get_router() is not None
