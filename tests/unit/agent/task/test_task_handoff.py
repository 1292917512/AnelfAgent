"""长任务结构化交接（agent/task/handoff + TaskDefinition.handoff）单元测试。"""

from __future__ import annotations

import os

from agent.task.handoff import extract_handoff, load_handoff, save_handoff
from agent.task.model import TaskDefinition


class TestExtractHandoff:
    def test_json_block_parsed(self) -> None:
        output = (
            "本轮整理了 3 个技能。\n"
            "# HANDOFF\n"
            '{"summary": "完成技能库 A 部分", "next_steps": ["继续 B 部分", "清理重复项"], "blocker": null}'
        )
        clean, handoff = extract_handoff(output)
        assert clean == "本轮整理了 3 个技能。"
        assert "完成技能库 A 部分" in handoff
        assert "- 继续 B 部分" in handoff
        assert "阻塞" not in handoff  # blocker 为 null 不出现

    def test_plain_text_fallback(self) -> None:
        output = "正文内容\n# HANDOFF\n下次从第 5 章开始读"
        clean, handoff = extract_handoff(output)
        assert clean == "正文内容"
        assert handoff == "下次从第 5 章开始读"

    def test_no_marker_untouched(self) -> None:
        output = "普通任务输出，无交接"
        clean, handoff = extract_handoff(output)
        assert clean == output
        assert handoff is None

    def test_marker_at_start(self) -> None:
        clean, handoff = extract_handoff("# HANDOFF\n只有交接没有正文")
        assert clean == ""
        assert "只有交接" in handoff

    def test_empty_block_after_marker(self) -> None:
        clean, handoff = extract_handoff("正文\n# HANDOFF\n")
        assert clean == "正文"
        assert handoff is None

    def test_last_marker_wins(self) -> None:
        output = "正文里提到 # HANDOFF 不是块\n# HANDOFF\n真正的交接"
        clean, handoff = extract_handoff(output)
        # 行中（非行首）的标记不命中；行首的最后一个命中
        assert handoff == "真正的交接"


class TestHandoffPersistence:
    def test_save_load_roundtrip(self, tmp_path, monkeypatch) -> None:
        from core import path as path_mod
        monkeypatch.setattr(path_mod.ConfigPaths, "TASKS_DIR", str(tmp_path), raising=False)
        assert save_handoff("daily-cleanup", "上次整理到第 3 章")
        assert load_handoff("daily-cleanup") == "上次整理到第 3 章"
        # 覆盖写
        save_handoff("daily-cleanup", "第 5 章")
        assert load_handoff("daily-cleanup") == "第 5 章"

    def test_load_missing_returns_empty(self, tmp_path, monkeypatch) -> None:
        from core import path as path_mod
        monkeypatch.setattr(path_mod.ConfigPaths, "TASKS_DIR", str(tmp_path / "none"), raising=False)
        assert load_handoff("ghost") == ""

    def test_save_empty_rejected(self, tmp_path, monkeypatch) -> None:
        from core import path as path_mod
        monkeypatch.setattr(path_mod.ConfigPaths, "TASKS_DIR", str(tmp_path), raising=False)
        assert save_handoff("x", "   ") is False
        assert not os.listdir(tmp_path)

    def test_save_truncates_to_limit(self, tmp_path, monkeypatch) -> None:
        from core import path as path_mod
        from core.config import ConfigManager
        ConfigManager.set("task_handoff_max_chars", 300)
        monkeypatch.setattr(path_mod.ConfigPaths, "TASKS_DIR", str(tmp_path), raising=False)
        save_handoff("big", "x" * 5000)
        assert len(load_handoff("big")) == 300


class TestTaskDefinitionHandoff:
    def test_from_dict_default_false(self) -> None:
        t = TaskDefinition.from_dict({"name": "t1", "prompt": "p"})
        assert t.handoff is False

    def test_from_to_dict_roundtrip(self) -> None:
        t = TaskDefinition.from_dict({"name": "t1", "prompt": "p", "handoff": True})
        assert t.handoff is True
        d = t.to_dict()
        assert d["handoff"] is True
        assert TaskDefinition.from_dict(d).handoff is True

    def test_non_handoff_omitted_in_dict(self) -> None:
        t = TaskDefinition.from_dict({"name": "t1", "prompt": "p"})
        assert "handoff" not in t.to_dict()
