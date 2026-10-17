"""召回准备结果；仅传递事实，不承载执行服务。"""

from dataclasses import dataclass, field


@dataclass
class RecollectionInputs:
    profiles: list[dict] = field(default_factory=list)
    memories: list[dict] = field(default_factory=list)
    relations: list[dict] = field(default_factory=list)
    cross_channel: list[dict] = field(default_factory=list)
    skills: list[dict] = field(default_factory=list)
    profile_blocks: list[str] = field(default_factory=list)
    hub: str = ""
    directives: str = ""
    repeat_hint: str = ""
    summary: dict | None = None
