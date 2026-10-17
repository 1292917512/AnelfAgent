"""解析用户通过 /name 发起的显式技能调用。

语义：用户消息正文以 ``/skill-name`` 开头（整词，后跟空白或行尾）时，
跳过语义评分强制加载该技能——用户明确知道要用什么时不应依赖模糊匹配的
概率命中。显式调用与关键词、语义匹配并存，不在本模块执行技能步骤。

防伪造：手势只在
``Mind.accept_feel`` 的真实外部消息路径检测（应答入队的消息），工具结果/
网页内容/子代理输出里出现的 "/xxx" 不构成手势。

模型可见：解析结果由 SkillRuntime 按会话登记，召回时提供所选技能正文。
Token 影响：显式调用提供有预算上限的正文，普通匹配仅提供候选摘要。
缓存影响：技能材料交由上下文层注入动态召回区，不修改稳定前缀。
"""

from __future__ import annotations

import re
from typing import Optional

# 手势正则：行首 /name（字母/数字开头，可含连字符/下划线），后随空白或行尾。
# 对齐 skill_store.normalize_name 的字符集，避免解析出永远不存在的名字。
_GESTURE_RE = re.compile(r"^/([A-Za-z0-9][A-Za-z0-9_-]*)(?=\s|$)")


def parse_skill_gesture(text: str) -> Optional[str]:
    """从已剥离元数据标签的消息正文解析 /name 手势，返回技能名或 None。

    输入应为 ``strip_message_meta_tags`` 处理后的纯正文（[time:][uid:] 等
    到达标签前缀已剥离）；非 / 开头、/ 后紧跟空格、名字含非法字符均不命中。
    """
    if not text:
        return None
    stripped = text.lstrip()
    if not stripped.startswith("/"):
        return None
    m = _GESTURE_RE.match(stripped)
    return m.group(1) if m else None
