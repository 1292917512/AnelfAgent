"""运行时引导流程 -- 基于频道系统的模块化初始化。

每个步骤独立 import，通过 FlowMachine blackboard 传递数据。
"""

from __future__ import annotations

from core.flow import FlowMachine
from core.log import log


class BK:
    """Bootstrap blackboard 键名常量。"""

    STORAGE = "result_init_storage"
    LLM = "result_init_llm"
    CHANNEL = "result_init_channel_system"
    PERSONA = "result_init_persona"
    MEMORY = "result_init_memory"


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
        import asyncio
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
            asyncio.create_task(
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

        db_path = default_sqlite_path().replace(".sqlite3", "_memory.sqlite3")
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
