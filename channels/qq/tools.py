"""QQ 工具层 — 表驱动的 OneBot v11 API 包装。

约 40 个同构 API 工具（int 参数解析 → 调 API → _ok/_err）由 ``_TOOL_SPECS``
声明表 + ``_build_tool`` 工厂批量生成，并挂到 ``QQToolsMixin`` 上；
每个生成函数带真实签名（``__signature__``）与 docstring，保证 LLM 看到的
JSON schema（参数名/类型/描述）与手写版本完全一致。

无法表化的特殊工具（文件读写、分支路由、raw retcode 处理等）保留手写。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Tuple, Union

from agent.channel.channel_types import _err, _ok
from agent.channel.schemas import SegmentType
from agent.channel.tool_bridge import channel_tool
from core.log import log

from .send import _read_file_base64, _split_forward_sections


def _ok_raw(data: Any) -> str:
    """构造成功响应，非 dict 的 data 自动包装为 data 字段。"""
    return _ok(data if isinstance(data, dict) else {"data": data})


# OneBot 群文件 busid：102 表示群文件（OneBot 协议固定取值）
_DEFAULT_GROUP_FILE_BUSID = 102

_MISSING = object()  # 参数无默认值哨兵


@dataclass(frozen=True)
class _Param:
    """工具参数声明。"""

    name: str                                   # 对外参数名（LLM schema 同名）
    kind: type = str                            # schema 类型（str/int/bool）
    default: Any = _MISSING                     # 默认值（缺省为必填）
    desc: str = ""                              # LLM 看到的参数描述
    key: Optional[str] = ""                     # OneBot API 参数键；None 表示不发给 API；"" 表示同名
    to_int: bool = False                        # str 形式的数字 ID，调用前转 int
    transform: Optional[Callable[[Any], Any]] = None  # 调用前额外变换（如截断上限）


# data/list 字段映射项：(输出键, data 键, cast 函数或 None, 默认值)
_FieldMap = Tuple[str, str, Optional[Callable[[Any], Any]], Any]

_ErrText = Union[str, Callable[[Dict[str, Any]], str]]


@dataclass(frozen=True)
class _Tool:
    """OneBot API 工具声明。"""

    name: str                            # 方法名（注册工具名为 qq_{name}；能力方法按名匹配）
    action: str                          # OneBot action
    desc: str                            # 工具描述（docstring 首行）
    params: Tuple[_Param, ...] = ()
    sensitive: bool = False              # 敏感操作（受 channel_tools_allow_sensitive 门控）
    mode: str = "action"                 # action=布尔写操作 / data=取 data 字段映射 / raw=透传 data / list=列表包装
    err: _ErrText = ""                   # 失败提示（可为 lambda params -> str）
    err_invalid: str = ""                # int 解析失败提示模板（{参数名} 占位）
    ok_echo: Tuple[str, ...] = ()        # action 模式成功时回显的参数名
    extra: Tuple[Tuple[str, Any], ...] = ()          # 固定附加 API 参数
    data_fields: Tuple[_FieldMap, ...] = ()          # data 模式字段映射
    list_source: str = ""                # list 模式：data 为 dict 时列表所在键
    list_out: str = ""                   # list 模式：输出列表字段名
    item_fields: Tuple[_FieldMap, ...] = ()          # list 模式：逐项字段映射
    with_count: bool = False             # list 模式：附加 count=len(列表)


# ------------------------------------------------------------------
# API 工具声明表
# ------------------------------------------------------------------

_ID_PAIR_ERR = "无效的 ID: group={chat_id}, user={user_id}"
_GROUP_ID_ERR = "无效的群 ID: {chat_id}"
_USER_ID_ERR = "无效的用户 ID: {user_id}"

_TOOL_SPECS: Tuple[_Tool, ...] = (
    _Tool(
        name="delete_message", action="delete_msg", desc="撤回指定消息",
        params=(
            _Param("chat_id", key=None),
            _Param("message_id", to_int=True),
        ),
        err="撤回失败", err_invalid="无效的消息 ID: {message_id}",
    ),
    _Tool(
        name="ban_user", action="set_group_ban", desc="禁言群成员（默认 30 分钟）",
        sensitive=True,
        params=(
            _Param("chat_id", desc="群号", key="group_id", to_int=True),
            _Param("user_id", desc="用户 QQ 号", to_int=True),
            _Param("duration", kind=int, default=1800, desc="禁言时长（秒），默认 1800（30 分钟）"),
        ),
        err="禁言失败", err_invalid=_ID_PAIR_ERR,
    ),
    _Tool(
        name="unban_user", action="set_group_ban", desc="解除群成员禁言",
        params=(
            _Param("chat_id", key="group_id", to_int=True),
            _Param("user_id", to_int=True),
        ),
        extra=(("duration", 0),),
        err="解禁失败", err_invalid=_ID_PAIR_ERR,
    ),
    _Tool(
        name="set_chat_title", action="set_group_name", desc="设置群名称",
        params=(
            _Param("chat_id", key="group_id", to_int=True),
            _Param("title", key="group_name"),
        ),
        err="设置群名失败", err_invalid=_GROUP_ID_ERR,
    ),
    _Tool(
        name="set_group_card", action="set_group_card",
        desc="设置群成员名片（群昵称）。card 为空则取消名片。",
        params=(
            _Param("chat_id", key="group_id", to_int=True),
            _Param("user_id", to_int=True),
            _Param("card", default=""),
        ),
        ok_echo=("chat_id", "user_id", "card"),
        err="设置群名片失败", err_invalid=_ID_PAIR_ERR,
    ),
    _Tool(
        name="get_stranger_info", action="get_stranger_info",
        desc="获取陌生人信息（昵称、性别、年龄、QQ 等级）。",
        params=(_Param("user_id", to_int=True),),
        mode="data",
        data_fields=(
            ("user_id", "user_id", str, ""),
            ("nickname", "nickname", str, ""),
            ("sex", "sex", str, "unknown"),
            ("age", "age", None, 0),
            ("level", "level", None, 0),
        ),
        err="获取用户信息失败", err_invalid=_USER_ID_ERR,
    ),
    _Tool(
        name="get_group_member_info", action="get_group_member_info",
        desc="获取群成员详细信息（群名片、角色、入群时间）。",
        params=(
            _Param("chat_id", key="group_id", to_int=True),
            _Param("user_id", to_int=True),
        ),
        mode="data",
        data_fields=(
            ("group_id", "group_id", str, ""),
            ("user_id", "user_id", str, ""),
            ("nickname", "nickname", str, ""),
            ("card", "card", str, ""),
            ("sex", "sex", str, "unknown"),
            ("age", "age", None, 0),
            ("join_time", "join_time", None, 0),
            ("role", "role", str, "member"),
            ("title", "title", str, ""),
        ),
        err="获取群成员信息失败", err_invalid=_ID_PAIR_ERR,
    ),
    _Tool(
        name="set_group_admin", action="set_group_admin", desc="设置/取消群管理员。",
        sensitive=True,
        params=(
            _Param("chat_id", key="group_id", to_int=True),
            _Param("user_id", to_int=True),
            _Param("enable", kind=bool, default=True),
        ),
        ok_echo=("chat_id", "user_id", "enable"),
        err=lambda p: f"{'设置' if p['enable'] else '取消'}管理员失败",
        err_invalid=_ID_PAIR_ERR,
    ),
    _Tool(
        name="set_group_whole_ban", action="set_group_whole_ban", desc="全员禁言。",
        sensitive=True,
        params=(
            _Param("chat_id", key="group_id", to_int=True),
            _Param("enable", kind=bool, default=True),
        ),
        ok_echo=("chat_id", "enable"),
        err=lambda p: f"{'开启' if p['enable'] else '关闭'}全员禁言失败",
        err_invalid=_GROUP_ID_ERR,
    ),
    _Tool(
        name="get_friend_list", action="get_friend_list", desc="获取好友列表。",
        mode="list", list_out="friends", with_count=True,
        item_fields=(
            ("user_id", "user_id", str, ""),
            ("nickname", "nickname", str, ""),
            ("remark", "remark", str, ""),
        ),
        err="获取好友列表失败",
    ),
    _Tool(
        name="get_group_list", action="get_group_list", desc="获取群列表。",
        mode="list", list_out="groups", with_count=True,
        item_fields=(
            ("group_id", "group_id", str, ""),
            ("group_name", "group_name", str, ""),
            ("member_count", "member_count", None, 0),
            ("max_member_count", "max_member_count", None, 0),
        ),
        err="获取群列表失败",
    ),
    _Tool(
        name="get_login_info", action="get_login_info", desc="获取登录账号信息（Bot 自身）。",
        mode="data",
        data_fields=(
            ("user_id", "user_id", str, ""),
            ("nickname", "nickname", str, ""),
        ),
        err="获取登录信息失败",
    ),
    _Tool(
        name="get_message", action="get_msg", desc="获取单条消息详情。",
        params=(_Param("message_id", to_int=True),),
        mode="data",
        data_fields=(
            ("message_id", "message_id", str, ""),
            ("sender", "sender", None, {}),
            ("message", "message", None, ""),
            ("time", "time", None, 0),
        ),
        err="获取消息失败", err_invalid="无效的消息 ID: {message_id}",
    ),
    _Tool(
        name="get_forward_msg", action="get_forward_msg", desc="获取合并转发消息内容。",
        params=(_Param("forward_id", key="id"),),
        mode="list", list_source="messages", list_out="messages", with_count=True,
        err="获取合并转发消息失败",
    ),
    _Tool(
        name="set_group_add_request", action="set_group_add_request", desc="处理加群申请。",
        sensitive=True,
        params=(
            _Param("flag", desc="请求标识（从事件中获取）"),
            _Param("approve", kind=bool, default=True, desc="是否同意"),
            _Param("reason", default="", desc="拒绝理由（approve=False 时有效）"),
        ),
        ok_echo=("flag", "approve"),
        err=lambda p: f"{'同意' if p['approve'] else '拒绝'}加群申请失败",
    ),
    _Tool(
        name="set_friend_add_request", action="set_friend_add_request", desc="处理好友申请。",
        sensitive=True,
        params=(
            _Param("flag", desc="请求标识（从事件中获取）"),
            _Param("approve", kind=bool, default=True, desc="是否同意"),
            _Param("remark", default="", desc="好友备注（approve=True 时有效）"),
        ),
        ok_echo=("flag", "approve"),
        err=lambda p: f"{'同意' if p['approve'] else '拒绝'}好友申请失败",
    ),
    _Tool(
        name="get_group_msg_history", action="get_group_msg_history", desc="获取群消息历史记录。",
        params=(
            _Param("chat_id", desc="群号", key="group_id", to_int=True),
            _Param("count", kind=int, default=20, desc="获取消息数量（最大 200）",
                   transform=lambda v: min(v, 200)),
        ),
        mode="list", list_source="messages", list_out="messages", with_count=True,
        err="获取群消息历史失败", err_invalid=_GROUP_ID_ERR,
    ),
    _Tool(
        name="set_group_kick", action="set_group_kick", desc="踢出群成员。",
        sensitive=True,
        params=(
            _Param("chat_id", desc="群号", key="group_id", to_int=True),
            _Param("user_id", desc="用户 QQ 号", to_int=True),
            _Param("reject_add_request", kind=bool, default=False, desc="是否拒绝此人的加群申请"),
        ),
        ok_echo=("chat_id", "user_id"),
        err="踢出群成员失败", err_invalid=_ID_PAIR_ERR,
    ),
    _Tool(
        name="set_group_leave", action="set_group_leave", desc="退出群聊。",
        sensitive=True,
        params=(
            _Param("chat_id", desc="群号", key="group_id", to_int=True),
            _Param("is_dismiss", kind=bool, default=False, desc="是否解散群（仅群主可用）"),
        ),
        ok_echo=("chat_id",),
        err="退出群聊失败", err_invalid=_GROUP_ID_ERR,
    ),
    _Tool(
        name="get_friend_msg_history", action="get_friend_msg_history", desc="获取好友消息历史记录。",
        params=(
            _Param("user_id", desc="好友 QQ 号", to_int=True),
            _Param("count", kind=int, default=20, desc="获取消息数量（最大 200）",
                   transform=lambda v: min(v, 200)),
        ),
        mode="list", list_source="messages", list_out="messages", with_count=True,
        err="获取好友消息历史失败", err_invalid=_USER_ID_ERR,
    ),
    _Tool(
        name="get_group_system_msg", action="get_group_system_msg",
        desc="获取群系统消息（加群申请、被邀请入群等）。",
        mode="data",
        data_fields=(
            ("invited_requests", "invited_requests", None, []),
            ("join_requests", "join_requests", None, []),
        ),
        err="获取群系统消息失败",
    ),
    _Tool(
        name="get_image", action="get_image", desc="获取图片信息。",
        params=(_Param("file_id", desc="图片文件 ID（从消息中获取）", key="file"),),
        mode="data",
        data_fields=(
            ("file", "file", str, ""),
            ("filename", "filename", str, ""),
            ("url", "url", str, ""),
            ("size", "size", None, 0),
        ),
        err="获取图片信息失败",
    ),
    _Tool(
        name="get_record", action="get_record", desc="获取语音信息。",
        params=(
            _Param("file_id", desc="语音文件 ID（从消息中获取）", key="file"),
            _Param("out_format", default="mp3", desc="输出格式（mp3/amr/wma/m4a/spx/ogg/wav/flac）"),
        ),
        mode="data",
        data_fields=(
            ("file", "file", str, ""),
            ("url", "url", str, ""),
        ),
        err="获取语音信息失败",
    ),
    _Tool(
        name="get_group_file_url", action="get_group_file_url", desc="获取群文件下载链接。",
        params=(
            _Param("chat_id", desc="群号", key="group_id", to_int=True),
            _Param("file_id", desc="文件 ID"),
            _Param("busid", kind=int, default=_DEFAULT_GROUP_FILE_BUSID, desc="文件类型（默认 102）"),
        ),
        mode="data",
        data_fields=(("url", "url", str, ""),),
        err="获取群文件下载链接失败", err_invalid=_GROUP_ID_ERR,
    ),
    _Tool(
        name="send_group_notice", action="_send_group_notice", desc="发送群公告。",
        params=(
            _Param("chat_id", desc="群号", key="group_id", to_int=True),
            _Param("content", desc="公告内容"),
        ),
        ok_echo=("chat_id",),
        err="发送群公告失败", err_invalid=_GROUP_ID_ERR,
    ),
    _Tool(
        name="get_group_honor_info", action="get_group_honor_info",
        desc="获取群荣誉信息（龙王、群聊之火等）。",
        params=(
            _Param("chat_id", desc="群号", key="group_id", to_int=True),
            _Param("honor_type", default="all", key="type",
                   desc="荣誉类型（talkative/performer/legend/strong_newbie/emotion/all）"),
        ),
        mode="raw",
        err="获取群荣誉信息失败", err_invalid=_GROUP_ID_ERR,
    ),
    _Tool(
        name="set_group_sign", action="set_group_sign", desc="群签到。",
        params=(_Param("chat_id", desc="群号", key="group_id", to_int=True),),
        ok_echo=("chat_id",),
        err="群签到失败", err_invalid=_GROUP_ID_ERR,
    ),
    _Tool(
        name="get_ai_record", action="get_ai_record", desc="AI 文字转语音。",
        params=(
            _Param("text", desc="要转换的文本"),
            _Param("character", default="", desc="AI 语音角色（为空则使用默认角色）"),
        ),
        mode="data",
        data_fields=(
            ("file", "file", str, ""),
            ("url", "url", str, ""),
        ),
        err="AI 文字转语音失败",
    ),
    _Tool(
        name="get_ai_characters", action="get_ai_characters", desc="获取 AI 语音角色列表。",
        mode="list", list_source="characters", list_out="characters",
        err="获取 AI 语音角色列表失败",
    ),
    _Tool(
        name="send_group_ai_record", action="send_group_ai_record", desc="群聊发送 AI 语音。",
        params=(
            _Param("chat_id", desc="群号", key="group_id", to_int=True),
            _Param("text", desc="要转换的文本"),
            _Param("character", default="", desc="AI 语音角色（为空则使用默认角色）"),
        ),
        ok_echo=("chat_id",),
        err="发送 AI 语音失败", err_invalid=_GROUP_ID_ERR,
    ),
    _Tool(
        name="get_friends_with_category", action="get_friends_with_category",
        desc="获取分类的好友列表。",
        mode="raw", err="获取分类好友列表失败",
    ),
    _Tool(
        name="translate_en2zh", action="translate_en2zh", desc="英译中。",
        params=(_Param("text", desc="要翻译的英文文本"),),
        mode="raw", err="翻译失败",
    ),
    _Tool(
        name="mark_private_msg_as_read", action="mark_private_msg_as_read", desc="设置私聊消息已读。",
        params=(_Param("user_id", desc="用户 QQ 号", to_int=True),),
        ok_echo=("user_id",),
        err="设置已读失败", err_invalid=_USER_ID_ERR,
    ),
    _Tool(
        name="mark_group_msg_as_read", action="mark_group_msg_as_read", desc="设置群聊消息已读。",
        params=(_Param("chat_id", desc="群号", key="group_id", to_int=True),),
        ok_echo=("chat_id",),
        err="设置已读失败", err_invalid=_GROUP_ID_ERR,
    ),
    _Tool(
        name="set_self_longnick", action="set_self_longnick", desc="设置签名。",
        sensitive=True,
        params=(_Param("longnick", desc="签名内容"),),
        err="设置签名失败",
    ),
    _Tool(
        name="get_recent_contact", action="get_recent_contact", desc="获取最近联系人列表。",
        mode="raw", err="获取最近联系人失败",
    ),
    _Tool(
        name="get_file", action="get_file", desc="获取文件信息。",
        params=(_Param("file_id", desc="文件 ID"),),
        mode="data",
        data_fields=(
            ("file", "file", str, ""),
            ("url", "url", str, ""),
            ("size", "size", None, 0),
        ),
        err="获取文件信息失败",
    ),
    _Tool(
        name="create_collection", action="create_collection", desc="创建收藏。",
        params=(
            _Param("title", desc="收藏标题"),
            _Param("content", desc="收藏内容"),
        ),
        err="创建收藏失败",
    ),
    _Tool(
        name="get_collection_list", action="get_collection_list", desc="获取收藏列表。",
        mode="list", list_source="collections", list_out="collections",
        err="获取收藏列表失败",
    ),
    _Tool(
        name="mark_all_as_read", action="_mark_all_as_read", desc="标记所有消息已读。",
        err="标记所有已读失败",
    ),
    _Tool(
        name="get_profile_like", action="get_profile_like", desc="获取自身点赞列表。",
        mode="raw", err="获取点赞列表失败",
    ),
    _Tool(
        name="fetch_custom_face", action="fetch_custom_face", desc="获取自定义表情。",
        params=(_Param("count", kind=int, default=10, desc="获取数量"),),
        mode="list", list_source="faces", list_out="faces",
        err="获取自定义表情失败",
    ),
    _Tool(
        name="set_online_status", action="set_online_status", desc="设置在线状态。",
        sensitive=True,
        params=(_Param("status", desc="在线状态（online/away/busy/invisible/offline）"),),
        err="设置在线状态失败",
    ),
    _Tool(
        name="get_robot_uin_range", action="get_robot_uin_range", desc="获取机器人账号范围。",
        mode="raw", err="获取机器人账号范围失败",
    ),
    _Tool(
        name="ark_share_peer", action="ArkSharePeer", desc="获取推荐好友/群聊卡片。",
        params=(_Param("user_id", desc="用户 QQ 号"),),
        mode="raw", err="获取推荐卡片失败",
    ),
    _Tool(
        name="ark_share_group", action="ArkShareGroup", desc="获取推荐群聊卡片。",
        params=(_Param("chat_id", desc="群号", key="group_id"),),
        mode="raw", err="获取推荐群聊卡片失败",
    ),
)


# ------------------------------------------------------------------
# 工具工厂
# ------------------------------------------------------------------

def _build_docstring(spec: _Tool) -> str:
    """按声明表生成 docstring（首行描述 + Args 参数说明），供 schema 提取。"""
    lines = [spec.desc]
    documented = [(p.name, p.desc) for p in spec.params if p.desc]
    if documented:
        lines.append("")
        lines.append("Args:")
        lines.extend(f"    {name}: {desc}" for name, desc in documented)
    return "\n".join(lines)


def _err_text(err: _ErrText, raw: Dict[str, Any]) -> str:
    """解析失败提示：支持固定字符串或按参数生成的 lambda。"""
    return err(raw) if callable(err) else err


def _build_tool(spec: _Tool) -> Callable[..., Any]:
    """按声明生成一个 API 包装方法（带真实签名/docstring/@channel_tool 标记）。"""

    async def _impl(self: Any, **kwargs: Any) -> str:
        raw: Dict[str, Any] = {}
        for p in spec.params:
            if p.name in kwargs:
                raw[p.name] = kwargs[p.name]
            elif p.default is not _MISSING:
                raw[p.name] = p.default
            else:
                raise TypeError(f"missing required argument: {p.name}")

        parsed: Dict[str, Any] = dict(raw)
        for p in spec.params:
            if p.to_int:
                try:
                    parsed[p.name] = int(raw[p.name])
                except (ValueError, TypeError):
                    return _err(spec.err_invalid.format(**raw))

        api_params: Dict[str, Any] = {}
        for p in spec.params:
            if p.key is None:
                continue
            value = parsed[p.name]
            if p.transform is not None:
                value = p.transform(value)
            api_params[p.key or p.name] = value
        api_params.update(dict(spec.extra))

        if spec.mode == "action":
            ok = await self._call_api(spec.action, api_params)
            if not ok:
                return _err(_err_text(spec.err, raw))
            return _ok({name: raw[name] for name in spec.ok_echo})

        data = await self._call_api_data(spec.action, api_params)
        if data is None:
            return _err(_err_text(spec.err, raw))

        if spec.mode == "raw":
            return _ok_raw(data)

        if spec.mode == "data":
            return _ok({
                out: (cast(data.get(src, dflt)) if cast else data.get(src, dflt))
                for out, src, cast, dflt in spec.data_fields
            })

        # list 模式：data 为 dict 时取 list_source 键，否则 data 本身即列表
        items: Any = data.get(spec.list_source, []) if isinstance(data, dict) else data
        if spec.item_fields:
            items = [
                {
                    out: (cast(item.get(src, dflt)) if cast else item.get(src, dflt))
                    for out, src, cast, dflt in spec.item_fields
                }
                for item in items
            ]
        result: Dict[str, Any] = {spec.list_out: items}
        if spec.with_count:
            result["count"] = len(items)
        return _ok(result)

    _impl.__name__ = spec.name
    _impl.__qualname__ = f"QQToolsMixin.{spec.name}"
    _impl.__doc__ = _build_docstring(spec)
    parameters = [inspect.Parameter("self", inspect.Parameter.POSITIONAL_OR_KEYWORD)]
    for p in spec.params:
        default = inspect.Parameter.empty if p.default is _MISSING else p.default
        parameters.append(inspect.Parameter(
            p.name, inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=p.kind, default=default,
        ))
    parameters.append(inspect.Parameter("kwargs", inspect.Parameter.VAR_KEYWORD, annotation=Any))
    _impl.__signature__ = inspect.Signature(parameters, return_annotation=str)  # type: ignore[attr-defined]

    return channel_tool(description=spec.desc, sensitive=spec.sensitive)(_impl)


# ------------------------------------------------------------------
# 工具 Mixin：表驱动工具（底部批量挂载）+ 无法表化的特殊工具（手写）
# ------------------------------------------------------------------

class QQToolsMixin:
    """QQ 频道工具层：表驱动 OneBot API 工具 + 特殊工具手写实现。

    依赖频道实例提供 ``_call_api`` / ``_call_api_data`` / ``_call_api_raw`` /
    ``_cfg`` / ``channel_id`` / ``_self_id``，由频道类经多继承装配。
    """

    if TYPE_CHECKING:
        channel_id: str
        _self_id: str

        def _cfg(self, key: str, default: Any = None) -> Any: ...
        async def _call_api(self, action: str, params: Dict[str, Any]) -> bool: ...
        async def _call_api_data(self, action: str, params: Dict[str, Any]) -> Optional[Any]: ...
        async def _call_api_raw(self, action: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]: ...

    @channel_tool(description="转发单条消息到指定会话")
    async def forward_msg(self, chat_id: str, from_chat_id: str, message_id: str, **kwargs: Any) -> str:
        channel_type = kwargs.get("channel_type", "private")
        try:
            mid = int(message_id)
            cid = int(chat_id)
        except (ValueError, TypeError):
            return _err(f"无效的 ID: chat_id={chat_id}, message_id={message_id}")
        if channel_type == "group":
            ok = await self._call_api("forward_group_single_msg", {"message_id": mid, "group_id": cid})
        else:
            ok = await self._call_api("forward_friend_single_msg", {"message_id": mid, "user_id": cid})
        return _ok({"chat_id": chat_id}) if ok else _err("转发失败")

    @channel_tool(description="获取会话信息（群聊为群信息，私聊为用户信息）")
    async def get_chat_info(self, chat_id: str, **kwargs: Any) -> str:
        channel_type = kwargs.get("channel_type", "group")
        try:
            cid = int(chat_id)
        except (ValueError, TypeError):
            return _err(f"无效的 ID: {chat_id}")
        if channel_type == "group":
            result = await self._call_api_data("get_group_info", {"group_id": cid})
        else:
            result = await self._call_api_data("get_stranger_info", {"user_id": cid})
        return json.dumps({"success": True, "data": result}, ensure_ascii=False) if result else _err("查询失败")

    @channel_tool(description="获取群成员列表")
    async def get_chat_members(self, chat_id: str, **kwargs: Any) -> str:
        try:
            gid = int(chat_id)
        except (ValueError, TypeError):
            return _err(f"无效的群 ID: {chat_id}")
        result = await self._call_api_data("get_group_member_list", {"group_id": gid})
        return json.dumps({"success": True, "data": result}, ensure_ascii=False) if result else _err("查询失败")

    @channel_tool()
    async def message_reaction(self, chat_id: str, message_id: str, emoji_id: str = "212", **kwargs: Any) -> str:
        """对指定消息添加表情回应（NapCat 扩展 API）。message_id 取自消息标签 [message_id:xxx]，仅对缓存期内的近期消息有效。"""
        try:
            mid = int(message_id)
        except (ValueError, TypeError):
            return _err(f"无效的消息 ID: {message_id}")
        raw = await self._call_api_raw("set_msg_emoji_like", {
            "message_id": mid, "emoji_id": str(emoji_id),
        })
        if raw and raw.get("retcode") == 0:
            return _ok({"message_id": message_id, "emoji_id": emoji_id})
        wording = str((raw or {}).get("wording") or (raw or {}).get("message") or "未知原因")
        return json.dumps({
            "success": False,
            "error": f"表情回应失败: {wording}",
            "retcode": (raw or {}).get("retcode"),
            "message_id": message_id,
        }, ensure_ascii=False)

    @channel_tool()
    async def send_poke(self, chat_id: str, user_id: str, **kwargs: Any) -> str:
        """向指定用户发送戳一戳互动。群聊中 chat_id 为群号，私聊中 chat_id 与 user_id 相同。"""
        try:
            uid = int(user_id)
        except (ValueError, TypeError):
            return _err(f"无效的用户 ID: {user_id}")

        # 优先使用调用方注入的 channel_type（源自事件 message_type 的路由缓存），
        # 缺失时回退 chat_id != user_id 启发式
        channel_type = kwargs.get("channel_type")
        if not channel_type:
            channel_type = "group" if chat_id != user_id else "private"
            log(f"QQ send_poke 缺少 channel_type，回退启发式判定: {chat_id=} -> {channel_type}",
                "DEBUG", tag="通道")

        try:
            if channel_type == "group":
                try:
                    gid = int(chat_id)
                except (ValueError, TypeError):
                    return _err(f"无效的群 ID: {chat_id}")
                ok = await self._call_api("group_poke", {"group_id": gid, "user_id": uid})
            else:
                ok = await self._call_api("friend_poke", {"user_id": uid})
            return _ok({"chat_id": chat_id, "user_id": user_id}) if ok else _err("戳一戳失败")
        except Exception as exc:
            # 捕获 NapCat 版本不兼容错误
            error_msg = str(exc)
            if "packetBackend" in error_msg or "不支持当前QQ版本" in error_msg:
                log(f"戳一戳功能不可用（NapCat 版本不兼容）: {error_msg}", "WARNING")
                return _err("戳一戳功能当前不可用（NapCat 版本不兼容，请检查 QQ 版本或升级 NapCat）")
            return _err(f"戳一戳失败: {error_msg}")

    @channel_tool()
    async def send_forward_msg(self, chat_id: str, content: str, **kwargs: Any) -> str:
        """将长文本以合并转发消息形式发送，自动按段落拆分。"""
        channel_type = kwargs.get("channel_type")
        if not channel_type:
            from agent.channel.manager import get_channel_manager
            channel_type = get_channel_manager().resolve_channel_type(self.channel_id, chat_id)

        try:
            cid = int(chat_id)
        except (ValueError, TypeError):
            return _err(f"无效的 ID: {chat_id}")

        sections = _split_forward_sections(content)
        if not sections:
            return _err("消息内容为空，无法发送合并转发")

        bot_name = "Bot"
        nodes = [
            {
                "type": "node",
                "data": {
                    "name": bot_name,
                    "uin": self._self_id or "0",
                    "content": [{"type": "text", "data": {"text": sec}}],
                },
            }
            for sec in sections
        ]

        if channel_type == "group":
            ok = await self._call_api("send_group_forward_msg", {
                "group_id": cid, "messages": nodes,
            })
        else:
            ok = await self._call_api("send_private_forward_msg", {
                "user_id": cid, "messages": nodes,
            })
        return _ok({"chat_id": chat_id, "sections": len(sections)}) if ok else _err("发送合并转发失败")

    @channel_tool()
    async def upload_group_file(self, chat_id: str, file_path: str, name: str = "", folder: str = "/", **kwargs: Any) -> str:
        """上传群文件。

        Args:
            chat_id: 群号
            file_path: 本地文件路径
            name: 文件名（为空则使用原文件名）
            folder: 上传到的文件夹路径（默认根目录）
        """
        try:
            gid = int(chat_id)
        except (ValueError, TypeError):
            return _err(f"无效的群 ID: {chat_id}")

        if not os.path.exists(file_path):
            return _err(f"文件不存在: {file_path}")

        # 读取文件内容并转为 base64（大文件移入线程，避免阻塞事件循环）
        file_content = await asyncio.to_thread(_read_file_base64, file_path)

        file_name = name or os.path.basename(file_path)
        ok = await self._call_api("upload_group_file", {
            "group_id": gid,
            "file": file_content,
            "name": file_name,
            "folder": folder,
        })
        return _ok({"chat_id": chat_id, "file_name": file_name}) if ok else _err("上传群文件失败")

    @channel_tool(sensitive=True)
    async def set_qq_avatar(self, file_path: str, **kwargs: Any) -> str:
        """设置 QQ 头像。

        Args:
            file_path: 头像文件路径
        """
        if not os.path.exists(file_path):
            return _err(f"文件不存在: {file_path}")

        file_content = await asyncio.to_thread(_read_file_base64, file_path)

        ok = await self._call_api("set_qq_avatar", {
            "file": file_content,
        })
        return _ok({}) if ok else _err("设置 QQ 头像失败")

    @channel_tool()
    async def download_file(self, file_id: str, save_name: str = "", **kwargs: Any) -> str:
        """下载文件到本地 workspace/uploads/file/，返回本地路径。

        用于消息标签中标记「未下载」且带 [media_file_id:xxx] 的文件。
        同机部署时直接复制 NapCat 本地缓存，否则从 URL 下载（上限 20MB）。

        Args:
            file_id: 文件 ID（消息标签 [media_file_id:xxx] 中的值）
            save_name: 期望保存的文件名（可选，默认使用原始文件名）
        """
        from agent.channel.media import download_to_uploads, get_upload_dir

        data = await self._call_api_data("get_file", {"file_id": file_id})
        if data is None:
            return _err("获取文件信息失败")

        src_file = str(data.get("file", "") or "")
        url = str(data.get("url", "") or "")
        name = save_name.strip() or str(
            data.get("file_name", "") or data.get("name", "") or "")

        # 同机部署：get_file 返回的本地路径直接复制，避免重复下载
        if src_file and os.path.isfile(src_file):
            if not name:
                name = os.path.basename(src_file)
            dl_dir = os.path.join(get_upload_dir(), "file")
            os.makedirs(dl_dir, exist_ok=True)
            local_path = os.path.join(
                dl_dir,
                f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}_{os.path.basename(name)}",
            )
            try:
                shutil.copyfile(src_file, local_path)
            except OSError as exc:
                return _err(f"复制本地文件失败: {exc}")
            return _ok({
                "path": local_path,
                "name": os.path.basename(local_path),
                "size": os.path.getsize(local_path),
                "hint": "文件已就绪，可用 read_file 读取该路径进行分析",
            })

        if url.startswith(("http://", "https://")):
            local_path = await download_to_uploads(url, SegmentType.FILE, save_name=name)
            if local_path:
                return _ok({
                    "path": local_path,
                    "name": os.path.basename(local_path),
                    "size": os.path.getsize(local_path),
                    "hint": "文件已就绪，可用 read_file 读取该路径进行分析",
                })
            return _err("文件下载失败（可能超过 20MB 限制或网络错误）")
        return _err("未获取到可用的下载地址")


# 按声明表批量生成工具方法并挂载到 Mixin
for _spec in _TOOL_SPECS:
    setattr(QQToolsMixin, _spec.name, _build_tool(_spec))
