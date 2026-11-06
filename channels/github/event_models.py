"""GitHub 入站与渲染事件的数据结构。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class EventPriority(str, Enum):
    """事件优先级(决定推送策略,见 pipeline)。"""

    IMMEDIATE = "immediate"  # 立即唤醒 AI
    NORMAL = "normal"        # 防抖聚合窗后唤醒
    DIGEST = "digest"        # 静默计数,每日 digest 汇总
    IGNORE = "ignore"        # 丢弃(不渲染不计数)




@dataclass
class RawEvent:
    """规范化输入:webhook 与轮询两种形态先归一为本结构,再进渲染器。"""

    name: str                    # 事件名(小写,如 "push" / "issues")
    action: str                  # 动作(如 "opened";无 action 的事件为空串)
    repo_full_name: str          # "owner/repo"(取不到为空串)
    actor: str                   # 触发者 login
    occurred_at: float           # epoch 秒
    payload: Dict[str, Any]      # 原始 payload(webhook body / events API 的 payload)
    event_id: str = ""           # 去重/游标键(events API 的 id / webhook delivery id)
    source: str = "webhook"
    account: str = ""


@dataclass
class GitHubEvent:
    """规范化事件(渲染器产出;pipeline 消费)。"""

    repo_full_name: str
    event_name: str
    action: str
    actor: str
    occurred_at: float
    title: str                   # 一行标题(已含关键信息)
    url: str = ""
    priority: EventPriority = EventPriority.NORMAL
    body_digest: str = ""        # 外部正文摘要(已 sanitize)
    event_id: str = ""
    source: str = "webhook"
    local_path: str = ""         # 由 pipeline 按订阅配置回填
    mention: bool = False        # 由 pipeline 按 watch_mentions_of 回填
    priority_override: Optional[EventPriority] = None  # 渲染器按数据内容定级(如 PR 未合并关闭)
    extra: Dict[str, Any] = field(default_factory=dict)  # 渲染器附加数据(如 push 的 branch/size)

    @property
    def event_key(self) -> str:
        return f"{self.event_name}.{self.action}" if self.action else self.event_name
