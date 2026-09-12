"""委托崩溃恢复 — 启动时消费账本未闭合条目，让中断对模型可读。

进程崩溃/SIGKILL 时运行中的委托（前台阻塞在工具调用里的 + 后台
asyncio 任务）随进程蒸发——重启后 AI 与用户都不知道有哪些长任务死了。
本模块补上这一环（与 crash_recovery 的 reply_checkpoints 同范式）：

- DelegationManager 在委托真正进入执行时向 ledger.jsonl 写 started，
  各终态路径写 closed；
- 启动时 ``recover_interrupted_delegations`` 扫描未闭合条目，向归属会话
  注入"[系统] 上次运行中有 N 个后台委托被进程中断"元消息（写 DB，
  下次会话可见），随后在账本闭合条目（at-most-once：扫描即闭合，注入
  失败不重试——通知只含事实，丢失无不可逆后果，换来绝不重启风暴）。

只保证模型知道中断了；是否续跑由 AI 决策（transcript 持久化时可用
follow_up_agent 无损续跑）。非会话 scope（reflect: 等一次性域）无处投递，
仅记日志——完成事实不因 scope 静默丢失的原则由账本闭合状态承担。
"""

from __future__ import annotations

from core.log import log

INTERRUPTED_NOTICE = (
    "[系统] 上一次运行中有后台委托被进程中断（未正常收尾），"
    "相关任务的结果不可信。如仍需要其产出，可基于目标重新委托；"
    "上次是后台委托且有 transcript 时可无损续跑。"
)


async def recover_interrupted_delegations(mind) -> int:
    """扫描未闭合委托，注入中断元消息并闭合账本。返回处理的 scope 数。"""
    try:
        return await _do_recover(mind)
    except Exception as exc:
        log(f"委托崩溃恢复失败（已忽略）: {exc}", "WARNING", tag="启动")
        return 0


async def _do_recover(mind) -> int:
    from agent.delegation import journal

    unclosed = journal.unclosed_delegations()
    if not unclosed:
        return 0

    from agent.messages.everything import parse_entity_scope
    from agent.storage.storage_router import StorageDomain

    router = mind.conversation_data.router
    # 按 scope 聚合：一个会话一条元消息（四个委托同时死不该四条打扰）
    by_scope: dict[str, list[dict]] = {}
    for record in unclosed:
        scope = str(record.get("scope", ""))
        if not scope.startswith(("user_", "group_")):
            log(
                f"中断委托无处投递（非会话域，仅记录）: {record.get('goal', '')[:60]}",
                "DEBUG", tag="启动",
            )
            continue
        by_scope.setdefault(scope, []).append(record)

    recovered = 0
    for scope, records in by_scope.items():
        scope_type, _adapter, _base_id, _session_id = parse_entity_scope(scope)
        if not scope_type:
            continue
        scope_id = scope[len(scope_type) + 1:]
        goals = "；".join(
            f"{r.get('goal', '')[:60]}（{r.get('id', '')}）" for r in records[:5]
        )
        notice = (
            f"{INTERRUPTED_NOTICE}\n被中断的委托：{goals}"
            if goals else INTERRUPTED_NOTICE
        )
        try:
            await router.append(
                StorageDomain.CONVERSATION,
                scope_type=scope_type, scope_id=scope_id,
                role="system", content=notice,
                adapter_key=records[0].get("adapter_key", "") or "",
                trigger_mind=False,
            )
            recovered += 1
        except Exception as exc:
            log(f"中断委托元消息注入失败: {scope} ({exc})", "WARNING", tag="启动")
    if recovered:
        log(f"委托崩溃恢复: {len(unclosed)} 个未闭合委托，{recovered} 个 scope 注入元消息",
            tag="启动")
    return recovered
