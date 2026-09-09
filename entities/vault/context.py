"""密码本状态上下文提供者：向 AI 注入解锁模式与条目数（非密元信息）。

只暴露"未初始化/机器模式自动解锁/主密码模式已锁定 + 条目数"，
帮助 AI 判断何时需要引导主人解锁，绝不注入任何条目内容。
"""

from __future__ import annotations

from typing import Optional

from core.context_provider import ProviderSnapshot
from entities._sdk import context_provider


@context_provider(
    name="vault_status",
    priority=40,
    max_tokens=60,
    group="vault",
    inject_key="vault_context_inject",
)
async def vault_status_provider(scope: str) -> Optional[ProviderSnapshot]:
    """密码本状态（解锁模式与条目数）。"""
    from .service import get_vault_service

    service = get_vault_service()
    try:
        status = await service.status()
    except Exception:
        return None
    if not status["initialized"]:
        return None  # 未初始化不注入（首次写入会自动建库），避免噪音
    count = status["entry_count"]
    if status["unlock_mode"] == "machine":
        content = f"[密码本] 机器密钥模式（自动解锁），共 {count} 条"
    elif status["unlocked"]:
        content = f"[密码本] 已解锁（主密码模式），共 {count} 条"
    else:
        content = (f"[密码本] 已锁定（主密码模式，共 {count} 条），"
                   "需要凭据时经 vault_unlock 解锁（主密码由主人提供）")
    return ProviderSnapshot(content=content, ready=True)
