"""经配置开关和统一审批执行 GitHub 写操作。"""
from __future__ import annotations

from typing import Any, Dict

from agent.channel.channel_types import _ok
from agent.channel.tool_bridge import channel_tool

from .tool_common import GitHubToolHost, _err, _err_of, _valid_repo

_GROUP = "github"


class GitHubWriteTools(GitHubToolHost):
    """GitHub 评论、议题与标签写操作。"""

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
            await self._remember_comment(repo, data.get("id"))
            return _ok({"comment_id": data.get("id"), "url": data.get("html_url")})
        except Exception as exc:
            return _err_of(exc, "发表评论")

    @channel_tool(group=_GROUP, sensitive=True,
                  description="给 issue/PR/评论添加表情回应(+1/-1/laugh/hooray/confused/heart/rocket/eyes)")
    async def react(self, repo: str, number: int, reaction: str = "+1", comment_id: int = 0) -> str:
        if gate := self._write_gate():
            return gate
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
    async def create_issue(self, repo: str, title: str, body: str = "", labels: list[str] | None = None) -> str:
        if gate := self._write_gate():
            return gate
        try:
            repo = _valid_repo(repo)
            if not title.strip():
                return _err("标题不能为空")
            payload: Dict[str, Any] = {"title": title, "body": body}
            label_list = [x.strip() for x in (labels or []) if x.strip()]
            if label_list:
                payload["labels"] = label_list
            data = (await self._require_client().post(
                f"/repos/{repo}/issues", json_body=payload,
            )).data or {}
            return _ok({"number": data.get("number"), "url": data.get("html_url")})
        except Exception as exc:
            return _err_of(exc, "创建 issue")

    @channel_tool(group=_GROUP, sensitive=True, description="给 issue/PR 打标签(写操作,需审批)")
    async def add_label(self, repo: str, number: int, labels: list[str]) -> str:
        if gate := self._write_gate():
            return gate
        try:
            repo = _valid_repo(repo)
            label_list = [x.strip() for x in (labels or []) if x.strip()]
            if not label_list:
                return _err("labels 数组不能为空")
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
                response = await self._require_client().post(
                    f"/repos/{repo}/issues/{int(number)}/comments",
                    json_body={"body": comment.strip()[:4000]},
                )
                await self._remember_comment(repo, (response.data or {}).get("id"))
            data = (await self._require_client().patch(
                f"/repos/{repo}/issues/{int(number)}", json_body={"state": "closed"},
            )).data or {}
            return _ok({"number": data.get("number"), "state": data.get("state"),
                        "url": data.get("html_url")})
        except Exception as exc:
            return _err_of(exc, "关闭 issue")
