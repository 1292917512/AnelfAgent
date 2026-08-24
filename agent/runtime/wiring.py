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
    from agent.memory.embedding import Embedder, EmbeddingWorker
    from agent.memory.memory_store import MemoryStore
    from agent.mind.mind import Mind
    from agent.storage.data_center import DataCenter
    from entities.sticker.worker import ImageIndexWorker


def wire_runtime(
    *,
    mind: "Mind",
    data_center: "DataCenter",
    memory_store: "MemoryStore",
    embedder: "Embedder",
    embedding_worker: "EmbeddingWorker",
    cognee_client: Optional["CogneeClient"],
    cognee_coordinator: Optional["CogneeCoordinator"],
    image_index_worker: "ImageIndexWorker",
) -> None:
    """统一施绑全部晚绑定端口并接线跨模块回调。

    由 bootstrap 的 assemble_runtime 尾部调用一次；端口清单与
    core.latebind 注册表对齐，遗漏由 check_health 的 assert_wired 暴露。
    """
    from agent.channel.output_tools import conversation_data_port
    from agent.delegation.delegate_tool import delegation_manager_port
    from agent.memory.auto_capture import auto_capture_port
    from agent.memory.cognee.runtime import cognee_client_port, cognee_coordinator_port
    from agent.memory.embedding.worker import attach_pending_backlogs, embedding_worker_port
    from agent.memory.graph.tools import graph_store_port
    from agent.memory.tools import MemoryToolDeps, memory_tools_port
    from agent.mind.context_compressor import compressor_port
    from agent.mind.tools.ports import mind_port
    from agent.planning.tracker import planning_store_port
    from agent.skills.tools import SkillToolDeps, skill_tools_port
    from agent.storage.conversation_fold import fold_data_port
    from entities.sticker.worker import image_index_worker_port

    # 思维工具组（scheduler / session_tools / short_term_tools 共用）
    mind_port.set(mind)

    # agent → entities 跨层桥（消费方 agent 侧声明端口，此处以 entities 实现施绑）
    from agent.approval.policy import WorkspacePathFns, workspace_paths_port
    from agent.mind.tools.media_pipeline import image_index_submit_port
    from agent.mind.tools.result_pipeline import shell_persist_port
    from agent.mind.tools.round_helpers import file_state_cache_port
    from entities.filesystem.file_state import get_cache as get_file_state_cache
    from entities.filesystem.paths import get_workspace_root, resolve_workspace_path
    from entities.filesystem.shell_state import persist_output
    from entities.sticker.worker import submit_image

    workspace_paths_port.set(WorkspacePathFns(get_workspace_root, resolve_workspace_path))
    file_state_cache_port.set(get_file_state_cache)
    shell_persist_port.set(persist_output)
    image_index_submit_port.set(submit_image)

    # 思维子系统实例（Mind 构造持有，工具层经端口消费）
    compressor_port.set(mind.compressor)
    delegation_manager_port.set(mind.delegation_manager)
    auto_capture_port.set(mind.auto_capture_pipeline)
    skill_tools_port.set(SkillToolDeps(mind.skill_store, mind.skill_matcher))

    # 记忆存储族（memory/graph/planning 工具组共用同一 MemoryStore）
    memory_tools_port.set(MemoryToolDeps(memory_store, embedder))
    graph_store_port.set(memory_store)
    planning_store_port.set(memory_store)

    # 会话数据（输出工具回写历史 / 对话折叠工具）
    conversation_data_port.set(data_center.conversation_data)
    fold_data_port.set(data_center.conversation_data)

    # Embedding 后台 worker（施绑后挂载施绑前挂起的外部 backlog 注册）
    embedding_worker_port.set(embedding_worker)
    attach_pending_backlogs(embedding_worker)

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
