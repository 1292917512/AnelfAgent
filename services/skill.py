"""SkillService — 技能库管理服务（供 Web API 使用）。

技能存储在 workspace/skills/ 目录（文件系统），本服务为无状态封装。
向量状态与 CRUD 后的即时嵌入经 Mind 侧 SkillIndex（同进程运行时单例）；
runtime 未就绪时优雅降级（embedded 字段为 None，不写死索引不可用）。

Model Experience：Web 健康报告只含元数据事实与向量缓存命中状态（不发嵌入请求，
读缓存是零成本）；CRUD 变更经 mind_index.embed_now 立即同步（单条 embed_query，
失败静默降级，心跳 warm 兜底）。
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from agent.skills.skill_index import SkillIndex
from agent.skills.skill_store import SkillState, SkillStore
from core.log import log


def _mind_index() -> Optional[SkillIndex]:
    """Mind 侧事实索引（运行时单例，向量缓存的唯一权威）。

    runtime 未就绪（测试/web-only）时返回 None，调用方全部降级处理。
    """
    try:
        from services import _runtime
        rt = _runtime.get_runtime()
        if rt is None:
            return None
        mind = getattr(rt, "mind", None)
        matcher = getattr(mind, "skill_matcher", None)
        return getattr(matcher, "index", None)
    except Exception:
        return None


def _skill_summary(skill: Any, index: Optional[SkillIndex]) -> Dict[str, Any]:
    """技能摘要的统一序列化（列表与详情共用字段口径）。"""
    return {
        "name": skill.name,
        "description": skill.description,
        "trigger_patterns": skill.trigger_patterns,
        "state": skill.state.value,
        "use_count": skill.use_count,
        "match_count": skill.match_count,
        "patch_count": skill.patch_count,
        "pinned": skill.pinned,
        "created_by": skill.created_by,
        "rationale": skill.rationale,
        "merged_into": skill.merged_into,
        # 向量就绪状态：缓存命中=已嵌入；索引不可用时 None（前端降级不显示）
        "embedded": index.is_embedded(skill) if index is not None else None,
        "created_at": skill.created_at,
        "last_activity_at": skill.last_activity_at,
        "last_match_at": skill.last_match_at,
    }


class SkillService:
    """技能 CRUD 与状态管理服务。"""

    def __init__(self, skills_dir: Optional[str] = None) -> None:
        self._store = SkillStore(skills_dir)

    def _sync_vector(self, name: str) -> None:
        """CRUD 后向量即时同步：嵌入当前表征 + 清理死键（fire-and-forget）。

        失败静默（心跳 warm 兜底）；无事件循环（同步测试环境）跳过。
        """
        index = _mind_index()
        if index is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(index.embed_now(name), name=f"skill.embed.{name}")
        task.add_done_callback(self._log_sync_result)

    @staticmethod
    def _log_sync_result(task: "asyncio.Task[bool]") -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log(f"技能向量即时同步失败: {exc}", "DEBUG", tag="技能")

    def list_skills(self, *, include_archived: bool = False) -> List[Dict[str, Any]]:
        """列出技能摘要信息（含向量就绪状态）。"""
        index = _mind_index()
        return [
            _skill_summary(s, index)
            for s in self._store.list_skills(include_archived=include_archived)
        ]

    def get_skill(self, name: str) -> Dict[str, Any]:
        """获取技能完整内容。"""
        skill = self._store.get(name)
        if skill is None:
            raise ValueError(f"技能 '{name}' 不存在")
        data = _skill_summary(skill, _mind_index())
        data["content"] = skill.content
        return data

    def library_health(self) -> Dict[str, Any]:
        """技能库健康报告（元数据事实 + Mind 侧向量覆盖统计与构建状态）。"""
        index = _mind_index()
        snapshot = SkillIndex(self._store).snapshot()
        if index is not None:
            # Mind 侧索引持有真实向量缓存与构建状态；Web 侧临时索引恒为空
            snapshot["embedding"] = index.embedding_stats()
            snapshot["build"] = index.build_state()
        return snapshot

    async def rebuild_vectors(self) -> Dict[str, Any]:
        """手动触发全量向量重建（模型切换后的标准操作，或手动修复）。

        幂等：进行中直接返回当前进度。返回触发后的构建状态快照。
        """
        index = _mind_index()
        if index is None:
            raise RuntimeError("技能索引不可用（runtime 未就绪）")
        if index.vector_rebuilding:
            return {"ok": True, "message": "重建已在进行中", "state": index.build_state()}
        await index.rebuild_all()
        return {"ok": True, "state": index.build_state()}

    async def embed_skill(self, name: str) -> Dict[str, Any]:
        """单个技能向量生成/重新生成（行内操作，不等全库重建）。

        幂等：向量已就绪直接返回；重建进行中让位（rebuild_all 会覆盖该技能）。
        """
        index = _mind_index()
        if index is None:
            raise RuntimeError("技能索引不可用（runtime 未就绪）")
        skill = self._store.get(name)
        if skill is None:
            raise ValueError(f"技能 '{name}' 不存在")
        if index.vector_rebuilding:
            return {
                "ok": True, "name": name, "embedded": False,
                "message": "全量重建进行中，该技能将由重建覆盖",
            }
        embedded = await index.embed_now(name)
        return {"ok": True, "name": name, "embedded": embedded}

    def create_skill(
            self,
            name: str,
            description: str,
            content: str,
            trigger_patterns: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """创建技能（created_by=user）。"""
        skill = self._store.create(
            name=name, description=description, content=content,
            trigger_patterns=trigger_patterns or [], created_by="user",
        )
        self._sync_vector(skill.name)
        return {"name": skill.name}

    def update_skill(self, name: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """更新技能内容/描述/触发词。"""
        skill = self._store.patch(
            name,
            content=data.get("content"),
            description=data.get("description"),
            add_trigger_patterns=data.get("add_trigger_patterns"),
            rationale=data.get("rationale") or "",
        )
        if skill is None:
            raise ValueError(f"技能 '{name}' 不存在")
        # 表征文本变化后旧向量键即失配，立即重嵌保持检索语义最新
        self._sync_vector(skill.name)
        return {"name": skill.name, "patch_count": skill.patch_count}

    def delete_skill(self, name: str) -> bool:
        """删除技能（向量死键由索引懒清理；索引可及时立即清理）。"""
        deleted = self._store.delete(name)
        if deleted:
            index = _mind_index()
            if index is not None:
                try:
                    index.prune_stale_vectors()
                except Exception:
                    pass
        return deleted

    def set_state(self, name: str, state: str) -> Dict[str, Any]:
        """变更技能状态（active/stale/archived）。"""
        try:
            skill_state = SkillState(state)
        except ValueError:
            raise ValueError(f"无效状态: {state}（可选: active/stale/archived）") from None
        skill = self._store.set_state(name, skill_state)
        if skill is None:
            raise ValueError(f"技能 '{name}' 不存在")
        return {"name": skill.name, "state": skill.state.value}

    def set_pinned(self, name: str, pinned: bool) -> Dict[str, Any]:
        """设置置顶。"""
        skill = self._store.set_pinned(name, pinned)
        if skill is None:
            raise ValueError(f"技能 '{name}' 不存在")
        return {"name": skill.name, "pinned": skill.pinned}
