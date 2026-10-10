"""GitHub 频道 AI 工具面 — @channel_tool 注册为 github_*(组 github)。

三档纪律:
- **读工具**(AUTO_ALLOW):查询仓库状态/事件/CI/配额;订阅自助工具让 AI
  在对话中自行增删监听仓(nekro-agent 哲学,写配置走 set_channel_config 唯一入口);
- **写工具**(sensitive + ``allow_write`` 配置门控,默认关):评论/开 issue/打标签/
  关闭 issue——全部经 ApprovalGate(审批规则可按 channel_id="github" 配置);
- **reaction**(轻量确认,敏感但不受 allow_write 限制):风险≈0 的互动。
不提供 merge/push/release/delete 等高危写操作(主人应在 GitHub 页面亲自操作)。
"""

from __future__ import annotations

import time
from typing import Any, Dict

from agent.channel.channel_types import _err, _ok
from agent.channel.tool_bridge import channel_tool

from .client import AuthError, GitHubApiError, NotFoundError
from .config import RepoSubscription
from .events import from_poll_api, render_event, sanitize_body

_GROUP = "github"


def _err_of(exc: Exception, action: str) -> str:
    """异常 → 工具错误 JSON(统一 cause 语义)。"""
    if isinstance(exc, AuthError):
        return _err(f"{action}失败: 凭据无效或权限不足({exc})")
    if isinstance(exc, NotFoundError):
        return _err(f"{action}失败: 仓库/资源不存在或对 token 不可见({exc})")
    if isinstance(exc, GitHubApiError):
        return _err(f"{action}失败: {exc}")
    return _err(f"{action}异常: {exc}")


def _valid_repo(repo: str) -> str:
    repo = (repo or "").strip().strip("/")
    if "/" not in repo or len(repo.split("/")) != 2 or not all(repo.split("/")):
        raise ValueError(f"仓库参数须为 'owner/repo' 形态: {repo!r}")
    return repo


class GitHubToolsMixin:
    """GitHub 频道工具集(经 @channel_tool 注册为 github_<method>)。"""

    # 以下属性由 GitHubChannel 提供(声明仅为类型提示)
    _client: Any
    _stats: Any
    _digest: Any

    config: Any  # ChannelConfig.get_config()

    def _require_client(self) -> Any:
        client = getattr(self, "_client", None)
        if client is None:
            raise RuntimeError("GitHub 频道未启动")
        return client

    # ==================================================================
    # 读组(AUTO_ALLOW)
    # ==================================================================

    @channel_tool(group=_GROUP, description="列出近期 GitHub 事件(本地已收事件环;可按仓过滤)")
    async def list_events(self, repo: str = "", hours: int = 24, limit: int = 30) -> str:
        items = self._stats.recent_events(repo=repo, limit=200)
        cutoff = time.time() - max(int(hours), 1) * 3600
        items = [x for x in items if float(x.get("ts") or 0) >= cutoff][: max(int(limit), 1)]
        return _ok({"count": len(items), "events": items, "source": "local"})

    @channel_tool(group=_GROUP, description="查看 issue/PR 详情与近期评论")
    async def get_issue(self, repo: str, number: int) -> str:
        try:
            repo = _valid_repo(repo)
            client = self._require_client()
            issue = (await client.get(f"/repos/{repo}/issues/{int(number)}")).data or {}
            comments = (await client.get(
                f"/repos/{repo}/issues/{int(number)}/comments", params={"per_page": 5},
            )).data or []
            return _ok({
                "number": issue.get("number"), "title": issue.get("title"),
                "state": issue.get("state"), "user": (issue.get("user") or {}).get("login"),
                "labels": [x.get("name") for x in (issue.get("labels") or []) if isinstance(x, dict)],
                "is_pr": bool(issue.get("pull_request")),
                "body": sanitize_body(issue.get("body"), 2000),
                "comments": [{
                    "user": (c.get("user") or {}).get("login"),
                    "body": sanitize_body(c.get("body"), 800),
                    "created_at": c.get("created_at"),
                } for c in comments[:5]],
                "url": issue.get("html_url"),
            })
        except Exception as exc:
            return _err_of(exc, "查询 issue")

    @channel_tool(group=_GROUP, description="列出仓库的 issue(默认不含 PR;可按状态/标签过滤)")
    async def list_issues(self, repo: str, state: str = "open", labels: str = "",
                          limit: int = 20) -> str:
        try:
            repo = _valid_repo(repo)
            params: Dict[str, Any] = {"state": state, "per_page": min(max(int(limit), 1), 50)}
            if labels.strip():
                params["labels"] = labels.strip()
            data = (await self._require_client().get(f"/repos/{repo}/issues", params=params)).data or []
            issues = [{
                "number": x.get("number"), "title": x.get("title"), "state": x.get("state"),
                "user": (x.get("user") or {}).get("login"),
                "labels": [l.get("name") for l in (x.get("labels") or []) if isinstance(l, dict)],
                "comments": x.get("comments"), "created_at": x.get("created_at"),
                "url": x.get("html_url"),
            } for x in data if not x.get("pull_request")]
            return _ok({"count": len(issues), "issues": issues})
        except Exception as exc:
            return _err_of(exc, "列出 issue")

    @channel_tool(group=_GROUP, description="列出仓库的 Pull Request")
    async def list_prs(self, repo: str, state: str = "open", limit: int = 20) -> str:
        try:
            repo = _valid_repo(repo)
            data = (await self._require_client().get(
                f"/repos/{repo}/pulls",
                params={"state": state, "per_page": min(max(int(limit), 1), 50)},
            )).data or []
            prs = [{
                "number": x.get("number"), "title": x.get("title"), "state": x.get("state"),
                "draft": x.get("draft"), "user": (x.get("user") or {}).get("login"),
                "head": (x.get("head") or {}).get("ref"), "base": (x.get("base") or {}).get("ref"),
                "created_at": x.get("created_at"), "url": x.get("html_url"),
            } for x in data]
            return _ok({"count": len(prs), "pull_requests": prs})
        except Exception as exc:
            return _err_of(exc, "列出 PR")

    @channel_tool(group=_GROUP, description="列出仓库近期 commit")
    async def list_commits(self, repo: str, branch: str = "", since_hours: int = 72,
                           limit: int = 20) -> str:
        try:
            repo = _valid_repo(repo)
            from datetime import datetime, timedelta, timezone
            params: Dict[str, Any] = {"per_page": min(max(int(limit), 1), 50)}
            if branch.strip():
                params["sha"] = branch.strip()
            if since_hours > 0:
                since = datetime.now(timezone.utc) - timedelta(hours=int(since_hours))
                params["since"] = since.strftime("%Y-%m-%dT%H:%M:%SZ")
            data = (await self._require_client().get(f"/repos/{repo}/commits", params=params)).data or []
            commits = [{
                "sha": str(x.get("sha") or "")[:8],
                "message": str((x.get("commit") or {}).get("message") or "").splitlines()[0][:80],
                "author": ((x.get("commit") or {}).get("author") or {}).get("name"),
                "date": ((x.get("commit") or {}).get("author") or {}).get("date"),
                "url": x.get("html_url"),
            } for x in data]
            return _ok({"count": len(commits), "commits": commits})
        except Exception as exc:
            return _err_of(exc, "列出 commit")

    @channel_tool(group=_GROUP, description="查看 PR 的 diff(截断返回;大 diff 给文件清单+头尾)")
    async def get_pr_diff(self, repo: str, number: int, max_chars: int = 8000) -> str:
        try:
            repo = _valid_repo(repo)
            resp = await self._require_client().get(
                f"/repos/{repo}/pulls/{int(number)}",
                headers={"Accept": "application/vnd.github.diff"},
            )
            diff = resp.data if isinstance(resp.data, str) else ""
            total = len(diff)
            limit = max(int(max_chars), 500)
            truncated = total > limit
            if truncated:
                half = limit // 2
                diff = diff[:half] + f"\n…(中间省略,共 {total} 字符)…\n" + diff[-half:]
            return _ok({"diff": diff, "truncated": truncated, "total_chars": total})
        except Exception as exc:
            return _err_of(exc, "获取 PR diff")

    @channel_tool(group=_GROUP, description="查看仓库 GitHub Actions 工作流运行状态(CI)")
    async def get_workflow_runs(self, repo: str, branch: str = "", status: str = "",
                                limit: int = 10) -> str:
        try:
            repo = _valid_repo(repo)
            params: Dict[str, Any] = {"per_page": min(max(int(limit), 1), 30)}
            if branch.strip():
                params["branch"] = branch.strip()
            if status.strip():
                params["status"] = status.strip()
            data = (await self._require_client().get(
                f"/repos/{repo}/actions/runs", params=params,
            )).data or {}
            runs = [{
                "name": x.get("name"), "status": x.get("status"), "conclusion": x.get("conclusion"),
                "branch": x.get("head_branch"), "event": x.get("event"),
                "actor": (x.get("actor") or {}).get("login"),
                "created_at": x.get("created_at"), "url": x.get("html_url"),
            } for x in (data.get("workflow_runs") or [])]
            return _ok({"count": len(runs), "total": data.get("total_count"), "runs": runs})
        except Exception as exc:
            return _err_of(exc, "查询工作流")

    @channel_tool(group=_GROUP, description="跨仓库搜索 issue/PR(GitHub 搜索语法)")
    async def search_issues(self, query: str, limit: int = 10) -> str:
        try:
            if not query.strip():
                return _err("query 不能为空")
            data = (await self._require_client().get(
                "/search/issues", params={"q": query, "per_page": min(max(int(limit), 1), 30)},
            )).data or {}
            items = [{
                "repo": (x.get("repository_url") or "").removeprefix("https://api.github.com/repos/"),
                "number": x.get("number"), "title": x.get("title"), "state": x.get("state"),
                "is_pr": bool(x.get("pull_request")), "url": x.get("html_url"),
            } for x in (data.get("items") or [])]
            return _ok({"count": len(items), "total": data.get("total_count"), "items": items})
        except Exception as exc:
            return _err_of(exc, "搜索 issue")

    @channel_tool(group=_GROUP, description="查看仓库 release(默认最新)")
    async def get_release(self, repo: str, tag: str = "") -> str:
        try:
            repo = _valid_repo(repo)
            path = f"/repos/{repo}/releases/tags/{tag.strip()}" if tag.strip() else f"/repos/{repo}/releases/latest"
            rel = (await self._require_client().get(path)).data or {}
            return _ok({
                "tag": rel.get("tag_name"), "name": rel.get("name"),
                "published_at": rel.get("published_at"), "prerelease": rel.get("prerelease"),
                "body": sanitize_body(rel.get("body"), 2000), "url": rel.get("html_url"),
            })
        except Exception as exc:
            return _err_of(exc, "查询 release")

    @channel_tool(group=_GROUP, description="查看 GitHub API 限流配额余额")
    async def get_rate_limit(self) -> str:
        try:
            data = (await self._require_client().get("/rate_limit")).data or {}
            core = (data.get("resources") or {}).get("core") or {}
            search = (data.get("resources") or {}).get("search") or {}
            return _ok({
                "core": {"limit": core.get("limit"), "remaining": core.get("remaining"),
                         "reset_at": core.get("reset")},
                "search": {"limit": search.get("limit"), "remaining": search.get("remaining"),
                           "reset_at": search.get("reset")},
            })
        except Exception as exc:
            return _err_of(exc, "查询配额")

    @channel_tool(group=_GROUP, description="读取今日 GitHub 事件汇总(静默事件计数与摘要)")
    async def get_daily_digest(self) -> str:
        today = time.strftime("%Y-%m-%d")
        return _ok({
            "date": today,
            "counts": self._stats.today_counts(day=today),
            "buffer": self._digest.snapshot(days=2),
        })

    @channel_tool(group=_GROUP, description="列出当前监听的 GitHub 仓库订阅清单")
    async def list_subscriptions(self) -> str:
        repos = [sub.model_dump() for sub in self.config.repos]
        return _ok({"count": len(repos), "repos": repos})

    # ==================================================================
    # 订阅自助(AUTO_ALLOW;写配置走 set_channel_config 唯一入口,热更生效)
    # ==================================================================

    @channel_tool(group=_GROUP, description="订阅监听一个 GitHub 仓库(已订阅则更新配置)")
    async def subscribe_repo(self, repo: str, events: str = "", branches: str = "",
                             local_path: str = "", poll_interval_sec: int = 0,
                             priority_boost: bool = False) -> str:
        try:
            repo = _valid_repo(repo)
            from agent.channel.config import set_channel_config

            current = [sub.model_dump() for sub in self.config.repos]
            owner, name = repo.split("/", 1)
            entry: Dict[str, Any] = {
                "owner": owner, "repo": name,
                "events": [e.strip() for e in events.split(",") if e.strip()],
                "branches": [b.strip() for b in branches.split(",") if b.strip()],
                "poll_interval_sec": max(int(poll_interval_sec), 0),
                "priority_boost": bool(priority_boost),
                "local_path": local_path.strip(),
                "digest": True,
            }
            replaced = False
            for i, old in enumerate(current):
                if f"{old.get('owner')}/{old.get('repo')}".lower() == repo.lower():
                    # 更新时保留未显式给出的旧值
                    merged = {**old, **{k: v for k, v in entry.items() if v not in ("", [], 0)}}
                    current[i] = merged
                    replaced = True
                    break
            if not replaced:
                current.append(entry)
            # 先校验再写(非法值直接拒绝,不污染配置)
            validated = [RepoSubscription.model_validate(x).model_dump() for x in current]
            set_channel_config("github", repos=validated)
            return _ok({
                "subscribed": repo, "updated": replaced,
                "hint": "订阅已生效(轮询下一拍开始;首次轮询只播种不重放历史)",
            })
        except Exception as exc:
            return _err_of(exc, "订阅仓库")

    @channel_tool(group=_GROUP, description="取消监听一个 GitHub 仓库")
    async def unsubscribe_repo(self, repo: str) -> str:
        try:
            repo = _valid_repo(repo)
            from agent.channel.config import set_channel_config

            current = [sub.model_dump() for sub in self.config.repos]
            kept = [x for x in current if f"{x.get('owner')}/{x.get('repo')}".lower() != repo.lower()]
            if len(kept) == len(current):
                return _err(f"未订阅该仓库: {repo}")
            set_channel_config("github", repos=kept)
            return _ok({"unsubscribed": repo})
        except Exception as exc:
            return _err_of(exc, "取消订阅")

    # ==================================================================
    # 写组(sensitive + allow_write 配置门控;reaction 除外)
    # ==================================================================

    def _write_gate(self) -> str:
        """写操作配置门控:返回空串放行,否则返回错误 JSON。"""
        if not bool(getattr(self.config, "allow_write", False)):
            return _err("GitHub 写操作未启用(频道配置 allow_write=false);"
                        "请主人在频道配置中开启后重试")
        return ""

    @channel_tool(group=_GROUP, sensitive=True, description="在 issue/PR 下发表评论(写操作,需审批)")
    async def create_comment(self, repo: str, number: int, body: str) -> str:
        if gate := self._write_gate():
            return gate
        try:
            repo = _valid_repo(repo)
            if not body.strip():
                return _err("评论内容不能为空")
            if len(body) > 4000:
                return _err("评论内容过长(>4000 字符),请精简")
            data = (await self._require_client().post(
                f"/repos/{repo}/issues/{int(number)}/comments", json_body={"body": body},
            )).data or {}
            return _ok({"comment_id": data.get("id"), "url": data.get("html_url")})
        except Exception as exc:
            return _err_of(exc, "发表评论")

    @channel_tool(group=_GROUP, sensitive=True,
                  description="给 issue/PR/评论添加表情回应(+1/-1/laugh/hooray/confused/heart/rocket/eyes)")
    async def react(self, repo: str, number: int, reaction: str = "+1", comment_id: int = 0) -> str:
        try:
            repo = _valid_repo(repo)
            allowed = {"+1", "-1", "laugh", "hooray", "confused", "heart", "rocket", "eyes"}
            if reaction not in allowed:
                return _err(f"非法 reaction: {reaction}(可选 {sorted(allowed)})")
            if int(comment_id) > 0:
                path = f"/repos/{repo}/issues/comments/{int(comment_id)}/reactions"
            else:
                path = f"/repos/{repo}/issues/{int(number)}/reactions"
            data = (await self._require_client().post(path, json_body={"content": reaction})).data or {}
            return _ok({"reaction_id": data.get("id"), "content": data.get("content")})
        except Exception as exc:
            return _err_of(exc, "添加表情回应")

    @channel_tool(group=_GROUP, sensitive=True, description="在仓库创建 issue(写操作,需审批)")
    async def create_issue(self, repo: str, title: str, body: str = "", labels: str = "") -> str:
        if gate := self._write_gate():
            return gate
        try:
            repo = _valid_repo(repo)
            if not title.strip():
                return _err("标题不能为空")
            payload: Dict[str, Any] = {"title": title, "body": body}
            label_list = [x.strip() for x in labels.split(",") if x.strip()]
            if label_list:
                payload["labels"] = label_list
            data = (await self._require_client().post(
                f"/repos/{repo}/issues", json_body=payload,
            )).data or {}
            return _ok({"number": data.get("number"), "url": data.get("html_url")})
        except Exception as exc:
            return _err_of(exc, "创建 issue")

    @channel_tool(group=_GROUP, sensitive=True, description="给 issue/PR 打标签(写操作,需审批)")
    async def add_label(self, repo: str, number: int, labels: str) -> str:
        if gate := self._write_gate():
            return gate
        try:
            repo = _valid_repo(repo)
            label_list = [x.strip() for x in labels.split(",") if x.strip()]
            if not label_list:
                return _err("labels 不能为空(逗号分隔)")
            data = (await self._require_client().post(
                f"/repos/{repo}/issues/{int(number)}/labels", json_body={"labels": label_list},
            )).data or []
            return _ok({"labels": [x.get("name") for x in data if isinstance(x, dict)]})
        except Exception as exc:
            return _err_of(exc, "打标签")

    @channel_tool(group=_GROUP, sensitive=True, description="关闭 issue(写操作,需审批;可选附带评论)")
    async def close_issue(self, repo: str, number: int, comment: str = "") -> str:
        if gate := self._write_gate():
            return gate
        try:
            repo = _valid_repo(repo)
            if comment.strip():
                await self._require_client().post(
                    f"/repos/{repo}/issues/{int(number)}/comments",
                    json_body={"body": comment.strip()[:4000]},
                )
            data = (await self._require_client().patch(
                f"/repos/{repo}/issues/{int(number)}", json_body={"state": "closed"},
            )).data or {}
            return _ok({"number": data.get("number"), "state": data.get("state"),
                        "url": data.get("html_url")})
        except Exception as exc:
            return _err_of(exc, "关闭 issue")


def render_poll_item(raw: Dict[str, Any], *, body_limit: int = 200) -> Dict[str, Any]:
    """Events API 条目 → 一行摘要(list_events 的 API 兜底渲染,不进管线)。"""
    event = render_event(from_poll_api(raw), body_limit=body_limit)
    if event is None:
        return {"type": raw.get("type"), "id": raw.get("id"), "skipped": True}
    return {
        "id": event.event_id, "name": event.event_name, "action": event.action,
        "title": event.title, "url": event.url, "priority": event.priority.value,
        "ts": event.occurred_at,
    }
