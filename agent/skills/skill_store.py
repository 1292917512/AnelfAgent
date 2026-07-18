"""技能存储 — SKILL.md 文件格式（YAML frontmatter + markdown 正文）。

技能是 AI 从任务经验中提炼的可复用知识，存储在 ``workspace/skills/<name>/SKILL.md``：

    ---
    name: web-research
    description: 网络调研流程
    trigger_patterns: ["调研", "查资料"]
    created_by: agent
    use_count: 3
    patch_count: 1
    state: active
    pinned: false
    created_at: 1784300000.0
    last_activity_at: 1784300000.0
    ---

    # 网络调研流程
    1. 先 web_search 广泛搜索 ...

frontmatter 解析优先使用 PyYAML，不可用时降级为简单 key: value 解析。
"""
from __future__ import annotations

import re
import time
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from core.log import log

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    yaml = None  # type: ignore[assignment]
    _HAS_YAML = False


class SkillState(str, Enum):
    """技能生命周期状态。"""

    ACTIVE = "active"
    STALE = "stale"
    ARCHIVED = "archived"


class Skill(BaseModel):
    """技能数据模型。"""

    name: str
    description: str = ""
    trigger_patterns: List[str] = Field(default_factory=list)
    content: str = ""
    created_by: str = "agent"
    use_count: int = 0
    patch_count: int = 0
    state: SkillState = SkillState.ACTIVE
    pinned: bool = False
    created_at: float = Field(default_factory=time.time)
    last_activity_at: float = Field(default_factory=time.time)

    def touch(self) -> None:
        """记录一次活动（使用/更新）。"""
        self.last_activity_at = time.time()


# ------------------------------------------------------------------
# SKILL.md 序列化
# ------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"\A\s*---\s*\n(.*?)\n---\s*\n?", re.DOTALL)

_META_FIELDS = (
    "name", "description", "trigger_patterns", "created_by",
    "use_count", "patch_count", "state", "pinned",
    "created_at", "last_activity_at",
)


def _parse_frontmatter_fallback(text: str) -> Dict[str, Any]:
    """简单 key: value 解析（PyYAML 不可用时的降级）。"""
    result: Dict[str, Any] = {}
    for line in text.split("\n"):
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if value.startswith("[") and value.endswith("]"):
            items = [v.strip().strip("'\"") for v in value[1:-1].split(",")]
            result[key] = [v for v in items if v]
        elif value.lower() in ("true", "false"):
            result[key] = value.lower() == "true"
        else:
            try:
                result[key] = int(value)
            except ValueError:
                try:
                    result[key] = float(value)
                except ValueError:
                    result[key] = value.strip("'\"")
    return result


def parse_skill_md(text: str) -> Tuple[Dict[str, Any], str]:
    """解析 SKILL.md，返回 (frontmatter 元数据, markdown 正文)。"""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text.strip()
    raw_meta = match.group(1)
    body = text[match.end():].strip()
    if _HAS_YAML:
        try:
            meta = yaml.safe_load(raw_meta) or {}
            if not isinstance(meta, dict):
                meta = {}
        except Exception:
            meta = _parse_frontmatter_fallback(raw_meta)
    else:
        meta = _parse_frontmatter_fallback(raw_meta)
    return meta, body


def render_skill_md(skill: Skill) -> str:
    """将技能序列化为 SKILL.md 文本。"""
    meta = {
        "name": skill.name,
        "description": skill.description,
        "trigger_patterns": skill.trigger_patterns,
        "created_by": skill.created_by,
        "use_count": skill.use_count,
        "patch_count": skill.patch_count,
        "state": skill.state.value,
        "pinned": skill.pinned,
        "created_at": skill.created_at,
        "last_activity_at": skill.last_activity_at,
    }
    if _HAS_YAML:
        frontmatter = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
    else:
        lines = []
        for key, value in meta.items():
            if isinstance(value, list):
                value = "[" + ", ".join(str(v) for v in value) + "]"
            lines.append(f"{key}: {value}")
        frontmatter = "\n".join(lines)
    return f"---\n{frontmatter}\n---\n\n{skill.content.strip()}\n"


# ------------------------------------------------------------------
# SkillStore
# ------------------------------------------------------------------

_NAME_SAFE_RE = re.compile(r"[^A-Za-z0-9_-]+")


class SkillStore:
    """技能库：workspace/skills/ 目录下的 SKILL.md 文件集合。"""

    def __init__(self, skills_dir: str = "workspace/skills") -> None:
        self.skills_dir = Path(skills_dir)
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def normalize_name(name: str) -> str:
        """规范化技能名（文件系统安全）。"""
        normalized = _NAME_SAFE_RE.sub("-", name.strip()).strip("-").lower()
        return normalized[:64] or "unnamed"

    def _skill_path(self, name: str) -> Path:
        return self.skills_dir / self.normalize_name(name) / "SKILL.md"

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def exists(self, name: str) -> bool:
        return self._skill_path(name).is_file()

    def get(self, name: str) -> Optional[Skill]:
        path = self._skill_path(name)
        if not path.is_file():
            return None
        try:
            meta, body = parse_skill_md(path.read_text(encoding="utf-8"))
            return self._skill_from_meta(meta, body, fallback_name=self.normalize_name(name))
        except Exception as exc:
            log(f"技能解析失败: {path}: {exc}", "WARNING", tag="技能")
            return None

    def list_skills(self, *, include_archived: bool = False) -> List[Skill]:
        """列出全部技能（按最近活动排序）。"""
        skills: List[Skill] = []
        if not self.skills_dir.is_dir():
            return skills
        for child in sorted(self.skills_dir.iterdir()):
            skill_file = child / "SKILL.md"
            if not child.is_dir() or not skill_file.is_file():
                continue
            skill = self.get(child.name)
            if skill is None:
                continue
            if not include_archived and skill.state == SkillState.ARCHIVED:
                continue
            skills.append(skill)
        skills.sort(key=lambda s: s.last_activity_at, reverse=True)
        return skills

    def save(self, skill: Skill) -> Skill:
        """保存技能（写入 SKILL.md）。"""
        skill.name = self.normalize_name(skill.name)
        path = self._skill_path(skill.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_skill_md(skill), encoding="utf-8")
        log(f"💾 技能已保存: {skill.name} (state={skill.state.value})", "DEBUG", tag="技能")
        return skill

    def create(
            self,
            name: str,
            description: str,
            content: str,
            trigger_patterns: Optional[List[str]] = None,
            created_by: str = "agent",
    ) -> Skill:
        """创建新技能（已存在时转为内容更新）。"""
        existing = self.get(name)
        if existing is not None:
            existing.content = content
            if description:
                existing.description = description
            if trigger_patterns:
                merged = list(dict.fromkeys(existing.trigger_patterns + trigger_patterns))
                existing.trigger_patterns = merged
            existing.patch_count += 1
            existing.touch()
            return self.save(existing)
        skill = Skill(
            name=self.normalize_name(name),
            description=description,
            content=content,
            trigger_patterns=trigger_patterns or [],
            created_by=created_by,
        )
        return self.save(skill)

    def patch(
            self,
            name: str,
            *,
            content: Optional[str] = None,
            description: Optional[str] = None,
            add_trigger_patterns: Optional[List[str]] = None,
    ) -> Optional[Skill]:
        """增量更新技能（patch_count +1）。"""
        skill = self.get(name)
        if skill is None:
            return None
        if content is not None:
            skill.content = content
        if description is not None:
            skill.description = description
        if add_trigger_patterns:
            skill.trigger_patterns = list(
                dict.fromkeys(skill.trigger_patterns + add_trigger_patterns)
            )
        skill.patch_count += 1
        skill.touch()
        return self.save(skill)

    def delete(self, name: str) -> bool:
        """删除技能（物理删除目录）。"""
        path = self._skill_path(name)
        if not path.is_file():
            return False
        import shutil
        shutil.rmtree(path.parent)
        log(f"🗑 技能已删除: {name}", tag="技能")
        return True

    def record_use(self, name: str) -> None:
        """记录一次使用（use_count +1，刷新活动时间）。"""
        skill = self.get(name)
        if skill is None:
            return
        skill.use_count += 1
        skill.touch()
        self.save(skill)

    def set_state(self, name: str, state: SkillState) -> Optional[Skill]:
        """变更技能状态（active/stale/archived）。"""
        skill = self.get(name)
        if skill is None:
            return None
        skill.state = state
        skill.touch()
        return self.save(skill)

    def set_pinned(self, name: str, pinned: bool) -> Optional[Skill]:
        """设置置顶（置顶技能豁免自动归档）。"""
        skill = self.get(name)
        if skill is None:
            return None
        skill.pinned = pinned
        skill.touch()
        return self.save(skill)

    @staticmethod
    def _skill_from_meta(meta: Dict[str, Any], body: str, *, fallback_name: str) -> Skill:
        data = {k: v for k, v in meta.items() if k in _META_FIELDS}
        data.setdefault("name", fallback_name)
        data["content"] = body
        if isinstance(data.get("trigger_patterns"), str):
            data["trigger_patterns"] = [
                p.strip() for p in data["trigger_patterns"].split(",") if p.strip()
            ]
        try:
            data["state"] = SkillState(data.get("state", "active"))
        except ValueError:
            data["state"] = SkillState.ACTIVE
        return Skill(**data)
