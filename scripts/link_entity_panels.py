#!/usr/bin/env python3
"""模块前端软链接维护脚本。

统一管理两类模块前端的软链（与 vite.config.ts 的 moduleFrontendsPlugin 同逻辑，
供 bootstrap / CI 中 tsc 前先创建软链的场景使用）：

- 实体面板：entities/<name>/panel.tsx（+ panels/ 子目录）
  → web/frontend/src/pages/entities/panels/
- 频道前端：channels/<id>/frontend/（整目录，需含 index.ts）
  → web/frontend/src/plugins/channels/<id>/

用法：
    python scripts/link_entity_panels.py

在 bootstrap 或前端构建前执行。
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENTITIES_DIR = PROJECT_ROOT / "entities"
CHANNELS_DIR = PROJECT_ROOT / "channels"
PANELS_DIR = PROJECT_ROOT / "web" / "frontend" / "src" / "pages" / "entities" / "panels"
CHANNEL_PLUGINS_DIR = PROJECT_ROOT / "web" / "frontend" / "src" / "plugins" / "channels"


def _sync_dir(target_dir: Path, expected: dict[str, str]) -> None:
    """把 target_dir 下的软链集合收敛到 expected（链接名 → 相对目标）。"""
    target_dir.mkdir(parents=True, exist_ok=True)
    for existing in target_dir.iterdir():
        if not existing.is_symlink():
            continue
        if existing.name not in expected or not existing.exists():
            existing.unlink()
    for name, rel in expected.items():
        link_path = target_dir / name
        if link_path.exists():
            if link_path.is_symlink() and os.readlink(link_path) == rel:
                continue
            link_path.unlink()
        link_path.symlink_to(rel, target_is_directory=(target_dir / rel).is_dir())


def link_panels() -> list[str]:
    """扫描并链接实体面板，返回已链接的实体名列表。"""
    expected: dict[str, str] = {}
    linked: list[str] = []
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
    _sync_dir(PANELS_DIR, expected)
    return linked


def link_channel_frontends() -> list[str]:
    """扫描并链接频道前端目录，返回已链接的频道 ID 列表。"""
    expected: dict[str, str] = {}
    linked: list[str] = []
    for channel_dir in sorted(CHANNELS_DIR.iterdir()):
        if not channel_dir.is_dir() or channel_dir.name.startswith("_"):
            continue
        frontend_dir = channel_dir / "frontend"
        if not frontend_dir.is_dir() or not (frontend_dir / "index.ts").exists():
            continue
        expected[channel_dir.name] = os.path.relpath(frontend_dir, CHANNEL_PLUGINS_DIR)
        linked.append(channel_dir.name)
    _sync_dir(CHANNEL_PLUGINS_DIR, expected)
    return linked


def main() -> None:
    panels = link_panels()
    channels = link_channel_frontends()
    if panels:
        print(f"✅ 实体面板已链接: {', '.join(panels)} ({len(panels)})")
    if channels:
        print(f"✅ 频道前端已链接: {', '.join(channels)} ({len(channels)})")
    if not panels and not channels:
        print("ℹ️  未发现模块前端（entities/*/panel.tsx 或 channels/*/frontend/）")


if __name__ == "__main__":
    main()
