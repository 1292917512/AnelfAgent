"""本地检索服务的部署配置、版本替换和跨进程操作锁。"""

from __future__ import annotations

import os
import re
import secrets
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DEFAULTS = {
    "CRAWL4AI_IMAGE": "unclecode/crawl4ai:0.9.4",
    "SEARXNG_IMAGE": "docker.io/searxng/searxng:2026.10.13-22c64b8d3",
    "CRAWL4AI_PORT": "11235", "SEARXNG_PORT": "18080",
}
IMAGE_KEYS = {"crawl4ai": "CRAWL4AI_IMAGE", "searxng": "SEARXNG_IMAGE"}
OFFICIAL_IMAGES = {"crawl4ai": "unclecode/crawl4ai", "searxng": "searxng/searxng"}


def write_private(path: Path, content: str) -> None:
    """原子写入只对当前用户可读的部署配置。"""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.touch(mode=0o600, exist_ok=True)
    temporary.chmod(0o600)
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def initialize(root: Path) -> None:
    """创建缺失的本地配置和随机凭据，不覆盖已有配置。"""
    if not (root / ".env").exists():
        values = {**DEFAULTS, "CRAWL4AI_API_TOKEN": secrets.token_hex(32)}
        write_private(root / ".env", "".join(f"{key}={value}\n" for key, value in values.items()))
    settings = root / "runtime" / "searxng" / "settings.yml"
    if not settings.exists():
        settings.parent.mkdir(parents=True, exist_ok=True)
        template = (root / "settings.yml.example").read_text(encoding="utf-8")
        settings.write_text(template.replace("__GENERATED_SECRET__", secrets.token_hex(32)), encoding="utf-8")


def read_values(root: Path) -> dict[str, str]:
    """读取本项目生成的 dotenv 字面量，端口和镜像有固定默认值。"""
    values = dict(DEFAULTS)
    path = root / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and not key.startswith("#"):
                values[key.strip()] = value.strip().strip("\"'")
    return values


def validate_images(images: dict[str, str]) -> None:
    """只接受明确版本或摘要的官方镜像，拒绝浮动 latest 与命令参数。"""
    for service, value in images.items():
        repository = OFFICIAL_IMAGES.get(service)
        if not repository or not re.fullmatch(
            rf"(?:docker\.io/)?{re.escape(repository)}(?::[A-Za-z0-9_][A-Za-z0-9_.-]{{0,127}}|@sha256:[a-f0-9]{{64}})", value,
        ) or value.endswith(":latest"):
            raise ValueError(f"{service} 需要官方镜像的明确标签或 sha256 摘要，不支持 latest")


def replace_images(content: str, images: dict[str, str]) -> str:
    """只替换镜像引用，保持端口、凭据及其他配置。"""
    validate_images(images)
    lines = content.splitlines()
    for service, value in images.items():
        key = IMAGE_KEYS[service]
        lines = [line for line in lines if not line.startswith(key + "=")]
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


@contextmanager
def operation_lock(root: Path) -> Iterator[None]:
    """串行化 Web、AI 与命令行的部署写操作；进程退出自动释放。"""
    with (root / ".operation.lock").open("a+b") as handle:
        handle.seek(0)
        if not handle.read(1):
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("本地检索服务正在执行其他部署操作，请等待其完成") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
