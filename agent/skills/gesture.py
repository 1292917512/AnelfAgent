"""用户技能手势 — /name 确定性触发。

语义：用户消息正文以 ``/skill-name`` 开头（整词，后跟空白或行尾）时，
跳过语义评分强制加载该技能——用户明确知道要用什么时不应依赖模糊匹配的
概率命中。与现有双路评分（关键词 0.4 + 语义 0.6）并存：手势为确定性
入口，评分为模糊兜底。

防伪造：手势只在
``Mind.accept_feel`` 的真实外部消息路径检测（应答入队的消息），工具结果/
网页内容/子代理输出里出现的 "/xxx" 不构成手势。

Model Experience：手势命中的技能正文经 recollection 注入 volatile 层
（尾部动态区，VOL>30），不触碰缓存前缀；token 影响等同一次常规技能匹配。
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
