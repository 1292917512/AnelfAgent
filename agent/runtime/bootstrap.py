"""运行时引导流程 -- 基于频道系统的模块化初始化。

每个步骤独立 import，通过 FlowMachine blackboard 传递数据。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Coroutine

from core.flow import FlowMachine, result_key
from core.log import log


class BK:
    """Bootstrap blackboard 键名常量。"""

    STORAGE = result_key("init_storage")
    LLM = result_key("init_llm")
    CHANNEL = result_key("init_channel_system")
    PERSONA = result_key("init_persona")
    MEMORY = result_key("init_memory")


# 持有后台任务引用，避免 fire-and-forget 任务被 GC 提前回收
_background_tasks: set[asyncio.Task] = set()


def _spawn_background(coro: Coroutine[Any, Any, Any], *, name: str) -> asyncio.Task:
    """创建后台任务并保活引用，任务结束后自动从集合移除。"""
    task = asyncio.create_task(coro, name=name)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


def create_bootstrap() -> FlowMachine:
    """构建运行时初始化流程并返回 FlowMachine 实例。"""
    machine = FlowMachine()

    @machine.node(skip_on_error=False)
    async def init_storage():
        from agent.storage.data_center import create_data_center
        from core.lifecycle import Lifecycle

        data_center = create_data_center()
        Lifecycle.register("data_center", data_center, cleanup=data_center.sqlite.close)
        log("DataCenter 已创建")
        return data_center

    @machine.node(skip_on_error=True)
    async def init_proxy():
        """将应用代理配置同步到环境变量，供 litellm 等库使用。"""
        import os
        from core.config import ConfigManager

        if not ConfigManager.get('proxy_enabled', False):
            return

        http_proxy: str = ConfigManager.get('http_proxy', '')
        https_proxy: str = ConfigManager.get('https_proxy', '')

        if http_proxy:
            os.environ['HTTP_PROXY'] = http_proxy
            os.environ['http_proxy'] = http_proxy
        if https_proxy:
            os.environ['HTTPS_PROXY'] = https_proxy
            os.environ['https_proxy'] = https_proxy

        log(f"代理已启用: http={http_proxy or '(未设置)'}, https={https_proxy or '(未设置)'}")

    @machine.node(skip_on_error=False)
    async def init_llm():
        from agent.llm import get_llm_manager
        from core.lifecycle import Lifecycle
        manager = get_llm_manager()
        Lifecycle.register("llm_manager", manager, cleanup=manager.close)
        llm = manager.get_default()
        log(f"LLM 默认客户端: {llm.config.name} ({llm.config.model})")
        return {"manager": manager, "llm": llm}

    @machine.node(skip_on_error=False)
    async def init_channel_system():
        """初始化频道管理器和输入管道。"""
        from agent.channel import ChannelManager, InputPipeline, get_channel_manager
        cm = get_channel_manager()
        pipeline = InputPipeline()
        return {"channel_manager": cm, "pipeline": pipeline}

    @machine.node(skip_on_error=True)
    async def register_entities():
        from entities import discover_entities
        discover_entities()

    @machine.node(skip_on_error=True)
    async def import_api_registry():
        from core.entity import EntityRegistry
        EntityRegistry.import_from_api_registry()

    @machine.node(skip_on_error=True)
    async def init_mcp():
        from entities.mcp.bridge import (
            MCPBridge,
            load_mcp_config,
            register_mcp_tools,
            set_mcp_bridge,
        )
        config = load_mcp_config()
        bridge = MCPBridge(config=config)
        set_mcp_bridge(bridge)
        register_mcp_tools()
        log(f"MCP Bridge: {len(config.servers)} servers")
        enabled_count = sum(1 for s in config.servers if s.enabled)
        if enabled_count:
            _spawn_background(
                asyncio.to_thread(bridge.connect_all),
                name="mcp-autoconnect",
            )
            log(f"MCP: {enabled_count} servers connecting in background...")

    @machine.node(skip_on_error=False)
    async def init_persona():
        from agent.runtime.factory import load_persona
        char = load_persona()
        return char

    @machine.node(skip_on_error=False)
    async def init_memory():
        from agent.memory.embedder import Embedder
        from agent.memory.memory_store import MemoryStore
        from agent.memory.memory_migrate import needs_migration, migrate_memories_to_md
        from agent.memory.memory_sync import sync_files
        from agent.storage.sqlite_backend import default_sqlite_path
        from core.lifecycle import Lifecycle

        # 在主库路径基础上派生记忆库路径： stem + "_memory" + 原后缀
        # （with_suffix 要求后缀以 "." 开头，且 replace 对非 .sqlite3 后缀会失效，故按 stem 拼接）
        _main = Path(default_sqlite_path())
        db_path = str(_main.with_name(f"{_main.stem}_memory{_main.suffix or '.sqlite3'}"))
        store = MemoryStore(db_path=db_path)
        embedder = Embedder()

        await store._get_db()
        Lifecycle.register("memory_store", store, cleanup=store.close)

        from agent.llm import get_llm_manager
        embed_client = get_llm_manager().get_embedding_client()
        log(
            f"MemoryStore: db={db_path}, "
            f"embedding={'可用 (' + embed_client.config.name + ')' if embed_client else 'FTS-only'}"
        )

        from agent.memory.notes import get_workspace_dir
        workspace_dir = get_workspace_dir()
        workspace_dir.mkdir(parents=True, exist_ok=True)

        if await needs_migration(db_path):
            try:
                count = await migrate_memories_to_md(db_path, workspace_dir)
                log(f"数据迁移: {count} 条记忆已导出到 {workspace_dir}")
            except Exception as exc:
                log(f"数据迁移失败（不影响启动）: {exc}", "WARNING")

        try:
            stats = await sync_files(store, embedder, workspace_dir)
            if stats["synced"] or stats["removed"]:
                log(f"文件索引同步: {stats}")
        except Exception as exc:
            log(f"文件索引同步失败（不影响启动）: {exc}", "WARNING")

        from agent.memory.embedding_worker import EmbeddingWorker, set_embedding_worker

        embedding_worker = EmbeddingWorker(store, embedder)
        await embedding_worker.start()
        set_embedding_worker(embedding_worker)
        Lifecycle.register(
            "embedding_worker",
            embedding_worker,
            cleanup=embedding_worker.close,
        )

        cognee_client = None
        cognee_coordinator = None
        try:
            from agent.memory.cognee.client import CogneeClient
            from agent.memory.cognee.config import load_cognee_config
            from agent.memory.cognee.coordinator import CogneeCoordinator
            from agent.memory.cognee.runtime import set_cognee_runtime

            cognee_config = load_cognee_config()
            cognee_client = CogneeClient(cognee_config)
            cognee_coordinator = CogneeCoordinator(store, cognee_client, cognee_config)
            await cognee_coordinator.start()
            set_cognee_runtime(cognee_client, cognee_coordinator)
            Lifecycle.register(
                "cognee_memory",
                cognee_coordinator,
                cleanup=cognee_coordinator.close,
            )
            availability = cognee_client.availability()
            log(
                f"Cognee: enabled={cognee_config.enabled}, "
                f"installed={availability.installed}, ready={availability.ready}"
            )
        except Exception as exc:
            log(f"Cognee 可选后端初始化失败（已降级）: {exc}", "WARNING")

        return {
            "store": store,
            "embedder": embedder,
            "workspace_dir": workspace_dir,
            "cognee_client": cognee_client,
            "cognee_coordinator": cognee_coordinator,
        }

    @machine.node(skip_on_error=False)
    async def register_internal_tools():
        from agent.memory.notes import register_notes_tools
        from agent.memory.tools import register_memory_tools
        from agent.planning import register_planning_tools
        from agent.channel.output_tools import register_output_tools
        from agent.skills import SkillMatcher, SkillStore, register_skill_tools

        mem = machine.get(BK.MEMORY)
        data_center = machine.get(BK.STORAGE)
        register_memory_tools(mem["store"], mem["embedder"])
        register_notes_tools(workspace_dir=mem.get("workspace_dir"))
        register_planning_tools(mem["store"])
        register_output_tools(data_center.conversation_data)

        skill_store = SkillStore()
        register_skill_tools(skill_store, SkillMatcher(skill_store, mem["embedder"]))

        # 图片感知索引 worker：入站图片后台沉淀（phash/描述/向量），支撑文搜图/图搜图
        from entities.sticker.worker import ImageIndexWorker, set_image_index_worker
        from core.lifecycle import Lifecycle
        image_index_worker = ImageIndexWorker()
        await image_index_worker.start()
        set_image_index_worker(image_index_worker)
        Lifecycle.register(
            "image_index_worker", image_index_worker,
            cleanup=image_index_worker.close,
        )

    @machine.node(skip_on_error=False)
    async def assemble_runtime():
        """纯组装：Mind -> Assistant -> Runtime -> set_runtime。"""
        from agent.mind import Mind
        from agent.runtime.assistant import AgentAssistant
        from agent.runtime.runtime import AgentRuntime
        from agent.runtime.singleton import set_runtime
        # 提前导入 scheduler 模块，使其 deferred 工具在 Mind 初始化
        # activate_group("thinking") 时一并注册
        from agent.mind.tools.scheduler import set_mind

        data_center = machine.get(BK.STORAGE)
        llm_data = machine.get(BK.LLM)
        ch_data = machine.get(BK.CHANNEL)
        char = machine.get(BK.PERSONA)
        mem = machine.get(BK.MEMORY)

        channel_manager = ch_data["channel_manager"]
        pipeline = ch_data["pipeline"]

        mind = Mind(
            char=char,
            llm=llm_data["llm"],
            llm_manager=llm_data["manager"],
            channel_manager=channel_manager,
            everything_data=data_center.everything_data,
            conversation_data=data_center.conversation_data,
            storage_router=data_center.router,
            memory_store=mem["store"],
        )
        assistant = AgentAssistant(mind)
        pipeline.register_agent(assistant)

        runtime = AgentRuntime(
            channel_manager=channel_manager,
            pipeline=pipeline,
            assistant=assistant,
            mind=mind,
            char=char,
            llm=llm_data["llm"],
            data_center=data_center,
        )
        set_runtime(runtime)

        set_mind(mind)

        log(
            f"AgentRuntime 已组装: chat={llm_data['llm'].config.name} "
            f"({llm_data['llm'].config.model})"
        )
        return runtime

    @machine.node(skip_on_error=False)
    async def start_agent():
        """启动 AgentApp 事件循环和 Assistant 心跳。"""
        from agent.runtime.singleton import get_runtime
        from agent.runtime.agent_app import get_agent_app
        from core.lifecycle import Lifecycle

        runtime = get_runtime()
        runtime.assistant.start()

        app = get_agent_app()
        await app.start()

        Lifecycle.register("agent_app", app, cleanup=app.stop)
        Lifecycle.register("assistant", runtime.assistant, cleanup=runtime.assistant.stop)
        log("AgentApp + Assistant 已启动")

    @machine.node(skip_on_error=True)
    async def restore_states():
        """恢复持久化的工具/实体状态覆盖（失败不影响启动）。"""
        from core.entity import EntityRegistry, EntityType
        from services.tool import ToolService
        from services.entity import EntityService
        from services.tag import TagService

        ToolService.apply_overrides()
        EntityService.apply_entity_states()
        TagService.load_custom_tags()

        tool_count = len(EntityRegistry.get_by_type(EntityType.TOOL))
        entity_count = len(EntityRegistry.get_all())
        catalog_count = len(EntityRegistry.get_entity_catalog())
        log(f"实体就绪: tools={tool_count}, entities={entity_count}, groups={catalog_count}")

    @machine.node(skip_on_error=True)
    async def register_channels():
        """自动发现并注册所有已启用的频道。"""
        from channels import discover_channels
        from agent.channel import get_channel_manager
        cm = get_channel_manager()
        for channel in discover_channels():
            cm.register(channel)

    @machine.node(skip_on_error=True)
    async def recover_unanswered():
        """启动恢复（后台执行，不阻塞 bootstrap 收尾与 WebUI 端口开放）：

        1. 未回复消息补回：feel() 先把消息写入 DB 再入内存队列，进程在
           "已收到未回复"窗口期重启后，消息在 DB 里但回复触发器已丢——
           扫描各 scope 最后一条消息，若是窗口期内的真用户消息则重新入队，
           让她"醒来后看到错过的消息"（复用提醒 catch-up 范式）。
        2. PFC 待办 replay：pending_tasks 表中未消费的画像分析/通用任务
           重新入队（消费时才删行，replay 后再次崩溃也不丢）。
        """
        _spawn_background(_recover_unanswered(), name="recover-unanswered")

    async def _recover_unanswered() -> None:
        try:
            await _do_recover_unanswered()
        except Exception as exc:
            log(f"启动恢复失败: {exc}", "ERROR", tag="启动")

    async def _do_recover_unanswered() -> None:
        import time
        from core.config import get_config_bool, get_config_float
        from agent.runtime.singleton import get_runtime

        rt = get_runtime()
        mind = rt.mind
        sqlite = rt.data_center.sqlite

        # ---- B: PFC 待办 replay ----
        if get_config_bool("pfc_persist_enabled", True):
            rows = await sqlite.load_pending_tasks()
            if rows:
                restored = mind.pfc.restore_persisted_tasks(rows)
                if restored:
                    log(f"PFC 待办已恢复: {restored} 条", tag="启动")

        # ---- A: 未回复消息补回 ----
        if not get_config_bool("recovery_unanswered_enabled", True):
            return
        max_age_hours = get_config_float("recovery_max_age_hours", 24.0)
        cutoff_ns = time.time_ns() - int(max_age_hours * 3600 * 1e9)

        from agent.mind.context_compressor import is_genuine_user_message
        from agent.mind.tools.scheduler import enqueue_scope_reply

        last_msgs = await sqlite.list_scopes_with_last_message()
        recovered = 0
        for row in last_msgs:
            if row["role"] != "user" or row["ts_ns"] < cutoff_ns:
                continue
            if not is_genuine_user_message({"role": "user", "content": row["content"]}):
                continue
            scope = f"{row['scope_type']}_{row['scope_id']}"
            preview = row["content"][:300]
            enqueue_scope_reply(
                mind.pfc, scope, row["adapter_key"], preview,
                f"[系统] 进程重启前你收到了这条消息但尚未回复（对话历史中可见其完整内容）：\n"
                f"{preview}\n请现在补回处理。",
            )
            recovered += 1
        if recovered:
            log(f"未回复消息恢复: {recovered} 个 scope 重新入队", tag="启动")
            _spawn_background(mind.try_execute_mind(), name="recover-mind-execute")

    @machine.node(skip_on_error=True)
    async def check_health():
        """启动健康检查 — 验证关键组件就绪状态。"""
        from core.entity import EntityRegistry, EntityType
        from agent.runtime.singleton import get_runtime

        issues: list[str] = []
        rt = get_runtime()

        if rt.llm is None:
            issues.append("LLM 默认客户端未就绪")

        if rt.mind.memory_store:
            try:
                await rt.mind.memory_store._get_db()
            except Exception as e:
                issues.append(f"MemoryStore 连接失败: {e}")

        tool_count = len(EntityRegistry.get_by_type(EntityType.TOOL))
        if tool_count == 0:
            issues.append("EntityRegistry 中无已注册工具")

        channels = rt.channel_manager.list_channels()
        if not channels:
            issues.append("无已注册频道")

        if issues:
            for issue in issues:
                log(f"健康检查警告: {issue}", "WARNING")
        else:
            log(
                f"健康检查通过: LLM={rt.llm.config.name}, "
                f"tools={tool_count}, channels={len(channels)}"
            )

    return machine


# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

_RECOVERY_CONFIGS = {
    "启动恢复": {
        "recovery_unanswered_enabled": {
            "description": "启动时扫描各对话窗口，补回重启前收到但尚未回复的消息",
            "default": True,
        },
        "recovery_max_age_hours": {
            "description": "未回复消息恢复的最大消息年龄（小时），超龄消息不再补回",
            "default": 24.0,
        },
        "pfc_persist_enabled": {
            "description": "PFC 待办（画像分析/通用任务）持久化，重启后自动恢复执行",
            "default": True,
        },
    },
}

from core.config import register_configs_safe  # noqa: E402

register_configs_safe(_RECOVERY_CONFIGS)
