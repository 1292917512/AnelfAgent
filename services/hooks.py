"""hooks 管理服务 — web 侧薄门面（config/hooks.json 的读写/校验/热重载/测试运行）。

校验逻辑复用 agent.hooks.runner.parse_hooks_data（与运行时加载同一口径），
写盘成功后立即 reload_hooks 生效（配置监听同时兜底）。观测数据（启用状态/
集成点/运行统计）同样来自 runner 单一事实源。

Model Experience：本面只做配置与观测，不注入任何模型上下文，token 零影响、
不触碰前缀层。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from core.file_utils import atomic_write_text
from core.path import ConfigPaths


class HookService:
    """hooks.json 管理面（读取 / 校验写入 / 立即重载 / 测试运行）。"""

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

    async def run_hooks_test(self, event: str, tool_name: str = "*") -> Dict[str, Any]:
        """手动触发一次事件（合成 payload），逐条返回执行结果。

        显式测试动作：不受 hooks_enabled 总开关影响（响应中带回当前开关
        状态供前端提示）；测试执行同样计入运行统计并标记 test=true。
        """
        from agent.hooks.runner import HOOK_EVENTS, run_event_hooks
        from core.config import get_config_bool

        if event not in HOOK_EVENTS:
            raise ValueError(f"未知 hook 事件: {event}（可用: {', '.join(HOOK_EVENTS)}）")
        payload: Dict[str, Any] = {"test": True, "source": "web"}
        if event != "reply_end":
            payload["tool_name"] = tool_name or "*"
        outcome = await run_event_hooks(event, **payload)
        return {
            "event": event,
            "enabled": get_config_bool("hooks_enabled", True),
            "allowed": outcome.allowed,
            "executed": outcome.executed,
            "reason": outcome.reason,
            "results": outcome.results,
        }

    def get_example(self) -> Dict[str, Any]:
        """返回样例配置（config/hooks.example.json）。"""
        example = Path(ConfigPaths.HOOKS).with_name("hooks.example.json")
        if not example.is_file():
            # 运行时 config 目录可被整体迁移，样例回退到仓库内置文件
            example = Path(__file__).resolve().parent.parent / "config" / "hooks.example.json"
        with open(example, "r", encoding="utf-8") as f:
            return json.load(f)


hook_service = HookService()
