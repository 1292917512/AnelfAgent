"""自我画像（agent:self）——AI 对自己的人格/行为认知，人格记忆三实体的"AI"位。

人格三实体在 Anelf 现有面上的落位（不新造存储）：
- 关于主人 → 既有 ENTITY 画像（user scope，update_entity_profile 维护）；
- 关于 AI → 本模块：``agent:self`` 保留身份的画像，存同一张画像体系
  （entity_profile 表 + ENTITY 记忆镜像），随画像注入层每轮呈现；
- 关系动态 → 关系图谱的 ``agent:self`` 节点（graph_add_relation 落边，
  load_relation_snippets 注入）。

宪法与生长分层：personas/*.json 静态人设是宪法（stable 层冻结，不进
记忆库）；自画像是生长层。反思晋升与 AI 的显式修订都经 ProfileWriter，
保留来源和理由；操作记录不充当认知证据，不自动改变反思确认状态。
"""

from __future__ import annotations

from core.log import log

from .memory_store import MemoryStore
from .memory_types import MemoryType

SELF_SCOPE = "agent:self"
"""自画像的保留身份（scope 记法；常量单点定义，全仓禁手工拼）。"""

SELF_PROFILE_SOURCE = "entity_agent_self"
"""自画像在 memories 表的 source 键。"""

SELF_TAG = "agent:self"
"""自画像记忆条目标签。"""

PROFILE_MEMORY_IMPORTANCE = 0.9
"""画像 ENTITY 镜像的统一重要度：画像内容是身份级事实（importance 校准表
最高档），高于普通语义记忆——画像条目理应在检索与遗忘线上更耐久。"""

_INJECT_HEADER = "[系统注入·自我画像] 关于你自己（生长层，由反思晋升与自主修订维护）"


async def load_self_profile(store: MemoryStore) -> str:
    """读取自画像文本（无则空串，异常静默降级）。"""
    try:
        entries = await store.list_recent(limit=1, memory_type=MemoryType.ENTITY, source=SELF_PROFILE_SOURCE)
        if entries:
            return entries[0].content
        return ""
    except Exception as exc:
        log(f"自画像读取失败: {exc}", "DEBUG", tag="记忆")
        return ""


def render_self_profile_block(content: str) -> str:
    """渲染注入块（空内容返回空串——无自画像时不占位）。"""
    content = (content or "").strip()
    return f"{_INJECT_HEADER}：\n{content}" if content else ""
