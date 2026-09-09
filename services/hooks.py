"""hooks 管理服务 — web 侧薄门面（config/hooks.json 的读写/校验/热重载）。

校验逻辑复用 agent.hooks.runner.parse_hooks_data（与运行时加载同一口径），
写盘成功后立即 reload_hooks 生效（配置监听同时兜底）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from core.file_utils import atomic_write_text
from core.path import ConfigPaths


class HookService:
    """hooks.json 管理面（读取 / 校验写入 / 立即重载）。"""

    @staticmethod
    def _path() -> str:
        return str(ConfigPaths.HOOKS)

    def get_hooks(self) -> Dict[str, Any]:
        """返回当前 hooks 配置（含运行时状态）。"""
        from agent.hooks.runner import HOOK_EVENTS, get_hook_registry

        path = self._path()
        raw: Dict[str, Any] = {}
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
            except (OSError, json.JSONDecodeError):
                raw = {}
        registry = get_hook_registry()
        return {
            "path": path,
            "exists": os.path.isfile(path),
            "events": list(HOOK_EVENTS),
            "hooks": raw,
            "active": {
                event: len(registry.for_event(event)) for event in HOOK_EVENTS
            },
        }

    def save_hooks(self, raw: Any) -> Dict[str, Any]:
        """校验并全量写入 hooks.json，成功后立即重载生效。

        非法内容抛 ValueError（不落盘）；空对象删除文件（恢复无 hook 状态）。
        """
        from agent.hooks.runner import parse_hooks_data, reload_hooks

        specs = parse_hooks_data(raw)  # 校验（全量，非法抛错）
        path = self._path()
        if not any(specs.values()):
            if os.path.isfile(path):
                os.unlink(path)
            reload_hooks(path)
            return {"saved": True, "count": 0}
        atomic_write_text(
            Path(path),
            json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
        )
        count = reload_hooks(path)
        return {"saved": True, "count": count}

    def get_example(self) -> Dict[str, Any]:
        """返回样例配置（config/hooks.example.json）。"""
        example = Path(ConfigPaths.HOOKS).with_name("hooks.example.json")
        if not example.is_file():
            # 运行时 config 目录可被整体迁移，样例回退到仓库内置文件
            example = Path(__file__).resolve().parent.parent / "config" / "hooks.example.json"
        with open(example, "r", encoding="utf-8") as f:
            return json.load(f)


hook_service = HookService()
