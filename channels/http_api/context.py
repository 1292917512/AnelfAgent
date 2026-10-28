"""外部应用的会话动态快照，复用公共 provider 与频道的完整 scope。

Model Experience: 通过 ContextProviderRegistry 在每次模型调用前读取当前会话快照，
与 AI 桌面等提供者一起注入动态尾部；更新不触发思考、不写入对话历史。
Token effect: 每份文本限 16K 字符，另受公共 provider token 总预算约束；skills 字段转为内部标签，正文由统一技能链加载。
Cache effect: 更新不改 stable/summary/conversation 前缀。
每个完整 scope 只保留一份快照，新写入覆盖旧值；清除或频道停止后移除。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from core.context_provider import ContextProviderRegistry, ProviderMeta, ProviderSnapshot
from core.tags import tag_label


@dataclass(frozen=True)
class HttpContextSnapshot:
    """连接的唯一快照；外部字段保留原样，内部标签只在注入时生成。"""

    dynamic_context: str = ""
    skills: tuple[str, ...] = ()


class HttpSessionContexts:
    """频道持有的内存快照，读写均在宿主事件循环内完成，无额外后台任务。"""

    provider_name = "http_api_context"

    def __init__(self, *, enabled: Callable[[], bool], capacity: Callable[[], int]) -> None:
        self._enabled = enabled
        self._capacity = capacity
        self._entries: dict[str, HttpContextSnapshot] = {}

    def register(self) -> None:
        """频道启动后接入公共动态上下文采集链。"""
        ContextProviderRegistry.register(ProviderMeta(
            name=self.provider_name, priority=50, max_tokens=4000,
            instance=self, inject_key="http_api_context_inject",
            description="HTTP 外部应用的会话动态快照",
        ))

    def close(self) -> None:
        """频道停止时清除快照并注销自身，避免热重载残留。"""
        self._entries.clear()
        if any(meta.name == self.provider_name and meta.instance is self for meta in ContextProviderRegistry.get_all()):
            ContextProviderRegistry.unregister(self.provider_name)

    def get(self, scope: str) -> HttpContextSnapshot:
        """读取当前连接的唯一快照，未登记时返回空快照。"""
        return self._entries.get(scope, HttpContextSnapshot())

    def update(self, scope: str, *, dynamic_context: str, skills: Sequence[str] = ()) -> HttpContextSnapshot:
        """覆盖该连接快照；容量只限制新增连接，文本和技能均空时清除。"""
        snapshot = HttpContextSnapshot(dynamic_context, tuple(skills))
        if dynamic_context or skills:
            if scope not in self._entries and len(self._entries) >= self._capacity():
                raise OverflowError("外部上下文会话已达上限，请清除不再使用的会话")
            self._entries[scope] = snapshot
        else:
            self._entries.pop(scope, None)
        return snapshot

    def clear(self, scope: str) -> None:
        """清除指定会话的快照，不触碰聊天历史。"""
        self._entries.pop(scope, None)

    async def provide(self, scope: str) -> ProviderSnapshot | None:
        """公共 provider 读取最新内存快照，返回值只归属当前完整 scope。"""
        if not self._enabled():
            return None
        snapshot = self.get(scope)
        prefix = "".join(tag_label("skill", name) for name in snapshot.skills)
        content = f"{prefix}\n{snapshot.dynamic_context}" if prefix else snapshot.dynamic_context
        return ProviderSnapshot(content=f"[外部应用动态上下文]\n{content}") if content else None
