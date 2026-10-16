"""上传媒体 URL 的文件名往返与路径边界。"""

from pathlib import Path

import pytest

from core.path import ConfigPaths, parse_upload_url, upload_path_to_url, upload_url_for


@pytest.mark.parametrize("name", ["image.png", "截图 (1) #100%?.png", "literal%2Fname.png"])
def test_upload_url_roundtrip(name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ConfigPaths, "UPLOAD_DIR", str(tmp_path))
    url = upload_url_for("image", name)
    assert not any(char in url for char in " #?()")
    assert parse_upload_url(url) == ("image", name)
    assert upload_path_to_url(str(tmp_path / "image" / name)) == url


@pytest.mark.parametrize("suffix", [
    "image/%2E%2E", "image/a%2Fb.png", "image/a%5Cb.png", "image/a%00.png",
    "image/a/b.png", "image/", "image/%FF", "%2E%2E/a.png",
])
def test_upload_url_rejects_decoded_path_boundaries(suffix: str) -> None:
    assert parse_upload_url(f"/api/chat/files/{suffix}") is None
