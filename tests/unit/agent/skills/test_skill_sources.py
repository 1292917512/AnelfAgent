"""外部技能源（agent.skills.sources）与外部变更感知单元测试。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent.skills import sources as skill_sources
from agent.skills.skill_store import SkillStore
from agent.skills.sources.base import ExternalSkill, SkillSource


@pytest.fixture
def store(tmp_path) -> SkillStore:
    return SkillStore(str(tmp_path / "skills"))


@pytest.fixture(autouse=True)
def _reset_sources():
    skill_sources.reset_sources()
    yield
    skill_sources.reset_sources()


class TestExternalChangeAwareness:
    def test_external_add_bumps_version(self, store: SkillStore) -> None:
        store.create("s1", "d", "内容")
        baseline = store.version
        # 模拟外部途径（skillhub CLI / 手动拷贝）直接落盘
        external = store.skills_dir / "external-skill"
        external.mkdir()
        (external / "SKILL.md").write_text("---\nname: external-skill\n---\n内容", encoding="utf-8")
        assert store.version > baseline
        assert store.get("external-skill") is not None

    def test_external_delete_bumps_version(self, store: SkillStore) -> None:
        import shutil

        store.create("s1", "d", "内容")
        baseline = store.version
        shutil.rmtree(store.skills_dir / "s1")
        assert store.version > baseline

    def test_internal_save_no_double_bump(self, store: SkillStore) -> None:
        store.create("s1", "d", "内容")
        baseline = store.version
        store.create("s2", "d", "内容")
        assert store.version == baseline + 1
        # save 已同步目录签名，重复访问 version 不再递增
        assert store.version == baseline + 1

    def test_external_skill_minimal_frontmatter_loads(self, store: SkillStore) -> None:
        # 外部技能包仅有 name/description，缺省字段应全部回退默认值
        skill_dir = store.skills_dir / "hub-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: hub-skill\ndescription: 商店技能\nlicense: MIT\n---\n# 用法\n步骤",
            encoding="utf-8",
        )
        skill = store.get("hub-skill")
        assert skill is not None
        assert skill.description == "商店技能"
        assert skill.use_count == 0 and skill.pinned is False
        assert "用法" in skill.content


class TestSourceRegistry:
    def test_builtin_skillhub_loaded(self) -> None:
        keys = [s.key for s in skill_sources.list_sources()]
        assert "skillhub" in keys

    def test_missing_module_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import importlib

        def raising_import(name, package=None):
            raise ImportError(f"No module named {name}")

        monkeypatch.setattr(importlib, "import_module", raising_import)
        skill_sources.reset_sources()
        assert skill_sources.list_sources() == []
        assert skill_sources.get_source("skillhub") is None

    def test_invalid_source_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import importlib
        from types import SimpleNamespace

        monkeypatch.setattr(
            importlib, "import_module",
            lambda name, package=None: SimpleNamespace(get_source=lambda: object()),
        )
        skill_sources.reset_sources()
        assert skill_sources.list_sources() == []


class _FakeSource(SkillSource):
    key = "fake"
    display_name = "Fake"

    async def search(self, query: str, category: str = "", top_k: int = 5) -> list:
        return [ExternalSkill(name="x", slug="x", source=self.key)]

    def install(self, slug: str, namespace: str, skills_dir: Path):
        raise NotImplementedError


class TestExternalSkillModel:
    def test_to_dict(self) -> None:
        item = ExternalSkill(name="n", slug="s", source="fake")
        data = item.to_dict()
        assert data["slug"] == "s" and data["source"] == "fake"
        assert data["requires_api_key"] is False


class TestSkillToolsExternalEntry:
    """tools.py 统一入口（search_skills scope / install_external_skill）行为契约。"""

    @pytest.fixture
    def skill_tools(self, store: SkillStore, monkeypatch: pytest.MonkeyPatch):
        from agent.skills import tools as skill_tools_mod
        from agent.skills.skill_matcher import SkillMatcher

        monkeypatch.setattr(skill_tools_mod, "_store", store)
        monkeypatch.setattr(skill_tools_mod, "_matcher", SkillMatcher(store))
        return skill_tools_mod

    async def test_search_invalid_scope(self, skill_tools) -> None:
        import json

        result = json.loads(await skill_tools.search_skills("q", scope="bogus"))
        assert result.get("error")

    async def test_search_external_no_sources(
            self, skill_tools, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import json

        monkeypatch.setattr(skill_sources, "list_sources", lambda: [])
        result = json.loads(await skill_tools.search_skills("q", scope="external"))
        assert result["external"] == []
        assert "外部技能源" in result["external_hint"]

    async def test_search_external_aggregates(
            self, skill_tools, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import json

        monkeypatch.setattr(skill_sources, "list_sources", lambda: [_FakeSource()])
        result = json.loads(await skill_tools.search_skills("q", scope="external"))
        assert result["external"][0]["slug"] == "x"
        assert result["external"][0]["source"] == "fake"
        assert "install_external_skill" in result["external_hint"]

    async def test_search_local_miss_hint(self, skill_tools) -> None:
        import json

        result = json.loads(await skill_tools.search_skills("不存在的技能"))
        assert result["local"] == []
        assert "scope='external'" in result["local_hint"]

    def test_install_no_sources(self, skill_tools, monkeypatch: pytest.MonkeyPatch) -> None:
        import json

        monkeypatch.setattr(skill_sources, "list_sources", lambda: [])
        result = json.loads(skill_tools.install_external_skill("demo"))
        assert result.get("error")

    def test_install_unknown_source(self, skill_tools, monkeypatch: pytest.MonkeyPatch) -> None:
        import json

        monkeypatch.setattr(skill_sources, "list_sources", lambda: [_FakeSource()])
        result = json.loads(skill_tools.install_external_skill("demo", source="nope"))
        assert result.get("error")
        assert "fake" in result.get("hint", "")

    def test_install_auto_select_single_source(
            self, skill_tools, store: SkillStore, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import json

        from agent.skills.sources.base import InstallResult

        def fake_install(self, slug, namespace, skills_dir):
            skill_dir = skills_dir / slug
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("---\nname: demo\n---\n内容", encoding="utf-8")
            return InstallResult(ok=True, path=str(skill_dir))

        monkeypatch.setattr(skill_sources, "list_sources", lambda: [_FakeSource()])
        monkeypatch.setattr(_FakeSource, "install", fake_install)
        result = json.loads(skill_tools.install_external_skill("demo"))
        assert result["ok"] is True
        assert result["loaded"] is True
        assert store.get("demo") is not None

    def test_install_existing_rejected(
            self, skill_tools, store: SkillStore, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import json

        store.create("demo", "d", "c")
        monkeypatch.setattr(skill_sources, "list_sources", lambda: [_FakeSource()])
        result = json.loads(skill_tools.install_external_skill("demo"))
        assert result.get("error")
        assert "已存在" in result.get("error", "")

    def test_list_sources_empty(self, skill_tools, monkeypatch: pytest.MonkeyPatch) -> None:
        import json

        monkeypatch.setattr(skill_sources, "list_sources", lambda: [])
        result = json.loads(skill_tools.list_skill_sources())
        assert result["count"] == 0
        assert "hint" in result


class TestSkillHubSource:
    @pytest.fixture
    def source(self) -> SkillSource:
        from agent.skills.sources.skillhub import SkillHubSource

        return SkillHubSource()

    async def test_search_parse(self, source: SkillSource, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = {
            "code": 0,
            "data": {"skills": [{
                "name": "find skill",
                "slug": "find-skill-skillhub",
                "description": "desc",
                "description_zh": "中文描述",
                "category": "ai-agent",
                "downloads": 100,
                "installs": 5,
                "labels": {"requires_api_key": "false"},
                "namespace": {"handle": "user_1"},
            }]},
        }

        class _Resp:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return payload

        class _Client:
            def __init__(self, **kwargs) -> None:
                pass

            async def __aenter__(self) -> "_Client":
                return self

            async def __aexit__(self, *args) -> None:
                return None

            async def get(self, url: str, params: dict | None = None) -> _Resp:
                return _Resp()

        import httpx

        monkeypatch.setattr(httpx, "AsyncClient", _Client)
        results = await source.search("find", top_k=3)
        assert len(results) == 1
        item = results[0]
        assert item.slug == "find-skill-skillhub"
        assert item.namespace == "user_1"
        assert item.description == "中文描述"
        assert item.requires_api_key is False
        assert item.source == "skillhub"
        assert item.homepage == "https://skillhub.cn/skills/user_1/find-skill-skillhub"

    async def test_search_invalid_category(self, source: SkillSource) -> None:
        with pytest.raises(ValueError, match="不支持分类"):
            await source.search("find", category="no-such-category")

    def test_install_cli_missing(self, source: SkillSource, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(type(source), "_find_cli", staticmethod(lambda: None))
        result = source.install("demo", "alice", tmp_path)
        assert result.ok is False
        assert "install.sh" in result.hint

    def test_install_flattens_namespaced_layout(
            self, source: SkillSource, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        def fake_run(cmd, **kwargs):
            # 模拟 CLI：在 --dir 目录下生成 @ns/slug 布局
            target = Path(cmd[cmd.index("--dir") + 1])
            skill_dir = target / "@alice" / "demo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("---\nname: demo\n---\n内容", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

        monkeypatch.setattr(type(source), "_find_cli", staticmethod(lambda: "/usr/bin/skillhub"))
        monkeypatch.setattr(subprocess, "run", fake_run)
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        result = source.install("demo", "alice", skills_dir)
        assert result.ok is True
        assert (skills_dir / "demo" / "SKILL.md").is_file()
        assert not (skills_dir / "@alice").exists()

    def test_install_cli_failure(
            self, source: SkillSource, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

        monkeypatch.setattr(type(source), "_find_cli", staticmethod(lambda: "/usr/bin/skillhub"))
        monkeypatch.setattr(subprocess, "run", fake_run)
        result = source.install("demo", "alice", tmp_path)
        assert result.ok is False
        assert "boom" in result.error
