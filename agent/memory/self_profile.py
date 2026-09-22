"""自我画像（agent:self）——AI 对自己的人格/行为认知，人格记忆三实体的"AI"位。

人格三实体在 Anelf 现有面上的落位（不新造存储）：
- 关于主人 → 既有 ENTITY 画像（user scope，update_entity_profile 维护）；
- 关于 AI → 本模块：``agent:self`` 保留身份的画像，存同一张画像体系
  （personality 表 + ENTITY 记忆镜像），随画像注入层每轮呈现；
- 关系动态 → 关系图谱的 ``agent:self`` 节点（graph_add_relation 落边，
  load_relation_snippets 注入）。

宪法与生长分层：personas/*.json 静态人设是宪法（stable 层冻结，不进
记忆库）；自画像是生长层——唯一写入通道是反思晋升（reflection_lifecycle
经 LLM 合并决策调用 update_profile_content），AI 不得在对话中随手改写
（铁律记忆写入路由已登记）。
"""

from __future__ import annotations

import time

from core.log import log

from .memory_store import MemoryStore
from .memory_types import MemoryEntry, MemoryType

SELF_SCOPE = "agent:self"
"""自画像的保留身份（scope 记法；常量单点定义，全仓禁手工拼）。"""

SELF_PROFILE_SOURCE = "entity_agent_self"
"""自画像在 memories 表的 source 键。"""

SELF_TAG = "agent:self"
"""自画像记忆条目标签。"""

PROFILE_MEMORY_IMPORTANCE = 0.9
"""画像 ENTITY 镜像的统一重要度：画像内容是身份级事实（importance 校准表
最高档），高于普通语义记忆——画像条目理应在检索与遗忘线上更耐久。"""

_SELF_SCOPE_TYPE = "agent"
_SELF_SCOPE_ID = "self"

_INJECT_HEADER = "[系统注入·自我画像] 关于你自己（生长层，由反思晋升维护）"


def resolve_promotion_target(entry: MemoryEntry) -> str:
    """反思晋升的目标画像：反思主体是 AI 自身 → agent:self；否则归所属用户。

    判据按标签：带 user:{adapter}:{uid} 归属标签的反思晋升到该用户画像
    （"小明其实不喜欢被打扰"是关于小明的）；无归属或主体含"我/自己"的
    反思晋升到自画像（"我发现自己解释太啰嗦"是关于我的）。
    """
    from .store.tag_intel import ENTITY_PREFIXES
    for tag in entry.tags or []:
        for prefix in ENTITY_PREFIXES:
            if tag.startswith(prefix):
                return tag
    return SELF_SCOPE


async def load_self_profile(store: MemoryStore) -> str:
    """读取自画像文本（无则空串，异常静默降级）。"""
    try:
        entries = await store.list_recent(limit=1, memory_type=MemoryType.ENTITY, source=SELF_PROFILE_SOURCE)
        if entries:
            return entries[0].content
        entries = await store.search_by_tags([SELF_TAG], limit=1)
        return entries[0].content if entries else ""
    except Exception as exc:
        log(f"自画像读取失败: {exc}", "DEBUG", tag="记忆")
        return ""


def render_self_profile_block(content: str) -> str:
    """渲染注入块（空内容返回空串——无自画像时不占位）。"""
    content = (content or "").strip()
    return f"{_INJECT_HEADER}：\n{content}" if content else ""


async def update_profile_content(
    store: MemoryStore,
    target_scope: str,
    content: str,
) -> bool:
    """晋升通道的画像写入（agent:self 或 user:{adapter}:{uid}）。

    覆盖式更新前先备份（复用画像工具的备份纪律）；ENTITY 记忆镜像同步重建，
    保持召回/注入两侧一致。
    """
    from .profile_backup import backup_entity_profile

    content = (content or "").strip()
    if not content:
        return False

    if target_scope == SELF_SCOPE:
        scope_type, scope_id = _SELF_SCOPE_TYPE, _SELF_SCOPE_ID
    else:
        # 晋升目标的实体画像：tag 形态 user:{adapter}:{uid} / group:{adapter}:{gid}
        parts = target_scope.split(":", 1)
        if len(parts) != 2 or parts[0] not in ("user", "group") or not parts[1]:
            return False
        scope_type, scope_id = parts[0], parts[1]

    try:
        from agent.runtime.singleton import require_runtime
        rt = require_runtime()
        sqlite = rt.data_center.sqlite
        old = await sqlite.get_entity_personality(scope_type=scope_type, scope_id=scope_id)
        if old and old.get("personality"):
            backup_entity_profile(scope_type, scope_id, old["personality"])
        await sqlite.set_entity_personality(
            scope_type=scope_type, scope_id=scope_id,
            personality=content,
            conv_num=int((old or {}).get("conv_num", 0)),
            conv_update_num=0,
        )
    except Exception as exc:
        log(f"画像写入失败（{target_scope}）: {exc}", "WARNING", tag="记忆")
        return False

    # ENTITY 记忆镜像重建（注入/召回读这里）
    try:
        source = SELF_PROFILE_SOURCE if target_scope == SELF_SCOPE else f"entity_{scope_id}"
        old_entries = await store.list_recent(limit=5, memory_type=MemoryType.ENTITY, source=source)
        for old_entry in old_entries:
            if old_entry.id:
                await store.delete(old_entry.id, actor="reflection")
        entry = MemoryEntry(
            memory_type=MemoryType.ENTITY,
            content=content,
            source=source,
            tags=[target_scope],
            importance=PROFILE_MEMORY_IMPORTANCE,
            timestamp=time.time(),
        )
        await store.add(entry, actor="reflection")
        from .embedding import wake_embedding_worker
        wake_embedding_worker()
    except Exception as exc:
        log(f"画像记忆镜像重建失败（{target_scope}）: {exc}", "WARNING", tag="记忆")
    return True
