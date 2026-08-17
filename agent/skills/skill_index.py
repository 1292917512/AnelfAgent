"""技能事实索引 — 相似度 / 统计 / 聚类的确定性计算层。

职责边界（系统构建定位）：
- 只回答"技能库的现状是什么"（事实），不回答"应该怎么做"（策略归 AI）
- 写入路径的诊断（write_advisory）在这里计算，经 tools.py 以"决策请求"呈现给
  AI，由 AI 带 decision 回执完成写入——系统呈现事实，不做拒绝
- 向量复用 Mind 的 Embedder（与记忆召回同一基础设施），SQLite 持久化
  （skill_vectors.sqlite3，与主库同目录独立文件）+ 内存缓存热路径；
  重启零重嵌直接恢复；Embedder 不可用时全部语义能力优雅降级，仅剩元数据事实

Model Experience：
① 模型看到什么：create/update 的诊断报告（相近技能/触发词碰撞/容量/无实质变化）、
  评审时的语义相近候选与库健康摘要、skill_library_health 工具返回
② token 影响：评审候选从"最近活动 top20"收敛为"语义相近 top10"（更少更准）；
  诊断报告仅在事实显著时出现（多数写入零摩擦直通）
③ 缓存影响：全部走 volatile / tool_chain 尾部动态区，不触碰前缀层
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent.memory.memory_utils import (
    cosine_similarity,
    hash_text,
    pack_embedding,
    unpack_embedding,
)
from agent.skills.skill_store import Skill, SkillState, SkillStore
from core.log import log

# 向量内存缓存（模型名+内容 hash 键控）：容量有界 FIFO，持久层为 SQLite
_EMBEDDING_CACHE_MAX = 1024


def _cfg_float(key: str, default: float) -> float:
    from core.config import get_config_float
    return get_config_float(key, default)


def _cfg_int(key: str, default: int) -> int:
    from core.config import get_config_int
    return get_config_int(key, default)


class SkillIndex:
    """技能库事实索引：相似度查询、写入诊断、库健康快照、相似聚类。

    全部方法只读技能库并产出事实数据；任何"是否允许写入"的判断都不在这里——
    事实的呈现方式由调用方（tools 决策协议 / 评审上下文 / 策展议程）决定。
    """

    def __init__(self, store: SkillStore, embedder: Optional[object] = None,
                 db_path: Optional[str] = None) -> None:
        self._store = store
        self._embedder = embedder
        self._db_path = db_path
        self._db_loaded = False
        self._vec_cache: Dict[str, List[float]] = {}
        # 当前模型快照：嵌入路径的写入键；模型切换经 _refresh_model 检测后整体清换
        self._current_model: Optional[str] = None
        # 构建状态机（Web 可观测、可操作）：
        #   idle      — 稳态，无构建任务
        #   warming   — 心跳渐进预热中（模型未切换，正常补算）
        #   rebuilding — 模型切换后的全量重建中（一次性，无过渡态）
        self._build_state: str = "idle"
        self._build_progress: Dict[str, int] = {"done": 0, "total": 0}
        # 上次全量重建的审计记录（切换模型后谁重建了多少个）
        self._last_rebuild_at: float = 0.0
        self._last_rebuild_count: int = 0
        self._last_rebuild_model: str = ""
        # 全量重建标志：模型被切换后待重建；重建进行中防重入
        self._rebuild_pending = False
        self._rebuild_lock: Optional[asyncio.Lock] = None
        # 检索端近重复折叠收集的合并信号（技能对 → 出现次数），供议程/库健康消费
        self._merge_signals: Dict[Tuple[str, str], int] = {}
        # 技能列表缓存（store.version 键控）：similar/warm/clusters/snapshot 共用，
        # 避免心跳一拍内多次全库读文件
        self._list_cache: Optional[Tuple[int, List[Skill]]] = None
        # 聚类结果缓存：(threshold, 向量缓存指纹) 不变时直接复用——
        # O(N²) 余弦不该每拍重算，只在库内容或向量覆盖变化后重算一次
        self._cluster_cache: Optional[Tuple[Tuple[float, int, int, str], List[List[Skill]]]] = None

    def _all_skills(self) -> List[Skill]:
        """全量技能列表（含归档，store.version 键控缓存）。"""
        version = self._store.version
        if self._list_cache and self._list_cache[0] == version:
            return self._list_cache[1]
        skills = self._store.list_skills(include_archived=True)
        self._list_cache = (version, skills)
        return skills

    def _matchable_skills(self) -> List[Skill]:
        """可匹配技能（ACTIVE/STALE，基于缓存列表过滤）。"""
        return [s for s in self._all_skills() if s.state != SkillState.ARCHIVED]

    # ------------------------------------------------------------------
    # 向量持久化（SQLite：重启零重嵌恢复）
    # ------------------------------------------------------------------

    def _db_file(self) -> Path:
        """向量库文件：主库同目录的独立 SQLite（schema 自治，不侵入 MemoryStore）。

        命名对齐数据库注册表惯例（{主库stem}_skill_vectors.sqlite3），
        Web 数据库管理页可浏览。
        """
        if self._db_path is not None:
            return Path(self._db_path)
        from agent.storage.sqlite_backend import default_sqlite_path
        main = Path(default_sqlite_path())
        return main.with_name(f"{main.stem}_skill_vectors.sqlite3")

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        """建表（读写路径各自保证 schema，写入不依赖加载先行）。"""
        conn.execute(
            "CREATE TABLE IF NOT EXISTS skill_vectors ("
            "  skill_name TEXT PRIMARY KEY,"
            "  model TEXT NOT NULL,"
            "  text_hash TEXT NOT NULL,"
            "  vector BLOB NOT NULL,"
            "  updated_at REAL NOT NULL"
            ")"
        )

    def _ensure_db_loaded(self) -> None:
        """启动加载：首次访问时从 SQLite 恢复向量到内存缓存（一次性，毫秒级）。

        恢复口径：模型名匹配 + 表征文本 hash 匹配的行才有效——
        模型切换/技能内容变更/技能删除的残留行全部清除并标记全量重建。
        同步读取（269 行 × 数 KB ≈ MB 级，一次性），各入口统一调用。
        """
        if self._db_loaded:
            return
        if self._embedder is None:
            # 旁观者索引：模型名为空无法校验有效性，加载=把真模型的向量误判失效
            self._db_loaded = True
            return
        if not self._model_key():
            # 客户端未就绪（启动/热重载窗口）：延迟加载，就绪后下次入口再试——
            # 空模型名永不做失效判定
            return
        self._db_loaded = True
        try:
            self._load_from_db()
        except Exception as exc:
            log(f"技能向量库加载失败（按空库处理）: {exc}", "WARNING", tag="技能")

    def _load_from_db(self) -> None:
        db = self._db_file()
        db.parent.mkdir(parents=True, exist_ok=True)
        # 旧命名（skill_vectors.sqlite3）一次性迁移为注册表惯例命名
        legacy = db.with_name("skill_vectors.sqlite3")
        if not db.exists() and legacy.exists():
            try:
                os.rename(legacy, db)
                log("技能向量库已迁移为注册表惯例命名", tag="技能")
            except OSError as exc:
                log(f"技能向量库旧文件迁移失败: {exc}", "WARNING", tag="技能")
        current_model = self._model_key()
        self._current_model = current_model
        with sqlite3.connect(db) as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                "SELECT skill_name, model, text_hash, vector FROM skill_vectors"
            ).fetchall()
            skills_by_name = {s.name: s for s in self._store.list_skills(include_archived=True)}
            stale_names: List[str] = []
            loaded = 0
            for name, model, text_hash, blob in rows:
                skill = skills_by_name.get(name)
                if (
                    skill is not None
                    and model == current_model
                    and text_hash == hash_text(self._embedding_text(skill))
                ):
                    self._vec_cache[self._vector_key(skill)] = unpack_embedding(blob)
                    loaded += 1
                else:
                    stale_names.append(name)
            for name in stale_names:
                conn.execute("DELETE FROM skill_vectors WHERE skill_name = ?", (name,))
        if stale_names:
            self._rebuild_pending = True
            log(
                f"技能向量库: 恢复 {loaded} 个，清理失效 {len(stale_names)} 个（已安排重建）",
                tag="技能",
            )
        elif loaded:
            log(f"技能向量库: 从磁盘恢复 {loaded} 个向量（零重嵌）", tag="技能")

    def _persist_skill_vector(self, skill: Skill, vec: List[float]) -> None:
        """嵌入成功后持久化（upsert；失败仅记日志，内存缓存仍可用）。"""
        if self._embedder is None:
            return
        try:
            with sqlite3.connect(self._db_file()) as conn:
                self._ensure_schema(conn)
                conn.execute(
                    "INSERT INTO skill_vectors (skill_name, model, text_hash, vector, updated_at) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(skill_name) DO UPDATE SET "
                    "model=excluded.model, text_hash=excluded.text_hash, "
                    "vector=excluded.vector, updated_at=excluded.updated_at",
                    (
                        skill.name, self._current_model or "",
                        hash_text(self._embedding_text(skill)),
                        pack_embedding(vec), time.time(),
                    ),
                )
        except Exception as exc:
            log(f"技能向量持久化失败: {exc}", "DEBUG", tag="技能")

    def _delete_persisted(self, names: List[str]) -> None:
        """删除持久化行（prune/删除技能的清库同步）。"""
        if self._embedder is None or not names:
            return
        try:
            with sqlite3.connect(self._db_file()) as conn:
                conn.executemany(
                    "DELETE FROM skill_vectors WHERE skill_name = ?",
                    [(n,) for n in names],
                )
        except Exception as exc:
            log(f"技能向量库清理失败: {exc}", "DEBUG", tag="技能")

    def _delete_all_persisted(self) -> None:
        """清空全部持久化行（模型切换：旧向量空间整体作废）。"""
        if self._embedder is None:
            return
        try:
            with sqlite3.connect(self._db_file()) as conn:
                conn.execute("DELETE FROM skill_vectors")
        except Exception as exc:
            log(f"技能向量库清空失败: {exc}", "DEBUG", tag="技能")

    # ------------------------------------------------------------------
    # 向量
    # ------------------------------------------------------------------

    @staticmethod
    def _embedding_text(skill: Skill) -> str:
        """技能的向量表征文本（与检索/查重共用同一配方，保证可比性）。"""
        return f"{skill.name} {skill.description} {' '.join(skill.trigger_patterns)}"

    def _model_key(self) -> str:
        """当前 embedding 模型标识（LLMManager 查询，低频入口调用）。"""
        if self._embedder is None:
            return ""
        return str(getattr(self._embedder, "client_name", "") or "")

    def _refresh_model(self) -> None:
        """检测 embedding 模型切换：切换只可能来自人为手动改配置——
        检测到即清空全部技能向量 + 标记全量重建，不存在新旧模型并存的过渡态。

        触发时机：每次嵌入调用/预热/健康统计前（非每技能的 cached_vector 热路径，
        热路径用 _current_model 快照读，零额外开销）。
        无 embedder 的旁观者索引（如 Web 健康报告的临时实例）不参与模型追踪——
        它无权对持久层做任何校验/清理。
        """
        if self._embedder is None:
            return
        model = self._model_key()
        if not model:
            # 客户端未就绪（启动/热重载窗口）：空模型名是"不知道"，
            # 不参与任何校验——否则会把真模型存的向量误判失效
            return
        if self._current_model is None:
            self._current_model = model
            return
        if model == self._current_model:
            return
        previous = self._current_model
        self._current_model = model
        self._vec_cache.clear()
        self._cluster_cache = None
        self._rebuild_pending = True
        # DB 同步清空：旧模型向量不可复用（不同向量空间）
        self._delete_all_persisted()
        log(
            f"Embedding 模型已手动切换（{previous or '无'} → {model or '无'}）："
            f"技能向量已全部清空，安排全量重建",
            "WARNING", tag="技能",
        )
        # 有事件循环时立即异步重建（不等心跳兜底）
        try:
            asyncio.get_running_loop().create_task(
                self.rebuild_all(), name="skills.vector_rebuild",
            )
        except RuntimeError:
            pass

    def _vector_key(self, skill: Skill) -> str:
        return f"skill:{self._current_model or ''}:{hash_text(self._embedding_text(skill))}"

    @property
    def rebuild_pending(self) -> bool:
        """是否存在待执行的全量向量重建（模型切换后，供心跳接管）。"""
        return self._rebuild_pending

    @property
    def vector_rebuilding(self) -> bool:
        """全量重建是否进行中或待执行（供 Web 健康展示）。"""
        return self._rebuild_pending or self._build_state == "rebuilding"

    def build_state(self) -> Dict[str, Any]:
        """向量构建完整状态（Web 可观测的核心接口）。

        状态机：
          idle      — 稳态，无构建任务（embedded == total 且无待重建）
          warming   — 心跳渐进预热中（正常补算，无模型切换）
          rebuilding — 模型切换后的全量重建中（一次性，无过渡态）
          pending   — 已标记待重建但尚未开始（等待心跳接管）
        """
        self._refresh_model()
        skills = self._matchable_skills()
        self._ensure_db_loaded()
        embedded = sum(1 for s in skills if self.is_embedded(s))
        total = len(skills)
        return {
            "state": self._build_state,
            "embedded": embedded,
            "total": total,
            "model": self._current_model or "",
            "rebuilding": self.vector_rebuilding,
            "progress": dict(self._build_progress),
            "last_rebuild": {
                "at": self._last_rebuild_at,
                "count": self._last_rebuild_count,
                "model": self._last_rebuild_model,
            } if self._last_rebuild_at > 0 else None,
        }

    async def text_vector(self, text: str) -> Optional[List[float]]:
        """文本向量（Embedder 不可用或失败时返回 None，调用方按无语义路处理）。"""
        self._refresh_model()
        if self._embedder is None or not text.strip():
            return None
        try:
            vec = await self._embedder.embed_query(text)  # type: ignore[attr-defined]
        except Exception as exc:
            log(f"技能向量计算失败: {exc}", "DEBUG", tag="技能")
            return None
        return vec or None

    async def skill_vector(self, skill: Skill) -> Optional[List[float]]:
        """技能向量（模型名 + 表征文本 hash 缓存，内容/模型变更自然失效）。

        嵌入成功后同步持久化到 SQLite——重启经 _ensure_db_loaded 零重嵌恢复。
        """
        key = self._vector_key(skill)
        cached = self._vec_cache.get(key)
        if cached is not None:
            return cached
        vec = await self.text_vector(self._embedding_text(skill))
        if vec:
            self._cache_vector(key, vec)
            self._persist_skill_vector(skill, vec)
        return vec

    def cached_vector(self, skill: Skill) -> Optional[List[float]]:
        """缓存只读查询（不触发嵌入计算）。"""
        return self._vec_cache.get(self._vector_key(skill))

    def prune_stale_vectors(self) -> int:
        """清理死键：已缓存但当前模型/文本下失配的向量键（内容变更/模型切换残留）。

        时机约束：必须在嵌入完成之后调用——未嵌入的技能键尚未存在，
        在嵌入前调用会把"待嵌入"误判成死键。故常规路径由嵌入侧
        （warm/embed_now 完成后）与删除侧（service.delete_skill）触发，
        不在列表重建时调用。内存与 SQLite 同步清理。
        """
        if self._embedder is None:
            return 0
        self._ensure_db_loaded()
        valid = {self._vector_key(s) for s in self._all_skills()}
        stale = [k for k in self._vec_cache if k not in valid]
        for key in stale:
            self._vec_cache.pop(key, None)
        # DB 同步：技能删除/内容变更/模型变更的残留行一并清除
        skills_by_name = {s.name: s for s in self._all_skills()}
        current_model = self._current_model or ""
        stale_names: List[str] = []
        try:
            with sqlite3.connect(self._db_file()) as conn:
                rows = conn.execute(
                    "SELECT skill_name, model, text_hash FROM skill_vectors"
                ).fetchall()
                for name, model, text_hash in rows:
                    skill = skills_by_name.get(name)
                    if (
                        skill is None
                        or model != current_model
                        or text_hash != hash_text(self._embedding_text(skill))
                    ):
                        stale_names.append(name)
        except Exception as exc:
            log(f"技能向量库扫描失败: {exc}", "DEBUG", tag="技能")
        self._delete_persisted(stale_names)
        return len(stale)

    def is_embedded(self, skill: Skill) -> bool:
        """技能向量是否已就绪（缓存命中即就绪，供 Web 展示与覆盖统计）。"""
        self._ensure_db_loaded()
        return self.cached_vector(skill) is not None

    def embedding_stats(self) -> Dict[str, Any]:
        """向量覆盖统计（可匹配技能的已嵌入数/总数，供健康报告与 Web 展示）。

        完整构建状态见 build_state()；此处保持向后兼容的紧凑口径。
        """
        state = self.build_state()
        return {
            "embedded": state["embedded"],
            "total": state["total"],
            "cache_keys": len(self._vec_cache),
            "model": state["model"],
            "rebuilding": state["rebuilding"],
        }

    async def embed_now(self, name: str) -> bool:
        """立即嵌入单个技能（CRUD 变更后的即时同步，不等心跳预热）。

        嵌入成功后顺带清理死键（内容变更残留的旧文本键）。
        全量重建进行中时让位（rebuild_all 会覆盖该技能）。
        """
        self._refresh_model()
        self._ensure_db_loaded()
        if self._build_state == "rebuilding":
            return False
        skill = self._store.get(name)
        if skill is None:
            return False
        embedded = await self.skill_vector(skill) is not None
        if embedded:
            self.prune_stale_vectors()
        return embedded

    async def ensure_vectors(
            self,
            skills: List[Skill],
            *,
            budget: int | None = None,
    ) -> Dict[str, Optional[List[float]]]:
        """批量获取技能向量：缓存命中 + 预算内补算。

        embed_query 是单条串行的交互路径（优先级门单飞），全库冷缓存时逐技能
        补算会把一次检索拖成 N 次 API 往返——这里限制单次调用最多补算
        budget 个（默认 skills_embed_budget），其余技能本轮退化为无语义路，
        由心跳 warm() 批量预热逐步填满缓存。
        """
        if self._embedder is None:
            return {s.name: None for s in skills}
        self._refresh_model()
        self._ensure_db_loaded()
        budget = _cfg_int("skills_embed_budget", 16) if budget is None else budget
        result: Dict[str, Optional[List[float]]] = {}
        uncached: List[Skill] = []
        for skill in skills:
            cached = self.cached_vector(skill)
            if cached is not None:
                result[skill.name] = cached
            else:
                uncached.append(skill)
        for skill in uncached[:max(0, budget)]:
            result[skill.name] = await self.skill_vector(skill)
        for skill in uncached[max(0, budget):]:
            result[skill.name] = None
        return result

    async def warm(self, limit: int = 0) -> int:
        """后台批量预热：用 embed_text（单次 API 嵌一批）填充未缓存向量。

        由心跳维护周期调用，若干拍后覆盖全库；失败静默返回 0（可用性状态
        由 Embedder 后台探测机制掌管）。模型切换的待重建状态由 rebuild_all
        接管，此时普通预热让位。limit=0 时读配置 skills_warm_batch_size。
        """
        self._refresh_model()
        self._ensure_db_loaded()
        if self._rebuild_pending or self._build_state == "rebuilding":
            return 0
        limit = limit or _cfg_int("skills_warm_batch_size", 32)
        self._build_state = "warming"
        try:
            return await self._warm_batch(limit)
        finally:
            self._build_state = "idle"

    async def _warm_batch(self, limit: int) -> int:
        """嵌入一批未缓存技能向量（内部原语，warm 与 rebuild_all 共用）。"""
        embedder = self._embedder
        if embedder is None or not getattr(embedder, "available", False):
            return 0
        uncached: List[Skill] = []
        for skill in self._matchable_skills():
            if self.cached_vector(skill) is None:
                uncached.append(skill)
                if len(uncached) >= limit:
                    break
        if not uncached:
            return 0
        texts = [self._embedding_text(s) for s in uncached]
        try:
            vectors = await embedder.embed_text(texts)  # type: ignore[attr-defined]
        except Exception as exc:
            log(f"技能向量预热失败: {exc}", "DEBUG", tag="技能")
            return 0
        warmed = 0
        for skill, vec in zip(uncached, vectors, strict=False):
            if vec:
                self._cache_vector(self._vector_key(skill), vec)
                self._persist_skill_vector(skill, vec)
                warmed += 1
        if warmed:
            self.prune_stale_vectors()
            log(f"技能向量预热: {warmed}/{len(uncached)}（缓存 {len(self._vec_cache)}）", "DEBUG", tag="技能")
        return warmed

    async def rebuild_all(self, batch_size: int = 0) -> int:
        """模型切换后的全量向量重建：分批嵌入直到覆盖全部可匹配技能。

        人为手动切换 embedding 模型的配套动作——不允许新旧向量并存的
        过渡态，切换时缓存已整体清空（_refresh_model），这里一次性重建。
        防重入（心跳兜底与即时任务可能同时到达）；Embedder 故障时保持
        pending 标志不清，下一拍心跳重试。batch_size=0 时读配置
        skills_rebuild_batch_size。
        """
        if self._rebuild_lock is None:
            self._rebuild_lock = asyncio.Lock()
        if self._rebuild_lock.locked():
            return 0
        async with self._rebuild_lock:
            self._ensure_db_loaded()
            self._build_state = "rebuilding"
            self._rebuild_pending = False
            batch_size = batch_size or _cfg_int("skills_rebuild_batch_size", 32)
            # 进度追踪：总数在开始时快照，逐批更新 done
            skills = self._matchable_skills()
            self._build_progress = {"done": 0, "total": len(skills)}
            try:
                total = 0
                while True:
                    batch = await self._warm_batch(batch_size)
                    if not batch:
                        break
                    total += batch
                    self._build_progress["done"] = total
                # 完成判定以覆盖率为准：total=0 可能是"早已全部嵌入"（正常），
                # 只有存在未嵌入技能时才说明端点不可用
                uncached = [
                    s for s in skills if not self.is_embedded(s)
                ]
                if not uncached:
                    log(
                        f"技能向量全量重建完成: 新嵌入 {total} 个，"
                        f"覆盖 {len(skills)} 个（模型 {self._current_model}）",
                        tag="技能",
                    )
                    self._last_rebuild_at = time.time()
                    self._last_rebuild_count = total
                    self._last_rebuild_model = self._current_model or ""
                else:
                    # 嵌入端点不可用：保持待重建状态，心跳重试
                    self._rebuild_pending = True
                    log(
                        f"技能向量全量重建暂未完成（{len(uncached)} 个未嵌入，"
                        f"嵌入端点不可用），待心跳重试",
                        "WARNING", tag="技能",
                    )
                return total
            finally:
                self._build_state = "idle"
                self._build_progress = {"done": 0, "total": 0}

    def _cache_vector(self, key: str, vec: List[float]) -> None:
        if len(self._vec_cache) >= _EMBEDDING_CACHE_MAX:
            oldest = next(iter(self._vec_cache))
            self._vec_cache.pop(oldest, None)
        self._vec_cache[key] = vec

    # ------------------------------------------------------------------
    # 相似度
    # ------------------------------------------------------------------

    async def similar(
            self,
            text: str = "",
            *,
            vec: Optional[List[float]] = None,
            top_k: int = 10,
            exclude: str = "",
            min_similarity: float = 0.0,
            budget: int | None = None,
    ) -> List[Tuple[Skill, float]]:
        """与给定文本/向量最相似的技能（按相似度降序）。

        exclude 用于更新场景排除技能自身；Embedder 不可用时返回空。
        技能向量经预算化补算（冷缓存时部分技能本轮不参与语义比对）。
        """
        if vec is None:
            vec = await self.text_vector(text)
        if vec is None:
            return []
        candidates = [s for s in self._matchable_skills() if s.name != exclude]
        vectors = await self.ensure_vectors(candidates, budget=budget)
        scored: List[Tuple[Skill, float]] = []
        for skill in candidates:
            skill_vec = vectors.get(skill.name)
            if not skill_vec:
                continue
            sim = cosine_similarity(vec, skill_vec)
            if sim >= min_similarity:
                scored.append((skill, sim))
        scored.sort(key=lambda x: (x[1], x[0].name), reverse=True)
        return scored[:top_k]

    def _vector_fingerprint(self) -> Tuple[int, int, str]:
        """向量缓存指纹：(容量, 条目数, 首键)——缓存演进可被聚类缓存感知。"""
        first_key = next(iter(self._vec_cache), "")
        return (len(self._vec_cache), self._store.version, first_key)

    async def clusters(self, threshold: float | None = None) -> List[List[Skill]]:
        """相似聚类（union-find，纯确定性）：供合并议程与库健康报告。

        只在已有缓存向量间计算（不补算，避免报告路径放大嵌入成本）；
        结果按 (库版本, 向量缓存指纹) 缓存——O(N²) 余弦只在库内容或向量
        覆盖变化后重算一次，心跳每拍调用不再重复支付。
        """
        threshold = threshold if threshold is not None else _cfg_float("skills_merge_similarity", 0.80)
        cache_key = (threshold, *self._vector_fingerprint())
        if (
            self._cluster_cache
            and self._cluster_cache[0] == cache_key
        ):
            return self._cluster_cache[1]
        named = [
            (s, v) for s in self._matchable_skills()
            if (v := self.cached_vector(s)) is not None
        ]
        parent = {s.name: s.name for s, _ in named}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for i, (sa, va) in enumerate(named):
            for sb, vb in named[i + 1:]:
                if cosine_similarity(va, vb) >= threshold:
                    ra, rb = find(sa.name), find(sb.name)
                    if ra != rb:
                        parent[rb] = ra
        groups: Dict[str, List[Skill]] = {}
        for s, _ in named:
            groups.setdefault(find(s.name), []).append(s)
        result = [sorted(g, key=lambda x: x.name) for g in groups.values() if len(g) > 1]
        self._cluster_cache = (cache_key, result)
        return result

    # ------------------------------------------------------------------
    # 写入诊断（事实，非策略）
    # ------------------------------------------------------------------

    async def write_advisory(
            self,
            *,
            name: str,
            description: str = "",
            content: str = "",
            trigger_patterns: Optional[List[str]] = None,
            updating: bool = False,
    ) -> Dict[str, object]:
        """为一次拟议写入计算相关事实，供决策协议呈现给 AI。

        返回 facts 字典；为空 dict 表示无显著事实（快路径直通写入）。
        显著与否只是事实的呈现分级，最终决策（合并/新建/放弃）始终归 AI。
        """
        facts: Dict[str, object] = {}
        patterns = trigger_patterns or []
        normalized = self._store.normalize_name(name)

        if updating:
            existing = self._store.get(normalized)
            if existing is None:
                return {"not_found": normalized}
            # 无实质变化：新旧正文文本相似度极高 → 提请确认（防评审轮次空转重写）。
            # SequenceMatcher 全量比对最坏 O(n²)，先截断 + quick_ratio 上界短路
            if content and content != existing.content:
                matcher = SequenceMatcher(None, existing.content[:4000], content[:4000])
                if matcher.quick_ratio() >= 0.95:
                    ratio = matcher.ratio()
                    if ratio >= 0.95:
                        facts["no_substantive_change"] = round(ratio, 3)
        else:
            if self._store.exists(normalized):
                facts["existing_same_name"] = normalized

        # 语义相近技能（查重核心事实；向量文本用拟议的 name+desc+patterns）
        prospective = Skill(
            name=normalized, description=description,
            trigger_patterns=patterns, content=content,
        )
        vec = await self.skill_vector(prospective)
        if vec is not None:
            similar = await self.similar(
                vec=vec, top_k=5, exclude=normalized,
                min_similarity=_cfg_float("skills_similar_threshold", 0.83),
                budget=8,  # 写入路径预算收紧：默认 16 是检索路的稳态预算
            )
            if similar:
                facts["similar_skills"] = [
                    {"name": s.name, "similarity": round(sim, 3),
                     "description": s.description, "use_count": s.use_count}
                    for s, sim in similar
                ]

        # 触发词碰撞：拟议词已是多个其他技能的触发词（匹配竞争事实）
        collision_limit = _cfg_int("skills_trigger_collision_limit", 3)
        if patterns:
            owners = self._trigger_owners(exclude=normalized)
            collisions = [
                {"pattern": p, "also_in": sorted(owners[p])}
                for p in dict.fromkeys(patterns)
                if len(owners.get(p, ())) >= collision_limit
            ]
            if collisions:
                facts["trigger_collisions"] = collisions

        # 容量水位（参考值而非上限：超水位作为事实呈现，治理决策归 AI/策展）
        active = len(self._matchable_skills())
        capacity = _cfg_int("skills_capacity_reference", 100)
        if active >= capacity:
            facts["capacity"] = {"active": active, "reference": capacity}

        # 正文超建议长度（事实提示，帮助内容收敛）
        advise_chars = _cfg_int("skills_body_advise_chars", 2000)
        if len(content) > advise_chars:
            facts["body_over_advice"] = {"chars": len(content), "advice": advise_chars}

        return facts

    def _trigger_owners(self, exclude: str = "") -> Dict[str, List[str]]:
        """触发词 → 持有该词的技能名集合（碰撞检测的底层数据）。"""
        owners: Dict[str, List[str]] = {}
        for skill in self._matchable_skills():
            if skill.name == exclude:
                continue
            for pattern in skill.trigger_patterns:
                owners.setdefault(pattern, []).append(skill.name)
        return owners

    # ------------------------------------------------------------------
    # 库健康快照（元数据事实，无需向量）
    # ------------------------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        """技能库健康快照：计数、零使用、高匹配零消费、触发词碰撞、合并信号。

        纯元数据计算（无向量依赖），供库健康工具、评审上下文与策展议程共用。
        """
        skills = self._all_skills()
        active = [s for s in skills if s.state == SkillState.ACTIVE]
        stale = [s for s in skills if s.state == SkillState.STALE]
        archived = [s for s in skills if s.state == SkillState.ARCHIVED]
        now = time.time()
        probation = _cfg_int("skills_probation_days", 14) * 86400.0

        return {
            "counts": {
                "active": len(active), "stale": len(stale),
                "archived": len(archived), "pinned": sum(1 for s in skills if s.pinned),
            },
            "embedding": self.embedding_stats(),
            # 解析失败的技能（严格契约下的脏文件）：呈现给 AI 决策修复
            # （读取原文件后用 create_skill 同名重建即恢复，覆盖脏文件）
            "parse_errors": self._store.parse_errors,
            "capacity_reference": _cfg_int("skills_capacity_reference", 100),
            # 零参与：过了试用期既没被真实用过也没被检索到（沉淀失败的直接证据）
            "zero_engagement": [
                s.name for s in active
                if s.use_count == 0 and s.match_count == 0
                and now - s.created_at >= probation
            ],
            # 高匹配零消费：常被注入但从未被真正用上——内容冗余/已内化/行为类候选
            "high_match_low_use": [
                {"name": s.name, "match_count": s.match_count}
                for s in active + stale
                if s.match_count >= _cfg_int("skills_high_match_threshold", 20)
                and s.use_count == 0
            ],
            # 高频改写：patch 多次仍未收敛的技能
            "patch_churn": [
                {"name": s.name, "patch_count": s.patch_count}
                for s in skills if s.patch_count >= 8
            ],
            # 触发词碰撞表（词 → 持有技能，≥collision_limit 才收录）
            "trigger_collisions": self._collision_map(),
            # 检索端近重复折叠的累计信号（每对按得分保留者→被折叠者）
            "merge_signals": [
                {"kept": kept, "folded": folded, "count": count}
                for (kept, folded), count in
                sorted(self._merge_signals.items(), key=lambda x: -x[1])
            ][:20],
        }

    def _collision_map(self) -> Dict[str, List[str]]:
        limit = _cfg_int("skills_trigger_collision_limit", 3)
        owners = self._trigger_owners()
        return {p: sorted(ns) for p, ns in owners.items() if len(ns) >= limit}

    def record_merge_signal(self, kept: str, folded: str) -> None:
        """记录一次检索端近重复折叠（kept 得分更高被保留，folded 被折叠）。"""
        pair = (kept, folded)
        self._merge_signals[pair] = self._merge_signals.get(pair, 0) + 1
        # 有界：冗余簇内的组合爆炸不会让映射无限增长
        if len(self._merge_signals) > 256:
            oldest = next(iter(self._merge_signals))
            self._merge_signals.pop(oldest, None)
