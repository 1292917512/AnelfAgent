"""目录镜像同步 watcher 测试：文件夹单元合并 / 增量 / 镜像删除 / 时间解析。"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

import entities.audiosync.watcher as watcher_mod
from agent.audio.store import AudioStore
from core.config import ConfigManager
from entities.audiosync.watcher import AudioSyncWatcher, parse_recording_time_ns


@pytest.fixture
def store(_isolate_audio_library):
    """核心音频库（conftest 隔离的临时单例；watcher 经 SDK 桥读写同库）。"""
    return _isolate_audio_library


@pytest.fixture
def watch_dir(tmp_path):
    d = tmp_path / "nas_audio"
    d.mkdir()
    ConfigManager.set("audiosync_watch_enabled", True)
    ConfigManager.set("audiosync_watch_dir", str(d))
    ConfigManager.set("funasr_endpoint", "http://funasr.local")
    return d


def _write(path: str, content: bytes = b"audio") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)


@pytest.fixture
def mock_pipeline(monkeypatch: pytest.MonkeyPatch):
    """打桩 ffmpeg 合并与 FunASR 转写（含可用性探测），记录调用。"""
    calls = {"merge": 0, "transcribe": 0, "merged_inputs": []}

    async def fake_probe() -> bool:
        return True

    monkeypatch.setattr(
        "entities.audiosync.client.probe_available", fake_probe)

    async def fake_merge(paths):
        calls["merge"] += 1
        calls["merged_inputs"].append(list(paths))
        # 与真实 merge_to_wav 语义一致：产出独立临时文件（调用方负责清理），
        # 不得返回输入路径——否则 _process_unit 的 finally 会误删源文件
        fd, merged = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        return merged

    async def fake_transcribe(path: str, source_time: str = ""):
        calls["transcribe"] += 1
        calls["source_time"] = source_time
        joined = " ".join(" ".join(ps) for ps in calls["merged_inputs"])
        if "silent" in joined:
            return []
        if "bad" in joined:
            raise RuntimeError("转写失败")
        return [{
            "start_ms": 0, "end_ms": 3000, "text": "你好",
            "vector": [1.0] + [0.0] * 191,
            "abs_start_ms": None, "abs_end_ms": None,
        }]

    async def fake_probe(path: str):
        return {"duration_s": 5.0, "sample_rate": 16000, "channels": 1,
                "codec_name": "pcm_s16le", "format_name": "wav"}

    async def fake_silences(path: str, **kwargs):
        return []

    async def fake_split(src: str, a: float, b: float, out: str):
        return None

    monkeypatch.setattr(watcher_mod._sdk, "merge_to_wav", fake_merge)
    monkeypatch.setattr(watcher_mod._sdk, "probe", fake_probe)
    monkeypatch.setattr(watcher_mod._sdk, "detect_silences", fake_silences)
    monkeypatch.setattr(watcher_mod._sdk, "split_wav", fake_split)
    monkeypatch.setattr(watcher_mod._sdk, "audio_transcribe", fake_transcribe)
    return calls


class TestFolderUnitMerge:
    async def test_saferec_folder_merged_as_one_unit(
        self, store: AudioStore, watch_dir, mock_pipeline,
    ) -> None:
        folder = os.path.join(str(watch_dir), "audio_20260806143300")
        _write(os.path.join(folder, "audio_20260806143300_001.m4a"))
        _write(os.path.join(folder, "audio_20260806143300_002.m4a"))
        _write(os.path.join(folder, "notes.txt"))  # 非音频被忽略

        watcher = AudioSyncWatcher()
        result = await watcher.sync_now()
        assert result["ingested"] == 1
        # 两个子文件按名称排序合并为一次 FunASR 调用
        assert mock_pipeline["merge"] == 1
        merged = mock_pipeline["merged_inputs"][0]
        assert len(merged) == 2
        assert merged[0].endswith("_001.m4a") and merged[1].endswith("_002.m4a")

        # 登记：文件夹单元 + 时间从文件夹名解析
        recording = await store.get_recording(folder)
        assert recording is not None
        assert recording["kind"] == "folder"
        assert recording["file_count"] == 2
        expected_ns = parse_recording_time_ns("audio_20260806143300")
        assert recording["started_ns"] == expected_ns
        # 片段归属文件夹且时间对齐（基准 + 段内偏移）
        segments = await store.list_segments(recording_path=folder)
        assert segments["total"] == 1
        seg = segments["items"][0]
        assert seg["recording_path"] == folder
        assert seg["ts_ns"] == expected_ns  # start_ms=0 → 基准时间
        # 录制时刻已作为 source_time 透传给 FunASR（epoch 毫秒）
        assert mock_pipeline["source_time"] == str(expected_ns // 1_000_000)

    async def test_loose_file_as_single_unit(
        self, store: AudioStore, watch_dir, mock_pipeline,
    ) -> None:
        _write(os.path.join(str(watch_dir), "loose.wav"))
        watcher = AudioSyncWatcher()
        result = await watcher.sync_now()
        assert result["ingested"] == 1
        recording = await store.get_recording(os.path.join(str(watch_dir), "loose.wav"))
        assert recording is not None and recording["kind"] == "file"


class TestIncrementalAndMirror:
    async def test_unchanged_skipped_then_change_reprocessed(
        self, store: AudioStore, watch_dir, mock_pipeline,
    ) -> None:
        folder = os.path.join(str(watch_dir), "audio_20260806143300")
        _write(os.path.join(folder, "audio_20260806143300_001.m4a"))
        watcher = AudioSyncWatcher()
        await watcher.sync_now()
        # 未变化 → 跳过
        second = await watcher.sync_now()
        assert second["new"] == 0
        assert mock_pipeline["merge"] == 1
        # 新增子文件（指纹变化）→ 重处理
        _write(os.path.join(folder, "audio_20260806143300_002.m4a"))
        third = await watcher.sync_now()
        assert third["new"] == 1
        assert mock_pipeline["merge"] == 2

    async def test_nas_delete_mirrors_locally(
        self, store: AudioStore, watch_dir, mock_pipeline,
    ) -> None:
        folder = os.path.join(str(watch_dir), "audio_20260806143300")
        _write(os.path.join(folder, "audio_20260806143300_001.m4a"))
        watcher = AudioSyncWatcher()
        await watcher.sync_now()
        assert (await store.list_segments(recording_path=folder))["total"] == 1
        # 样本已挂接片段
        speakers = await store.list_speakers()
        assert speakers["total"] == 1

        # NAS 删除文件夹 → 本地级联删除
        import shutil
        shutil.rmtree(folder)
        result = await watcher.sync_now()
        assert result["deleted"] == 1
        assert (await store.list_segments(recording_path=folder))["total"] == 0
        assert await store.get_recording(folder) is None
        # 声纹样本被级联清理，说话人档案保留
        speaker_id = speakers["items"][0]["id"]
        assert await store.list_samples(speaker_id) == []
        assert await store.get_speaker(speaker_id) is not None

    async def test_no_speech_and_error_semantics(
        self, store: AudioStore, watch_dir, mock_pipeline,
    ) -> None:
        _write(os.path.join(str(watch_dir), "silent.wav"))
        _write(os.path.join(str(watch_dir), "bad.wav"))
        watcher = AudioSyncWatcher()
        first = await watcher.sync_now()
        assert first["no_speech"] == 1
        assert first["failed"] == 1
        # 两者后续扫描都不再重试（no_speech 跳过 / error 内容未变不重试）
        second = await watcher.sync_now()
        assert second["new"] == 0
        # bad.wav 内容变化后重试
        _write(os.path.join(str(watch_dir), "bad.wav"), b"bad-changed")
        third = await watcher.sync_now()
        assert third["new"] == 1


class TestSyncResultSerialization:
    async def test_result_with_status_is_json_serializable(
        self, store: AudioStore, watch_dir, mock_pipeline,
    ) -> None:
        """sync_now 返回值追加 status 后仍可 JSON 序列化（防自引用循环）。"""
        import json
        _write(os.path.join(str(watch_dir), "a.wav"))
        watcher = AudioSyncWatcher()
        await watcher.sync_now()
        # 模拟工具/路由用法：返回值上追加 status（其 last_result 不得指回自身）
        result = await watcher.sync_now()
        result["status"] = watcher.status()
        dumped = json.dumps(result, ensure_ascii=False)
        assert "scanned" in dumped and "last_result" in dumped


class TestTrigger:
    """后台触发语义：立即返回、执行不绑定调用方生命周期、在跑不重发。"""

    async def test_small_sync_completes_within_grace(
        self, store: AudioStore, watch_dir, mock_pipeline,
    ) -> None:
        _write(os.path.join(str(watch_dir), "a.wav"))
        watcher = AudioSyncWatcher()
        out = await watcher.trigger(wait_seconds=5.0)
        assert out["started"] is True and out["completed"] is True
        assert out["result"]["ingested"] == 1
        status = watcher.status()
        assert status["syncing"] is False
        assert status["last_result"]["ingested"] == 1

    async def test_long_sync_returns_early_and_finishes_in_background(
        self, store: AudioStore, watch_dir, mock_pipeline,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        inner = watcher_mod._sdk.audio_transcribe

        async def slow_transcribe(path: str, source_time: str = ""):
            await asyncio.sleep(0.3)
            return await inner(path, source_time=source_time)

        monkeypatch.setattr(watcher_mod._sdk, "audio_transcribe", slow_transcribe)
        _write(os.path.join(str(watch_dir), "a.wav"))
        watcher = AudioSyncWatcher()
        out = await watcher.trigger(wait_seconds=0.05)
        # 宽限窗内未完成 → 立即返回 started，同步在后台继续（调用方超时不波及）
        assert out["started"] is True and out["completed"] is False
        assert watcher.status()["syncing"] is True
        manual = watcher._manual_task
        assert manual is not None
        summary = await asyncio.wait_for(manual, timeout=5)
        assert summary["ingested"] == 1
        assert watcher.status()["last_result"]["ingested"] == 1

    async def test_trigger_while_busy_does_not_double_start(
        self, store: AudioStore, watch_dir, mock_pipeline,
    ) -> None:
        watcher = AudioSyncWatcher()
        await watcher._sync_lock.acquire()
        try:
            out = await watcher.trigger(wait_seconds=0)
        finally:
            watcher._sync_lock.release()
        assert out["started"] is False and out["completed"] is False
        assert out["reason"]
        assert watcher._manual_task is None

    async def test_close_cancels_manual_task(
        self, store: AudioStore, watch_dir, mock_pipeline,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        inner = watcher_mod._sdk.audio_transcribe

        async def slow_transcribe(path: str, source_time: str = ""):
            await asyncio.sleep(5)
            return await inner(path, source_time=source_time)

        monkeypatch.setattr(watcher_mod._sdk, "audio_transcribe", slow_transcribe)
        _write(os.path.join(str(watch_dir), "a.wav"))
        watcher = AudioSyncWatcher()
        out = await watcher.trigger(wait_seconds=0.05)
        assert out["started"] is True and out["completed"] is False
        task = watcher._manual_task
        await watcher.close()
        assert watcher._manual_task is None
        assert task is not None and task.cancelled()


class TestExcludeRules:
    async def test_excluded_units_not_synced(
        self, store: AudioStore, watch_dir, mock_pipeline,
    ) -> None:
        ConfigManager.set("audiosync_watch_exclude", "tmp_*,*备份*")
        _write(os.path.join(str(watch_dir), "normal.wav"))
        _write(os.path.join(str(watch_dir), "tmp_cache.wav"))
        folder = os.path.join(str(watch_dir), "audio_备份 2026")
        _write(os.path.join(folder, "b.m4a"))
        watcher = AudioSyncWatcher()
        result = await watcher.sync_now()
        assert result["ingested"] == 1  # 只有 normal.wav
        assert mock_pipeline["merge"] == 1
        preview = await watcher.preview()
        assert preview["excluded"] == 2

    async def test_excluded_not_mirror_deleted(
        self, store: AudioStore, watch_dir, mock_pipeline,
    ) -> None:
        """已同步的目录加入排除规则后：不再同步但本地资源保留（不误删）。"""
        folder = os.path.join(str(watch_dir), "keep_me")
        _write(os.path.join(folder, "a.wav"))
        watcher = AudioSyncWatcher()
        await watcher.sync_now()
        assert (await store.list_segments(recording_path=folder))["total"] == 1
        # 加入排除 + NAS 上仍有该目录 → 不参与任何处理
        ConfigManager.set("audiosync_watch_exclude", "keep_me")
        result = await watcher.sync_now()
        assert result["deleted"] == 0
        assert (await store.list_segments(recording_path=folder))["total"] == 1
        assert await store.get_recording(folder) is not None


class TestRebuild:
    async def test_rebuild_reingests(
        self, store: AudioStore, watch_dir, mock_pipeline,
    ) -> None:
        folder = os.path.join(str(watch_dir), "audio_20260806143300")
        _write(os.path.join(folder, "audio_20260806143300_001.m4a"))
        watcher = AudioSyncWatcher()
        await watcher.sync_now()
        assert (await store.list_segments(recording_path=folder))["total"] == 1
        assert mock_pipeline["merge"] == 1

        result = await watcher.rebuild([folder])
        assert result["error"] == ""
        assert result["results"][0]["outcome"] == "done"
        # 清理后重新入库（merge 被再次调用，片段仍是 1 条不重复）
        assert mock_pipeline["merge"] == 2
        assert (await store.list_segments(recording_path=folder))["total"] == 1

    async def test_rebuild_missing_on_nas_only_cleans(
        self, store: AudioStore, watch_dir, mock_pipeline,
    ) -> None:
        folder = os.path.join(str(watch_dir), "audio_20260806143300")
        _write(os.path.join(folder, "a.m4a"))
        watcher = AudioSyncWatcher()
        await watcher.sync_now()
        import shutil
        shutil.rmtree(folder)
        result = await watcher.rebuild([folder])
        assert result["results"][0]["outcome"] == "deleted"
        assert await store.get_recording(folder) is None
        assert (await store.list_segments(recording_path=folder))["total"] == 0

    async def test_rebuild_excluded_rejected(
        self, store: AudioStore, watch_dir, mock_pipeline,
    ) -> None:
        ConfigManager.set("audiosync_watch_exclude", "audio_*")
        folder = os.path.join(str(watch_dir), "audio_20260806143300")
        watcher = AudioSyncWatcher()
        result = await watcher.rebuild([folder])
        assert result["results"][0]["outcome"] == "excluded"


class TestPreview:
    async def test_pending_diff(
        self, store: AudioStore, watch_dir, mock_pipeline,
    ) -> None:
        folder = os.path.join(str(watch_dir), "audio_20260806143300")
        _write(os.path.join(folder, "audio_20260806143300_001.m4a"))
        _write(os.path.join(str(watch_dir), "new_loose.wav"))
        watcher = AudioSyncWatcher()

        # 未同步时：两个单元都是 new
        first = await watcher.preview()
        assert first["nas_total"] == 2
        assert len(first["pending"]) == 2
        assert all(p["reason"] == "new" for p in first["pending"])
        folder_pending = next(p for p in first["pending"] if p["kind"] == "folder")
        assert folder_pending["started_ns"] == parse_recording_time_ns("audio_20260806143300")

        # 同步后：散装文件已同步，文件夹新增子文件 → changed
        await watcher.sync_now()
        _write(os.path.join(folder, "audio_20260806143300_002.m4a"))
        second = await watcher.preview()
        assert len(second["pending"]) == 1
        assert second["pending"][0]["reason"] == "changed"
        assert second["synced"].get("done") == 2

    async def test_preview_no_source(self, store: AudioStore) -> None:
        ConfigManager.set("audiosync_watch_dir", "")
        watcher = AudioSyncWatcher()
        result = await watcher.preview()
        assert result["error"]
        assert result["pending"] == []


class TestTimeParsing:
    def test_14_digit_timestamp(self) -> None:
        ns = parse_recording_time_ns("audio_20260806143300")
        from datetime import datetime
        expected = int(datetime(2026, 8, 6, 14, 33, 0).timestamp() * 1e9)
        assert ns == expected

    def test_8_digit_date(self) -> None:
        ns = parse_recording_time_ns("meeting_20260806")
        from datetime import datetime
        assert ns == int(datetime(2026, 8, 6).timestamp() * 1e9)

    def test_fallback(self) -> None:
        assert parse_recording_time_ns("无时间戳", 12345) == 12345


class TestExtensionFilter:
    def test_custom_extensions(self) -> None:
        ConfigManager.set("audiosync_audio_extensions", "wav, .flac")
        assert watcher_mod._extensions() == (".wav", ".flac")


class TestOpenListSourceScan:
    async def test_nested_dirs_with_audio_all_discovered(self, monkeypatch) -> None:
        """含音频的父目录成单元，其子目录仍继续下钻（与本地扫描语义一致）。"""
        from entities.audiosync.sources.openlist import OpenListSource

        listing = {
            "/root": {"dirs": [{"path": "/root/a", "mtime_ns": 0}], "files": []},
            "/root/a": {
                "dirs": [{"path": "/root/a/b", "mtime_ns": 0}],
                "files": [{"path": "/root/a/x.wav", "name": "x.wav",
                           "size": 10, "mtime_ns": 1}],
            },
            "/root/a/b": {
                "dirs": [],
                "files": [{"path": "/root/a/b/y.wav", "name": "y.wav",
                           "size": 10, "mtime_ns": 2}],
            },
        }

        async def fake_list_dir(path: str):
            return listing[path]

        monkeypatch.setattr(
            "entities.audiosync.sources.openlist.openlist.list_dir", fake_list_dir)
        monkeypatch.setattr(
            "entities.audiosync.sources.openlist.openlist.is_configured", lambda: True)
        monkeypatch.setattr(
            "entities.audiosync.sources.openlist.openlist.configured_root", lambda: "/root")

        source = OpenListSource()
        candidates = await source.scan((".wav",), True)
        paths = {c["path"] for c in candidates}
        assert paths == {"/root/a", "/root/a/b"}
