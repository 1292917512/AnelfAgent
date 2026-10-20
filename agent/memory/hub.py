"""主标签记忆的骨架、逐轮读取与有界渲染。

Model Experience：模型在 provider 动态区读取 main:hub 的最新索引与工作窗口。
Token：沿用 memory_hub_inject_max_chars 预算，超长时保留首尾并标记省略。
缓存：工具链之后注入；内容未变时字节稳定，不重写稳定前缀或历史。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from core.config import register_configs_safe
from core.context_provider import ContextProviderRegistry, ProviderMeta
from core.log import log
from core.sanitizer import sanitize_for_context, truncate_middle

from .memory_types import MemoryEntry, MemoryType

if TYPE_CHECKING:
    from .memory_store import MemoryStore

# 保留标签：主标签记忆的唯一标识（main: 前缀不参与标签联想网络，属结构性标签）
HUB_TAG = "main:hub"

HUB_TAGS = ["type:permanent", HUB_TAG]

# 骨架只声明段结构，段义与维护纪律在铁律（stable 层同前缀可见，不在此重复）
HUB_SKELETON = "## 标签索引\n\n## 即时记录\n"

_INJECT_HEADER = "[主标签记忆] 索引中枢与工作窗口"


async def get_hub_entry(store: "MemoryStore") -> Optional[MemoryEntry]:
    """读取主标签记忆条目（不存在返回 None，异常静默降级）。"""
    try:
        entries = await store.search_by_tags([HUB_TAG], limit=1)
    except Exception as exc:
        log(f"主标签记忆读取失败: {exc}", "DEBUG", tag="记忆")
        return None
    for entry in entries:
        if entry.memory_type == MemoryType.PERMANENT:
            return entry
    return None


async def ensure_hub(store: "MemoryStore") -> bool:
    """确保主标签记忆存在（缺失/被清理时重建骨架），返回是否发生创建。

    幂等；由心跳维护段周期性调用兜底（AI 侧误操作的自愈通道）。
    """
    if await get_hub_entry(store) is not None:
        return False
    try:
        entry = MemoryEntry(
            memory_type=MemoryType.PERMANENT,
            content=HUB_SKELETON,
            tags=list(HUB_TAGS),
            importance=1.0,
        )
        await store.add(entry, actor="system")
        from .embedding import wake_embedding_worker
        wake_embedding_worker()
        log("主标签记忆骨架已创建", "INFO", tag="记忆")
        return True
    except Exception as exc:
        log(f"主标签记忆创建失败: {exc}", "WARNING", tag="记忆")
        return False


async def load_hub_block(store: "MemoryStore") -> str:
    """渲染最新工作窗口，超预算保留首尾并标记省略。"""
    entry = await get_hub_entry(store)
    if entry is None:
        return ""
    content = entry.content.strip()
    if not content:
        return ""
    from core.config import get_config_int
    budget = get_config_int("memory_hub_inject_max_chars", 3000)
    content = truncate_middle(content, max(0, budget), head_ratio=0.5,
                              marker="\n……（超出注入预算已截断，请精简后整段覆写）\n")
    return sanitize_for_context(f"{_INJECT_HEADER}\n{content}", max_chars=len(_INJECT_HEADER) + 1 + max(0, budget))


def register_provider(store: "MemoryStore") -> None:
    """由组合根绑定存储；每次模型调用读取权威条目，不保留第二份工作状态。"""
    async def provide(scope: str) -> str | None:
        return await load_hub_block(store) or None

    ContextProviderRegistry.register(ProviderMeta(
        name="memory_hub", priority=31, retention_priority=10, max_tokens=800,
        inject_key="memory_hub_inject_enabled", provide_fn=provide,
        description="主标签记忆：最新资产索引与工作窗口",
    ))


register_configs_safe({"memory/recall": {
    "memory_hub_inject_enabled": {"description": "每轮注入主标签记忆工作窗口", "default": True},
    "memory_hub_inject_max_chars": {
        "description": "主标签记忆的字符预算，超出时保留首尾并标记省略",
        "default": 3000, "advanced": True, "unit": "字符",
    },
}})
