"""SkillService — 技能库管理服务（供 Web API 使用）。

技能存储在 workspace/skills/ 目录（文件系统），本服务为无状态封装。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from agent.skills.skill_store import SkillState, SkillStore


class SkillService:
    """技能 CRUD 与状态管理服务。"""

    def __init__(self, skills_dir: str = "workspace/skills") -> None:
        self._store = SkillStore(skills_dir)

    def list_skills(self, *, include_archived: bool = False) -> List[Dict[str, Any]]:
        """列出技能摘要信息。"""
        return [
            {
                "name": s.name,
                "description": s.description,
                "trigger_patterns": s.trigger_patterns,
                "state": s.state.value,
                "use_count": s.use_count,
                "patch_count": s.patch_count,
                "pinned": s.pinned,
                "created_by": s.created_by,
                "created_at": s.created_at,
                "last_activity_at": s.last_activity_at,
            }
            for s in self._store.list_skills(include_archived=include_archived)
        ]

    def get_skill(self, name: str) -> Dict[str, Any]:
        """获取技能完整内容。"""
        skill = self._store.get(name)
        if skill is None:
            raise ValueError(f"技能 '{name}' 不存在")
        return {
            "name": skill.name,
            "description": skill.description,
            "trigger_patterns": skill.trigger_patterns,
            "content": skill.content,
            "state": skill.state.value,
            "use_count": skill.use_count,
            "patch_count": skill.patch_count,
            "pinned": skill.pinned,
            "created_by": skill.created_by,
            "created_at": skill.created_at,
            "last_activity_at": skill.last_activity_at,
        }

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
        return {"name": skill.name}

    def update_skill(self, name: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """更新技能内容/描述/触发词。"""
        skill = self._store.patch(
            name,
            content=data.get("content"),
            description=data.get("description"),
            add_trigger_patterns=data.get("add_trigger_patterns"),
        )
        if skill is None:
            raise ValueError(f"技能 '{name}' 不存在")
        return {"name": skill.name, "patch_count": skill.patch_count}

    def delete_skill(self, name: str) -> bool:
        """删除技能。"""
        return self._store.delete(name)

    def set_state(self, name: str, state: str) -> Dict[str, Any]:
        """变更技能状态（active/stale/archived）。"""
        try:
            skill_state = SkillState(state)
        except ValueError:
            raise ValueError(f"无效状态: {state}（可选: active/stale/archived）")
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
