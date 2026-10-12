"""脚本钩子的统一读写入口；运行时、Web 和 AI 共享校验与热重载。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from core.file_utils import atomic_write_text
from core.path import ConfigPaths


class HookConfiguration:
    """脚本声明的单一读写面，保存前完成全量校验。"""
    @staticmethod
    def _path() -> str:
        return str(ConfigPaths.HOOKS)

    def get_hooks(self) -> Dict[str, Any]:
        """返回当前 hooks 配置（含启用状态、集成点与运行统计）。"""
        from agent.hooks.runner import (
            HOOK_EVENTS,
            HOOK_INTEGRATIONS,
            get_hook_registry,
            get_hook_stats,
        )
        from core.config import get_config_bool
        from core.hooks import hook_journal

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
            "enabled": get_config_bool("hooks_enabled", True),
            "events": list(HOOK_EVENTS),
            "integrations": [dict(item) for item in HOOK_INTEGRATIONS],
            "hooks": raw,
            "active": {
                event: sum(1 for s in registry.for_event(event) if s.enabled)
                for event in HOOK_EVENTS
            },
            "stats": get_hook_stats(),
            "execution": hook_journal.snapshot(),
        }

    def save_hooks(self, raw: Any) -> Dict[str, Any]:
        """校验并全量写入 hooks.json，成功后立即重载生效。

        非法内容抛 ValueError（不落盘）；空对象删除文件（恢复无 hook 状态）。
        """
        from agent.hooks.runner import hooks_data_from_specs, parse_hooks_data, reload_hooks

        specs = parse_hooks_data(raw)  # 校验（全量，非法抛错）
        path = self._path()
        if not any(specs.values()):
            if os.path.isfile(path):
                os.unlink(path)
            reload_hooks(path)
            return {"saved": True, "count": 0}
        atomic_write_text(
            Path(path),
            json.dumps(hooks_data_from_specs(specs), ensure_ascii=False, indent=2) + "\n",
        )
        count = reload_hooks(path)
        return {"saved": True, "count": count}

    def replace_event(self, event: str, entries: list[dict[str, Any]]) -> Dict[str, Any]:
        """只替换指定事件，原文件损坏时拒绝覆盖其他声明。"""
        path = Path(self._path())
        raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        from agent.hooks.runner import parse_hooks_data
        parse_hooks_data(raw)
        raw[event] = entries
        return self.save_hooks(raw)


hook_configuration = HookConfiguration()
