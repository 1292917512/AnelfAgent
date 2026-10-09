"""陪玩会话的任务停止与事件记录。

Model Experience: 反射与停止事实作为带来源的 system 消息追加到世界历史，
不冒充玩家、不唤醒回复。每次事件增量约数十 token，只追加历史尾部，
不改 stable/summary 前缀。
"""

from agent.messages import build_entity_scope, parse_entity_scope
from agent.mind.tools.scheduler import _append_one_shot_history, remove_scope_reminders
from core.log import log
from core.tags import tag_label
from core.tool_context import consume_control_delegates


def _belongs_to_world(scope: str, world_id: str) -> bool:
    scope_type, adapter, base_id, _ = parse_entity_scope(scope)
    return adapter == "minecraft" and (
        (scope_type == "group" and base_id == world_id)
        or (scope_type == "user" and base_id.startswith(f"{world_id}/"))
    )


async def stop_companion_work(world_id: str, *, server: str = "minecraft", reason: str = "玩家已停止陪玩任务") -> None:
    """中断本世界会话及实际调用过本执行器的委托，清理待回复与续跑提醒。"""
    from agent.runtime.singleton import get_runtime

    runtime = get_runtime()
    if runtime is None:
        return
    mind = runtime.mind
    scopes = {build_entity_scope("group", "minecraft", world_id)}
    scopes.update(scope for scope in mind.pfc.known_scopes() if _belongs_to_world(scope, world_id))
    manager = mind.delegation_manager
    worker_scopes = {
        scope for delegation_id, scope in consume_control_delegates(server).items()
        if manager.cancel(delegation_id) and scope
    }
    for scope in scopes | worker_scopes:
        mind.interrupt(scope, reason=reason)
        mind.pfc.consume_scope_task(scope)
    for scope in scopes:
        manager.cancel_scope(scope)
    await remove_scope_reminders(scopes | worker_scopes)
    note = f"{reason}；此前任务已取消，等待玩家的新指令，不再续跑。"
    for scope in scopes | worker_scopes:
        adapter = parse_entity_scope(scope)[1]
        if not await _append_one_shot_history(
            mind.pfc, scope, adapter, f"{tag_label('push', 'minecraft_stop')} {note}",
        ):
            log(f"Minecraft 停止事实记录失败: {scope}", "WARNING", tag="Minecraft")


async def record_game_event(world_id: str, text: str) -> None:
    """记录确定性动作事实，独立于游戏聊天回声且不触发新回复。"""
    from agent.runtime.singleton import get_runtime

    runtime = get_runtime()
    if runtime is None:
        return
    scope = build_entity_scope("group", "minecraft", world_id)
    prompt = f"{tag_label('push', 'minecraft_reflex')} {text}"
    if not await _append_one_shot_history(runtime.mind.pfc, scope, "minecraft", prompt):
        log(f"Minecraft 事件记录失败: {scope}", "WARNING", tag="Minecraft")
