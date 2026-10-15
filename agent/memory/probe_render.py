"""补充回忆的有界渲染，按消费会话去重并保留来源标签。

模型可见：分节事实沿用记忆归属标签，超预算时提示主动 recall。
Token 影响：整轮共享字符预算；缓存影响：未追加时输出字节不变。
"""

from dataclasses import dataclass, replace

from core.sanitizer import sanitize_for_context

_HEADER = (
    "[系统注入·记忆召回·续] 与当前对话相关的补充资料"
    "（与此前注入的记忆已去重，仅作参考，不代表当前任务进程）："
)
_OVERFLOW = "\n（其余内容超出预算，可经 recall 主动获取）"


@dataclass(frozen=True)
class RecallDelta:
    """待呈现的事实及其去重标识。"""

    section: str
    text: str
    result_id: str = ""
    snippet: str = ""
    edge_id: int | None = None


class RecallBuffer:
    """按到达顺序追加事实，预算耗尽后保留已呈现的内容。"""

    def __init__(self, max_chars: int) -> None:
        self.max_chars = max(400, max_chars)
        self._body = ""
        self._section = ""
        self._overflow = False
        self.facts: list[RecallDelta] = []

    def append(self, delta: RecallDelta) -> bool:
        """接纳完整事实；首条过长时保留有界摘录，未呈现的条目不入账。"""
        text = sanitize_for_context(delta.text).strip()
        if not text:
            return False
        heading = f"\n▸ {delta.section}" if delta.section != self._section else ""
        prefix = heading + "\n  - "
        remaining = self.max_chars - len(_HEADER) - len(self._body) - len(prefix) - len(_OVERFLOW)
        if len(text) > remaining:
            self._overflow = True
            if self._body or remaining < 80:
                return False
            text = text[:remaining - 1] + "…"
        self._body += prefix + text
        self._section = delta.section
        self.facts.append(replace(delta, text=text))
        return True

    def render(self) -> str:
        """返回整个轮次的补充记忆；空缓冲不产生注入。"""
        if not self._body:
            return ""
        return _HEADER + self._body + (_OVERFLOW if self._overflow else "")
