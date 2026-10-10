"""Minecraft 角色的有界自主生活循环。"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable

from agent.messages import build_entity_scope
from core.log import log
from core.tool_context import tool_request

from .world_plan import WorldPlan, WorldPlanStore


class MinecraftLifestyle:
    """在空闲时推进一小段 Minecraft 生活，并把规划持久化到世界维度。"""

    def __init__(
        self,
        *,
        server_id: Callable[[], str],
        mcp_server: Callable[[], str],
        world_signature: Callable[[], str] | None = None,
        enabled: Callable[[], bool],
        interval_seconds: Callable[[], float],
        idle_seconds: Callable[[], float],
        plan_store: WorldPlanStore | None = None,
    ) -> None:
        self._server_id = server_id
        self._mcp_server = mcp_server
        self._world_signature = world_signature or (lambda: "")
        self._enabled = enabled
        self._interval_seconds = interval_seconds
        self._idle_seconds = idle_seconds
        self._plan_store = plan_store or WorldPlanStore()
        self._last_player_activity = time.monotonic()
        self._next_run = 0.0
        self._running: asyncio.Task[None] | None = None
        self._paused = False

    def note_player_activity(self) -> None:
        """记录玩家输入，避免对话期间启动自主动作。"""
        self._last_player_activity = time.monotonic()

    def pause(self) -> None:
        """停止并抑制自主生活，直到玩家明确恢复。"""
        self._paused = True
        self.note_player_activity()
        if self._running is not None:
            self._running.cancel()

    def resume(self) -> None:
        """解除手动停止留下的自主生活抑制。"""
        self._paused = False
        self.note_player_activity()
        self._next_run = time.monotonic() + self._idle_seconds()

    async def stop(self) -> None:
        """关闭频道时取消当前反思并等待退出。"""
        task, self._running = self._running, None
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    def tick(self, connection_state: str | None) -> None:
        """按轮询节奏尝试启动一轮自主生活。"""
        now = time.monotonic()
        if (
            not self._enabled()
            or self._paused
            or connection_state != "online"
            or self._running is not None and not self._running.done()
            or now < self._next_run
            or now - self._last_player_activity < self._idle_seconds()
        ):
            return
        self._next_run = now + max(60.0, self._interval_seconds())
        self._running = asyncio.create_task(self._run(), name="channel.minecraft.lifestyle")
        self._running.add_done_callback(self._clear_task)

    def _clear_task(self, task: asyncio.Task[None]) -> None:
        if self._running is task:
            self._running = None
        if not task.cancelled() and task.exception() is not None:
            log(f"Minecraft 自主生活轮次异常: {task.exception()}", "WARNING", tag="Minecraft")

    async def _run(self) -> None:
        from agent.runtime.singleton import get_runtime

        runtime = get_runtime()
        if runtime is None:
            return
        world = self._server_id()
        scope = build_entity_scope("group", "minecraft", world)
        plan = self._plan_store.load(world)
        signature = self._world_signature()
        if plan is not None and signature and plan.world_signature != signature:
            log(f"Minecraft 世界规划指纹不匹配，放弃旧规划: world={world}", "INFO", tag="Minecraft")
            plan = None
        next_activity_index: int | None = None
        if plan is None:
            focus = "base_planning"
        elif plan.mine_entrance is None or plan.mine_direction is None:
            focus = "mine_planning"
        else:
            focuses = ("survival", "wood", "equipment", "shelter", "mining", "exploration")
            index = plan.activity_index % len(focuses)
            if focuses[index] == "shelter" and plan.home_phase == "complete":
                index += 1
            focus = focuses[index]
            next_activity_index = index + 1
        plan_snapshot = plan.prompt_json() if plan is not None else "{}"
        prompt = (
            "这是一轮 Minecraft 自主生活；只在在线、存活、健康、食物充足、背包有空间且没有其他动作时推进。"
            "先读取连接、生存、当前状态和背包。只用当前 MCP 服务的实际注册工具名，内部结果不要发聊天。"
            "每轮只推进一个活动，工具受阻就保留进度并结束，不换地址、不扩大授权、不重做旧任务。\n"
            "base_planning：调用 find_build_site，只从成功结果保存 home；本轮不改动世界。"
            "mine_planning：在 home 附近 6~12 格选固定矿口和方向，验证往返路线，本轮不挖掘。"
            "mining：到固定 mine_entrance 后沿 mine_direction 继续已有矿道，禁止因延伸失败另开矿道。"
            "wood：近处查找明确树种，验证路线后到达再用 gather_resources(mode=tree,count=32)；"
            "count 是整树原木上限，超限不得扩大。无路可达时只探索一个已核验的近处落脚点或结束。"
            "equipment：查库存后用 prepare_item(mode=ensure,count=1) 补首个缺项：工作台、木镐、木斧、"
            "石镐、火把、石斧、盾牌。采集补料必须指定实际观察且获准的区域和 maxCount。"
            "后台任务必须等终态再继续，不能把受理当完成。\n"
            "shelter：仅用已有 WORLD_PLAN.home；先查库存，用 prepare_item 准备一批相同木板，"
            "再调用 build_starter_home(home=WORLD_PLAN.home,maxBlocks=16)。此工具按实际方块续建地基、墙和屋顶，"
            "不会另选家址、自动伐木或扩大任务；缺材料时下一轮继续准备，不调用逐块工具重复施工。"
            "home_phase=interior 时仅在已完成屋内按实际缺项安排床、箱子、工作台和熔炉；"
            "home_phase=complete 不再建造。其他活动优先观察生存状况或短距离探索。\n"
            "规划坐标的移动用 goto(goalType=near,range=2)，到达后确认位置；不得破坏玩家建筑或访问猜测的箱子。"
            "家址、矿口和方向保持稳定。只在执行器验证完整阶段后推进 home_phase，"
            "并在 WORLD_PLAN 前输出独立行 TEMPLATE_VERIFIED=true；局部批次完成仍保留未完成阶段。"
            "规划更新仅通过最后一行 WORLD_PLAN=严格 JSON 提交，保留 home、mine_entrance、mine_direction、"
            "terrain、home_template、home_phase；home_phase 为 foundation/walls/roof/interior/complete。"
            "没有新的核验事实则不提交规划，最后 end_reply。\n"
            f"本轮偏好活动是 {focus}，当前 WORLD_PLAN={plan_snapshot}。"
        )
        try:
            with tool_request(scope, "@reflex"):
                result = await runtime.mind.reflect(
                    [{"role": "user", "content": f"[Minecraft 自主生活轮次]\n世界: {world}\n{prompt}"}],
                    adapter_key="minecraft",
                    tool_tags=[f"mcp:{self._mcp_server()}"],
                    allow_output_tools=False,
                    max_iterations=12,
                    require_output=True,
                )
            result_text = result or ""
            self._save_plan_marker(world, result_text)
            if next_activity_index is not None:
                current = self._plan_store.load(world)
                if current is not None:
                    current.activity_index = next_activity_index
                    self._plan_store.save(current)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log(f"Minecraft 自主生活轮次失败: {exc}", "WARNING", tag="Minecraft")

    def _save_plan_marker(self, world: str, result: str) -> None:
        """提取模型提交的规划标记，只有完整校验通过才落盘。"""
        match = re.search(r"(?m)^\s*WORLD_PLAN\s*=\s*(\{.*\})\s*$", result or "")
        if match is None:
            return
        try:
            marker = json.loads(match.group(1))
            if not isinstance(marker, dict):
                return
            current = self._plan_store.load(world)
            signature = self._world_signature()
            if current is not None and signature and current.world_signature != signature:
                current = None
            verified = re.search(r"(?im)^\s*TEMPLATE_VERIFIED\s*=\s*true\s*$", result or "") is not None
            if current is not None:
                if marker.get("home", current.home.model_dump()) != current.home.model_dump():
                    raise ValueError("已有世界规划不能在自主轮次中更换家址")
                if current.mine_entrance is not None:
                    marker["mine_entrance"] = current.mine_entrance.model_dump()
                    marker["mine_direction"] = current.mine_direction
                marker.setdefault("home_phase", current.home_phase)
                marker["activity_index"] = current.activity_index
                marker.setdefault("world_signature", current.world_signature)
                if marker.get("home_phase") != current.home_phase and not verified:
                    marker["home_phase"] = current.home_phase
            elif marker.get("home_phase") not in (None, "foundation") and not verified:
                marker["home_phase"] = "foundation"
            if signature:
                marker["world_signature"] = signature
            plan = WorldPlan.from_marker(world, marker)
            self._plan_store.save(plan)
            mine = plan.mine_entrance.model_dump() if plan.mine_entrance else None
            log(f"Minecraft 世界规划已保存: world={world} home={plan.home.model_dump()} mine={mine}", "INFO", tag="Minecraft")
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            log(f"Minecraft 世界规划标记无效: {exc}", "WARNING", tag="Minecraft")
