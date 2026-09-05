"""AcFun 频道适配器 — 基于 acfunsdk（账号密码登录 + 通知轮询入站 + 评论/弹幕出站）。

AcFun 平台无实时私信推送能力（acfunsdk-ws 的 IM reader 为空桩且依赖钉死、长期失修，
不引入），频道形态为：通知中心轮询入站（评论回复/@ 触发思维，点赞/投蕉/系统通知
按配置记录或触发）+ 评论回复/直播弹幕出站闭环 + SDK 全能力工具面（acfun_*）。

Model Experience 三行声明：
1. 模型看到什么：AcFun 互动事件（评论回复/@/点赞/投蕉/系统通知）经 AdapterMessage
   进入会话；直播模式下上下文 provider（volatile 尾部动态区）注入房间实时快照
   （连接状态/观众数/最近弹幕/礼物），点名弹幕与礼物事件触发思维；工具目录新增
   acfun_* 组（channel_ops，频道注册时才挂载 schema）。
2. token 影响：入站消息按正常会话计轮；直播状态块约 200-450 token（随弹幕窗口浮动，
   live_recent_window 可调，直播模式关闭时零注入）；工具 schema 为增量（约 53 个窄参
   数工具），频道未启用/未注册时零开销。
3. 缓存影响：不触碰 stable/summary/conversation 前缀层；直播状态走 provider 层
   （VOL_SESSION+4，历史之后的尾部动态区），一轮滞后的后台快照，符合既有缓存纪律。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel

from agent.channel.base import BaseChannel, ChannelMetadata
from agent.channel.channel_types import ChannelCapability, ChannelStatus
from agent.channel.schemas import (
    ChannelInfo,
    ChannelType,
    ChannelUser,
    HealthStatus,
    SendRequest,
    SendResponse,
)
from core.log import log

from .client import AcfunClient
from .config import AcfunConfig
from .context import register_live_context_provider
from .live.manager import LiveSessionManager
from .poller import NotificationPoller
from .send import send_channel_text
from .state import clear_cookies, load_cookies, save_cookies
from .tools import AcfunToolsMixin

# 直播状态上下文 provider 随模块注册（幂等；模式关闭时 provide 返回 None 零注入）
register_live_context_provider()

_SELF_INFO_TTL = 300.0  # 自身资料缓存（秒）
_HEALTH_PROBE_TTL = 60.0  # 健康探针最小间隔（秒）


class AcfunChannel(AcfunToolsMixin, BaseChannel[AcfunConfig]):
    """AcFun 频道（acfunsdk HTTP 客户端 + 通知轮询）。"""

    _entity_description = "AcFun 频道（acfunsdk）"

    channel_id = "acfun"
    display_name = "AcFun"
    capabilities: Set[ChannelCapability] = {
        ChannelCapability.SEND_TEXT,
        ChannelCapability.REPLY_TO,
        ChannelCapability.GET_CHAT_INFO,
    }
    metadata = ChannelMetadata(
        name="AcFun",
        description="AcFun 弹幕视频网频道（账号密码登录，通知轮询入站，评论/直播弹幕出站）",
        version="1.0.0",
        author="AnelfAgent",
        homepage="https://www.acfun.cn",
        tags=["acfun", "a站"],
    )
    _Configs = AcfunConfig

    def __init__(self) -> None:
        self.client = AcfunClient()
        self.poller = NotificationPoller(self)
        self.live_manager = LiveSessionManager(self)
        self.live_danmaku_last_sent: Dict[str, float] = {}
        self._self_info: Optional[ChannelUser] = None
        self._self_info_at: float = 0.0
        self._last_probe_at: float = 0.0
        self._last_probe_ok: bool = False
        super().__init__()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """启动：恢复 cookie 会话（或配置账密兜底登录）→ 起通知轮询。"""
        credential = load_cookies()
        restored = False
        if credential is not None:
            restored = await self.client.run(self.client.restore_sync, credential["cookies"])
            if restored:
                log(f"AcFun: cookie 会话恢复成功 uid={self.client.uid}", tag="通道")
        if not restored and self.config.username and self.config.password:
            result = await self.client.run(
                self.client.login_sync, self.config.username, self.config.password,
            )
            if result.get("success"):
                save_cookies(self.config.username, result.get("uid", ""), result["cookies"])
                restored = True
                log(f"AcFun: 配置账密登录成功 uid={result.get('uid')}", tag="通道")
            else:
                log(f"AcFun: 配置账密登录失败: {result.get('error_msg')}", "WARNING", tag="通道")
        if not restored:
            raise RuntimeError("AcFun 未登录：请在频道页完成账号登录（或配置 username/password）")
        await self.poller.start()
        if self.config.live_mode:
            await self.live_manager.set_mode(True)
        log("AcFun: 频道已启动，通知轮询运行中", tag="通道")

    async def stop(self) -> None:
        """停止：收轮询任务 + 断开直播连接 + 关闭底层连接（凭据保留）。"""
        await self.poller.stop()
        await self.live_manager.stop()
        self.client.close()
        self._self_info = None
        log("AcFun: 频道已停止", tag="通道")

    def _on_config_changed(self, key: str, value: Any) -> None:
        """配置变更监听走 reload_config 统一入口（含直播 diff 应用）。"""
        self.reload_config()

    def reload_config(self) -> bool:
        """热重载配置：diff 直播模式与观察列表并即时应用（Web 表单/AI 工具热切换入口）。"""
        prev_mode = self.config.live_mode
        prev_rooms = self.live_manager.watched
        ok = super().reload_config()
        if not ok:
            return False
        if self.config.live_mode != prev_mode or (
            self.config.live_mode and self._parse_rooms() != prev_rooms
        ):
            self._schedule_live_apply()
        return True

    def _parse_rooms(self) -> List[str]:
        raw = str(self.config.live_watch_rooms or "")
        return [x.strip() for x in raw.split(",") if x.strip().isdigit()]

    def _schedule_live_apply(self) -> None:
        """在事件循环内异步应用直播配置变更（无循环环境静默跳过）。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def _apply() -> None:
            if bool(self.config.live_mode) != self.live_manager.mode_enabled:
                await self.live_manager.set_mode(bool(self.config.live_mode))
            elif self.config.live_mode:
                await self.live_manager.sync_rooms()

        loop.create_task(_apply(), name="acfun-live-apply")

    async def live_enter_params_async(self, uid: str) -> Optional[Dict[str, Any]]:
        """连接层的进房参数回调（HTTP 调用经线程移出事件循环）。"""
        if not self.client.is_logined:
            return None
        try:
            return await self.client.run(self.client.live_enter_params, uid)
        except Exception as exc:
            log(f"AcFun直播: 进房参数获取失败 live:{uid}: {exc}", "WARNING", tag="通道")
            return None

    def persist_live_config(self, *, live_mode: Optional[bool] = None,
                            rooms: Optional[List[str]] = None) -> None:
        """直播模式/观察列表变更后写回统一配置（AI 工具与 Web 直播 API 同源的持久化入口）。"""
        from agent.channel.config import set_channel_config

        try:
            updates: Dict[str, Any] = {}
            if live_mode is not None:
                updates["live_mode"] = live_mode
            if rooms is not None:
                updates["live_watch_rooms"] = ",".join(rooms)
            if updates:
                set_channel_config("acfun", **updates)
        except Exception as exc:
            log(f"AcFun直播: 配置持久化失败（运行时变更仍已生效）: {exc}", "DEBUG", tag="通道")

    def on_login_expired(self) -> None:
        """登录态失效（轮询检测到）：置 ERROR 交频道守护退避重启，detail 引导重新登录。"""
        self._status = ChannelStatus.ERROR
        log("AcFun: 登录态失效，频道置 ERROR（请重新登录）", "WARNING", tag="通道")

    # ------------------------------------------------------------------
    # 发送
    # ------------------------------------------------------------------

    def live_danmaku_cooldown_seconds(self) -> int:
        return max(int(self.config.live_danmaku_cooldown_seconds), 0)

    async def forward_message(self, request: SendRequest) -> SendResponse:
        return await self._forward_via_segment_map(request)

    async def send_text(
        self,
        chat_id: str,
        text: str,
        *,
        reply_to: Optional[str] = None,
        parse_mode: Optional[str] = None,
        silent: bool = False,
        channel_type: str = "private",
        **kwargs: Any,
    ) -> str:
        """发送文本：按 chat_id 前缀路由（comment:/live:/user: 见 send.py）。"""
        return await send_channel_text(self, chat_id, text, reply_to=reply_to)

    def is_known_group(self, target_id: str) -> bool:
        """评论区/直播间目标按群语义（供回复路由 channel_type 推断）。"""
        return target_id.startswith(("comment:", "live:"))

    # ------------------------------------------------------------------
    # 信息查询
    # ------------------------------------------------------------------

    async def get_self_info(self) -> ChannelUser:
        if self._self_info is not None and time.time() - self._self_info_at < _SELF_INFO_TTL:
            return self._self_info
        profile = self.client.profile  # 登录时已由 _get_personal 拉取，读内存即可
        info = ChannelUser(
            platform=self.channel_id,
            user_id=str(profile.get("userId") or self.client.uid),
            user_name=str(profile.get("userName") or self.client.username),
            avatar=str(profile.get("headUrl") or ""),
            is_bot=True,
            extra={"signature": profile.get("signature", "")},
        )
        self._self_info = info
        self._self_info_at = time.time()
        return info

    async def get_user_info(self, user_id: str, channel_id: str = "") -> ChannelUser:
        try:
            profile = await self.client.run(self.client.user_info, user_id)
            return ChannelUser(
                platform=self.channel_id,
                user_id=str(user_id),
                user_name=str(profile.get("userName") or profile.get("name") or user_id),
                avatar=str(profile.get("headUrl") or ""),
            )
        except Exception:
            return ChannelUser(platform=self.channel_id, user_id=str(user_id), user_name=str(user_id))

    async def get_channel_info(self, channel_id: str) -> ChannelInfo:
        """会话信息：comment: → 内容标题；live: → 直播间标题；system → 系统通知。"""
        from .parser import SYSTEM_CHANNEL_ID
        from .send import parse_chat_target

        if channel_id == SYSTEM_CHANNEL_ID:
            return ChannelInfo(channel_id=channel_id, channel_name="AcFun 系统通知")
        if channel_id == "notification":
            return ChannelInfo(channel_id=channel_id, channel_name="AcFun 通知中心")
        target = parse_chat_target(channel_id)
        if target is None:
            return ChannelInfo(channel_id=channel_id, channel_name=channel_id)
        kind, rest = target
        try:
            if kind == "comment":
                rtype, rid = rest.split(":", 1)
                name = await self._resolve_content_title(rtype, rid)
                return ChannelInfo(
                    channel_id=channel_id, channel_name=name or channel_id,
                    channel_type=ChannelType.GROUP,
                    extra={"rtype": rtype, "rid": rid},
                )
            if kind == "live":
                info = await self.client.run(self.client.live_info, rest)
                return ChannelInfo(
                    channel_id=channel_id,
                    channel_name=str(info.get("title") or channel_id),
                    channel_type=ChannelType.GROUP,
                    extra=info,
                )
            user = await self.get_user_info(rest)
            return ChannelInfo(channel_id=channel_id, channel_name=user.user_name)
        except Exception as exc:
            log(f"AcFun: 会话信息查询失败 {channel_id}: {exc}", "DEBUG", tag="通道")
            return ChannelInfo(channel_id=channel_id, channel_name=channel_id)

    async def _resolve_content_title(self, rtype: str, rid: str) -> str:
        fetch = {"1": "bangumi_info", "2": "video_info", "3": "article_info", "10": "moment_info"}.get(rtype)
        if fetch is None:
            return ""
        info = await self.client.run(getattr(self.client, fetch), rid)
        return str(info.get("title") or info.get("text") or "")

    # ------------------------------------------------------------------
    # 健康探针 / 状态
    # ------------------------------------------------------------------

    async def health_check(self) -> HealthStatus:
        """轻量探活（未读计数调用），60s TTL 防看门狗高频打接口；永不抛异常。"""
        if not self.client.is_logined:
            return HealthStatus(healthy=False, detail="未登录", last_error="not logged in")
        if time.time() - self._last_probe_at < _HEALTH_PROBE_TTL:
            return HealthStatus(healthy=self._last_probe_ok, detail="cached probe")
        try:
            unread = await self.client.run(self.client.unread)
            self._last_probe_at = time.time()
            self._last_probe_ok = unread is not None
            if unread is None:
                return HealthStatus(healthy=False, detail="会话探活无响应", last_error="unread probe None")
            return HealthStatus(healthy=True, detail=f"在线 uid={self.client.uid}")
        except Exception as exc:
            self._last_probe_at = time.time()
            self._last_probe_ok = False
            return HealthStatus(healthy=False, detail="会话探活失败", last_error=str(exc))

    def get_status_info(self) -> Dict[str, Any]:
        info = super().get_status_info()
        info["online"] = self.client.is_logined
        info["self_id"] = self.client.uid
        info["poll_running"] = self.poller.running
        info["last_poll_at"] = self.poller.last_poll_at
        if self.poller.like_count:
            info["like_count"] = self.poller.like_count
        if self.client.is_logined:
            info["detail"] = f"已登录 {self.client.username}(uid={self.client.uid})，通知轮询中"
        else:
            info["detail"] = "未登录（请在频道页完成账号登录）"
        if self.poller.last_error:
            info["detail"] += f"；轮询异常: {self.poller.last_error}"
        info["target_syntax"] = "comment:{rtype}:{rid} | live:{uid}"
        live_snap = self.live_manager.snapshot()
        info["live_mode"] = live_snap["mode"]
        if live_snap["mode"]:
            rooms = live_snap["rooms"]
            summary = "、".join(
                f"live:{r['uid']}={r['state']}" + (f"({r['detail']})" if r["detail"] else "")
                for r in rooms
            ) or "无连接房间"
            info["live_rooms"] = [r["state"] for r in rooms]
            info["detail"] += f"；直播模式: {summary}"
        return info

    # ------------------------------------------------------------------
    # HTTP 路由钩子（Web 登录）
    # ------------------------------------------------------------------

    def get_router(self) -> Optional[Any]:
        return build_router()


CHANNEL_CLASS = AcfunChannel


# ======================================================================
# WebUI 登录路由（挂载于 /api/channels/acfun，无需频道已启用）
# ======================================================================

class _LoginBody(BaseModel):
    """登录请求体（模块级定义：__future__ annotations 下 FastAPI 需可解析的类型引用）。"""

    username: str
    password: str
    key: str = ""
    captcha: str = ""


class _LiveModeBody(BaseModel):
    """直播模式开关请求体。"""

    enabled: bool


class _LiveWatchBody(BaseModel):
    """观察房间增删请求体。"""

    uid: str


def _persist_live_config_offline(*, live_mode: Optional[bool] = None,
                                 rooms: Optional[List[str]] = None) -> None:
    """频道未注册/未运行时直接写统一配置（下次启动生效）。"""
    from agent.channel.config import set_channel_config

    updates: Dict[str, Any] = {}
    if live_mode is not None:
        updates["live_mode"] = live_mode
    if rooms is not None:
        updates["live_watch_rooms"] = ",".join(rooms)
    if updates:
        set_channel_config("acfun", **updates)


def build_router() -> Any:
    """AcFun 频道 HTTP 路由：账号密码登录 / 登录状态 / 退出登录。"""
    from fastapi import APIRouter

    router = APIRouter()

    @router.post("/login")
    async def login(body: _LoginBody) -> Dict[str, Any]:
        """账号密码登录：成功则保存 cookie 凭据 → 启用并（重）启动频道。"""
        if not body.username.strip() or not body.password:
            return {"success": False, "error_msg": "账号与密码不能为空"}
        client = AcfunClient()
        try:
            result = await client.run(
                client.login_sync, body.username.strip(), body.password, body.key, body.captcha,
            )
        except Exception as exc:
            return {"success": False, "error_msg": f"登录请求异常: {exc}"}
        finally:
            client.close()
        if not result.get("success"):
            return result
        save_cookies(body.username.strip(), result.get("uid", ""), result["cookies"])
        await _apply_login_success(body.username.strip())
        return {
            "success": True,
            "uid": str(result.get("uid") or ""),
            "username": str(result.get("username") or body.username.strip()),
        }

    @router.get("/login/status")
    async def login_status() -> Dict[str, Any]:
        """登录状态（凭据存在性 + 运行中频道的实时登录态）。"""
        credential = load_cookies()
        status: Dict[str, Any] = {
            "logined": False,
            "uid": "",
            "username": "",
            "saved_at": None,
            "channel_running": False,
            "online": False,
        }
        if credential is not None:
            status.update({
                "logined": True,
                "uid": str(credential.get("uid") or ""),
                "username": str(credential.get("username") or ""),
                "saved_at": credential.get("saved_at"),
            })
        try:
            from agent.channel import get_channel_manager

            channel = get_channel_manager().get("acfun")
            if isinstance(channel, AcfunChannel):
                status["channel_running"] = channel.status == ChannelStatus.RUNNING
                status["online"] = channel.client.is_logined
                if channel.client.is_logined:
                    status["uid"] = channel.client.uid
                    status["username"] = channel.client.username
        except Exception:
            pass
        return status

    @router.post("/logout")
    async def logout() -> Dict[str, Any]:
        """退出登录：停频道 → 服务端登出 → 清凭据 → 配置置为停用。"""
        try:
            from agent.channel import get_channel_manager

            mgr = get_channel_manager()
            channel = mgr.get("acfun")
            if channel is not None and channel.status == ChannelStatus.RUNNING:
                await mgr.stop_channel("acfun")
        except Exception as exc:
            log(f"AcFun: 登出时停频道异常: {exc}", "WARNING", tag="通道")
        clear_cookies()
        from agent.channel.manager import set_channel_enabled
        set_channel_enabled("acfun", False)
        return {"success": True}

    # --------------------------------------------------------------
    # 直播模式（Web 面板实时控制/状态；与 AI 工具 live_* 同路径）
    # --------------------------------------------------------------

    @router.get("/live/status")
    async def live_status() -> Dict[str, Any]:
        """直播会话快照（房间状态/计数/诊断），供前端实时轮询。"""
        base: Dict[str, Any] = {"mode": False, "watched": [], "rooms": [],
                                "state_events": [], "channel_running": False, "logined": False}
        try:
            channel = _get_acfun_channel()
        except Exception:
            channel = None
        if channel is None:
            return base
        base["channel_running"] = channel.status == ChannelStatus.RUNNING
        base["logined"] = channel.client.is_logined
        base.update(channel.live_manager.snapshot())
        return base

    @router.post("/live/mode")
    async def live_mode(body: _LiveModeBody) -> Dict[str, Any]:
        """开关直播模式（运行中即时连接/断开；未运行仅持久化）。"""
        try:
            channel = _get_acfun_channel()
        except Exception:
            channel = None
        if channel is None or channel.status != ChannelStatus.RUNNING:
            _persist_live_config_offline(live_mode=body.enabled)
            return {"success": True, "live_mode": body.enabled,
                    "result": "配置已保存（频道未运行，启动后生效）"}
        result = await channel.live_manager.set_mode(body.enabled)
        channel.persist_live_config(live_mode=body.enabled)
        return {"success": True, "live_mode": body.enabled, "result": result}

    @router.post("/live/watch")
    async def live_watch(body: _LiveWatchBody) -> Dict[str, Any]:
        """添加观察房间。"""
        try:
            channel = _get_acfun_channel()
        except Exception:
            channel = None
        if channel is None:
            return {"success": False, "error_msg": "频道未注册（请先启用频道）"}
        result = await channel.live_manager.watch(body.uid)
        channel.persist_live_config(rooms=channel.live_manager.watched)
        return {"success": True, "result": result, "watched": channel.live_manager.watched}

    @router.post("/live/unwatch")
    async def live_unwatch(body: _LiveWatchBody) -> Dict[str, Any]:
        """移除观察房间。"""
        try:
            channel = _get_acfun_channel()
        except Exception:
            channel = None
        if channel is None:
            return {"success": False, "error_msg": "频道未注册（请先启用频道）"}
        result = await channel.live_manager.unwatch(body.uid)
        channel.persist_live_config(rooms=channel.live_manager.watched)
        return {"success": True, "result": result, "watched": channel.live_manager.watched}

    # --------------------------------------------------------------
    # 扫码登录（scan.acfun.cn QR 流程，与微信扫码同状态词汇）
    # --------------------------------------------------------------

    @router.post("/qr/start")
    async def qr_start() -> Dict[str, Any]:
        """拉取登录二维码，返回 {session_id, qr_png(data URL), expire_seconds}。"""
        from fastapi import HTTPException

        from .qr_login import get_qr_manager

        try:
            return await get_qr_manager().start()
        except Exception as exc:
            raise HTTPException(500, str(exc)) from exc

    @router.get("/qr/{session_id}/status")
    async def qr_status(session_id: str) -> Dict[str, Any]:
        """推进一次扫码状态检查；确认后自动写凭据并启动频道。"""
        from .qr_login import get_qr_manager

        result = await get_qr_manager().poll(session_id)
        credential = result.pop("credential", None)
        if result.get("status") == "confirmed" and credential:
            applied = await _apply_qr_credential(credential["cookies"])
            result.update(applied)
        return result

    @router.delete("/qr/{session_id}")
    async def qr_discard(session_id: str) -> Dict[str, str]:
        from .qr_login import get_qr_manager

        await get_qr_manager().discard(session_id)
        return {"status": "ok"}

    @router.get("/login/captcha")
    async def login_captcha() -> Dict[str, Any]:
        """获取账密登录的图形验证码（id.app.acfun.cn 代理，返回 {image, key}）。"""
        import httpx
        from fastapi import HTTPException

        try:
            async with httpx.AsyncClient(timeout=15.0, headers={
                "Referer": "https://www.acfun.cn/login/",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            }) as client:
                resp = await client.get("https://id.app.acfun.cn/rest/web/login/captcha")
                data = resp.json()
        except Exception as exc:
            raise HTTPException(502, f"验证码获取失败: {exc}") from exc
        if data.get("result") != 0:
            raise HTTPException(502, f"验证码获取失败: {data.get('error_msg') or data.get('result')}")
        return {"image": data.get("image", ""), "key": data.get("key", "")}

    return router


async def _apply_qr_credential(cookies: Dict[str, str]) -> Dict[str, Any]:
    """扫码确认后：cookie 恢复探活（拿 uid/用户名）→ 凭据落盘 → 频道按新凭据（重）启动。"""
    client = AcfunClient()
    try:
        ok = await client.run(client.restore_sync, cookies)
        if not ok:
            return {"success": False, "error_msg": "登录凭据校验失败，请重试扫码"}
        username = client.username or ""
        uid = client.uid
    finally:
        client.close()
    save_cookies(username, uid, cookies)
    await _apply_login_success(username)
    return {"success": True, "uid": str(uid), "username": username}


def _get_acfun_channel() -> "AcfunChannel":
    """取已注册的 AcFun 频道实例（未注册抛 LookupError）。"""
    from agent.channel import get_channel_manager

    channel = get_channel_manager().get("acfun")
    if not isinstance(channel, AcfunChannel):
        raise LookupError("acfun channel not registered")
    return channel


async def _apply_login_success(username: str) -> None:
    """登录成功后：写统一配置（enabled + username）→ 已注册则热重启，未注册则注册并启动。"""
    from agent.channel.config import set_channel_config

    set_channel_config("acfun", enabled=True, username=username)
    log(f"AcFun: 登录凭据已写入配置 username={username}", tag="通道")

    try:
        from agent.channel import get_channel_manager

        mgr = get_channel_manager()
        channel = mgr.get("acfun")
        if channel is not None:
            # 配置变更监听已热更内存态；直接按新凭据重启
            if channel.status == ChannelStatus.RUNNING:
                await mgr.stop_channel("acfun")
            await mgr.start_channel("acfun")
            log("AcFun: 频道已按新凭据重启", tag="通道")
            return
        instance = AcfunChannel()
        mgr.register(instance)
        await mgr.start_channel("acfun")
        log("AcFun: 频道已注册并启动", tag="通道")
    except Exception as exc:
        log(f"AcFun: 登录后自动启动失败（可手动在频道页启动）: {exc}", "WARNING", tag="通道")
