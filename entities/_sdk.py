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

from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional, TypeVar

from core.entity import EntityRegistry
from core.tool_errors import ErrorCause, error_from_exception, tool_error
from core.tool_schema import extract_tool_params, get_first_line

if TYPE_CHECKING:
    from agent.llm.llm_client import LLMClient as LLMClient
    from agent.llm.llm_manager import LLMManager as LLMManager

__all__ = [
    "tool", "deferred_tool", "entity", "activate_group",
    "entity_manifest", "entity_config", "context_provider",
    "push_notify",
    "get_embedder", "wake_embedding_worker", "register_embedding_backlog",
    "download_media_to_uploads", "execute_send_action",
    "set_default_model", "get_active_llm_client", "get_llm_client_class",
    "get_session_llm_params", "canonical_efforts",
    "tool_error", "error_from_exception", "ErrorCause",
]

# 兼容别名：tests/entities/test_sdk_extract_params.py 仍引用该私有名，暂不能删除
_extract_params = extract_tool_params

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
) -> Callable[[F], F]:
    """装饰器：将函数注册为 LLM 可调用工具（注册到 EntityRegistry）。

    参数的名称、类型、是否必填从函数签名自动推导。

    Args:
        timeout: 工具执行超时时间（秒），默认使用全局配置（30秒）
        check_fn: 工具门控前置检查（返回 bool 或 Awaitable[bool]），
            检查不通过时工具不出现在 LLM schema 中
        allow_sleep: 是否允许沉睡（沉睡时仅展示 sleep_brief）
        sleep_brief: 沉睡状态下展示给 AI 的简短描述
        concurrency_safe: 是否可与其他安全工具并行执行（只读工具才应开启，
            默认 False — 与 Claude Code isConcurrencySafe 一致的 fail-closed 语义）
    """
    def decorator(func: F) -> F:
        tool_name = name or func.__name__
        tool_desc = description or get_first_line(func.__doc__) or tool_name
        params = _extract_params(func)

        meta = {}
        if timeout is not None:
            meta["timeout"] = timeout
        if concurrency_safe:
            meta["concurrency_safe"] = True

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
) -> Callable[[F], F]:
    """延迟注册装饰器：装饰时仅收集元数据，activate_group() 时批量注册。

    用于需要运行时依赖注入的工具（如 MemoryStore、Embedder 等）。
    参数名称、类型、描述从函数签名和 docstring 自动推导。

    Args:
        timeout: 工具执行超时时间（秒），默认使用全局配置（30秒）
        check_fn: 工具门控前置检查（返回 bool 或 Awaitable[bool]）
        allow_sleep: 是否允许沉睡（沉睡时仅展示 sleep_brief）
        sleep_brief: 沉睡状态下展示给 AI 的简短描述
        concurrency_safe: 是否可与其他安全工具并行执行（只读工具才应开启）
    """
    def decorator(func: F) -> F:
        tool_name = name or func.__name__
        tool_desc = description or get_first_line(func.__doc__) or tool_name
        params = _extract_params(func)

        meta = {}
        if timeout is not None:
            meta["timeout"] = timeout
        if concurrency_safe:
            meta["concurrency_safe"] = True

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


def get_background_registry() -> Any:
    """获取后台任务注册表（延迟导入 agent.runtime，未初始化返回 None）。

    供 entities 层工具登记/完成后台任务（如后台 shell 执行）。
    """
    try:
        from agent.runtime.singleton import require_runtime
        return require_runtime().mind.background_tasks
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
) -> str:
    """经频道统一发送管道执行发送：校验 -> 目标解析 -> 调用频道 -> 结果解析 -> 日志。

    Args:
        channel_id: 目标频道 ID
        target_id: 目标会话 ID（uid 或群号）
        operation: 操作名（日志与错误归因用，如 "表情包"）
        invoke: 真实发送回调 invoke(ch, resolved_target_id, channel_type)
        enrich: 结果补充回调 enrich(parsed, ok)，向返回 JSON 附加调用方字段
        success_suffix: 成功日志的附加信息

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
    )


# ------------------------------------------------------------------
# 模型管理桥接（延迟导入 agent.llm / agent.runtime）
# ------------------------------------------------------------------


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
        priority: 注入优先级（越小越优先，预算超限时低优先级先被截断）。
        max_tokens: 静态预估上限（Web 展示 + 预算告警参考）。
        scope: 作用域过滤。None=全局；"webui:*"=前缀匹配；"webui:u123"=精确匹配。
        group: 所属工具分组（如 "ssh"）。声明后随实体启停联动：分组内全部
            工具被禁用时停止采集与注入，重新启用自动恢复；None 表示全局常驻。
            属于某个实体分组的 provider 应始终声明，否则关闭实体无法停止其注入。
    """
    from core.context_provider import ContextProviderRegistry, ProviderMeta

    def decorator(cls_or_func: Any) -> Any:
        provider_name = name or getattr(cls_or_func, "__name__", str(cls_or_func))

        if isinstance(cls_or_func, type):
            # 类模式：实例化后注册
            instance = cls_or_func()
            meta = ProviderMeta(
                name=provider_name,
                priority=priority,
                max_tokens=max_tokens,
                scope_filter=scope,
                group=group,
                instance=instance,
                description=getattr(cls_or_func, "__doc__", "") or "",
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
                provide_fn=cls_or_func,
                description=getattr(cls_or_func, "__doc__", "") or "",
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
        order: 工具目录排序权重（越小越靠前，默认 50）。
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
    import inspect
    import json
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

