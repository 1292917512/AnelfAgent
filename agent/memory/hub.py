"""主标签记忆（main:hub）— AI 的随身索引中枢与长工作流即时记录窗口。

定位：标签系统是连接全部记忆子系统的索引总线，主标签记忆是这条总线
在上下文中的常驻投影——一条带保留标签 main:hub 的 PERMANENT 记忆，
每回复周期经专属注入块置顶呈现，不占永久记忆 pin 名额。

职责切分：
- 系统侧（本模块）：骨架创建与自愈（ensure_hub）、注入渲染（load_hub_block）、
  预算截断；使用规则在 agent.mind.usage_rules.MEMORY_RULES（stable 层铁律）
- AI 侧：经 memorize 携带 type:permanent + main:hub 整段覆写维护内容
  （索引段精细维护、即时区完工即清理），禁止归档/删除（forget 侧有拦截）

Model Experience：
1. 模型看到什么：context 层独立消息 [主标签记忆]（vol 36，尾部动态区最前段），
   内容为该记忆全文（超预算保索引段截尾部）
2. token 影响：上限 memory_hub_inject_max_chars（默认 3000），空骨架约百字
3. 缓存影响：独立消息块，AI 更新只漂移自身；置于 context 层（35）与
   status 层（40）之间，不触碰 stable/summary/conversation 前缀
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from core.log import log

from .memory_types import MemoryEntry, MemoryType

if TYPE_CHECKING:
    from .memory_store import MemoryStore

# 保留标签：主标签记忆的唯一标识（main: 前缀不参与标签联想网络，属结构性标签）
HUB_TAG = "main:hub"

HUB_TAGS = ["type:permanent", HUB_TAG]

HUB_SKELETON = (
    "## 标签索引\n"
    "（活跃标签 → 各记忆系统入口的映射，精细维护、随用随更；"
    "标签前缀语义与纪律见 stable 层「记忆体系铁律」）\n"
    "\n"
    "## 即时记录\n"
    "（长工作流的进行中状态：当前在做什么、进行到哪步、下一步是什么；完工即清理）\n"
)

_INJECT_HEADER = (
    "[主标签记忆] 你的索引中枢与工作窗口（每轮置顶；"
    "更新方式与维护纪律见「记忆体系铁律」）"
)


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
        await store.add(entry)
        from .embedding import wake_embedding_worker
        wake_embedding_worker()
        log("主标签记忆骨架已创建", "INFO", tag="记忆")
        return True
    except Exception as exc:
        log(f"主标签记忆创建失败: {exc}", "WARNING", tag="记忆")
        return False


async def load_hub_block(store: "MemoryStore") -> str:
    """渲染主标签记忆的注入块文本（超预算保头截尾），缺失/异常返回空串。"""
    entry = await get_hub_entry(store)
    if entry is None:
        return ""
    content = entry.content.strip()
    if not content:
        return ""
    from core.config import get_config_int
    budget = get_config_int("memory_hub_inject_max_chars", 3000)
    if len(content) > budget:
        content = (
            content[:budget].rstrip()
            + "\n……（超出注入预算已截断，请精简后整段覆写）"
        )
    return f"{_INJECT_HEADER}\n{content}"
