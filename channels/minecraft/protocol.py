"""开源 Mineflayer MCP 的事件契约与游戏文本分段。"""

from typing import Any, Literal

from pydantic import BaseModel, Field

ONLINE_STATE = "online"


class SurvivalStatus(BaseModel):
    """执行器自持有的本地反射设置与会话标识。"""

    version: Literal[1]
    runtime_id: str = Field(alias="runtimeId", min_length=1)
    enabled: bool
    interval_ms: int = Field(alias="intervalMs", ge=100, le=10000)


class ConnectionStatus(BaseModel):
    """机器人连接状态中的频道所需字段。"""

    state: str = Field(alias="status")
    username: str | None = None
    version: str | None = None
    survival: SurvivalStatus | None = None


class SurvivalProgress(BaseModel):
    """本地观察与自救事实；移动结束不等于危险已经解除。"""

    id: str = Field(min_length=1)
    runtime_id: str = Field(alias="runtimeId", min_length=1)
    kind: Literal["death", "danger", "drowning", "burning", "trapped", "hurt", "hostile", "stuck"]
    phase: Literal["dead", "respawned", "started", "moved", "clear", "blocked", "held", "cancelled"]

    def announcement(self) -> str | None:
        cause = {"drowning": "头部入水", "burning": "着火或接触火/熔岩", "trapped": "头部被方块挡住",
                 "hurt": "掉血", "hostile": "敌对生物逼近", "stuck": "移动卡住"}.get(self.kind, "危险")
        return {
            "dead": "服务器已确认我死亡，等待重生；原任务已中断。",
            "respawned": "我已经重生，原任务保持停止。",
            "started": f"检测到{cause}，我已中断原动作，正在本地自救。",
            "clear": "当前危险迹象已消失，我没有恢复之前的任务。",
            "blocked": f"检测到{cause}，本地自救受阻；我不会盲挖或继续原任务。",
            "held": f"检测到{cause}；目前处于停止或暂停状态，没有自动移动。",
        }.get(self.phase)


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


class ProductionProgress(BaseModel):
    """制作任务的库存与收尾事实；已有工具不计入新制作数量。"""

    id: str = Field(min_length=1)
    action_id: str = Field(alias="actionId", min_length=1)
    phase: Literal["running", "completed", "blocked", "cancelled", "interrupted"]
    item: str = Field(pattern=r"^[a-z0-9_]+$")
    created: int = Field(ge=0)
    available: int = Field(ge=0)
    required: int = Field(ge=1)
    reused: int = Field(ge=0)
    inventory_clean: bool = Field(alias="inventoryClean")

    def announcement(self) -> str | None:
        """只播报终态；未确认数量或收尾时不得称制作成功。"""
        if self.phase == "running":
            return None
        complete = self.phase == "completed" and self.available >= self.required and self.inventory_clean
        label = "制作任务已完成" if complete else {
            "cancelled": "制作任务已取消", "interrupted": "制作任务已中断",
        }.get(self.phase, "制作任务受阻，已停止")
        name = {"wooden_pickaxe": "木镐", "wooden_axe": "木斧", "wooden_shovel": "木锹",
                "wooden_hoe": "木锄", "wooden_sword": "木剑", "crafting_table": "工作台", "stick": "木棍"}.get(self.item, self.item)
        cleanup = "合成格和光标已收尾" if self.inventory_clean else "尚未确认临时材料收尾，不能继续合成"
        return f"{label}：新合成 {self.created} 个{name}，复用原有 {self.reused} 个，背包现有 {self.available} 个；{cleanup}。"


class SupplyItemProgress(BaseModel):
    """单类物品的容器/背包对账，不把请求量当实际转移量。"""

    item: str = Field(pattern=r"^[a-z0-9_]+$")
    deposited: int = Field(ge=0)
    withdrawn: int = Field(ge=0)
    available: int = Field(ge=0)
    target: int = Field(ge=0)
    verified: bool


class SupplyProgress(BaseModel):
    """补给终态与真实库存收尾事实。"""

    id: str = Field(min_length=1)
    action_id: str = Field(alias="actionId", min_length=1)
    phase: Literal["running", "completed", "blocked", "cancelled", "interrupted"]
    confirmed: bool
    inventory_clean: bool = Field(alias="inventoryClean")
    items: list[SupplyItemProgress] = Field(max_length=16)
    failure_code: str = Field(default="", alias="failureCode")

    def announcement(self) -> str | None:
        if self.phase == "running":
            return None
        verified = self.confirmed and all(item.verified for item in self.items)
        complete = self.phase == "completed" and verified and self.inventory_clean and bool(self.items) and all(
            item.available == item.target for item in self.items
        )
        label = "补给任务已完成" if complete else {
            "cancelled": "补给任务已取消", "interrupted": "补给任务已中断",
        }.get(self.phase, "补给任务受阻，已停止")
        names = {"cobblestone": "圆石", "oak_log": "橡木原木", "oak_planks": "橡木板", "stick": "木棍",
                 "bread": "面包", "torch": "火把", "wooden_pickaxe": "木镐", "stone_pickaxe": "石镐", "iron_pickaxe": "铁镐"}
        facts = "；".join(f"{names.get(item.item, item.item)} 存入 {item.deposited}、取出 {item.withdrawn}、现有 {item.available}" for item in self.items[:3])
        if len(self.items) > 3:
            facts += f"；另 {len(self.items) - 3} 类物品见补给状态"
        if not verified:
            cause = {"MISSING_MATERIALS": "箱子物资不足", "INVENTORY_FULL_NO_CHEST": "箱子或背包空间不足",
                     "FORBIDDEN": "不满足物资保留量或使用条件", "NO_WINDOW": "箱子连接已失效",
                     "BUSY": "库存发生变化或尚未收尾"}.get(self.failure_code, "双方库存尚未完整确认")
            facts = f"{cause}，请先查看补给状态"
        cleanup = "光标已收尾并关闭箱子" if self.inventory_clean else "临时物品收尾尚未确认，不能继续搬运"
        return f"{label}：{facts}；{cleanup}。"


class GatherProgress(BaseModel):
    """采集只认可新入包、返程与交付事实，方块挖掉不代表资源到手。"""

    id: str = Field(min_length=1)
    action_id: str = Field(alias="actionId", min_length=1)
    phase: Literal["running", "completed", "blocked", "cancelled", "interrupted"]
    item: str = Field(pattern=r"^[a-z0-9_]+$")
    requested: int = Field(ge=1)
    dug: int = Field(ge=0)
    gained: int = Field(ge=0)
    deposited: int = Field(ge=0)
    deposit_requested: bool = Field(alias="depositRequested")
    returned: bool
    inventory_clean: bool = Field(alias="inventoryClean")

    def announcement(self) -> str | None:
        if self.phase == "running":
            return None
        complete = (self.phase == "completed" and self.gained >= self.requested and self.returned
                    and self.inventory_clean and (not self.deposit_requested or self.deposited >= self.gained))
        label = "采集任务已完成" if complete else {
            "cancelled": "采集任务已取消", "interrupted": "采集任务已中断",
        }.get(self.phase, "采集任务受阻，已停止")
        name = {"cobblestone": "圆石", "oak_log": "橡木原木", "birch_log": "白桦原木"}.get(self.item, self.item)
        returned = "已回到出发点" if self.returned else "尚未确认回到出发点"
        delivery = f"；确认存入指定箱子 {self.deposited} 个" if self.deposit_requested else ""
        cleanup = "" if self.inventory_clean else "；临时物品收尾尚未确认"
        return (f"{label}：确认挖掉 {self.dug} 个方块，新入包 {self.gained}/{self.requested} 个{name}"
                f"；{returned}{delivery}{cleanup}。")


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
