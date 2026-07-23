"""感知哈希（dHash）：纯 PIL 实现的 64 位差值哈希，用于图搜图粗筛与重复检测。

不引入 imagehash 依赖：灰度化 → 缩放到 9x8 → 相邻像素比较得到 64 bit。
对缩放/压缩/轻微编辑鲁棒，汉明距离越小越相似（0 = 几乎相同）。
"""

from __future__ import annotations

from typing import Optional

_HASH_SIZE = 8  # 8x8 = 64 bit


def compute_phash(path: str) -> str:
    """计算图片的 64 位 dHash，返回 16 字符十六进制字符串。失败返回空串。"""
    try:
        from PIL import Image
        with Image.open(path) as img:
            # 动图取首帧；转灰度后缩放到 (hash_size+1, hash_size)
            try:
                img.seek(0)
            except EOFError:
                pass
            gray = img.convert("L").resize((_HASH_SIZE + 1, _HASH_SIZE), Image.LANCZOS)
            pixels = list(gray.getdata())
        bits = 0
        for row in range(_HASH_SIZE):
            for col in range(_HASH_SIZE):
                left = pixels[row * (_HASH_SIZE + 1) + col]
                right = pixels[row * (_HASH_SIZE + 1) + col + 1]
                bits = (bits << 1) | (1 if left > right else 0)
        return f"{bits:016x}"
    except Exception:
        return ""


def hamming_distance(hash_a: str, hash_b: str) -> Optional[int]:
    """两个十六进制哈希的汉明距离；非法输入返回 None。"""
    if not hash_a or not hash_b or len(hash_a) != len(hash_b):
        return None
    try:
        return bin(int(hash_a, 16) ^ int(hash_b, 16)).count("1")
    except ValueError:
        return None
