"""操作态势注入 — 按需触发的操作上下文（纪律 + 关联操作 + 近期执行）。

按需纪律：注入不是常态——仅当操作活跃窗口内有执行（desktop_act 或
Web 端操作执行）才注入，窗口随每次执行滑动续期，静默期零注入。层级
排在视觉（32）之后（36）：操作态势比视觉状态更动态，靠后漂移面更小。

内容三段（静态在前动态在后）：
- 桌面纪律：看屏→操作→验证循环、高危确认、ASCII 限制、物理急停；
- 关联操作：已注册的 MCP 操作（注释 + 参数 + 所属工具组）——关联的
  意义是语义索引：执行仍走 mcp:<server> 工具组，注入让 AI 在操作
  语境下直接知道"有哪些语义化能力、怎么激活"，不必翻工具目录；
- 近期执行：最近结果 ✓/✗（行动连续性）。

段内逐条渲染、超预算跳过该条继续（skip-not-stop），绝不半截截断。
"""

from __future__ import annotations

import time
from typing import List, Optional

from core.config import get_config_int
from core.context_provider import ProviderSnapshot
from entities._sdk import context_provider

from . import executor, framework

# 分段字符预算（合计与 provider max_tokens=800 对齐，中文按 3 字符/token 估）
_BUDGET_GUIDE = 420
_BUDGET_OPERATIONS = 1400
_BUDGET_RECENT = 420

_DESKTOP_GUIDE = (
    "桌面操控纪律：先 vision_look 看屏定位坐标 → desktop_act 执行（默认自动回看验证）"
    " → 小步推进不盲操作；删除/支付/发送类不可逆动作先向用户确认；"
    "desktop.type 仅 ASCII（中文经剪贴板+hotkey 粘贴）；急停=鼠标猛移屏幕左上角。"
)


def _render_guide(desktop_ready: bool) -> str:
    return _DESKTOP_GUIDE[:_BUDGET_GUIDE] if desktop_ready else ""


def _render_operations(specs: List[framework.OperationSpec]) -> str:
    """关联操作目录：有注释在前、最近执行在前；预算内逐条、超预算跳过。"""
    ops = [s for s in specs if s.kind == framework.KIND_MCP and s.enabled]
    if not ops:
        return ""
    recent_order = {h["op"]: i for i, h in enumerate(executor.history(50))}
    ops.sort(key=lambda s: (not s.annotation, recent_order.get(s.id, 999)))

    lines: List[str] = [
        "关联操作（语义索引——执行经对应 mcp 工具组，activate_tool_group 激活）：",
    ]
    used = len(lines[0])
    omitted = 0
    for spec in ops:
        params = ",".join(str(p.get("name", "")) for p in spec.params[:5]) or "无参数"
        note = spec.annotation or spec.description[:40] or spec.title
        line = f"- {spec.id}：{note}（参数: {params}｜工具组 mcp:{spec.server}）"
        if used + len(line) > _BUDGET_OPERATIONS:
            omitted += 1
            continue
        lines.append(line)
        used += len(line)
    if omitted:
        lines.append(f"（另有 {omitted} 个关联操作省略，list_operations 可查全量）")
    return "\n".join(lines)


def _render_recent() -> str:
    """近期执行态势（动态尾段）：最近 3 条结果，给 AI 行动连续性。"""
    entries = executor.history(3)
    if not entries:
        return ""
    lines = ["近期操作执行："]
    used = len(lines[0])
    for entry in entries:
        mark = "✓" if entry.get("ok") else "✗"
        detail = str(entry.get("detail", ""))[:60]
        line = f"- {mark} {entry['op']}: {detail}"
        if used + len(line) > _BUDGET_RECENT:
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines) if len(lines) > 1 else ""


@context_provider(
    name="operation", priority=36, max_tokens=800,
    group="operation", inject_key="operation_context_inject",
)
class OperationProvider:
    """操作活跃窗口内注入操作态势；静默期返回 None（零注入）。"""

    def __init__(self) -> None:
        self.last_inject_at: float = 0.0

    async def provide(self, scope: str) -> Optional[ProviderSnapshot]:
        window = max(0, get_config_int("operation_context_window_seconds", 600))
        if executor.seconds_since_activity() > window:
            return None  # 非操作语境：零注入

        from . import desktop as desktop_exec

        desktop_ready = desktop_exec.runtime_ready()
        specs = framework.list_operations()
        has_desktop = any(
            s.kind == framework.KIND_DESKTOP and s.enabled for s in specs
        )
        sections = [
            _render_guide(desktop_ready and has_desktop),
            _render_operations(specs),
            _render_recent(),
        ]
        if has_desktop and not desktop_ready:
            sections.insert(
                0, "桌面操控暂不可用（pyautogui 环境未同步或无显示环境）。",
            )
        body = "\n".join(s for s in sections if s)
        if not body:
            return None
        self.last_inject_at = time.time()
        return ProviderSnapshot(
            content="[系统注入·操作态势]\n" + body, ready=True,
        )
