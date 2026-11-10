"""常驻自主作业循环：进服即开工，零模型驱动，按组汇报并接受继续/停止裁决。

架构定位：与 MinecraftLifestyle（模型反思驱动的自主生活）相对，本循环把控制权
交给确定性脚本——在线即开始采集作业，模型不指令也持续干活；每组终态向模型
汇报一次事实，模型只输出 WORK_VERDICT 裁决（continue / stop），不编排动作。
汇报（历史写入 + 裁决轮）整体是后台任务，作业循环从不等待汇报——完成组终态后
立即受理下一组（受阻组保持一个轮询周期的歇拍，防瞬时失败放大成 RPC 风暴），
执行器终态本身已含返程与库存收尾，无需任何额外间歇。
开启 worker_judgment_gate 时，汇报先经 Jev 门卫预判（judgment_gate.py），
例行进展高置信跳过裁决轮，异常或值得播报的才进入模型。
玩家聊天不打断作业（大模型思考期间脚本不停）；只有 !stop / !pause、模型
stop 裁决、连续受阻保护或频道关停会暂停它。

Model Experience:
① 模型看到什么：每组终态一条 push 历史事实（数十 token）；另有一个无工具
   reflect 小轮次接收汇报并输出裁决，裁决说明经 announce 播报到游戏聊天；
   裁决走快车道（召回策略钉 off 档 + 整轮墙钟限时），不触发召回与后台协作阶段。
② token 影响：每组一次 2 迭代小轮次（数百 token），替代按分钟轮询的
   多迭代大反思，作业期间模型零调用；汇报与裁决只进动态尾部。
③ 缓存影响：不触碰 stable/summary 前缀；历史追加与裁决轮均为动态区，不破缓存。

自动启动进程：原木合计达到 8 个后准备工作台与木镐，采 3 个圆石升级石镐，补齐三组材料。
模型只看到已确认的动态作业事实，制作与阶段切换不增加模型轮次，不触碰缓存前缀。
"""

from __future__ import annotations

import asyncio
import math
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from agent.messages import build_entity_scope
from core.log import log
from core.tool_context import tool_request

from .judgment_gate import verdict_needed
from .protocol import GATHER_ITEM_LABELS
from .worker_crafting import prepare_pickaxe
from .worker_mining import stone_corridor_order

ToolCall = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]

# 作业节奏：执行器 gather 单任务自带约 120s 时间预算，监视上限只作兜底。
_WATCH_TIMEOUT_SECONDS = 300.0
_FOREIGN_ACTION_TIMEOUT_SECONDS = 600.0
_WORK_RADIUS = 8
# 附近无作业目标时的换区策略：朝轮换方向前进一段距离再开工，避免原地死循环裁决。
_NO_TARGET_MARKER = "GATHER_NO_TARGET"
# 服务器强制重同步引发的格内位置拉锯（客户端世界数据分歧）：换区即可绕开，
# 与 NO_TARGET 共用「受阻 → 裁决 → 自动 relocate」通道。
_POSITION_FIGHT_MARKER = "GATHER_POSITION_FIGHT"
# 支架路线无法确认（作业面不可达、无法立足延伸）：换区即可绕开，走 relocate 通道。
_NO_ROUTE_MARKER = "GATHER_NO_ROUTE"
# 矿口无稳定整块地板：脚下地形（自挖口/雪盖）不可开工，原地重试无意义，
# 立即换区找新地面。
_UNSTABLE_FLOOR_MARKER = "No stable full-block floor"
# 返程通道被挡：通道上方常有正在枯萎的树不停掉叶（自己的挖掘切断了树干），
# 清不胜清，该井口直接弃置换区。
_RETURN_BLOCKED_MARKER = "Return passage obstructed"
# 安家三要素评估：木头与泥土在走廊半径内可见即合格（石头地下随处可挖，
# 盲走廊总能到达）；沙漠/海滩/裸冰原出生点会不合格，需要先找家。
_HOME_LOG_MIN = 3
_HOME_SOIL_MIN = 8
_HOME_SEEK_LEGS = 8
_HOME_SEEK_DISTANCE = 64
# 海平面 63：迁徙必须走在海平面以上，低于就先垂直爬回地表——水平
# 挖掘赶路会让 bot 变成地鼠在地下钻。
_JOURNEY_MIN_Y = 64
# 安家群系白名单：核心平原族（平坦、动物多、草场）+ 优质扩展（樱花谷/
# 热带草原/森林族木头丰富且可平整）。其余群系（沙漠、恶地、雪原、丛林、
# 沼泽、黑森林、山地、下界与末地族等）一律不落脚——白名单制天然排除
# crimson/warped_forest 这类名字带 forest 的下界群系。
_HOME_BIOMES = frozenset({
    "plains", "sunflower_plains", "meadow",
    "cherry_grove", "savanna", "savanna_plateau",
    "forest", "flower_forest", "birch_forest", "old_growth_birch_forest",
})
# 高台判定：高出周围地表这么多格（恶地台地/悬崖顶）需要受控下撤。
_SPIRE_HEIGHT_THRESHOLD = 6
# 坑判定：bot 脚下低于周围地表（探测命中的泥土/原木高度）这么多格即在坑里。
# 绝对 y 是世界相关的，不能用于判断；只有与周围地表的相对差才是事实。
_PIT_DEPTH_THRESHOLD = 5
# 迁徙旅程：白天赶路狩猎，夜里睡床，直到合格的平原站点。
_JOURNEY_LEGS = 12
_JOURNEY_PREP_WOOD = 8
_HUNT_PREY = ("pig", "cow", "sheep", "chicken", "rabbit")
# 进食疗法的食物偏好：熟食优先，生肉与腐肉只在无更好选择时应急。
_FOOD_PREFERENCE = (
    "cooked_beef", "cooked_porkchop", "cooked_mutton", "cooked_chicken",
    "cooked_rabbit", "cooked_cod", "cooked_salmon", "bread", "baked_potato",
    "golden_carrot", "carrot", "apple", "melon_slice", "sweet_berries",
    "beetroot", "potato", "raw_beef", "raw_porkchop", "raw_chicken",
    "raw_cod", "raw_salmon", "rotten_flesh",
)
# 同因快速重复受阻的汇报抑制窗口：秒级失败循环只报首次，
# 防止汇报旁路被放大成裁决轮 LLM 调用风暴（曾打到供应商 Token Plan 限流）。
_RAPID_REBLOCK_SECONDS = 15.0
_RELOCATE_DISTANCE = 24
_RELOCATE_DIRECTIONS = ((1, 0), (0, 1), (-1, 0), (0, -1))
# 换区探测的目标族：与执行器 auto 订单的展开一致（泥土族 + 自然树干原木），
# 探测只决定"朝哪走"，授权与可挖性仍由 gather 订单在到达后裁定。
_RELOCATE_SOIL_BLOCKS = ("dirt", "grass_block", "podzol", "mycelium", "dirt_path")
_RELOCATE_LOG_BLOCKS = (
    "oak_log", "spruce_log", "birch_log", "jungle_log", "acacia_log",
    "dark_oak_log", "cherry_log", "mangrove_log", "pale_oak_log",
)
# 垫脚保底的 worker 侧镜像（仅用于探测排序）：权威值在执行器 tier1DirtFloor。
_DIRT_RESERVE_FLOOR = 10
_RELOCATE_PROBE_DISTANCE = 64
# 连续换区无效后的远程跳区距离：残破挖掘区里的探测命中会互相指向圈内，
# 近距换区永远跳不出去；连续两轮换区无产出就直接跳出 48 格。
_RELOCATE_LONG_HOP_DISTANCE = 48
_RELOCATE_FAIL_ESCALATION = 2
# 深埋/高山目标走向了也挖不到：泥土候选的高差对齐挖掘带（±8，走到水平
# 位置不改变高度），原木允许更大落差（山坡可寻路）。
_RELOCATE_SOIL_MAX_DY = 8
_RELOCATE_LOG_MAX_DY = 24
# 探测与四个方向全部失败后的冷却：避免固定节奏的寻路风暴（原地打转观感）。
_RELOCATE_FAIL_COOLDOWN_SECONDS = 60.0
# 冷却播报节流：同一困境反复进入冷却时最多每 5 分钟告知玩家一次。
_COOLDOWN_ANNOUNCE_INTERVAL = 300.0
# 同因连续受阻升级换区的阈值：个别原因（如脚下不是完整实心方块的 UNSAFE_ENTRY）
# 不在换区名单里，但原地连败说明站位本身不利，主动搬家比原地重试强。
_REPEATED_BLOCK_RELOCATE_AFTER = 5
# 泥土保有量封顶一组；原木达到启动量后制作木镐，先备齐一批圆石。
_WORK_GOAL_DIRT = 64
_CRAFT_LOG_TARGET = 8
_WORK_GOAL_WOOD = 64
_WORK_GOAL_STONE = 64
_STONE_UPGRADE_COST = 3
_STRONG_PICKAXES = frozenset({"stone_pickaxe", "iron_pickaxe", "diamond_pickaxe", "netherite_pickaxe"})
_ACTIVE_PHASES = frozenset({"running", "returning", "pausing", "paused", "stopping"})
_BLOCKED_PHASES = frozenset({"blocked", "interrupted"})


def _needs_relocate(reason: str) -> bool:
    """受阻原因是否指向「换区即可绕开」的环境性阻塞
    （无目标 / 位置拉锯 / 支架路线不可达）。"""
    return (_NO_TARGET_MARKER in reason or _POSITION_FIGHT_MARKER in reason
            or _NO_ROUTE_MARKER in reason or _UNSTABLE_FLOOR_MARKER in reason
            or _RETURN_BLOCKED_MARKER in reason)

# 裁决快车道墙钟上限：供应商回退链整体卡死时兜底；健康链路数秒内返回，
# 超时只发生在异常网络下，等价 continue（裁决是旁路，无回应不阻塞作业）。
_VERDICT_TIMEOUT_SECONDS = 120.0

_VERDICT_PROMPT = (
    "你是 Minecraft 陪玩机器人的自主作业监督者。作业脚本不依赖模型即可持续干活，"
    "每完成一组工作会向你汇报一次事实。请只做两件事：\n"
    "1. 第一行输出裁决标记 WORK_VERDICT=continue（继续下一组）或 WORK_VERDICT=stop（暂停作业）；\n"
    "2. 随后用一句话（不超过 40 字）向玩家说明进展，这句话会播报到游戏聊天。\n"
    "没有新的危险或玩家叫停事实时默认 continue；只有玩家明确要求停止、连续受阻或出现危险事实时才输出 stop。"
)

_VERDICT_PATTERN = re.compile(r"WORK_VERDICT\s*=\s*(continue|stop)", re.IGNORECASE)


@dataclass(frozen=True)
class StackOutcome:
    """一组作业的终态事实，数值只认执行器确认的入包与挖掘数。"""

    phase: str
    reason: str
    dug: int
    gained: int
    requested: int
    items: tuple[tuple[str, int], ...] = ()
    entry: tuple[int, int, int] | None = None

    @classmethod
    def from_snapshot(cls, snapshot: dict[str, Any]) -> "StackOutcome":
        phase = snapshot.get("phase")
        if not isinstance(phase, str) or not phase:
            phase = "running" if bool(snapshot.get("active")) else "unknown"
        raw_entry = snapshot.get("entry")
        entry = ((int(raw_entry["x"]), int(raw_entry["y"]), int(raw_entry["z"]))
                 if isinstance(raw_entry, dict) and all(k in raw_entry for k in ("x", "y", "z")) else None)
        raw_items = snapshot.get("items")
        items = tuple(
            (str(item["item"]), _as_int(item.get("gained")))
            for item in raw_items
            if isinstance(item, dict) and item.get("item")
        ) if isinstance(raw_items, list) else ()
        return cls(
            phase=phase,
            reason=str(snapshot.get("reason") or ""),
            dug=_as_int(snapshot.get("dug")),
            gained=_as_int(snapshot.get("gained")),
            requested=_as_int(snapshot.get("requested", snapshot.get("count"))),
            items=items or (((str(snapshot["item"]), _as_int(snapshot.get("gained"))),)
                            if snapshot.get("item") and _as_int(snapshot.get("gained")) else ()),
            entry=entry,
        )


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


class MinecraftWorker:
    """确定性自主作业：在线开工、按组汇报、接受裁决，与玩家对话并行。"""

    def __init__(
        self,
        call_tool: ToolCall,
        *,
        server_id: Callable[[], str],
        online: Callable[[], bool],
        enabled: Callable[[], bool],
        item: Callable[[], str],
        stack_size: Callable[[], int],
        poll_seconds: Callable[[], float],
        record: Callable[[str], Awaitable[None]],
        announce: Callable[[str], Awaitable[None]],
        judgment_gate: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._call_tool = call_tool
        self._server_id = server_id
        self._online = online
        self._enabled = enabled
        self._item = item
        self._stack_size = stack_size
        self._poll_seconds = poll_seconds
        self._record = record
        self._announce = announce
        self._judgment_gate = judgment_gate or (lambda: True)
        self._clock = clock
        self._session: asyncio.Task[None] | None = None
        self._reports: set[asyncio.Task[None]] = set()
        self._paused = False
        self._hold_reason: str | None = None
        self._stuck_entry: tuple[int, int, int] | None = None
        self._stuck_streak = 0
        self._relocate_fails = 0
        self._paused_action = False
        self._stacks_done = 0
        self._total_gained = 0
        self._totals: dict[str, int] = {}
        self._inv_dirt = 0
        self._inv_wood = 0
        self._inv_stone = 0
        self._inventory: dict[str, int] = {}
        self._pickaxe_durability: dict[str, int | None] = {}
        self._last_block_reason = ""
        self._repeated_blocks = 0
        self._relocate_pending = False
        self._relocate_heading = 0
        self._relocate_cooldown_until = 0.0
        self._cooldown_announced_at: float | None = None
        # 同因复发记账：第 2 次同因升级诊断（播报根因与疗法状态），
        # 不再让轮换掩盖重复出现的同类问题。
        self._cause_counts: dict[str, int] = {}
        self._cause_remedies: dict[str, str] = {}
        # 安家门：本作业会话是否已确认站点合格（或被玩家明确豁免）。
        self._home_ready = False
        self._last_relocate_target: tuple[int, int] | None = None
        self._last_block_at = 0.0
        self._suppress_report = False

    @property
    def holding(self) -> bool:
        """作业因模型明确 model_stop 而暂停开工；受阻走换区自治，不进入持有。"""
        return self._hold_reason is not None

    def world_changed(self) -> None:
        """进服到新世界：配额、持有、安家门与全部会话账本随之作废。"""
        was_session = self._session is not None and not self._session.done()
        self.pause()
        self._hold_reason = None
        self._paused_action = False
        self._home_ready = False
        self._stacks_done = 0
        self._total_gained = 0
        self._totals.clear()
        self._inv_dirt = self._inv_wood = self._inv_stone = 0
        self._inventory.clear()
        self._cause_counts.clear()
        self._cause_remedies.clear()
        self._stuck_entry = None
        self._stuck_streak = 0
        self._relocate_fails = 0
        self._repeated_blocks = 0
        self._last_block_reason = ""
        self._relocate_pending = False
        self._relocate_cooldown_until = 0.0
        self._paused = False
        log("Minecraft 自主作业检测到世界变更：全部配额与持有状态已重置", "INFO", tag="Minecraft")
        if was_session:
            self.tick("online")

    def tick(self, connection_state: str | None) -> None:
        """按轮询节奏维持常驻作业会话；玩家活动不参与任何门槛。"""
        task = self._session
        if task is not None and not task.done():
            return
        if connection_state != "online" or not self._enabled() or self._paused:
            return
        self._session = asyncio.create_task(self._session_loop(), name="channel.minecraft.worker")
        self._session.add_done_callback(self._clear_session)

    def pause(self) -> None:
        """!stop / !pause：停止作业循环；已在执行的执行器动作由停止指令自己处理。"""
        self._paused = True
        task, self._session = self._session, None
        if task is not None:
            task.cancel()

    def resume(self) -> None:
        """解除手动暂停与所有持有状态；执行器侧恢复由 !resume 的 resume_action 负责。"""
        self._paused = False
        if self._hold_reason is not None and _needs_relocate(self._last_block_reason):
            self._relocate_pending = True
        self._hold_reason = None
        self._paused_action = False

    async def stop(self) -> None:
        """频道关停：取消作业会话与未完成的汇报任务。"""
        self._paused = True
        tasks = [task for task in (self._session, *self._reports) if task is not None and not task.done()]
        self._session = None
        self._reports.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _clear_session(self, task: asyncio.Task[None]) -> None:
        if self._session is task:
            self._session = None
        if not task.cancelled() and task.exception() is not None:
            log(f"Minecraft 自主作业循环异常退出: {task.exception()}", "WARNING", tag="Minecraft")

    async def _session_loop(self) -> None:
        if self._hold_reason == "no_home":
            # 求助持有：等玩家 !resume（带到新地点）再重新评估，绝不每秒重跑安家门。
            return
        if not self._home_ready:
            if not await self._ensure_home():
                self._hold_reason = "no_home"
                self._spawn_announce("没找到能安家的地方，我在这儿等；带我走两步再喊 !resume 我就重新找。")
                return
            self._home_ready = True
        while self._online() and not self._paused:
            if self._hold_reason is not None:
                # 持有只剩模型明确 model_stop（或用户 !pause 的 paused），受阻从不进入该分支。
                await asyncio.sleep(self._poll_seconds())
                continue
            if self._relocate_pending:
                if self._clock() < self._relocate_cooldown_until:
                    # 探测与方向全部失败后的冷却：不再按固定节奏重试寻路。
                    await asyncio.sleep(self._poll_seconds())
                    continue
                self._relocate_pending = False
                await self._relocate()
            try:
                outcome = await self._work_one_stack()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log(f"Minecraft 自主作业单组失败: {exc}", "DEBUG", tag="Minecraft")
                await asyncio.sleep(max(self._poll_seconds(), 2.0))
                continue
            if outcome is None or outcome.phase == "cancelled":
                # 他方动作占着 bot，或裁决/暂停落在挖掘中途：本轮无新事实。
                await asyncio.sleep(self._poll_seconds())
                continue
            if outcome.gained > 0:
                # 组号只计有产出的组：空手受阻组不占号，「第 N 组」与实际收获对齐。
                self._stacks_done += 1
                self._relocate_fails = 0
            self._total_gained += outcome.gained
            for item, gained in outcome.items:
                self._totals[item] = self._totals.get(item, 0) + gained
            await self._apply_block_outcome(outcome)
            self._track_stuck_spot(outcome)
            await self._refresh_inventory_goals()
            self._hold_if_goal_met()
            if outcome.phase != "completed":
                # 瞬时受阻（如新连接区块未加载的 UNSAFE_ENTRY）若不歇拍会形成
                # 每秒数十次的 RPC 风暴，反而饿死区块加载；完成组仍立即受理下一组。
                await asyncio.sleep(self._poll_seconds())
            # 汇报整体是后台任务：作业循环不等待历史写入与裁决轮，
            # 下一组立即受理——执行器终态已含返程与库存收尾。
            # 同因快速重复受阻视为瞬时失败风暴：抑制重复汇报，只换区续跑。
            if self._suppress_report:
                log("Minecraft 自主作业同因重复受阻，抑制本轮汇报", "DEBUG", tag="Minecraft")
            else:
                self._spawn_report(self._report_line(outcome))

    async def _work_one_stack(self) -> StackOutcome | None:
        """开工一组：等 bot 空闲 → 以当前位置为作业面下达采集 → 监视到终态。"""
        if not await self._wait_until_free():
            return None
        if not await self._refresh_inventory_goals():
            return None
        if self._item() == "auto" and self._hold_if_goal_met():
            return None
        if (self._item() == "auto" and not self._has_pickaxe() and self._inv_wood >= _CRAFT_LOG_TARGET
                and self._inv_stone < _STONE_UPGRADE_COST):
            try:
                await prepare_pickaxe(
                    self._call, running=lambda: self._online() and not self._paused and self._hold_reason is None,
                    poll_seconds=self._poll_seconds(), clock=self._clock,
                    mode="craft" if self._inventory.get("wooden_pickaxe", 0) else "ensure",
                )
                if not await self._refresh_inventory_goals() or not self._has_pickaxe():
                    raise RuntimeError("CRAFT_UNCONFIRMED: 制作结束但背包未确认有镐")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                return StackOutcome(phase="blocked", reason=str(exc), dug=0, gained=0, requested=0)
            self._spawn_announce("工作台和木镐准备好啦，先挖 3 个圆石升级石镐，再备齐三组材料。")
        if (self._item() == "auto" and not self._has_strong_pickaxe() and self._inv_stone >= _STONE_UPGRADE_COST
                and (self._has_pickaxe() or self._inv_wood >= _CRAFT_LOG_TARGET)):
            try:
                await prepare_pickaxe(
                    self._call, running=lambda: self._online() and not self._paused and self._hold_reason is None,
                    poll_seconds=self._poll_seconds(), clock=self._clock, item="stone_pickaxe",
                    mode="craft" if self._inventory.get("stone_pickaxe", 0) else "ensure",
                )
                if not await self._refresh_inventory_goals() or not self._has_strong_pickaxe():
                    raise RuntimeError("CRAFT_UNCONFIRMED: 背包未确认有可用石镐")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                return StackOutcome(phase="blocked", reason=str(exc), dug=0, gained=0, requested=0)
        if not self._online() or self._paused or self._hold_reason is not None:
            return None
        stone_work = self._stone_work()
        stone_target = _WORK_GOAL_STONE if self._has_strong_pickaxe() else _STONE_UPGRADE_COST
        if stone_work:
            # 统一脑的石头流：既定矿道延续优先（确定性几何目标，与表面
            # 采集共用同一套安全与挖掘原语），其余交给引擎表面单——阻塞
            # 原木、通道堵塞都由执行器按同一规则原生处理，不再有桥。
            opening = await stone_corridor_order(
                self._call, await self._entry_point(), min(self._stack_size(), stone_target - self._inv_stone),
                self._relocate_heading, extend_only=True,
            )
            if opening is not None:
                await self._mine_call(opening)
                return await self._watch_stack("mining_status")
        entry = await self._entry_point()
        order: dict[str, Any] = {
            "block": "stone" if stone_work else self._item(),
            "count": min(self._stack_size(), stone_target - self._inv_stone) if stone_work else self._stack_size(),
            "x": entry["x"],
            "y": entry["y"],
            "z": entry["z"],
            "radius": _WORK_RADIUS,
            "mode": "count",
        }
        if order["block"] in {"auto", "dirt"}:
            # 区块对齐作业面：// 是 floor 语义，负坐标也落在正确区块。
            order["chunk"] = {"x": entry["x"] // 16, "z": entry["z"] // 16}
        if order["block"] == "auto":
            order["logTarget"] = _WORK_GOAL_WOOD if self._has_pickaxe() else _CRAFT_LOG_TARGET
            if self._dirt_goal_met():
                order["logsOnly"] = True
            elif self._inv_wood >= _WORK_GOAL_WOOD:
                order["block"] = "dirt"
                order["count"] = min(self._stack_size(), _WORK_GOAL_DIRT - self._inv_dirt)
                order.pop("logTarget")
        await self._call("gather_resources", order)
        outcome = await self._watch_stack()
        if stone_work and outcome.phase == "blocked" and _NO_TARGET_MARKER in outcome.reason:
            # 表面无石料（全被埋）：盲开浅走廊——石头总在几格之下，不依
            # 赖先看见石头才敢下挖；方向轮换，与换区共用偏航计数。
            if not await self._wait_until_free() or not await self._refresh_inventory_goals():
                return outcome
            missing = min(self._stack_size(), stone_target - self._inv_stone)
            if missing <= 0:
                return outcome
            opening = await stone_corridor_order(
                self._call, await self._entry_point(), missing, self._relocate_heading,
            )
            if opening is not None and not opening.get("extend"):
                self._relocate_heading += 1
                try:
                    await self._mine_call(opening)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    return StackOutcome(phase="blocked", reason=str(exc), dug=outcome.dug,
                                        gained=outcome.gained, requested=outcome.requested, items=outcome.items)
                mined = await self._watch_stack("mining_status")
                gains = dict(outcome.items)
                for item, gained in mined.items:
                    gains[item] = gains.get(item, 0) + gained
                return StackOutcome(phase=mined.phase, reason=mined.reason, dug=outcome.dug + mined.dug,
                                    gained=outcome.gained + mined.gained, requested=outcome.requested,
                                    items=tuple(gains.items()), entry=outcome.entry)
        return outcome

    def _dirt_goal_met(self) -> bool:
        return self._inv_dirt >= _WORK_GOAL_DIRT

    def _hold_if_goal_met(self) -> bool:
        """三类材料库存均达到一组后待命；重复检查不重复播报。"""
        if (self._item() != "auto" or not self._has_pickaxe() or not self._dirt_goal_met()
                or self._inv_wood < _WORK_GOAL_WOOD or self._inv_stone < _WORK_GOAL_STONE):
            return False
        if self._hold_reason != "goal_met":
            log(f"Minecraft 自主作业目标达成（泥土 {self._inv_dirt}、原木 {self._inv_wood}、圆石 {self._inv_stone}），收工待命",
                "INFO", tag="Minecraft")
            self._spawn_announce("一组泥土、一组原木和一组圆石都备齐啦，我在这儿歇着；用掉后喊 !resume 我再补。")
        self._hold_reason = "goal_met"
        self._relocate_pending = False
        return True

    def _has_pickaxe(self) -> bool:
        return any(count > 0 and name.endswith("_pickaxe") and self._usable_pickaxe(name)
                   for name, count in self._inventory.items())

    def _has_strong_pickaxe(self) -> bool:
        return any(count > 0 and name in _STRONG_PICKAXES and self._usable_pickaxe(name)
                   for name, count in self._inventory.items())

    def _usable_pickaxe(self, name: str) -> bool:
        remaining = self._pickaxe_durability.get(name)
        return remaining is None or remaining >= 12

    def _stone_work(self) -> bool:
        return self._item() == "auto" and self._has_pickaxe() and self._inv_stone < _WORK_GOAL_STONE

    async def _refresh_inventory_goals(self) -> bool:
        """按背包持有量刷新目标进度：重启不清零，玩家取走材料后 resume 补足。"""
        try:
            inventory = await asyncio.wait_for(self._call("get_inventory", {"format": "detailed"}), timeout=10)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log(f"Minecraft 自主作业库存查询失败: {exc}", "DEBUG", tag="Minecraft")
            return False
        counts: dict[str, int] = {}
        durability: dict[str, int | None] = {}
        for item in inventory.get("items") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").casefold().removeprefix("minecraft:")
            counts[name] = counts.get(name, 0) + _as_int(item.get("count"))
            if name.endswith("_pickaxe"):
                remaining = item.get("durabilityLeft")
                left = _as_int(remaining) if remaining is not None else None
                previous = durability.get(name)
                durability[name] = max(previous or 0, left) if left is not None else previous
        self._inventory = counts
        self._pickaxe_durability = durability
        self._inv_dirt = counts.get("dirt", 0)
        self._inv_wood = sum(count for name, count in counts.items() if name.endswith("_log"))
        self._inv_stone = counts.get("cobblestone", 0)
        return True

    async def _wait_until_free(self) -> bool:
        """等他方动作（玩家指令、对话派工）释放 bot；持有或掉线则放弃本轮。"""
        deadline = self._clock() + _FOREIGN_ACTION_TIMEOUT_SECONDS
        while True:
            if not self._online() or self._paused or self._hold_reason is not None:
                return False
            action = await self._call("action_status", {})
            if not self._is_active(action):
                return True
            if self._clock() >= deadline:
                log("Minecraft 自主作业等待动作释放超时，本组跳过", "DEBUG", tag="Minecraft")
                return False
            await asyncio.sleep(self._poll_seconds())

    async def _entry_point(self) -> dict[str, int]:
        state = await self._call("get_state", {})
        position = state.get("position") or {}
        if not all(k in position for k in ("x", "y", "z")):
            raise RuntimeError("无法读取 bot 位置，无法确定作业面")
        return {k: math.floor(float(position[k])) for k in ("x", "y", "z")}

    async def _relocate(self) -> None:
        """原地无目标时换区：目标在正下方先受控下撤，再朝最近可见资源走，
        找不到再按轮换方向拓荒；全部失败进入冷却并告知玩家，不再原地打转。"""
        state = await self._safe_call("get_state", {})
        position = (state or {}).get("position") or {}
        if not all(k in position for k in ("x", "y", "z")):
            return
        here = {k: math.floor(float(position[k])) for k in ("x", "y", "z")}
        # 目标明显在下方时优先受控下撤：能摔不死直接掉血落地，会摔死才
        # 逐格垂直下挖（每格确认下方仍有实心支撑）到安全高度再落。下撤
        # 带上目标层高度，越过资源层继续挖没有意义。它先于长跳升级——
        # 站在树冠/悬空树叶上时四向寻路全是 NO_PATH，脚下的资源才是唯一
        # 可达的出路。探测列表按环距/块距排序，最近命中常是同层或更高，
        # 必须扫出真正低于脚下 3 格以上的目标，不能只看第一个。
        probe = await self._probe_targets(here)
        below = next((target for target in probe if target["y"] <= here["y"] - 3), None)
        if below is not None and await self._descend(below["y"]):
            log(f"Minecraft 自主作业目标在下方 {here['y'] - below['y']} 格，受控下撤后重新探测", "INFO", tag="Minecraft")
            return
        # 目标明显在上方（坑底被困）：立柱上攀爬出——行进寻路不垫方块，
        # 四向寻路在坑里必然 NO_PATH，垫脚塔是唯一出路。
        above = next((target for target in probe if target["y"] >= here["y"] + 3), None)
        if above is not None and await self._ascend(above["y"]):
            log(f"Minecraft 自主作业目标在上方 {above['y'] - here['y']} 格，立柱上攀后重新探测", "INFO", tag="Minecraft")
            return
        if self._relocate_fails >= _RELOCATE_FAIL_ESCALATION:
            # 近距换区连续无产出：探测命中互相指向残破区内部，直接远程跳区
            # 离开整个地带，落点后再按探测恢复就近作业。长跳尝试全部轮换
            # 方向（单方向直线在山脊地形常 NO_PATH），48 不通降 24，仍全部
            # 不通进入冷却——绝不再落回 8 格碎步，那正是打乒乓的来源。
            for distance in (_RELOCATE_LONG_HOP_DISTANCE, _RELOCATE_DISTANCE):
                for _ in _RELOCATE_DIRECTIONS:
                    dx, dz = _RELOCATE_DIRECTIONS[self._relocate_heading % len(_RELOCATE_DIRECTIONS)]
                    self._relocate_heading += 1
                    if await self._walk_relocate(here["x"] + dx * distance, here["z"] + dz * distance):
                        log(f"Minecraft 自主作业连续 {self._relocate_fails} 轮换区无产出，跳区至 "
                            f"({here['x'] + dx * distance}, {here['z'] + dz * distance}) 离开残破地带",
                            "INFO", tag="Minecraft")
                        return
            # 四向寻路全败的最后手段：先垫柱爬出深坑；仍不行就朝当前轮换
            # 方向「破壁走」——行进寻路不挖地，被挖烂的台地上只有允许挖
            # 自然地形的逃生配置能走出去。
            if await self._ascend():
                log("Minecraft 自主作业四向跳区不可达，立柱上攀爬出深坑后重新探测", "INFO", tag="Minecraft")
                return
            dx, dz = _RELOCATE_DIRECTIONS[self._relocate_heading % len(_RELOCATE_DIRECTIONS)]
            if await self._ascend(None, here["x"] + dx * _RELOCATE_DISTANCE, here["z"] + dz * _RELOCATE_DISTANCE):
                log("Minecraft 自主作业四向跳区不可达，破壁走出被挖烂地带后重新探测", "INFO", tag="Minecraft")
                return
            self._relocate_cooldown_until = self._clock() + _RELOCATE_FAIL_COOLDOWN_SECONDS
            log("Minecraft 自主作业残破地带四向跳区全部不可达，进入冷却", "WARNING", tag="Minecraft")
            await self._announce_cooldown("这一带的地形我走不出去也挖不动，先歇一会儿；带我到平缓些的地方我马上继续。")
            return
        for target in await self._probe_targets(here):
            if await self._walk_relocate(target["x"], target["z"]):
                log(f"Minecraft 自主作业附近无目标，走向可见资源 ({target['x']}, {target['z']}) 继续开工",
                    "INFO", tag="Minecraft")
                return
        # 单方向寻路失败（河流/崖壁）不代表无处可去：先试近距（陡坡上缓段
        # 更可能可达）再试远距，失败即轮换下一方向，全部不通才进入冷却。
        for _ in _RELOCATE_DIRECTIONS:
            dx, dz = _RELOCATE_DIRECTIONS[self._relocate_heading % len(_RELOCATE_DIRECTIONS)]
            self._relocate_heading += 1
            for distance in (8, _RELOCATE_DISTANCE):
                if await self._walk_relocate(here["x"] + dx * distance, here["z"] + dz * distance):
                    label = GATHER_ITEM_LABELS.get(self._item(), self._item())
                    log(f"Minecraft 自主作业附近无{label}，换区至 ({here['x'] + dx * distance}, "
                        f"{here['z'] + dz * distance}) 继续开工", "INFO", tag="Minecraft")
                    return
        self._relocate_cooldown_until = self._clock() + _RELOCATE_FAIL_COOLDOWN_SECONDS
        log("Minecraft 自主作业换区探测与拓荒全部失败，进入冷却", "WARNING", tag="Minecraft")
        await self._announce_cooldown("附近暂时找不到能挖的泥土和树，我先歇一会儿再找；也可以带我走两步换个地方。")

    async def _announce_cooldown(self, text: str) -> None:
        """冷却求助首次立即播报，后续最多每 5 分钟一次，失败不反噬作业循环。"""
        now = self._clock()
        if (
            self._cooldown_announced_at is not None
            and now - self._cooldown_announced_at < _COOLDOWN_ANNOUNCE_INTERVAL
        ):
            return
        self._cooldown_announced_at = now
        try:
            await self._announce(text)
        except Exception as exc:
            log(f"Minecraft 自主作业冷却播报失败: {exc}", "DEBUG", tag="Minecraft")

    async def _mine_call(self, order: dict[str, Any]) -> dict[str, Any]:
        """下矿单；被「旧矿道未确认返程」拒绝时先走回入口解除占用再重试。"""
        try:
            return await self._call("mine_resources", order)
        except Exception as exc:
            if "has not confirmed a return" not in str(exc):
                raise
            log("Minecraft 自主作业上次矿道未确认返程，先走回入口解除占用", "INFO", tag="Minecraft")
            try:
                await asyncio.wait_for(self._call("return_from_mine", {}), timeout=90)
            except Exception as inner:
                raise RuntimeError(f"MINING_STUCK_UNRETURNED: 旧矿道无法返程 ({inner})") from exc
            return await self._call("mine_resources", order)

    async def _staircase_escape(self) -> bool:
        """确定性台阶逃生（分块）：每次最多两级台阶，循环到登顶或无进展。"""
        last_spot = None
        for _chunk in range(24):
            try:
                result = await asyncio.wait_for(self._call("ascend_stairs", {}), timeout=70)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log(f"Minecraft 自主作业台阶逃生失败: {exc}", "DEBUG", tag="Minecraft")
                return False
            position = result.get("position") or {}
            spot = (position.get("x"), position.get("y"), position.get("z"))
            log(f"Minecraft 自主作业台阶逃生: from={result.get('from')} to={result.get('to')} "
                f"dug={result.get('dug')} at={spot}", "DEBUG", tag="Minecraft")
            if result.get("completed"):
                return True
            if spot == last_spot:
                log("Minecraft 自主作业台阶逃生无进展，停止", "DEBUG", tag="Minecraft")
                return False
            last_spot = spot
        return False

    async def _ascend(self, target_y: int | None = None, target_x: int | None = None,
                      target_z: int | None = None) -> bool:
        """逃生：垫柱爬出深坑，或带目标列「破壁走」穿过被挖烂的地带。"""
        args: dict[str, Any] = {}
        if target_y is not None:
            args["targetY"] = int(target_y)
        if target_x is not None and target_z is not None:
            args["targetX"] = int(target_x)
            args["targetZ"] = int(target_z)
        try:
            result = await asyncio.wait_for(self._call("ascend", args), timeout=120)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log(f"Minecraft 自主作业立柱上攀失败: {exc}", "DEBUG", tag="Minecraft")
            return False
        ok = bool(result.get("completed"))
        log(f"Minecraft 自主作业立柱上攀: from={result.get('from')} to={result.get('to')}", "DEBUG", tag="Minecraft")
        return ok

    async def _descend(self, target_y: int | None = None) -> bool:
        """受控下撤：优先掉血落地（不摔死），否则逐格垂直下挖到安全高度。"""
        args: dict[str, Any] = {}
        if target_y is not None:
            args["targetY"] = int(target_y)
        try:
            result = await asyncio.wait_for(self._call("descend", args), timeout=150)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log(f"Minecraft 自主作业受控下撤失败: {exc}", "DEBUG", tag="Minecraft")
            return False
        ok = bool(result.get("completed"))
        log(f"Minecraft 自主作业受控下撤: fallen={result.get('fallen')} dug={result.get('dug')} "
            f"from={result.get('from')} to={result.get('to')}", "DEBUG", tag="Minecraft")
        return ok

    async def _probe_targets(self, here: dict[str, int]) -> list[dict[str, Any]]:
        """可见资源探测：按「保底泥土优先 → 区块环距 → 块距」排序返回可走向位置。

        背包泥土不足保底时泥土命中排在原木前（垫脚启动金）；环距即「本区块
        → 周围 8 区块 → 继续扩环」的扩搜顺序。丢弃高差过大与刚走完的目标——
        深埋方块走向了也挖不到，原地重走只会制造"到达即完成"的空换区。
        """
        await self._refresh_inventory_goals()
        stone_work = self._stone_work()
        soil_first = self._inv_dirt < _DIRT_RESERVE_FLOOR
        soil = [] if stone_work or (self._item() == "auto" and self._dirt_goal_met()) else await self._probe_blocks(_RELOCATE_SOIL_BLOCKS, here)
        if stone_work:
            logs = await self._probe_blocks(("stone",), here)
        else:
            logs = [] if self._item() == "auto" and self._inv_wood >= _WORK_GOAL_WOOD else await self._probe_blocks(_RELOCATE_LOG_BLOCKS, here)
        home = (here["x"] // 16, here["z"] // 16)
        ranked: list[tuple[tuple[int, int, int], dict[str, Any]]] = []
        for is_soil, hits in ((True, soil), (False, logs)):
            for hit in hits:
                x, y, z = (hit[k] for k in ("x", "y", "z"))
                dy = y - here["y"]
                if (stone_work and not -6 <= dy <= 16) or (not stone_work and
                        abs(dy) > (_RELOCATE_SOIL_MAX_DY if is_soil else _RELOCATE_LOG_MAX_DY)):
                    continue
                if self._last_relocate_target is not None and abs(x - self._last_relocate_target[0]) <= 8 \
                        and abs(z - self._last_relocate_target[1]) <= 8:
                    continue
                ring = max(abs(x // 16 - home[0]), abs(z // 16 - home[1]))
                # 3D 距离让浅层命中排在深埋命中前：走向 40 格深的地下土
                # 只会空跑一趟。
                distance = (x - here["x"]) ** 2 + (z - here["z"]) ** 2 + (y - here["y"]) ** 2
                priority = 0 if not soil_first or is_soil else 1
                ranked.append(((priority, ring, distance), {"x": x, "y": y, "z": z}))
        ranked.sort(key=lambda item: item[0])
        return [entry for _key, entry in ranked]

    async def _probe_blocks(self, names: tuple[str, ...], here: dict[str, int]) -> list[dict[str, int]]:
        try:
            found = await asyncio.wait_for(self._call("find_blocks", {
                "matching": list(names),
                "point": here,
                "maxDistance": _RELOCATE_PROBE_DISTANCE,
                "count": 8,
            }), timeout=10)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log(f"Minecraft 自主作业资源探测失败: {exc}", "DEBUG", tag="Minecraft")
            return []
        positions = found.get("positions") or []
        return [
            {k: int(float(entry[k])) for k in ("x", "y", "z")}
            for entry in positions
            if isinstance(entry, dict) and all(k in entry for k in ("x", "y", "z"))
        ]

    async def _walk_relocate(self, x: int, z: int) -> bool:
        """y 无关目标走一步：固定高度的 near 目标在起伏地形上必然大量不可达。"""
        try:
            await asyncio.wait_for(
                self._call("goto", {"goalType": "nearxz", "x": x, "z": z, "range": 4, "timeout": 60000}),
                timeout=70.0,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log(f"Minecraft 自主作业换区移动失败: {exc}", "DEBUG", tag="Minecraft")
            return False
        self._last_relocate_target = (x, z)
        return True

    async def _watch_stack(self, status_tool: str = "gathering_status") -> StackOutcome:
        """轮询采集状态直到终态；裁决/暂停/掉线落在中途则放弃记账。"""
        deadline = self._clock() + _WATCH_TIMEOUT_SECONDS
        previous_progress: tuple[str, int, int] | None = None
        logged_at = 0.0
        while True:
            if self._hold_reason is not None or self._paused or not self._online():
                return StackOutcome(phase="cancelled", reason="watch released", dug=0, gained=0, requested=0)
            status = await self._call(status_tool, {})
            progress = (str(status.get("stage") or ""), _as_int(status.get("dug")), _as_int(status.get("gained")))
            now = self._clock()
            if progress != previous_progress or now - logged_at >= 15:
                log(f"Minecraft 自主作业进度: stage={progress[0]} dug={progress[1]} gained={progress[2]} "
                    f"active={status.get('active')} elapsed={now - (deadline - _WATCH_TIMEOUT_SECONDS):.1f}s "
                    f"reason={status.get('reason') or ''}", "DEBUG", tag="Minecraft")
                previous_progress, logged_at = progress, now
            if not self._is_active(status):
                log(f"Minecraft 自主作业本组终态: {status}", "DEBUG", tag="Minecraft")
                return StackOutcome.from_snapshot(status)
            if self._clock() >= deadline:
                log("Minecraft 自主作业监视超时，取消卡死的采集任务", "WARNING", tag="Minecraft")
                await self._best_effort_cancel()
                snapshot = status if isinstance(status, dict) else {}
                return StackOutcome(
                    phase="blocked",
                    reason="watch_timeout",
                    dug=_as_int(snapshot.get("dug")),
                    gained=_as_int(snapshot.get("gained")),
                    requested=_as_int(snapshot.get("requested")),
                )
            await asyncio.sleep(self._poll_seconds())

    async def _best_effort_cancel(self) -> None:
        try:
            await asyncio.wait_for(self._call("cancel_task", {}), timeout=3)
        except Exception as exc:
            log(f"Minecraft 自主作业监视超时后取消失败: {exc}", "WARNING", tag="Minecraft")

    def _remedy_summary(self) -> str:
        """会话疗法账本：同因受阻几次、每次做了什么——轮换不再是唯一答案。"""
        if not self._cause_remedies:
            return ""
        parts = []
        for cause, count in sorted(self._cause_counts.items()):
            remedy = self._cause_remedies.get(cause)
            if remedy and count >= 1:
                parts.append(f"{cause}×{count}（{remedy}）")
        return "；受阻疗法：" + "、".join(parts) if parts else ""

    def _report_line(self, outcome: StackOutcome) -> str:
        prefix = f"第 {self._stacks_done} 组" if outcome.gained else ""
        if len(outcome.items) > 1:
            head_item = "资源"
            detail = "、".join(f"{GATHER_ITEM_LABELS.get(name, name)} {gained}" for name, gained in outcome.items)
            gains = f"{detail}（合计 {outcome.gained}/{outcome.requested}）"
        else:
            name = outcome.items[0][0] if outcome.items else self._item()
            head_item = GATHER_ITEM_LABELS.get(name, name)
            gains = f"{outcome.gained}/{outcome.requested} 个{head_item}"
        if len(self._totals) > 1:
            totals = "、".join(f"{GATHER_ITEM_LABELS.get(name, name)} {gained}" for name, gained in self._totals.items())
        else:
            totals = f"{self._total_gained} 个"
        if outcome.phase in _BLOCKED_PHASES or outcome.phase not in {"completed"}:
            head = "受阻" if outcome.phase in _BLOCKED_PHASES else outcome.phase
            return (
                f"自主作业{prefix}{head_item}{head}：{outcome.reason or '无终态原因'}；"
                f"本组新入包 {gains}，挖掉 {outcome.dug} 个方块，"
                f"累计 {totals}。{self._remedy_summary()}"
            )
        return (
            f"自主作业{prefix}{head_item}完成：新入包 {gains}，"
            f"挖掉 {outcome.dug} 个方块，累计 {totals}。{self._remedy_summary()}"
        )

    async def _apply_block_outcome(self, outcome: StackOutcome) -> None:
        """受阻是本地自治信号，不是停止信号：换区可绕开的设下一轮搬家，同因连败
        升级换区离开不利站位，其余原样续跑。汇报与裁决始终是旁路，模型不回应等价
        于 continue，循环永不停等。"""
        if outcome.phase in _BLOCKED_PHASES:
            reason = outcome.reason or outcome.phase
            now = self._clock()
            # 秒级同因连败是瞬时失败风暴（如 NO_ROUTE 立判）：首次必报，
            # 重复抑制汇报——裁决是旁路，不能被失败循环放大成 LLM 调用风暴。
            self._suppress_report = (
                reason == self._last_block_reason
                and now - self._last_block_at < _RAPID_REBLOCK_SECONDS
            )
            self._repeated_blocks = self._repeated_blocks + 1 if reason == self._last_block_reason else 1
            self._last_block_reason = reason
            self._last_block_at = now
            # 同因复发记账 + 根因疗法：第 1 次就地疗法；第 2 次起播报升级诊断。
            cause = reason.split(":", 1)[0].strip()
            self._cause_counts[cause] = self._cause_counts.get(cause, 0) + 1
            remedy = await self._apply_remedy(reason)
            if remedy:
                self._cause_remedies[cause] = remedy
                log(f"Minecraft 自主作业受阻疗法（{cause} 第 {self._cause_counts[cause]} 次）：{remedy}",
                    "INFO", tag="Minecraft")
            if self._cause_counts[cause] == 2:
                log(f"Minecraft 自主作业同因第 2 次复发：{cause}——升级诊断，"
                    f"疗法记录：{self._cause_remedies.get(cause, '无就地疗法')}", "WARNING", tag="Minecraft")
            if _needs_relocate(reason):
                self._relocate_pending = True
                # 换区类受阻计入无产出计数：探测命中可能互相指向残破区内部
                # （可见但安全检查不通过），近距换区空转两轮即远程跳区离开。
                self._relocate_fails += 1
                log(f"Minecraft 自主作业作业面受阻，自动换区继续: {reason}", "INFO", tag="Minecraft")
            elif self._repeated_blocks >= _REPEATED_BLOCK_RELOCATE_AFTER:
                self._relocate_pending = True
                self._relocate_fails += 1
                log(f"Minecraft 自主作业同因受阻 {self._repeated_blocks} 组，主动换区离开不利站位: {reason}",
                    "INFO", tag="Minecraft")
            else:
                log(f"Minecraft 自主作业本组受阻，继续下一组: {reason}", "DEBUG", tag="Minecraft")
            return
        self._last_block_reason = ""
        self._repeated_blocks = 0
        self._last_block_at = 0.0
        self._suppress_report = False

    async def _assess_site(self) -> tuple[int, int, int]:
        """评估当前站位：走廊半径内的原木数、泥土族方块数与相对坑深。

        第三个值是「周围地表 − 脚下」的相对高差：探测命中的泥土/原木
        高度就是周围地表，正得多说明 bot 在坑里。"""
        state = await self._safe_call("get_state", {})
        position = (state or {}).get("position") or {}
        if not all(k in position for k in ("x", "y", "z")):
            return 0, 0, 0, ""
        here = {k: int(float(position[k])) for k in ("x", "y", "z")}
        soil = await self._probe_blocks(_RELOCATE_SOIL_BLOCKS, here)
        logs = await self._probe_blocks(_RELOCATE_LOG_BLOCKS, here)
        surface = [hit["y"] for hit in soil + logs if isinstance(hit.get("y"), int)]
        pit = max(surface) - here["y"] if surface else 0
        biome = ""
        try:
            biome = str(((await asyncio.wait_for(self._call("get_biome", {}), timeout=5)).get("biome")) or "")
        except asyncio.CancelledError:
            raise
        except Exception:
            biome = ""
        return len(logs), len(soil), pit, biome

    def _site_suitable(self, logs: int, soil: int, pit: int | None = None,
                       biome: str = "") -> bool:
        """合格 = 群系在白名单 + 至少一棵树的木头 + 足够的泥土 + 不在坑里
        （与周围地表齐平）；石头盲挖可达，绝对海拔不参与判定。群系读不到
        （旧执行器/异常）时跳过群系项，其余条件照常。"""
        if biome and biome not in _HOME_BIOMES:
            return False
        if logs < _HOME_LOG_MIN or soil < _HOME_SOIL_MIN:
            return False
        return pit is None or pit < _PIT_DEPTH_THRESHOLD

    async def _ensure_home(self) -> bool:
        """开工的第一道门：站点不合格先找家，走遍无果诚实求助玩家。

        沙漠/海滩/裸冰原的出生点没有木头与泥土，原地作业只会陷入
        NO_TARGET 循环——先到有树有土的地方，配额才有完成的地基。
        """
        logs, soil, pit, biome = await self._assess_site()
        if self._site_suitable(logs, soil, pit, biome):
            log(f"Minecraft 自主作业站点合格（群系 {biome or '未知'}、原木 {logs}、泥土 {soil}、坑深 {pit}），就地开工",
                "INFO", tag="Minecraft")
            return True
        return await self._migrate(logs, soil, pit, biome)

    async def _migrate(self, logs: int, soil: int, pit: int, biome: str = "") -> bool:
        """迁徙旅程：备剑 → 白天赶路狩猎、夜里睡床，直到合格的平原安家点。

        高山或缺资源的出生地撑不起配额作业；旅程本身就是生存——路上撸
        木做剑、杀猪牛羊鸡捡肉与羊毛、凑齐床过夜，走到树土齐备且海拔
        合适的平原才安家开工。
        """
        reason = f"群系 {biome or '未知'}、原木 {logs}、泥土 {soil}、低于周围地表 {pit} 格"
        log(f"Minecraft 自主作业站点不宜安家（{reason}），踏上迁徙旅程", "INFO", tag="Minecraft")
        self._spawn_announce("这里的群系不适合安家，我出发去找平原或森林安家；路上自己打猎吃饭，放心。")
        await self._journey_warmup()
        await self._journey_prepare()
        leg_origin: tuple[int, int] | None = None
        pit = 0
        legs_left = _JOURNEY_LEGS
        while legs_left > 0:
            if not self._online() or self._paused or self._hold_reason is not None:
                return False
            leg_start = None
            state = await self._safe_call("get_state", {})
            if not state:
                continue
            if (state.get("time") or {}).get("isDay") is False:
                await self._sleep_through_night(state)
            position = (state.get("position") or {})
            if not all(k in position for k in ("x", "y", "z")):
                continue
            here = {k: int(float(position[k])) for k in ("x", "y", "z")}
            # 迁徙认准方向：本腿真的挪动了就沿用同一航向继续外推，只有
            # 原地踏步（<8 格位移）才轮换——每腿换向会在大沙漠里绕出生
            # 点画圈，走不出出生坑的半径。
            if leg_origin is not None and abs(here["x"] - leg_origin[0]) + abs(here["z"] - leg_origin[1]) < 8:
                self._relocate_heading += 1
            leg_origin = (here["x"], here["z"])
            # 地上纪律：低于海平面先垂直爬回地表（绝不水平挖隧道——那是
            # 地鼠）；高出周围地表 6 格以上（恶地台地/悬崖顶）先受控下撤
            # 到周围地表——纯走路翻不下台地，腿会秒判 NO_PATH 烧完旅程。
            # 坑深用上一腿的评估（正=坑、负=高台），首腿用初始评估。
            if here["y"] < _JOURNEY_MIN_Y:
                # 先立柱（有垫材最快）；空背包时改挖台阶——探测最近免挖
                # 免垫出口，逐级开凿走上去（含落沙重挖）。
                if not await self._ascend(_JOURNEY_MIN_Y):
                    await self._staircase_escape()
            elif pit < -_SPIRE_HEIGHT_THRESHOLD:
                # 单腿下撤封顶 10 格：极端地形的「周围地表」可能来自 50 格
                # 外的峡谷底，一次挖到底会慢到 RPC 截断；够到可走台阶即可，
                # 剩余高差由后续腿继续消化。
                await self._descend(here["y"] + max(pit, -10))
            logs, soil, pit, biome = await self._assess_site()
            if self._site_suitable(logs, soil, pit, biome):
                log(f"Minecraft 自主作业旅程终点：安家点（群系 {biome}、原木 {logs}、泥土 {soil}、坑深 {pit}）",
                    "INFO", tag="Minecraft")
                self._spawn_announce(f"找到理想群系的安家点了（{biome}），在这儿开工！")
                return True
            dx, dz = _RELOCATE_DIRECTIONS[self._relocate_heading % len(_RELOCATE_DIRECTIONS)]
            target_x = here["x"] + dx * _HOME_SEEK_DISTANCE
            target_z = here["z"] + dz * _HOME_SEEK_DISTANCE
            leg_start = (here["x"], here["z"])
            if not await self._walk_relocate(target_x, target_z):
                try:
                    await asyncio.wait_for(self._call("cancel_task", {}), timeout=5)
                    await asyncio.sleep(1)
                except Exception:
                    pass
                # 首败可能是区块边缘未加载或部分路径（NOT_ARRIVED 也常带着
                # 真实位移）：等一拍同方向再走一次。
                await asyncio.sleep(3)
                if not await self._walk_relocate(target_x, target_z):
                    # 陡壁地形（恶地台地）纯走路几乎不可通行：破壁走用地表
                    # 切削翻丘（逃生配置带海平面地板，绝不转入地下）。
                    await self._ascend(None, target_x, target_z)
            # 位移计费：真实挪动 ≥8 格不算消耗腿数（NOT_ARRIVED 的部分进
            # 展也是进展）；原地踏步才烧一条腿，直到预算耗尽。
            state = await self._safe_call("get_state", {}) or state
            moved = 0
            after = (state.get("position") or {})
            if leg_start and all(k in after for k in ("x", "z")):
                moved = abs(int(float(after["x"])) - leg_start[0]) + abs(int(float(after["z"])) - leg_start[1])
            if moved < 8:
                legs_left -= 1
            await self._hunt_nearby()
            if int(float(state.get("food") or 20)) < 14:
                await self._remedy_eat()
        log("Minecraft 自主作业迁徙未果：没有走到合格的平原站点", "WARNING", tag="Minecraft")
        self._spawn_announce("走了很远还是没找到理想的平原，先在原地安顿；带我到平原会更快。")
        return False

    async def _journey_warmup(self) -> None:
        """刚进服区块未加载，寻路器看到的全是未知地形会秒判 NO_PATH：先等区块。"""
        await asyncio.sleep(6)

    async def _journey_prepare(self) -> None:
        """启程备货：没有木剑时先撸一组木头合成武器（顺带工作台余料）。"""
        try:
            if self._inventory.get("wooden_sword"):
                return
            entry = await self._entry_point()
            await self._call("gather_resources", {
                "block": "auto", "count": _JOURNEY_PREP_WOOD,
                "x": entry["x"], "y": entry["y"], "z": entry["z"],
                "radius": _WORK_RADIUS, "mode": "count",
            })
            await self._watch_stack()
            await prepare_pickaxe(
                self._call, running=lambda: self._online() and not self._paused,
                poll_seconds=self._poll_seconds(), clock=self._clock, item="wooden_sword",
            )
            log("Minecraft 自主作业旅程备货：木剑已就绪", "INFO", tag="Minecraft")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log(f"Minecraft 自主作业旅程备货受阻（继续赶路）: {exc}", "DEBUG", tag="Minecraft")

    async def _hunt_nearby(self) -> None:
        """猎杀 24 格内的猪牛羊鸡兔：近战击杀后走到掉落点捡肉与羊毛。"""
        try:
            found = await asyncio.wait_for(self._call("list_entities", {
                "type": "mob", "maxDistance": 24, "count": 24,
            }), timeout=10)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log(f"Minecraft 自主作业猎物探测失败: {exc}", "DEBUG", tag="Minecraft")
            return
        prey = None
        for entity in found.get("entities") or []:
            if not isinstance(entity, dict) or str(entity.get("name") or "") not in _HUNT_PREY:
                continue
            if entity.get("isValid") is False or not isinstance(entity.get("position"), dict):
                continue
            prey = entity
            break
        if prey is None:
            return
        spot = {k: int(float(prey["position"][k])) for k in ("x", "y", "z") if k in prey["position"]}
        try:
            await asyncio.wait_for(self._call("pvp_attack", {"entityId": prey.get("id")}), timeout=60)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log(f"Minecraft 自主作业狩猎失败（{prey.get('name')}）: {exc}", "DEBUG", tag="Minecraft")
            return
        if len(spot) == 3:
            try:
                await asyncio.wait_for(self._call("goto", {
                    "goalType": "near", **spot, "range": 1, "timeout": 15000,
                }), timeout=20)
            except Exception:
                pass
        log(f"Minecraft 自主作业狩猎得手（{prey.get('name')}）", "INFO", tag="Minecraft")

    async def _sleep_through_night(self, state: dict[str, Any]) -> None:
        """入夜有床就睡到天亮；没有床（或睡不着）就继续赶路。"""
        if not self._inventory.get("bed"):
            wool = sum(count for name, count in self._inventory.items() if name.endswith("_wool"))
            planks = sum(count for name, count in self._inventory.items() if name.endswith("_planks"))
            if wool < 3 or planks < 3:
                return
            try:
                await prepare_pickaxe(
                    self._call, running=lambda: self._online() and not self._paused,
                    poll_seconds=self._poll_seconds(), clock=self._clock, item="bed",
                )
                await self._refresh_inventory_goals()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log(f"Minecraft 自主作业造床未成（今夜继续赶路）: {exc}", "DEBUG", tag="Minecraft")
                return
        if not self._inventory.get("bed"):
            return
        try:
            placed = await asyncio.wait_for(self._call("place_item", {"item": "bed"}), timeout=15)
            cell = placed.get("position") or {}
            if all(k in cell for k in ("x", "y", "z")):
                await asyncio.wait_for(self._call("sleep", cell), timeout=600)
                log("Minecraft 自主作业一觉到天亮，继续赶路", "INFO", tag="Minecraft")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log(f"Minecraft 自主作业睡床未成（今夜继续赶路）: {exc}", "DEBUG", tag="Minecraft")

    async def _apply_remedy(self, reason: str) -> str:
        """受阻原因的根因疗法：分诊表映射原因前缀到纠正动作，返回做了什么。

        无法就地解决的（需要玩家/基础设施）诚实说明缺口，不再用轮换掩盖。
        """
        cause = reason.split(":", 1)[0].strip()
        if cause in {"GATHER_LOW_SUPPLIES", "MINING_LOW_SUPPLIES"}:
            return await self._remedy_eat()
        if cause == "MINING_OFF_ROUTE":
            return await self._remedy_approach_mine()
        if cause == "GATHER_FULL":
            return "背包已满：需要玩家指定存箱点（或整理背包），换区解决不了满背包"
        return ""

    async def _remedy_eat(self) -> str:
        """按食物偏好进食：找背包最好的食物装备并吃掉。"""
        try:
            inventory = await asyncio.wait_for(self._call("get_inventory", {}), timeout=10)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return f"低补给受阻，且查背包失败（{exc}）"
        names = {str(item.get("name") or "") for item in inventory.get("items") or []}
        food = next((name for name in _FOOD_PREFERENCE if name in names), None)
        if food is None:
            return "低补给受阻且背包没有任何食物：需要玩家补给食物，否则作业无法持续"
        try:
            await asyncio.wait_for(self._call("equip_item", {"item": food, "destination": "hand"}), timeout=10)
            await asyncio.wait_for(self._call("consume", {}), timeout=30)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return f"尝试吃 {food} 失败（{exc}）"
        log(f"Minecraft 自主作业进食疗法：吃掉了 {food}", "INFO", tag="Minecraft")
        return f"低补给受阻，已吃掉 {food} 恢复"

    async def _remedy_approach_mine(self) -> str:
        """OFF_ROUTE：走回矿道入口，恢复与通道的空间关系。"""
        try:
            saved = await asyncio.wait_for(self._call("mining_status", {}), timeout=10)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return f"矿道脱轨且查矿道状态失败（{exc}）"
        entry = saved.get("entry")
        if not isinstance(entry, dict) or not all(k in entry for k in ("x", "y", "z")):
            return "矿道脱轨但没有已保存的矿道入口"
        try:
            await asyncio.wait_for(self._call("goto", {
                "goalType": "near", "x": int(entry["x"]), "y": int(entry["y"]),
                "z": int(entry["z"]), "range": 2, "timeout": 60000,
            }), timeout=70)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return f"矿道脱轨，走回入口失败（{exc}）"
        return "矿道脱轨，已走回矿道入口恢复通道关系"

    def _track_stuck_spot(self, outcome: StackOutcome) -> None:
        """同点位空手连败升级换区：失败原因可能轮换（NO_TARGET/寻路/挖掘中止），
        按「原因」计数永远凑不齐；按「入口邻域 + 空手」计数——表面单与走廊单
        的入口会在同一片自挖地形上来回挪动，完全相等永远凑不齐阈值。"""
        entry = outcome.entry
        if entry is None:
            return
        same_spot = (self._stuck_entry is not None
                     and max(abs(entry[axis] - self._stuck_entry[axis]) for axis in range(3)) <= 8)
        if same_spot:
            if outcome.gained == 0:
                self._stuck_streak += 1
        else:
            self._stuck_entry = entry
            self._stuck_streak = 1 if outcome.gained == 0 else 0
        if self._stuck_streak >= _REPEATED_BLOCK_RELOCATE_AFTER and not self._relocate_pending:
            self._relocate_pending = True
            self._stuck_streak = 0
            self._relocate_fails += 1
            log(f"Minecraft 自主作业同点位邻域连续空手受阻 {entry}，主动换区离开死点", "INFO", tag="Minecraft")

    def _spawn_report(self, report_line: str) -> None:
        """后台汇报：历史写入与裁决轮都不占用作业循环，失败不反噬作业。"""
        task = asyncio.create_task(self._report_and_verdict(report_line), name="channel.minecraft.worker-report")
        self._reports.add(task)
        task.add_done_callback(self._reports.discard)

    def _spawn_announce(self, text: str) -> None:
        """后台播报：收工等节点事实直接告知玩家，失败不反噬作业循环。"""

        async def guarded() -> None:
            try:
                await self._announce(text)
            except Exception as exc:
                log(f"Minecraft 自主作业节点播报失败: {exc}", "DEBUG", tag="Minecraft")

        task = asyncio.create_task(guarded(), name="channel.minecraft.worker-announce")
        self._reports.add(task)
        task.add_done_callback(self._reports.discard)

    async def _report_and_verdict(self, report_line: str) -> None:
        try:
            await self._record(report_line)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log(f"Minecraft 自主作业汇报记录失败: {exc}", "DEBUG", tag="Minecraft")
        if self._relocate_pending or self._clock() < self._relocate_cooldown_until:
            # 换区自治已接管：换区类受阻是本循环自己处理的信号，此时让
            # 模型裁决只会在自救中途收到 stop。事实已记录，跳过裁决轮。
            log("Minecraft 自主作业换区自治中，跳过本轮裁决", "DEBUG", tag="Minecraft")
            return
        await self._verdict_turn(report_line)

    async def _verdict_turn(self, report_line: str) -> None:
        """每组一次的监督快车道：模型只输出裁决与一句播报，不编排任何动作。

        开启门卫时先经 Jev 预判，例行汇报高置信跳过本轮。召回策略钉 off 档、
        整轮墙钟限时——供应商回退链卡死时到点放弃，等价 continue，绝不阻塞作业循环。
        """
        from agent.runtime.singleton import get_runtime

        if self._judgment_gate() and not await verdict_needed(
            report_line, blocked_reason=self._last_block_reason
        ):
            return
        runtime = get_runtime()
        if runtime is None:
            return
        world = self._server_id()
        scope = build_entity_scope("group", "minecraft", world)
        prompt = f"[自主作业汇报]\n世界: {world}\n{report_line}\n{_VERDICT_PROMPT}"
        try:
            with tool_request(scope, "@worker"):
                result = await asyncio.wait_for(
                    self._fast_reflect(runtime, prompt),
                    timeout=_VERDICT_TIMEOUT_SECONDS,
                )
        except asyncio.TimeoutError:
            log("Minecraft 自主作业裁决轮超时，按 continue 继续", "DEBUG", tag="Minecraft")
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log(f"Minecraft 自主作业裁决轮失败: {exc}", "DEBUG", tag="Minecraft")
            return
        await self._apply_verdict(result or "")

    async def _fast_reflect(self, runtime: Any, prompt: str) -> str:
        """裁决快车道 reflect：经 recall_policy 声明 off 档召回（SDK 直调入口的
        策略覆盖面），不触发深召回规划与后台补充；base_messages 直传零检索。"""
        from agent.memory.recall_policy import RecallPolicy, recall_options

        with recall_options(RecallPolicy(level="off", background=False)):
            return await runtime.mind.reflect(
                [{"role": "user", "content": prompt}],
                adapter_key="minecraft",
                allow_output_tools=False,
                max_iterations=2,
                require_output=True,
            )

    async def _apply_verdict(self, text: str) -> None:
        match = _VERDICT_PATTERN.search(text)
        verdict = match.group(1).lower() if match else "continue"
        if verdict == "stop":
            self._hold_reason = "model_stop"
            action = await self._safe_call("action_status", {})
            if action is not None and self._is_active(action):
                try:
                    await asyncio.wait_for(self._call("pause_action", {}), timeout=3)
                    self._paused_action = True
                except Exception as exc:
                    log(f"Minecraft 裁决暂停未确认: {exc}", "WARNING", tag="Minecraft")
            log("Minecraft 模型裁决 stop，自主作业暂停开工", "INFO", tag="Minecraft")
        else:
            if self._hold_reason == "goal_met":
                # 配额收工只由玩家 !resume 解除：后台裁决的 continue 不重启
                # 已达标的作业，否则刚宣布收工待命就自相矛盾地继续挖。
                log("Minecraft 模型裁决 continue，但本轮配额已达标，保持收工待命", "INFO", tag="Minecraft")
            else:
                if self._hold_reason is not None and _needs_relocate(self._last_block_reason):
                    self._relocate_pending = True
                if self._paused_action:
                    self._paused_action = False
                    try:
                        await asyncio.wait_for(self._call("resume_action", {"allowIdle": True}), timeout=3)
                    except Exception as exc:
                        log(f"Minecraft 裁决恢复未确认: {exc}", "WARNING", tag="Minecraft")
                if self._hold_reason is not None:
                    log(f"Minecraft 模型裁决 continue，解除持有: {self._hold_reason}", "INFO", tag="Minecraft")
                self._hold_reason = None
        message = _verdict_message(text)
        if message:
            await self._announce(message)

    async def _safe_call(self, name: str, args: dict[str, Any]) -> dict[str, Any] | None:
        try:
            return await self._call(name, args)
        except Exception as exc:
            log(f"Minecraft 自主作业查询 {name} 失败: {exc}", "DEBUG", tag="Minecraft")
            return None

    async def _call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        result = await self._call_tool(name, args)
        if not isinstance(result, dict):
            raise TypeError(f"MCP {name} 返回了非对象结果")
        return result

    @staticmethod
    def _is_active(result: dict[str, Any]) -> bool:
        if bool(result.get("active")):
            return True
        phase = result.get("phase")
        return isinstance(phase, str) and phase in _ACTIVE_PHASES


def _verdict_message(text: str) -> str:
    """剥掉裁决标记行，剩下的正文播报给玩家；空正文不播。

    reflect 多轮迭代会合并各轮文本，模型复述同一句播报时在此按行去重，
    避免同一句话重复进游戏聊天。
    """
    lines = [line.strip() for line in text.splitlines() if not _VERDICT_PATTERN.search(line)]
    return "\n".join(dict.fromkeys(line for line in lines if line))
