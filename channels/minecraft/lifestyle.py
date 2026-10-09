"""Minecraft 角色的有界自主生活循环。"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from agent.messages import build_entity_scope
from core.log import log
from core.tool_context import tool_request

from .world_plan import WorldPlan, WorldPlanStore

MCPCall = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
Announce = Callable[[str], Awaitable[None]]


class MinecraftLifestyle:
    """在空闲时推进一小段 Minecraft 生活，并把规划持久化到世界维度。"""

    def __init__(
        self,
        call: MCPCall,
        announce: Announce,
        *,
        server_id: Callable[[], str],
        world_signature: Callable[[], str] | None = None,
        enabled: Callable[[], bool],
        interval_seconds: Callable[[], float],
        idle_seconds: Callable[[], float],
        plan_store: WorldPlanStore | None = None,
    ) -> None:
        self._call = call
        self._announce = announce
        self._server_id = server_id
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
        if plan is not None and signature and plan.world_signature and plan.world_signature != signature:
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
            focus = focuses[index]
            next_activity_index = index + 1
        plan_snapshot = plan.prompt_json() if plan is not None else "{}"
        prompt = (
            "你是 Minecraft 世界里有自己生活节奏的角色。这是一轮没有玩家新指令的短暂自主生活，不是聊天回复。"
            "先依次读取 get_connection_status、get_survival_status、get_state、get_inventory；只要离线、死亡、受伤、正在执行动作、食物或背包空间不足，立即调用 end_reply。"
            "确认安全后，每轮只能推进一个有界的小活动，并在 90 秒内收尾。内部进度只写入模型历史，不要调用 send_message。"
            "如果 focus=base_planning，严格先观察不挖掘、不放置：用 get_blocks_in_region 扫描小块候选平地，优先草方块/泥土、至少 6x6 平整、没有水/岩浆/沙漠或悬崖，24 格内有树木和石头，并用 get_path_to 验证可达。"
            "找到合适的平原后规划 starter_cabin_v1：home 是房屋西北角地面锚点；Minecraft 坐标中 z 向南递增；地基覆盖 x..x+6、z..z+6，墙高 4，南墙固定在 z+6 并在 x+3 留 1x2 门，屋内西北放床、东北放箱子、东南放工作台和熔炉；先地基、墙、屋顶、室内，每轮最多 16 块。规划完成前禁止建造和挖矿。"
            "如果 focus=mine_planning，只能在已选 home 附近规划一个距离 6~12 格的固定矿洞入口，避开房屋和耕地，用 get_path_to 验证往返；不要直接挖。"
            "如果已有规划，绝不在随机脚下开矿：先 goto 到 plan.mine_entrance，再严格使用 plan.mine_direction；mine_resources 只允许固定入口和固定方向，优先 extend=true 继续同一条走廊，禁止换点、换方向或为了走路挖洞。"
            "采木必须用 gather_resources 完整收尾；建造只能使用 plan.home 锚点和 starter_cabin_v1 模板；不得破坏玩家建筑、访问猜测的箱子或丢弃物品。"
            "Wood focus has a strict reachable-target route: first search for logs within 8 blocks of the current position; if none are found, move to the planned home or another verified surface point and search again instead of selecting a far cliff tree. For any candidate beyond 8 blocks, first use get_path_to to verify a safe route, then use goto with goalType=near and range=2; only after arriving may you call gather_resources with a center within 8 blocks. If get_path_to or goto returns NO_PATH, discard that candidate and choose another result once; never pass a remote tree coordinate directly to gather_resources or retry the same rejected remote center."
            "Execution must use only registered tool names: movement is goto; never call move_to_position or invent a tool name.\n"
            "For planned home or mine coordinates, do not use an exact block goal when the anchor may be occupied: call goto with goalType=near and range=2, then verify the actual position before the next action.\n"
            "For a non-planning focus, after safety checks execute one concrete safe action before ending: prefer prepare_item, goto to the planned point, or one template place_block.\n"
            "Only end_reply without an action when the world is unsafe, disconnected, blocked, or the inventory has no usable space.\n"
            "If a planned mine extension returns NOT_FOUND or a supply guard rejects it, do not retry with extend=false and do not create another corridor; end the round and let the next focus handle supplies.\n"
            "Equipment focus is deterministic: inspect inventory first, then use prepare_item(mode=ensure,count=1) for the first missing item in this order: crafting_table, wooden_pickaxe, wooden_axe, stone_pickaxe, torch, stone_axe, shield. This keeps mining supplies ahead of optional tools when stone is temporarily short. Wait for production_progress or production_status before another preparation.\n"
            "Shelter focus follows starter_cabin_v1 in fixed phases from WORLD_PLAN.home_phase. Before creating a new plan, call find_build_site(searchRadius=24, footprint=7, maxFillDepth=2); it is read-only and returns the only valid home anchor. Never invent home coordinates, never build over a mine entrance, and never use a point whose result is NO_SAFE_BUILD_SITE. The returned home.y is the future floor level. Every shelter focus, including walls, roof, and interior, must first call prepare_build_site for the current plan.home and require verified=true; if the old home is rejected, place nothing, find a new site, prepare it, and reset home_phase to foundation. Before the first foundation block, call prepare_build_site(home=returned home, footprint=7, maxFillDepth=2); this deterministically removes only safe natural blocks above the support plane and fills shallow low columns from carried dirt/cobblestone/stone. If it reports BUILD_SITE_MISSING_FILL or BUILD_SITE_BLOCKED, stop construction and handle supplies or choose a new site.\n"
            "For structural phases foundation, walls, and roof, never build by issuing individual place_block calls. First ensure the full phase material with prepare_item(item=oak_planks, mode=ensure): foundation needs 49, walls 70, roof 49. If planks are missing, find a nearby authorized oak_log area, move within 8 blocks, pass gather={block=oak_log,...}, and wait for production_status to reach completed; do not start construction while preparation is active or blocked. Then call build_starter_cabin(home=plan.home, phase=home_phase). The template executor preflights the whole phase, verifies every placement, and never accepts dirt as structural material.\n"
            "Treat home as the northwest floor corner: the 7x7 floor is x..x+6,z..z+6 at home.y, z increases toward south, the south wall is z+6, and its 1x2 door is x+3,z+6 at y+1..y+2. Foundation fills the floor, other walls are the perimeter at y+1..y+3, the roof is a full 7x7 at y+4, and interior places the bed northwest, chest northeast, crafting_table and furnace southeast. Query the relevant region, use build_starter_cabin for structural phases, verify it, and advance home_phase only after the phase is complete.\n"
            "place_block writes the adjacent target cell at reference plus faceVector; the reference is the existing support block, not the target. With faceVector y=1, a target at y=78 requires referenceY=77, a wall at y=79 requires referenceY=78, and a roof at y=82 requires referenceY=81. Never pass the target cell as reference or place a duplicate layer.\n"
            "When a phase has more missing blocks than one round allows, place a bounded batch in row-major order and persist the unchanged phase until verification shows completion. Never place at the anchor blindly or rebuild an existing block.\n"
            "After querying the complete phase region and verifying every expected cell, emit TEMPLATE_VERIFIED=true immediately before WORLD_PLAN; without that marker never advance home_phase.\n"
            'WORLD_PLAN.home_phase must be one of "foundation|walls|roof|interior|complete" and must only advance after block verification.\n'
            'WORLD_PLAN marker JSON must retain home_phase, activity_index and world_signature alongside home, mine_entrance, mine_direction, terrain, and home_template.\n'
            "Site planning gate: an empty WORLD_PLAN may be created only from a successful find_build_site result. The home coordinates in WORLD_PLAN must exactly equal that result; do not guess a coordinate from get_state or a mine location. Before foundation construction, prepare_build_site must return verified=true for the same home.\n"
            f"本轮偏好活动是 {focus}，当前 WORLD_PLAN={plan_snapshot}。"
            "规划变化只能通过最后一行机器标记提交，格式必须是一行严格 JSON："
            'WORLD_PLAN={"home":{"x":整数,"y":整数,"z":整数},"mine_entrance":{"x":整数,"y":整数,"z":整数},"mine_direction":"north|south|east|west","terrain":"plains_like","home_template":"starter_cabin_v1"}。'
            "没有完成地形核验就不要输出标记。完成后只输出内部摘要并调用 end_reply，禁止 send_message。"
        )
        try:
            with tool_request(scope, "@reflex"):
                result = await runtime.mind.reflect(
                    [{"role": "user", "content": f"[Minecraft 自主生活轮次]\n世界: {world}\n{prompt}"}],
                    adapter_key="minecraft",
                    tool_tags=["mcp:minecraft"],
                    allow_output_tools=False,
                    max_iterations=12,
                    require_output=True,
                )
            result_text = result or ""
            if "MINING_LOW_SUPPLIES" in result_text:
                next_activity_index = 2
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
            verified = re.search(r"(?im)^\s*TEMPLATE_VERIFIED\s*=\s*true\s*$", result or "") is not None
            if current is not None:
                marker.setdefault("home_phase", current.home_phase)
                marker["activity_index"] = current.activity_index
                marker.setdefault("world_signature", current.world_signature)
                if marker.get("home_phase") != current.home_phase and not verified:
                    marker["home_phase"] = current.home_phase
            elif marker.get("home_phase") not in (None, "foundation") and not verified:
                marker["home_phase"] = "foundation"
            signature = self._world_signature()
            if signature:
                marker["world_signature"] = signature
            plan = WorldPlan.from_marker(world, marker)
            self._plan_store.save(plan)
            mine = plan.mine_entrance.model_dump() if plan.mine_entrance else None
            log(f"Minecraft 世界规划已保存: world={world} home={plan.home.model_dump()} mine={mine}", "INFO", tag="Minecraft")
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            log(f"Minecraft 世界规划标记无效: {exc}", "WARNING", tag="Minecraft")
