#!/usr/bin/env python3
"""实体面板软链接维护脚本。

扫描 entities/*/panel.tsx，在 web/frontend/src/pages/entities/panels/ 下
创建/更新软链接，使 Vite 的 import.meta.glob 能发现实体自定义面板。

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
    # 清理无效链接
    for existing in PANELS_DIR.iterdir():
        if existing.suffix == ".tsx" and existing.is_symlink():
            if not existing.resolve().exists():
                existing.unlink()

    for entity_dir in sorted(ENTITIES_DIR.iterdir()):
        if not entity_dir.is_dir() or entity_dir.name.startswith("_"):
            continue
        panel_src = entity_dir / "panel.tsx"
        if not panel_src.exists():
            continue

        link_name = f"{entity_dir.name}.tsx"
        link_path = PANELS_DIR / link_name

        # 计算相对路径（从 panels 目录到实体 panel.tsx）
        rel = os.path.relpath(panel_src, PANELS_DIR)

        if link_path.is_symlink():
            current = os.readlink(link_path)
            if current == rel:
                linked.append(entity_dir.name)
                continue
            link_path.unlink()

        link_path.symlink_to(rel)
        linked.append(entity_dir.name)

    return linked


def main() -> None:
    linked = link_panels()
    if linked:
        print(f"✅ 实体面板已链接: {', '.join(linked)} ({len(linked)})")
    else:
        print("ℹ️  未发现实体面板 (entities/*/panel.tsx)")


if __name__ == "__main__":
    main()
