"""Script-first Minecraft lifestyle activity runner.

Model Experience: established world plans are executed by the Minecraft
adapter through high-level MCP actions; no lifestyle decision is sent to the
model. Token effect: removes repeated observation/planning turns from the
normal activity loop; only action results and terminal status are retained.
Cache effect: this path does not touch the model context cache; dynamic game
state stays in MCP results and the persisted world plan.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from core.log import log

from .world_plan import WorldPlan

ToolCall = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
Activity = Literal["survival", "wood", "equipment", "shelter", "mining", "exploration"]

FOCUSES: tuple[Activity, ...] = ("survival", "wood", "equipment", "shelter", "mining", "exploration")


def focus_index(activity_index: int, home_complete: bool) -> int:
    """六焦点轮换取位：shelter 在家已完工时跳过；lifestyle 与脚本生活共用同一份。"""
    index = activity_index % len(FOCUSES)
    if FOCUSES[index] == "shelter" and home_complete:
        index = (index + 1) % len(FOCUSES)
    return index

_LOG_BLOCKS = (
    "oak_log",
    "birch_log",
    "spruce_log",
    "jungle_log",
    "acacia_log",
    "dark_oak_log",
    "cherry_log",
    "mangrove_log",
    "pale_oak_log",
)
_ACTIVE_PHASES = {"running", "returning", "pausing", "paused", "stopping"}
_STATUS_TOOLS = {
    "gather_resources": "gathering_status",
    "prepare_item": "production_status",
    "mine_resources": "mining_status",
}


@dataclass(frozen=True)
class ScriptedActivityResult:
    """Outcome of one deterministic activity pass."""

    handled: bool
    advance: bool = False
    reason: str = ""


class MinecraftScriptedLifestyle:
    """Run one bounded activity without asking an LLM to choose the action."""

    def __init__(self, call_tool: ToolCall, *, world: str) -> None:
        self._call_tool = call_tool
        self._world = world
        self._pending_tool: str | None = None

    async def run(self, plan: WorldPlan) -> ScriptedActivityResult:
        """Run or advance one activity and return whether the script handled it."""
        try:
            action = await self._call("action_status", {})
        except Exception as exc:
            return ScriptedActivityResult(False, reason=f"action_status unavailable: {exc}")

        if self._is_active(action):
            return ScriptedActivityResult(True, reason="an MCP action still owns the bot")

        if self._pending_tool is not None:
            pending_tool = self._pending_tool
            status_name = _STATUS_TOOLS.get(pending_tool)
            self._pending_tool = None
            if status_name is not None:
                try:
                    status = await self._call(status_name, {})
                    if self._is_active(status):
                        self._pending_tool = pending_tool
                        return ScriptedActivityResult(True, reason=f"{status_name} is still active")
                except Exception as exc:
                    log(f"Minecraft 脚本状态核对失败: {status_name}: {exc}", "DEBUG", tag="Minecraft")
            return ScriptedActivityResult(True, advance=True, reason=f"{pending_tool} reached a terminal state")

        focus = self._focus(plan)
        try:
            if focus == "survival":
                # Eating, danger rescue and safe idle pickup are owned by the
                # local MCP survival controller. Advance without a model turn.
                return ScriptedActivityResult(True, advance=True, reason="local survival controller owns this focus")
            if focus == "wood":
                return await self._wood(plan)
            if focus == "equipment":
                return await self._equipment()
            if focus == "shelter":
                return await self._shelter(plan)
            if focus == "mining":
                return await self._mining(plan)
            return await self._exploration(plan)
        except Exception as exc:
            log(f"Minecraft 脚本活动受阻: focus={focus} error={exc}", "DEBUG", tag="Minecraft")
            return ScriptedActivityResult(True, advance=True, reason=str(exc))

    @staticmethod
    def _focus(plan: WorldPlan) -> Activity:
        return FOCUSES[focus_index(plan.activity_index, plan.home_phase == "complete")]

    async def _wood(self, plan: WorldPlan) -> ScriptedActivityResult:
        point = await self._position()
        found = await self._call("find_blocks", {
            "matching": list(_LOG_BLOCKS), "maxDistance": 24, "count": 12, "point": point,
        })
        positions = found.get("positions", [])
        if not isinstance(positions, list) or not positions:
            return ScriptedActivityResult(True, advance=True, reason="no nearby log was found")
        for candidate in positions[:8]:
            if not isinstance(candidate, dict):
                continue
            block = await self._call("get_block_at", {
                "x": int(candidate["x"]), "y": int(candidate["y"]), "z": int(candidate["z"]),
            })
            name = block.get("name")
            if name not in _LOG_BLOCKS:
                continue
            await self._goto(candidate)
            result = await self._call("gather_resources", {
                "x": int(candidate["x"]), "y": int(candidate["y"]), "z": int(candidate["z"]),
                "block": name, "count": 32, "radius": 4, "mode": "tree",
            })
            if bool(result.get("active")):
                self._pending_tool = "gather_resources"
                return ScriptedActivityResult(True, reason="tree gathering admitted")
            return ScriptedActivityResult(True, advance=True, reason="tree gathering returned synchronously")
        return ScriptedActivityResult(True, advance=True, reason="nearby log positions were stale")

    async def _equipment(self) -> ScriptedActivityResult:
        inventory = await self._call("get_inventory", {"format": "detailed"})
        items = inventory.get("items", [])
        names = {str(item.get("name")) for item in items if isinstance(item, dict)}
        for item in ("stone_pickaxe", "stone_axe", "crafting_table"):
            if item in names:
                continue
            result = await self._call("prepare_item", {"item": item, "count": 1, "mode": "ensure"})
            if bool(result.get("active")):
                self._pending_tool = "prepare_item"
                return ScriptedActivityResult(True, reason=f"preparing {item}")
            return ScriptedActivityResult(True, advance=True, reason=f"prepared {item}")
        return ScriptedActivityResult(True, advance=True, reason="equipment baseline is present")

    async def _shelter(self, plan: WorldPlan) -> ScriptedActivityResult:
        home = plan.home.model_dump()
        await self._goto(home)
        result = await self._call("build_starter_home", {"home": home, "maxBlocks": 16})
        next_phase = result.get("nextPhase")
        if isinstance(next_phase, str) and next_phase in {"foundation", "walls", "roof", "interior", "complete"}:
            plan.home_phase = next_phase  # type: ignore[assignment]
        return ScriptedActivityResult(True, advance=True, reason="starter home batch completed")

    async def _mining(self, plan: WorldPlan) -> ScriptedActivityResult:
        if plan.mine_entrance is None or plan.mine_direction is None:
            return ScriptedActivityResult(True, advance=True, reason="mine entrance is not planned")
        entrance = plan.mine_entrance.model_dump()
        await self._goto(entrance)
        result = await self._call("mine_resources", {
            "direction": plan.mine_direction,
            "depth": 8,
            "length": 8,
            "item": "cobblestone",
            "count": 16,
            "extend": True,
        })
        if bool(result.get("active")):
            self._pending_tool = "mine_resources"
            return ScriptedActivityResult(True, reason="mining task admitted")
        return ScriptedActivityResult(True, advance=True, reason="mining returned synchronously")

    async def _exploration(self, plan: WorldPlan) -> ScriptedActivityResult:
        # Keep exploration local and bounded. It is a movement activity, not
        # a reason to ask the model to scan an unbounded world.
        offset = ((plan.activity_index % 3) - 1) * 8
        point = {"x": plan.home.x + 8, "y": plan.home.y, "z": plan.home.z + offset}
        await self._goto(point)
        return ScriptedActivityResult(True, advance=True, reason="bounded exploration point reached")

    async def _position(self) -> dict[str, int]:
        observation = await self._call("get_observation", {
            "includeEntities": False, "includeEvents": False, "format": "concise",
        })
        position = observation.get("self", {}).get("position", {})
        return {"x": int(float(position["x"])), "y": int(float(position["y"])), "z": int(float(position["z"]))}

    async def _goto(self, point: dict[str, Any]) -> None:
        await self._call("goto", {
            "goalType": "near", "x": int(point["x"]), "y": int(point["y"]), "z": int(point["z"]),
            "range": 2, "timeout": 30000,
        })

    async def _call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        result = await self._call_tool(name, args)
        if not isinstance(result, dict):
            raise TypeError(f"MCP {name} returned a non-object result")
        return result

    @staticmethod
    def _is_active(result: dict[str, Any]) -> bool:
        if bool(result.get("active")):
            return True
        phase = result.get("phase")
        return isinstance(phase, str) and phase in _ACTIVE_PHASES
