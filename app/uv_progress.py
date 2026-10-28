"""将 uv 的流式回执转为安装状态；未知输出保留日志，不推算整体百分比。"""

from __future__ import annotations

import importlib
import re
from typing import Any

from app.operation import Operation
from app.terminal import COLUMNS, ROWS

_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_SUMMARY = re.compile(r"^(Resolved|Prepared|Installed|Audited|Checked) (\d+) packages?\b")
_BYTES = r"(\d+(?:\.\d+)?)\s*([KMGT]?i?B)"
_DOWNLOAD = re.compile(r"^(\S+)\s+.*?" + _BYTES + r"\s*/\s*" + _BYTES + r"\s*$")


def byte_count(value: str, unit: str) -> int:
    """uv 使用二进制单位；同时兼容明确标注的十进制字节单位。"""
    power = "BKMGT".index(unit[0])
    return round(float(value) * (1024 if "i" in unit else 1000) ** power)


class UvProgress:
    """按完整行消费回执，兼容分块 UTF-8、CRLF 与不同 uv 版本的额外诊断。"""

    def __init__(self, operation: Operation) -> None:
        self.operation = operation
        self.pending = ""
        self.active: dict[str, None] = {}
        self.completed: set[str] = set()
        self.value: dict[str, Any] = {"phase": "resolving", "package": "", "downloads": 0, "active": []}
        self.operation.report(self.value)
        self.screen: Any = None
        self.stream: Any = None

    def terminal(self, text: str) -> list[str]:
        """通过成熟 VT 解析器还原光标刷新，提取每个包的真实字节进度。"""
        if self.screen is None:
            pyte = importlib.import_module("pyte")
            self.screen = pyte.Screen(COLUMNS, ROWS)
            self.stream = pyte.Stream(self.screen)
        self.stream.feed(text)
        lines = [line.strip() for line in self.screen.display if line.strip()]
        packages = []
        for line in lines:
            self._line(line)
            match = _DOWNLOAD.match(line)
            if match:
                name, received, unit, total, total_unit = match.groups()
                packages.append({"name": name, "received": byte_count(received, unit),
                                 "total": byte_count(total, total_unit)})
        self.value.update(packages=packages[-8:])
        if packages:
            self.value.update(phase="downloading", package="", active=[item["name"] for item in packages])
        self.operation.report(self.value)
        return lines

    def feed(self, text: str, *, final: bool = False) -> None:
        self.pending += text.replace("\r", "\n")
        lines = self.pending.split("\n")
        self.pending = lines.pop()
        if final and self.pending:
            lines.append(self.pending)
            self.pending = ""
        for line in lines:
            self._line(_ANSI.sub("", line).strip())
        # A tool can write a very long line; diagnostic data must stay bounded.
        self.pending = self.pending[-8000:]

    def _line(self, line: str) -> None:
        summary = _SUMMARY.match(line)
        event = re.match(r"^(Downloading|Downloaded|Building|Built|Extracting|Extracted)\s+(.+)", line)
        if summary:
            key, count = summary.groups()
            field = {"Resolved": "resolved", "Prepared": "prepared", "Installed": "installed",
                     "Audited": "audited", "Checked": "audited"}[key]
            self.value[field] = int(count)
            self.value.update(phase={"Resolved": "preparing", "Prepared": "installing",
                                     "Installed": "verifying", "Audited": "verifying", "Checked": "verifying"}[key],
                              package="")
        elif event:
            verb, package = event.groups()
            name = package.split(" (", 1)[0]
            if verb == "Downloading":
                self.active[name] = None
            elif verb == "Downloaded":
                self.active.pop(name, None)
                self.completed.add(name)
            self.value.update(phase={"Downloading": "downloading", "Downloaded": "preparing",
                                     "Building": "building", "Built": "preparing",
                                     "Extracting": "extracting", "Extracted": "preparing"}[verb], package=package)
        else:
            # Verbose mode also emits useful small-package/cache activity without a TTY.
            debug = re.match(r"^DEBUG (?:Installing|Extracting|Unpacking) (.+)", line)
            if not debug:
                return
            self.value.update(phase="installing", package=debug.group(1)[:200])
        self.value.update(downloads=len(self.completed), active=list(self.active)[-5:])
        self.operation.report(self.value)
