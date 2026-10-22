"""有界工作进程诊断；落盘前遮盖凭据，不记录 RPC 参数或记忆正文。"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from core.sanitizer import sanitize_text


def credential_values(value: Any) -> set[str]:
    if not isinstance(value, dict):
        return set()
    secrets: set[str] = set()
    for key, item in value.items():
        if isinstance(item, str) and item and any(word in str(key).lower() for word in ("key", "token", "password", "secret")):
            secrets.add(item)
        elif isinstance(item, dict):
            secrets.update(credential_values(item))
    return secrets


class WorkerDiagnostics:
    """最多两个 1 MiB 文件；超长单行由读取器丢弃，避免切断凭据后泄漏。"""

    def __init__(self, path: Path, secrets: set[str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.secrets = sorted(secrets, key=len, reverse=True)
        self.handler = RotatingFileHandler(path, maxBytes=1024**2, backupCount=1, encoding="utf-8", delay=True)
        self.handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))

    def write(self, line: str) -> None:
        for value in self.secrets:
            line = line.replace(value, "[REDACTED]")
        line = sanitize_text(line).rstrip()
        if line:
            self.handler.emit(logging.LogRecord("cognee.worker", logging.WARNING, "", 0, line, (), None))

    def close(self) -> None:
        self.handler.close()
