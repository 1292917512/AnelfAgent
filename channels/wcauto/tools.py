"""wcauto 频道 AI 工具面 — @channel_tool 注册为 wcauto_*（组 wcauto）。

三档纪律（对齐 github 频道）：
- **读工具**（AUTO_ALLOW）：会话/消息/联系人/群成员/标签/朋友圈/撤回记录/桥自检；
- **写工具**（sensitive + ``allow_write`` 配置门控，默认关）：@群成员/撤回/
  转发/朋友圈点赞评论/标签群发——全部经 ApprovalGate，且桥侧 rhythm
  拟人节流兜底（微信风控敏感，高频自动写是封号主因）。
- 普通发消息/图片/文件走通用能力工具（send_message 等），不在此列。
"""

from __future__ import annotations

from typing import Any

from agent.channel.channel_types import _err, _ok
from agent.channel.tool_bridge import channel_tool

from .bridge.wcauto_bridge import protocol
from .transport import WcautoRpcError

_GROUP = "wcauto"


class WcautoToolsMixin:
    """wcauto 频道工具集（经 @channel_tool 注册为 wcauto_<method>）。"""

    # 以下属性由 WcautoChannel 提供（声明仅为类型提示）
    config: Any
    _transport: Any

    async def _call(self, method: str, params: dict, action: str) -> Any:
        """桥 RPC 统一出口：异常转 None，由调用方组装错误。"""
        try:
            return await self._transport.call_rpc(method, params)
        except WcautoRpcError as exc:
            return _err(f"{action}失败: {exc}")
        except Exception as exc:
            return _err(f"{action}异常: {exc}")

    def _write_gate(self) -> str:
        """写操作配置门控：返回空串放行，否则返回错误 JSON。"""
        if not bool(getattr(self.config, "allow_write", False)):
            return _err("wcauto 敏感写操作未启用（频道配置 allow_write=false）；"
                        "微信风控敏感，请主人确认后在频道配置中开启")
        return ""

    # ==================================================================
    # 能力方法（通用能力工具：get_chat_info / get_chat_members）
    # ==================================================================

    @channel_tool(description="获取会话信息（微信个人号）")
    async def get_chat_info(self, chat_id: str, **kwargs: Any) -> str:
        result = await self._call(protocol.RPC_SEARCH_CONTACT,
                                  {"keyword": chat_id, "limit": 5}, "查询会话")
        if isinstance(result, str):
            return result
        hits = [h for h in (result or [])
                if h.get("username") == chat_id]
        return _ok({"chat_id": chat_id, "contact": hits[0] if hits else None,
                    "hits": result})

    @channel_tool(description="获取群成员列表（微信群）")
    async def get_chat_members(self, chat_id: str, **kwargs: Any) -> str:
        if not str(chat_id).endswith("@chatroom"):
            return _err(f"非群聊会话: {chat_id}")
        result = await self._call(protocol.RPC_GET_GROUP_MEMBERS,
                                  {"group": chat_id}, "获取群成员")
        if isinstance(result, str):
            return result
        members = result or []
        return _ok({"chat_id": chat_id, "count": len(members),
                    "members": members[:200],
                    "truncated": len(members) > 200})

    # ==================================================================
    # 读组（AUTO_ALLOW）
    # ==================================================================

    @channel_tool(group=_GROUP, description="列出微信近期会话（昵称/未读数/最后消息摘要）")
    async def get_sessions(self, limit: int = 30) -> str:
        result = await self._call(protocol.RPC_GET_SESSIONS,
                                  {"limit": int(limit)}, "列出会话")
        if isinstance(result, str):
            return result
        sessions = [{
            "username": s.get("username"), "chat_name": s.get("chat_name"),
            "is_group": s.get("is_group"), "unread": s.get("unread"),
            "summary": str(s.get("summary") or "")[:100],
            "last_time": s.get("last_time"),
        } for s in (result or [])]
        return _ok({"count": len(sessions), "sessions": sessions})

    @channel_tool(group=_GROUP, description="读取指定会话的近期消息（群/私聊均可）")
    async def get_messages(self, chat_id: str, limit: int = 20) -> str:
        result = await self._call(protocol.RPC_GET_MESSAGES,
                                  {"chat": chat_id, "limit": int(limit)}, "读取消息")
        if isinstance(result, str):
            return result
        msgs = [{
            "local_id": m.get("local_id"), "type": m.get("type"),
            "sender": m.get("sender_username"), "content": str(m.get("content") or "")[:500],
            "create_time": m.get("create_time"), "shard": m.get("shard"),
        } for m in (result or [])]
        return _ok({"chat_id": chat_id, "count": len(msgs), "messages": msgs})

    @channel_tool(group=_GROUP, description="按昵称/备注/微信号搜索联系人或群")
    async def search_contact(self, keyword: str, limit: int = 10) -> str:
        if not keyword.strip():
            return _err("keyword 不能为空")
        result = await self._call(protocol.RPC_SEARCH_CONTACT,
                                  {"keyword": keyword, "limit": int(limit)}, "搜索联系人")
        if isinstance(result, str):
            return result
        return _ok({"count": len(result or []), "contacts": result or []})

    @channel_tool(group=_GROUP, description="下载指定消息的媒体附件到本地（图片/语音/视频/文件）")
    async def download_media(self, chat_id: str, local_id: int) -> str:
        try:
            saved = await self._download_media(chat_id, int(local_id))
        except Exception as exc:
            return _err(f"下载媒体失败: {exc}")
        if not saved:
            return _err("媒体不可用（未落盘或超过大小上限）")
        return _ok({"path": saved, "chat_id": chat_id, "local_id": int(local_id)})

    @channel_tool(group=_GROUP, description="列出微信通讯录标签")
    async def list_labels(self) -> str:
        result = await self._call(protocol.RPC_LIST_LABELS, {}, "列出标签")
        if isinstance(result, str):
            return result
        return _ok({"labels": result})

    @channel_tool(group=_GROUP, description="列出指定通讯录标签的成员")
    async def label_members(self, label: str) -> str:
        result = await self._call(protocol.RPC_LABEL_MEMBERS,
                                  {"label": label}, "查询标签成员")
        if isinstance(result, str):
            return result
        return _ok(result if isinstance(result, dict) else {"members": result})

    @channel_tool(group=_GROUP, description="读取朋友圈时间线（可按发布者/关键词/时间过滤）")
    async def get_moments(self, limit: int = 20, username: str = "",
                          keyword: str = "", since_hours: int = 0) -> str:
        params: dict = {"limit": int(limit)}
        if username.strip():
            params["username"] = username.strip()
        if keyword.strip():
            params["keyword"] = keyword.strip()
        if since_hours > 0:
            import time
            params["since"] = int(time.time()) - since_hours * 3600
        result = await self._call(protocol.RPC_GET_MOMENTS, params, "读取朋友圈")
        if isinstance(result, str):
            return result
        moments = [{
            "publisher": m.get("publisher") or m.get("username"),
            "content": str(m.get("content") or m.get("contentDesc") or "")[:300],
            "create_time": m.get("create_time"),
            "likes": len(m.get("likes") or []),
            "comments": len(m.get("comments") or []),
        } for m in (result or [])]
        return _ok({"count": len(moments), "moments": moments})

    @channel_tool(group=_GROUP, description="查询撤回记录（防撤回镜像库，含原文）")
    async def get_recalled(self, chat_id: str = "", limit: int = 20) -> str:
        params: dict = {"limit": int(limit)}
        if chat_id.strip():
            params["chat"] = chat_id.strip()
        result = await self._call(protocol.RPC_GET_RECALLED, params, "查询撤回记录")
        if isinstance(result, str):
            return result
        return _ok({"count": len(result or []), "recalled": result or []})

    @channel_tool(group=_GROUP, description="桥自检（账号/数据库密钥/图片密钥/窗口状态）")
    async def bridge_doctor(self) -> str:
        result = await self._call(protocol.RPC_DOCTOR, {}, "桥自检")
        if isinstance(result, str):
            return result
        return _ok({"doctor": result})

    # ==================================================================
    # 写组（sensitive + allow_write 配置门控）
    # ==================================================================

    @channel_tool(group=_GROUP, sensitive=True,
                  description="在群聊中 @ 指定成员并发送消息（写操作，需审批）")
    async def send_at(self, chat_id: str, member: str, text: str) -> str:
        if gate := self._write_gate():
            return gate
        if not str(chat_id).endswith("@chatroom"):
            return _err("send_at 仅适用于群聊")
        if not member.strip() or not text.strip():
            return _err("member 与 text 均不能为空")
        result = await self._call(protocol.RPC_SEND_AT, {
            "chat": chat_id, "member": member.strip(), "text": text,
            "verify": bool(getattr(self.config, "send_verify", True)),
        }, "发送@消息")
        if isinstance(result, str):
            return result
        return _ok({"chat_id": chat_id, "member": member, "bridge": result})

    @channel_tool(group=_GROUP, sensitive=True,
                  description="撤回自己在指定会话最近发送的一条消息（写操作，需审批）")
    async def recall_last(self, chat_id: str) -> str:
        if gate := self._write_gate():
            return gate
        result = await self._call(protocol.RPC_RECALL_LAST,
                                  {"chat": chat_id}, "撤回消息")
        if isinstance(result, str):
            return result
        return _ok({"chat_id": chat_id, "bridge": result})

    @channel_tool(group=_GROUP, sensitive=True,
                  description="把指定会话中匹配的消息转发给多个联系人/群（写操作，需审批）")
    async def forward_msg(self, chat_id: str, targets: str,
                          match: str = "") -> str:
        if gate := self._write_gate():
            return gate
        names = [t.strip() for t in targets.split(",") if t.strip()]
        if not names:
            return _err("targets 不能为空（逗号分隔多个接收方）")
        if len(names) > 9:
            return _err("单次转发最多 9 个接收方（微信窗口分批上限）")
        result = await self._call(protocol.RPC_FORWARD, {
            "chat": chat_id, "targets": names, "match": match,
        }, "转发消息")
        if isinstance(result, str):
            return result
        return _ok({"chat_id": chat_id, "bridge": result})

    @channel_tool(group=_GROUP, sensitive=True,
                  description="给指定朋友圈点赞（按发布者昵称或正文关键词定位；写操作，需审批）")
    async def moment_like(self, publisher: str = "", keyword: str = "") -> str:
        if gate := self._write_gate():
            return gate
        if not publisher.strip() and not keyword.strip():
            return _err("publisher 与 keyword 至少给一个（定位目标朋友圈）")
        result = await self._call(protocol.RPC_MOMENT_LIKE, {
            "publisher": publisher.strip(), "keyword": keyword.strip(),
        }, "朋友圈点赞")
        if isinstance(result, str):
            return result
        return _ok({"bridge": result})

    @channel_tool(group=_GROUP, sensitive=True,
                  description="评论指定朋友圈（按发布者昵称或正文关键词定位；写操作，需审批）")
    async def moment_comment(self, content: str, publisher: str = "",
                             keyword: str = "") -> str:
        if gate := self._write_gate():
            return gate
        if not content.strip():
            return _err("评论内容不能为空")
        result = await self._call(protocol.RPC_MOMENT_COMMENT, {
            "publisher": publisher.strip(), "keyword": keyword.strip(),
            "content": content.strip(),
        }, "朋友圈评论")
        if isinstance(result, str):
            return result
        return _ok({"bridge": result})

    @channel_tool(group=_GROUP, sensitive=True,
                  description="按通讯录标签群发文本（先 dry_run 解析收件人，确认后再发；写操作，需审批）")
    async def label_send(self, label: str, text: str, dry_run: bool = True) -> str:
        if gate := self._write_gate():
            return gate
        if not label.strip() or not text.strip():
            return _err("label 与 text 均不能为空")
        result = await self._call(protocol.RPC_LABEL_SEND, {
            "label": label.strip(), "text": text, "dry_run": bool(dry_run),
        }, "标签群发")
        if isinstance(result, str):
            return result
        return _ok({"bridge": result,
                    "hint": "dry_run=true 未实际发送；确认收件人无误后以 dry_run=false 重调"})
