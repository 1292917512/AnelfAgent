"""AcFun 直播状态上下文注入 — 直播模式开启时向 volatile 层注入实时房间快照。

经 core.context_provider 注册（channels → core 合法依赖方向），落在 provider 层
（VOL_SESSION+4，历史之后的尾部动态区），一轮滞后的后台快照、<1s 超时、异常
fail-open 跳过——直播模式关闭时返回 None，本轮完全不注入。
内容纯读 LiveSessionManager 内存（零 I/O），计数/相对时间为 volatile 区合法内容。
"""

from __future__ import annotations

from typing import Optional

from core.context_provider import ContextProviderRegistry, ProviderMeta
from core.log import log

_PROVIDER_NAME = "acfun_live_status"


def _format_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}分钟"
    return f"{seconds // 3600}小时{max(seconds % 3600 // 60, 1)}分"


def _format_age(age: Optional[float]) -> str:
    if age is None:
        return "-"
    if age < 5:
        return "刚刚"
    return f"{_format_duration(age)}前"


def _render_live_status(scope: str = "") -> Optional[str]:
    """渲染直播状态块；模式关闭 / 无房间 / 频道未注册时返回 None（不注入）。

    scope: 当前对话 scope（provider 协议要求接收；直播状态全局一致，不使用）。
    """
    try:
        from agent.channel.manager import get_channel_manager

        channel = get_channel_manager().get("acfun")
        if channel is None:
            return None
        manager = getattr(channel, "live_manager", None)
        if manager is None or not manager.mode_enabled:
            return None
        snap = manager.snapshot()
        rooms = snap.get("rooms") or []
        if not rooms:
            return None

        lines = [f"[AcFun 直播] 模式开启 · {len(rooms)} 个房间"]
        for room in rooms:
            state = str(room.get("state"))
            detail = str(room.get("detail") or "")
            title = str(room.get("title") or "")
            up_name = str(room.get("user_name") or "")
            header = f"- live:{room.get('uid')}"
            if up_name:
                header += f" {up_name}"
            if title:
                header += f"《{title}》"
            if state == "connected":
                uptime = _format_duration(room.get("uptime") or 0)
                header += f" 已连接 {uptime}"
                watching = str(room.get("watching") or "")
                likes = str(room.get("likes") or "")
                banana = str(room.get("banana") or "")
                counts = []
                if watching:
                    counts.append(f"观众≈{watching}")
                if likes:
                    counts.append(f"点赞≈{likes}")
                if banana:
                    counts.append(f"香蕉≈{banana}")
                if counts:
                    header += " " + " ".join(counts)
            elif state == "reconnecting":
                header += f" 重连中（{detail}）"
            elif state == "closed":
                header += f" 未开播（慢速重探中）: {detail}"
            else:
                header += f" {state} {detail}".rstrip()
            lines.append(header)

            stats = room.get("stats") or {}
            recent = manager.recent_danmaku(str(room.get("uid")), limit=int(
                getattr(channel.config, "live_recent_window", 20) or 20))
            if recent:
                lines.append(f"  近5分钟弹幕 {room.get('danmaku_recent', 0)} 条，最近:")
                for item in recent[-8:]:
                    lines.append(f"  · [{item.get('name') or item.get('uid')}] {item.get('text')}")
            gifts = manager.recent_gifts(str(room.get("uid")), limit=3)
            if gifts:
                lines.append("  礼物: " + "；".join(gifts))
            last_error = str(stats.get("last_error") or "")
            diag = f"  诊断: 弹幕{stats.get('danmaku', 0)} 重连{stats.get('reconnects', 0)}次"
            diag += f" 最后信号{_format_age(stats.get('last_signal_age'))}"
            if last_error:
                diag += f" 最后错误: {last_error[:80]}"
            lines.append(diag)
        return "\n".join(lines)
    except Exception as exc:
        log(f"AcFun直播: 上下文注入渲染失败（fail-open 跳过）: {exc}", "DEBUG", tag="通道")
        return None


def register_live_context_provider() -> None:
    """注册直播状态 provider（幂等，channels.acfun.adapter 导入时调用）。"""
    if any(meta.name == _PROVIDER_NAME for meta in ContextProviderRegistry.get_all()):
        return
    ContextProviderRegistry.register(ProviderMeta(
        name=_PROVIDER_NAME,
        priority=30,
        max_tokens=450,
        scope_filter=None,
        group=None,  # 全局常驻；直播模式关闭时 provide 内部返回 None 实现零注入
        provide_fn=_render_live_status,
        description="AcFun 直播模式开启时的实时房间状态与最近弹幕注入",
    ))
