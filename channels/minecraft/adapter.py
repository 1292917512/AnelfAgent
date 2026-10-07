"""将 Mineflayer MCP 的玩家聊天接入统一频道管道。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from typing import Any

from pydantic import ValidationError

from agent.channel.base import BaseChannel, ChannelMetadata
from agent.channel.channel_types import ChannelCapability, ChannelStatus
from agent.channel.schemas import (
    AdapterChannel,
    AdapterMessage,
    AdapterUser,
    ChannelInfo,
    ChannelType,
    ChannelUser,
    HealthStatus,
    SegmentType,
    SendRequest,
    SendResponse,
    SendSegment,
)
from core.entity import EntityMetadata, EntityRegistry
from core.log import log
from entities._sdk import call_mcp_server_tool

from . import discovery as _discovery  # noqa: F401  导入即注册全局发现工具
from .config import MinecraftConfig
from .protocol import ConnectionStatus, EventBatch, GameEvent, PlayerChat, split_chat
from .reflexes import ReflexEngine


class MinecraftChannel(BaseChannel[MinecraftConfig]):
    """轮询游戏聊天，按世界隔离会话，并将回复投递回游戏。"""

    channel_id = "minecraft"
    display_name = "Minecraft"
    display_order = 75
    capabilities = {ChannelCapability.SEND_TEXT}
    metadata = ChannelMetadata(name="Minecraft", description="Java 游戏聊天与陪玩，执行器使用 Mineflayer MCP")
    _Configs = MinecraftConfig

    def __init__(self) -> None:
        super().__init__()
        self._poll_task: asyncio.Task[None] | None = None
        self._cursor: int | None = None
        self._server_entity: EntityMetadata | None = None
        self._connection: ConnectionStatus | None = None
        self._background: set[asyncio.Task[Any]] = set()
        self._reflex_task: asyncio.Task[None] | None = None
        self._reflex_stop: asyncio.Event | None = None
        self._home: dict[str, Any] | None = None
        self._marks: dict[str, dict[str, Any]] = {}
        self._last_success: float | None = None
        self._last_error = ""

    async def start(self) -> None:
        if self._poll_task is not None and not self._poll_task.done():
            return
        self._status = ChannelStatus.RUNNING
        self._cursor = None
        self._poll_task = asyncio.create_task(self._poll_loop(), name="channel.minecraft.events")
        self._sync_reflexes()

    async def stop(self) -> None:
        task, self._poll_task = self._poll_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        reflex, self._reflex_task = self._reflex_task, None
        if self._reflex_stop is not None:
            self._reflex_stop.set()
        if reflex is not None:
            reflex.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reflex
        for bg in list(self._background):
            bg.cancel()
        for bg in list(self._background):
            with contextlib.suppress(asyncio.CancelledError):
                await bg
        self._status = ChannelStatus.STOPPED

    def _sync_reflexes(self) -> None:
        """按配置热开关反射循环（配置中心改动经轮询周期生效）。"""
        if self.get_config().reflexes_enabled:
            self._start_reflexes()
        else:
            self._stop_reflexes()

    def _start_reflexes(self) -> None:
        if self._reflex_task is not None and not self._reflex_task.done():
            return
        self._reflex_stop = asyncio.Event()
        engine = ReflexEngine(self._call, self._announce_reflex, spawn=self._spawn_background)
        self._reflex_task = asyncio.create_task(
            engine.run(self.get_config().reflex_interval_seconds, self._reflex_stop),
            name="channel.minecraft.reflexes",
        )

    def _stop_reflexes(self) -> None:
        if self._reflex_stop is not None:
            self._reflex_stop.set()
        task, self._reflex_task = self._reflex_task, None
        if task is not None:
            task.cancel()

    async def _announce_reflex(self, text: str) -> None:
        """反射事件播报到世界公共频道，沟通者从聊天事件自然得知上下文。"""
        cfg = self.get_config()
        await self._send_text(
            AdapterChannel(
                channel_id=cfg.server_id,
                channel_type=ChannelType.GROUP,
                channel_name=f"Minecraft {cfg.server_id}",
            ),
            text,
        )

    async def _call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raw = await call_mcp_server_tool(self.get_config().mcp_server, tool_name, arguments)
        data: object = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("Minecraft MCP 返回了非对象结果")
        if data.get("error") or data.get("success") is False:
            raise RuntimeError(str(data.get("error") or data))
        return data

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self._poll_once()
                self._last_success = time.time()
                self._last_error = ""
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                detail = str(exc)
                if detail != self._last_error:
                    log(f"Minecraft 聊天桥等待执行器: {detail}", "WARNING", tag="Minecraft")
                self._last_error = detail
            self._sync_reflexes()
            await asyncio.sleep(self.get_config().poll_interval_seconds)

    async def _poll_once(self) -> None:
        cfg = self.get_config()
        entity = EntityRegistry.get(f"mcp:{cfg.mcp_server}")
        if entity is not self._server_entity:
            self._server_entity = entity
            self._cursor = None
        self._connection = ConnectionStatus.model_validate(await self._call("get_connection_status", {}))
        args: dict[str, Any] = {
            "types": ["__anelf_cursor__"] if self._cursor is None else ["chat", "whisper"],
            "limit": 32,
        }
        if self._cursor is not None:
            args["since"] = self._cursor
        batch = EventBatch.model_validate(await self._call("get_events", args))
        if self._cursor is None or batch.next_since < self._cursor:
            self._cursor = batch.next_since
            return
        if batch.dropped:
            log("Minecraft 聊天事件缓冲区溢出，部分历史消息已丢失", "WARNING", tag="Minecraft")
        for event in batch.events:
            if event.seq <= self._cursor:
                continue
            await self._dispatch_event(event)
            self._cursor = event.seq
        self._cursor = batch.next_since

    async def _dispatch_event(self, event: GameEvent) -> None:
        if event.type not in {"chat", "whisper"}:
            return
        try:
            chat = PlayerChat.model_validate(event.data)
        except ValidationError:
            log(f"Minecraft 忽略非法玩家聊天事件: seq={event.seq}", "WARNING", tag="Minecraft")
            return
        cfg = self.get_config()
        bot_name = (self._connection.username if self._connection else None) or cfg.bot_username
        if chat.username.casefold() == bot_name.casefold():
            return
        if cfg.allowed_players and chat.username.casefold() not in {p.casefold() for p in cfg.allowed_players}:
            return
        private = event.type == "whisper"
        mention_pattern = rf"(?<![A-Za-z0-9_])@{re.escape(bot_name)}(?![A-Za-z0-9_])"
        mentioned = bool(re.search(mention_pattern, chat.message, re.IGNORECASE))
        addressed = private or mentioned or not cfg.require_mention
        content = re.sub(mention_pattern, "", chat.message, flags=re.IGNORECASE).strip()
        if not content:
            return
        player_id = f"{cfg.server_id}/{chat.username}"
        channel = AdapterChannel(
            channel_id=player_id if private else cfg.server_id,
            channel_type=ChannelType.PRIVATE if private else ChannelType.GROUP,
            channel_name=chat.username if private else f"Minecraft {cfg.server_id}",
        )
        message = AdapterMessage(
            message_id=f"mc-{event.seq}",
            sender=AdapterUser(platform=self.channel_id, user_id=player_id, user_name=chat.username),
            channel=channel,
            content=chat.message[:2048],
            timestamp=event.ts / 1000,
            is_to_me=addressed,
            trigger_mind=addressed,
        )
        command = self._resolve_command(content)
        if addressed and command is not None:
            await self._run_command(command[0], command[1], chat.username, channel)
            # 指令已由频道直执行完毕；消息降级为 history-only 入队，
            # 唤醒思维循环会让 AI 再跑一遍动作（双重执行 + 白烧十几次模型调用）。
            await self.on_message(message.model_copy(update={"trigger_mind": False}))
            return
        await self.on_message(message)

    # 快捷指令：确定性直执行（零模型调用，毫秒级响应），设计参考 mindcraft
    # 的 !command 面。两种触发方式：
    # 1) `!` 前缀 + 别名（!stop / !come / !follow，兼容习惯输入）
    # 2) 自然短句整句匹配（“过来”“跟着我”“停下”，不用打符号）——整句精确
    #    相等才触发，且短语都很短，所以“别过来”“过来帮我看看”不会被误触。
    # 带参数的指令（!give）只走 `!` 前缀，不做整句匹配。
    _COMMAND_ALIASES = {
        "!stop": "stop",
        "!停": "stop",
        "!come": "come",
        "!过来": "come",
        "!follow": "follow",
        "!跟我": "follow",
        "!跟着我": "follow",
        "!stay": "stay",
        "!待着": "stay",
        "!sethome": "sethome",
        "!设家": "sethome",
        "!home": "home",
        "!gohome": "home",
        "!回家": "home",
        "!give": "give",
        "!给": "give",
        "!给我": "give",
        "!mark": "mark",
        "!标记": "mark",
        "!记住这": "mark",
        "!记住这里": "mark",
        "!go": "go",
        "!去": "go",
        "!marks": "marks",
        "!地名": "marks",
    }
    _PHRASE_COMMANDS: tuple[tuple[str, frozenset[str]], ...] = (
        ("stop", frozenset({"停", "停下", "别动", "站住", "stop"})),
        ("come", frozenset({"过来", "过来一下", "来我这", "到我这边来", "过来玩", "come"})),
        ("follow", frozenset({"跟我", "跟着我", "跟我走", "跟着我走", "follow"})),
        ("stay", frozenset({"待着", "待着别动", "在这待着", "stay"})),
        ("home", frozenset({"回家", "go home"})),
    )

    def _resolve_command(self, content: str) -> tuple[str, str] | None:
        parts = content.split(None, 1)
        token = parts[0].casefold()
        if token.startswith("!"):
            command = self._COMMAND_ALIASES.get(token)
            if command is None:
                return None
            return (command, parts[1].strip() if len(parts) > 1 else "")
        text = content.strip().strip("！!。~～…").casefold()
        if len(text) > 8:
            return None
        for command, phrases in self._PHRASE_COMMANDS:
            if text in phrases:
                return (command, "")
        return None

    async def _run_command(self, command: str, args: str, username: str, channel: AdapterChannel) -> None:
        handlers: dict[str, Any] = {
            "stop": lambda: self._cmd_stop(username, channel),
            "come": lambda: self._cmd_come(username, channel),
            "follow": lambda: self._cmd_follow(username, channel),
            "stay": lambda: self._cmd_stay(username, channel),
            "sethome": lambda: self._cmd_sethome(username, channel),
            "home": lambda: self._cmd_home(username, channel),
            "give": lambda: self._cmd_give(args, username, channel),
            "mark": lambda: self._cmd_mark(args),
            "go": lambda: self._cmd_go(args, channel),
            "marks": lambda: self._cmd_marks(),
        }
        try:
            text = await handlers[command]()
        except Exception as exc:
            log(f"Minecraft 快捷指令 {command} 执行异常: {exc}", "WARNING", tag="Minecraft")
            text = "指令没执行成，我可能没连上游戏……你再用话说一遍呗。"
        await self._send_text(channel, text)

    async def _send_text(self, channel: AdapterChannel, text: str) -> None:
        response = await self.forward_message(
            SendRequest(
                adapter_key=self.channel_id,
                channel=channel,
                segments=[SendSegment(type=SegmentType.TEXT, content=text)],
            )
        )
        if not response.success:
            log(f"Minecraft 指令回执发送失败: {response.error}", "WARNING", tag="Minecraft")

    async def _cmd_stop(self, username: str, channel: AdapterChannel) -> str:
        failures = await self._cancel_actions()
        return "停止请求已处理，部分动作未能确认停止。" if failures else "已停止当前动作。"

    async def _cmd_come(self, username: str, channel: AdapterChannel) -> str:
        entity = await self._find_player(username)
        if entity is None:
            return "我看不到你在哪——离我太远或不在同一个世界，靠近一点再喊我。"
        self._spawn_background(self._goto_and_report(channel, entity["position"]))
        return "我来了！"

    async def _cmd_follow(self, username: str, channel: AdapterChannel) -> str:
        entity = await self._find_player(username)
        if entity is None:
            return "我看不到你在哪——离我太远或不在同一个世界，靠近一点再喊我。"
        await asyncio.wait_for(self._call("follow_entity", {"entityId": entity["id"], "range": 2}), timeout=5)
        return "跟着你了，走哪跟哪～（!stop 让我停下）"

    async def _cmd_stay(self, username: str, channel: AdapterChannel) -> str:
        """待着：只停移动，不取消采集等手上的任务（与 !stop 全停不同）。"""
        failures: list[str] = []
        for name in ("stop_pathfinding", "clear_control_states"):
            try:
                await asyncio.wait_for(self._call(name, {}), timeout=3)
            except Exception as exc:
                failures.append(name)
                log(f"Minecraft 待着指令 {name} 失败: {exc}", "WARNING", tag="Minecraft")
        return "好，我待着不动。" if not failures else "我尽量待着……（有控制没清掉，再喊我一次）"

    async def _cmd_sethome(self, username: str, channel: AdapterChannel) -> str:
        state = await asyncio.wait_for(self._call("get_state", {}), timeout=5)
        position = state.get("position") or {}
        if not all(k in position for k in ("x", "y", "z")):
            return "我现在读不到位置，等连稳了再设家。"
        self._home = {k: position[k] for k in ("x", "y", "z")}
        return (
            f"家设好了（{int(position['x'])}, {int(position['y'])}, {int(position['z'])}），"
            "喊 !home 我就回去。"
        )

    async def _cmd_home(self, username: str, channel: AdapterChannel) -> str:
        if self._home is None:
            return "还没设家呢——站在你想让我回的位置喊 !sethome。"
        self._spawn_background(self._goto_and_report(channel, dict(self._home)))
        return "往家走！"

    async def _cmd_give(self, args: str, username: str, channel: AdapterChannel) -> str:
        parts = args.split()
        if not parts:
            return "要给什么？!give <物品名> [数量]，比如 !give oak_log 10"
        wanted = parts[0].casefold().removeprefix("minecraft:")
        count = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
        entity = await self._find_player(username, max_distance=6)
        if entity is None:
            return "你离我太远了——走到我旁边我再给你。"
        inventory = await asyncio.wait_for(self._call("get_inventory", {}), timeout=5)
        matched: tuple[str, int] | None = None
        for item in inventory.get("items") or []:
            name = str(item.get("name") or "").casefold().removeprefix("minecraft:")
            if name == wanted:
                matched = (str(item["name"]), int(item.get("count") or 0))
                break
        if matched is None:
            return f"我背包里没有 {parts[0]}……你问我背包里有啥我就报给你。"
        item_name, available = matched
        give = min(count, available)
        await asyncio.wait_for(self._call("toss_item", {"item": item_name, "count": give}), timeout=5)
        return f"给你 {give} 个{item_name}，接着！"

    async def _cmd_mark(self, args: str) -> str:
        """!mark <名字>：把 bot 当前位置记为地名（地名记忆，零模型调用）。"""
        name = args.strip()
        if not re.fullmatch(r"[^\s！!]{1,16}", name):
            return "地名要 1~16 个字符、不带空格和感叹号，比如 !mark 矿洞口"
        state = await asyncio.wait_for(self._call("get_state", {}), timeout=5)
        position = state.get("position") or {}
        if not all(k in position for k in ("x", "y", "z")):
            return "我现在读不到位置，等连稳了再记。"
        self._marks[name.casefold()] = {"name": name, "pos": {k: position[k] for k in ("x", "y", "z")}}
        return f"记住啦：『{name}』= ({int(position['x'])}, {int(position['y'])}, {int(position['z'])})。喊 !去 {name} 我就到。"

    async def _cmd_go(self, args: str, channel: AdapterChannel) -> str:
        """!go <名字>：走到记住的地名。"""
        name = args.strip()
        mark = self._marks.get(name.casefold())
        if mark is None:
            known = "、".join(m["name"] for m in self._marks.values())
            if known:
                return f"我没记住过『{name}』。记住过：{known}"
            return "还没记住任何地方——站在目标位置喊 !mark 名字"
        self._spawn_background(self._goto_and_report(channel, dict(mark["pos"])))
        return f"去『{mark['name']}』！"

    async def _cmd_marks(self) -> str:
        if not self._marks:
            return "还没记住任何地方——站在目标位置喊 !mark 名字"
        lines = [
            f"『{m['name']}』({int(m['pos']['x'])}, {int(m['pos']['y'])}, {int(m['pos']['z'])})"
            for m in self._marks.values()
        ]
        return "记住的地方：" + "、".join(lines)

    async def _find_player(self, username: str, max_distance: int = 64) -> dict[str, Any] | None:
        """按游戏名找玩家实体；找不到（太远/不同维度/不在线）返回 None。"""
        result = await asyncio.wait_for(
            self._call("find_nearest_entity", {"username": username, "type": "player", "maxDistance": max_distance}),
            timeout=5,
        )
        return None if result.get("found") is False else result

    def _spawn_background(self, coro: Any) -> None:
        """后台任务记账，防 GC 回收；测试可 drain self._background 等待完成。"""
        task = asyncio.create_task(coro, name="channel.minecraft.cmd")
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _goto_and_report(self, channel: AdapterChannel, pos: dict[str, Any]) -> None:
        """!come 的寻路在后台跑（最长 90 秒），到达或失败都回告玩家。"""
        try:
            await self._call(
                "goto",
                {
                    "goalType": "near",
                    "x": pos["x"],
                    "y": pos["y"],
                    "z": pos["z"],
                    "range": 2,
                    "timeout": 90000,
                },
            )
            await self._send_text(channel, "我到了！")
        except Exception as exc:
            log(f"Minecraft goto 玩家失败: {exc}", "WARNING", tag="Minecraft")
            await self._send_text(channel, "我走不过去，好像被卡住了……你用 !stop 让我停下再想想办法。")

    async def _cancel_actions(self) -> list[str]:
        """分别尝试取消与释放控制，一个工具失败或超时不阻断其他停止操作。"""
        failures: list[str] = []
        for name in ("cancel_task", "stop_pathfinding", "clear_control_states", "cancel_collect"):
            try:
                await asyncio.wait_for(self._call(name, {}), timeout=3)
            except Exception as exc:
                failures.append(name)
                log(f"Minecraft 停止工具 {name} 失败: {exc}", "WARNING", tag="Minecraft")
        return failures

    async def forward_message(self, request: SendRequest) -> SendResponse:
        try:
            if any(seg.type != SegmentType.TEXT for seg in request.segments):
                raise ValueError("Minecraft 频道仅支持文字")
            cfg = self.get_config()
            private = request.channel.channel_type == ChannelType.PRIVATE
            target = request.channel.channel_id
            username = ""
            if private:
                prefix = f"{cfg.server_id}/"
                if not target.startswith(prefix) or not re.fullmatch(r"[A-Za-z0-9_]{1,16}", target[len(prefix) :]):
                    raise ValueError("Minecraft 私聊目标不属于当前世界")
                username = target[len(prefix) :]
            elif target != cfg.server_id:
                raise ValueError("Minecraft 公共聊天目标不属于当前世界")
            text = "\n".join(seg.content for seg in request.segments)
            limit = min(240, 256 - len(f"/tell {username} ")) if private else 240
            chunks = split_chat(text, limit=limit)
            if not chunks:
                raise ValueError("Minecraft 聊天内容为空")
            for chunk in chunks:
                args = {"username": username, "message": chunk} if private else {"message": chunk}
                await self._call("whisper" if private else "chat", args)
            return SendResponse(success=True, message_id=f"mc-out-{time.time_ns()}")
        except Exception as exc:
            return SendResponse(success=False, error=str(exc))

    async def get_self_info(self) -> ChannelUser:
        name = (self._connection.username if self._connection else None) or self.get_config().bot_username
        return ChannelUser(platform=self.channel_id, user_id=name, user_name=name, is_bot=True)

    async def get_channel_info(self, channel_id: str) -> ChannelInfo:
        group = channel_id == self.get_config().server_id
        return ChannelInfo(
            channel_id=channel_id,
            channel_name=f"Minecraft {channel_id}",
            channel_type=ChannelType.GROUP if group else ChannelType.PRIVATE,
        )

    def is_known_group(self, channel_id: str) -> bool:
        return channel_id == self.get_config().server_id

    async def health_check(self) -> HealthStatus:
        state = self._connection.state if self._connection else "等待 MCP"
        return HealthStatus(
            healthy=self._last_success is not None and not self._last_error,
            detail=f"机器人: {state}",
            last_error=self._last_error or None,
            last_success_at=self._last_success,
        )


CHANNEL_CLASS = MinecraftChannel
