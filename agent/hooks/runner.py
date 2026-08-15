"""用户 hook 执行器 — 匹配、串行执行与退出码合并（实现见包 docstring）。"""

from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List

from core.command import run_command
from core.log import log

# 支持的事件（封闭集合：新增事件须同步更新 __init__ docstring 与文档）
HOOK_EVENTS = ("tool_pre", "tool_post", "reply_end")

# 单 hook 超时上限（秒）——用户配置值被 clamp 到此值
_MAX_TIMEOUT_SEC = 60.0
_DEFAULT_TIMEOUT_SEC = 10.0


@dataclass(frozen=True)
class HookSpec:
    """一条 hook 声明。"""

    event: str
    matcher: str            # 工具名 glob（fnmatch）；reply_end 恒 "*"
    command: str
    timeout: float = _DEFAULT_TIMEOUT_SEC

    def matches_tool(self, tool_name: str) -> bool:
        return fnmatch.fnmatchcase(tool_name or "*", self.matcher or "*")


@dataclass
class HookOutcome:
    """一次事件的 hook 合并结果。

    合并语义（对齐 dsh deny>ask>allow）：任一 hook exit 2 → allowed=False
    （reason 取第一个阻塞理由）；否则 allowed=True。executed 为实际运行的
    hook 数（含非阻塞失败）。
    """

    allowed: bool = True
    reason: str = ""
    executed: int = 0
    blocked_by: List[str] = field(default_factory=list)


class HookRegistry:
    """hook 配置持有者（fail-closed 加载：坏文件保留上次成功集）。"""

    def __init__(self) -> None:
        self._hooks: Dict[str, List[HookSpec]] = {e: [] for e in HOOK_EVENTS}

    def load(self, path: str) -> int:
        """从 JSON 文件加载，返回加载的 hook 总数（失败抛异常，调用方决定保留旧集）。"""
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raise ValueError("hooks.json 顶层应为对象")
        hooks: Dict[str, List[HookSpec]] = {e: [] for e in HOOK_EVENTS}
        count = 0
        for event, entries in raw.items():
            if event not in HOOK_EVENTS:
                raise ValueError(f"未知 hook 事件: {event}（可用: {', '.join(HOOK_EVENTS)}）")
            if not isinstance(entries, list):
                raise ValueError(f"hooks.json[{event}] 应为数组")
            for entry in entries:
                if not isinstance(entry, dict) or not entry.get("command"):
                    raise ValueError(f"hooks.json[{event}] 条目缺少 command")
                hooks[event].append(HookSpec(
                    event=event,
                    matcher=str(entry.get("matcher", "*")),
                    command=str(entry["command"]),
                    timeout=max(1.0, min(float(entry.get("timeout", _DEFAULT_TIMEOUT_SEC)), _MAX_TIMEOUT_SEC)),
                ))
                count += 1
        self._hooks = hooks  # 全量校验通过才提交（原子替换）
        return count

    def empty(self) -> bool:
        return not any(self._hooks.values())

    def for_event(self, event: str) -> List[HookSpec]:
        return self._hooks.get(event, [])


_registry = HookRegistry()


def get_hook_registry() -> HookRegistry:
    return _registry


def hooks_active(event: str = "") -> bool:
    """快捷判定（集成点的零开销短路）：总开关关闭或无该事件的 hook 即 False。"""
    try:
        from core.config import get_config_bool
        if not get_config_bool("hooks_enabled", True):
            return False
    except Exception:
        return False
    if not event:
        return not _registry.empty()
    return bool(_registry.for_event(event))


def reload_hooks(path: str) -> int:
    """加载（或热重载）hooks.json；文件不存在视为空配置（清空旧集）。"""
    global _registry
    if not os.path.isfile(path):
        _registry = HookRegistry()
        return 0
    try:
        new_reg = HookRegistry()
        count = new_reg.load(path)
        _registry = new_reg
        log(f"用户 hooks 已加载: {count} 条（{path}）", tag="Hook")
        return count
    except Exception as exc:
        # fail-closed：保留上次成功集（对齐 permission_rules 语义）
        log(f"hooks.json 加载失败（保留上次配置）: {exc}", "WARNING", tag="Hook")
        return -1


async def run_event_hooks(event: str, **payload: Any) -> HookOutcome:
    """执行匹配该事件的 hooks（串行），返回合并结果。

    payload 以 JSON 写入 stdin，并镜像到环境变量 HOOK_EVENT/HOOK_TOOL
    （便于简单脚本免解析）。调用方负责先经 hooks_active(event) 短路。
    """
    outcome = HookOutcome()
    tool_name = str(payload.get("tool_name", ""))
    env = {
        "HOOK_EVENT": event,
        "HOOK_TOOL": tool_name,
    }
    stdin_text = json.dumps({**payload, "event": event}, ensure_ascii=False, default=str)
    for spec in _registry.for_event(event):
        if tool_name and not spec.matches_tool(tool_name):
            continue
        outcome.executed += 1
        result = await run_command.async_version(
            spec.command,
            timeout_sec=int(spec.timeout),
            env_vars=env,
            stdin_data=stdin_text,
        )
        if result.ok:
            continue
        # exit 2 = 阻塞（stderr 作为理由，对齐 Claude Code hooks 语义）；
        # 超时/其他退出码/异常 = 非阻塞错误（WARNING，不影响主流程）
        if result.returncode == 2:
            outcome.allowed = False
            outcome.blocked_by.append(spec.command[:60])
            if not outcome.reason:
                outcome.reason = result.stderr[:300] or "hook 拒绝（exit 2，无理由输出）"
            log(f"hook 阻塞 {event}/{tool_name or '-'}: {result.stderr[:120]}", "WARNING", tag="Hook")
        else:
            log(f"hook 非阻塞失败 {event}/{tool_name or '-'}: "
                f"{result.stderr[:120] or f'exit {result.returncode}'}", "WARNING", tag="Hook")
    return outcome


# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

_HOOK_CONFIGS = {
    "system/hooks": {
        "hooks_enabled": {
            "description": "启用用户 hook 事件面（config/hooks.json，工具前/后与回复完成"
                           "事件执行用户脚本；exit 2 阻塞工具调用）。空配置零开销",
            "default": True,
        },
    },
}

from core.config import register_configs_safe  # noqa: E402

register_configs_safe(_HOOK_CONFIGS)
