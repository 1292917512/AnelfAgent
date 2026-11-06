"""GitHub 订阅、配置与恢复操作。

Model Experience: 模型可查询配置 schema，并以数组和对象更新配置。
Token effect: 按需返回 schema，不自动注入完整配置。
Cache effect: 结果仅进入工具消息，不改变稳定前缀。
"""

from __future__ import annotations

from typing import Any

from agent.channel.channel_types import _ok
from agent.channel.config import set_channel_config
from agent.channel.tool_bridge import channel_tool

from .config import GitHubConfig, RepoSubscription
from .tool_common import GitHubToolHost, _err_of, _valid_repo


class GitHubConfigTools(GitHubToolHost):
    """通过同一份配置声明管理订阅和失败恢复。"""

    @channel_tool(group="github", description="按收件箱或平台事件 ID 读取原始事件与完整上下文")
    async def get_event(self, event_id: str) -> str:
        """按需读取被摘要省略的事件字段。"""
        try:
            from .tool_api import safe_result

            if self._journal is None:
                raise RuntimeError("频道未启动")
            event = await self._journal.event(event_id)
            if event is None:
                raise ValueError("事件不存在或已超出保留期")
            return _ok({"event": safe_result(event)})
        except Exception as exc:
            return _err_of(exc, "查询事件")

    @channel_tool(group="github", description="查询 GitHub 配置与字段 schema；可指定字段减少输出")
    async def describe_config(self, field: str = "") -> str:
        """返回无密钥的配置及可选项。"""
        try:
            schema = GitHubConfig.model_json_schema()
            values = self.config.model_dump(exclude={"webhook_secret"})
            if field:
                if field not in schema["properties"]:
                    raise ValueError("未知配置字段")
                return _ok({"field": field, "value": values.get(field), "schema": schema["properties"][field], "$defs": schema.get("$defs", {})})
            return _ok({"config": values, "fields": list(schema["properties"]), "hint": "指定 field 查询字段 schema；数组以 JSON 数组传入"})
        except Exception as exc:
            return _err_of(exc, "查询配置")

    @channel_tool(group="github", sensitive=True, description="按字段更新 GitHub 配置；未提供的字段保留，空数组、false、0 可显式设置")
    async def configure(self, changes: dict[str, Any]) -> str:
        """校验完整候选配置后原子更新指定字段。"""
        try:
            unknown = set(changes) - set(GitHubConfig.model_fields)
            if unknown:
                raise ValueError(f"未知配置字段: {sorted(unknown)}")
            validated = GitHubConfig.model_validate({**self.config.model_dump(), **changes})
            values = validated.model_dump()
            set_channel_config("github", **{key: values[key] for key in changes})
            return _ok({"updated": list(changes), "restart_required": bool(set(changes) & {"mode", "api_base_url"})})
        except Exception as exc:
            return _err_of(exc, "更新配置")

    @channel_tool(group="github", description="新增或更新 GitHub 仓库订阅；events、branches、message_fields 使用数组")
    async def subscribe_repo(self, repo: str, events: list[str] | None = None, branches: list[str] | None = None,
                             local_path: str | None = None, poll_interval_sec: int | None = None,
                             priority_boost: bool | None = None, digest: bool | None = None,
                             message_fields: list[str] | None = None, account: str | None = None,
                             installation_id: int | None = None, session_mode: str | None = None,
                             delivery: str | None = None) -> str:
        """保留未传入字段，显式值覆盖已有订阅。"""
        try:
            repo = _valid_repo(repo)
            old = self.config.subscription_for(repo)
            owner, name = repo.split("/")
            updates = {"events": events, "branches": branches, "local_path": local_path,
                       "poll_interval_sec": poll_interval_sec, "priority_boost": priority_boost,
                       "digest": digest, "message_fields": message_fields, "account": account,
                       "installation_id": installation_id, "session_mode": session_mode, "delivery": delivery}
            entry = RepoSubscription.model_validate({
                **(old.model_dump() if old else {"owner": owner, "repo": name}),
                **{key: value for key, value in updates.items() if value is not None},
            })
            repos = [sub.model_dump() for sub in self.config.repos if sub.full_name.lower() != repo.lower()]
            repos.append(entry.model_dump())
            validated = GitHubConfig.model_validate({**self.config.model_dump(), "repos": repos})
            set_channel_config("github", repos=[sub.model_dump() for sub in validated.repos])
            return _ok({"subscribed": repo, "updated": old is not None, "subscription": entry.model_dump()})
        except Exception as exc:
            return _err_of(exc, "订阅仓库")

    @channel_tool(group="github", description="取消一个 GitHub 仓库订阅")
    async def unsubscribe_repo(self, repo: str) -> str:
        """移除指定仓库，保留其他订阅。"""
        try:
            repo = _valid_repo(repo)
            if self.config.subscription_for(repo) is None:
                raise ValueError("未订阅该仓库")
            set_channel_config("github", repos=[sub.model_dump() for sub in self.config.repos if sub.full_name.lower() != repo.lower()])
            return _ok({"unsubscribed": repo})
        except Exception as exc:
            return _err_of(exc, "取消订阅")

    @channel_tool(group="github", description="查询 GitHub 持久收件箱与失败事件")
    async def delivery_status(self) -> str:
        """返回待处理和失败记录。"""
        try:
            if self._journal is None:
                raise RuntimeError("频道未启动")
            return _ok(await self._journal.snapshot())
        except Exception as exc:
            return _err_of(exc, "查询收件箱")

    @channel_tool(group="github", description="重试 GitHub 失败事件并恢复仓库轮询；空参数恢复全部")
    async def retry_delivery(self, event_id: str = "", repo: str = "") -> str:
        """恢复失败收件箱记录和暂停的轮询。"""
        try:
            if self._journal is None:
                raise RuntimeError("频道未启动")
            count = await self._journal.retry(event_id)
            if self._poller:
                self._poller.resume(repo)
            return _ok({"retried": count, "repo": repo})
        except Exception as exc:
            return _err_of(exc, "恢复投递")
