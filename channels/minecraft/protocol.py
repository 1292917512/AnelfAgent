"""开源 Mineflayer MCP 的事件契约与游戏文本分段。"""

from typing import Any

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
