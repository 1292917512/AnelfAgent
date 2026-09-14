"""视觉工具 — AI 侧的看画面、盯屏开关与视觉源管理。

vision_look 走多模态工具结果约定（_multimodal: true）：视觉模型
立即"亲眼看到"画面，非视觉模型拿到路径标签经媒体工具链识别。

隐私纪律：截屏/盯屏是隐私敏感操作，工具默认可被审批规则引擎拦截
（用户可在权限规则中为 vision 组配置 ask）；监视默认关闭，注入开关
vision_context_inject 单独可关。
"""

from __future__ import annotations

import json

from core.tool_errors import ErrorCause
from entities._sdk import deferred_tool, tool_error

from .buffer import get_vision_buffer
from .framework import all_sources, get_source, is_enabled, set_enabled
from .watcher import get_vision_watcher


def _frame_result(path: str, note: str, changed: bool) -> str:
    return json.dumps({
        "_multimodal": True,
        "text": note,
        "images": [path],
        "path": path,
        "changed_since_last": changed,
    }, ensure_ascii=False)


@deferred_tool(group="vision")
async def vision_look(source: str = "screen") -> str:
    """立即查看指定视觉源的画面（视觉模型将直接看到）。

    Args:
        source: 视觉源 key（默认 screen 本机屏幕；外部源取该源最新推送帧）
    """
    src = get_source(source)
    if src is None:
        known = ", ".join(s.key for s in all_sources()) or "（无）"
        return tool_error(
            f"视觉源不存在: {source}", cause=ErrorCause.NOT_FOUND,
            hint=f"可用源: {known}", retryable=False,
        )
    if not is_enabled(source):
        return tool_error(
            f"视觉源已停用: {source}", cause=ErrorCause.STATE,
            hint="用 vision_source_set 激活后再看", retryable=False,
        )
    buffer = get_vision_buffer()
    if src.can_capture:
        try:
            frame = await src.capture()
        except Exception as exc:
            return tool_error(
                f"画面捕获失败: {exc}", cause=ErrorCause.PERMISSION,
                hint="检查系统屏幕录制/摄像头权限与该源的连接状态", retryable=False,
            )
        if frame is None:
            return tool_error(f"视觉源 {source} 当前无画面", cause=ErrorCause.STATE, retryable=True)
        _f, changed = await buffer.ingest(
            frame.path, source,
            width=frame.width, height=frame.height, captured_at=frame.captured_at,
        )
        return _frame_result(
            frame.path,
            f"已获取 {src.display_name} 画面（{frame.width}x{frame.height}）",
            changed,
        )
    # 外部推送源：取该源最新帧（附帧年龄——AI 判断画面时效的依据）
    latest = buffer.latest_by_source.get(source)
    if latest is None:
        return tool_error(
            f"外部视觉源 {source} 尚无推送画面", cause=ErrorCause.STATE, retryable=True,
        )
    import time
    age_s = max(0, int(time.time() - latest.captured_at))
    result = json.loads(_frame_result(
        latest.path, f"{source} 最新推送画面（{age_s} 秒前捕获）", False))
    result["captured_age_s"] = age_s
    return json.dumps(result, ensure_ascii=False)


@deferred_tool(group="vision")
async def vision_watch(action: str = "status", source: str = "screen", interval: float = 0) -> str:
    """视觉源监视开关：持续观察画面变化（变化时 AI 每轮上下文带上最新画面）。

    Args:
        action: start 开始监视 / stop 停止 / status 查看状态（默认）
        source: 视觉源 key（默认 screen；仅轮询型源可监视）
        interval: start 时的捕获间隔秒数（0 = 用配置 vision_watch_interval_s，默认 5s）
    """
    from entities._sdk import save_config_value

    watcher = get_vision_watcher()
    action = action.strip().lower()

    if action == "start":
        if interval > 0:
            save_config_value("vision_watch_interval_s", interval)
        error = await watcher.start(source)
        if error:
            return tool_error(error, cause=ErrorCause.PARAM, retryable=False)
        return json.dumps({"success": True, "watching": watcher.watching_sources()}, ensure_ascii=False)
    if action == "stop":
        await watcher.stop(source)
        return json.dumps({"success": True, "watching": watcher.watching_sources()}, ensure_ascii=False)
    if action == "status":
        return json.dumps(watcher.status(), ensure_ascii=False, default=str)
    return tool_error(
        f"未知 action: {action}", cause=ErrorCause.PARAM,
        hint="可选 start/stop/status", retryable=False,
    )


@deferred_tool(group="vision", concurrency_safe=True)
def vision_source_set(source: str, enabled: bool = True) -> str:
    """激活或停用视觉源：停用后监视停止、AI 与 Web 均不可再取帧（激活状态持久化）。

    Args:
        source: 视觉源 key（经 vision_sources 查看）
        enabled: true 激活 / false 停用（默认 true）
    """
    src = get_source(source)
    if src is None:
        known = ", ".join(s.key for s in all_sources()) or "（无）"
        return tool_error(
            f"视觉源不存在: {source}", cause=ErrorCause.NOT_FOUND,
            hint=f"可用源: {known}", retryable=False,
        )
    set_enabled(source, enabled)
    if not enabled:
        import asyncio
        watcher = get_vision_watcher()
        if watcher.watching(source):
            try:
                asyncio.get_running_loop().create_task(watcher.stop(source))
            except RuntimeError:
                pass
    from .framework import disabled_sources
    return json.dumps({
        "source": source, "enabled": enabled,
        "disabled_sources": sorted(disabled_sources()),
    }, ensure_ascii=False)


@deferred_tool(group="vision", concurrency_safe=True)
def vision_sources() -> str:
    """列出全部视觉源（类型/能力/激活与监视状态/最新帧时间）。"""
    watcher = get_vision_watcher()
    buffer = get_vision_buffer()
    out = []
    for src in all_sources():
        latest = buffer.latest_by_source.get(src.key)
        out.append({
            "key": src.key,
            "display_name": src.display_name,
            "description": src.description,
            "pollable": src.poll_interval > 0 and src.can_capture,
            "can_capture": src.can_capture,
            "enabled": is_enabled(src.key),
            "watching": watcher.watching(src.key),
            "latest_at": latest.captured_at if latest else None,
        })
    return json.dumps({"sources": out, "count": len(out)}, ensure_ascii=False)
