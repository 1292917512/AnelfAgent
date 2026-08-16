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

Model Experience 三行声明：
① 模型看到什么——中断元消息（role=system 写 DB，随对话历史可见）附崩溃
   上下文（约 10 行文本）；无检查点的崩溃经 PushHub 走既有推送通道（volatile
   层短期记忆）。
② token 影响——增量，仅崩溃后首轮可见（每次 ≤ 数百 token），无崩溃时为零。
③ 缓存影响——元消息写 DB 属 conversation 层纯追加；推送走 volatile 尾部动态
   区，均不破前缀缓存。
"""

from __future__ import annotations

from core.log import log

# 注入的中断元消息（文案对齐 think_loop._handle_interrupt 的协作中断提示）
INTERRUPTED_NOTICE = (
    "[系统] 上一次回复在执行中被意外中断（进程重启），"
    "未完成的操作已放弃。如需继续，请基于对话历史重新发起。"
)


def collect_crash_context() -> str:
    """消费上一次进程的崩溃状态，生成可注入的崩溃上下文（无崩溃返回空串）。

    读取启动脚本守护循环写入的崩溃状态（logs/crash_state.json），macOS 上
    自动关联系统崩溃报告（DiagnosticReports .ips）补充故障模块与栈摘要；
    读取后标记 reported，保证只注入一次。任何异常 fail-open 返回空串。
    """
    try:
        from core import crash_report

        crash = crash_report.collect_previous_crash()
        if not crash:
            return ""
        summary = crash_report.format_crash_summary(crash)
        log(f"检测到上次进程崩溃，将注入崩溃上下文: {summary.splitlines()[0]}", tag="启动")
        return summary
    except Exception as exc:
        log(f"崩溃上下文收集失败（已忽略）: {exc}", "DEBUG", tag="启动")
        return ""


def build_interrupted_notice(crash_context: str = "") -> str:
    """组装中断元消息；有崩溃上下文时附带崩溃信息。"""
    if not crash_context:
        return INTERRUPTED_NOTICE
    return f"{INTERRUPTED_NOTICE}\n崩溃信息：\n{crash_context}"


async def recover_interrupted_replies(mind, crash_context: str = "") -> int:
    """扫描崩溃残留的回复检查点，注入中断元消息并清除。返回处理的 scope 数。

    crash_context 非空时（上一次是崩溃而非正常重启），随元消息附带崩溃信息，
    让模型知道上次为何中断。在 bootstrap 的 recover_interrupted 节点（后台）
    调用；任何异常吞掉并记日志，绝不影响启动流程（fail-open）。
    """
    try:
        return await _do_recover(mind, crash_context)
    except Exception as exc:
        log(f"崩溃尾部恢复失败（已忽略）: {exc}", "WARNING", tag="启动")
        return 0


async def _do_recover(mind, crash_context: str = "") -> int:
    router = mind.conversation_data.router
    sqlite = router.sqlite
    rows = await sqlite.load_reply_checkpoints()
    if not rows:
        return 0

    from agent.messages.everything import parse_entity_scope
    from agent.storage.storage_router import StorageDomain

    notice = build_interrupted_notice(crash_context)
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
                role="system", content=notice,
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
