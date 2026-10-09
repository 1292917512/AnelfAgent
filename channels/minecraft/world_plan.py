"""Minecraft 世界规划的持久化模型。

规划是自主生活循环和实际世界之间的稳定契约：先选定家址，再固定矿洞入口，
后续轮次不能因为模型临时看到一个方块就另挖一处。
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MineDirection = Literal["north", "south", "east", "west"]
HomePhase = Literal["foundation", "walls", "roof", "interior", "complete"]


class WorldPosition(BaseModel):
    """世界中的整数方块坐标。"""

    model_config = ConfigDict(extra="forbid")

    x: int
    y: int
    z: int


class WorldPlan(BaseModel):
    """自主生活使用的家址、模板和固定矿道入口。"""

    model_config = ConfigDict(extra="ignore")

    version: Literal[1] = 1
    world_id: str = Field(min_length=1)
    world_signature: str = ""
    terrain: Literal["plains_like"] = "plains_like"
    home_template: Literal["starter_cabin_v1"] = "starter_cabin_v1"
    home_phase: HomePhase = "foundation"
    activity_index: int = Field(default=0, ge=0)
    home: WorldPosition
    mine_entrance: WorldPosition | None = None
    mine_direction: MineDirection | None = None
    updated_at: str = ""

    def prompt_json(self) -> str:
        """返回给模型的稳定、紧凑规划快照。"""
        return json.dumps(self.model_dump(mode="json", exclude_none=True), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_marker(cls, world_id: str, marker: dict[str, Any]) -> "WorldPlan":
        """从模型输出的 WORLD_PLAN 标记构造并校验规划。"""
        data = dict(marker)
        data["world_id"] = world_id
        data["updated_at"] = datetime.now(UTC).isoformat()
        return cls.model_validate(data)


class WorldPlanStore:
    """按世界隔离保存规划，损坏文件不会阻塞频道启动。"""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or Path(__file__).with_name("world_plans")

    def _path(self, world_id: str) -> Path:
        digest = hashlib.sha256(world_id.encode("utf-8")).hexdigest()
        return self._root / f"{digest}.json"

    def load(self, world_id: str) -> WorldPlan | None:
        path = self._path(world_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            plan = WorldPlan.model_validate(raw)
            if plan.world_id != world_id:
                return None
            return plan
        except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError):
            return None

    def save(self, plan: WorldPlan) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._path(plan.world_id)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
