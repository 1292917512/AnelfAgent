"""操作态势注入 — 纪律指引 + 操作目录 + MCP 概览 + 近期执行态势。

渲染纪律（对齐上下文注入的预算工程）：
- 分段预算：纪律/目录/概览/态势四段各有字符上限，段内逐条渲染、
  超预算跳过该条继续（skip-not-stop），末尾汇总省略数——绝不半截截断；
- 静态在前动态在后：纪律与目录字节稳定（仅目录增删时变化），近期执行
  态势是动态尾段（每次执行后变化）——变化只漂移注入块尾部；
- 排序即优先级：注册操作按"有注释在前、最近执行在前"渲染，预算不足时
  被跳过的必然是最不可能被用到的；
- 无内容零注入：桌面未就绪且无注册操作且无已连接 MCP 时返回 None。
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

from core.context_provider import ProviderSnapshot
from entities._sdk import context_provider

from . import executor, framework

# 分段字符预算（合计与 provider max_tokens=800 对齐，中文按 3 字符/token 估）
_BUDGET_GUIDE = 420
_BUDGET_OPERATIONS = 1200
_BUDGET_MCP = 300
_BUDGET_RECENT = 420

_DESKTOP_GUIDE = (
    "桌面操控纪律：先 vision_look 看屏定位坐标 → desktop_act 执行 → 再看屏验证，"
    "小步推进不盲操作；删除/支付/发送类不可逆动作先向用户确认；"
    "desktop.type 仅 ASCII（中文经剪贴板+hotkey 粘贴）；急停=鼠标猛移屏幕左上角。"
)


def _render_guide(desktop_ready: bool) -> str:
    return _DESKTOP_GUIDE[:_BUDGET_GUIDE] if desktop_ready else ""


def _render_operations(specs: List[framework.OperationSpec]) -> str:
    """注册操作目录：有注释在前、最近执行在前；预算内逐条、超预算跳过。"""
    ops = [s for s in specs if s.kind == framework.KIND_MCP and s.enabled]
    if not ops:
        return ""
    recent_order = {h["op"]: i for i, h in enumerate(executor.history(50))}
    ops.sort(key=lambda s: (not s.annotation, recent_order.get(s.id, 999)))

    lines: List[str] = ["注册操作（execute_operation 按注释语义调用）："]
    used = len(lines[0])
    omitted = 0
    for spec in ops:
        params = ",".join(str(p.get("name", "")) for p in spec.params[:5]) or "无参数"
        note = spec.annotation or spec.description[:40] or spec.title
        line = f"- {spec.id}：{note}（参数: {params}）"
        if used + len(line) > _BUDGET_OPERATIONS:
            omitted += 1
            continue
        lines.append(line)
        used += len(line)
    if omitted:
        lines.append(f"（另有 {omitted} 个操作因篇幅省略，list_operations 可查全量）")
    return "\n".join(lines)


def _render_mcp(connected: Dict[str, List[str]]) -> str:
    if not connected:
        return ""
    summary = "、".join(f"{name}({len(tools)} 工具)" for name, tools in connected.items())
    text = (
        f"已连接 MCP：{summary}。未注册为操作的工具可 "
        "activate_tool_group(\"mcp:<server>\") 激活直用，常用者建议 register_mcp_operation 注册。"
    )
    return text[:_BUDGET_MCP]


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
    name="operation", priority=33, max_tokens=800,
    group="operation", inject_key="operation_context_inject",
)
class OperationProvider:
    """注入操作态势（纪律 + 目录 + MCP 概览 + 近期执行）。"""

    def __init__(self) -> None:
        self.last_inject_at: float = 0.0

    async def provide(self, scope: str) -> Optional[ProviderSnapshot]:
        from . import desktop as desktop_exec

        desktop_ready = desktop_exec.runtime_ready()
        specs = framework.list_operations()
        has_desktop = any(
            s.kind == framework.KIND_DESKTOP and s.enabled for s in specs
        )
        connected: Dict[str, List[str]] = {}
        gateway = executor.mcp_gateway()
        if gateway is not None:
            try:
                connected = gateway.connected_servers()
            except Exception:
                connected = {}

        sections = [
            _render_guide(desktop_ready and has_desktop),
            _render_operations(specs),
            _render_mcp(connected),
            _render_recent(),
        ]
        if has_desktop and not desktop_ready:
            sections.insert(
                0, "桌面操控暂不可用（pyautogui 环境未同步或无显示环境），注册操作不受影响。",
            )
        body = "\n".join(s for s in sections if s)
        if not body:
            return None
        self.last_inject_at = time.time()
        return ProviderSnapshot(
            content="[系统注入·操作态势]\n" + body, ready=True,
        )
