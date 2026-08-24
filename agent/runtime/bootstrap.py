"""运行时引导流程 -- 基于频道系统的模块化初始化。

每个步骤独立 import，通过 FlowMachine blackboard 传递数据。
"""

from __future__ import annotations

import asyncio
import contextlib
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


async def cancel_background_tasks() -> None:
    """取消并等待所有 bootstrap 后台任务（关停前置步骤，由 Application 宿主调用）。"""
    tasks = [t for t in _background_tasks if not t.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def create_bootstrap() -> FlowMachine:
    """构建运行时初始化流程并返回 FlowMachine 实例。"""
    machine = FlowMachine()

    @machine.node(skip_on_error=False, depends_on=[])
    async def init_storage():
        from agent.storage.data_center import create_data_center
        from agent.storage.volume_restore import consume_pending_restores
        from core.lifecycle import Lifecycle

        # 重启落盘的卷恢复交换：任何存储连接打开前完成文件替换
        try:
            consume_pending_restores()
        except Exception as exc:
            log(f"存储卷恢复标记消费失败（跳过）: {exc}", "ERROR")

        data_center = create_data_center()
        Lifecycle.register("data_center", data_center, cleanup=data_center.sqlite.close)
        log("DataCenter 已创建")
        return data_center

    @machine.node(skip_on_error=True, depends_on=[])
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

    @machine.node(skip_on_error=False, depends_on=["init_proxy"])
    async def init_llm():
        from agent.llm import get_llm_manager
        from core.lifecycle import Lifecycle
        manager = get_llm_manager()
        Lifecycle.register("llm_manager", manager, cleanup=manager.close)
        llm = manager.get_default()
        log(f"LLM 默认客户端: {llm.config.name} ({llm.config.model})")
        return {"manager": manager, "llm": llm}

    @machine.node(skip_on_error=False, depends_on=[])
    async def init_channel_system():
        """初始化频道管理器和输入管道。"""
        from agent.channel import InputPipeline, get_channel_manager
        cm = get_channel_manager()
        pipeline = InputPipeline()
        return {"channel_manager": cm, "pipeline": pipeline}

    @machine.node(skip_on_error=True, depends_on=[])
    async def register_entities():
        from entities import discover_entities
        discover_entities()

    @machine.node(skip_on_error=True, depends_on=["register_entities"])
    async def import_api_registry():
        from core.entity import EntityRegistry
        EntityRegistry.import_from_api_registry()

    @machine.node(skip_on_error=True, depends_on=["register_entities"])
    async def init_mcp():
        from core.lifecycle import Lifecycle
        from entities.mcp.bridge import MCPBridge, set_mcp_bridge
        from entities.mcp.config import load_mcp_config
        from entities.mcp.manage_tools import register_mcp_tools

        config = load_mcp_config()
        bridge = MCPBridge(config=config)
        set_mcp_bridge(bridge)
        Lifecycle.register("mcp_bridge", bridge, cleanup=bridge.shutdown)
        register_mcp_tools()
        log(f"MCP Bridge: {len(config.servers)} servers")
        enabled_count = sum(1 for s in config.servers if s.enabled)
        if enabled_count:
            _spawn_background(
                asyncio.to_thread(bridge.connect_all),
                name="mcp-autoconnect",
            )
            log(f"MCP: {enabled_count} servers connecting in background...")

    @machine.node(skip_on_error=False, depends_on=[])
    async def init_persona():
        from agent.runtime.factory import load_persona
        char = load_persona()
        return char

    @machine.node(skip_on_error=False, depends_on=["init_storage", "init_llm"])
    async def init_memory():
        from agent.memory.embedding import get_embedder
        from agent.memory.memory_migrate import migrate_memories_to_md, needs_migration
        from agent.memory.memory_store import MemoryStore
        from agent.memory.memory_sync import sync_files
        from core.lifecycle import Lifecycle

        store = MemoryStore()
        db_path = store._db_path
        embedder = get_embedder("text")

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

        from agent.memory.embedding import EmbeddingWorker

        embedding_worker = EmbeddingWorker(store, embedder)
        await embedding_worker.start()
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
            from core.storage_volume import get_volume_registry

            cognee_config = load_cognee_config()
            get_volume_registry().mark_active("cognee", cognee_config.absolute_data_root)
            cognee_client = CogneeClient(cognee_config)
            cognee_coordinator = CogneeCoordinator(store, cognee_client, cognee_config)
            await cognee_coordinator.start()
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
            "embedding_worker": embedding_worker,
            "cognee_client": cognee_client,
            "cognee_coordinator": cognee_coordinator,
        }

    @machine.node(skip_on_error=False, depends_on=["init_memory", "register_entities"])
    async def register_internal_tools():
        """激活内部 deferred 工具组（依赖引用经 wiring.wire_runtime 统一施绑）。

        各工具模块 import 时完成 deferred 注册，本节点只按组弹出激活；
        agent.task.tools 挂在 planning 组，须在 planning 激活前 import。
        """
        import agent.channel.output_tools  # noqa: F401
        import agent.memory.graph.tools  # noqa: F401
        import agent.memory.tools  # noqa: F401
        import agent.planning.tools  # noqa: F401
        import agent.skills.tools  # noqa: F401
        import agent.storage.conversation_fold  # noqa: F401
        import agent.task.tools  # noqa: F401
        from agent.memory.graph.tools import _resolve_alias
        from agent.memory.notes import register_notes_tools
        from entities._sdk import activate_group

        mem = machine.get(BK.MEMORY)
        # 关系图谱别名解析桥（store 侧接线，工具依赖经 wiring 端口施绑）
        mem["store"].graph.set_alias_resolver(_resolve_alias)

        count = activate_group("memory", "长期记忆 - 记忆存储、语义检索、标签索引、遗忘")
        log(f"💾 内部记忆工具已注册 ({count} 个)", tag="思维")
        count = activate_group("graph", "关系图谱 - 人物/概念关系网络的结构化存储与查询")
        log(f"🕸 关系图谱工具已注册 ({count} 个)", tag="思维")
        activate_group("planning", "目标规划管理 - 创建执行计划、追踪目标进度")
        count = activate_group("output", "消息输出 — 向频道发送文本、图片、语音、文件等")
        log(f"统一输出工具已注册 ({count} 个)", tag="通道")
        count = activate_group(
            "skills",
            "技能 - 经验技能的创建、检索、合并与策展，及外部技能源（可插拔商店）的搜索与安装",
        )
        log(f"🎓 技能工具已注册 ({count} 个)", tag="技能")
        register_notes_tools(workspace_dir=mem.get("workspace_dir"))

        # 图片感知索引 worker：入站图片后台沉淀（phash/描述/向量），支撑文搜图/图搜图
        # （引用经 wiring.wire_runtime 统一施绑，本节点只负责创建与生命周期注册）
        from core.lifecycle import Lifecycle
        from entities.sticker.worker import ImageIndexWorker
        image_index_worker = ImageIndexWorker()
        await image_index_worker.start()
        Lifecycle.register(
            "image_index_worker", image_index_worker,
            cleanup=image_index_worker.close,
        )
        return {"image_index_worker": image_index_worker}

    @machine.node(skip_on_error=True, depends_on=["register_entities", "init_memory"])
    async def register_entity_lifecycles():
        """扫描并调用所有实体的 register_lifecycle() 启动钩子。

        实体自治规范：entities/<name>/__init__.py 暴露 register_lifecycle()
        即在此时被自动调用，无需在 bootstrap 中硬编码。
        """
        from entities import discover_entity_lifecycles
        await discover_entity_lifecycles()

    @machine.node(
        skip_on_error=False,
        depends_on=["init_channel_system", "init_persona", "init_memory", "register_internal_tools"],
    )
    async def assemble_runtime():
        """纯组装：Mind -> Assistant -> Runtime -> set_runtime -> 统一施绑。"""
        from agent.mind import Mind

        # 提前导入工具模块，使其 deferred 工具在 Mind 初始化激活
        # thinking/session 分组时一并注册（依赖引用经 wiring 统一施绑）
        from agent.mind.tools import scheduler, session_tools, short_term_tools  # noqa: F401
        from agent.runtime.assistant import AgentAssistant
        from agent.runtime.runtime import AgentRuntime
        from agent.runtime.singleton import set_runtime
        from agent.runtime.wiring import wire_runtime

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

        # 晚绑定统一施绑（mind 工具组 / cognee 可选后端 / sticker worker + 跨模块回调），
        # 漏接线由 check_health 的 assert_wired 暴露为启动红字
        tools_ctx: dict[str, Any] = machine.get_result("register_internal_tools") or {}
        wire_runtime(
            mind=mind,
            data_center=data_center,
            memory_store=mem["store"],
            embedder=mem["embedder"],
            embedding_worker=mem["embedding_worker"],
            cognee_client=mem.get("cognee_client"),
            cognee_coordinator=mem.get("cognee_coordinator"),
            image_index_worker=tools_ctx["image_index_worker"],
        )

        log(
            f"AgentRuntime 已组装: chat={llm_data['llm'].config.name} "
            f"({llm_data['llm'].config.model})"
        )
        return runtime

    @machine.node(skip_on_error=False, depends_on=["assemble_runtime"])
    async def start_agent():
        """启动 AgentApp 事件循环和 Assistant 心跳。"""
        from agent.runtime.agent_app import get_agent_app
        from agent.runtime.singleton import require_runtime
        from core.lifecycle import Lifecycle

        runtime = require_runtime()
        runtime.assistant.start()

        app = get_agent_app()
        await app.start()

        Lifecycle.register("agent_app", app, cleanup=app.stop)
        Lifecycle.register("assistant", runtime.assistant, cleanup=runtime.assistant.stop)
        log("AgentApp + Assistant 已启动")

    @machine.node(skip_on_error=True, depends_on=["assemble_runtime"])
    async def restore_states():
        """恢复持久化的工具/实体状态覆盖（失败不影响启动）。"""
        from agent.runtime.state_restore import (
            apply_entity_states,
            apply_tool_overrides,
            load_custom_tags,
        )
        from core.entity import EntityRegistry, EntityType

        apply_tool_overrides()
        apply_entity_states()
        load_custom_tags()

        tool_count = len(EntityRegistry.get_by_type(EntityType.TOOL))
        entity_count = len(EntityRegistry.get_all())
        catalog_count = len(EntityRegistry.get_entity_catalog())
        log(f"实体就绪: tools={tool_count}, entities={entity_count}, groups={catalog_count}")

    @machine.node(skip_on_error=True, depends_on=["assemble_runtime"])
    async def start_context_providers():
        """启动所有上下文提供者的生命周期（on_start）。"""
        from core.context_provider import ContextProviderRegistry
        from core.lifecycle import Lifecycle

        await ContextProviderRegistry.start_all()
        Lifecycle.register(
            "context_providers", None,
            cleanup=ContextProviderRegistry.stop_all,
        )
        provider_count = len(ContextProviderRegistry.get_all())
        if provider_count:
            log(f"上下文提供者已启动: {provider_count} 个", tag="启动")

    @machine.node(skip_on_error=True, depends_on=["init_channel_system"])
    async def register_channels():
        """自动发现并注册所有已启用的频道。"""
        from agent.channel import get_channel_manager
        from channels import discover_channels
        cm = get_channel_manager()
        for channel in discover_channels():
            cm.register(channel)

    @machine.node(skip_on_error=False, depends_on=["register_channels", "start_agent"])
    async def register_channel_services():
        """频道与看门狗注册进 Lifecycle：on_start 后台启动，cleanup 逆序回收。

        注册在 start_agent 之后，关停时频道先于 assistant/mind 停止（drain 语义：
        先停消息进水口，再收思考与资源）。
        """
        from agent.channel import get_channel_manager
        from agent.channel.supervision import (
            is_supervisor_enabled,
            start_channel_supervisor,
            stop_channel_supervisor,
        )
        from core.lifecycle import Lifecycle

        cm = get_channel_manager()
        start_task: asyncio.Task[None] | None = None

        async def _start_channels() -> None:
            nonlocal start_task

            async def _run() -> None:
                try:
                    await cm.start_all()
                    log("全部频道启动流程完成", tag="启动")
                except Exception as exc:
                    log(f"频道后台启动异常: {exc}", "ERROR", tag="启动")

            start_task = asyncio.create_task(_run(), name="agent.channels_start")

        async def _stop_channels() -> None:
            if start_task and not start_task.done():
                start_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await start_task
            try:
                await cm.stop_all()
            except BaseException:
                pass

        Lifecycle.register(
            "channels", cm, on_start=_start_channels, cleanup=_stop_channels,
        )
        if is_supervisor_enabled():
            Lifecycle.register(
                "channel_supervisor", None,
                on_start=lambda: start_channel_supervisor(cm),
                cleanup=stop_channel_supervisor,
            )

    @machine.node(skip_on_error=True, depends_on=["start_agent"])
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

        from agent.runtime.singleton import require_runtime
        from core.config import get_config_bool, get_config_float

        rt = require_runtime()
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

        from agent.mind.message_schema import is_genuine_user_message
        from agent.mind.tools.scheduler import enqueue_scope_reply

        last_msgs = await sqlite.list_scopes_with_last_message()
        recovered = 0
        for row in last_msgs:
            if row["role"] != "user" or row["ts_ns"] < cutoff_ns:
                continue
            # 入库时 trigger_mind=False 的消息（如 require_mention 下非 @ 群消息）
            # 当时就被判定为不触发思考，重启恢复不应补回
            if not row.get("trigger_mind", True):
                continue
            if not is_genuine_user_message({"role": "user", "content": row["content"]}):
                continue
            scope = f"{row['scope_type']}_{row['scope_id']}"
            preview = row["content"][:300]
            await enqueue_scope_reply(
                mind.pfc, scope, row["adapter_key"], preview,
                f"[系统] 进程重启前你收到了这条消息但尚未回复（对话历史中可见其完整内容）：\n"
                f"{preview}\n请现在补回处理。",
            )
            recovered += 1
        if recovered:
            log(f"未回复消息恢复: {recovered} 个 scope 重新入队", tag="启动")
            _spawn_background(mind.try_execute_mind(), name="recover-mind-execute")

    @machine.node(skip_on_error=True, depends_on=["start_agent"])
    async def recover_interrupted():
        """崩溃尾部恢复（后台执行）：扫描上一次进程崩溃/SIGKILL 残留的
        回复检查点，向对应会话注入"上次被中断"元消息（写 DB，下次会话可见）。
        与 recover_unanswered 职责分离：这里只保证模型知道上次中断了。"""
        _spawn_background(_recover_interrupted(), name="recover-interrupted")

    async def _recover_interrupted() -> None:
        from core.config import get_config_bool
        if not get_config_bool("recovery_interrupted_enabled", True):
            return
        from agent.mind.crash_recovery import (
            collect_crash_context,
            recover_interrupted_replies,
        )
        from agent.runtime.singleton import require_runtime
        try:
            mind = require_runtime().mind
            # 消费上次崩溃状态（守护脚本写入 + 系统崩溃报告关联），仅注入一次
            crash_context = collect_crash_context()
            recovered = await recover_interrupted_replies(mind, crash_context)
            if crash_context and not recovered:
                # 无进行中的回复检查点（如空闲时崩溃）：推送全局通知让她知晓崩溃经过，
                # 并唤醒一轮思维（她的重启报到技能会接管后续向主人报平安）
                mind.push_hub.push(
                    "", "system",
                    f"进程异常重启完成。{crash_context}\n"
                    "请检查自身状态并向主人报到（可经 devops 工具 get_crash_report 复查详情）。",
                    trigger=False,
                )
                _spawn_background(mind.try_execute_mind(), name="crash-notify-mind")
        except Exception as exc:
            log(f"崩溃尾部恢复失败: {exc}", "ERROR", tag="启动")

    @machine.node(skip_on_error=True, depends_on=["start_agent", "register_channel_services"])
    async def check_health():
        """启动健康检查 — 验证关键组件就绪状态。"""
        from agent.runtime.singleton import require_runtime
        from core.entity import EntityRegistry, EntityType
        from core.latebind import assert_wired

        issues: list[str] = []
        rt = require_runtime()

        # 晚绑定端口漏接线在此暴露（wire_runtime 遗漏即缺陷，不静默降级）
        for port_name in assert_wired():
            issues.append(f"晚绑定端口未施绑: {port_name}")

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
    "system/recovery": {
        "recovery_unanswered_enabled": {
            "description": "是否在启动时补回重启前收到但尚未回复的消息",
            "default": True,
        },
        "recovery_interrupted_enabled": {
            "description": "是否在启动时向崩溃残留的会话注入'上次回复被中断'元消息（崩溃尾部修复）",
            "default": True,
        },
        "recovery_max_age_hours": {
            "description": "未回复消息补回的最大年龄，超龄不再补回",
            "default": 24.0,
            "advanced": True,
            "unit": "小时",
        },
        "pfc_persist_enabled": {
            "description": "是否持久化 PFC 待办（画像分析/通用任务），重启后自动恢复执行",
            "default": True,
        },
    },
}

from core.config import register_configs_safe  # noqa: E402

register_configs_safe(_RECOVERY_CONFIGS)
