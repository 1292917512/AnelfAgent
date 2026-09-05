"""AcFun 工具层 — SDK 全能力面的 @channel_tool 封装（注册为 acfun_<方法名>）。

所有工具经 ``_ac`` 统一收口：登录检查 → 串行线程化调用 AcfunClient 同步方法 →
结果/异常规整为 _ok/_err JSON。SDK 异常按语义归因（未登录/404/结构变更/网络），
供工具守卫与模型重试决策消费。

写操作（评论/关注/合辑/礼物/举报等）标 sensitive=True，走审批门控。

会话目标语法：评论区 ``comment:{rtype}:{rid}``（rtype 1 番剧/2 视频/3 文章/10 动态）、
直播间 ``live:{uid}``——与 send.py / parser.py 同构。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

from acfunsdk.exceptions import AcExploded, NotInCar, ShuiNi, TingBuDong

from agent.channel.channel_types import _err, _ok
from agent.channel.tool_bridge import channel_tool

from .send import send_live_danmaku_gated

if TYPE_CHECKING:
    from .client import AcfunClient
    from .live.manager import LiveSessionManager


def _acfun_error(exc: Exception) -> str:
    """SDK 异常 → 面向模型的错误文案（按语义归因，不暴露堆栈）。"""
    if isinstance(exc, NotInCar):
        return "AcFun 未登录，请先在频道页完成账号登录"
    if isinstance(exc, ShuiNi):
        return str(exc) or "内容不存在（404）"
    if isinstance(exc, TingBuDong):
        return f"接口数据格式异常（平台页面结构可能已变更）: {exc}"
    if isinstance(exc, AcExploded):
        return f"AcFun 网络/服务异常: {exc}"
    if isinstance(exc, (ValueError, KeyError, IndexError)):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


def _to_int(value: Any, name: str) -> int:
    """LLM 可能把数字按字符串传，统一转 int，失败抛 ValueError。"""
    try:
        return int(str(value).strip().lstrip("ac"))
    except (TypeError, ValueError):
        raise ValueError(f"参数 {name} 不是有效数字: {value!r}") from None


def _to_bool(value: Any) -> bool:
    return str(value).strip().lower() in ("true", "1", "yes", "on")


class AcfunToolsMixin:
    """AcFun 频道工具集（由 AcfunChannel 继承，下列成员由频道实例提供）。

    注：config 仅作类型注解（类级注解不创建属性），
    运行时由 BaseChannel 的属性提供，mixin 不得定义同名成员遮蔽。
    """

    client: "AcfunClient"
    live_manager: "LiveSessionManager"
    live_danmaku_last_sent: Dict[str, float]
    config: Any  # AcfunConfig
    persist_live_config: Any  # AcfunChannel.persist_live_config

    def live_danmaku_cooldown_seconds(self) -> int:
        """直播弹幕同房间冷却秒数（频道实现，读取热配置）。"""
        raise NotImplementedError

    async def _ac(self, fn_name: str, *args: Any, **kwargs: Any) -> str:
        """统一调用 AcfunClient 同步方法并规整结果为 JSON。"""
        if not self.client.is_logined:
            return _err("AcFun 未登录，请先在频道页完成账号登录")
        fn = getattr(self.client, fn_name, None)
        if fn is None:
            return _err(f"客户端方法不存在: {fn_name}")
        try:
            result = await self.client.run(fn, *args, **kwargs)
        except Exception as exc:
            return _err(_acfun_error(exc))
        if isinstance(result, bool):
            return _ok({"result": "ok"}) if result else _err("操作未成功（平台拒绝或条件不满足）")
        if isinstance(result, list):
            return _ok({"items": result, "count": len(result)})
        if isinstance(result, dict):
            return _ok(result)
        if result is None:
            return _err("接口无有效返回（可能需要创作权限或目标不存在）")
        return _ok({"data": result})

    # ------------------------------------------------------------------
    # 搜索 / 内容信息
    # ------------------------------------------------------------------

    @channel_tool(description="AcFun 全站搜索（视频/文章/用户/番剧/合辑）")
    async def search(self, keyword: str, search_type: str = "complex",
                     page: str = "1", sortby: str = "1") -> str:
        """搜索 AcFun 内容。search_type: complex 综合/video 视频/article 文章/user 用户/bangumi 番剧/album 合辑；sortby: 1 相关/2 最多观看/3 最多评论/4 最多收藏/5 最新发布。"""
        return await self._ac("search", keyword, search_type, _to_int(page, "page"), _to_int(sortby, "sortby"))

    @channel_tool(description="获取 AcFun 视频稿件详情（标题/UP主/播放量/弹幕分P等）")
    async def video_info(self, ac_id: str) -> str:
        """获取视频详情。ac_id 为 ac 号（如 12345 或 ac12345）。"""
        return await self._ac("video_info", ac_id)

    @channel_tool(description="获取 AcFun 文章详情")
    async def article_info(self, ac_id: str) -> str:
        """获取文章详情。ac_id 为 ac 号。"""
        return await self._ac("article_info", ac_id)

    @channel_tool(description="获取 AcFun 番剧详情")
    async def bangumi_info(self, aa_id: str) -> str:
        """获取番剧详情。aa_id 为 aa 号。"""
        return await self._ac("bangumi_info", aa_id)

    @channel_tool(description="获取 AcFun 用户资料")
    async def user_info(self, uid: str) -> str:
        """获取用户公开资料。uid 为 AcFun 用户 ID。"""
        return await self._ac("user_info", uid)

    @channel_tool(description="获取 AcFun 用户空间内容列表（稿件/关注/粉丝）")
    async def user_content(self, uid: str, kind: str = "video", page: str = "1") -> str:
        """获取用户空间列表。kind: video 视频/article 文章/album 合辑/following 关注/followed 粉丝。"""
        return await self._ac("user_content", uid, kind, _to_int(page, "page"))

    @channel_tool(description="获取 AcFun 动态详情")
    async def moment_info(self, am_id: str) -> str:
        """获取动态详情。am_id 为 am 号。"""
        return await self._ac("moment_info", am_id)

    # ------------------------------------------------------------------
    # 评论
    # ------------------------------------------------------------------

    @channel_tool(description="读取 AcFun 内容评论区（含楼中楼预览）")
    async def list_comments(self, rtype: str, rid: str, page: str = "1") -> str:
        """读取评论。rtype: 1 番剧/2 视频/3 文章/10 动态；rid 为内容 ID。"""
        return await self._ac("list_comments", _to_int(rtype, "rtype"), rid, _to_int(page, "page"))

    @channel_tool(description="在 AcFun 内容下发表评论或回复评论", sensitive=True)
    async def send_comment(self, rtype: str, rid: str, content: str, reply_id: str = "") -> str:
        """发表评论；填 reply_id（评论 ID）则为楼中楼回复。rtype: 1 番剧/2 视频/3 文章/10 动态。"""
        return await self._ac("send_comment", _to_int(rtype, "rtype"), rid, content, reply_id or None)

    @channel_tool(description="删除自己的 AcFun 评论", sensitive=True)
    async def delete_comment(self, rtype: str, rid: str, comment_id: str) -> str:
        """删除评论（仅限自己发布的）。rtype: 1 番剧/2 视频/3 文章/10 动态。"""
        return await self._ac("delete_comment", _to_int(rtype, "rtype"), rid, comment_id)

    @channel_tool(description="点赞 / 取消点赞 AcFun 评论")
    async def like_comment(self, rtype: str, rid: str, comment_id: str, on_off: str = "true") -> str:
        """评论点赞。on_off: true 点赞 / false 取消。"""
        return await self._ac("like_comment", _to_int(rtype, "rtype"), rid, comment_id, _to_bool(on_off))

    # ------------------------------------------------------------------
    # 互动（点赞 / 投蕉 / 收藏 / 关注）
    # ------------------------------------------------------------------

    @channel_tool(description="点赞 / 取消点赞 AcFun 内容")
    async def like_content(self, rtype: str, rid: str, on_off: str = "true") -> str:
        """内容点赞。rtype: 1 番剧/2 视频/3 文章/10 动态。"""
        return await self._ac("like", _to_int(rtype, "rtype"), rid, _to_bool(on_off))

    @channel_tool(description="给 AcFun 内容投蕉（香蕉，1-5 根）")
    async def throw_banana(self, rtype: str, rid: str, count: str = "1") -> str:
        """投蕉支持内容。count 1-5。rtype: 1 番剧/2 视频/3 文章/10 动态。"""
        return await self._ac("throw_banana", _to_int(rtype, "rtype"), rid, _to_int(count, "count"))

    @channel_tool(description="收藏 / 取消收藏 AcFun 内容")
    async def favorite_content(self, rtype: str, rid: str, on_off: str = "true") -> str:
        """内容收藏。rtype: 1 番剧/2 视频/3 文章/4 合辑。"""
        return await self._ac("favorite", _to_int(rtype, "rtype"), rid, _to_bool(on_off))

    @channel_tool(description="关注 AcFun 用户", sensitive=True)
    async def follow_user(self, uid: str, special: str = "false") -> str:
        """关注用户。special: true 设为特别关注。"""
        return await self._ac("follow", uid, _to_bool(special))

    @channel_tool(description="取消关注 AcFun 用户", sensitive=True)
    async def unfollow_user(self, uid: str) -> str:
        """取消关注用户。"""
        return await self._ac("unfollow", uid)

    @channel_tool(description="AcFun 每日签到")
    async def signin(self) -> str:
        """每日签到（登录后通常已自动签到，重复调用幂等）。"""
        return await self._ac("signin")

    @channel_tool(description="修改 AcFun 个人签名", sensitive=True)
    async def update_signature(self, text: str) -> str:
        """修改个人签名。"""
        return await self._ac("update_signature", text)

    @channel_tool(description="查询 AcFun 账户 AC 币余额")
    async def acoin_balance(self) -> str:
        """查询 AC 币余额。"""
        return await self._ac("acoin")

    # ------------------------------------------------------------------
    # 通知 / 动态 / 历史 / 社交列表
    # ------------------------------------------------------------------

    @channel_tool(description="查询 AcFun 未读通知计数")
    async def unread_count(self) -> str:
        """未读计数（评论/@/点赞/礼物/公告/系统）。"""
        return await self._ac("unread")

    @channel_tool(description="读取 AcFun 通知中心消息列表")
    async def list_notifications(self, kind: str = "reply", page: str = "1") -> str:
        """读取通知。kind: reply 评论回复/at 提及/like 点赞/gift 礼物/notice 站内公告/system 系统通知。"""
        return await self._ac("get_notifications", kind, _to_int(page, "page"))

    @channel_tool(description="读取 AcFun 关注动态流")
    async def moment_feed(self, limit: str = "10", tab: str = "all", refresh: str = "false") -> str:
        """关注动态流。tab: all 综合/video 视频/article 文章/moment 动态；refresh: true 刷新到最新。"""
        return await self._ac("moment_feed", _to_int(limit, "limit"), tab, _to_bool(refresh))

    @channel_tool(description="查询 AcFun 观看历史")
    async def history(self, page: str = "1", limit: str = "10") -> str:
        """观看历史记录。"""
        return await self._ac("history", _to_int(page, "page"), _to_int(limit, "limit"))

    @channel_tool(description="查询 AcFun 关注分组列表")
    async def follow_groups(self) -> str:
        """关注分组列表。"""
        return await self._ac("follow_groups")

    @channel_tool(description="查询我的 AcFun 粉丝列表")
    async def my_fans(self, page: str = "1") -> str:
        """我的粉丝列表。"""
        return await self._ac("my_fans", _to_int(page, "page"))

    @channel_tool(description="查询 AcFun 收藏夹列表")
    async def favourite_folders(self) -> str:
        """收藏夹（视频夹）列表。"""
        return await self._ac("favourite_folders")

    @channel_tool(description="查询 AcFun 收藏夹内容")
    async def favourite_list(self, kind: str = "video", folder_id: str = "", page: str = "1") -> str:
        """收藏夹内容。kind: video/article/bangumi/album；video 可指定 folder_id（缺省默认夹）。"""
        return await self._ac("favourite_list", kind, folder_id or None, _to_int(page, "page"))

    # ------------------------------------------------------------------
    # 视频弹幕
    # ------------------------------------------------------------------

    @channel_tool(description="读取 AcFun 视频弹幕列表")
    async def video_danmaku_list(self, ac_id: str, part: str = "0", page: str = "1") -> str:
        """读取视频弹幕。part 为分 P 索引（0 起）；每页 200 条。"""
        return await self._ac("video_danmaku_list", ac_id, _to_int(part, "part"), _to_int(page, "page"))

    @channel_tool(description="发送 AcFun 视频弹幕", sensitive=True)
    async def video_danmaku_send(self, ac_id: str, content: str, position_ms: str,
                                 part: str = "0", color: str = "16777215",
                                 mode: str = "1", size: str = "25") -> str:
        """发送视频弹幕。position_ms 为出现时间点（毫秒）；mode: 1 滚动/4 底部/5 顶部；size: 25 中/16 小；color 为十进制 RGB。"""
        return await self._ac(
            "video_danmaku_send", ac_id, content, _to_int(position_ms, "position_ms"),
            _to_int(part, "part"), _to_int(color, "color"), _to_int(mode, "mode"), _to_int(size, "size"),
        )

    # ------------------------------------------------------------------
    # 直播
    # ------------------------------------------------------------------

    @channel_tool(description="获取 AcFun 直播分区目录（正在开播的直播间）")
    async def live_list(self) -> str:
        """正在开播的直播间列表。"""
        return await self._ac("live_list")

    @channel_tool(description="获取 AcFun 直播间信息")
    async def live_info(self, uid: str) -> str:
        """直播间信息（标题/开播状态/开播时间）。uid 为主播 ID。"""
        return await self._ac("live_info", uid)

    @channel_tool(description="向 AcFun 直播间发送弹幕（受同房间冷却限制）", sensitive=True)
    async def send_live_danmaku(self, uid: str, content: str) -> str:
        """发送直播弹幕。uid 为主播 ID（需开播中）。"""
        return await send_live_danmaku_gated(
            self.client, self.live_danmaku_cooldown_seconds(), self.live_danmaku_last_sent, uid, [content],
        )

    @channel_tool(description="给 AcFun 直播间点赞")
    async def live_like(self, uid: str, times: str = "1") -> str:
        """直播点赞（1-10 次）。"""
        return await self._ac("live_like", uid, _to_int(times, "times"))

    @channel_tool(description="查询 AcFun 直播间可送礼物列表")
    async def live_gift_list(self, uid: str) -> str:
        """礼物列表（含价格与可用数量档）。"""
        return await self._ac("live_gift_list", uid)

    @channel_tool(description="给 AcFun 直播间送礼物（消耗 AC 币/香蕉）", sensitive=True)
    async def live_send_gift(self, uid: str, gift_id: str, size: str = "1", times: str = "1") -> str:
        """送礼物。gift_id 见 live_gift_list；size 须为礼物允许的数量档。"""
        return await self._ac("live_send_gift", uid, _to_int(gift_id, "gift_id"),
                              _to_int(size, "size"), _to_int(times, "times"))

    @channel_tool(description="查询 AcFun 直播钱包余额")
    async def live_balance(self, uid: str) -> str:
        """直播钱包余额（acb/香蕉）。uid 为任一直播间主播 ID。"""
        return await self._ac("live_balance", uid)

    # ------------------------------------------------------------------
    # 创作中心 / 粉丝团 / 合辑
    # ------------------------------------------------------------------

    @channel_tool(description="查询我的 AcFun 稿件（创作中心）")
    async def my_content(self, kind: str = "video", page: str = "1", status: str = "all",
                         sortby: str = "recently", keyword: str = "") -> str:
        """我的稿件。kind: video/article；status: all/passed/posting/returned；sortby: recently/banana/viwed。"""
        return await self._ac("my_content", kind, _to_int(page, "page"), status, sortby, keyword)

    @channel_tool(description="查询 AcFun 创作中心数据总览")
    async def data_center(self, days: str = "1") -> str:
        """创作数据总览（近 N 天）。"""
        return await self._ac("data_center", _to_int(days, "days"))

    @channel_tool(description="查询 AcFun 创作中心分项数据")
    async def data_center_detail(self, rtype: str = "video", days: str = "1", sortby: str = "viewCount") -> str:
        """分项数据。rtype: video/article/live；sortby: viewCount/commentCount/stowCount/shareCount/bananaCount。"""
        return await self._ac("data_center_detail", rtype, _to_int(days, "days"), sortby)

    @channel_tool(description="查询我的 AcFun 粉丝团勋章")
    async def medal_list(self) -> str:
        """粉丝团勋章列表。"""
        return await self._ac("medal_list")

    @channel_tool(description="佩戴 / 卸下 AcFun 粉丝团勋章")
    async def medal_wear(self, uid: str, on_off: str = "true") -> str:
        """佩戴/卸下某 UP 主的粉丝勋章。uid 为 UP 主 ID。"""
        return await self._ac("medal_wear", uid, _to_bool(on_off))

    @channel_tool(description="查询我的 AcFun 合辑列表")
    async def album_list(self, page: str = "1") -> str:
        """我的合辑列表。"""
        return await self._ac("album_list", _to_int(page, "page"))

    @channel_tool(description="查询 AcFun 合辑内容")
    async def album_contents(self, album_id: str, page: str = "1") -> str:
        """合辑内容列表。"""
        return await self._ac("album_contents", album_id, _to_int(page, "page"))

    @channel_tool(description="创建 AcFun 合辑", sensitive=True)
    async def album_add(self, title: str, rtype: str = "2", cover: str = "",
                        intro: str = "", status: str = "1") -> str:
        """创建合辑。rtype: 2 视频/3 文章；status: 1 公开/2 私密；cover 为封面图 URL。"""
        return await self._ac("album_add", title, _to_int(rtype, "rtype"), cover, intro, _to_int(status, "status"))

    @channel_tool(description="删除 AcFun 合辑", sensitive=True)
    async def album_remove(self, album_id: str) -> str:
        """删除合辑。"""
        return await self._ac("album_remove", album_id)

    @channel_tool(description="更新 AcFun 合辑信息", sensitive=True)
    async def album_update(self, album_id: str, title: str, rtype: str = "2",
                           cover: str = "", intro: str = "", status: str = "1") -> str:
        """更新合辑。rtype: 2 视频/3 文章；status: 1 公开/2 私密。"""
        return await self._ac("album_update", album_id, title, _to_int(rtype, "rtype"),
                              cover, intro, _to_int(status, "status"))

    @channel_tool(description="向 AcFun 合辑批量添加内容", sensitive=True)
    async def album_contents_add(self, album_id: str, rids: str, rtype: str = "2") -> str:
        """添加内容到合辑。rids 逗号分隔；rtype: 2 视频/3 文章。"""
        return await self._ac("album_contents_add", album_id, _to_int(rtype, "rtype"), rids)

    @channel_tool(description="从 AcFun 合辑批量移除内容", sensitive=True)
    async def album_contents_del(self, album_id: str, rids: str) -> str:
        """从合辑移除内容。rids 逗号分隔。"""
        return await self._ac("album_contents_del", album_id, rids)

    # ------------------------------------------------------------------
    # 举报
    # ------------------------------------------------------------------

    @channel_tool(description="举报 AcFun 违规内容（需确凿证据，谨慎使用）", sensitive=True)
    async def report_content(self, rtype: str, rid: str, crime: str, proof: str, description: str) -> str:
        """举报内容。rtype: 1 番剧/2 视频/3 文章/10 动态；crime 举报类目；proof 证据；description 描述。"""
        return await self._ac("report_content", _to_int(rtype, "rtype"), rid, crime, proof, description)

    # ------------------------------------------------------------------
    # 频道配置自管理
    # ------------------------------------------------------------------

    # AI 可编辑的配置白名单（账号/密码/凭据/enabled/直播观察列表不可经此修改：
    # 直播开关与观察列表走 acfun_live_mode / acfun_live_watch 专用工具）
    _CONFIG_EDITABLE: Dict[str, type] = {
        "poll_interval_seconds": int,
        "notify_like": bool,
        "notify_gift": bool,
        "notify_system": bool,
        "gift_trigger_mind": bool,
        "like_trigger_mind": bool,
        "live_danmaku_cooldown_seconds": int,
        "whitelist_enabled": bool,
        "user_whitelist": str,
        "message_max_length": int,
        "live_recent_window": int,
        "live_mention_names": str,
        "live_mention_trigger": bool,
        "live_gift_trigger_mind": bool,
        "live_record_chatter": bool,
        "live_max_rooms": int,
        "live_closed_retry_seconds": int,
    }

    @channel_tool(description="查看 AcFun 频道当前配置（可调项与当前值）")
    async def config_show(self) -> str:
        """查看频道可调配置项及当前值（不含账号/凭据）。"""
        values = {key: getattr(self.config, key, None) for key in self._CONFIG_EDITABLE}
        values["live_mode"] = bool(self.config.live_mode)
        values["live_watch_rooms"] = str(self.config.live_watch_rooms or "")
        return _ok({"config": values, "logined": self.client.is_logined})

    @channel_tool(description="修改 AcFun 频道配置（轮询/通知开关/直播参数等，立即生效并持久化）", sensitive=True)
    async def config_set(self, key: str, value: str) -> str:
        """修改频道配置。key 须为 acfun_config_show 返回的可调项；value 按目标类型自动转换。"""
        if key not in self._CONFIG_EDITABLE:
            return _err(
                f"配置项不可编辑: {key!r}（可调项见 acfun_config_show；"
                f"直播开关/观察列表请用 acfun_live_mode / acfun_live_watch）"
            )
        target_type = self._CONFIG_EDITABLE[key]
        try:
            coerced: Any = (
                _to_bool(value) if target_type is bool
                else _to_int(value, key) if target_type is int
                else str(value)
            )
        except ValueError as exc:
            return _err(str(exc))
        from agent.channel.config import set_channel_config
        set_channel_config("acfun", **{key: coerced})  # 变更监听自动热更频道内存态
        return _ok({"key": key, "value": coerced, "result": "已生效并持久化"})

    # ------------------------------------------------------------------
    # 直播模式运维（实时弹幕 / 上下文注入 / 诊断）
    # ------------------------------------------------------------------

    @channel_tool(description="开启/关闭 AcFun 直播模式（连接观察中的直播间实时接收弹幕，关闭则断开并停止上下文注入）")
    async def live_mode(self, enabled: str) -> str:
        """开关直播模式。enabled: true 开启 / false 关闭。观察列表经 acfun_live_watch 管理。"""
        want = _to_bool(enabled)
        result = await self.live_manager.set_mode(want)
        self._persist_live_config(live_mode=want)
        return _ok({"result": result, "live_mode": want})

    @channel_tool(description="观察一个 AcFun 直播间（直播模式下建立连接实时接收弹幕）")
    async def live_watch(self, uid: str) -> str:
        """添加观察房间。uid 为主播 ID；受 live_max_rooms 上限约束。"""
        result = await self.live_manager.watch(uid)
        self._persist_live_config(rooms=self.live_manager.watched)
        return _ok({"result": result, "watched": self.live_manager.watched})

    @channel_tool(description="取消观察 AcFun 直播间（断开该房间连接）")
    async def live_unwatch(self, uid: str) -> str:
        """移除观察房间。uid 为主播 ID。"""
        result = await self.live_manager.unwatch(uid)
        self._persist_live_config(rooms=self.live_manager.watched)
        return _ok({"result": result, "watched": self.live_manager.watched})

    @channel_tool(description="AcFun 直播连接完整诊断（状态机/重连/心跳/计数/最后错误）")
    async def live_status(self) -> str:
        """逐房间诊断：状态、uptime、观众数、各类消息计数、重连/换票次数、最后信号年龄与错误。"""
        snap = self.live_manager.snapshot()
        return _ok(snap)

    def _persist_live_config(self, *, live_mode: Optional[bool] = None,
                             rooms: Optional[list] = None) -> None:
        """直播模式/观察列表变更持久化（委托频道统一入口）。"""
        try:
            self.persist_live_config(live_mode=live_mode, rooms=rooms)
        except Exception:
            pass  # 持久化失败不影响运行时已生效的变更
