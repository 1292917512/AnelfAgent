"""扫码组件按需准备无头浏览器，复用安装缓存并回收下载子进程。"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING

import psutil

from core.log import log

if TYPE_CHECKING:
    from playwright.async_api import Browser, Playwright


def _stop_download(pid: int) -> None:
    """结束安装器及其子进程，避免关闭登录后遗留下载任务。"""
    try:
        parent = psutil.Process(pid)
        processes = parent.children(recursive=True) + [parent]
    except psutil.NoSuchProcess:
        return
    for process in processes:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            log("扫码浏览器下载进程无法回收，请检查运行用户权限", "WARNING")
    psutil.wait_procs(processes, timeout=3)


async def _install_browser() -> None:
    """仅下载当前 Playwright 对应的 Chromium 无头运行包。"""
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "playwright", "install", "chromium", "--only-shell",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with asyncio.timeout(300):
            code = await process.wait()
        if code:
            log(f"扫码浏览器自动准备失败（退出码 {code}）", "WARNING")
            raise RuntimeError("browser_prepare_failed")
    finally:
        if process.returncode is None:
            await asyncio.to_thread(_stop_download, process.pid)
            await process.wait()


async def open_browser(runtime: Playwright, on_prepare: Callable[[], None]) -> Browser:
    """复用已安装浏览器；缺少对应版本时自动准备，不修改 Python 环境。"""
    from playwright.async_api import Error

    try:
        return await runtime.chromium.launch(headless=True)
    except Error as exc:
        if "Executable doesn't exist" not in str(exc):
            raise
    on_prepare()
    await _install_browser()
    return await runtime.chromium.launch(headless=True)
