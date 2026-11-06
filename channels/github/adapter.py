"""GitHub 频道的组件装配、生命周期和标准消息适配。

Model Experience: 事件送入仓库或议题会话，回复经统一出站转述。
Token effect: 策略指引约百余 token，事件内容由订阅控制。
Cache effect: 指引位于 channel_policy 层，事件不改写稳定前缀。
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Set

from agent.channel import get_provider_key
from agent.channel.base import BaseChannel, ChannelMetadata
from agent.channel.channel_types import ChannelCapability, ChannelStatus
from agent.channel.schemas import (
    AdapterChannel,
    AdapterMessage,
    AdapterUser,
    ChannelInfo,
    ChannelType,
    ChannelUser,
    ChannelUserRole,
    HealthStatus,
    MessageKind,
    SegmentType,
    SendRequest,
    SendResponse,
)
from core.log import log

from .auth import GitHubAuth
from .client import GitHubClient
from .config import GitHubConfig
from .credentials import register_defaults
from .journal import EventJournal
from .pipeline import EventPipeline
from .policy import github_reply_policy
from .poller import GitHubPoller
from .router import build_router as build_router
from .state import DigestBuffer, EventStats
from .tools import GitHubToolsMixin
from .webhook import WebhookIngress

register_defaults()

_SELF_INFO_TTL = 300.0
_HEALTH_PROBE_TTL = 60.0


class GitHubChannel(GitHubToolsMixin, BaseChannel[GitHubConfig]):
    """GitHub 频道(轮询/webhook 双模监听 + AI 工具面)。"""

    _entity_description = "GitHub 仓库监听频道(事件推送 + 读写工具面)"

    channel_id = "github"
    display_name = "GitHub"
    display_order = 33
    capabilities: Set[ChannelCapability] = {
        ChannelCapability.SEND_TEXT,
        ChannelCapability.GET_CHAT_INFO,
    }
    metadata = ChannelMetadata(
        name="GitHub",
        description="监听 GitHub 仓库动态(push/issue/PR/release/CI/star)推送给 AI,"
                    "并提供 github_* 查询/回写工具面;轮询(默认)与 webhook 双模",
        version="1.0.0",
        author="AnelfAgent",
        homepage="https://github.com",
        tags=["github", "代码托管"],
    )
    _Configs = GitHubConfig

    def __init__(self) -> None:
        self._client: Optional[GitHubClient] = None
        self._auth: Optional[GitHubAuth] = None
        self._journal: Optional[EventJournal] = None
        self._poller: Optional[GitHubPoller] = None
        self._pipeline: Optional[EventPipeline] = None
        self._ingress: Optional[WebhookIngress] = None
        self._digest = DigestBuffer()
        self._stats = EventStats()
        self._self_info: Optional[ChannelUser] = None
        self._self_info_at: float = 0.0
        self._last_probe_at: float = 0.0
        self._last_probe: Optional[HealthStatus] = None
        super().__init__()


    async def start(self) -> None:
        """启动:建客户端 → 事件管线 → 按模式起轮询/webhook 接收器。

        webhook 模式 fail-closed:未配置 webhook_secret 拒绝启动(不验签的
        公网端点是任人投毒的 AI 注入面)。
        """
        cfg = self.config
        if cfg.webhook_enabled and not cfg.webhook_secret and not any(account.webhook_secret_ref and get_provider_key(account.webhook_secret_ref) for account in cfg.accounts):
            raise RuntimeError(
                "GitHub 频道 webhook 模式必须配置 webhook_secret(验签 fail-closed);"
                "或改用 mode=poll(零公网依赖)"
            )
        if cfg.auth_mode == "pat" and not get_provider_key(cfg.token_ref):
            log("GitHub: 未配置 PAT(provider_keys.json 的 github 条目),"
                "轮询将以匿名形态运行(60 次/小时,仅公开仓)", "WARNING", tag="通道")
        if cfg.auth_mode == "app" and not get_provider_key(cfg.app_private_key_ref):
            raise RuntimeError(
                f"auth_mode=app 但 provider_keys.json 未配置 {cfg.app_private_key_ref}(App 私钥 PEM)"
            )

        self._stats.load()
        self._digest.load()

        try:
            self._client = self._build_client(cfg)
            self._journal = EventJournal()
            self._pipeline = EventPipeline(self, digest=self._digest, stats=self._stats, journal=self._journal)
            await self._pipeline.start()

            if cfg.poll_enabled:
                self._poller = GitHubPoller(self, self._client, self._pipeline)
                await self._poller.start()
            if cfg.webhook_enabled:
                self._ingress = WebhookIngress(self._pipeline)
                await self._ingress.start()

        except BaseException:
            await self.stop()
            raise

        log(
            f"GitHub: 频道已启动(mode={cfg.mode}, 订阅 {len(cfg.repos)} 仓, "
            f"auth={cfg.auth_mode}, 写操作={'开' if cfg.allow_write else '关'})",
            tag="通道",
        )

    async def stop(self) -> None:
        """停止:收轮询/webhook worker → 冲刷残余聚合 → 关客户端。"""
        if self._poller is not None:
            await self._poller.stop()
            self._poller = None
        if self._ingress is not None:
            await self._ingress.stop()
            self._ingress = None
        if self._pipeline is not None:
            await self._pipeline.stop()
            self._pipeline = None
        if self._client is not None:
            await self._client.close()
            self._client = None
        if self._auth is not None:
            await self._auth.close()
            self._auth = None
        self._journal = None
        log("GitHub: 频道已停止", tag="通道")

    def _build_client(self, cfg: GitHubConfig) -> GitHubClient:
        self._auth = GitHubAuth(lambda: self.config)
        return GitHubClient(base_url=cfg.api_base_url, auth=self._auth)

    @property
    def reply_policy(self) -> Any:
        """声明 GitHub 回复规则和按需工具目录。"""
        return github_reply_policy()

    def _on_config_changed(self, key: str, value: Any) -> None:
        """加载配置并解除可恢复的轮询暂停。"""
        super()._on_config_changed(key, value)
        if self._auth is not None:
            self._auth.register_credentials()
        if self._poller is not None:
            self._poller.resume()


    def build_inbound(self, repo: str, text: str, *, trigger: bool = True) -> AdapterMessage:
        """构造 GitHub 事件消息:每仓一个 GROUP 会话 scope(group_github:{owner/repo})。"""
        return AdapterMessage(
            sender=AdapterUser(platform="github", user_id="github_events", user_name="GitHub"),
            channel=AdapterChannel(
                channel_id=repo,
                channel_type=ChannelType.GROUP,
                channel_name=f"GitHub {repo}",
            ),
            content=text,
            kind=MessageKind.EVENT,
            is_to_me=True,
            trigger_mind=trigger,
        )

    async def dispatch_events(self, repo: str, events: List[Any], *, text: str) -> None:
        """事件派发入口(pipeline 调用):走标准入站链。"""
        import hashlib

        message = self.build_inbound(repo, text, trigger=True)
        sub = self.config.subscription_for(repo)
        if sub and sub.session_mode == "subject" and events:
            message.session_id = str(events[0].extra.get("subject") or "")
        keys = [str(event.extra.get("_journal_id") or event.event_id) for event in events]
        if keys:
            message.message_id = hashlib.sha256("|".join(keys).encode()).hexdigest()
        await self.on_message(message)

    async def alert_repo(self, repo: str, message: str) -> None:
        """频道告警(凭据失效/仓不可见等):SYSTEM 消息注入该仓会话并唤醒 AI 转告主人。"""
        text = f"[GitHub 频道告警] {repo}: {message}"
        await self.on_message(self.build_inbound(repo or "notification", text, trigger=True)
                              .model_copy(update={"kind": MessageKind.SYSTEM}))

    @property
    def pipeline(self) -> Optional[EventPipeline]:
        return self._pipeline

    @property
    def ingress(self) -> Optional[WebhookIngress]:
        return self._ingress


    async def forward_message(self, request: SendRequest) -> SendResponse:
        """回复转述:GitHub 无会话可回,经 reply_relay 配置转述到主人频道。

        未配置 reply_relay 时如实失败并给出指引(不静默吞消息);
        回写 GitHub 本身是 github_* 写工具的职责,不经此路径。
        """
        text_parts = [seg.content for seg in request.segments if seg.type == SegmentType.TEXT]
        text = "\n".join(t for t in text_parts if t).strip()
        if not text:
            return SendResponse(success=False, error="空消息")
        relay = (self.config.reply_relay or "").strip()
        if not relay:
            return SendResponse(
                success=False,
                error="GitHub 频道不直接投递回复;请配置 reply_relay(adapter:target)"
                      "或用 send_message 工具转述 / github_create_comment 回写",
            )
        adapter_key, sep, target = relay.partition(":")
        if not sep or not adapter_key or not target or adapter_key == self.channel_id:
            return SendResponse(success=False, error=f"reply_relay 格式非法: {relay}(应为 'adapter:target')")
        try:
            from agent.channel import get_channel_manager
            relay_channel = get_channel_manager().get(adapter_key)
        except Exception as exc:
            return SendResponse(success=False, error=f"转述目标频道解析失败: {exc}")
        if relay_channel is None or relay_channel.status != ChannelStatus.RUNNING:
            return SendResponse(success=False, error=f"转述目标频道不可用: {adapter_key}")
        repo = request.channel.channel_id
        prefix = f"[GitHub {repo}] " if repo else "[GitHub] "
        try:
            from agent.channel.output_tools import send_text_to_channel

            result_json = await send_text_to_channel(adapter_key, target, prefix + text)
            result = json.loads(result_json)
        except Exception as exc:
            return SendResponse(success=False, error=f"转述发送异常: {exc}")
        if not result.get("success"):
            return SendResponse(success=False, error=f"转述失败: {result.get('error') or '未知'}")
        return SendResponse(success=True, message_id=str(result.get("message_id") or "relayed"))


    async def get_self_info(self) -> ChannelUser:
        """自身信息:PAT→认证用户(缓存 300s);App→App 标识;匿名→占位。"""
        if self._self_info is not None and time.time() - self._self_info_at < _SELF_INFO_TTL:
            return self._self_info
        cfg = self.config
        user_id, user_name = "github_bot", "GitHub"
        if cfg.auth_mode == "pat" and self._client is not None:
            try:
                data = (await self._client.get("/user")).data or {}
                user_id = str(data.get("login") or user_id)
                user_name = str(data.get("name") or data.get("login") or user_name)
            except Exception as exc:
                log(f"GitHub: 读取当前用户失败: {exc}", "DEBUG", tag="通道")
        elif cfg.auth_mode == "app":
            user_id, user_name = f"github-app-{cfg.app_id}", f"GitHub App {cfg.app_id}"
        self._self_info = ChannelUser(
            platform=self.channel_id, user_id=user_id, user_name=user_name,
            role=ChannelUserRole.MEMBER, is_bot=True,
        )
        self._self_info_at = time.time()
        return self._self_info

    async def get_channel_info(self, channel_id: str) -> ChannelInfo:
        """会话信息:channel_id 即仓名(owner/repo)。"""
        sub = self.config.subscription_for(channel_id)
        extra: Dict[str, Any] = {}
        if sub is not None:
            extra = {"local_path": sub.local_path, "priority_boost": sub.priority_boost}
        return ChannelInfo(
            channel_id=channel_id,
            channel_name=f"GitHub {channel_id}",
            channel_type=ChannelType.GROUP,
            description="GitHub 仓库事件会话",
            extra=extra,
        )

    async def health_check(self) -> HealthStatus:
        """健康探针:配额接口实测(60s 缓存)+ 接收器状态汇总。"""
        if self._status != ChannelStatus.RUNNING:
            return HealthStatus(healthy=False, detail="频道未运行", last_error="not_running")
        problems: List[str] = []
        cfg = self.config
        if cfg.poll_enabled and (self._poller is None or not self._poller.running):
            problems.append("轮询器未运行")
        if cfg.webhook_enabled and (self._ingress is None or not self._ingress.running):
            problems.append("webhook 接收器未运行")
        if cfg.auth_mode == "pat" and not get_provider_key(cfg.token_ref):
            problems.append("PAT 未配置(匿名形态,配额极低)")

        if self._poller:
            for repo, state in self._poller.repo_states().items():
                if float(state.get("paused_until") or state.get("quota_paused_until") or 0) > time.time():
                    problems.append(f"{repo}: {state.get('last_error') or '配额暂停'}")
                if state.get("possible_gap"):
                    problems.append(f"{repo}: 轮询窗口可能存在事件缺口")
        if self._journal:
            inbox = await self._journal.snapshot(limit=1)
            if inbox["counts"].get("failed", 0):
                problems.append(f"收件箱失败事件: {inbox['counts']['failed']}")
        detail_parts = [f"mode={cfg.mode}", f"repos={len(cfg.repos)}"]
        if self._client is not None and self._client.rate_remaining >= 0:
            detail_parts.append(f"rate_remaining={self._client.rate_remaining}")

        if self._client is not None and time.time() - self._last_probe_at > _HEALTH_PROBE_TTL:
            self._last_probe_at = time.time()
            try:
                await self._client.get("/rate_limit")
                self._last_probe = HealthStatus(healthy=True, detail="api reachable",
                                                last_success_at=time.time())
            except Exception as exc:
                self._last_probe = HealthStatus(healthy=False, detail=f"api unreachable: {exc}",
                                                last_error=str(exc))
        if self._last_probe is not None and not self._last_probe.healthy:
            problems.append(self._last_probe.detail)

        return HealthStatus(
            healthy=not problems,
            detail="; ".join(detail_parts + problems),
            last_error="; ".join(problems) if problems else None,
            last_success_at=(self._last_probe.last_success_at if self._last_probe else None),
        )

    def get_status_info(self) -> Dict[str, Any]:
        info = super().get_status_info()
        info["github"] = {
            "mode": self.config.mode,
            "repos": [sub.full_name for sub in self.config.repos],
            "poller_running": bool(self._poller and self._poller.running),
            "webhook_running": bool(self._ingress and self._ingress.running),
            "webhook_rejected": self._ingress.rejected_count if self._ingress else 0,
            "dispatched_events": self._pipeline.dispatched_count if self._pipeline else 0,
            "ignored_events": self._pipeline.ignored_count if self._pipeline else 0,
            "rate_remaining": self._client.rate_remaining if self._client else -1,
        }
        return info



CHANNEL_CLASS = GitHubChannel
