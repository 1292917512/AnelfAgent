"""崩溃状态设施（core.crash_report）单元测试。

覆盖：崩溃状态读取/标记/消费、macOS .ips 崩溃报告解析与时间关联、
崩溃摘要渲染、崩溃退出码判定、多格式时间解析。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import crash_report


@pytest.fixture()
def state_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "crash_state.json"
    monkeypatch.setattr(crash_report, "CRASH_STATE_PATH", path)
    return path


@pytest.fixture()
def ips_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "DiagnosticReports"
    directory.mkdir()
    monkeypatch.setattr(crash_report, "_diagnostic_report_dirs", lambda: [directory])
    return directory


def _write_state(path: Path, **overrides: object) -> dict:
    state: dict = {
        "exit_code": 139,
        "signal": "SIGSEGV",
        "crashed_at": "2026-08-16T08:08:00+0800",
        "crash_count": 2,
        **overrides,
    }
    path.write_text(json.dumps(state), encoding="utf-8")
    return state


def _write_ips(directory: Path, name: str, capture_time: str) -> Path:
    """构造最小可解析的 .ips 报告（首行 JSON 头 + JSON 体）。"""
    images = [
        {"name": "libsystem_kernel.dylib"},
        {"name": "libpython3.10.dylib"},
        {"name": "_lbug.cpython-310-darwin.so"},
    ]
    body = {
        "procName": "python3.10",
        "captureTime": capture_time,
        "exception": {"type": "EXC_BAD_ACCESS", "signal": "SIGSEGV", "codes": "0x1, 0x0"},
        "faultingThread": 1,
        "usedImages": images,
        "threads": [
            {"frames": [{"imageIndex": 0, "symbol": "kevent", "imageOffset": 0}]},
            {
                "triggered": True,
                "frames": [
                    {"imageIndex": 0, "symbol": "__pthread_kill", "imageOffset": 0},
                    {"imageIndex": 1, "symbol": "faulthandler_fatal_error", "imageOffset": 0},
                    {
                        "imageIndex": 2,
                        "symbol": "lbug::storage::NodeTableScanState::scanNext(lbug::transaction::Transaction*)",
                        "imageOffset": 100,
                        "symbolLocation": 60,
                    },
                    {
                        "imageIndex": 2,
                        "symbol": "lbug::common::TaskScheduler::runWorkerThread()",
                        "imageOffset": 200,
                        "symbolLocation": 492,
                    },
                ],
            },
        ],
    }
    path = directory / name
    path.write_text(
        json.dumps({"bug_type": "309"}) + "\n" + json.dumps(body), encoding="utf-8",
    )
    return path


class TestCrashState:
    def test_missing_state_returns_none(self, state_path: Path) -> None:
        assert crash_report.read_crash_state() is None

    def test_corrupt_state_returns_none(self, state_path: Path) -> None:
        state_path.write_text("{not json", encoding="utf-8")
        assert crash_report.read_crash_state() is None

    def test_read_and_mark_reported(self, state_path: Path) -> None:
        _write_state(state_path)
        state = crash_report.read_crash_state()
        assert state is not None
        assert state["exit_code"] == 139
        crash_report.mark_crash_reported()
        again = crash_report.read_crash_state()
        assert again is not None and again["reported"] is True

    def test_collect_consumes_once(self, state_path: Path, ips_dir: Path) -> None:
        _write_state(state_path)
        crash = crash_report.collect_previous_crash()
        assert crash is not None
        assert crash["exit_code"] == 139
        assert crash["ips"] is None  # 目录中无匹配报告
        # 已标记 reported：二次消费返回 None
        assert crash_report.collect_previous_crash() is None

    def test_collect_ignores_already_reported(self, state_path: Path) -> None:
        _write_state(state_path, reported=True)
        assert crash_report.collect_previous_crash() is None


class TestIpsParsing:
    def test_parse_report_extracts_fault_info(self, ips_dir: Path) -> None:
        path = _write_ips(ips_dir, "python3.10-2026-08-16-080812.ips",
                          "2026-08-16 08:08:00.8050 +0800")
        parsed = crash_report.parse_ips_report(path)
        assert parsed is not None
        assert parsed["process"] == "python3.10"
        assert parsed["exception_type"] == "EXC_BAD_ACCESS"
        assert parsed["signal"] == "SIGSEGV"
        assert parsed["faulting_module"] == "_lbug.cpython-310-darwin.so"
        # 栈摘要跳过 libsystem/libpython 机制帧，从故障模块开始
        assert parsed["stack"][0].startswith("_lbug.cpython-310-darwin.so")
        assert "scanNext" in parsed["stack"][0]

    def test_parse_broken_report_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.ips"
        path.write_text("only one line", encoding="utf-8")
        assert crash_report.parse_ips_report(path) is None

    def test_find_related_within_window(self, state_path: Path, ips_dir: Path) -> None:
        _write_ips(ips_dir, "python3.10-2026-08-16-080812.ips",
                   "2026-08-16 08:08:00.8050 +0800")
        found = crash_report.find_related_ips("2026-08-16T08:08:05+08:00")
        assert found is not None
        assert found["faulting_module"] == "_lbug.cpython-310-darwin.so"

    def test_find_related_outside_window(self, ips_dir: Path) -> None:
        _write_ips(ips_dir, "python3.10-2026-08-16-080812.ips",
                   "2026-08-16 08:08:00.8050 +0800")
        assert crash_report.find_related_ips("2026-08-16T09:30:00+08:00") is None

    def test_latest_summary_prefers_newest(self, ips_dir: Path) -> None:
        _write_ips(ips_dir, "python3.10-old.ips", "2026-08-15 01:00:00.0000 +0800")
        _write_ips(ips_dir, "python3.10-new.ips", "2026-08-16 08:08:00.8050 +0800")
        summary = crash_report.latest_ips_summary(max_age_hours=10 ** 6)
        assert summary is not None
        assert summary["capture_time"].startswith("2026-08-16")

    def test_collect_attaches_related_ips(self, state_path: Path, ips_dir: Path) -> None:
        _write_state(state_path, crashed_at="2026-08-16T08:08:05+0800")
        _write_ips(ips_dir, "python3.10-2026-08-16-080812.ips",
                   "2026-08-16 08:08:00.8050 +0800")
        crash = crash_report.collect_previous_crash()
        assert crash is not None
        assert crash["ips"] is not None
        assert crash["ips"]["faulting_module"] == "_lbug.cpython-310-darwin.so"


class TestRendering:
    def test_format_summary_with_ips(self) -> None:
        crash = {
            "exit_code": 139,
            "signal": "SIGSEGV",
            "crashed_at": "2026-08-16T08:08:00+0800",
            "crash_count": 2,
            "ips": {
                "process": "python3.10",
                "exception_type": "EXC_BAD_ACCESS",
                "signal": "SIGSEGV",
                "codes": "0x1, 0x0",
                "faulting_module": "_lbug.cpython-310-darwin.so",
                "stack": ["_lbug.cpython-310-darwin.so: scanNext +60"],
            },
        }
        text = crash_report.format_crash_summary(crash)
        assert "SIGSEGV" in text
        assert "连续第 2 次" in text
        assert "_lbug.cpython-310-darwin.so" in text
        assert "scanNext" in text

    def test_format_summary_without_ips(self) -> None:
        crash = {"exit_code": 134, "crashed_at": "2026-08-16T08:08:00+0800"}
        text = crash_report.format_crash_summary(crash)
        assert "SIGABRT" in text
        assert "崩溃栈" not in text


class TestExitCodes:
    def test_crash_codes_detected(self) -> None:
        assert crash_report.is_crash_exit_code(139)
        assert crash_report.is_crash_exit_code(134)
        assert crash_report.is_crash_exit_code(132)

    def test_non_crash_codes_rejected(self) -> None:
        # SIGKILL/SIGTERM/普通错误不触发崩溃守护
        assert not crash_report.is_crash_exit_code(137)
        assert not crash_report.is_crash_exit_code(143)
        assert not crash_report.is_crash_exit_code(1)
        assert not crash_report.is_crash_exit_code(0)

    def test_signal_name_lookup(self) -> None:
        assert crash_report.signal_name_for_code(139) == "SIGSEGV"
        assert crash_report.signal_name_for_code(1) == ""


class TestDatetimeParsing:
    @pytest.mark.parametrize(
        "text",
        [
            "2026-08-16T08:08:00+0800",          # start.sh date +%z
            "2026-08-16T08:08:00+08:00",         # ISO
            "2026-08-16 08:08:00.8050 +0800",    # ips captureTime
            "2026-08-16T08:08:00",               # 无时区
        ],
    )
    def test_supported_formats(self, text: str) -> None:
        parsed = crash_report._parse_datetime(text)
        assert parsed is not None
        assert parsed.hour == 8

    def test_invalid_returns_none(self) -> None:
        assert crash_report._parse_datetime("not a date") is None
        assert crash_report._parse_datetime("") is None
