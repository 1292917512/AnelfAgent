"""文件扫描原语（entities.filesystem.scan）与 read_file 二进制嗅探单元测试。"""

from __future__ import annotations

import json
import re
from typing import Iterator

import pytest

from entities.filesystem import scan
from entities.filesystem.scan import (
    DEFAULT_EXCLUDE_DIRS,
    content_search,
    iter_matches,
    looks_binary,
)


@pytest.fixture()
def tree(tmp_path) -> Iterator:
    """构造带噪声目录的搜索树。"""
    (tmp_path / "a.py").write_text("def root():\n    pass\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("def sub():\n    # TODO: x\n    pass\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.py").write_text("def dep():\n    pass\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config.py").write_text("git")
    return tmp_path


def _names(root, pattern: str, exclude=None) -> set:
    return {e.relpath for e in iter_matches(str(root), pattern, exclude)}


def test_noise_dirs_pruned(tree) -> None:
    """默认排除目录不进结果（node_modules/.git）。"""
    names = _names(tree, "*.py")
    assert names == {"a.py", "sub/b.py"}


def test_plain_pattern_matches_any_depth(tree) -> None:
    """裸模式任意深度命中（对齐 Claude Code Glob）。"""
    assert "sub/b.py" in _names(tree, "*.py")


def test_double_star_zero_dir_semantics(tree) -> None:
    """**/*.py 同时命中根级与子目录（零目录语义）。"""
    assert _names(tree, "**/*.py") == {"a.py", "sub/b.py"}


def test_scoped_pattern_limits_depth(tree) -> None:
    """sub/*.py 只命中一级子目录。"""
    assert _names(tree, "sub/*.py") == {"sub/b.py"}


def test_directories_participate(tree) -> None:
    """目录条目同样参与匹配。"""
    assert "sub" in _names(tree, "s*")


def test_custom_exclude_via_config(tree, monkeypatch: pytest.MonkeyPatch) -> None:
    """search_exclude_dirs 可配置覆盖默认表。"""
    monkeypatch.setattr(
        "core.config.get_config",
        lambda k, d=None: "node_modules" if k == "search_exclude_dirs" else d,
    )
    assert scan.resolve_exclude_dirs() == frozenset({"node_modules"})
    # 自定义后 .git 不再排除，node_modules 仍排除
    names = _names(tree, "*.py", scan.resolve_exclude_dirs())
    assert ".git/config.py" in names
    assert "node_modules/dep.py" not in names


def test_default_exclude_table_contents() -> None:
    """默认排除表覆盖 VCS/依赖/构建/缓存目录。"""
    for name in (".git", "node_modules", "__pycache__", ".venv", "dist", ".pytest_cache"):
        assert name in DEFAULT_EXCLUDE_DIRS


def test_content_search_skips_noise_and_finds_hits(tree) -> None:
    """内容检索：噪声目录不扫，命中带行号预览。"""
    hits = content_search(str(tree), "**/*.py", re.compile(r"TODO"))
    assert len(hits) == 1
    assert hits[0].relpath == "sub/b.py"
    assert hits[0].lines and hits[0].lines[0].startswith("2:")


def test_content_search_skips_large_files(tree) -> None:
    """超大文件跳过（>2MB 打包产物不逐行扫）。"""
    big = tree / "big.py"
    big.write_text("needle\n" + "x" * (3 * 1024 * 1024))
    hits = content_search(str(tree, ), "**/*.py", re.compile("needle"), max_file_bytes=2 * 1024 * 1024)
    assert all(h.relpath != "big.py" for h in hits)


def test_content_search_skips_binary_ext(tree) -> None:
    """二进制扩展名跳过（性能防线）。"""
    (tree / "blob.zip").write_text("needle")
    hits = content_search(str(tree), "*", re.compile("needle"))
    assert all(not h.relpath.endswith(".zip") for h in hits)


def test_looks_binary_by_nul_sampling(tmp_path) -> None:
    """NUL 采样判定：二进制命中、纯文本不命中、不存在文件按非二进制。"""
    binary = tmp_path / "noext"
    binary.write_bytes(b"PK\x03\x04\x00\x00rest")
    text = tmp_path / "noext2"
    text.write_text("普通文本")
    assert looks_binary(str(binary)) is True
    assert looks_binary(str(text)) is False
    assert looks_binary(str(tmp_path / "ghost")) is False


def test_read_file_binary_sniff(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """read_file：无扩展名二进制文件返回 binary JSON 而非乱码文本。"""
    from entities.filesystem import tools

    # _safe_path 每次调用都会 _load_config 重读全局，须一并打桩
    monkeypatch.setattr(tools, "_load_config", lambda: None)
    monkeypatch.setattr(tools, "_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(tools, "_SANDBOX", False)
    blob = tmp_path / "payload"
    blob.write_bytes(b"\x00\x01\x02\x00binary")

    result = json.loads(tools.read_file(str(blob)))
    assert result["type"] == "binary"
    assert "\x00" not in result.get("hint", "")

    # 对照：普通文本正常读出
    text_file = tmp_path / "note.txt"
    text_file.write_text("hello")
    assert "hello" in tools.read_file(str(text_file))
