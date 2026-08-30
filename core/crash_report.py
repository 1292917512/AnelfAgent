"""进程崩溃状态设施 — 守护脚本崩溃状态读写 + 系统崩溃报告解析。

外层启动脚本（start.sh / start.bat）维护守护循环，进程以崩溃退出码
（128+信号）退出时把崩溃状态写入 logs/crash_state.json。本模块提供三类读取：

- ``collect_previous_crash``：启动时消费崩溃状态（标记 reported 防重复注入），
  macOS 上自动关联 DiagnosticReports（.ips）补充故障模块与崩溃栈摘要；
- ``read_crash_state`` / ``latest_ips_summary``：只读查询，供运维工具与面板；
- ``format_crash_summary``：渲染为可注入 AI 上下文的简洁文本。

Model Experience：本模块不触碰模型输入层，仅产出文本供 crash_recovery
（会话元消息 / 实体推送）与 devops 工具消费。
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CRASH_STATE_PATH = PROJECT_ROOT / "logs" / "crash_state.json"

# 崩溃退出码 = 128 + 信号号；macOS 与 Linux 的 SIGBUS 编号不同，按平台建表
_SIGNAL_NAMES_BY_CODE: Dict[int, str] = {
    128 + 4: "SIGILL",
    128 + 5: "SIGTRAP",
    128 + 6: "SIGABRT",
    128 + 8: "SIGFPE",
    128 + (10 if sys.platform == "darwin" else 7): "SIGBUS",
    128 + 11: "SIGSEGV",
}

# 关联系统崩溃报告的时间窗（崩溃状态时间 vs 报告 captureTime）
_IPS_MATCH_WINDOW_MINUTES = 15
# 崩溃栈摘要最多保留帧数
_IPS_MAX_FRAMES = 8
# 崩溃栈中属于系统/解释器的镜像（定位"故障模块"时跳过）
_SYSTEM_IMAGE_PREFIXES = ("libsystem_", "dyld", "libpython", "libc++")


def signal_name_for_code(exit_code: int) -> str:
    """崩溃退出码对应的信号名（未知返回空串）。"""
    return _SIGNAL_NAMES_BY_CODE.get(exit_code, "")


def is_crash_exit_code(exit_code: int) -> bool:
    """是否为应自动重新拉起的崩溃退出码（致命信号，不含 SIGKILL/SIGTERM）。"""
    return exit_code in _SIGNAL_NAMES_BY_CODE


# ── 崩溃状态（守护脚本写入） ─────────────────────────────────────────


def read_crash_state() -> Optional[Dict[str, Any]]:
    """读取守护脚本写入的崩溃状态；不存在或损坏返回 None。"""
    try:
        raw = CRASH_STATE_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) and data.get("crashed_at") else None
    except (OSError, json.JSONDecodeError):
        return None


def mark_crash_reported() -> None:
    """把当前崩溃状态标记为已通报（启动注入只消费一次，文件保留供运维查询）。"""
    state = read_crash_state()
    if state is None or state.get("reported"):
        return
    state["reported"] = True
    try:
        CRASH_STATE_PATH.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    except OSError:
        pass


def collect_previous_crash() -> Optional[Dict[str, Any]]:
    """读取上一次崩溃状态并关联系统崩溃报告（不标记已通报）。

    标记 reported 是调用方的责任：恢复流程成功注入后才调用
    mark_crash_reported()——先标记后注入会让恢复失败时崩溃上下文永久丢失。
    无崩溃状态或已通报返回 None。
    """
    state = read_crash_state()
    if state is None or state.get("reported"):
        return None
    state["ips"] = find_related_ips(str(state.get("crashed_at") or ""))
    return state


# ── 系统崩溃报告（macOS .ips） ────────────────────────────────────────


def _diagnostic_report_dirs() -> List[Path]:
    # 经变量中转避免 mypy 按 sys.platform 字面量收窄（linux 上会把
    # darwin 分支判为 unreachable，CI（Linux）必红而本地（macOS）全绿）
    is_darwin = sys.platform == "darwin"
    if not is_darwin:
        return []
    home = Path(os.path.expanduser("~"))
    return [
        home / "Library" / "Logs" / "DiagnosticReports",
        Path("/Library/Logs/DiagnosticReports"),
    ]


_FRACTION_RE = re.compile(r"\.\d{1,6}")


def _normalize_for_isoformat(candidate: str) -> str:
    """规范化为 Python 3.10 fromisoformat 可解析的形式。

    - 无冒号时区（+0800）→ 冒号时区（+08:00）；
    - 小数秒补齐到 6 位（3.10 只接受 3 位或 6 位，ips 的 .8050 是 4 位）。
    """
    if len(candidate) >= 5 and candidate[-5] in "+-" and candidate[-4:].isdigit():
        candidate = candidate[:-5] + candidate[-5:-2] + ":" + candidate[-2:]
    return _FRACTION_RE.sub(lambda m: (m.group(0) + "000000")[:7], candidate, count=1)


def _parse_datetime(text: str) -> Optional[datetime]:
    """宽容解析多种时间格式（ISO / 空格分隔 / 无冒号时区）。"""
    if not text:
        return None
    candidate = text.strip()
    # "2026-08-16 08:08:00.8050 +0800"（ips captureTime）→ ISO 风格
    if " " in candidate and candidate.rsplit(" ", 1)[-1][:1] in "+-":
        head, _, tz = candidate.rpartition(" ")
        candidate = f"{head.replace(' ', 'T')}{tz}"
    for value in (candidate, candidate.replace(" ", "T", 1)):
        try:
            return datetime.fromisoformat(_normalize_for_isoformat(value))
        except ValueError:
            continue
    return None


def parse_ips_report(path: Path) -> Optional[Dict[str, Any]]:
    """解析 macOS .ips 崩溃报告，提取异常信息与故障线程栈摘要。

    .ips 格式：首行 JSON 头 + 其余部分 JSON 体。任何解析失败返回 None。
    """
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        _header_sep = raw.find("\n")
        if _header_sep < 0:
            return None
        body = json.loads(raw[_header_sep + 1:])
        images = body.get("usedImages") or []
        threads = body.get("threads") or []
        faulting_index = body.get("faultingThread")
        if not isinstance(faulting_index, int) or faulting_index >= len(threads):
            return None

        def _frame_text(frame: Dict[str, Any]) -> tuple[str, str]:
            image: Dict[str, Any] = {}
            idx = frame.get("imageIndex")
            if isinstance(idx, int) and idx < len(images):
                image = images[idx] or {}
            image_name = str(image.get("name") or "?")
            symbol = frame.get("symbol") or hex(frame.get("imageOffset", 0))
            return image_name, str(symbol)

        frames = threads[faulting_index].get("frames") or []
        # 跳过信号/解释器机制帧，定位真正的故障模块
        fault_start = 0
        for i, frame in enumerate(frames):
            image_name, _ = _frame_text(frame)
            if not image_name.startswith(_SYSTEM_IMAGE_PREFIXES):
                fault_start = i
                break
        # 优先保留带符号的非系统帧（剥离符号的帧只剩裸地址，无诊断价值）
        rest = frames[fault_start:]
        symbolic = [
            f for f in rest
            if f.get("symbol") and not _frame_text(f)[0].startswith(_SYSTEM_IMAGE_PREFIXES)
        ]
        selected = (symbolic or rest)[:_IPS_MAX_FRAMES]
        stack: List[str] = []
        for frame in selected:
            image_name, symbol = _frame_text(frame)
            stack.append(f"{image_name}: {symbol}")

        exception = body.get("exception") or {}
        capture_time = _parse_datetime(str(body.get("captureTime") or ""))
        return {
            "report_path": str(path),
            "process": str(body.get("procName") or path.name.split("-", 1)[0]),
            "capture_time": capture_time.isoformat() if capture_time else "",
            "exception_type": str(exception.get("type") or ""),
            "signal": str(exception.get("signal") or ""),
            "codes": str(exception.get("codes") or ""),
            "faulting_module": (
                _frame_text(frames[fault_start])[0] if frames and fault_start < len(frames) else ""
            ),
            "stack": stack,
        }
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def _iter_python_ips() -> List[tuple[datetime, Path]]:
    """按 captureTime 列出 DiagnosticReports 中的 python 崩溃报告（新→旧）。"""
    reports: List[tuple[datetime, Path]] = []
    for directory in _diagnostic_report_dirs():
        try:
            candidates = sorted(directory.glob("python*.ips"), reverse=True)
        except OSError:
            continue
        for path in candidates[:20]:
            parsed = parse_ips_report(path)
            if not parsed or not parsed["capture_time"]:
                continue
            capture_time = _parse_datetime(parsed["capture_time"])
            if capture_time is not None:
                reports.append((capture_time, path))
    reports.sort(key=lambda item: item[0], reverse=True)
    return reports


def find_related_ips(crashed_at: str) -> Optional[Dict[str, Any]]:
    """查找与崩溃状态时间匹配（±15 分钟）的系统崩溃报告。"""
    anchor = _parse_datetime(crashed_at)
    if anchor is None:
        return None
    window = timedelta(minutes=_IPS_MATCH_WINDOW_MINUTES)
    for capture_time, path in _iter_python_ips():
        if abs(capture_time - anchor) <= window:
            return parse_ips_report(path)
    return None


def latest_ips_summary(max_age_hours: float = 72.0) -> Optional[Dict[str, Any]]:
    """最近窗口内的 python 进程崩溃报告摘要（只读，供运维查询）。"""
    now = datetime.now().astimezone()
    for capture_time, path in _iter_python_ips():
        if now - capture_time <= timedelta(hours=max_age_hours):
            return parse_ips_report(path)
        break  # 列表按新→旧排序，最新一条超窗即无更旧可看
    return None


# ── 文本渲染 ─────────────────────────────────────────────────────────


def format_crash_summary(crash: Dict[str, Any]) -> str:
    """渲染崩溃信息为简洁文本（注入 AI 上下文 / 运维展示）。"""
    signal_name = str(crash.get("signal") or "") or signal_name_for_code(
        int(crash.get("exit_code") or 0)
    )
    count = int(crash.get("crash_count") or 1)
    lines = [
        f"上一次进程崩溃：{crash.get('crashed_at', '未知时间')}"
        f"（{signal_name or '异常退出'}，退出码 {crash.get('exit_code', '?')}"
        f"，连续第 {count} 次）"
    ]
    ips = crash.get("ips")
    if isinstance(ips, dict) and ips.get("stack"):
        detail = f"{ips.get('exception_type', '')}/{ips.get('signal', '')}".strip("/")
        codes = ips.get("codes") or ""
        lines.append(
            f"系统崩溃报告：进程 {ips.get('process', 'python')} {detail}"
            + (f"（{codes}）" if codes else "")
        )
        module = ips.get("faulting_module") or ""
        if module:
            lines.append(f"故障模块：{module}")
        lines.append("崩溃栈（故障线程）：")
        lines.extend(f"  {frame}" for frame in ips["stack"])
    return "\n".join(lines)
