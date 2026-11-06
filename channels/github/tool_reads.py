"""GitHub 读取工具。

Model Experience: 工具结果包含对象 ID、最新评论与分页信息。
Token effect: 每次查询有条数和正文上限，通常数百至数千 token。
Cache effect: 结果只进入工具消息，不改变稳定前缀。
"""
from __future__ import annotations

from typing import Any, Dict

from agent.channel.channel_types import _ok
from agent.channel.tool_bridge import channel_tool

from .events import sanitize_body
from .tool_common import GitHubToolHost, _err, _err_of, _valid_repo

_GROUP = "github"

import time


class GitHubReadTools(GitHubToolHost):
    """读取仓库事件、议题、变更和运行状态。"""

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
                f"/repos/{repo}/issues/{int(number)}/comments", params={"per_page": 100, "page": max(1, (int(issue.get("comments") or 0) + 99) // 100)},
            )).data or []
            count = int(issue.get("comments") or 0)
            page = max(1, (count + 99) // 100)
            if len(comments) < 5 and page > 1:
                previous = (await client.get(f"/repos/{repo}/issues/{int(number)}/comments",
                                            params={"per_page": 100, "page": page - 1})).data or []
                comments = previous + comments
            return _ok({
                "id": issue.get("id"), "node_id": issue.get("node_id"),
                "number": issue.get("number"), "title": issue.get("title"),
                "state": issue.get("state"), "user": (issue.get("user") or {}).get("login"),
                "labels": [x.get("name") for x in (issue.get("labels") or []) if isinstance(x, dict)],
                "is_pr": bool(issue.get("pull_request")),
                "body": sanitize_body(issue.get("body"), 2000),
                "total_comments": count, "comments_truncated": count > 5,
                "comments": [{
                    "id": c.get("id"), "node_id": c.get("node_id"), "url": c.get("html_url"),
                    "user": (c.get("user") or {}).get("login"),
                    "body": sanitize_body(c.get("body"), 800),
                    "created_at": c.get("created_at"),
                } for c in comments[-5:]],
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
                "message": (str((x.get("commit") or {}).get("message") or "").splitlines() or [""])[0][:80],
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
            limit = min(max(int(max_chars), 500), 30000)
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
                "id": x.get("id"), "head_sha": x.get("head_sha"),
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
        excluded = {sub.full_name.lower() for sub in self.config.repos if not sub.digest}
        return _ok({
            "date": today,
            "counts": {repo: counts for repo, counts in self._stats.today_counts(day=today).items() if repo.lower() not in excluded},
            "buffer": {day: {repo: rows for repo, rows in repos.items() if repo.lower() not in excluded}
                       for day, repos in self._digest.snapshot(days=2).items()},
        })

    @channel_tool(group=_GROUP, description="列出当前监听的 GitHub 仓库订阅清单")
    async def list_subscriptions(self) -> str:
        repos = [sub.model_dump() for sub in self.config.repos]
        return _ok({"count": len(repos), "repos": repos})
