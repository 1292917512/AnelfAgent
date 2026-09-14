"""屏幕捕获后端（mss 截屏 + JPEG 落盘）。

mss 实例非线程安全且抓取是阻塞 I/O：每次捕获在工作线程内新建实例，
抓完即释放。macOS 需"屏幕录制"权限，未授权时 mss 抛错或返回黑图——
异常向上抛由工具层转结构化错误（fail-closed，不静默产出假画面）。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path

from entities._sdk import CapturedFrame


def _capture_sync(monitor: int, out_dir: Path) -> CapturedFrame:
    import mss  # 延迟导入：未安装/无显示环境下实体其余功能仍可用
    from PIL import Image

    out_dir.mkdir(parents=True, exist_ok=True)
    with mss.mss() as sct:
        monitors = sct.monitors  # [0]=全部合并，1..N=各显示器
        if monitor < 0 or monitor >= len(monitors):
            monitor = 1 if len(monitors) > 1 else 0
        shot = sct.grab(monitors[monitor])
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    dest = out_dir / f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}.jpg"
    img.save(dest, "JPEG", quality=80)
    return CapturedFrame(
        path=str(dest), width=img.width, height=img.height,
        captured_at=time.time(), monitor=monitor,
    )


async def capture_screen(monitor: int = 1) -> CapturedFrame:
    """截取指定显示器（默认主屏），JPEG q80 落盘 workspace/uploads/screen/。"""
    from core.path import ConfigPaths
    out_dir = Path(str(ConfigPaths.UPLOAD_DIR)) / "screen"
    return await asyncio.to_thread(_capture_sync, monitor, out_dir)
