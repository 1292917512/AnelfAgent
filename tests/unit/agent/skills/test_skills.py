"""技能自学习系统（agent.skills）单元测试。"""

from __future__ import annotations

import json
import re
import time
import zlib
from types import SimpleNamespace

import pytest

from agent.skills.curator import SkillCurator
from agent.skills.skill_index import SkillIndex
from agent.skills.skill_matcher import SkillMatcher
from agent.skills.skill_store import (
    Skill,
    SkillState,
    SkillStore,
    parse_skill_md,
    render_skill_md,
)


@pytest.fixture
def store(tmp_path) -> SkillStore:
    return SkillStore(str(tmp_path / "skills"))


@pytest.fixture(autouse=True)
def _isolate_vector_db(tmp_path, monkeypatch) -> None:
    """隔离技能向量库：默认路径指向临时文件，不读写真实 data 目录。"""
    from core.path import ConfigPaths
    monkeypatch.setattr(
        ConfigPaths, "SQLITE_DB", str(tmp_path / "data" / "agent.sqlite3"),
    )


class FakeEmbedder:
    """确定性词袋嵌入：英文词 → crc32 维度计数，共享词越多余弦越高。"""

    def __init__(self, dims: int = 64, model: str = "fake-emb-v1") -> None:
        self.dims = dims
        self.client_name = model
        self.call_count = 0  # 嵌入调用计数（验证零重嵌恢复）

    @property
    def available(self) -> bool:
        return True

    async def embed_query(self, text: str) -> list[float]:
        self.call_count += 1
        vec = [0.0] * self.dims
        for tok in re.findall(r"[a-z0-9]+", text.lower()):
            vec[zlib.crc32(tok.encode()) % self.dims] += 1.0
        return vec

    async def embed_text(self, texts: list[str]) -> list[list[float]]:
        self.call_count += len(texts)
        return [await self.embed_query(t) for t in texts]


class TestSkillMdFormat:
    def test_roundtrip(self) -> None:
        skill = Skill(
            name="web-research",
            description="网络调研流程",
            trigger_patterns=["调研", "查资料"],
            content="# 步骤\n1. 搜索\n2. 总结",
            use_count=3,
            patch_count=1,
        )
        text = render_skill_md(skill)
        meta, body = parse_skill_md(text)
        assert meta["name"] == "web-research"
        assert meta["description"] == "网络调研流程"
        assert meta["trigger_patterns"] == ["调研", "查资料"]
        assert meta["use_count"] == 3
        assert meta["state"] == "active"
        assert "步骤" in body

    def test_parse_without_frontmatter(self) -> None:
        meta, body = parse_skill_md("# 纯正文")
        assert meta == {} and body == "# 纯正文"

    def test_legacy_frontmatter_defaults(self, store: SkillStore) -> None:
        """旧格式 SKILL.md（无 match_count/rationale 等新字段）解析即迁移：默认值兜底。"""
        legacy = (
            "---\n"
            "name: legacy-skill\n"
            "description: 旧版技能\n"
            "use_count: 5\n"
            "state: active\n"
            "---\n\n旧正文\n"
        )
        target = store.skills_dir / "legacy-skill"
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text(legacy, encoding="utf-8")
        skill = store.get("legacy-skill")
        assert skill is not None
        assert skill.use_count == 5 and skill.match_count == 0
        assert skill.rationale == "" and skill.merged_into == ""
        assert skill.last_match_at == 0.0
        assert skill.content == "旧正文"

    def test_parse_error_registered_and_cleared(self, store: SkillStore) -> None:
        """严格解析失败 → 登记为健康事实（抛给 AI 决策）；修复后自动清除。"""
        dirty = (
            "---\n"
            "name: broken-skill\n"
            "merged_into:\n"
            "- some-list-value\n"
            "---\n\n正文\n"
        )
        target = store.skills_dir / "broken-skill"
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text(dirty, encoding="utf-8")

        assert store.get("broken-skill") is None  # 严格解析：不兜底
        errors = store.parse_errors
        assert "broken-skill" in errors
        assert "merged_into" in errors["broken-skill"] or "ValidationError" in errors["broken-skill"]

        # 修复通道：create 同名重建覆盖脏文件（严格模式下 get 为 None 走新建）
        store.create("broken-skill", "修复后的描述", "修复后的内容")
        assert store.parse_errors == {}
        skill = store.get("broken-skill")
        assert skill is not None and skill.description == "修复后的描述"

    def test_parse_error_cleared_on_delete(self, store: SkillStore) -> None:
        target = store.skills_dir / "broken-skill"
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text("---\nmerged_into:\n- x\n---\n\nc\n", encoding="utf-8")
        assert store.get("broken-skill") is None
        assert "broken-skill" in store.parse_errors
        store.delete("broken-skill")
        assert store.parse_errors == {}

    def test_snapshot_includes_parse_errors(self, store: SkillStore) -> None:
        """健康快照透传解析失败事实（评审上下文与库健康工具共用）。"""
        target = store.skills_dir / "broken-skill"
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text("---\nmerged_into:\n- x\n---\n\nc\n", encoding="utf-8")
        assert store.get("broken-skill") is None
        snapshot = SkillIndex(store).snapshot()
        assert "broken-skill" in snapshot["parse_errors"]


class TestSkillStore:
    def test_create_and_get(self, store: SkillStore) -> None:
        store.create("web-research", "调研", "# 内容", ["调研"])
        skill = store.get("web-research")
        assert skill is not None
        assert skill.description == "调研"
        assert skill.trigger_patterns == ["调研"]
        assert skill.state == SkillState.ACTIVE

    def test_create_existing_patches(self, store: SkillStore) -> None:
        store.create("s1", "v1", "内容1")
        store.create("s1", "v2", "内容2", ["新词"])
        skill = store.get("s1")
        assert skill.content == "内容2"
        assert skill.patch_count == 1
        assert "新词" in skill.trigger_patterns

    def test_patch(self, store: SkillStore) -> None:
        store.create("s1", "desc", "旧内容")
        patched = store.patch("s1", content="新内容", add_trigger_patterns=["a", "b"])
        assert patched.content == "新内容"
        assert patched.trigger_patterns == ["a", "b"]
        assert patched.patch_count == 1
        assert store.patch("nonexistent") is None

    def test_list_excludes_archived(self, store: SkillStore) -> None:
        store.create("s1", "d", "c")
        store.create("s2", "d", "c")
        store.set_state("s2", SkillState.ARCHIVED)
        names = [s.name for s in store.list_skills()]
        assert names == ["s1"]
        names_all = [s.name for s in store.list_skills(include_archived=True)]
        assert set(names_all) == {"s1", "s2"}

    def test_record_use(self, store: SkillStore) -> None:
        store.create("s1", "d", "c")
        store.record_use("s1")
        assert store.get("s1").use_count == 1

    def test_record_match_no_touch(self, store: SkillStore) -> None:
        """检索注入只计匹配：不刷 use_count、不刷活动时间（被匹配≠被消费）。"""
        store.create("s1", "d", "c")
        before = store.get("s1").last_activity_at
        time.sleep(0.01)
        store.record_match("s1")
        skill = store.get("s1")
        assert skill.match_count == 1 and skill.use_count == 0
        assert skill.last_match_at > 0.0
        assert skill.last_activity_at == before

    def test_record_use_no_touch_mode(self, store: SkillStore) -> None:
        """touch=False 的使用（如 get_skill 查阅）：计数但不刷新活动时间。"""
        store.create("s1", "d", "c")
        before = store.get("s1").last_activity_at
        time.sleep(0.01)
        store.record_use("s1", touch=False)
        skill = store.get("s1")
        assert skill.use_count == 1 and skill.last_activity_at == before
        store.record_use("s1")
        assert store.get("s1").use_count == 2
        assert store.get("s1").last_activity_at > before

    def test_merge_archives_sources(self, store: SkillStore) -> None:
        store.create("a", "desc a", "内容 a", ["qa"])
        store.create("b", "desc b", "内容 b", ["qb"])
        merged = store.merge(["a"], "b", content="合并后内容", add_trigger_patterns=["qa"])
        assert merged is not None and merged.content == "合并后内容"
        assert "qa" in merged.trigger_patterns
        assert "合并自: a" in merged.rationale
        src = store.get("a")
        assert src.state == SkillState.ARCHIVED and src.merged_into == "b"
        assert [s.name for s in store.list_skills()] == ["b"]

    def test_merge_target_missing(self, store: SkillStore) -> None:
        store.create("a", "d", "c")
        assert store.merge(["a"], "ghost", content="x") is None

    def test_rationale_roundtrip(self, store: SkillStore) -> None:
        store.create("s1", "d", "c", rationale="与 X 差异：覆盖容器路径变体")
        assert store.get("s1").rationale == "与 X 差异：覆盖容器路径变体"

    def test_delete(self, store: SkillStore) -> None:
        store.create("s1", "d", "c")
        assert store.delete("s1")
        assert store.get("s1") is None
        assert not store.delete("s1")

    def test_name_normalization(self, store: SkillStore) -> None:
        store.create("Web Research 调研!", "d", "c")
        assert store.get("web-research") is not None

    def test_pinned(self, store: SkillStore) -> None:
        store.create("s1", "d", "c")
        store.set_pinned("s1", True)
        assert store.get("s1").pinned is True


class TestSkillMatcher:
    async def test_keyword_match(self, store: SkillStore) -> None:
        store.create("web-research", "网络调研", "内容", ["调研", "搜索"])
        store.create("code-review", "代码审查", "内容", ["审查", "review"])
        matcher = SkillMatcher(store)
        matched = await matcher.match(["帮我调研一下这个话题"])
        assert matched and matched[0][0].name == "web-research"

    async def test_no_match(self, store: SkillStore) -> None:
        store.create("web-research", "网络调研", "内容", ["调研"])
        matcher = SkillMatcher(store)
        matched = await matcher.match(["完全无关的内容 xyz"])
        assert matched == []

    async def test_archived_not_matched(self, store: SkillStore) -> None:
        store.create("s1", "d", "c", ["调研"])
        store.set_state("s1", SkillState.ARCHIVED)
        matcher = SkillMatcher(store)
        assert await matcher.match(["调研"]) == []

    async def test_top_k(self, store: SkillStore) -> None:
        for i in range(5):
            store.create(f"s{i}", "d", "c", ["调研"])
        matcher = SkillMatcher(store)
        matched = await matcher.match(["调研"], top_k=2)
        assert len(matched) == 2

    async def test_redundant_fold_and_merge_signal(self, store: SkillStore) -> None:
        """近重复折叠：同簇技能只注入得分更高者，折叠记入合并信号。"""
        store.create("qq-media-fallback-a", "qq media download fallback",
                     "内容", ["qq media"])
        store.create("qq-media-fallback-b", "qq media download fallback",
                     "内容", ["qq media"])
        matcher = SkillMatcher(store, FakeEmbedder())
        matched = await matcher.match(["qq media download fallback"], top_k=3)
        assert len(matched) == 1
        signals = matcher.index.snapshot()["merge_signals"]
        assert signals and signals[0]["count"] == 1

    async def test_distinct_skills_not_folded(self, store: SkillStore) -> None:
        """不同域技能不折叠：各占各的坑位。"""
        store.create("qq-media-fallback", "qq media download fallback",
                     "内容", ["qq media"])
        store.create("cooking-recipe", "cooking recipe pasta dinner",
                     "内容", ["cooking"])
        matcher = SkillMatcher(store, FakeEmbedder())
        matched = await matcher.match(["qq media and cooking recipe"], top_k=3)
        assert len(matched) == 2


class TestSkillCurator:
    def test_active_to_stale(self, store: SkillStore) -> None:
        skill = store.create("s1", "d", "c")
        # 模拟 40 天未活动
        skill.last_activity_at = time.time() - 40 * 86400
        store.save(skill)
        curator = SkillCurator(store)
        report = curator.apply_automatic_transitions()
        assert report["staled"] == ["s1"]
        assert store.get("s1").state == SkillState.STALE

    def test_stale_to_archived(self, store: SkillStore) -> None:
        skill = store.create("s1", "d", "c")
        skill.state = SkillState.STALE
        skill.last_activity_at = time.time() - 100 * 86400
        store.save(skill)
        curator = SkillCurator(store)
        report = curator.apply_automatic_transitions()
        assert report["archived"] == ["s1"]
        assert store.get("s1").state == SkillState.ARCHIVED

    def test_pinned_exempt(self, store: SkillStore) -> None:
        skill = store.create("s1", "d", "c")
        skill.pinned = True
        skill.last_activity_at = time.time() - 200 * 86400
        store.save(skill)
        curator = SkillCurator(store)
        report = curator.apply_automatic_transitions()
        assert report["staled"] == [] and report["archived"] == []
        assert report["skipped_pinned"] == 1

    def test_recent_untouched(self, store: SkillStore) -> None:
        store.create("s1", "d", "c")
        curator = SkillCurator(store)
        report = curator.apply_automatic_transitions()
        assert report["staled"] == [] and report["archived"] == []

    def test_probation_zero_engagement_staled(self, store: SkillStore) -> None:
        """试用期快筛：零参与（无使用无匹配）的新技能直接降级。"""
        skill = store.create("s1", "d", "c")
        skill.created_at = time.time() - 20 * 86400
        store.save(skill)
        curator = SkillCurator(store)
        report = curator.apply_automatic_transitions()
        assert report["staled"] == ["s1"]

    def test_probation_matched_not_staled(self, store: SkillStore) -> None:
        """被检索到的试用期技能不降级：match 也是参与的证据。"""
        skill = store.create("s1", "d", "c")
        skill.created_at = time.time() - 20 * 86400
        store.save(skill)
        store.record_match("s1")
        curator = SkillCurator(store)
        report = curator.apply_automatic_transitions()
        assert report["staled"] == []

    def test_stale_soft_keep_on_match(self, store: SkillStore) -> None:
        """stale 软保留：仍被检索注入的技能不归档（有召回价值）。"""
        skill = store.create("s1", "d", "c")
        skill.state = SkillState.STALE
        skill.last_activity_at = time.time() - 100 * 86400
        store.save(skill)
        store.record_match("s1")  # 最近仍被匹配
        curator = SkillCurator(store)
        report = curator.apply_automatic_transitions()
        assert report["archived"] == []
        assert store.get("s1").state == SkillState.STALE

    async def test_build_agenda_clusters(self, store: SkillStore) -> None:
        """议程生成：相似聚类进合并候选，元数据事实完整呈现。"""
        store.create("qq-media-fallback-a", "qq media download fallback", "c", ["qq media"])
        store.create("qq-media-fallback-b", "qq media download fallback", "c", ["qq media"])
        store.create("cooking-recipe", "cooking recipe pasta dinner", "c", ["cooking"])
        index = SkillIndex(store, FakeEmbedder())
        await index.warm()  # 议程聚类只读缓存：先按生产流预热
        curator = SkillCurator(store, index)
        agenda = await curator.build_agenda()
        names = {c["name"] for cluster in agenda["merge_candidates"] for c in cluster}
        assert names == {"qq-media-fallback-a", "qq-media-fallback-b"}
        assert agenda["counts"]["active"] == 3


class TestSkillIndexFacts:
    async def test_write_advisory_similar(self, store: SkillStore) -> None:
        """写入诊断：与现有技能语义相近 → 相近事实（呈现给 AI，不是拒绝）。"""
        store.create("qq-media-fallback", "qq media download fallback flow",
                     "c", ["qq media"])
        index = SkillIndex(store, FakeEmbedder())
        facts = await index.write_advisory(
            name="qq-media-recovery", description="qq media download fallback flow",
            content="x", trigger_patterns=["qq media"],
        )
        similar = facts.get("similar_skills")
        assert similar and similar[0]["name"] == "qq-media-fallback"
        assert similar[0]["similarity"] >= 0.83

    async def test_write_advisory_distinct_no_facts(self, store: SkillStore) -> None:
        """不同域拟议写入无显著事实：快路径直通（空 facts）。"""
        store.create("qq-media-fallback", "qq media download fallback", "c")
        index = SkillIndex(store, FakeEmbedder())
        facts = await index.write_advisory(
            name="cooking-recipe", description="cooking recipe pasta dinner",
            content="x", trigger_patterns=["pasta"],
        )
        assert facts == {}

    async def test_write_advisory_no_substantive_change(self, store: SkillStore) -> None:
        """更新诊断：新旧正文几乎一致 → 无实质变化事实（防空转重写）。"""
        body = "步骤一 " * 60
        store.create("s1", "d", body)
        index = SkillIndex(store)
        facts = await index.write_advisory(
            name="s1", content=body + " ", updating=True,
        )
        assert "no_substantive_change" in facts

    async def test_write_advisory_trigger_collision(self, store: SkillStore) -> None:
        """触发词碰撞：泛词已被多个技能持有时呈现碰撞事实。"""
        for i in range(3):
            store.create(f"s{i}", "d", "c", [f"独有词{i}", "泛词"])
        index = SkillIndex(store)
        facts = await index.write_advisory(
            name="s3", description="d", content="c", trigger_patterns=["泛词"],
        )
        collisions = facts.get("trigger_collisions")
        assert collisions and collisions[0]["pattern"] == "泛词"
        assert len(collisions[0]["also_in"]) == 3

    def test_snapshot_metadata_facts(self, store: SkillStore) -> None:
        """库快照：计数/零参与/碰撞等元数据事实（无向量依赖）。"""
        old = store.create("old-zero", "d", "c")
        old.created_at = time.time() - 20 * 86400
        store.save(old)
        for i in range(3):
            store.create(f"s{i}", "d", "c", ["泛词"])
        snapshot = SkillIndex(store).snapshot()
        assert snapshot["counts"]["active"] == 4
        assert "old-zero" in snapshot["zero_engagement"]
        assert "泛词" in snapshot["trigger_collisions"]

    async def test_clusters_deterministic(self, store: SkillStore) -> None:
        store.create("a-one", "alpha beta gamma topic", "c")
        store.create("a-two", "alpha beta gamma topic", "c")
        store.create("b-one", "delta epsilon zeta topic", "c")
        index = SkillIndex(store, FakeEmbedder())
        await index.warm()  # 聚类只读缓存：先按生产流预热
        clusters = await index.clusters(threshold=0.8)
        flat = [{s.name for s in cluster} for cluster in clusters]
        assert {"a-one", "a-two"} in flat
        assert all("b-one" not in group for group in flat)
        # 聚类缓存：库与向量未变时直接复用（同对象语义等价）
        assert await index.clusters(threshold=0.8) == clusters
        # 库变更后缓存失效：新技能进入聚类范围
        store.create("a-three", "alpha beta gamma topic", "c")
        await index.warm()
        regrouped = await index.clusters(threshold=0.8)
        assert {"a-one", "a-two", "a-three"} in [{s.name for s in c} for c in regrouped]

    async def test_ensure_vectors_budget(self, store: SkillStore) -> None:
        """预算化补算：超出 budget 的技能本轮拿不到向量（防冷启动串行风暴）。"""
        for i in range(5):
            store.create(f"s{i}", f"skill number {i}", "c")
        index = SkillIndex(store, FakeEmbedder())
        skills = store.list_skills()
        vectors = await index.ensure_vectors(skills, budget=2)
        computed = [name for name, v in vectors.items() if v is not None]
        assert len(computed) == 2
        # 再次调用：已缓存的直接命中，剩余预算继续补算
        vectors2 = await index.ensure_vectors(skills, budget=2)
        assert sum(1 for v in vectors2.values() if v is not None) == 4

    async def test_warm_batch(self, store: SkillStore) -> None:
        """批量预热：一次 embed_text 填一批向量，幂等（全热后返回 0）。"""
        for i in range(3):
            store.create(f"s{i}", f"skill number {i}", "c")
        embedder = FakeEmbedder()
        index = SkillIndex(store, embedder)
        warmed = await index.warm(limit=4)
        assert warmed == 3
        assert await index.warm(limit=4) == 0
        assert index.cached_vector(store.get("s0")) is not None

    async def test_model_switch_full_rebuild(self, store: SkillStore) -> None:
        """模型手动切换：清空全部向量 → 标记重建 → rebuild_all 一次性全量重建。"""
        store.create("s1", "alpha beta topic", "c")
        store.create("s2", "delta epsilon topic", "c")
        embedder = FakeEmbedder(model="fake-emb-v1")
        index = SkillIndex(store, embedder)
        await index.warm()
        assert index.embedding_stats()["embedded"] == 2
        assert not index.rebuild_pending

        # 人为切换模型：检测即全清，无新旧并存过渡态
        index._embedder = FakeEmbedder(model="fake-emb-v2")
        await index.warm()  # warm 入口检测切换
        assert index.rebuild_pending
        assert index.embedding_stats()["embedded"] == 0
        assert index.embedding_stats()["model"] == "fake-emb-v2"
        assert index.embedding_stats()["rebuilding"] is True

        # rebuild_all 一次性全量重建（不分拍渐进）
        rebuilt = await index.rebuild_all()
        assert rebuilt == 2
        stats = index.embedding_stats()
        assert stats["embedded"] == 2 and stats["cache_keys"] == 2
        assert not index.rebuild_pending and not stats["rebuilding"]

    async def test_model_switch_rebuild_retry_on_failure(self, store: SkillStore) -> None:
        """重建失败时保持 pending（心跳重试），不静默丢状态。"""
        store.create("s1", "alpha beta topic", "c")
        index = SkillIndex(store, FakeEmbedder(model="fake-emb-v1"))
        await index.warm()

        class DeadEmbedder(FakeEmbedder):
            @property
            def available(self) -> bool:
                return False

        index._embedder = DeadEmbedder(model="fake-emb-dead")
        index._refresh_model()
        assert index.rebuild_pending
        rebuilt = await index.rebuild_all()
        assert rebuilt == 0 and index.rebuild_pending  # 端点不可用，待重试

    async def test_prune_stale_vectors(self, store: SkillStore) -> None:
        """死键清理：删除后旧向量键在显式 prune 时清掉；内容变更在 embed_now 后清掉。"""
        store.create("s1", "alpha beta topic", "c")
        store.create("s2", "delta epsilon topic", "c")
        index = SkillIndex(store, FakeEmbedder())
        await index.warm()
        keys_before = len(index._vec_cache)
        store.delete("s2")
        index.prune_stale_vectors()
        assert len(index._vec_cache) == keys_before - 1
        stats = index.embedding_stats()
        assert stats["embedded"] == 1 and stats["total"] == 1

    async def test_embed_now_after_update(self, store: SkillStore) -> None:
        """CRUD 即时同步：更新表征后 embed_now 重嵌新文本，is_embedded 即时为真。"""
        store.create("s1", "alpha beta topic", "c")
        index = SkillIndex(store, FakeEmbedder())
        assert not index.is_embedded(store.get("s1"))
        assert await index.embed_now("s1")
        assert index.is_embedded(store.get("s1"))
        store.patch("s1", description="completely different gamma domain")
        assert not index.is_embedded(store.get("s1"))  # 新文本未嵌入
        assert await index.embed_now("s1")
        assert index.is_embedded(store.get("s1"))


class TestSkillVectorPersistence:
    """向量持久化：重启零重嵌恢复（商业级核心验收）。"""

    @pytest.fixture
    def db_path(self, tmp_path) -> str:
        return str(tmp_path / "skill_vectors.sqlite3")

    async def test_restart_zero_reembed(self, store: SkillStore, db_path: str) -> None:
        """嵌入 → 关闭 → 新实例（模拟重启）→ 向量从 SQLite 恢复，零嵌入调用。"""
        store.create("s1", "alpha beta topic", "c")
        store.create("s2", "delta epsilon topic", "c")
        first = SkillIndex(store, FakeEmbedder(), db_path=db_path)
        await first.warm()
        assert first.embedding_stats()["embedded"] == 2
        calls_after_first = first._embedder.call_count
        assert calls_after_first > 0  # 首次确实嵌入了

        # 模拟重启：全新实例 + 全新 embedder（计数归零）
        second_embedder = FakeEmbedder()
        second = SkillIndex(store, second_embedder, db_path=db_path)
        stats = second.embedding_stats()
        assert stats["embedded"] == 2
        assert second_embedder.call_count == 0  # 零嵌入调用恢复
        assert stats["rebuilding"] is False

    async def test_restart_after_model_switch_rebuilds(self, store: SkillStore, db_path: str) -> None:
        """切换模型后重启：旧模型向量作废清除，标记全量重建。"""
        store.create("s1", "alpha beta topic", "c")
        first = SkillIndex(store, FakeEmbedder(model="fake-emb-v1"), db_path=db_path)
        await first.warm()

        second = SkillIndex(store, FakeEmbedder(model="fake-emb-v2"), db_path=db_path)
        stats = second.embedding_stats()
        assert stats["embedded"] == 0
        assert second.rebuild_pending
        # 重建后恢复
        rebuilt = await second.rebuild_all()
        assert rebuilt == 1
        assert second.embedding_stats()["embedded"] == 1

    async def test_content_change_reembeds(self, store: SkillStore, db_path: str) -> None:
        """内容变更 → 重启时该行失效清除并重嵌（text_hash 失配）。"""
        store.create("s1", "alpha beta topic", "c")
        first = SkillIndex(store, FakeEmbedder(), db_path=db_path)
        await first.warm()
        store.patch("s1", description="completely different omega domain")

        second_embedder = FakeEmbedder()
        second = SkillIndex(store, second_embedder, db_path=db_path)
        assert second.embedding_stats()["embedded"] == 0
        assert second.rebuild_pending  # 失效行已清，待重建
        await second.rebuild_all()
        assert second.embedding_stats()["embedded"] == 1

    async def test_delete_syncs_db(self, store: SkillStore, db_path: str) -> None:
        """删除技能 → prune 同步删除持久化行，重启后不复活。"""
        store.create("s1", "alpha beta topic", "c")
        store.create("s2", "delta epsilon topic", "c")
        first = SkillIndex(store, FakeEmbedder(), db_path=db_path)
        await first.warm()
        store.delete("s2")
        first.prune_stale_vectors()

        second = SkillIndex(store, FakeEmbedder(), db_path=db_path)
        stats = second.embedding_stats()
        assert stats["embedded"] == 1 and stats["total"] == 1
        assert stats["rebuilding"] is False

    async def test_single_embed_persisted(self, store: SkillStore, db_path: str) -> None:
        """单技能 embed_now 同样持久化（行内按钮操作重启后不失效）。"""
        store.create("s1", "alpha beta topic", "c")
        first = SkillIndex(store, FakeEmbedder(), db_path=db_path)
        await first.embed_now("s1")

        second = SkillIndex(store, FakeEmbedder(), db_path=db_path)
        assert second.is_embedded(store.get("s1"))
        assert second._embedder.call_count == 0

    async def test_observer_index_never_touches_db(self, store: SkillStore, db_path: str) -> None:
        """无 embedder 的旁观者索引（Web 健康报告临时实例）绝不触碰持久层。

        回归：此前 library_health 的临时索引以空模型名校验 DB，
        把真模型存的向量全部误判失效删除——每次 Web 刷新清空全库。
        """
        store.create("s1", "alpha beta topic", "c")
        owner = SkillIndex(store, FakeEmbedder(), db_path=db_path)
        await owner.embed_now("s1")

        # 旁观者索引：Web library_health 同款构造（无 embedder）
        observer = SkillIndex(store, db_path=db_path)
        observer.snapshot()           # library_health 的调用路径
        observer.build_state()
        observer.prune_stale_vectors()
        assert observer.is_embedded(store.get("s1")) is False  # 旁观者本就无语义能力

        # 真索引重启恢复：向量必须还在
        second = SkillIndex(store, FakeEmbedder(), db_path=db_path)
        assert second.is_embedded(store.get("s1"))
        assert second._embedder.call_count == 0

    async def test_unready_client_never_invalidates_db(self, store: SkillStore, db_path: str) -> None:
        """客户端未就绪（client_name 空串）时不做任何失效判定。

        回归：启动/热重载窗口期 client_name 为 ""，加载校验会把
        真模型存的向量全部误判失效——"不知道"不等于"无模型"。
        """
        store.create("s1", "alpha beta topic", "c")
        owner = SkillIndex(store, FakeEmbedder(model="real-model"), db_path=db_path)
        await owner.embed_now("s1")

        # 模拟重启早期：embedding 客户端尚未注册（client_name 为空）
        unready = FakeEmbedder(model="")
        idx = SkillIndex(store, unready, db_path=db_path)
        stats = idx.embedding_stats()  # 触发加载尝试
        assert stats["embedded"] == 0  # 未加载（延迟到客户端就绪）
        assert not idx.rebuild_pending  # 不做失效判定

        # 客户端就绪后：正常恢复，DB 未受损
        ready = SkillIndex(store, FakeEmbedder(model="real-model"), db_path=db_path)
        assert ready.is_embedded(store.get("s1"))
        assert ready._embedder.call_count == 0


class TestSkillTools:
    async def _patch_tools(self, store: SkillStore, monkeypatch) -> None:
        from agent.skills import tools as skill_tools
        monkeypatch.setattr(skill_tools, "_store", store)
        monkeypatch.setattr(skill_tools, "_matcher", SkillMatcher(store, FakeEmbedder()))

    async def test_create_and_list(self, store: SkillStore, monkeypatch) -> None:
        await self._patch_tools(store, monkeypatch)
        from agent.skills import tools as skill_tools

        result = json.loads(await skill_tools.create_skill(
            name="t1", description="测试", content="内容", trigger_patterns="测试,示例",
        ))
        assert result["ok"]

        listed = json.loads(skill_tools.list_skills())
        assert listed["count"] == 1
        assert listed["skills"][0]["name"] == "t1"

        detail = json.loads(skill_tools.get_skill("t1"))
        assert detail["content"] == "内容"

        updated = json.loads(await skill_tools.update_skill("t1", content="完全不同的新内容"))
        assert updated["patch_count"] == 1

        searched = json.loads(await skill_tools.search_skills("测试"))
        assert len(searched["local"]) >= 1

    async def test_decision_protocol_create(self, store: SkillStore, monkeypatch) -> None:
        """决策协议：相近拟议创建首次返回诊断（不写入），带 decision 回执后写入。"""
        await self._patch_tools(store, monkeypatch)
        from agent.skills import tools as skill_tools

        store.create("qq-media-fallback", "qq media download fallback flow",
                     "内容", ["qq media"])
        first = json.loads(await skill_tools.create_skill(
            name="qq-media-recovery", description="qq media download fallback flow",
            content="新内容", trigger_patterns="qq media",
        ))
        assert first["status"] == "needs_decision"
        assert first["facts"]["similar_skills"][0]["name"] == "qq-media-fallback"
        assert {o["action"] for o in first["options"]} == {"merge", "confirm", "abort"}
        assert store.get("qq-media-recovery") is None  # 未写入

        second = json.loads(await skill_tools.create_skill(
            name="qq-media-recovery", description="qq media download fallback flow",
            content="新内容", trigger_patterns="qq media",
            decision="覆盖容器路径变体，与现有技能差异明确",
        ))
        assert second["ok"]
        skill = store.get("qq-media-recovery")
        assert skill is not None
        assert "容器路径变体" in skill.rationale

    async def test_decision_protocol_update_noop(self, store: SkillStore, monkeypatch) -> None:
        """决策协议：无实质变化的更新先返回诊断，带理由确认后写入。"""
        await self._patch_tools(store, monkeypatch)
        from agent.skills import tools as skill_tools

        body = "步骤一 " * 60
        store.create("t1", "d", body)
        first = json.loads(await skill_tools.update_skill("t1", content=body + " "))
        assert first["status"] == "needs_decision"
        assert "no_substantive_change" in first["facts"]

        second = json.loads(await skill_tools.update_skill(
            "t1", content=body + " ", decision="补充收尾步骤",
        ))
        assert second["ok"]

    async def test_merge_skills_tool(self, store: SkillStore, monkeypatch) -> None:
        await self._patch_tools(store, monkeypatch)
        from agent.skills import tools as skill_tools

        store.create("src-a", "d", "内容 a")
        store.create("target-b", "d", "内容 b")
        result = json.loads(skill_tools.merge_skills(
            sources="src-a", target="target-b", content="合并后内容",
        ))
        assert result["ok"] and result["archived_sources"] == ["src-a"]
        assert store.get("target-b").content == "合并后内容"
        assert store.get("src-a").state == SkillState.ARCHIVED

    async def test_library_health_tool(self, store: SkillStore, monkeypatch) -> None:
        await self._patch_tools(store, monkeypatch)
        from agent.skills import tools as skill_tools

        store.create("qq-media-fallback-a", "qq media download fallback", "c")
        store.create("qq-media-fallback-b", "qq media download fallback", "c")
        payload = json.loads(await skill_tools.skill_library_health())
        assert payload["counts"]["active"] == 2
        assert any({"qq-media-fallback-a", "qq-media-fallback-b"} ==
                   {m["name"] for m in cluster}
                   for cluster in payload.get("similar_clusters", []))


class TestSkillWebSerialization:
    """Web 服务层序列化（向量状态随 CRUD 同步可见）。"""

    def test_summary_embedded_field(self, tmp_path) -> None:
        """列表/详情带 embedded 字段；无索引时降级为 None。"""
        from services.skill import SkillService, _skill_summary

        svc = SkillService(str(tmp_path / "skills"))
        svc.create_skill("s1", "d", "c", ["t"])
        item = [s for s in svc.list_skills() if s["name"] == "s1"][0]
        assert item["embedded"] is None  # 无 runtime → 索引不可用 → 降级
        assert "match_count" in item and "rationale" in item

        # 有索引时按缓存命中返回布尔
        store = SkillStore(str(tmp_path / "skills"))
        skill = store.get("s1")
        index = SkillIndex(store, FakeEmbedder())
        assert _skill_summary(skill, index)["embedded"] is False
        # 嵌入后为真
        import asyncio
        asyncio.run(index.embed_now("s1"))
        assert _skill_summary(store.get("s1"), index)["embedded"] is True

    def test_health_embedding_stats(self, tmp_path) -> None:
        """健康报告带向量覆盖统计（embedded/total/cache_keys/model）。"""
        from services.skill import SkillService

        svc = SkillService(str(tmp_path / "skills"))
        svc.create_skill("s1", "d", "c")
        health = svc.library_health()
        assert "embedding" in health
        assert set(health["embedding"]) == {"embedded", "total", "cache_keys", "model", "rebuilding"}
        assert health["embedding"]["total"] == 1


class TestSkillReviewerContract:
    """SkillReviewer 与 finish_think / EVENT_AFTER_REPLY 的数据契约。"""

    async def test_reads_execution_summary_from_event(self, store: SkillStore, monkeypatch) -> None:
        from unittest.mock import AsyncMock

        from agent.skills.background_review import SkillReviewer
        from core.event_bus import EVENT_AFTER_REPLY, event_bus

        mind = SimpleNamespace(reflect=AsyncMock(return_value=""))
        reviewer = SkillReviewer(mind, store)
        monkeypatch.setattr(SkillReviewer, "_enabled", staticmethod(lambda: True))
        reviewer.start()
        try:
            await event_bus.emit(EVENT_AFTER_REPLY, {
                "error": False,
                "iterations": 2,
                "execution_summary": "[已执行操作摘要]\n  #1 recall(q=x) → ok",
            })
            assert reviewer._task is not None
            await reviewer._task
            mind.reflect.assert_awaited_once()
            prompt = mind.reflect.await_args.args[0][0]["content"]
            assert "recall(q=x)" in prompt
        finally:
            reviewer.stop()

    async def test_skips_when_summary_missing(self, store: SkillStore, monkeypatch) -> None:
        from unittest.mock import AsyncMock

        from agent.skills.background_review import SkillReviewer
        from core.event_bus import EVENT_AFTER_REPLY, event_bus

        mind = SimpleNamespace(reflect=AsyncMock(return_value=""), pfc=SimpleNamespace(temporary=[]))
        reviewer = SkillReviewer(mind, store)
        monkeypatch.setattr(SkillReviewer, "_enabled", staticmethod(lambda: True))
        reviewer.start()
        try:
            await event_bus.emit(EVENT_AFTER_REPLY, {
                "error": False,
                "iterations": 1,
                "execution_summary": "",
            })
            assert reviewer._task is None
            mind.reflect.assert_not_awaited()
        finally:
            reviewer.stop()
