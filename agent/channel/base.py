"""BaseChannel — 频道抽象基类。

借鉴 nekro-agent 的 BaseAdapter + BaseAdapterConfig + AdapterMetadata 设计，
提供统一的频道抽象。

核心特性：
1. **元数据声明**（ChannelMetadata）：让频道自描述，便于 WebUI 与文档生成
2. **强类型配置**（ChannelConfig + 子类继承）：pydantic 校验，统一注册到 ConfigManager
3. **统一发送入口**（forward_message）：所有发送动作都走 SendRequest，
   子类只需实现 1 个抽象方法
4. **健康探针**（health_check）：看门狗主动检测频道健康状态
5. **命令系统钩子**（detect_command / execute_command）：内建命令路由，
   由 InputPipeline 中的 CommandProcessor 统一驱动
6. **能力 / 信息查询**（get_self_info / get_user_info / get_channel_info）：
   统一的"我是谁 / 你是谁 / 这是哪"查询协议
7. **路由器挂载**（get_router）：HTTP 类频道（http_api / webui / webhook）统一
   从此处暴露 FastAPI Router

子类约定（最小实现）：
    class MyChannel(BaseChannel[MyConfig]):
        channel_id = "my"
        display_name = "我的频道"
        capabilities = {ChannelCapability.SEND_TEXT}
        metadata = ChannelMetadata(name="My", description="...")

        _Configs = MyConfig

        async def start(self): ...
        async def stop(self): ...
        async def forward_message(self, request): ...
        async def get_self_info(self): ...
        async def get_channel_info(self, channel_id): ...
        async def health_check(self): ...
"""

from __future__ import annotations

import asyncio
import json
import time
from abc import ABC, abstractmethod
from typing import (
    Any,
    Awaitable,
    Callable,
    ClassVar,
    Dict,
    Generic,
    List,
    Literal,
    Optional,
    Set,
    Tuple,
    Type,
    TypeVar,
)

from pydantic import BaseModel, Field

from core.entity import BaseEntity, EntityType
from core.log import log

from .channel_types import ChannelCapability, ChannelStatus, _err, _ok
from .schemas import (
    AdapterChannel,
    ChannelInfo,
    ChannelType,
    ChannelUser,
    CommandResponse,
    HealthStatus,
    SendRequest,
    SendResponse,
    SendSegment,
)
from .tool_bridge import channel_tool

# ======================================================================
# 频道元数据 / 配置
# ======================================================================


class ChannelMetadata(BaseModel):
    """频道元数据（借鉴 nekro-agent AdapterMetadata）。

    让频道自描述，供 WebUI / 文档 / 日志使用。
    """

    name: str
    description: str
    version: str = "1.0.0"
    author: str = ""
    homepage: str = ""
    tags: List[str] = Field(default_factory=list)


class ChannelConfig(BaseModel):
    """频道配置基类（借鉴 nekro-agent BaseAdapterConfig，pydantic 化）。

    所有频道通用配置项。子类继承后追加平台特有字段即可。

    与统一配置系统（`core/config.py`）的集成方式：
    - 频道配置模型即声明源：`channels/<id>/config.py` 的 ``CONFIG_MODEL``
      经 ``agent/channel/config.py`` 派生注册进 ConfigRegistry，
      值为 ``<channel_id>_<field>`` 键存入 ConfigManager（app_config.json）
    - 运行时 ``BaseChannel.get_config()`` 从 ConfigManager 物化本模型实例
    - 配置写入（Web /config/meta 或 AI update_entity_config）经
      ConfigManager 变更监听即时重新物化，无需重启
    - 环境变量 ``ANELF_<CHANNEL_ID>_<FIELD>`` 优先级高于文件（ConfigManager 既有机制）
    """

    # ---- 基础 ----
    enabled: bool = Field(default=True, description="启用频道")

    # ---- 交互 ----
    session_enable_at: bool = Field(default=True, description="启用 @用户 功能")
    show_processing_emoji: bool = Field(default=True, description="显示处理中表情反馈")
    typing_indicator: bool = Field(default=True, description="显示打字中状态")

    # ---- 命令 ----
    command_prefix: str = Field(default="/", description="命令前缀")
    command_enabled: bool = Field(default=True, description="启用命令系统")
    command_unauthorized_output: bool = Field(default=True, description="权限不足提示")
    command_enhanced_output: bool = Field(default=False, description="命令增强输出")
    command_enhanced_output_min_length: int = Field(default=200, description="增强输出触发字数")

    # ---- 消息 ----
    message_max_length: int = Field(default=4000, description="单条消息最大长度，超出自动分段")
    reply_to_source: bool = Field(default=True, description="回复时引用源消息")

    # ---- 批准机制 ----
    approval_timeout_seconds: float = Field(default=60.0, description="批准请求超时时间")
    approval_default_action: Literal["deny", "allow", "ask"] = Field(
        default="ask", description="批准默认动作（超时未响应时）",
    )

    # ---- 重连策略 ----
    reconnect_max_retries: int = Field(default=5, description="最大重连次数")
    reconnect_backoff_seconds: float = Field(default=2.0, description="重连基础退避")
    reconnect_backoff_max_seconds: float = Field(default=60.0, description="重连最大退避")

    # ---- 健康探针 ----
    health_check_interval_seconds: float = Field(default=60.0, description="健康探针周期")
    health_check_timeout_seconds: float = Field(default=5.0, description="健康探针超时")

    # ---- 平台特有扩展位 ----
    # 子类通过继承追加字段，例如 TelegramConfig(ChannelConfig) 加 bot_token 等
    model_config = {"extra": "allow"}


TConfig = TypeVar("TConfig", bound=ChannelConfig)
T = TypeVar("T", bound="BaseChannel")


# ======================================================================
# 批准机制相关（前置声明，完整实现在 agent/approval/）
# ======================================================================


class ApprovalPromptRenderContext(BaseModel):
    """渲染批准提示的上下文。

    各频道根据自身能力渲染（Telegram 用 InlineKeyboard、WebUI 用按钮、CLI 用 y/n）。
    """

    request_id: str
    tool_name: str
    tool_args_summary: str  # 已脱敏
    risk_level: str
    reason: str
    timeout_seconds: float


# ======================================================================
# BaseChannel
# ======================================================================


class BaseChannel(BaseEntity, ABC, Generic[TConfig]):
    """平台频道抽象基类。

    继承 BaseEntity（自动注册到 EntityRegistry，类型 ADAPTER）。
    子类必须声明：
      - channel_id / display_name / capabilities / metadata
      - _Configs: ChannelConfig 子类
      - start() / stop()
      - forward_message()
      - get_self_info() / get_channel_info()
      - health_check()

    子类可选覆盖：
      - set_message_reaction()（表情反馈）
      - detect_command()（命令解析定制）
      - get_router()（HTTP 频道）
      - render_approval_prompt()（批准提示渲染）
    """

    _entity_type = EntityType.ADAPTER

    # 子类必填：类属性声明
    channel_id: str
    display_name: str
    capabilities: Set[ChannelCapability]
    metadata: ChannelMetadata

    # 子类必填：配置类
    _Configs: ClassVar[Type[ChannelConfig]] = ChannelConfig

    # 配置缓存（实例级）
    _config: Optional[ChannelConfig] = None

    def __init__(self) -> None:
        self._status: ChannelStatus = ChannelStatus.STOPPED
        self._last_health_check: float = 0.0
        self._last_health_status: Optional[HealthStatus] = None
        super().__init__()
        self._load_and_register_config()
        # 配置变更监听：ConfigManager 写入命中本频道键前缀时即时重新物化
        try:
            from core.config import ConfigManager
            ConfigManager.add_listener(self._config_key_prefix, self._on_config_changed)
        except Exception as exc:
            log(f"配置变更监听注册失败: {self.channel_id} -> {exc}", "WARNING", tag="通道")

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------

    @property
    def status(self) -> ChannelStatus:
        return self._status

    @property
    def key(self) -> str:
        """频道唯一标识（channel_id 的别名）。"""
        return self.channel_id

    def get_status_info(self) -> Dict[str, Any]:
        return {
            "key": self.channel_id,
            "name": self.display_name,
            "status": self._status.value,
            "capabilities": [c.value for c in self.capabilities],
            "metadata": self.metadata.model_dump(),
            "last_health_check": self._last_health_check,
            "last_health_status": (
                self._last_health_status.model_dump() if self._last_health_status else None
            ),
        }

    # ------------------------------------------------------------------
    # 生命周期（抽象）
    # ------------------------------------------------------------------

    @abstractmethod
    async def start(self) -> None:
        """启动频道。"""

    @abstractmethod
    async def stop(self) -> None:
        """停止频道。"""

    # ------------------------------------------------------------------
    # 统一发送入口（子类必须实现）
    # ------------------------------------------------------------------

    @abstractmethod
    async def forward_message(self, request: SendRequest) -> SendResponse:
        """唯一发送入口。所有 send_* 便捷方法最终都会调用此方法。

        Args:
            request: 统一发送请求（包含 segments / reply_to / parse_mode / silent 等）

        Returns:
            统一发送响应（成功/失败 + message_id）
        """

    # ------------------------------------------------------------------
    # forward_message 模板实现（segment 类型 → send_* 方法统一分发）
    # ------------------------------------------------------------------

    # segment type → send_* 方法名。子类可覆盖本映射声明支持的段类型，
    # 然后以 ``return await self._forward_via_segment_map(request)`` 实现 forward_message
    _SEGMENT_SENDERS: ClassVar[Dict[str, str]] = {
        "text": "send_text",
        "image": "send_photo",
        "video": "send_video",
        "audio": "send_audio",
        "voice": "send_voice",
        "file": "send_file",
    }

    async def _forward_via_segment_map(self, request: SendRequest) -> SendResponse:
        """forward_message 模板实现：按 _SEGMENT_SENDERS 逐段分发到 send_* 方法。

        逐段调用映射的 send_* 方法，从 JSON 返回中收集 message_id
        （单值 ``message_id`` 与列表 ``message_ids`` 均识别），
        汇总为统一 SendResponse；单段失败不中断后续段。
        """
        try:
            chat_id = request.channel.channel_id
            message_ids: List[str] = []
            for seg in request.segments:
                seg_type = seg.type.value
                method_name = self._SEGMENT_SENDERS.get(seg_type)
                if not method_name:
                    continue
                method = getattr(self, method_name, None)
                if method is None:
                    continue
                result_json = await self._call_segment_sender(method, seg, request, chat_id)
                message_ids.extend(self._extract_message_ids(result_json))
            if message_ids:
                return SendResponse(
                    success=True,
                    message_id=message_ids[0],
                    message_ids=message_ids,
                )
            return SendResponse(success=True, message_id="empty")
        except Exception as exc:
            return SendResponse(success=False, error=str(exc))

    @staticmethod
    async def _call_segment_sender(
        method: Callable[..., Awaitable[str]],
        seg: SendSegment,
        request: SendRequest,
        chat_id: str,
    ) -> str:
        """按段类型调用 send_* 方法（文本带 reply_to/parse_mode，媒体带 caption）。"""
        seg_type = seg.type.value
        kwargs: Dict[str, Any] = {}
        if seg_type == "text":
            args: Tuple[Any, ...] = (seg.content,)
            if request.reply_to:
                kwargs["reply_to"] = request.reply_to
            parse_mode = request.parse_mode or seg.parse_mode
            if parse_mode:
                kwargs["parse_mode"] = parse_mode
        else:
            args = (seg.file_path or seg.content,)
            if seg_type != "voice":
                kwargs["caption"] = seg.caption
        return await method(chat_id, *args, **kwargs)

    @staticmethod
    def _extract_message_ids(result_json: str) -> List[str]:
        """从 send_* 的 JSON 返回中收集 message_id（message_id 单值 / message_ids 列表）。"""
        try:
            result = json.loads(result_json)
        except (TypeError, ValueError):
            return []
        if not isinstance(result, dict) or not result.get("success"):
            return []
        ids: List[str] = []
        for mid in result.get("message_ids") or []:
            ids.append(str(mid))
        single = result.get("message_id")
        if single:
            ids.append(str(single))
        return ids

    # ------------------------------------------------------------------
    # 便捷发送方法（默认实现，内部走 forward_message）
    # ------------------------------------------------------------------

    def _build_send_request(
        self,
        chat_id: str,
        segments: List[SendSegment],
        *,
        reply_to: Optional[str] = None,
        parse_mode: Optional[str] = None,
        silent: bool = False,
        thread_id: Optional[int] = None,
        channel_type: str = "private",
        extra: Optional[Dict[str, Any]] = None,
    ) -> SendRequest:
        """构造统一发送请求。"""
        channel = AdapterChannel(
            channel_id=chat_id,
            channel_type=ChannelType.GROUP if channel_type == "group" else ChannelType.PRIVATE,
        )
        return SendRequest(
            adapter_key=self.channel_id,
            channel=channel,
            segments=segments,
            reply_to=reply_to,
            parse_mode=parse_mode,
            silent=silent,
            thread_id=thread_id,
            extra=extra or {},
        )

    async def send_text(
        self,
        chat_id: str,
        text: str,
        *,
        reply_to: Optional[str] = None,
        parse_mode: Optional[str] = None,
        silent: bool = False,
        channel_type: str = "private",
        **kwargs: Any,
    ) -> str:
        """发送文本消息。"""
        req = self._build_send_request(
            chat_id,
            [SendSegment(type="text", content=text)],
            reply_to=reply_to,
            parse_mode=parse_mode,
            silent=silent,
            channel_type=channel_type,
            extra=kwargs,
        )
        resp = await self.forward_message(req)
        if resp.success:
            return _ok({"chat_id": chat_id, "message_id": resp.message_id})
        return _err(resp.error or "发送失败")

    async def send_photo(
        self,
        chat_id: str,
        photo: str,
        caption: str = "",
        **kwargs: Any,
    ) -> str:
        req = self._build_send_request(
            chat_id,
            [SendSegment(type="image", file_path=photo, caption=caption)],
            **kwargs,
        )
        resp = await self.forward_message(req)
        return _ok({"message_id": resp.message_id}) if resp.success else _err(resp.error or "失败")

    @channel_tool(description="向指定会话发送视频")
    async def send_video(
        self, chat_id: str, video: str, caption: str = "", **kwargs: Any,
    ) -> str:
        req = self._build_send_request(
            chat_id,
            [SendSegment(type="video", file_path=video, caption=caption)],
            **kwargs,
        )
        resp = await self.forward_message(req)
        return _ok({"message_id": resp.message_id}) if resp.success else _err(resp.error or "失败")

    @channel_tool(description="向指定会话发送音频")
    async def send_audio(
        self, chat_id: str, audio: str, caption: str = "", **kwargs: Any,
    ) -> str:
        req = self._build_send_request(
            chat_id,
            [SendSegment(type="audio", file_path=audio, caption=caption)],
            **kwargs,
        )
        resp = await self.forward_message(req)
        return _ok({"message_id": resp.message_id}) if resp.success else _err(resp.error or "失败")

    async def send_voice(
        self, chat_id: str, voice: str, caption: str = "", **kwargs: Any,
    ) -> str:
        req = self._build_send_request(
            chat_id,
            [SendSegment(type="voice", file_path=voice, caption=caption)],
            **kwargs,
        )
        resp = await self.forward_message(req)
        return _ok({"message_id": resp.message_id}) if resp.success else _err(resp.error or "失败")

    async def send_file(
        self, chat_id: str, file_path: str, caption: str = "", **kwargs: Any,
    ) -> str:
        req = self._build_send_request(
            chat_id,
            [SendSegment(type="file", file_path=file_path, caption=caption)],
            **kwargs,
        )
        resp = await self.forward_message(req)
        return _ok({"message_id": resp.message_id}) if resp.success else _err(resp.error or "失败")

    # 其他 send_* 略（编辑 / 删除 / 转发 / 置顶等保持旧接口，由 channel.py 提供默认实现）
    # 这些暂时仍走旧 BaseChannel 的默认 _err("不支持 ...")，等 Phase 2 重构各 adapter 时统一接入

    # ------------------------------------------------------------------
    # 旧版兼容接口（各频道共用的薄封装，集中在基类维护）
    # ------------------------------------------------------------------

    async def send_message(self, request: Any) -> bool:
        """旧版兼容发送接口：提取文本走 send_text，返回是否成功。"""
        chat_id = request.channel.channel_id
        text = request.content
        reply_to = getattr(request, "reply_to", None)
        result_json = await self.send_text(chat_id, text, reply_to=reply_to)
        try:
            result = json.loads(result_json)
        except (TypeError, ValueError):
            return False
        return bool(result.get("success", False))

    _KNOWN_CHATS_EMPTY_HINT = (
        "Bot 尚未与任何会话交互。当有人发消息给 Bot 后，会话信息会自动记录。"
    )

    @channel_tool()
    async def list_known_chats(self, **kwargs: Any) -> str:
        """列出 Bot 已交互过的所有会话（数据由适配器的 _known_chats 维护）。"""
        known: Dict[str, Dict[str, Any]] = getattr(self, "_known_chats", None) or {}
        if not known:
            return _ok({"chats": [], "hint": self._KNOWN_CHATS_EMPTY_HINT})
        return _ok({"chats": list(known.values()), "count": len(known)})

    # ------------------------------------------------------------------
    # 信息查询（抽象，借鉴 nekro-agent）
    # ------------------------------------------------------------------

    @abstractmethod
    async def get_self_info(self) -> ChannelUser:
        """获取自身（Bot）信息。"""

    async def get_user_info(self, user_id: str, channel_id: str) -> ChannelUser:
        """获取用户信息。

        默认实现：平台无用户查询能力时的占位返回；
        有真实查询能力的频道（如 Telegram）覆盖本方法。
        """
        return ChannelUser(
            platform=self.channel_id,
            user_id=user_id,
            user_name=user_id,
        )

    @abstractmethod
    async def get_channel_info(self, channel_id: str) -> ChannelInfo:
        """获取频道信息。"""

    # ------------------------------------------------------------------
    # 健康探针（抽象）
    # ------------------------------------------------------------------

    @abstractmethod
    async def health_check(self) -> HealthStatus:
        """主动健康探针。

        子类实现：通常是一次轻量 API 调用（如 telegram.bot.get_me()），
        返回 HealthStatus。失败要捕获异常并返回 healthy=False，
        而不是抛出让看门狗崩溃。
        """

    async def check_health(self) -> HealthStatus:
        """执行健康探针并缓存结果。

        由 supervision 调用；带超时控制。
        """
        cfg = self.get_config()
        timeout = cfg.health_check_timeout_seconds if cfg else 5.0
        started = time.time()
        try:
            status = await asyncio.wait_for(self.health_check(), timeout=timeout)
            if status.latency_ms is None:
                status.latency_ms = (time.time() - started) * 1000
        except asyncio.TimeoutError:
            status = HealthStatus(
                healthy=False,
                detail="health check timeout",
                last_error=f"timeout after {timeout}s",
                latency_ms=timeout * 1000,
            )
        except Exception as exc:
            status = HealthStatus(
                healthy=False,
                detail=f"health check exception: {exc}",
                last_error=str(exc),
                latency_ms=(time.time() - started) * 1000,
            )
        self._last_health_check = time.time()
        self._last_health_status = status
        if status.healthy:
            status.last_success_at = time.time()
        return status

    # ------------------------------------------------------------------
    # 表情反馈（可选）
    # ------------------------------------------------------------------

    async def set_message_reaction(
        self,
        chat_id: str,
        message_id: str,
        emoji: str = "👍",
        on: bool = True,
    ) -> bool:
        """设置消息表情反应（默认不支持）。"""
        return False

    # ------------------------------------------------------------------
    # 命令系统钩子
    # ------------------------------------------------------------------

    def detect_command(self, text: str) -> Optional[Tuple[str, str]]:
        """检测命令前缀，返回 (command_name, raw_args)。

        子类可覆盖：例如 Telegram 群聊 "/help@botname" 需要去掉 @botname 后缀。
        """
        cfg = self.get_config()
        if not cfg or not cfg.command_enabled:
            return None
        prefix = cfg.command_prefix
        if not text.startswith(prefix):
            return None
        content = text[len(prefix):]
        parts = content.split(None, 1)
        if not parts:
            return None
        return parts[0], parts[1] if len(parts) > 1 else ""

    async def execute_command(
        self,
        chat_key: str,
        user_id: str,
        username: str,
        command_name: str,
        raw_args: str,
        *,
        is_super_user: bool = False,
        is_advanced_user: bool = False,
    ) -> Optional[List[CommandResponse]]:
        """执行命令并消费流式输出（由 InputPipeline 的 CommandProcessor 调用）。

        默认返回 None 让 Mind 处理；子类可覆盖实现频道自定义命令。
        """
        return None

    # ------------------------------------------------------------------
    # 批准机制钩子（占位，由 agent/approval/ 驱动）
    # ------------------------------------------------------------------

    async def render_approval_prompt(
        self,
        ctx: ApprovalPromptRenderContext,
    ) -> SendRequest:
        """渲染批准提示消息。

        默认实现：纯文本提示（微信 / 飞书 / HTTP API 等纯文本频道共用），
        子类可覆盖（Telegram 用 InlineKeyboard、WebUI 用 SSE 弹窗、CLI 用 y/n 提示等）。
        """
        text = (
            f"⚠️ 工具调用需要批准\n"
            f"工具: {ctx.tool_name}\n"
            f"参数: {ctx.tool_args_summary[:200]}\n"
            f"风险: {ctx.risk_level}\n"
            f"原因: {ctx.reason}\n"
            f"超时: {ctx.timeout_seconds:.0f}s\n"
            f"\n"
            f"回复以下命令之一：\n"
            f"  approve {ctx.request_id}\n"
            f"  deny {ctx.request_id}"
        )
        return self._build_send_request(
            chat_id="",  # 由 approval/gate.py 填充
            segments=[SendSegment(type="text", content=text)],
            extra={"approval_request_id": ctx.request_id},
        )

    # ------------------------------------------------------------------
    # HTTP 频道钩子
    # ------------------------------------------------------------------

    def get_router(self) -> Optional[Any]:
        """返回 FastAPI Router（HTTP 类频道用，如 http_api / webui / webhook）。

        返回 None 表示本频道不是 HTTP 服务类型。
        """
        return None

    # ------------------------------------------------------------------
    # 入站消息分发
    # ------------------------------------------------------------------

    async def on_message(self, message: Any) -> None:
        """收到平台消息后调用，转发到 ChannelManager。"""
        from .manager import get_channel_manager
        await get_channel_manager().dispatch_inbound(self, message)

    # ------------------------------------------------------------------
    # 配置（统一配置系统物化 + 变更监听热更）
    # ------------------------------------------------------------------

    @property
    def _config_key_prefix(self) -> str:
        """本频道在 ConfigManager 中的键前缀（<channel_id>_）。"""
        return f"{self.channel_id}_"

    def _resolve_config_group(self) -> str:
        """实体注册时的配置分组（adapter/<channel_id>），供 AI/Web 按实体定位配置。"""
        return f"adapter/{self.channel_id}"

    def _load_and_register_config(self) -> None:
        """从 ConfigManager 物化频道配置（<channel_id>_<field> 键 → pydantic 模型）。

        未写入过 ConfigManager 的字段取模型默认值；校验失败整体回退默认配置。
        """
        from core.config import ConfigManager

        values: Dict[str, Any] = {}
        for name in self._Configs.model_fields:
            key = self._config_key_prefix + name
            if ConfigManager.has(key):
                values[name] = ConfigManager.get(key)
        try:
            self._config = self._Configs.model_validate(values)
        except Exception as exc:
            log(
                f"频道配置校验失败 ({self.channel_id}): {exc}，使用默认配置",
                "WARNING",
                tag="通道",
            )
            self._config = self._Configs()

    def _on_config_changed(self, key: str, value: Any) -> None:
        """ConfigManager 变更监听回调：本频道配置键写入时重新物化。"""
        try:
            self._load_and_register_config()
            log(f"频道配置已热更新: {self.channel_id} ({key})", "DEBUG", tag="通道")
        except Exception as exc:
            log(f"频道配置热更新失败: {self.channel_id} -> {exc}", "WARNING", tag="通道")

    def get_config(self) -> TConfig:
        """获取配置（带类型）。"""
        if self._config is None:
            self._load_and_register_config()
        return self._config  # type: ignore[return-value]

    @property
    def config(self) -> TConfig:
        """属性方式访问配置。"""
        return self.get_config()

    def _cfg(self, key: str, default: Any = None) -> Any:
        """读取配置项（extra=allow 扩展字段缺失时返回 default）。"""
        return getattr(self.config, key, default)

    def reload_config(self) -> bool:
        """手动触发配置重新物化（正常路径由变更监听自动热更，无需调用）。"""
        try:
            self._load_and_register_config()
            log(f"频道配置已热重载: {self.channel_id}", tag="通道")
            return True
        except Exception as exc:
            log(f"频道配置热重载失败: {exc}", "WARNING", tag="通道")
            return False

    # ------------------------------------------------------------------
    # @ 格式工具（保留旧版能力）
    # ------------------------------------------------------------------

    @staticmethod
    def normalize_at_mentions(text: str) -> str:
        """将 [at_uid:xxx] 转为纯文本 @uid。"""
        from .channel_types import normalize_at_mentions as _normalize
        return _normalize(text)



# ======================================================================
# 类型工具
# ======================================================================


def is_channel(obj: Any) -> bool:
    """检查对象是否为 BaseChannel 实例。"""
    return isinstance(obj, BaseChannel)
