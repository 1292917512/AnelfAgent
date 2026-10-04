"""实时轮次录音留存测试：落盘开关、WAV 可回读、到期日目录滚动清理。"""

from __future__ import annotations

import os
import time

import pytest

from agent.realtime import recordings

RATE = 16000


@pytest.fixture
def ws_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """workspace 根重定向到临时目录（留存文件不进真实 workspace）。"""
    monkeypatch.setattr("core.path.workspace_root", lambda: str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _retention_on():
    """本文件测留存本体：显式开（目录级 conftest 默认关以防引擎测试落盘）。"""
    from core.config import ConfigManager
    ConfigManager.set("realtime_keep_recordings", True)
    yield
    ConfigManager.set("realtime_keep_recordings", True)


class TestSaveTurnRecording:
    async def test_disabled_returns_empty(self, ws_tmp) -> None:
        from core.config import ConfigManager
        ConfigManager.set("realtime_keep_recordings", False)
        try:
            path = await recordings.save_turn_recording("u1", 1, b"\x00" * 640, RATE)
            assert path == ""
            assert not os.path.exists(os.path.join(str(ws_tmp), "uploads", "realtime"))
        finally:
            ConfigManager.set("realtime_keep_recordings", True)

    async def test_saved_wav_readable(self, ws_tmp) -> None:
        import wave

        pcm = b"\x01\x02" * RATE  # 1s
        path = await recordings.save_turn_recording("u/1:x", 3, pcm, RATE)
        assert path.endswith(".wav")
        assert os.path.exists(path)
        # owner 中的非法文件名字符已清洗
        assert "u_1_x" in os.path.basename(path)
        with wave.open(path, "rb") as wf:
            assert wf.getframerate() == RATE
            assert wf.getnframes() == RATE

    async def test_empty_pcm_skipped(self, ws_tmp) -> None:
        assert await recordings.save_turn_recording("u1", 1, b"", RATE) == ""


class TestSweepExpired:
    async def test_expired_day_dirs_removed(self, ws_tmp, monkeypatch: pytest.MonkeyPatch) -> None:
        from core.config import ConfigManager
        ConfigManager.set("realtime_recording_retention_days", 7)
        try:
            base = os.path.join(str(ws_tmp), "uploads", "realtime")
            old_day = time.strftime("%Y%m%d", time.localtime(time.time() - 9 * 86400))
            new_day = time.strftime("%Y%m%d")
            for day in (old_day, new_day):
                os.makedirs(os.path.join(base, day), exist_ok=True)
                with open(os.path.join(base, day, "t.wav"), "wb") as f:
                    f.write(b"\x00")
            os.makedirs(os.path.join(base, "not-a-day"), exist_ok=True)

            await recordings.sweep_expired()

            assert not os.path.exists(os.path.join(base, old_day))
            assert os.path.exists(os.path.join(base, new_day))
            assert os.path.exists(os.path.join(base, "not-a-day"))
        finally:
            ConfigManager.set("realtime_recording_retention_days", 7)

    async def test_zero_days_disables_sweep(self, ws_tmp) -> None:
        from core.config import ConfigManager
        ConfigManager.set("realtime_recording_retention_days", 0)
        try:
            base = os.path.join(str(ws_tmp), "uploads", "realtime")
            old_day = time.strftime("%Y%m%d", time.localtime(time.time() - 30 * 86400))
            os.makedirs(os.path.join(base, old_day), exist_ok=True)
            await recordings.sweep_expired()
            assert os.path.exists(os.path.join(base, old_day))
        finally:
            ConfigManager.set("realtime_recording_retention_days", 7)
