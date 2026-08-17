"""运行时晚绑定施绑点 — bootstrap 组装完成后的唯一接线入口。

端口由各消费模块声明（``core.latebind.LateBinding``），本模块在组合根
位置统一施绑，check_health 经 ``assert_wired()`` 校验无遗漏。
纪律：只分发已创建的引用 —— 不创建资源、不调用业务方法，保证接线
无序可依赖、清单可审计。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from agent.memory.cognee.client import CogneeClient
    from agent.memory.cognee.coordinator import CogneeCoordinator
    from agent.mind.mind import Mind
    from agent.storage.data_center import DataCenter
    from entities.sticker.worker import ImageIndexWorker


def wire_runtime(
    *,
    mind: "Mind",
    data_center: "DataCenter",
    cognee_client: Optional["CogneeClient"],
    cognee_coordinator: Optional["CogneeCoordinator"],
    image_index_worker: "ImageIndexWorker",
) -> None:
    """统一施绑全部晚绑定端口并接线跨模块回调。

    由 bootstrap 的 assemble_runtime 尾部调用一次；端口清单与
    core.latebind 注册表对齐，遗漏由 check_health 的 assert_wired 暴露。
    """
    from agent.memory.cognee.runtime import cognee_client_port, cognee_coordinator_port
    from agent.mind.tools.ports import mind_port
    from entities.sticker.worker import image_index_worker_port

    # 思维工具组（scheduler / session_tools / short_term_tools 共用）
    mind_port.set(mind)

    # Cognee 可选后端：初始化失败时以 None 施绑（None 是合法绑定值）
    cognee_client_port.set(cognee_client)
    cognee_coordinator_port.set(cognee_coordinator)

    # 图片索引 worker（entities 层端口，agent 组合根施绑）
    image_index_worker_port.set(image_index_worker)

    # 折后预热钩子（storage 层不反向依赖 mind，经注入解耦）
    from agent.storage.conversation_fold import conversation_folder
    conversation_folder.set_prewarm_hook(mind.prewarm_scope_cache)

    # 会话用量账本接线：scope_usage 只暴露回调接口，落盘由主库实现
    # （分层纪律：agent/mind 不直接依赖 storage 层）
    from agent.mind.scope_usage import wire_scope_usage
    wire_scope_usage(data_center.router.sqlite.upsert_scope_usage)
