"""规划态势快照 — 目标/计划执行状态的单一态势数据源与轮内注入。

所有规划写路径（tools 的 goal CRUD / tracker 的 plan 状态机）在变更后调
``invalidate()`` 失效版本化快照；快照按需重建（版本失配或超出再同步窗口，
单飞防并发重查），渲染为纯内存操作。注入经上下文 provider 层（think_loop
每轮尾部收集）——AI 的目标感知不再停留在回复起点的单次快照：回复中途
目标被删除/收敛后下一轮即见最新事实，配合规划工具 not_found 错误附带的
活跃目标简报，过期 goal_id 可立即自纠（连续 update_goal 打不存在目标的
根因）。

Model Experience:
- 模型看到什么：当前会话可见的活跃目标/计划快照（goal_id/标题/步骤进度/
  当前计划和命中本轮问题的目标附步骤、描述与待办备注，其余留查询指针），每轮刷新；reflect（任务/子代理）scope
  不注入，保持 lean 精简语义
- token 影响：无活跃目标时零注入；有目标时受渲染上限约束（数百字符，
  provider 预算再兜底）
- 缓存影响：provider 层位于工具链之后的尾部动态区，不触碰任何前缀缓存层
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from agent.memory.passages import PassageQuery
from agent.planning.goals import iter_goals
from agent.planning.tracker import PLAN_KIND, get_planning_store
from core.config import register_configs_safe
from core.context_provider import ContextProviderRegistry, ProviderMeta
from core.log import log
from core.sanitizer import sanitize_for_context

# 快照再同步窗口（秒）：失效钩子已覆盖全部规划写路径，窗口仅兜底绕过钩子的
# 越轨写入（如记忆工具直接编辑 goal 条目），代价是每窗口至多一次有界重查
_RESYNC_SECONDS = 60.0
# 渲染上限：注入目标数 / 每目标步骤数 / 单字段截断 / 总文本截断
_MAX_GOALS = 5
_MAX_STEPS = 8
_TITLE_CHARS = 40
_STEP_CHARS = 18
_MAX_RENDER_CHARS = 1400

_STEP_MARKS = {"completed": "✓", "in_progress": "▶", "pending": "○", "skipped": "−"}

# 停滞概况的报告门槛（天）：超过即列入心跳事实行，由 AI 决策处置
_STALE_REPORT_DAYS = 7.0
_TS_FORMAT = "%Y-%m-%d %H:%M:%S"

register_configs_safe({
    "planning/core": {
        "goals_inject_enabled": {
            "description": "上下文注入活跃目标/计划态势（每轮刷新：goal_id、步骤进度与状态，AI 始终感知进行中的规划）",
            "default": True,
        },
    },
})


@dataclass(slots=True)
class _GoalView:
    """单条活跃目标的解析视图（渲染与错误简报共用）。"""

    goal_id: str
    title: str
    description: str
    step_notes: list[str]
    is_plan: bool
    plan_scope: str
    steps: List[Tuple[str, str]]
    done: int
    total: int
    updated_days: float


@dataclass(slots=True)
class _Snapshot:
    """全局规划快照（一次重建，任意 scope 渲染共享）。"""

    version: int
    built_at: float
    goals: List[_GoalView] = field(default_factory=list)


_version: int = 0
_snapshot: Optional[_Snapshot] = None
_rebuild_lock = asyncio.Lock()


def invalidate() -> None:
    """失效快照（全部规划写路径变更后调用；下次消费前重建）。"""
    global _version
    _version += 1


def reset() -> None:
    """清空快照状态（测试隔离用）。"""
    global _version, _snapshot
    _version = 0
    _snapshot = None


def _needs_rebuild() -> bool:
    return (
        _snapshot is None
        or _snapshot.version != _version
        or time.monotonic() - _snapshot.built_at > _RESYNC_SECONDS
    )


def _parse_goal(entry: Any, data: dict[str, Any] | None = None) -> Optional[_GoalView]:
    """解析 goal 记忆条目为视图（非 active / 解析失败返回 None）。"""
    try:
        data = json.loads(entry.content) if data is None else data
    except (json.JSONDecodeError, AttributeError, TypeError):
        return None
    if not isinstance(data, dict) or data.get("status") != "active":
        return None
    metadata = entry.metadata if isinstance(entry.metadata, dict) else {}
    is_plan = metadata.get("kind") == PLAN_KIND
    steps: List[Tuple[str, str]] = []
    raw_steps = data.get("steps")
    if isinstance(raw_steps, list):
        for s in raw_steps:
            status = str(s.get("status", "pending")) if isinstance(s, dict) else "pending"
            content = str(s.get("content", "")) if isinstance(s, dict) else ""
            steps.append((status, content))
    goal_id = str(data.get("goal_id", ""))
    if not goal_id:
        return None
    try:
        updated = time.mktime(time.strptime(str(data.get("updated_at", "")), _TS_FORMAT))
        updated_days = max(0.0, (time.time() - updated) / 86400)
    except (ValueError, OverflowError):
        updated_days = 0.0
    return _GoalView(
        goal_id=goal_id,
        title=str(data.get("title", "")),
        description=str(data.get("description") or ""),
        step_notes=[
            f"{step.get('content', '')}: {step['note']}"
            for step in (raw_steps if isinstance(raw_steps, list) else [])
            if isinstance(step, dict) and step.get("note")
            and step.get("status") in ("pending", "in_progress")
        ],
        is_plan=is_plan,
        plan_scope=str(metadata.get("scope", "")) if is_plan else "",
        steps=steps,
        done=sum(1 for status, _ in steps if status == "completed"),
        total=len(steps),
        updated_days=updated_days,
    )


async def _rebuild() -> _Snapshot:
    """从存储重建全局快照（查询失败原样上抛，由调用方降级）。"""
    version = _version
    store = get_planning_store()
    goals: List[_GoalView] = []
    if store is not None:
        async for entry, goal in iter_goals(store, status="active"):
            view = _parse_goal(entry, goal)
            if view is not None:
                goals.append(view)
    return _Snapshot(version=version, built_at=time.monotonic(), goals=goals)


async def ensure_snapshot() -> _Snapshot:
    """取最新快照：版本失配或超出再同步窗口时单飞重建。"""
    global _snapshot
    if _needs_rebuild():
        async with _rebuild_lock:
            if _needs_rebuild():
                _snapshot = await _rebuild()
    assert _snapshot is not None
    return _snapshot


# ------------------------------------------------------------------
# 渲染（纯内存）
# ------------------------------------------------------------------

def _goal_details(goal: _GoalView, query: PassageQuery) -> str:
    """渲染相关步骤、描述和待办备注，保留步骤的原有顺序。"""
    steps = goal.steps
    if len(steps) > _MAX_STEPS:
        def priority(index: int) -> tuple[bool, int, bool]:
            status, content = goal.steps[index]
            return status == "in_progress", query.score(content), status == "pending"

        selected = sorted(range(len(steps)), key=priority, reverse=True)[:_MAX_STEPS]
        steps = [steps[i] for i in sorted(selected)]
    marks = " ".join(
        f"{_STEP_MARKS.get(status, '·')}{query.excerpt(content, _STEP_CHARS)}"
        for status, content in steps
    )
    if len(goal.steps) > _MAX_STEPS:
        marks += f" …（余 {len(goal.steps) - _MAX_STEPS} 步）"
    details = [f"  步骤: {marks}"] if marks else []
    if goal.description:
        details.append(f"  描述: {query.excerpt(goal.description, 200)}")
    notes = sorted(goal.step_notes, key=query.score, reverse=True)
    details.extend(f"  步骤备注: {query.excerpt(note, 180)}" for note in notes[:2])
    return "\n".join(details)


def render(scope: str, focus: str = "") -> str:
    """渲染指定会话可见的规划态势（无目标或非用户会话 scope 返回空）。

    当前 scope 的执行计划（present_plan）置顶，长期目标随后；
    reflect 前缀 scope（任务/子代理）不注入，保持 lean 精简语义。
    """
    snap = _snapshot
    if snap is None or not scope or scope.startswith("reflect"):
        return ""
    visible = [g for g in snap.goals if not (g.plan_scope and g.plan_scope != scope)]
    if not visible:
        return ""
    query = PassageQuery.from_text(focus)

    def priority(goal: _GoalView) -> tuple[bool, bool, int, int]:
        body = " ".join([goal.description, *goal.step_notes, *(step for _, step in goal.steps)])
        return bool(query.score(goal.goal_id)), goal.is_plan, query.score(goal.title), query.score(body)

    ranked = sorted(((priority(g), g) for g in visible), key=lambda item: item[0], reverse=True)
    lines = [
        f"[规划态势] 进行中的目标 {len(visible)} 个（每轮刷新，goal_id 以此为准；"
        "备注中的等待条件须先核实；get_goal 查看全文，update_goal 记录实际结果）：",
    ]
    details_by_line: dict[int, str] = {}
    for index, (rank, goal) in enumerate(ranked[:_MAX_GOALS]):
        kind = "▸ 执行计划" if goal.is_plan else "• 目标"
        progress = f"（{goal.done}/{goal.total} 步）" if goal.total else ""
        lines.append(f"{kind} [{goal.goal_id}] {query.excerpt(goal.title, _TITLE_CHARS)}{progress}")
        if goal.is_plan or (index < 2 and any(rank)) or (not focus and len(visible) == 1):
            details = _goal_details(goal, query)
            if details:
                details_by_line[len(lines) - 1] = details
    if len(ranked) > _MAX_GOALS:
        lines.append(f"…另有 {len(ranked) - _MAX_GOALS} 个目标（list_goals 查看全部）")
    # 先预留每条目标的身份与进度，再由相关目标共享剩余的明细预算。
    remaining = _MAX_RENDER_CHARS - len("\n".join(lines))
    for index, (line, detail) in enumerate(details_by_line.items()):
        limit = remaining // (len(details_by_line) - index) - 1
        if limit >= 3:
            excerpt = query.excerpt(detail, limit)
            lines[line] += "\n" + excerpt
            remaining -= len(excerpt) + 1
    return "\n".join(lines)


# ------------------------------------------------------------------
# 消费面（provider 注入 / 错误简报 / 态势收集）
# ------------------------------------------------------------------

async def _provide(scope: str, focus: str = "") -> Optional[str]:
    """provider 注入口：确保快照新鲜后按 scope 渲染（失败降级为不注入）。"""
    if not scope or scope.startswith("reflect"):
        return None
    try:
        await ensure_snapshot()
    except Exception as exc:
        log(f"规划态势快照重建失败（本轮不注入）: {exc}", "DEBUG", tag="规划")
        return None
    return render(scope, focus) or None


async def active_goal_briefs() -> List[Dict[str, str]]:
    """活跃目标简报（全局口径；规划工具 not_found 错误的自纠上下文）。"""
    try:
        snap = await ensure_snapshot()
    except Exception as exc:
        log(f"活跃目标简报构建失败: {exc}", "DEBUG", tag="规划")
        return []
    return [
        {
            "goal_id": g.goal_id,
            "title": sanitize_for_context(g.title, max_chars=_TITLE_CHARS),
            "progress": f"{g.done}/{g.total} 步",
        }
        for g in snap.goals[:8]
    ]


async def active_goal_lines() -> List[str]:
    """活跃目标摘要行（自主循环态势收集，全局口径）。"""
    try:
        snap = await ensure_snapshot()
    except Exception as exc:
        log(f"活跃目标摘要构建失败: {exc}", "DEBUG", tag="规划")
        return []
    return [f"{g.goal_id}: {sanitize_for_context(g.title, max_chars=_TITLE_CHARS)} ({g.done}/{g.total} 步)"
            for g in snap.goals[:10]]


async def stale_goal_line() -> str:
    """长期未更新目标的事实概况行（心跳日志消费，空串不注入）。

    只呈现事实不做处置：目标规划适应长期工作，存续判断权在 AI——
    停滞可能是被遗忘，也可能是无需日常推进的长期目标。
    """
    try:
        snap = await ensure_snapshot()
    except Exception as exc:
        log(f"目标停滞概况构建失败: {exc}", "DEBUG", tag="规划")
        return ""
    stale = sorted(
        (g for g in snap.goals if g.updated_days >= _STALE_REPORT_DAYS),
        key=lambda g: g.updated_days,
        reverse=True,
    )
    if not stale:
        return ""
    items = ", ".join(
        f"{g.goal_id}({g.updated_days:.0f}天)" for g in stale[:5]
    )
    more = f" 等 {len(stale)} 个" if len(stale) > 5 else ""
    return (
        f"[目标停滞] {len(stale)} 个活跃目标 ≥{_STALE_REPORT_DAYS:.0f} 天未更新: "
        f"{items}{more}——由你决策：仍在推进则 update_goal 刷新，"
        "已放弃则标记终态或 delete_goal（系统不自动清理）"
    )


# 规划态势注入（priority 30 会话操作态势档：仅目标 CRUD 时字节变化，
# 稳态渲染零 I/O；group=planning 随规划工具组启停联动）。
# 注册收敛为幂等函数：模块加载时调用，registry 被重置的测试环境可重挂。
def _register_provider() -> None:
    ContextProviderRegistry.register(ProviderMeta(
        name="plan_ops",
        priority=30,
        retention_priority=10,
        max_tokens=600,
        group="planning",
        inject_key="goals_inject_enabled",
        provide_fn=_provide,
        accepts_focus=True,
        description="规划态势：活跃目标/计划快照（goal_id + 步骤进度，每轮刷新）",
    ))


_register_provider()
