"""崩溃尾部修复 — 让中断对模型可读（对齐 dsh session/repair 的合成关闭事件）。

think_loop 进行中的状态目前只有内存标记（``mind._active_scopes``），进程
崩溃/SIGKILL 时随进程蒸发——重启后模型不知道上一轮执行到哪、哪个工具没
返回、回复发了一半。本模块补上这一环：

- 回复进入时向 ``reply_checkpoints`` 表写检查点，正常/协作中断结束在 finally
  清除（见 decision_executor.execute_reply）；
- 启动时 ``recover_interrupted_replies`` 扫描残留检查点，向对应会话注入
  "[系统] 上一次回复被意外中断"元消息（写 DB，下次会话可见），随后删除行。

幂等性：元消息注入成功后才删行——注入后、删行前再次崩溃，下次重启会重复
注入一条。该代价可接受（元消息轻量、模型可忽略），换来的是绝不丢失"上次
被中断"这一事实。与 recover_unanswered（未回复消息补回）职责分离：这里只
保证模型知道上次中断了，是否补回回复由消息补回机制决定。
"""

from __future__ import annotations

from core.log import log

# 注入的中断元消息（文案对齐 think_loop._handle_interrupt 的协作中断提示）
INTERRUPTED_NOTICE = (
    "[系统] 上一次回复在执行中被意外中断（进程重启），"
    "未完成的操作已放弃。如需继续，请基于对话历史重新发起。"
)


async def recover_interrupted_replies(mind) -> int:
    """扫描崩溃残留的回复检查点，注入中断元消息并清除。返回处理的 scope 数。

    在 bootstrap 的 recover_interrupted 节点（后台）调用；任何异常吞掉并
    记日志，绝不影响启动流程（fail-open）。
    """
    try:
        return await _do_recover(mind)
    except Exception as exc:
        log(f"崩溃尾部恢复失败（已忽略）: {exc}", "WARNING", tag="启动")
        return 0


async def _do_recover(mind) -> int:
    router = mind.conversation_data.router
    sqlite = router.sqlite
    rows = await sqlite.load_reply_checkpoints()
    if not rows:
        return 0

    from agent.messages.everything import parse_entity_scope
    from agent.storage.storage_router import StorageDomain

    recovered = 0
    for row in rows:
        scope_key = row.get("scope_key") or ""
        if not scope_key:
            continue
        # parse_entity_scope 对非法格式返回空 scope_type（不抛异常）
        scope_type, _adapter, _base_id, _session_id = parse_entity_scope(scope_key)
        if not scope_type:
            log(f"残留检查点 scope 非法，直接清除: {scope_key}", "DEBUG", tag="启动")
            await sqlite.clear_reply_checkpoint(scope_key)
            continue
        # entity_scope = "{scope_type}_{scope_id}" → scope_id 为首个下划线后的剩余部分
        scope_id = scope_key[len(scope_type) + 1:]
        try:
            await router.append(
                StorageDomain.CONVERSATION,
                scope_type=scope_type, scope_id=scope_id,
                role="system", content=INTERRUPTED_NOTICE,
                adapter_key=row.get("adapter_key", "") or "",
                trigger_mind=False,
            )
        except Exception as exc:
            # 注入失败不删行，下次重启重试（at-least-once）
            log(f"中断元消息注入失败（保留检查点待重试）: {scope_key} ({exc})",
                "WARNING", tag="启动")
            continue
        await sqlite.clear_reply_checkpoint(scope_key)
        recovered += 1
    if recovered:
        log(f"崩溃尾部恢复: {recovered} 个 scope 注入中断元消息", tag="启动")
    return recovered
