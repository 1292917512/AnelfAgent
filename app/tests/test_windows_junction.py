"""Native junction creation preserves dangling targets and cleans up failed writes."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path

import pytest

from app.migration_links import plain_path
from app.windows_junction import create_junction

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows reparse points")


def test_missing_target_and_long_unicode_paths_are_preserved(tmp_path: Path) -> None:
    missing = tmp_path / ("中文🧪" * 12) / ("nested" * 20) / "missing package"
    link = tmp_path / "directory junction"
    create_junction(link, str(missing))
    assert link.is_junction() and not link.exists()
    assert Path(plain_path(Path(os.readlink(link)))) == missing
    assert not missing.parent.exists()
    missing.mkdir(parents=True)
    (missing / "keep.txt").write_text("target data")
    assert (link / "keep.txt").read_text() == "target data"
    link.rmdir()
    assert (missing / "keep.txt").read_text() == "target data"
    link.mkdir()
    (link / "original.txt").write_text("do not replace")
    with pytest.raises(FileExistsError):
        create_junction(link, str(missing))
    assert (link / "original.txt").read_text() == "do not replace"


def test_failed_reparse_write_closes_handle_and_removes_only_new_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)

    def denied(*_args: object) -> int:
        ctypes.set_last_error(5)
        return 0

    monkeypatch.setattr(kernel, "DeviceIoControl", denied)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: kernel)
    link = tmp_path / "failed link"
    target = tmp_path / "target"
    target.mkdir()
    (target / "keep.txt").write_text("target data")
    with pytest.raises(OSError) as error:
        create_junction(link, str(target))
    assert error.value.winerror == 5
    assert not os.path.lexists(link)
    assert (target / "keep.txt").read_text() == "target data"
