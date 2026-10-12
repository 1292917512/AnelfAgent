"""常驻自主作业循环：进服即开工，零模型驱动，按组汇报并接受继续/停止裁决。

架构定位：与 MinecraftLifestyle（模型反思驱动的自主生活）相对，本循环把控制权
交给确定性脚本——在线即开始采集作业，模型不指令也持续干活；每组终态向模型
汇报一次事实，模型只输出 WORK_VERDICT 裁决（continue / stop），不编排动作。
汇报（历史写入 + 裁决轮）整体是后台任务，作业循环从不等待汇报——一组终态后
立即受理下一组，执行器终态本身已含返程与库存收尾，无需任何额外间歇。
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
_ACTIVE_PHASES = frozenset({"running", "returning", "pausing", "paused", "stopping"})
_BLOCKED_PHASES = frozenset({"blocked", "interrupted"})


def _needs_relocate(reason: str) -> bool:
    """受阻原因是否指向「换区即可绕开」的环境性阻塞
    （无目标 / 位置拉锯 / 支架路线不可达）。"""
    return (_NO_TARGET_MARKER in reason or _POSITION_FIGHT_MARKER in reason
            or _NO_ROUTE_MARKER in reason)
_ITEM_LABELS = {
    "dirt": "泥土",
    "cobblestone": "圆石",
    "stone": "石头",
    "oak_log": "橡木原木",
    "birch_log": "白桦原木",
    "spruce_log": "云杉原木",
    "jungle_log": "丛林原木",
    "acacia_log": "金合欢原木",
    "dark_oak_log": "深色橡木原木",
    "cherry_log": "樱花原木",
    "mangrove_log": "红树林原木",
}

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

    @classmethod
    def from_snapshot(cls, snapshot: dict[str, Any]) -> "StackOutcome":
        phase = snapshot.get("phase")
        if not isinstance(phase, str) or not phase:
            phase = "running" if bool(snapshot.get("active")) else "unknown"
        return cls(
            phase=phase,
            reason=str(snapshot.get("reason") or ""),
            dug=_as_int(snapshot.get("dug")),
            gained=_as_int(snapshot.get("gained")),
            requested=_as_int(snapshot.get("requested")),
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
        self._clock = clock
        self._session: asyncio.Task[None] | None = None
        self._reports: set[asyncio.Task[None]] = set()
        self._paused = False
        self._hold_reason: str | None = None
        self._paused_action = False
        self._stacks_done = 0
        self._total_gained = 0
        self._last_block_reason = ""
        self._relocate_pending = False
        self._relocate_heading = 0
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
            self._stacks_done += 1
            self._total_gained += outcome.gained
            self._apply_block_outcome(outcome)
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
        await self._call("gather_resources", {
            "block": self._item(),
            "count": self._stack_size(),
            "x": entry["x"],
            "y": entry["y"],
            "z": entry["z"],
            "radius": _WORK_RADIUS,
            "mode": "count",
        })
        return await self._watch_stack()

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
        """原地无目标时换区：朝轮换方向前进一段再开工，让裁决循环有新作业面可挖。"""
        state = await self._safe_call("get_state", {})
        position = (state or {}).get("position") or {}
        if not all(k in position for k in ("x", "y", "z")):
            return
        # 单方向寻路失败（河流/崖壁）不代表无处可去：失败即轮换下一方向，
        # 四个方向都不通才留在原地开工。半途放弃会把换区变成新的 NO_TARGET 来源。
        for _ in _RELOCATE_DIRECTIONS:
            dx, dz = _RELOCATE_DIRECTIONS[self._relocate_heading % len(_RELOCATE_DIRECTIONS)]
            self._relocate_heading += 1
            target = {
                "x": int(float(position["x"])) + dx * _RELOCATE_DISTANCE,
                "y": int(float(position["y"])),
                "z": int(float(position["z"])) + dz * _RELOCATE_DISTANCE,
            }
            try:
                await asyncio.wait_for(
                    self._call("goto", {"goalType": "near", **target, "range": 2, "timeout": 90000}),
                    timeout=100.0,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log(f"Minecraft 自主作业换区移动失败: {exc}", "DEBUG", tag="Minecraft")
                continue
            label = _ITEM_LABELS.get(self._item(), self._item())
            log(f"Minecraft 自主作业附近无{label}，换区至 ({target['x']}, {target['z']}) 继续开工",
                "INFO", tag="Minecraft")
            return

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
        label = _ITEM_LABELS.get(self._item(), self._item())
        if outcome.phase in _BLOCKED_PHASES or outcome.phase not in {"completed"}:
            head = "受阻" if outcome.phase in _BLOCKED_PHASES else outcome.phase
            return (
                f"自主作业第 {self._stacks_done} 组{label}{head}：{outcome.reason or '无终态原因'}；"
                f"本组新入包 {outcome.gained}/{outcome.requested}，挖掉 {outcome.dug} 个方块，"
                f"累计 {self._total_gained} 个。"
            )
        return (
            f"自主作业第 {self._stacks_done} 组{label}完成：新入包 {outcome.gained}/{outcome.requested}，"
            f"挖掉 {outcome.dug} 个方块，累计 {self._total_gained} 个。"
        )

    def _apply_block_outcome(self, outcome: StackOutcome) -> None:
        """受阻是本地自治信号，不是停止信号：换区可绕开的设下一轮搬家，其余原样
        续跑。汇报与裁决始终是旁路，模型不回应等价于 continue，循环永不停等。"""
        if outcome.phase in _BLOCKED_PHASES:
            reason = outcome.reason or outcome.phase
            now = self._clock()
            # 秒级同因连败是瞬时失败风暴（如 NO_ROUTE 立判）：首次必报，
            # 重复抑制汇报——裁决是旁路，不能被失败循环放大成 LLM 调用风暴。
            self._suppress_report = (
                reason == self._last_block_reason
                and now - self._last_block_at < _RAPID_REBLOCK_SECONDS
            )
            self._last_block_reason = reason
            self._last_block_at = now
            if _needs_relocate(reason):
                self._relocate_pending = True
                log(f"Minecraft 自主作业作业面受阻，自动换区继续: {reason}", "INFO", tag="Minecraft")
            else:
                log(f"Minecraft 自主作业本组受阻，继续下一组: {reason}", "DEBUG", tag="Minecraft")
            return
        self._last_block_reason = ""
        self._last_block_at = 0.0
        self._suppress_report = False

    def _spawn_report(self, report_line: str) -> None:
        """后台汇报：历史写入与裁决轮都不占用作业循环，失败不反噬作业。"""
        task = asyncio.create_task(self._report_and_verdict(report_line), name="channel.minecraft.worker-report")
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

        召回策略钉 off 档、整轮墙钟限时——供应商回退链卡死时到点放弃，
        等价 continue，绝不阻塞作业循环。
        """
        from agent.runtime.singleton import get_runtime

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
