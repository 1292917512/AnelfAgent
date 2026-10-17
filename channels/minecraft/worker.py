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
"""

from __future__ import annotations

import asyncio
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
# 一轮自主作业的产出目标：2 组泥土 + 1 组木头，达成即收工待命（!resume 开新一轮）。
_WORK_GOAL_DIRT_GROUPS = 2
_WORK_GOAL_WOOD_GROUPS = 1
_ACTIVE_PHASES = frozenset({"running", "returning", "pausing", "paused", "stopping"})
_BLOCKED_PHASES = frozenset({"blocked", "interrupted"})


def _needs_relocate(reason: str) -> bool:
    """受阻原因是否指向「换区即可绕开」的环境性阻塞
    （无目标 / 位置拉锯 / 支架路线不可达）。"""
    return (_NO_TARGET_MARKER in reason or _POSITION_FIGHT_MARKER in reason
            or _NO_ROUTE_MARKER in reason)

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
            requested=_as_int(snapshot.get("requested")),
            items=items,
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
        self._last_block_reason = ""
        self._repeated_blocks = 0
        self._relocate_pending = False
        self._relocate_heading = 0
        self._relocate_cooldown_until = 0.0
        self._cooldown_announced_at = 0.0
        self._last_relocate_target: tuple[int, int] | None = None
        self._last_block_at = 0.0
        self._suppress_report = False

    @property
    def holding(self) -> bool:
        """作业因模型明确 model_stop 而暂停开工；受阻走换区自治，不进入持有。"""
        return self._hold_reason is not None

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
            self._apply_block_outcome(outcome)
            self._track_stuck_spot(outcome)
            await self._refresh_inventory_goals()
            if self._dirt_goal_met() and self._wood_goal_met():
                self._hold_reason = "goal_met"
                self._relocate_pending = False
                log(f"Minecraft 自主作业目标达成（背包泥土 {self._inv_dirt}、原木 {self._inv_wood}），收工待命",
                    "INFO", tag="Minecraft")
                self._spawn_announce("两组泥土和一组木头都备齐啦，我在这儿歇着；用掉一些后喊 !resume 我再补。")
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
        entry = await self._entry_point()
        order: dict[str, Any] = {
            "block": self._item(),
            "count": self._stack_size(),
            "x": entry["x"],
            "y": entry["y"],
            "z": entry["z"],
            "radius": _WORK_RADIUS,
            "mode": "count",
        }
        if self._item() in {"auto", "dirt"}:
            # 区块对齐作业面：// 是 floor 语义，负坐标也落在正确区块。
            order["chunk"] = {"x": entry["x"] // 16, "z": entry["z"] // 16}
        await self._refresh_inventory_goals()
        if self._item() == "auto" and self._dirt_goal_met() and not self._wood_goal_met():
            # 泥土配额已满、木头未满：本组只砍树。
            order["logsOnly"] = True
        if self._item() == "auto" and self._wood_goal_met() and not self._dirt_goal_met():
            # 木头配额已满、泥土未满：本组只挖土，别再让混合单挑中树后撞爬树储备门槛。
            order["block"] = "dirt"
        await self._call("gather_resources", order)
        return await self._watch_stack()

    def _dirt_goal_met(self) -> bool:
        return self._inv_dirt >= self._stack_size() * _WORK_GOAL_DIRT_GROUPS

    def _wood_goal_met(self) -> bool:
        return self._inv_wood >= self._stack_size() * _WORK_GOAL_WOOD_GROUPS

    async def _refresh_inventory_goals(self) -> None:
        """按背包持有量刷新目标进度：重启不清零，玩家取走材料后 resume 补足。"""
        try:
            inventory = await asyncio.wait_for(self._call("get_inventory", {}), timeout=10)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log(f"Minecraft 自主作业库存查询失败: {exc}", "DEBUG", tag="Minecraft")
            return
        dirt = wood = 0
        for item in inventory.get("items") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").casefold().removeprefix("minecraft:")
            if name == "dirt":
                dirt += int(item.get("count") or 0)
            elif name.endswith("_log"):
                wood += int(item.get("count") or 0)
        self._inv_dirt, self._inv_wood = dirt, wood

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
        return {k: int(float(position[k])) for k in ("x", "y", "z")}

    async def _relocate(self) -> None:
        """原地无目标时换区：目标在正下方先受控下撤，再朝最近可见资源走，
        找不到再按轮换方向拓荒；全部失败进入冷却并告知玩家，不再原地打转。"""
        state = await self._safe_call("get_state", {})
        position = (state or {}).get("position") or {}
        if not all(k in position for k in ("x", "y", "z")):
            return
        here = {k: int(float(position[k])) for k in ("x", "y", "z")}
        # 目标明显在下方时优先受控下撤：能摔不死直接掉血落地，会摔死才
        # 逐格垂直下挖（每格确认下方仍有实心支撑）到安全高度再落。下撤
        # 带上目标层高度，越过资源层继续挖没有意义。它先于长跳升级——
        # 站在树冠/悬空树叶上时四向寻路全是 NO_PATH，脚下的资源才是唯一
        # 可达的出路。探测列表按环距/块距排序，最近命中常是同层或更高，
        # 必须扫出真正低于脚下 3 格以上的目标，不能只看第一个。
        below = next((target for target in await self._probe_targets(here)
                      if target["y"] <= here["y"] - 3), None)
        if below is not None and await self._descend(below["y"]):
            log(f"Minecraft 自主作业目标在下方 {here['y'] - below['y']} 格，受控下撤后重新探测", "INFO", tag="Minecraft")
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
        """冷却求助播报节流：同一困境最多每 5 分钟说一次，失败不反噬作业循环。"""
        if self._clock() - self._cooldown_announced_at < _COOLDOWN_ANNOUNCE_INTERVAL:
            return
        self._cooldown_announced_at = self._clock()
        try:
            await self._announce(text)
        except Exception as exc:
            log(f"Minecraft 自主作业冷却播报失败: {exc}", "DEBUG", tag="Minecraft")

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
        soil_first = await self._dirt_below_floor()
        soil = await self._probe_blocks(_RELOCATE_SOIL_BLOCKS, here)
        logs = await self._probe_blocks(_RELOCATE_LOG_BLOCKS, here)
        home = (here["x"] // 16, here["z"] // 16)
        ranked: list[tuple[tuple[int, int, int], dict[str, Any]]] = []
        for is_soil, hits in ((True, soil), (False, logs)):
            for hit in hits:
                x, y, z = (hit[k] for k in ("x", "y", "z"))
                if abs(y - here["y"]) > (_RELOCATE_SOIL_MAX_DY if is_soil else _RELOCATE_LOG_MAX_DY):
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

    async def _dirt_below_floor(self) -> bool:
        """背包泥土是否低于垫脚保底；读不到库存时保守视为不缺。"""
        try:
            inventory = await asyncio.wait_for(self._call("get_inventory", {}), timeout=10)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log(f"Minecraft 自主作业库存查询失败: {exc}", "DEBUG", tag="Minecraft")
            return False
        total = 0
        for item in inventory.get("items") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").casefold().removeprefix("minecraft:")
            if name == "dirt":
                total += int(item.get("count") or 0)
        return total < _DIRT_RESERVE_FLOOR

    async def _walk_relocate(self, x: int, z: int) -> bool:
        """y 无关目标走一步：固定高度的 near 目标在起伏地形上必然大量不可达。"""
        try:
            await asyncio.wait_for(
                self._call("goto", {"goalType": "nearxz", "x": x, "z": z, "range": 4, "timeout": 90000}),
                timeout=100.0,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log(f"Minecraft 自主作业换区移动失败: {exc}", "DEBUG", tag="Minecraft")
            return False
        self._last_relocate_target = (x, z)
        return True

    async def _watch_stack(self) -> StackOutcome:
        """轮询采集状态直到终态；裁决/暂停/掉线落在中途则放弃记账。"""
        deadline = self._clock() + _WATCH_TIMEOUT_SECONDS
        while True:
            if self._hold_reason is not None or self._paused or not self._online():
                return StackOutcome(phase="cancelled", reason="watch released", dug=0, gained=0, requested=0)
            status = await self._call("gathering_status", {})
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
                f"累计 {totals}。"
            )
        return (
            f"自主作业{prefix}{head_item}完成：新入包 {gains}，"
            f"挖掉 {outcome.dug} 个方块，累计 {totals}。"
        )

    def _apply_block_outcome(self, outcome: StackOutcome) -> None:
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

    def _track_stuck_spot(self, outcome: StackOutcome) -> None:
        """同点位空手连败升级换区：失败原因可能轮换（NO_TARGET/寻路/挖掘中止），
        按「原因」计数永远凑不齐，按「入口坐标 + 空手」计数才能跳出死点。"""
        entry = outcome.entry
        if entry is None:
            return
        if entry == self._stuck_entry:
            if outcome.gained == 0:
                self._stuck_streak += 1
        else:
            self._stuck_entry = entry
            self._stuck_streak = 1 if outcome.gained == 0 else 0
        if self._stuck_streak >= _REPEATED_BLOCK_RELOCATE_AFTER and not self._relocate_pending:
            self._relocate_pending = True
            self._stuck_streak = 0
            self._relocate_fails += 1
            log(f"Minecraft 自主作业同点位连续空手受阻 {entry}，主动换区离开死点", "INFO", tag="Minecraft")

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
