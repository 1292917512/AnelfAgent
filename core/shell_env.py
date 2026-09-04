"""Shell 子进程环境变量卫生 — 对齐 codex unified_exec 的注入策略。

给工具发起的 shell 命令注入一组"面向脚本"的环境变量，避免两个常见事故：
- ANSI 色码混进输出（NO_COLOR / TERM=dumb / COLORTERM=""）——浪费 token
  且干扰模型解析；
- pager 在管道下等待输入（PAGER/GIT_PAGER/GH_PAGER=cat）——无 tty 时 less
  挂起直到超时被杀，整个输出丢失。
统一 UTF-8 locale 消除工具在 ASCII locale 下的解码警告与编码错误。

用户显式设置的同名变量优先（env.update 在前、本模块补缺在后）。
"""

from __future__ import annotations

from typing import Dict

# 注入的环境变量（对齐 codex process_manager.rs 的强制集）
_SHELL_ENV_DEFAULTS: Dict[str, str] = {
    "NO_COLOR": "1",
    "TERM": "dumb",
    "COLORTERM": "",
    "PAGER": "cat",
    "GIT_PAGER": "cat",
    "GH_PAGER": "cat",
    "LANG": "C.UTF-8",
    "LC_CTYPE": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}


def shell_env_defaults() -> Dict[str, str]:
    """返回卫生变量的副本（调用方先并入自身 env，再以此补缺，用户值优先）。"""
    return dict(_SHELL_ENV_DEFAULTS)
