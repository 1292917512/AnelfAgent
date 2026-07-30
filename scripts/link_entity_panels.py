#!/usr/bin/env python3
"""实体面板软链接维护脚本。

扫描 entities/*/panel.tsx，在 web/frontend/src/pages/entities/panels/ 下
创建/更新软链接，使 Vite 的 import.meta.glob 能发现实体自定义面板。
实体可将面板拆分为 entities/<name>/panels/ 子目录，整个目录会被软链为
panels/<name>/，panel.tsx 内用相对导入（如 ./<name>/SubPanel）引用。

用法：
    python scripts/link_entity_panels.py

在 bootstrap 或前端构建前执行。
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENTITIES_DIR = PROJECT_ROOT / "entities"
PANELS_DIR = PROJECT_ROOT / "web" / "frontend" / "src" / "pages" / "entities" / "panels"


def link_panels() -> list[str]:
    """扫描并创建软链接，返回已链接的实体名列表。"""
    PANELS_DIR.mkdir(parents=True, exist_ok=True)

    linked: list[str] = []
    # 期望的软链集合：链接名 → 相对目标
    expected: dict[str, str] = {}
    for entity_dir in sorted(ENTITIES_DIR.iterdir()):
        if not entity_dir.is_dir() or entity_dir.name.startswith("_"):
            continue
        panel_src = entity_dir / "panel.tsx"
        if not panel_src.exists():
            continue
        expected[f"{entity_dir.name}.tsx"] = os.path.relpath(panel_src, PANELS_DIR)
        # 面板拆分子目录：entities/<name>/panels/ → panels/<name>/
        sub_dir = entity_dir / "panels"
        if sub_dir.is_dir():
            expected[entity_dir.name] = os.path.relpath(sub_dir, PANELS_DIR)
        linked.append(entity_dir.name)

    # 清理：非期望集合或目标已失效的软链
    for existing in PANELS_DIR.iterdir():
        if not existing.is_symlink():
            continue
        if existing.name not in expected or not existing.exists():
            existing.unlink()

    # 创建/更新软链
    for name, rel in expected.items():
        link_path = PANELS_DIR / name
        if link_path.exists():
            if link_path.is_symlink() and os.readlink(link_path) == rel:
                continue
            link_path.unlink()
        link_path.symlink_to(rel, target_is_directory=(PANELS_DIR / rel).is_dir())

    return linked


def main() -> None:
    linked = link_panels()
    if linked:
        print(f"✅ 实体面板已链接: {', '.join(linked)} ({len(linked)})")
    else:
        print("ℹ️  未发现实体面板 (entities/*/panel.tsx)")


if __name__ == "__main__":
    main()
