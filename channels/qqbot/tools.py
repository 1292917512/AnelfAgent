"""QQ 开放平台专属工具，经频道工具桥注册；管理写操作沿用敏感操作门控。

模型按需发现频道工具，通过工具结果取得消息回执、会话目标或权限错误。
仅工具启用时增加相应 schema 和结果的动态 token。
不修改稳定前缀。
"""

from abc import abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import quote

from agent.channel import channel_tool
from agent.channel.channel_types import _ok
from core.log import log
from core.tool_errors import error_from_exception

from .client import QQAPI
from .events import Target, identifier
from .media import upload_markdown_image


class QQBotTools:
    @property
    @abstractmethod
    def api(self) -> QQAPI:
        """当前运行实例的 OpenAPI 客户端。"""

    @channel_tool(description="将本地图片上传为 QQ Markdown 可复用的图片素材")
    async def upload_markdown_material(self, image_path: str) -> str:
        """返回当前机器人专属的素材引用 URL、图片 ID 与有效期。"""
        try:
            return _ok(await upload_markdown_image(self.api, image_path))
        except Exception as exc:
            return error_from_exception(exc, action="上传 QQ Markdown 图片素材")

    @channel_tool(description="分页查询当前机器人的 QQ Markdown 图片素材")
    async def list_markdown_materials(self, cursor: str = "") -> str:
        try:
            result = await self.api.request("GET", "/v2/media-images", params={"cursor": cursor} if cursor else {})
            images = [{k: img[k] for k in ("image_id", "url", "file_name", "expires_at") if k in img}
                      for img in result.get("images", [])]
            return _ok({"images": images, "next_cursor": result.get("next_cursor", "")})
        except Exception as exc:
            return error_from_exception(exc, action="查询 QQ Markdown 素材", retry_safe=True)

    @channel_tool(description="删除当前机器人的指定 QQ Markdown 素材", sensitive=True)
    async def delete_markdown_material(self, image_id: str) -> str:
        try:
            await self.api.request("DELETE", f"/v2/media-images/{identifier(image_id)}/delete")
            return _ok({"image_id": image_id})
        except Exception as exc:
            return error_from_exception(exc, action="删除 QQ Markdown 素材")

    @channel_tool(description="分页列出机器人加入的 QQ 频道，返回下一页游标")
    async def list_guilds(self, after: str = "", limit: int = 50) -> str:
        """after 使用前一页最后一个频道 ID，limit 范围 1–100。"""
        try:
            if not 1 <= limit <= 100:
                raise ValueError("limit 应为 1–100")
            params = {"limit": str(limit)}
            if after:
                params["after"] = identifier(after)
            guilds = await self.api.get_list("/users/@me/guilds", params=params)
            return _ok({"guilds": guilds, "next_after": guilds[-1]["id"] if len(guilds) == limit else ""})
        except Exception as exc:
            return error_from_exception(exc, action="查询 QQ 频道列表", retry_safe=True)

    @channel_tool(description="列出 QQ 频道下可见的子频道和发送目标")
    async def list_subchannels(self, guild_id: str) -> str:
        """列出指定频道内的子频道，文字子频道可作为聊天目标。"""
        try:
            channels = await self.api.get_list(f"/guilds/{identifier(guild_id)}/channels")
            return _ok({"channels": [{"id": c["id"], "name": c.get("name"), "type": c.get("type"),
                                      "chat_id": Target("channel", c["id"]).address if c.get("type") == 0 else None}
                                     for c in channels]})
        except Exception as exc:
            return error_from_exception(exc, action="查询 QQ 子频道", retry_safe=True)

    @channel_tool(description="查看 QQ 群入群申请及下一页游标")
    async def list_join_requests(self, chat_id: str, cursor: str = "", limit: int = 20) -> str:
        """需平台授予 QQ 群管理权限。"""
        try:
            target = Target.parse(chat_id)
            if target.kind != "group" or not 1 <= limit <= 50:
                raise ValueError("需要 group.ID 和 1–50 的 limit")
            params = {"limit": str(limit)}
            if cursor:
                params["cursor"] = cursor
            return _ok(await self.api.request("GET", target.base + "/join_request_list", params=params))
        except Exception as exc:
            return error_from_exception(exc, action="查询入群申请", retry_safe=True)

    @channel_tool(description="查询机器人在指定 QQ 群的角色、主动发送权限及接收消息模式")
    async def get_group_bot_state(self, chat_id: str) -> str:
        try:
            target = Target.parse(chat_id)
            if target.kind != "group":
                raise ValueError("需要 group.ID 目标")
            return _ok(await self.api.request("GET", target.base + "/bot_state"))
        except Exception as exc:
            return error_from_exception(exc, action="查询 QQ 群机器人状态", retry_safe=True)

    @channel_tool(description="按申请 ID 同意或拒绝 QQ 入群请求", sensitive=True)
    async def review_join_request(self, chat_id: str, member_openid: str, join_request_id: str,
                                  operation: Literal["approve", "decline"], reject_reason: str = "") -> str:
        """处理对应群、成员与申请 ID 的单条申请。"""
        try:
            target = Target.parse(chat_id)
            if target.kind != "group" or not join_request_id or operation not in ("approve", "decline"):
                raise ValueError("需要 group.ID、申请 ID 和有效的审批操作")
            body = {"op": operation, "join_request_id": join_request_id}
            if operation == "decline" and reject_reason:
                body["reject_reason"] = reject_reason
            await self.api.request("POST", f"{target.base}/approval_join_request/{identifier(member_openid)}", json=body)
            return _ok({"join_request_id": join_request_id, "operation": operation})
        except Exception as exc:
            return error_from_exception(exc, action="处理 QQ 入群申请")

    async def _pin(self, chat_id: str, message_id: str, on: bool) -> str:
        try:
            target = Target.parse(chat_id)
            if target.kind != "channel" or not message_id or message_id in ("all", ".", ".."):
                raise ValueError("需要子频道和单条消息 ID")
            await self.api.request("PUT" if on else "DELETE", f"{target.base}/pins/{quote(message_id, safe='')}")
            return _ok({"message_id": message_id, "pinned": on})
        except Exception as exc:
            return error_from_exception(exc, action="设置 QQ 子频道精华消息")

    @channel_tool(description="在 QQ 子频道将单条消息设为精华", sensitive=True)
    async def pin_message(self, chat_id: str, message_id: str) -> str:
        """设置单条精华消息，需要管理子频道的权限。"""
        return await self._pin(chat_id, message_id, True)

    @channel_tool(description="取消指定 QQ 子频道消息的精华标记", sensitive=True)
    async def unpin_message(self, chat_id: str, message_id: str) -> str:
        """取消指定消息的精华标记。"""
        return await self._pin(chat_id, message_id, False)

    @channel_tool(description="打开同一 QQ 频道内成员的私信会话并返回可发送目标")
    async def open_direct_message(self, guild_id: str, user_id: str) -> str:
        """创建或获取平台私信目标。

        Args:
            guild_id: 机器人与成员的共同频道 ID。
            user_id: 该频道中的成员 ID。
        """
        try:
            data = await self.api.request("POST", "/users/@me/dms", json={
                "recipient_id": identifier(user_id), "source_guild_id": identifier(guild_id),
            })
            target = Target("dm", identifier(data.get("guild_id")), user_id)
            return _ok({"chat_id": target.address})
        except Exception as exc:
            return error_from_exception(exc, action="创建 QQ 频道私信")

    @channel_tool(description="设置或取消 QQ 群成员禁言，需要群管理权限", sensitive=True)
    async def set_member_mute(self, chat_id: str, member_openid: str, operation: Literal["add", "update", "del"],
                              seconds: int = 600) -> str:
        """设置指定成员的禁言。

        Args:
            chat_id: group.ID 格式的目标。
            member_openid: 该群中的成员 OpenID。
            operation: add 新增，update 调整时长，del 解除。
            seconds: 新增或调整时的禁言秒数。
        """
        try:
            target = Target.parse(chat_id)
            if target.kind != "group" or operation not in ("add", "update", "del"):
                raise ValueError("需要 group.ID 目标与有效的禁言操作")
            member = {"op": operation, "member_openid": identifier(member_openid)}
            if operation != "del":
                if seconds <= 0:
                    raise ValueError("禁言时长必须为正数")
                member["mute_expire_at"] = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()
            await self.api.request("POST", target.base + "/restrict_chat_setting", json={"members": [member]})
            return _ok({"chat_id": chat_id, "member_openid": member_openid, "operation": operation})
        except Exception as exc:
            return error_from_exception(exc, action="设置 QQ 群成员禁言")

    @channel_tool(description="在 QQ 子频道消息上添加或移除机器人自己的表情回应")
    async def message_reaction(self, chat_id: str, message_id: str, emoji_id: str,
                               emoji_type: int = 1, on: bool = True) -> str:
        """发表或撤销指定表情。

        Args:
            chat_id: channel.ID 格式的子频道目标。
            message_id: 原消息 ID。
            emoji_id: QQ 平台表情 ID。
            emoji_type: 平台表情类别，取值 1 或 2。
            on: true 为添加，false 为移除。
        """
        try:
            await self._react(chat_id, message_id, emoji_type, emoji_id, on)
            return _ok({"message_id": message_id, "emoji_id": emoji_id, "on": on})
        except Exception as exc:
            return error_from_exception(exc, action="设置 QQ 表情回应")

    async def _react(self, chat_id: str, message_id: str, emoji_type: int, emoji_id: str, on: bool) -> None:
        target = Target.parse(chat_id)
        if target.kind != "channel" or emoji_type not in (1, 2) or not emoji_id.isascii() or not emoji_id.isdigit():
            raise ValueError("表情回应需要 channel.ID 目标、有效表情类别和数字表情 ID")
        if not message_id or message_id in (".", ".."):
            raise ValueError("需要 QQ 消息 ID")
        path = f"{target.base}/messages/{quote(message_id, safe='')}/reactions/{emoji_type}/{emoji_id}"
        await self.api.request("PUT" if on else "DELETE", path)

    async def set_message_reaction(self, chat_id: str, message_id: str, emoji: str = "👍", on: bool = True) -> bool:
        """平台无关的 Unicode 表情通过 QQ 的十进制码点发送。"""
        glyph = emoji.removesuffix("\ufe0f")
        if len(glyph) != 1:
            return False
        try:
            await self._react(chat_id, message_id, 2, str(ord(glyph)), on)
            return True
        except Exception as exc:
            log(f"QQ 表情回应失败: {exc}", "DEBUG", tag="通道")
            return False
