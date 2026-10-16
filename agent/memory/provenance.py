"""记忆出处的保存、校验与读取，不从主题或人物标签猜测发言来源。

模型看到摘要的材料会话、消息范围和记录方式；缺失出处明确标为未知。
每条召回只展示简短来源，完整材料指针按 mem ID 读取；不新增模型调用。
出处随动态召回和工具结果进入上下文，不改变稳定前缀的装配位置。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator


class MemoryOrigin(BaseModel):
    """产生记忆的材料窗口；摘要指针不表示逐字引用或每句均由用户确认。"""

    kind: Literal["conversation_summary", "agent_note"]
    scope: str
    message_ids: list[Annotated[int, Field(strict=True, gt=0)]] = Field(default_factory=list)
    started_at: float = Field(default=0, ge=0, lt=253402300800, allow_inf_nan=False)
    ended_at: float = Field(default=0, ge=0, lt=253402300800, allow_inf_nan=False)

    @field_validator("scope")
    @classmethod
    def valid_scope(cls, value: str) -> str:
        from agent.messages import parse_entity_scope
        if value != "_global" and not parse_entity_scope(value)[0]:
            raise ValueError("材料会话无效")
        return value


def origins_from(metadata: Mapping[str, Any]) -> list[MemoryOrigin]:
    """读取合法出处，旧记录缺失时保持未知。"""
    raw = metadata.get("origins", [])
    if not isinstance(raw, list):
        return []
    origins: list[MemoryOrigin] = []
    for value in raw:
        try:
            origin = MemoryOrigin.model_validate(value)
        except (ValidationError, TypeError):
            continue
        if origin.scope and origin not in origins:
            origins.append(origin)
    return origins


def capture_origin(scope: str, messages: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """保存实际送入提取器的材料指针，不复制正文或信任模型生成的 ID。"""
    ids = [m["id"] for m in messages if type(m.get("id")) is int and m["id"] > 0]
    times = [int(m["ts_ns"]) / 1e9 for m in messages if m.get("ts_ns")]
    origin = MemoryOrigin(
        kind="conversation_summary", scope=scope, message_ids=list(dict.fromkeys(ids)),
        started_at=min(times, default=0), ended_at=max(times, default=0),
    )
    return {"origins": [origin.model_dump(exclude_defaults=True)]}


def note_origin(scope: str) -> dict[str, Any]:
    """标记 AI 主动记录发生的会话，不把当前对话对象当成事实发言者。"""
    return {"origins": [MemoryOrigin(kind="agent_note", scope=scope).model_dump(exclude_defaults=True)]}


def merge_metadata(*items: Mapping[str, Any]) -> dict[str, Any]:
    """合并更新元数据，保留来源谱系与最严格的隐私级别。"""
    merged: dict[str, Any] = {}
    origins: list[MemoryOrigin] = []
    sensitivity = "normal"
    linked: list[int] = []
    levels = {"normal": 0, "private": 1, "secret": 2}
    for item in items:
        merged.update(item)
        for reference in item.get("linked_to", []) if isinstance(item.get("linked_to"), list) else []:
            if type(reference) is int and reference > 0 and reference not in linked:
                linked.append(reference)
        for origin in origins_from(item):
            if origin not in origins:
                origins.append(origin)
        level = str(item.get("sensitivity", "normal"))
        if levels.get(level, 0) > levels[sensitivity]:
            sensitivity = level
    if origins:
        merged["origins"] = [o.model_dump(exclude_defaults=True) for o in origins]
    if sensitivity != "normal":
        merged["sensitivity"] = sensitivity
    if linked:
        merged["linked_to"] = linked[:5]
    return merged


def recall_provenance(metadata: Mapping[str, Any], *, source: str = "") -> dict[str, Any]:
    """召回只携带出处与时间语义，不将运行状态等任意元数据注入模型。"""
    result = {key: metadata[key] for key in ("activity_date", "temporal_scope") if metadata.get(key)}
    origins = origins_from(metadata)
    if origins:
        result["origins"] = [o.model_dump(exclude_defaults=True) for o in origins]
    if source:
        result["recorded_by"] = source
    return result


def visible_provenance(provenance: Mapping[str, Any]) -> dict[str, Any]:
    """工具展示最近三份出处，更多来源保留在原记忆的元数据中。"""
    result = recall_provenance(provenance, source=str(provenance.get("recorded_by", "")))
    origins = result.get("origins", [])
    if len(origins) > 3:
        result["origins"] = origins[-3:]
        result["origin_count"] = len(origins)
    result["attribution"] = "recorded" if origins else "unrecorded"
    return result
