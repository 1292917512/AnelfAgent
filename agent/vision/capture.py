"""视觉帧数据契约与变化检测（分块 dHash）。

CapturedFrame 是视觉源组件的取帧契约；grid dHash 是内容级变化判定的
统一算法（对亮度/对比度漂移稳健，分块隔离局部活动区与光标闪烁）。
具体捕获后端（mss 截屏等）以组件形式实现并注册为视觉源。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class CapturedFrame:
    """视觉源捕获的一帧。"""

    path: str
    width: int
    height: int
    captured_at: float
    monitor: int = 0


def grid_dhash(path: str, grid: int = 4) -> list[int]:
    """分块差异哈希（变化检测用）：灰度缩放后按 grid×grid 分块，每格 8x8 dHash。

    dHash 跟踪相邻像素梯度，对亮度/对比度漂移稳健；分块则隔离局部持续
    活动区（视频/动画）与光标闪烁（只占一格），由调用方按"变化格占比"
    判定内容级变化。
    """
    from PIL import Image

    with Image.open(path) as img:
        gray = img.convert("L").resize((grid * 9, grid * 8))
    px = gray.load()
    if px is None:
        return []
    cells: list[int] = []
    for gy in range(grid):
        for gx in range(grid):
            bits = 0
            for y in range(8):
                for x in range(8):
                    bits <<= 1
                    left = px[gx * 9 + x, gy * 8 + y]
                    right = px[gx * 9 + x + 1, gy * 8 + y]
                    # "L" 模式像素恒为 int（PIL 类型标注为 union，此处收窄）
                    if int(left) > int(right):  # type: ignore[arg-type]
                        bits |= 1
            cells.append(bits)
    return cells


def hamming(a: int, b: int) -> int:
    """两个 dHash 的汉明距离。"""
    return bin(a ^ b).count("1")


def grid_change_ratio(
    old: list[int], new: list[int], *, cell_threshold: int,
) -> float:
    """变化格占比（两帧同网格哈希的逐格汉明距离超阈值的比例）。"""
    if not old or not new or len(old) != len(new):
        return 1.0
    changed = sum(1 for a, b in zip(old, new, strict=True) if hamming(a, b) > cell_threshold)
    return changed / len(old)
