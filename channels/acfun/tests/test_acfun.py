"""AcFun 频道单元测试 — 解析/发送路由/轮询去重/工具规整/登录路由（不触网）。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from channels.acfun import parser, send, state
from channels.acfun.poller import NotificationPoller
from channels.acfun.tools import AcfunToolsMixin, _acfun_error, _to_bool, _to_int

# ======================================================================
# 测试替身
# ======================================================================


class FakeClient:
    """AcfunClient 替身：同步方法直接返回预置数据，run 原样转发。"""

    def __init__(self, logined: bool = True) -> None:
        self.is_logined = logined
        self.notifications: Dict[str, List[Dict[str, Any]]] = {}
        self.sent_comments: List[tuple] = []
        self.sent_danmaku: List[tuple] = []
        self.comment_ok = True
        self.danmaku_ok = True

    async def run(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)

    def get_notifications(self, kind: str, page: int = 1) -> List[Dict[str, Any]]:
        return self.notifications.get(kind, [])

    def send_comment(self, rtype: Any, rid: Any, content: str, reply_id: Any = None) -> bool:
        self.sent_comments.append((rtype, rid, content, reply_id))
        return self.comment_ok

    def push_live_danmaku(self, uid: Any, content: str) -> bool:
        self.sent_danmaku.append((uid, content))
        return self.danmaku_ok


def _make_channel(client: FakeClient, **cfg_overrides: Any) -> SimpleNamespace:
    cfg = {
        "message_max_length": 1000,
        "live_danmaku_cooldown_seconds": 5,
        "poll_interval_seconds": 60,
        "notify_like": True,
        "notify_gift": True,
        "notify_system": True,
        "like_trigger_mind": False,
        "gift_trigger_mind": True,
        "whitelist_enabled": False,
        "user_whitelist": "",
    }
    cfg.update(cfg_overrides)
    return SimpleNamespace(
        client=client,
        config=SimpleNamespace(**cfg),
        live_danmaku_last_sent={},
    )


def _reply_item(**overrides: Any) -> Dict[str, Any]:
    item = {
        "content_url": "https://www.acfun.cn/v/ac12345",
        "content_title": "测试视频",
        "replied": "我的原评论",
        "uid": "1001",
        "username": "acer甲",
        "create_at": "2026-09-01 10:00",
        "ncid": "9001",
        "content": "回复你的内容",
        "intro": "回复了你的评论",
    }
    item.update(overrides)
    return item


# ======================================================================
# parser：URL 解析 / 去重键 / 通知映射
# ======================================================================


class TestParseContentUrl:
    @pytest.mark.parametrize("url,expected", [
        ("https://www.acfun.cn/v/ac12345", (2, "12345")),
        ("https://www.acfun.cn/a/ac678", (3, "678")),
        ("https://www.acfun.cn/bangumi/aa6001", (1, "6001")),
        ("https://www.acfun.cn/moment/am777", (10, "777")),
    ])
    def test_supported(self, url, expected):
        assert parser.parse_content_url(url) == expected

    @pytest.mark.parametrize("url", ["", "https://live.acfun.cn/live/1001", "https://www.acfun.cn/u/1001"])
    def test_unsupported(self, url):
        assert parser.parse_content_url(url) is None


class TestDedupKey:
    def test_comment_kinds_use_ncid(self):
        key = parser.dedup_key("reply", _reply_item())
        assert key == "reply:9001:1001"
        assert parser.dedup_key("reply", _reply_item()) == key  # 稳定

    def test_fallback_fingerprint(self):
        item = {"uid": "1", "content_url": "u", "create_at": "t", "intro": "i"}
        assert parser.dedup_key("system", item) == parser.dedup_key("system", dict(item))
        assert parser.dedup_key("system", item) != parser.dedup_key("system", {**item, "intro": "x"})


class TestNotificationToMessage:
    def test_reply_triggers_mind_with_quote(self):
        msg = parser.notification_to_message("reply", _reply_item())
        assert msg is not None
        assert msg.is_to_me is True and msg.trigger_mind is True
        assert msg.channel.channel_id == "comment:2:12345"
        assert msg.channel.channel_type.value == "group"
        assert msg.reply_to_id == "9001"
        assert msg.reply_content == "我的原评论"
        assert msg.sender.user_id == "1001"
        assert msg.content == "回复你的内容"

    def test_reply_empty_content_dropped(self):
        assert parser.notification_to_message("reply", _reply_item(content="  ")) is None

    def test_at_triggers_mind(self):
        item = {"content_url": "https://www.acfun.cn/a/ac678", "ncid": "81",
                "uid": "1002", "username": "acer乙", "intro": "在评论中提到了你"}
        msg = parser.notification_to_message("at", item)
        assert msg is not None and msg.trigger_mind is True
        assert msg.channel.channel_id == "comment:3:678"
        assert msg.reply_to_id == "81"

    def test_like_record_only_by_default(self):
        msg = parser.notification_to_message("like", _reply_item(replied="我的评论"))
        assert msg is not None
        assert msg.is_to_me is False and msg.trigger_mind is False
        assert "赞了你的评论" in msg.content

    def test_gift_trigger_flag(self):
        item = {"content_url": "https://www.acfun.cn/v/ac12345", "content_title": "测试视频",
                "uid": "1003", "username": "acer丙", "banana": 5}
        assert parser.notification_to_message("gift", item, gift_trigger_mind=True).trigger_mind is True
        assert parser.notification_to_message("gift", item, gift_trigger_mind=False).trigger_mind is False
        assert "5 根香蕉" in parser.notification_to_message("gift", item).content

    def test_system_goes_to_system_channel(self):
        item = {"content_title": "公告", "intro": "系统升级维护"}
        msg = parser.notification_to_message("system", item)
        assert msg is not None
        assert msg.channel.channel_id == parser.SYSTEM_CHANNEL_ID
        assert msg.trigger_mind is False
        assert msg.sender.user_id == "acfun_system"

    def test_unparseable_url_falls_back_to_system_channel(self):
        msg = parser.notification_to_message("reply", _reply_item(content_url="https://live.acfun.cn/live/1"))
        assert msg is not None
        assert msg.channel.channel_id == parser.SYSTEM_CHANNEL_ID

    def test_unknown_kind_returns_none(self):
        assert parser.notification_to_message("mystery", {}) is None


# ======================================================================
# send：目标解析 / 分段 / 发送路由
# ======================================================================


class TestParseChatTarget:
    def test_comment(self):
        assert send.parse_chat_target("comment:2:12345") == ("comment", "2:12345")

    def test_live_and_user(self):
        assert send.parse_chat_target("live:1001") == ("live", "1001")
        assert send.parse_chat_target("user:1001") == ("user", "1001")

    @pytest.mark.parametrize("raw", ["", "12345", "comment:", "weird:thing:"])
    def test_invalid(self, raw):
        assert send.parse_chat_target(raw) is None


class TestSplitText:
    def test_short_text_single_chunk(self):
        assert send._split_text("你好", 1000) == ["你好"]

    def test_long_text_split_on_newline(self):
        text = ("段落一\n" * 200).strip()
        chunks = send._split_text(text, 200)
        assert len(chunks) > 1
        assert all(len(c) <= 200 for c in chunks[:-1])

    def test_chunk_cap_truncates(self):
        text = "x" * 10000
        chunks = send._split_text(text, 1000)
        assert len(chunks) == send._MAX_CHUNKS
        assert chunks[-1].endswith("...")


class TestSendChannelText:
    async def test_not_logined(self):
        result = json.loads(await send.send_channel_text(_make_channel(FakeClient(logined=False)), "comment:2:1", "hi"))
        assert result["success"] is False
        assert "未登录" in result["error"]

    async def test_comment_reply(self):
        channel = _make_channel(FakeClient())
        result = json.loads(await send.send_channel_text(channel, "comment:2:12345", "收到！", reply_to="9001"))
        assert result["success"] is True
        assert channel.client.sent_comments == [("2", "12345", "收到！", "9001")]

    async def test_comment_rejected_by_platform(self):
        client = FakeClient()
        client.comment_ok = False
        result = json.loads(await send.send_channel_text(_make_channel(client), "comment:2:1", "hi"))
        assert result["success"] is False
        assert "被拒" in result["error"]

    async def test_user_dm_unsupported(self):
        result = json.loads(await send.send_channel_text(_make_channel(FakeClient()), "user:1001", "hi"))
        assert result["success"] is False
        assert "私信" in result["error"]

    async def test_bad_target(self):
        result = json.loads(await send.send_channel_text(_make_channel(FakeClient()), "???", "hi"))
        assert result["success"] is False

    async def test_live_danmaku_and_cooldown(self):
        channel = _make_channel(FakeClient())
        first = json.loads(await send.send_channel_text(channel, "live:1001", "弹幕一"))
        assert first["success"] is True
        # 冷却期内第二次被拒
        second = json.loads(await send.send_channel_text(channel, "live:1001", "弹幕二"))
        assert second["success"] is False
        assert "冷却" in second["error"]
        # 另一个直播间不受限
        third = json.loads(await send.send_channel_text(channel, "live:2002", "弹幕三"))
        assert third["success"] is True


# ======================================================================
# state：凭据与轮询游标持久化
# ======================================================================


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "acfun_data_dir", lambda: str(tmp_path))
    return tmp_path


class TestCredentialStore:
    def test_roundtrip(self, data_dir):
        state.save_cookies("13800000000", "264001", {"acfun.midground.api_st": "token"})
        loaded = state.load_cookies()
        assert loaded is not None
        assert loaded["uid"] == "264001"
        assert loaded["cookies"]["acfun.midground.api_st"] == "token"

    def test_missing_returns_none(self, data_dir):
        assert state.load_cookies() is None

    def test_clear(self, data_dir):
        state.save_cookies("u", "1", {"k": "v"})
        state.clear_cookies()
        assert state.load_cookies() is None


class TestPollCursorStore:
    def test_seed_mark_persist(self, data_dir):
        store = state.PollCursorStore()
        store.load()
        assert store.is_seeded("reply") is False
        store.mark("reply", "k1")
        store.mark_seeded("reply")
        store.save()
        store2 = state.PollCursorStore()
        store2.load()
        assert store2.is_seeded("reply") is True
        assert store2.is_seen("reply", "k1") is True
        assert store2.is_seen("reply", "k2") is False

    def test_bounded_eviction(self, data_dir):
        store = state.PollCursorStore()
        for i in range(state._MAX_SEEN_PER_KIND + 20):
            store.mark("like", f"k{i}")
        assert store.is_seen("like", "k0") is False  # 最旧的被淘汰
        assert store.is_seen("like", f"k{state._MAX_SEEN_PER_KIND + 19}") is True

    def test_save_skips_when_clean(self, data_dir):
        store = state.PollCursorStore()
        store.save()  # 无变更不落盘
        assert not (data_dir / "poll_state.json").exists()


# ======================================================================
# poller：播种 / 增量派发 / 白名单 / 登录失效
# ======================================================================


class PollerChannel:
    """轮询宿主替身。"""

    def __init__(self, client: FakeClient, **cfg: Any) -> None:
        base = _make_channel(client, **cfg)
        self.client = client
        self.config = base.config
        self.messages: List[Any] = []
        self.login_expired = False

    async def on_message(self, message: Any) -> None:
        self.messages.append(message)

    def on_login_expired(self) -> None:
        self.login_expired = True


class TestPoller:
    async def test_first_run_seeds_without_dispatch(self, data_dir):
        client = FakeClient()
        client.notifications["reply"] = [_reply_item()]
        channel = PollerChannel(client)
        poller = NotificationPoller(channel)
        poller._cursors.load()
        await poller._poll_once()
        assert channel.messages == []  # 首次播种不派发

        client.notifications["reply"] = [_reply_item(ncid="9002", content="新回复"), _reply_item()]
        await poller._poll_once()
        assert len(channel.messages) == 1
        assert channel.messages[0].content == "新回复"

    async def test_dispatch_order_oldest_first(self, data_dir):
        client = FakeClient()
        channel = PollerChannel(client)
        poller = NotificationPoller(channel)
        poller._cursors.load()
        await poller._poll_once()  # 播种
        client.notifications["reply"] = [
            _reply_item(ncid="9003", content="较新"),
            _reply_item(ncid="9002", content="较旧"),
        ]
        await poller._poll_once()
        assert [m.content for m in channel.messages] == ["较旧", "较新"]

    async def test_whitelist_blocks_dispatch(self, data_dir):
        client = FakeClient()
        channel = PollerChannel(client, whitelist_enabled=True, user_whitelist="9999")
        poller = NotificationPoller(channel)
        poller._cursors.load()
        await poller._poll_once()
        client.notifications["reply"] = [_reply_item(ncid="9005", uid="1001")]
        await poller._poll_once()
        assert channel.messages == []  # 非白名单用户被拦截
        # 但游标已登记，不会重复评估
        assert poller._cursors.is_seen("reply", "reply:9005:1001")

    async def test_notify_switches(self, data_dir):
        client = FakeClient()
        client.notifications["like"] = [_reply_item(ncid="7001")]
        channel = PollerChannel(client, notify_like=False)
        poller = NotificationPoller(channel)
        poller._cursors.load()
        await poller._poll_once()
        await poller._poll_once()
        assert channel.messages == []  # like 通知关闭时不拉取

    async def test_login_expired_notifies_channel(self, data_dir):
        client = FakeClient(logined=False)
        channel = PollerChannel(client)
        poller = NotificationPoller(channel)
        await poller._loop()  # NotInCar → 通知频道并退出循环（不 sleep）
        assert channel.login_expired is True


# ======================================================================
# tools：参数规整与结果/异常归一
# ======================================================================


class ToolHost(AcfunToolsMixin):
    def __init__(self, client: FakeClient) -> None:
        self.client = client
        self.live_danmaku_last_sent: Dict[str, float] = {}

    def live_danmaku_cooldown_seconds(self) -> int:
        return 0


class TestToolHelpers:
    def test_to_int(self):
        assert _to_int("ac12345", "x") == 12345
        assert _to_int(42, "x") == 42
        with pytest.raises(ValueError):
            _to_int("abc", "x")

    def test_to_bool(self):
        assert _to_bool("true") is True
        assert _to_bool("0") is False
        assert _to_bool(True) is True

    def test_error_mapping(self):
        from acfunsdk.exceptions import AcExploded, NotInCar, ShuiNi, TingBuDong

        assert "未登录" in _acfun_error(NotInCar())
        assert "404" in _acfun_error(ShuiNi("内容不存在（404）"))
        assert "格式" in _acfun_error(TingBuDong("bad"))
        assert "网络" in _acfun_error(AcExploded("boom"))
        assert "参数错" in _acfun_error(ValueError("参数错"))


class TestToolDispatch:
    async def test_unlogged_short_circuits(self):
        host = ToolHost(FakeClient(logined=False))
        result = json.loads(await host.video_info("12345"))
        assert result["success"] is False
        assert "未登录" in result["error"]

    async def test_list_result_wrapping(self):
        client = FakeClient()
        client.search = lambda *a: [{"type": "video", "id": 1}]  # type: ignore[attr-defined]
        host = ToolHost(client)
        result = json.loads(await host.search("キーワード"))
        assert result["success"] is True
        assert result["count"] == 1

    async def test_bool_false_is_error(self):
        client = FakeClient()
        client.signin = lambda: False  # type: ignore[attr-defined]
        host = ToolHost(client)
        result = json.loads(await host.signin())
        assert result["success"] is False

    async def test_exception_mapped_to_error_json(self):
        def _boom(*a: Any) -> Any:
            raise ValueError("分 P 索引越界")

        client = FakeClient()
        client.video_danmaku_list = _boom  # type: ignore[attr-defined]
        host = ToolHost(client)
        result = json.loads(await host.video_danmaku_list("12345"))
        assert result["success"] is False
        assert "越界" in result["error"]


# ======================================================================
# 登录路由（build_router）：入参校验 / 状态 / 登出
# ======================================================================


class TestLoginRouter:
    def _app(self, monkeypatch, tmp_path):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from channels.acfun import adapter as adapter_mod

        monkeypatch.setattr(adapter_mod, "_channel_config_file", lambda: tmp_path / "channel_config.json")
        (tmp_path / "channel_config.json").write_text('{"enabled": false}', encoding="utf-8")
        app = FastAPI()
        app.include_router(adapter_mod.build_router(), prefix="/channels/acfun")
        return TestClient(app), adapter_mod

    def test_login_empty_credentials_rejected(self, monkeypatch, tmp_path):
        client, _ = self._app(monkeypatch, tmp_path)
        resp = client.post("/channels/acfun/login", json={"username": " ", "password": ""})
        assert resp.status_code == 200
        assert resp.json()["success"] is False

    def test_login_success_flow(self, monkeypatch, tmp_path):
        client, adapter_mod = self._app(monkeypatch, tmp_path)

        class FakeLoginClient:
            login_sync = None  # 端点以 client.login_sync 取参，仅需存在

            async def run(self, fn, *args, **kwargs):
                return {"success": True, "uid": 264001, "username": "测试acer", "cookies": {"k": "v"}}

            def close(self):
                pass

        saved: List[tuple] = []
        monkeypatch.setattr(adapter_mod, "AcfunClient", lambda: FakeLoginClient())
        monkeypatch.setattr(adapter_mod, "save_cookies", lambda *a: saved.append(a))
        applied: List[str] = []

        async def _fake_apply(username: str) -> None:
            applied.append(username)

        monkeypatch.setattr(adapter_mod, "_apply_login_success", _fake_apply)
        resp = client.post("/channels/acfun/login", json={"username": "13800000000", "password": "pw"})
        body = resp.json()
        assert body["success"] is True and body["uid"] == "264001"
        assert saved and saved[0][0] == "13800000000"
        assert applied == ["13800000000"]

    def test_login_failure_passthrough(self, monkeypatch, tmp_path):
        client, adapter_mod = self._app(monkeypatch, tmp_path)

        class FailClient:
            login_sync = None

            async def run(self, fn, *args, **kwargs):
                return {"success": False, "error_msg": "账号或密码错误", "need_captcha": False}

            def close(self):
                pass

        monkeypatch.setattr(adapter_mod, "AcfunClient", lambda: FailClient())
        resp = client.post("/channels/acfun/login", json={"username": "u", "password": "bad"})
        body = resp.json()
        assert body["success"] is False and "密码" in body["error_msg"]

    def test_status_without_credential(self, monkeypatch, tmp_path):
        client, adapter_mod = self._app(monkeypatch, tmp_path)
        monkeypatch.setattr(adapter_mod, "load_cookies", lambda: None)
        resp = client.get("/channels/acfun/login/status")
        body = resp.json()
        assert body["logined"] is False
        assert body["channel_running"] is False

    def test_logout_disables_config(self, monkeypatch, tmp_path):
        client, adapter_mod = self._app(monkeypatch, tmp_path)
        cleared: List[bool] = []
        monkeypatch.setattr(adapter_mod, "clear_cookies", lambda: cleared.append(True))
        resp = client.post("/channels/acfun/logout")
        assert resp.json()["success"] is True
        assert cleared == [True]
        cfg = json.loads((tmp_path / "channel_config.json").read_text(encoding="utf-8"))
        assert cfg["enabled"] is False


# ======================================================================
# 扫码登录（qr_login.py 状态机 + /qr/* 路由，全程假 httpx 不触网）
# ======================================================================


class _FakeResp:
    def __init__(self, payload: Any = None, headers: Any = None, exc: Exception = None):
        self._payload = payload
        self.headers = headers
        self._exc = exc
        self.cookies: Dict[str, str] = {}

    def json(self) -> Any:
        return self._payload


class _FakeAsyncClient:
    """httpx.AsyncClient 替身：跨实例共享响应队列。"""

    queue: List[_FakeResp] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.cookies: Dict[str, str] = {}

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def get(self, url: str, params: Any = None) -> _FakeResp:
        resp = _FakeAsyncClient.queue.pop(0)
        if resp._exc is not None:
            raise resp._exc
        return resp


def _httpx_headers(pairs: List[str]) -> Any:
    import httpx

    return httpx.Headers([("set-cookie", p) for p in pairs])


@pytest.fixture()
def qr_manager(monkeypatch):
    from channels.acfun import qr_login

    _FakeAsyncClient.queue = []
    monkeypatch.setattr(qr_login.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(qr_login, "_QR_MANAGER", None)
    return qr_login.get_qr_manager()


def _start_payload() -> Dict[str, Any]:
    return {"result": 0, "expireTime": 120000, "qrLoginSignature": "sig1",
            "imageData": "aGVsbG8=", "qrLoginToken": "tok1"}


class TestQrLoginManager:
    async def test_start(self, qr_manager):
        _FakeAsyncClient.queue.append(_FakeResp(_start_payload()))
        result = await qr_manager.start()
        assert result["qr_png"].startswith("data:image/png;base64,")
        assert result["expire_seconds"] == 120
        assert result["session_id"]

    async def test_start_failure_raises(self, qr_manager):
        _FakeAsyncClient.queue.append(_FakeResp({"result": 500, "error_msg": "boom"}))
        with pytest.raises(RuntimeError):
            await qr_manager.start()

    async def test_poll_wait_on_timeout(self, qr_manager):
        import httpx as real_httpx

        _FakeAsyncClient.queue.append(_FakeResp(_start_payload()))
        sid = (await qr_manager.start())["session_id"]
        _FakeAsyncClient.queue.append(_FakeResp(exc=real_httpx.TimeoutException("slow")))
        result = await qr_manager.poll(sid)
        assert result["status"] == "wait"

    async def test_poll_scaned(self, qr_manager):
        _FakeAsyncClient.queue.append(_FakeResp(_start_payload()))
        sid = (await qr_manager.start())["session_id"]
        _FakeAsyncClient.queue.append(_FakeResp({"result": 100400002}))
        assert (await qr_manager.poll(sid))["status"] == "scaned"

    async def test_poll_confirmed_harvests_cookies(self, qr_manager):
        _FakeAsyncClient.queue.append(_FakeResp(_start_payload()))
        sid = (await qr_manager.start())["session_id"]
        _FakeAsyncClient.queue.append(_FakeResp({"result": 0, "qrLoginSignature": "sig2"}))
        _FakeAsyncClient.queue.append(_FakeResp(
            {"result": 0},
            headers=_httpx_headers([
                "acfun.midground.api_st=ST-TOKEN; Path=/; Domain=.acfun.cn",
                "auth_key=AUTH; Path=/",
                "empty_cookie=; Path=/",
            ]),
        ))
        result = await qr_manager.poll(sid)
        assert result["status"] == "confirmed"
        cookies = result["credential"]["cookies"]
        assert cookies["acfun.midground.api_st"] == "ST-TOKEN"
        assert cookies["auth_key"] == "AUTH"
        assert "empty_cookie" not in cookies  # 空值 cookie 丢弃
        # 再次轮询直接返回终态
        assert (await qr_manager.poll(sid))["status"] == "confirmed"

    async def test_poll_accept_rejected(self, qr_manager):
        _FakeAsyncClient.queue.append(_FakeResp(_start_payload()))
        sid = (await qr_manager.start())["session_id"]
        _FakeAsyncClient.queue.append(_FakeResp({"result": 0, "qrLoginSignature": "sig2"}))
        _FakeAsyncClient.queue.append(_FakeResp({"result": 21, "error_msg": "sign error"}))
        result = await qr_manager.poll(sid)
        assert result["status"] == "error"

    async def test_poll_invalid_session(self, qr_manager):
        assert (await qr_manager.poll("nope"))["status"] == "error"

    async def test_poll_expired_session(self, qr_manager, monkeypatch):
        _FakeAsyncClient.queue.append(_FakeResp(_start_payload()))
        sid = (await qr_manager.start())["session_id"]
        session = qr_manager._sessions[sid]
        session.created_at -= 9999
        result = await qr_manager.poll(sid)
        assert result["status"] == "timeout"

    async def test_poll_sign_error_is_timeout(self, qr_manager):
        _FakeAsyncClient.queue.append(_FakeResp(_start_payload()))
        sid = (await qr_manager.start())["session_id"]
        _FakeAsyncClient.queue.append(_FakeResp({"result": 21, "error_msg": "sign error"}))
        result = await qr_manager.poll(sid)
        assert result["status"] == "timeout" and "sign error" in result["error"]


class TestQrRouter:
    def _app(self, monkeypatch, tmp_path):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from channels.acfun import adapter as adapter_mod

        monkeypatch.setattr(
            adapter_mod, "_channel_config_file", lambda: tmp_path / "channel_config.json",
        )
        (tmp_path / "channel_config.json").write_text('{"enabled": false}', encoding="utf-8")
        app = FastAPI()
        app.include_router(adapter_mod.build_router(), prefix="/channels/acfun")
        return TestClient(app), adapter_mod

    def test_qr_start_and_discard(self, monkeypatch, tmp_path):
        client, adapter_mod = self._app(monkeypatch, tmp_path)

        class FakeMgr:
            async def start(self):
                return {"session_id": "s1", "qr_png": "data:image/png;base64,x", "expire_seconds": 120}

            async def discard(self, sid):
                pass

        from channels.acfun import qr_login

        monkeypatch.setattr(qr_login, "get_qr_manager", lambda: FakeMgr())
        resp = client.post("/channels/acfun/qr/start")
        assert resp.json()["session_id"] == "s1"
        assert client.delete("/channels/acfun/qr/s1").json()["status"] == "ok"

    def test_qr_confirmed_applies_credential(self, monkeypatch, tmp_path):
        client, adapter_mod = self._app(monkeypatch, tmp_path)

        class FakeMgr:
            async def poll(self, sid):
                return {"status": "confirmed", "credential": {"cookies": {"k": "v"}}}

        applied: List[Dict[str, str]] = []

        async def _fake_apply(cookies):
            applied.append(cookies)
            return {"success": True, "uid": "264001", "username": "测试acer"}

        from channels.acfun import qr_login

        monkeypatch.setattr(qr_login, "get_qr_manager", lambda: FakeMgr())
        monkeypatch.setattr(adapter_mod, "_apply_qr_credential", _fake_apply)
        body = client.get("/channels/acfun/qr/s1/status").json()
        assert body["status"] == "confirmed" and body["success"] is True
        assert applied == [{"k": "v"}]

    def test_qr_status_error_passthrough(self, monkeypatch, tmp_path):
        client, _ = self._app(monkeypatch, tmp_path)

        class FakeMgr:
            async def poll(self, sid):
                return {"status": "error", "error": "会话不存在"}

        from channels.acfun import qr_login

        monkeypatch.setattr(qr_login, "get_qr_manager", lambda: FakeMgr())
        body = client.get("/channels/acfun/qr/none/status").json()
        assert body["status"] == "error"


# ======================================================================
# 通知拉取容错与轮询故障隔离（空分类页 None.attrs 回归）
# ======================================================================


class TestNotificationParsing:
    """自研容错通知解析器（HTML fixture 驱动，不触网）。"""

    def _client(self, html: str, tail: bool = True):
        from channels.acfun.client import AcfunClient

        text = json.dumps({"html": html}) + ("/*<!-- fetch-stream -->*/" if tail else "")
        resp = SimpleNamespace(text=text)
        http = SimpleNamespace(get=lambda url, params=None: resp)
        client = AcfunClient()
        client._acer = SimpleNamespace(is_logined=True, client=http)
        return client

    _REPLY_HTML = """
    <div id="listview" totalcount="2">
      <ul>
        <div class="intro"><a href="/v/ac12345">测试视频</a></div>
        <div class="msg-replied"><span class="inner">我的原评论</span></div>
        <div class="titlebar"><a class="name" href="https://www.acfun.cn/u/1001">acer甲</a>
          <span class="time">2026-09-01 10:00</span></div>
        <a class="msg-reply" href="/v/ac12345#ncid=9001"><span class="inner">这是回复内容</span></a>
        <div class="content"><span class="intro">回复了你的评论</span></div>
      </ul>
      <ul><div class="intro"><a href="/v/ac999">坏条目缺回复体</a></div></ul>
    </div>"""

    def test_reply_page_parsed_and_bad_item_skipped(self):
        items = self._client(self._REPLY_HTML).get_notifications("reply")
        assert len(items) == 1  # 坏条目跳过不拖垮整页
        item = items[0]
        assert item["content"] == "这是回复内容"
        assert item["ncid"] == "9001"
        assert item["uid"] == "1001" and item["username"] == "acer甲"
        assert item["content_url"] == "https://www.acfun.cn/v/ac12345"
        assert item["replied"] == "我的原评论"

    def test_empty_page_and_missing_tail(self):
        assert self._client('<div class="empty">暂无通知</div>').get_notifications("reply") == []
        assert self._client("<div id='listview'></div>", tail=False).get_notifications("reply") == []

    def test_unknown_kind(self):
        assert self._client("").get_notifications("mystery") == []

    def test_notice_page(self):
        html = """
        <div id="listview" totalcount="1"><ul>
          <div>公告标题</div>
          <div>系统维护公告内容<a href="https://www.acfun.cn/info">详情</a></div>
          <span class="msg-item-time">2026-09-01 12:00</span>
        </ul></div>"""
        items = self._client(html).get_notifications("notice")
        assert len(items) == 1
        assert items[0]["content_title"] == "公告标题"
        assert "系统维护" in items[0]["intro"]

    def test_system_page_links_classified(self):
        html = """
        <div id="listview" totalcount="1"><ul>
          <p>acer乙 关注了你</p>
          <a href="https://www.acfun.cn/u/2002">acer乙</a>
          <p class="msg-item-time">昨天 12:00</p>
        </ul></div>"""
        items = self._client(html).get_notifications("system")
        assert len(items) == 1
        assert items[0]["intro"] == "acer乙 关注了你"
        assert items[0]["up"] == ["acer乙", "https://www.acfun.cn/u/2002"]

    def test_like_page(self):
        html = """
        <div id="listview" totalcount="1"><ul>
          <div class="titlebar"><a class="name" href="https://www.acfun.cn/u/3003">acer丙</a>
            <span class="time">1小时前</span></div>
          <a class="replied" href="https://www.acfun.cn/v/ac12345#ncid=9001">
            <span class="clamp-text"><span class="inner">我的被赞评论</span></span></a>
        </ul></div>"""
        items = self._client(html).get_notifications("like")
        assert len(items) == 1
        assert items[0]["ncid"] == "9001"
        assert items[0]["uid"] == "3003"
        assert items[0]["replied"] == "我的被赞评论"


class TestPollerFaultIsolation:
    async def test_partial_failure_does_not_poison_cycle(self, data_dir):
        client = FakeClient()
        channel = PollerChannel(client)
        poller = NotificationPoller(channel)
        poller._cursors.load()
        await poller._poll_once()  # 播种

        def fail_like(kind: str, page: int = 1):
            if kind == "like":
                raise RuntimeError("like page exploded")
            return client.notifications.get(kind, [])

        client.get_notifications = fail_like  # type: ignore[method-assign]
        client.notifications["reply"] = [_reply_item(ncid="9101", content="新回复")]
        await poller._poll_once()  # like 失败不阻塞 reply
        assert [m.content for m in channel.messages] == ["新回复"]
        assert "like" in poller.last_error
        assert poller.last_poll_at > 0

    async def test_all_kinds_fail_raises_for_backoff(self, data_dir):
        client = FakeClient()

        def fail_all(kind: str, page: int = 1):
            raise RuntimeError("network down")

        client.get_notifications = fail_all  # type: ignore[method-assign]
        channel = PollerChannel(client)
        poller = NotificationPoller(channel)
        poller._cursors.load()
        with pytest.raises(RuntimeError, match="全部通知类别拉取失败"):
            await poller._poll_once()


class TestLikeNoiseReduction:
    """点赞降噪：未开启触发时仅计数不进历史。"""

    async def test_like_counted_not_dispatched(self, data_dir):
        client = FakeClient()
        channel = PollerChannel(client)  # like_trigger_mind 默认 False
        poller = NotificationPoller(channel)
        poller._cursors.load()
        await poller._poll_once()  # 播种
        client.notifications["like"] = [_reply_item(ncid="7001"), _reply_item(ncid="7002")]
        await poller._poll_once()
        assert channel.messages == []  # 不进历史
        assert poller.like_count == 2

    async def test_like_dispatched_when_trigger_on(self, data_dir):
        client = FakeClient()
        channel = PollerChannel(client, like_trigger_mind=True)
        poller = NotificationPoller(channel)
        poller._cursors.load()
        await poller._poll_once()
        client.notifications["like"] = [_reply_item(ncid="7003", replied="被赞评论")]
        await poller._poll_once()
        assert len(channel.messages) == 1
        assert channel.messages[0].trigger_mind is True
