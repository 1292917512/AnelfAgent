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

import os
import re
import threading
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
    yaml = None
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
    # 真实消费次数（手势命中 / AI 读全文 / 内容被合并采用）；检索注入不计入
    use_count: int = 0
    # 检索注入次数（语义/关键词匹配命中即计数，不代表被真正消费）
    match_count: int = 0
    patch_count: int = 0
    state: SkillState = SkillState.ACTIVE
    pinned: bool = False
    # 用户手势可调用（/name 确定性触发；False = 仅语义/关键词匹配可见）
    user_invocable: bool = True
    # 最近一次写入的决策理由（决策协议回执，供后续评审追溯问责）
    rationale: str = ""
    # 被合并归档时的去向技能名（可逆归档的恢复线索）
    merged_into: str = ""
    created_at: float = Field(default_factory=time.time)
    last_activity_at: float = Field(default_factory=time.time)
    # 最近一次被检索注入的时间（stale 层的软保留信号：仍被检索到则不归档）
    last_match_at: float = 0.0

    def touch(self) -> None:
        """记录一次活动（真实使用/更新）。检索注入不调用本方法。"""
        self.last_activity_at = time.time()


# ------------------------------------------------------------------
# SKILL.md 序列化
# ------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"\A\s*---\s*\n(.*?)\n---\s*\n?", re.DOTALL)

_META_FIELDS = (
    "name", "description", "trigger_patterns", "created_by",
    "use_count", "match_count", "patch_count", "state", "pinned", "user_invocable",
    "rationale", "merged_into", "created_at", "last_activity_at", "last_match_at",
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
        "match_count": skill.match_count,
        "patch_count": skill.patch_count,
        "state": skill.state.value,
        "pinned": skill.pinned,
        "user_invocable": skill.user_invocable,
        "rationale": skill.rationale,
        "merged_into": skill.merged_into,
        "created_at": skill.created_at,
        "last_activity_at": skill.last_activity_at,
    }
    if skill.last_match_at > 0.0:
        meta["last_match_at"] = skill.last_match_at
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


def _atomic_write(target: Path, content: str) -> None:
    """原子写入文件（委托 core.file_utils 统一实现）。"""
    from core.file_utils import atomic_write_text

    atomic_write_text(target, content)


class SkillStore:
    """技能库：workspace/skills/ 目录下的 SKILL.md 文件集合。"""

    def __init__(self, skills_dir: Optional[str] = None) -> None:
        if skills_dir is None:
            from core.path import workspace_root
            skills_dir = os.path.join(workspace_root(), "skills")
        self.skills_dir = Path(skills_dir)
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        # 读写锁（可重入）：save 原子写 + get/delete 读取串行化，
        # record_use 等读-改-写操作需在同一把锁内完成，避免并发计数互相覆盖
        self._lock = threading.RLock()
        # 内容版本号：每次 save/delete 递增，供调用方做廉价缓存失效判断
        self._version = 0
        # 目录签名：外部途径（如 skillhub CLI / 手动拷贝）增删技能时检测变化
        self._last_dir_signature = self._dir_signature()
        # 解析失败登记：严格契约下脏文件不静默消失——错误作为健康事实呈现，
        # 由 AI 评审/治理时自主决策修复（create 同名重建即为修复通道）
        self._parse_errors: Dict[str, str] = {}

    @property
    def parse_errors(self) -> Dict[str, str]:
        """当前解析失败的技能（技能名 → 错误摘要，只读副本）。"""
        with self._lock:
            return dict(self._parse_errors)

    @property
    def version(self) -> int:
        """技能库内容版本号（单调递增，含外部目录变更感知）。"""
        with self._lock:
            signature = self._dir_signature()
            if signature != self._last_dir_signature:
                self._last_dir_signature = signature
                self._version += 1
            return self._version

    def _dir_signature(self) -> int:
        """目录指纹：子目录名 + SKILL.md 大小/_mtime 的轻量签名。

        matcher 按 version 缓存技能列表，仅 stat 不开文件，代价可忽略。
        """
        signature = 0
        try:
            for child in self.skills_dir.iterdir():
                if not child.is_dir():
                    continue
                try:
                    stat = (child / "SKILL.md").stat()
                except OSError:
                    continue
                signature ^= hash((child.name, stat.st_size, stat.st_mtime_ns))
        except OSError:
            pass
        return signature

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
        with self._lock:
            if not path.is_file():
                return None
            try:
                meta, body = parse_skill_md(path.read_text(encoding="utf-8"))
                skill = self._skill_from_meta(meta, body, fallback_name=self.normalize_name(name))
                self._parse_errors.pop(self.normalize_name(name), None)
                return skill
            except Exception as exc:
                log(f"技能解析失败: {path}: {exc}", "WARNING", tag="技能")
                self._parse_errors[self.normalize_name(name)] = f"{type(exc).__name__}: {exc}"[:200]
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
        """保存技能（原子写入 SKILL.md）。"""
        skill.name = self.normalize_name(skill.name)
        path = self._skill_path(skill.name)
        with self._lock:
            _atomic_write(path, render_skill_md(skill))
            # save 写入的必然是合法格式（render 自模型），清除同名解析失败登记
            self._parse_errors.pop(skill.name, None)
            self._last_dir_signature = self._dir_signature()
            self._version += 1
        log(f"💾 技能已保存: {skill.name} (state={skill.state.value})", "DEBUG", tag="技能")
        return skill

    def create(
            self,
            name: str,
            description: str,
            content: str,
            trigger_patterns: Optional[List[str]] = None,
            created_by: str = "agent",
            rationale: str = "",
    ) -> Skill:
        """创建新技能（已存在时转为内容更新）。rationale 记录本次写入的决策理由。"""
        existing = self.get(name)
        if existing is not None:
            existing.content = content
            if description:
                existing.description = description
            if trigger_patterns:
                merged = list(dict.fromkeys(existing.trigger_patterns + trigger_patterns))
                existing.trigger_patterns = merged
            existing.patch_count += 1
            existing.rationale = rationale or existing.rationale
            existing.touch()
            return self.save(existing)
        skill = Skill(
            name=self.normalize_name(name),
            description=description,
            content=content,
            trigger_patterns=trigger_patterns or [],
            created_by=created_by,
            rationale=rationale,
        )
        return self.save(skill)

    def patch(
            self,
            name: str,
            *,
            content: Optional[str] = None,
            description: Optional[str] = None,
            add_trigger_patterns: Optional[List[str]] = None,
            rationale: str = "",
    ) -> Optional[Skill]:
        """增量更新技能（patch_count +1）。rationale 记录本次写入的决策理由。"""
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
        if rationale:
            skill.rationale = rationale
        skill.touch()
        return self.save(skill)

    def delete(self, name: str) -> bool:
        """删除技能（物理删除目录）。"""
        path = self._skill_path(name)
        with self._lock:
            if not path.is_file():
                return False
            import shutil
            shutil.rmtree(path.parent)
            self._parse_errors.pop(self.normalize_name(name), None)
            self._last_dir_signature = self._dir_signature()
            self._version += 1
        log(f"🗑 技能已删除: {name}", tag="技能")
        return True

    def record_use(self, name: str, *, touch: bool = True) -> None:
        """记录一次真实使用（use_count +1）。

        真实使用 = 手势命中 / AI 读全文 / 内容被采用。检索注入走 record_match，
        两者分离后策展的闲置计时不再被"碰巧被匹配到"刷新。
        touch=False 供系统侧查阅（如评审读候选）计数但不刷新活动时间——
        检查不等于消费，不能给技能续命。
        """
        with self._lock:
            skill = self.get(name)
            if skill is None:
                return
            skill.use_count += 1
            if touch:
                skill.touch()
            self.save(skill)

    def record_match(self, name: str) -> None:
        """记录一次检索注入（match_count +1，仅刷新 last_match_at）。

        不刷新 last_activity_at：被匹配不等于被消费，不能阻断闲置降级；
        last_match_at 供 stale 层软保留（仍被检索到的技能不归档）。
        """
        with self._lock:
            skill = self.get(name)
            if skill is None:
                return
            skill.match_count += 1
            skill.last_match_at = time.time()
            self.save(skill)

    def merge(
            self,
            sources: List[str],
            target: str,
            *,
            content: str,
            description: str = "",
            add_trigger_patterns: Optional[List[str]] = None,
            rationale: str = "",
    ) -> Optional[Skill]:
        """将若干源技能合并进目标技能（目标增量更新，源可逆归档）。

        合并是 AI 的显式策展决策，本方法只负责稳定落地：目标经 patch 语义
        写入合并后内容，源技能置 ARCHIVED 并记录 merged_into 以便追溯恢复。
        """
        target_name = self.normalize_name(target)
        with self._lock:
            if target_name not in [s.name for s in self.list_skills(include_archived=True)]:
                return None
            source_names: List[str] = []
            for raw in sources:
                src_name = self.normalize_name(raw)
                if src_name == target_name or src_name in source_names:
                    continue
                source_names.append(src_name)
            merged = self.patch(
                target_name,
                content=content,
                description=description or None,
                add_trigger_patterns=add_trigger_patterns,
                rationale=rationale or f"合并自: {', '.join(source_names)}",
            )
            if merged is None:
                return None
            for src_name in source_names:
                src = self.get(src_name)
                if src is None:
                    continue
                src.state = SkillState.ARCHIVED
                src.merged_into = target_name
                self.save(src)
            return merged

    def set_state(self, name: str, state: SkillState) -> Optional[Skill]:
        """变更技能状态（active/stale/archived）。

        状态迁移不刷新活动时间——curator 的自动降级/归档若 touch 会重置
        闲置计时，导致同一技能永远无法进入下一状态阶段。
        """
        with self._lock:
            skill = self.get(name)
            if skill is None:
                return None
            skill.state = state
            return self.save(skill)

    def set_pinned(self, name: str, pinned: bool) -> Optional[Skill]:
        """设置置顶（置顶技能豁免自动归档）。"""
        with self._lock:
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
