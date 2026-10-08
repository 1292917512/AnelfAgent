"""开源 Mineflayer MCP 的事件契约与游戏文本分段。"""

from typing import Any, Literal

from pydantic import BaseModel, Field

ONLINE_STATE = "online"


class ConnectionStatus(BaseModel):
    """机器人连接状态中的频道所需字段。"""

    state: str = Field(alias="status")
    username: str | None = None
    version: str | None = None


class GameEvent(BaseModel):
    """执行器环形缓冲区中的离散事件。"""

    seq: int = Field(ge=1)
    ts: int = Field(ge=0)
    type: str
    data: Any = None


class EventBatch(BaseModel):
    """事件读取结果，nextSince 用于增量消费。"""

    events: list[GameEvent]
    next_since: int = Field(alias="nextSince", ge=0)
    dropped: bool = False


class PlayerChat(BaseModel):
    """来自 Java 玩家聊天或私聊的消息。"""

    username: str = Field(pattern=r"^[a-zA-Z0-9_]{1,16}$")
    message: str = Field(min_length=1)


class MineProgress(BaseModel):
    """执行器确认的矿洞任务事实，不将已挖方块数当成物品收入。"""

    id: str = ""
    action_id: str = Field(default="", alias="actionId")

    phase: Literal["running", "returning", "pausing", "paused", "completed", "partial", "blocked", "cancelled", "interrupted"]
    steps: int = Field(ge=0)
    dug: int = Field(ge=0)
    item: str = Field(pattern=r"^[a-z0-9_]+$")
    gained: int = Field(ge=0)
    returned: bool

    def announcement(self) -> str | None:
        """只播报终态，明确区分完成、受阻、取消和返程确认。"""
        labels = {
            "completed": "矿洞任务已完成", "partial": "矿洞任务提前结束",
            "blocked": "矿洞任务遇到障碍，已停止", "cancelled": "矿洞任务已取消",
            "interrupted": "矿洞任务被中断，需要检查后继续",
            "paused": "矿洞任务已在安全检查点暂停，进度保留",
        }
        label = labels.get(self.phase)
        if label is None:
            return None
        returned = "已沿通道回到入口" if self.returned else "尚未确认回到入口，请查看任务位置"
        return f"{label}：通道推进 {self.steps} 格，确认挖掉 {self.dug} 个方块，背包净增加 {self.gained} 个 {self.item}；{returned}。"


class ActionOrigin(BaseModel):
    """宿主生成的动作来源，用于区分新请求与停止前的迟到事件。"""

    producer: str
    epoch: int
    actor: str
    world_id: str = Field(alias="worldId")


class ActionProgress(BaseModel):
    """频道只消费动作阶段及来源，其余详情保留在执行器查询中。"""

    phase: str
    origin: ActionOrigin | None = None


def split_chat(text: str, limit: int = 240) -> list[str]:
    """按 Java 聊天的 UTF-16 字符长度分段，保留换行边界。"""
    if limit < 2:
        raise ValueError("聊天分段长度至少为 2")
    chunks: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        part = ""
        units = 0
        for char in line:
            size = len(char.encode("utf-16-le")) // 2
            if units + size > limit:
                chunks.append(part)
                part, units = "", 0
            part += char
            units += size
        if part:
            chunks.append(part)
    if any(chunk.lstrip().startswith("/") for chunk in chunks):
        raise ValueError("游戏聊天回复不能作为斜杠命令发送")
    return chunks
