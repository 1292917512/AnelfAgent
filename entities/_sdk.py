"""
实体开发标准接口。

所有实体模块统一使用本模块提供的装饰器进行声明与注册，
注册目标为 ``core.entity.EntityRegistry``，不依赖 ``agent``。

两种注册模式：

1. 立即注册 — 模块导入时自动注册（适合无运行时依赖的 entities/ 层工具）::

    from entities._sdk import tool, entity

    entity("weather", "天气查询服务")

    @tool(name="get_weather", group="weather")
    async def get_weather(city: str) -> str:
        ...

2. 延迟注册 — 装饰时仅收集元数据，bootstrap 按组 activate_group 批量注册；
   运行时依赖经 core.latebind 端口分发（agent.runtime.wiring 统一施绑）::

    from entities._sdk import deferred_tool, activate_group

    @deferred_tool(group="memory", tags=["always"], source="mind.memory")
    async def memorize(content: str) -> str:
        deps = memory_tools_port.get()  # LateBinding 端口取依赖
        ...

    # bootstrap 组合根：
    activate_group("memory", "长期记忆 - 记忆存储、语义检索")
    memory_tools_port.set(MemoryToolDeps(store, embedder))  # wiring 统一施绑
"""

from __future__ import annotations

import inspect
import json
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional, TypeVar

from core.entity import EntityRegistry
from core.log import log
from core.provider_keys import (  # 组件凭据中心桥
    get_provider_key,
    list_provider_keys,
    register_provider_key,
    set_provider_key,
)
from core.tool_errors import ErrorCause, error_from_exception, tool_error
from core.tool_schema import extract_tool_params, get_first_line

if TYPE_CHECKING:
    from agent.llm.llm_client import LLMClient as LLMClient
    from agent.llm.llm_manager import LLMManager as LLMManager

__all__ = [
    "tool", "deferred_tool", "entity", "activate_group",
    "entity_manifest", "entity_config", "context_provider",
    "push_notify", "register_entity_llm_hook",
    "get_embedder", "wake_embedding_worker", "register_embedding_backlog",
    "register_provider_key", "get_provider_key", "set_provider_key",
    "list_provider_keys",
    "register_audio_provider", "register_tts_provider", "audio_transcribe", "audio_speaker_embed",
    "default_tts_voice", "realtime_tts_voice",
    "audio_ingest_payload", "audio_has_provider", "KIND_ASR", "KIND_VOICEPRINT",
    "get_audio_store", "register_audio_source_fetcher",
    "audio_get_recording", "audio_list_recording_paths", "audio_mark_recording",
    "audio_set_recording_files", "audio_delete_recording",
    "IngestPayload", "IngestResult", "SegmentIn",
    "KIND_ASR_STREAM", "AsrEvent", "StreamingAsrProvider", "StreamingAsrSession",
    "TtsStream", "decode_stream_to_pcm16",
    "PreprocessError", "probe", "ensure_16k_mono_wav", "mean_volume_db",
    "detect_silences", "split_wav", "merge_to_wav",
    "register_vision_source", "unregister_vision_source", "vision_ingest_frame",
    "vision_look",
    "VisualSource", "CapturedFrame",
    "register_visual_provider", "unregister_visual_provider",
    "register_sound_provider", "unregister_sound_provider",
    "register_retrieval_provider", "unregister_retrieval_provider",
    "CapabilityProvider", "ProviderUnavailable", "CapabilityNotSupported",
    "RetrievalProvider", "run_coro_sync", "llm_provider_key",
    "SOURCE_CONFIG", "SOURCE_LLM", "SOURCE_ENV",
    "resolve_workspace_path",
    "download_media_to_uploads", "execute_send_action",
    "extract_document_text", "supported_doc_exts",
    "set_default_model", "get_active_llm_client", "get_llm_client_class",
    "get_llm_manager", "save_config_value",
    "get_session_llm_params", "canonical_efforts",
    "activate_tool_group_now", "notify_tool_set_changed",
    "tool_error", "error_from_exception", "ErrorCause",
]

F = TypeVar("F", bound=Callable[..., Any])


def coerce_bool_arg(value: Any, default: bool) -> bool:
    """将工具参数稳健转为 bool（兼容 LLM 误传字符串）。

    entities 层统一的布尔容错解析实现（mcp/filesystem 等工具共用，
    services/ 与 web/ 侧的对应副本由各自负责人收敛）。
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        return default
    return bool(value)


def tool(
    name: Optional[str] = None,
    description: Optional[str] = None,
    group: str = "default",
    tags: Optional[List[str]] = None,
    cacheable: bool = False,
    timeout: Optional[float] = None,
    check_fn: Optional[Callable[[], Any]] = None,
    allow_sleep: bool = False,
    sleep_brief: str = "",
    concurrency_safe: bool = False,
    risk: str = "",
) -> Callable[[F], F]:
    """装饰器：将函数注册为 LLM 可调用工具（注册到 EntityRegistry）。

    参数的名称、类型、是否必填从函数签名自动推导。

    Args:
        timeout: 工具执行超时时间（秒），默认使用全局默认（60秒）
        check_fn: 工具门控前置检查（返回 bool 或 Awaitable[bool]），
            检查不通过时工具不出现在 LLM schema 中
        allow_sleep: 是否允许沉睡（沉睡时仅展示 sleep_brief）
        sleep_brief: 沉睡状态下展示给 AI 的简短描述
        concurrency_safe: 是否可与其他安全工具并行执行（只读工具才应开启，
            默认 False — fail-closed 语义）。这是对整条执行链的断言：
            框架层（事件/审批/ContextVar 隔离）已保证并发安全，
            标注者只需确保工具体自身只读无共享写状态
        risk: 风险等级标记（如 CRITICAL），无显式权限规则覆盖时由审批
            元数据层兜底升级为 ask（见 agent.approval.rules.tool_meta_risk_rule）
    """
    def decorator(func: F) -> F:
        tool_name = name or func.__name__
        tool_desc = description or get_first_line(func.__doc__) or tool_name
        params = extract_tool_params(func)

        meta = {}
        if timeout is not None:
            meta["timeout"] = timeout
        if concurrency_safe:
            meta["concurrency_safe"] = True
        if risk:
            meta["risk"] = risk

        EntityRegistry.register_tool(
            name=tool_name,
            func=func,
            description=tool_desc,
            group=group,
            params=params,
            tags=tags or [],
            source="internal",
            meta=meta,
            check_fn=check_fn,
            allow_sleep=allow_sleep,
            sleep_brief=sleep_brief,
        )
        return func

    return decorator


def entity(group: str, description: str) -> None:
    """声明实体分组及其描述（立即注册），AI 将自动发现该实体。"""
    EntityRegistry.register_group(group, description)


# ------------------------------------------------------------------
# 延迟注册（适合需要运行时依赖注入的 core 层工具）
# ------------------------------------------------------------------

_deferred_registry: dict[str, list[dict]] = {}


def deferred_tool(
    name: Optional[str] = None,
    description: Optional[str] = None,
    group: str = "default",
    tags: Optional[List[str]] = None,
    source: str = "internal",
    timeout: Optional[float] = None,
    check_fn: Optional[Callable[[], Any]] = None,
    allow_sleep: bool = False,
    sleep_brief: str = "",
    concurrency_safe: bool = False,
    risk: str = "",
) -> Callable[[F], F]:
    """延迟注册装饰器：装饰时仅收集元数据，activate_group() 时批量注册。

    用于需要运行时依赖注入的工具（如 MemoryStore、Embedder 等）。
    参数名称、类型、描述从函数签名和 docstring 自动推导。

    Args:
        timeout: 工具执行超时时间（秒），默认使用全局默认（60秒）
        check_fn: 工具门控前置检查（返回 bool 或 Awaitable[bool]）
        allow_sleep: 是否允许沉睡（沉睡时仅展示 sleep_brief）
        sleep_brief: 沉睡状态下展示给 AI 的简短描述
        concurrency_safe: 是否可与其他安全工具并行执行（只读工具才应开启；
            框架层已保证并发安全，标注者只需确保工具体自身只读无共享写状态）
        risk: 风险等级标记（如 CRITICAL），无显式权限规则覆盖时由审批
            元数据层兜底升级为 ask（见 agent.approval.rules.tool_meta_risk_rule）
    """
    def decorator(func: F) -> F:
        tool_name = name or func.__name__
        tool_desc = description or get_first_line(func.__doc__) or tool_name
        params = extract_tool_params(func)

        meta = {}
        if timeout is not None:
            meta["timeout"] = timeout
        if concurrency_safe:
            meta["concurrency_safe"] = True
        if risk:
            meta["risk"] = risk

        _deferred_registry.setdefault(group, []).append({
            "name": tool_name, "func": func, "description": tool_desc,
            "group": group, "params": params, "tags": tags or [],
            "source": source, "meta": meta,
            "check_fn": check_fn, "allow_sleep": allow_sleep,
            "sleep_brief": sleep_brief,
        })
        return func
    return decorator


def activate_group(group: str, description: str = "") -> int:
    """将延迟注册的工具批量注册到 EntityRegistry，返回注册数量。

    通常在 register_xxx_tools() 中注入依赖后调用。
    """
    entries = _deferred_registry.pop(group, [])
    if not entries:
        return 0
    if description:
        EntityRegistry.register_group(group, description)
    for e in entries:
        EntityRegistry.register_tool(**e)
    return len(entries)


# ------------------------------------------------------------------
# LLM 桥接（延迟导入 agent.llm，供 entities 层使用）
# ------------------------------------------------------------------


def get_llm_manager() -> Any:
    """获取 LLMManager 实例（延迟导入 agent.llm）。"""
    from agent.llm import get_llm_manager as _get
    return _get()



def get_current_scope() -> str:
    """获取当前对话 scope（延迟导入 agent.mind，未绑定时返回 "_global"）。

    供 entities 层工具按 scope 隔离会话状态（如文件读取缓存）。
    在思维会话外调用（测试、心跳等）时返回全局作用域。
    """
    try:
        from agent.mind.tool_activation import ToolActivationManager
        return ToolActivationManager.current_scope()
    except Exception:
        return "_global"


def get_owner_scope() -> str:
    """获取后台任务的归属会话 scope（延迟导入 agent.mind，失败返回 "_global"）。

    委托链绑定的父会话 scope 优先（子代理内启动的后台任务归属发起会话，
    完成通知才能路由回对话），否则退回当前激活 scope——启动与查询同链。
    """
    try:
        from agent.mind.tool_activation import current_owner_scope
        return current_owner_scope()
    except Exception:
        return "_global"


def tool_group_rounds_left(group: str) -> int:
    """查询可沉睡分组在当前会话 scope 下的剩余激活轮数（0 = 沉睡中）。

    供 entities 层展示工具分组的真实可调用状态（如 list_entity_methods
    标注沉睡并引导 activate_tool_group），延迟导入 agent.mind。
    """
    try:
        from agent.mind.tool_activation import tool_activation
        return tool_activation.rounds_left(group)
    except Exception:
        return 0


def activate_tool_group_now(group: str, rounds: int = 0) -> int:
    """在当前会话 scope 程序化激活工具分组，返回生效轮数（0 = 激活失败）。

    供 entities 层在运行时装载工具后立即可用（版本号递增触发下一轮
    重组装与目录重建）；思维会话外或激活失败时返回 0，延迟导入 agent.mind。
    """
    try:
        from agent.mind.tool_activation import tool_activation
        return tool_activation.activate(group, rounds or None)
    except Exception:
        return 0


def notify_tool_set_changed() -> None:
    """通知工具集成员已变化（不改变激活状态），触发下一轮重组装与目录重建。

    供 entities 层在运行时注册/注销工具后调用（如插件装卸载），
    延迟导入 agent.mind，失败静默。
    """
    try:
        from agent.mind.tool_activation import tool_activation
        tool_activation.notify_tools_changed()
    except Exception:
        pass


def replay_tool_states() -> None:
    """回放持久化的工具启停/属性覆盖到当前注册表（实体热插入/热重载后调用）。

    幂等：仅对注册表中现存实体生效。
    """
    from agent.runtime.state_restore import apply_entity_states, apply_tool_overrides

    apply_tool_overrides()
    apply_entity_states()


def get_current_channel() -> str:
    """获取当前会话频道 adapter_key（未绑定时返回空串，延迟导入 agent.channel）。

    供 entities 层工具记录回复路由（如重启交接后把通知路由回原频道）。
    """
    try:
        from agent.channel.context import get_current_channel as _get
        return _get() or ""
    except Exception:
        return ""


def is_mind_busy() -> bool:
    """思维是否有进行中的回复/反思（延迟导入 agent.runtime）。

    供 entities 层在破坏性操作（如重启）前等待思维空闲；运行时未就绪
    或查询失败按不忙碌处理（fail-open，不阻塞操作流程）。
    """
    try:
        from agent.runtime.singleton import get_runtime
        rt = get_runtime()
        if rt is None:
            return False
        mind = rt.mind
        return bool(mind.is_reply or mind.is_reflecting)
    except Exception:
        return False


def get_background_registry() -> Any:
    """获取后台任务注册表（延迟导入 agent.runtime，未初始化返回 None）。

    供 entities 层工具登记/完成后台任务（如后台 shell 执行）。
    """
    try:
        from agent.runtime.singleton import require_runtime
        return require_runtime().mind.background_tasks
    except Exception:
        return None


def get_mind() -> Any:
    """获取当前思维核心实例（延迟导入 agent.runtime，未初始化返回 None）。

    供 entities 层工具只读查询思维侧状态（上下文窗口/用量等）；
    返回值是 Mind 实例，调用方自行 getattr 防御属性差异。
    """
    try:
        from agent.runtime.singleton import get_runtime
        rt = get_runtime()
        return rt.mind if rt is not None else None
    except Exception:
        return None


def push_notify(
    content: str,
    source: str,
    scope: str = "",
    channel: str = "",
    trigger: bool = True,
) -> bool:
    """向 AI 推送一条系统通知（[push:来源] 标签，区别于用户消息）。

    语义对齐手机弹窗：通知写入目标会话短期记忆，trigger=True 时
    唤醒一轮思维；对话进行中到达则由思维循环轮内并入当前上下文。

    Args:
        content: 通知正文。
        source: 推送来源标识（实体名，渲染为 [push:来源]）。
        scope: 目标会话 scope；缺省取当前会话 ContextVar，
            无法解析时写入全局短期记忆桶（不触发回复）。
        channel: 回复路由 adapter_key（缺省沿用该 scope 已登记的路由）。
        trigger: 是否立即唤醒一轮思维（False 则仅留待后续轮次看到）。

    Returns:
        推送是否受理（系统未初始化或内容为空时返回 False）。
    """
    if not content or not content.strip():
        return False
    try:
        if not scope:
            scope = get_current_scope()
            if scope == "_global":
                scope = ""
        from agent.runtime.singleton import require_runtime
        return bool(require_runtime().mind.push_hub.push(
            scope, source, content, channel=channel, trigger=trigger))
    except Exception:
        return False


def register_entity_llm_hook(
    name: str,
    event: str,
    handler: Callable[..., Any],
    **spec_overrides: Any,
) -> bool:
    """实体注册一个 LLM 钩子（达到事件条件即并行拉起一段 LLM 工作，不卡主思考）。

    经 LLM 钩子面（agent/hooks_llm）注册——同一事件可挂多个钩子并发执行，
    各自独立的并发/频控/防递归治理；钩子 LLM 产出经后台任务注册表路由，
    不阻塞、不打扰主对话。

    Args:
        name: 全局唯一钩子名（建议带实体前缀，如 weather_analyze）。
        event: 生命周期事件（after_reply/context_pressure/delegation_resolved/llm_end）。
            llm_end 为高频事件（每次 LLM 调用后触发），强制最小冷却 20s，
            声明更小值也会被钳到下限。
        handler: 执行体，签名 async (ctx: HookContext) -> Optional[str]；
            ctx.messages 为按 context 档位构建的上下文快照，ctx.payload 为事件数据。
        spec_overrides: LLMHookSpec 治理字段直转（context/tool_tags/
            allow_output_tools/max_iterations/model/max_concurrent/
            cooldown_seconds/debounce_seconds/priority/description/when/
            owner），未列键由 spec 构造器拒绝（注册失败返回 False）。
            context 接受字符串档位（none/lean/transcript），tool_tags 接受 list；
            owner 缺省取调用方实体模块名（热拔卸载按 owner 批量清理）。

    Returns:
        注册是否成功（事件非法/钩子面不可用/参数非法返回 False）。
    """
    try:
        from agent.hooks_llm import HookContextMode, HookRegistry, LLMHookSpec
        from agent.hooks_llm.spec import (
            HOOK_EVENT_LLM_END,
            LLM_END_MIN_COOLDOWN_SECONDS,
        )

        owner = spec_overrides.pop("owner", "")
        if not owner:
            # owner 推断：handler 定义在 entities.<name>[.子模块] 时取 <name>；
            # 非 entities 模块退化为 entity
            module = getattr(handler, "__module__", "") or ""
            parts = module.split(".")
            owner = parts[1] if len(parts) >= 2 and parts[0] == "entities" and parts[1] else "entity"
        context = spec_overrides.pop("context", HookContextMode.NONE)
        if isinstance(context, str):
            context = HookContextMode(context)
        cooldown = max(0.0, float(spec_overrides.pop("cooldown_seconds", 0.0)))
        if event == HOOK_EVENT_LLM_END:
            cooldown = max(cooldown, LLM_END_MIN_COOLDOWN_SECONDS)
        spec = LLMHookSpec(
            name=name, event=event, handler=handler,
            context=context,
            tool_tags=tuple(spec_overrides.pop("tool_tags", ())),
            max_concurrent=max(1, int(spec_overrides.pop("max_concurrent", 1))),
            cooldown_seconds=cooldown,
            debounce_seconds=max(0.0, float(spec_overrides.pop("debounce_seconds", 0.0))),
            owner=str(owner),
            source="entity",
            **spec_overrides,
        )
        HookRegistry.register(spec)
        return True
    except Exception:
        return False


async def add_persistent_reminder(
    note: str, run_at_ts: float, scope: str = "", channel: str = "",
) -> str:
    """写入一条持久化定时提醒（entities 桥接 mind 调度器），返回提醒 id。

    到期由心跳触发一轮完整 REPLY（重启不丢失），供日历事件等实体复用；
    请勿绕开此桥直写 reminders.json。无会话上下文时 scope 不可路由，
    add_reminder 拒绝写入——如实记日志并返回空串（调用方降级为不提醒）。
    """
    if not scope:
        scope = get_current_scope()
        if scope == "_global":
            scope = get_owner_scope()
    try:
        from agent.mind.tools.scheduler import add_reminder
        reminder = await add_reminder(note, run_at_ts, scope, channel)
        return str(reminder["id"])
    except Exception as exc:
        log(f"持久化提醒创建失败（scope 不可路由: {scope!r}）: {exc}", "WARNING")
        return ""


async def cancel_persistent_reminder(reminder_id: str) -> bool:
    """按 id 取消一条未触发的持久化提醒（add_persistent_reminder 配对）。"""
    if not reminder_id:
        return False
    try:
        from agent.mind.tools.scheduler import remove_reminder
        return await remove_reminder(reminder_id)
    except Exception:
        return False


def load_image_from_path(path: str) -> Any:
    """从本地路径加载图片为 base64 ImageContent。"""
    from agent.llm.image_utils import load_image_from_path as _load
    return _load(path)


def optimize_image_for_vision(image: Any) -> Any:
    """对发送给视觉模型的图片做分辨率/体积优化（幂等）。"""
    from agent.llm.image_utils import optimize_for_vision
    return optimize_for_vision(image)


def is_content_policy_error(exc: BaseException) -> bool:
    """判断异常是否为内容审核拒绝（确定性，同模型重试无意义）。"""
    from agent.llm.resilience import ErrorCategory, classify_llm_error
    return classify_llm_error(exc).category is ErrorCategory.CONTENT_POLICY


def download_image_to_base64(url: str) -> Any:
    """下载 URL 图片并转为 base64 ImageContent。"""
    from agent.llm.image_utils import download_image_to_base64 as _dl
    return _dl(url)


def get_image_content_class() -> type:
    """获取 ImageContent 类型。"""
    from agent.llm.types import ImageContent
    return ImageContent


def is_video_path(path: str) -> bool:
    """判断路径或 URL 是否指向视频文件。"""
    from agent.llm.image_utils import is_video_path as _is_video
    return _is_video(path)


def load_video_from_path(path: str) -> Any:
    """从本地路径加载视频为 base64 VideoContent。"""
    from agent.llm.image_utils import load_video_from_path as _load
    return _load(path)


def download_video_to_base64(url: str) -> Any:
    """下载 URL 视频并转为 base64 VideoContent。"""
    from agent.llm.image_utils import download_video_to_base64 as _dl
    return _dl(url)


def get_video_content_class() -> type:
    """获取 VideoContent 类型。"""
    from agent.llm.types import VideoContent
    return VideoContent


def get_model_type_enum() -> Any:
    """获取 ModelType 枚举。"""
    from agent.llm.llm_manager import ModelType
    return ModelType


# ------------------------------------------------------------------
# Embedding 桥接（延迟导入 agent.memory.embedding）
# ------------------------------------------------------------------


def get_embedder(purpose: str = "text") -> Any:
    """获取共享 Embedder（按用途域隔离向量空间：text=记忆/对话，vision=贴纸/图片）。"""
    from agent.memory.embedding import get_embedder as _get
    return _get(purpose)


def wake_embedding_worker() -> None:
    """唤醒后台 embedding 回填 worker（有新 backlog 任务待消化时调用）。"""
    from agent.memory.embedding import wake_embedding_worker as _wake
    _wake()


def register_embedding_backlog(name: str, handler: Any) -> None:
    """注册后台 embedding 回填任务（EmbeddingWorker 启动后统一消化）。"""
    from agent.memory.embedding import register_embedding_backlog as _reg
    _reg(name, handler)


from agent.audio.providers import KIND_ASR, KIND_VOICEPRINT  # noqa: E402  # 桥层再导出
from agent.audio.schemas import (  # noqa: E402  # 桥层再导出（外部音源推送契约）
    IngestPayload,
    IngestResult,
    SegmentIn,
)
from agent.audio.streaming import (  # noqa: E402  # 桥层再导出（流式 ASR 组件契约）
    KIND_ASR_STREAM,
    AsrEvent,
    StreamingAsrProvider,
    StreamingAsrSession,
)
from agent.tts.decode import decode_stream_to_pcm16  # noqa: E402  # 桥层再导出
from agent.tts.providers import TtsStream  # noqa: E402  # 桥层再导出（流式 TTS 组件契约）


def extract_document_text(path: str) -> str:
    """提取文档纯文本（PDF/Word/Excel/PPT/文本），无可用文本抛 ValueError。"""
    from pathlib import Path

    from agent.memory.doc_extract import extract_document_text as _extract
    return _extract(Path(path))


def supported_doc_exts() -> tuple:
    """可解析的文档扩展名集合（含点号小写）。"""
    from agent.memory.doc_extract import SUPPORTED_DOC_EXTS
    return tuple(sorted(SUPPORTED_DOC_EXTS))


def get_audio_store() -> Any:
    """核心音频库单例（声纹身份/片段/录制单元统一存储）。"""
    from agent.audio import get_audio_store as _get
    return _get()


async def audio_transcribe(audio_path: str, source_time: str = "") -> list:
    """经核心音频服务转写音频（ASR 提供者优先级链）。"""
    from agent.audio import get_audio_service
    return await get_audio_service().transcribe(audio_path, source_time=source_time)


async def audio_speaker_embed(audio_path: str) -> Any:
    """经核心音频服务提取声纹向量（无可用提供者返回 None）。"""
    from agent.audio import get_audio_service
    return await get_audio_service().speaker_embed(audio_path)


async def audio_ingest_payload(payload: IngestPayload) -> IngestResult:
    """解析结果经核心入库管线存档（噪音过滤 → 声纹识别 → 落库）。"""
    from agent.audio import get_audio_service
    return await get_audio_service().ingest_payload(payload)


async def audio_has_provider(kind: str) -> bool:
    """该类别是否有可用音频提供者（工具/路由的 check 门控用）。"""
    from agent.audio import get_audio_registry
    return await get_audio_registry().resolve(kind) is not None


def register_audio_provider(provider: Any) -> None:
    """注册音频能力提供者（ASR 转写 / 声纹提取组件接入核心层）。

    provider 需满足 agent.audio.providers 的协议（name/kind/priority +
    check_available + transcribe/embed），注册后进入相应类别的优先级链。
    """
    from agent.audio.providers import get_audio_registry
    get_audio_registry().register(provider)


def register_audio_source_fetcher(name: str, fetcher: Any, priority: int = 50) -> None:
    """注册音源取回器（回听/分析定位原始音频：远程下载/挂载映射等组件）。

    fetcher 契约：async (source_path) -> (local_path, is_temp)；
    is_temp=True 表示临时文件（调用方用后负责删除）。
    """
    from agent.audio.source_fetch import register_source_fetcher
    register_source_fetcher(name, fetcher, priority=priority)


def register_tts_provider(provider: Any) -> None:
    """注册流式 TTS 提供者（语音合成组件接入核心层）。

    provider 需满足 agent.tts.providers 的协议（name/priority +
    check_available + stream_synthesize），注册后进入优先级链；
    运行时失败按链降级（首字节前无缝切换，首字节后截断该句）。
    """
    from agent.tts.providers import get_tts_registry
    get_tts_registry().register(provider)


# 音色解析桥（实体合成入口与核心/AI/Web 共用同一预设决策链）
def default_tts_voice() -> str:
    """全局默认音色 ID（默认预设指派解析；未指派为空串）。"""
    from agent.tts.voice import default_voice
    return default_voice()


def realtime_tts_voice() -> str:
    """实时通话音色 ID（通话预设，未指派跟随默认）。"""
    from agent.tts.voice import realtime_voice
    return realtime_voice()


# 录制单元登记桥（音源同步组件的增量依据与合并清单读写）
async def audio_get_recording(path: str) -> Any:
    return await get_audio_store().get_recording(path)


async def audio_list_recording_paths() -> list:
    return await get_audio_store().list_recording_paths()


async def audio_mark_recording(path: str, **kwargs: Any) -> None:
    await get_audio_store().mark_recording(path, **kwargs)


async def audio_set_recording_files(path: str, files: list) -> None:
    await get_audio_store().set_recording_files(path, files)


async def audio_delete_recording(path: str) -> dict:
    return await get_audio_store().delete_recording(path)


# ffmpeg 音频预处理桥（核心实现，组件共用）
from agent.audio.ffmpeg import (  # noqa: E402
    PreprocessError,
    detect_silences,
    ensure_16k_mono_wav,
    mean_volume_db,
    merge_to_wav,
    probe,
    split_wav,
)

# ------------------------------------------------------------------
# 视觉桥接（延迟导入 agent.vision）
# ------------------------------------------------------------------
from agent.vision.capture import CapturedFrame  # noqa: E402  # 桥层再导出
from agent.vision.framework import VisualSource  # noqa: E402  # 桥层再导出


def register_vision_source(source: VisualSource) -> None:
    """注册视觉源组件（屏幕/摄像头/外部画面桥等接入核心视觉框架）。

    source 需声明 key/display_name，并按类型声明 poll_interval（>0 轮询型，
    watcher 定频采帧）与 can_capture（即时取帧能力）；外部推送型源帧经
    vision_ingest_frame 或 POST /api/vision/push 汇入同一缓冲。
    """
    from agent.vision.framework import register_source
    register_source(source)


def unregister_vision_source(key: str) -> None:
    """注销视觉源组件（实体热拔除时调用）。"""
    from agent.vision.framework import unregister_source
    unregister_source(key)


async def vision_ingest_frame(
    path: str,
    source: str,
    *,
    width: int = 0,
    height: int = 0,
    captured_at: Optional[float] = None,
) -> tuple:
    """外部帧汇入视觉缓冲统一入口（判变 + 注入轨迹），返回 (帧, 是否内容级变化)。"""
    from agent.vision.buffer import get_vision_buffer
    return await get_vision_buffer().ingest(
        path, source, width=width, height=height, captured_at=captured_at)


async def vision_look(source: str = "screen") -> str:
    """立即查看指定视觉源的画面（多模态结果契约：顶层 _multimodal+images）。"""
    from agent.vision.tools import vision_look as _vision_look
    return await _vision_look(source=source)


# ------------------------------------------------------------------
# 能力组件桥接（视觉生成 / 声音合成 / 检索提供者接入核心路由）
# ------------------------------------------------------------------
from agent.capabilities import (  # noqa: E402  # 桥层再导出（组件实现契约）
    CapabilityNotSupported,
    CapabilityProvider,
    ProviderUnavailable,
)


def register_visual_provider(provider: Any) -> None:
    """注册视觉能力提供者（图片/视频理解、图像生成/编辑、视频生成组件）。

    provider 需满足 agent.capabilities.CapabilityProvider 协议
    （name/capabilities + is_configured + run），能力名:
    understand / image_gen / image_edit / video。
    """
    from agent.vision.capabilities import get_visual_router
    get_visual_router().register(provider)


def unregister_visual_provider(name: str) -> None:
    """注销视觉能力提供者（组件热拔除时调用）。"""
    from agent.vision.capabilities import get_visual_router
    get_visual_router().unregister(name)


def register_sound_provider(provider: Any) -> None:
    """注册声音能力提供者（一次性语音合成/音色管理/音乐生成组件）。

    provider 需满足 agent.capabilities.CapabilityProvider 协议，能力名:
    tts / voice_mgmt / music。
    """
    from agent.audio.capabilities import get_sound_router
    get_sound_router().register(provider)


def unregister_sound_provider(name: str) -> None:
    """注销声音能力提供者（组件热拔除时调用）。"""
    from agent.audio.capabilities import get_sound_router
    get_sound_router().unregister(name)


def register_retrieval_provider(provider: Any) -> None:
    """注册检索提供者（联网检索/网页读取/仓库文档组件）。

    provider 需继承 agent.retrieval.providers.base.Provider 并按需实现
    SearchCap / ReaderCap / RepoCap 协议方法。
    """
    from agent.retrieval import providers
    providers.register(provider)
def unregister_retrieval_provider(name: str) -> None:
    """注销检索提供者（组件热拔除时调用）。"""
    from agent.retrieval import providers
    providers.unregister(name)


# 检索组件实现契约（桥层再导出）：Provider 基类 + 凭据来源常量 + 凭据回退辅助
from agent.retrieval.providers.base import (  # noqa: E402
    SOURCE_CONFIG,
    SOURCE_ENV,
    SOURCE_LLM,
    llm_provider_key,
    run_coro_sync,
)
from agent.retrieval.providers.base import (
    Provider as RetrievalProvider,
)


def resolve_workspace_path(path: str) -> str:
    """解析工具入参路径为绝对路径（工作区沙箱校验，越界抛 ValueError）。"""
    from agent.utils.workspace import resolve_workspace_path as _resolve
    return _resolve(path)


# ------------------------------------------------------------------
# 频道桥接（延迟导入 agent.channel）
# ------------------------------------------------------------------


async def download_media_to_uploads(
    url: str,
    media_type: str,
    save_name: str = "",
    max_size: Optional[int] = None,
) -> str:
    """下载 URL 媒体到 uploads 对应子目录，返回本地路径（失败返回空串）。

    Args:
        url: 远程文件地址（http/https）
        media_type: 媒体类型（SegmentType 值：image/voice/audio/video/file 等），
            决定落盘子目录与默认扩展名
        save_name: 期望的文件名（仅取 basename，附加唯一前缀防冲突）
        max_size: 允许的最大字节数（None = 全局默认上限）
    """
    from agent.channel.media import MAX_DOWNLOAD_SIZE, download_to_uploads
    from agent.channel.schemas import SegmentType
    return await download_to_uploads(
        url, SegmentType(media_type), save_name=save_name,
        max_size=max_size if max_size is not None else MAX_DOWNLOAD_SIZE,
    )


async def execute_send_action(
    *,
    channel_id: str,
    target_id: str,
    operation: str,
    invoke: Callable[[Any, str, str], Awaitable[Any]],
    enrich: Optional[Callable[[dict, bool], None]] = None,
    success_suffix: str = "",
    outbound_preview: str = "",
) -> str:
    """经频道统一发送管道执行发送：校验 -> 目标解析 -> 出站哨兵 -> 调用频道 -> 结果解析 -> 日志。

    Args:
        channel_id: 目标频道 ID
        target_id: 目标会话 ID（uid 或群号）
        operation: 操作名（日志与错误归因用，如 "表情包"）
        invoke: 真实发送回调 invoke(ch, resolved_target_id, channel_type)
        enrich: 结果补充回调 enrich(parsed, ok)，向返回 JSON 附加调用方字段
        success_suffix: 成功日志的附加信息
        outbound_preview: 发送内容短摘要（出站哨兵近期窗口判定与拒绝回执展示）

    Returns:
        结果 JSON 字符串（含 success/channel_id/target_id 及 enrich 附加字段）。
    """
    from agent.channel.output_tools import execute_send_action as _exec
    return await _exec(
        channel_id=channel_id,
        target_id=target_id,
        operation=operation,
        invoke=invoke,
        enrich=enrich,
        success_suffix=success_suffix,
        outbound_preview=outbound_preview,
    )


# ------------------------------------------------------------------
# 模型管理桥接（延迟导入 agent.llm / agent.runtime）
# ------------------------------------------------------------------


def save_config_value(key: str, value: Any) -> None:
    """统一配置写入：MindConfig 字段路由 save_mind_config（双轨同步 + 实时生效），
    其余走 ConfigManager.set + save（变更监听驱动消费方热更）。

    Web 的 PUT /config/meta 与本函数是同一写纪律的两个入口，AI 配置工具应走这里。
    """
    from core.config import ConfigManager

    try:
        from agent.config import MIND_CONFIG_FIELDS
        mind_fields = frozenset(MIND_CONFIG_FIELDS)
    except Exception:
        mind_fields = frozenset()
    if key in mind_fields:
        from agent.config import get_config_provider
        get_config_provider().save_mind_config(**{key: value})
        return
    ConfigManager.set(key, value)
    ConfigManager.save()


def set_default_model(model_id: str) -> bool:
    """热切换默认对话模型：持久化配置并同步运行时激活客户端。"""
    mgr = get_llm_manager()
    if not mgr.set_default(model_id):
        return False
    from agent.runtime.singleton import get_runtime
    rt = get_runtime()
    if rt is not None:
        rt.switch_llm(mgr.get_default())
    return True


def get_active_llm_client() -> Any:
    """获取运行时当前激活的 LLM 客户端（未初始化返回 None）。"""
    from agent.runtime.singleton import get_runtime
    rt = get_runtime()
    return getattr(rt, "llm", None) if rt is not None else None


def get_llm_client_class() -> "type[LLMClient]":
    """获取 LLMClient 类型（isinstance 判断用）。"""
    from agent.llm.llm_client import LLMClient
    return LLMClient


def get_session_llm_params() -> Dict[str, Any]:
    """获取会话级临时 LLM 参数覆盖（可变 dict，直接读写；重启失效）。"""
    from agent.runtime.singleton import require_runtime
    return require_runtime().mind.session_llm_params


def canonical_efforts() -> List[str]:
    """获取思考等级规范词汇表（update_model_config 等校验用）。"""
    from agent.llm.reasoning import CANONICAL_EFFORTS
    return list(CANONICAL_EFFORTS)


# ------------------------------------------------------------------
# 上下文提供者（实体向 PFC volatile 层注入实时数据）
# ------------------------------------------------------------------


def get_provider_registry() -> Any:
    """获取 ContextProviderRegistry（延迟导入 core.context_provider）。"""
    from core.context_provider import ContextProviderRegistry
    return ContextProviderRegistry


def context_provider(
    name: Optional[str] = None,
    priority: int = 50,
    max_tokens: int = 500,
    scope: Optional[str] = None,
    group: Optional[str] = None,
    inject_key: Optional[str] = None,
) -> Callable:
    """装饰器：将类或函数注册为上下文提供者。

    PFC 每轮构建 volatile 层时拉取所有 provider 的最新快照，
    实体自行管理更新节奏（RunTimeline），PFC 只做被动拉取。

    类模式（有生命周期）::

        @context_provider(name="health", priority=10, max_tokens=200, group="system")
        class HealthWatcher:
            async def on_start(self):
                self._task = asyncio.create_task(self._collect_loop())

            async def provide(self, scope: str) -> Optional[ProviderSnapshot]:
                return self._snapshot  # 零 I/O，只读快照

            async def on_tick(self):       # 可选：心跳 tick 时触发
                ...

            async def on_stop(self):       # 可选：shutdown 时触发
                self._task.cancel()

    函数模式（无状态）::

        @context_provider(name="weather", priority=20)
        async def weather(scope: str) -> Optional[str]:
            return f"[天气] {await fetch_weather()}"

    Args:
        name: 提供者唯一标识（默认取类名/函数名）。
        priority: 注入优先级（越小越靠前，预算超限时大值先被截断）。语义为变动率
            排序：快照越静态越小（靠前），含时间/秒计数等逐轮变化内容的实时
            快照越大（靠尾部动态区末尾）。段位：10-19 状态级 / 20-29 摘要级 /
            30-39 会话操作态势 / 40+ 实时快照（详见 core.context_provider.ProviderMeta）。
        max_tokens: 静态预估上限（Web 展示 + 预算告警参考）。
        scope: 作用域过滤。None=全局；"webui:*"=前缀匹配；"webui:u123"=精确匹配。
        group: 所属工具分组（如 "ssh"）。声明后随实体启停联动：分组内全部
            工具被禁用时停止采集与注入，重新启用自动恢复；None 表示全局常驻。
            属于某个实体分组的 provider 应始终声明，否则关闭实体无法停止其注入。
        inject_key: 注入开关配置键（约定 ``<group>_context_inject``）。会产出
            注入内容的 provider 必须声明——配置为 False 时框架停止采集与注入
            （在 _is_active 层拦截，provide 内无需再手工检查）。声明后若该键
            尚未注册，装饰器自动以默认值 True 兜底注册进 ``entity/<group>``
            配置组（实体自行 register_configs 声明的更丰富定义优先，不被覆盖）；
            频道等不走 entity 配置组的 provider 直接传 ProviderMeta.inject_key。
    """
    from core.context_provider import ContextProviderRegistry, ProviderMeta

    def decorator(cls_or_func: Any) -> Any:
        provider_name = name or getattr(cls_or_func, "__name__", str(cls_or_func))
        provider_desc = getattr(cls_or_func, "__doc__", "") or ""

        if inject_key and group:
            # 注入开关兜底注册（实体未自行声明时），配置中心/实体配置 tab 自动出现
            from core.config import ConfigRegistry, register_configs_safe
            if ConfigRegistry.get_item(inject_key) is None:
                register_configs_safe({
                    f"entity/{group}": {
                        inject_key: {
                            "description": f"是否向 AI 上下文注入{provider_desc.strip().rstrip('。') or provider_name}",
                            "default": True,
                        },
                    },
                })

        if isinstance(cls_or_func, type):
            # 类模式：实例化后注册
            instance = cls_or_func()
            meta = ProviderMeta(
                name=provider_name,
                priority=priority,
                max_tokens=max_tokens,
                scope_filter=scope,
                group=group,
                inject_key=inject_key,
                instance=instance,
                description=provider_desc,
            )
            ContextProviderRegistry.register(meta)
            return cls_or_func
        else:
            # 函数模式
            meta = ProviderMeta(
                name=provider_name,
                priority=priority,
                max_tokens=max_tokens,
                scope_filter=scope,
                group=group,
                inject_key=inject_key,
                provide_fn=cls_or_func,
                description=provider_desc,
            )
            ContextProviderRegistry.register(meta)
            return cls_or_func

    return decorator


# ------------------------------------------------------------------
# 实体清单与配置（实体 APP 化）
# ------------------------------------------------------------------


def entity_manifest(
    display_name: str = "",
    icon: str = "box",
    description: str = "",
    version: str = "1.0.0",
    order: int = 50,
    nav: Optional[Dict[str, Any]] = None,
    group: Optional[str] = None,
) -> None:
    """声明实体展示清单（前端详情页 + 实体列表页 + 侧边栏导航使用）。

    在 entity() 之后调用，为当前分组注册展示元数据::

        entity("web", "网络工具")
        entity_manifest(
            display_name="网络工具",
            icon="globe",
            description="网页搜索、内容提取、URL 抓取",
            order=20,
            nav={"path": "/web", "label": "web", "nav_group": "group_ability"},
        )

    Args:
        display_name: 前端展示名称（i18n 由前端按 group key 翻译）。
        icon: lucide 图标名（如 globe / image / terminal）。
        description: 实体功能描述。
        version: 语义化版本号。
        order: 工具目录排序权重（越小越靠前，默认 50）。分段约定见
            core/entity.py 默认权重表（0-9 输出思维 / 10-19 记忆 / 20-29 规划执行 /
            30-49 能力感知 / 50-59 模型运维 / 60-69 管理集成 / 70-79 界面会话）。
        nav: 侧边栏导航声明（可选），字段：
            - path: 前端路由路径（默认 "/<group>"）
            - label: i18n key（默认 group 名）
            - nav_group: 导航分组（默认 "group_ability"）
        group: 目标分组名（必传）。历史上缺省时按 list_groups()[-1] 推导，
            但 _groups 仅收录已有工具的分组，推导结果不可靠（manifest 串组覆盖），
            因此缺省时拒绝注册并告警。
    """
    from core.entity import EntityRegistry
    from core.log import log
    if group is None:
        log("entity_manifest 缺少 group 参数，已跳过注册（manifest 推导机制已移除）", "WARNING")
        return
    manifest: Dict[str, Any] = {
        "display_name": display_name,
        "icon": icon,
        "description": description,
        "version": version,
        "order": order,
    }
    if nav is not None:
        manifest["nav"] = nav
    EntityRegistry.register_group_manifest(group, manifest)
    EntityRegistry.register_group_order(group, order)


def entity_config(
    configs: Dict[str, Dict[str, Dict[str, Any]]],
    config_dir: str = "",
) -> None:
    """注册实体专属配置并管理 config.json 生命周期。

    配置存储于实体目录下的 config.json（运行时，gitignored），
    不存在时从 config.example.json 复制并填入默认值。

    configs 格式与 core.config.register_configs 一致，分组名约定为 ``entity/<实体组名>``::

        entity_config({
            "entity/ssh": {
                "ssh_ai_enabled": {
                    "description": "允许 AI 调用 SSH 工具",
                    "default": True,
                },
            },
        })

    Args:
        configs: 配置 schema 字典 {group: {key: {description, default, ...}}}。
        config_dir: 配置文件所在目录（默认自动推导为调用方所在目录）。
    """
    import os

    from core.config import ConfigManager, register_configs_safe

    # 注册到全局 ConfigRegistry（schema 层面）
    register_configs_safe(configs)

    # 推导配置目录
    if not config_dir:
        frame = inspect.stack()[1]
        caller_file = frame.filename
        config_dir = os.path.dirname(os.path.abspath(caller_file))

    config_path = os.path.join(config_dir, "config.json")
    example_path = os.path.join(config_dir, "config.example.json")

    # 加载 config.json（不存在则从 example 或默认值创建）
    values: Dict[str, Any] = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                values = json.load(f)
        except Exception:
            values = {}
    elif os.path.exists(example_path):
        try:
            with open(example_path, "r", encoding="utf-8") as f:
                values = json.load(f)
        except Exception:
            values = {}

    # 将 config.json 中的值写入 ConfigManager（覆盖默认值）
    for group_items in configs.values():
        for key, item in group_items.items():
            if key in values:
                ConfigManager.set(key, values[key])
            elif "default" in item:
                ConfigManager.set(key, item["default"])

