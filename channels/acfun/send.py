"""AcFun 出站发送 — 会话 ID 路由（评论区回复 / 直播间弹幕）与文本分段。

目标语法（与 parser.py 的入站会话 ID 同构，思维回复经 ChannelManager 原样回传）：
- ``comment:{rtype}:{rid}`` — 评论/回复（rtype: 1 番剧/2 视频/3 文章/10 动态）
- ``live:{uid}`` — 直播间弹幕（受同房间冷却限制）
- ``user:{uid}`` — 私信，acfunsdk 未实现出站私信，返回明确错误引导
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Dict, Optional, Tuple

from agent.channel.channel_types import _err, _ok

if TYPE_CHECKING:
    from .adapter import AcfunChannel
    from .client import AcfunClient

_TARGET_RE = re.compile(r"^(comment|live|user):(.+)$")
_COMMENT_RE = re.compile(r"^(\d+):(\w+)$")
_MAX_CHUNKS = 5  # 评论分段上限（防长文拆成评论刷屏）

_TARGET_USAGE = (
    "无法识别的 AcFun 会话目标。支持：comment:{rtype}:{rid}（评论区，rtype 1 番剧/2 视频/3 文章/10 动态）、"
    "live:{uid}（直播间弹幕）；AcFun 私信出站暂不支持"
)

# 通知中心/系统通知是只读聚合会话，直接回复没有意义，引导到对应工具
_NOTIFICATION_GUIDANCE = (
    "这是通知中心会话（平台推送聚合，非真人对话），不支持直接回复。"
    "要回应通知内容：回评论用 acfun_send_comment(rtype, rid, content, reply_id=ncid)；"
    "弹幕互动用 live:{uid} 目标"
)
_SYSTEM_GUIDANCE = "系统通知会话为只读记录，不支持回复，也无需回复"


def parse_chat_target(chat_id: str) -> Optional[Tuple[str, str]]:
    """解析会话目标为 (kind, rest)；kind ∈ comment/live/user，不合法返回 None。"""
    match = _TARGET_RE.match((chat_id or "").strip())
    if not match:
        return None
    kind, rest = match.group(1), match.group(2).strip()
    if not rest:
        return None
    return kind, rest


def _split_text(text: str, max_length: int) -> list:
    """按最大长度分段（优先换行边界），超限段数截断并追加省略标记。"""
    limit = max(max_length, 50)
    chunks: list = []
    remaining = text
    while len(remaining) > limit and len(chunks) < _MAX_CHUNKS - 1:
        cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip("\n")
    chunks.append(remaining if len(remaining) <= limit else remaining[: limit - 3] + "...")
    return [c for c in chunks if c]


async def send_channel_text(
    channel: "AcfunChannel",
    chat_id: str,
    text: str,
    reply_to: Optional[str] = None,
) -> str:
    """频道文本发送主路径：按目标前缀路由到评论回复或直播弹幕。"""
    client = channel.client
    # 通知/系统聚合会话优先短路（无需登录检查，直接给 AI 可执行的引导）
    if chat_id == "notification":
        return _err(_NOTIFICATION_GUIDANCE)
    if chat_id == "system":
        return _err(_SYSTEM_GUIDANCE)
    if not client.is_logined:
        return _err("AcFun 未登录，请先在频道页完成账号登录")
    target = parse_chat_target(chat_id)
    if target is None:
        return _err(f"{_TARGET_USAGE}，收到: {chat_id!r}")
    kind, rest = target

    if kind == "user":
        return _err("AcFun 私信出站暂不支持（acfunsdk 未实现），请改用评论回复（comment: 目标）")

    chunks = _split_text(text, channel.config.message_max_length)

    if kind == "comment":
        comment_match = _COMMENT_RE.match(rest)
        if not comment_match:
            return _err(f"评论目标格式应为 comment:{{rtype}}:{{rid}}，收到: {chat_id!r}")
        rtype, rid = comment_match.group(1), comment_match.group(2)
        for index, chunk in enumerate(chunks):
            try:
                ok = await client.run(
                    client.send_comment, rtype, rid, chunk, reply_to if index == 0 else None,
                )
            except Exception as exc:
                return _err(f"评论发送失败（第 {index + 1}/{len(chunks)} 段）: {exc}")
            if not ok:
                return _err(f"评论发送被拒（第 {index + 1}/{len(chunks)} 段，可能触发平台风控或内容违规）")
        return _ok({"chat_id": chat_id, "message_id": f"acfun-comment-{rtype}-{rid}", "chunks": len(chunks)})

    # live：直播间弹幕，同房间冷却防刷屏
    uid = rest
    cooldown = max(int(channel.config.live_danmaku_cooldown_seconds), 0)
    result = await send_live_danmaku_gated(
        channel.client, cooldown, channel.live_danmaku_last_sent, uid, chunks,
    )
    return result


async def send_live_danmaku_gated(
    client: "AcfunClient",
    cooldown_seconds: int,
    last_sent: Dict[str, float],
    uid: str,
    chunks: list,
) -> str:
    """直播间弹幕发送（冷却门控）：供频道 send_text 路径与 send_live_danmaku 工具共用。"""
    if not client.is_logined:
        return _err("AcFun 未登录，请先在频道页完成账号登录")
    if cooldown_seconds and time.time() - last_sent.get(uid, 0.0) < cooldown_seconds:
        return _err(f"直播间 {uid} 弹幕冷却中（{cooldown_seconds}s 内已发送），请稍后再发")
    try:
        for chunk in chunks:
            ok = await client.run(client.push_live_danmaku, uid, chunk)
            if not ok:
                return _err("直播弹幕发送被拒（主播未开播或平台限制）")
        last_sent[uid] = time.time()
    except Exception as exc:
        return _err(f"直播弹幕发送失败: {exc}")
    return _ok({"chat_id": f"live:{uid}", "message_id": f"acfun-live-{uid}", "chunks": len(chunks)})
