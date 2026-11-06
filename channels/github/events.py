"""GitHub 事件规范化、订阅匹配和上下文提取。"""

from __future__ import annotations

import fnmatch
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from .event_models import EventPriority as EventPriority
from .event_models import GitHubEvent as GitHubEvent
from .event_models import RawEvent as RawEvent

POLL_TYPE_MAP: Dict[str, str] = {
    "PushEvent": "push",
    "IssuesEvent": "issues",
    "IssueCommentEvent": "issue_comment",
    "PullRequestEvent": "pull_request",
    "PullRequestReviewEvent": "pull_request_review",
    "PullRequestReviewCommentEvent": "pull_request_review_comment",
    "WatchEvent": "watch",
    "ForkEvent": "fork",
    "ReleaseEvent": "release",
    "CreateEvent": "create",
    "DeleteEvent": "delete",
    "CommitCommentEvent": "commit_comment",
    "DiscussionEvent": "discussion",
    "MemberEvent": "member",
    "PublicEvent": "public",
    "GollumEvent": "gollum",
}


def from_webhook(event_name: str, payload: Dict[str, Any], delivery_id: str = "") -> RawEvent:
    """webhook 请求体 → RawEvent。"""
    repo = str((payload.get("repository") or {}).get("full_name") or "")
    subject: dict[str, Any] = next((payload[key] for key in ("comment", "review", "release", "issue", "pull_request", "discussion", "workflow_run")
                    if isinstance(payload.get(key), dict)), {})
    occurred = subject.get("submitted_at") or subject.get("published_at") or subject.get("updated_at") or subject.get("created_at")
    if not repo:
        organization = payload.get("organization") or {}
        installation = payload.get("installation") or {}
        repo = "@" + str(organization.get("login") or installation.get("id") or "account")
    sender = payload.get("sender") or {}
    return RawEvent(
        name=event_name.strip().lower(),
        action=str(payload.get("action") or "").strip().lower(),
        repo_full_name=repo,
        actor=str(sender.get("login") or ""),
        occurred_at=_parse_time(occurred or payload.get("created_at") or payload.get("updated_at")),
        payload=payload,
        event_id=delivery_id,
        source="webhook",
    )


def from_poll_api(raw: Dict[str, Any]) -> RawEvent:
    """Events API 条目 → RawEvent(type 驼峰映射;repo/actor 在顶层)。"""
    api_type = str(raw.get("type") or "")
    name = POLL_TYPE_MAP.get(api_type, api_type[:-5].lower() if api_type.endswith("Event") else api_type.lower())
    payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
    assert payload is not None  # isinstance 检查后 mypy 仍判 Optional，收窄为 dict
    action = str(payload.get("action") or "").strip().lower()
    if name == "watch" and not action:
        action = "started"  # Events API 的 WatchEvent 即"被标星",对齐 webhook watch.started
    return RawEvent(
        name=name,
        action=action,
        repo_full_name=str((raw.get("repo") or {}).get("name") or ""),
        actor=str((raw.get("actor") or {}).get("login") or ""),
        occurred_at=_parse_time(raw.get("created_at")),
        payload=payload,
        event_id=str(raw.get("id") or ""),
        source="poll",
    )


def _parse_time(value: Any) -> float:
    """GitHub ISO 时间戳 → epoch;缺失/非法回退当前时间。"""
    if not value:
        return time.time()
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return time.time()



from .formatting import digest_line as digest_line
from .formatting import format_event_message as format_event_message
from .formatting import sanitize_body as sanitize_body


def contains_mention(text: str, login: str) -> bool:
    """正文是否 @提及了指定 login(大小写不敏感,词边界)。 issue/评论提及升级用。"""
    if not text or not login:
        return False
    return re.search(rf"(?<![\w@])@{re.escape(login)}(?![\w-])", text, re.IGNORECASE) is not None




def event_subscribed(sub_events: List[str], default_events: List[str], name: str, action: str) -> bool:
    """订阅匹配三档粒度:'*' 全部 / 'issues' 事件族 / 'issues.opened' 精确 action。"""
    pats = [p.strip().lower() for p in (sub_events or default_events) if p.strip()]
    if not pats:
        return False
    name = name.lower()
    action = action.lower()
    if "*" in pats or name in pats:
        return True
    return bool(action) and f"{name}.{action}" in pats


def branch_allowed(branches: List[str], ref: str) -> bool:
    """push 分支过滤:ref 形如 'refs/heads/main';branches 空 = 全部放行(支持 glob)。"""
    if not branches:
        return True
    branch = ref.removeprefix("refs/heads/")
    return any(fnmatch.fnmatchcase(branch, pat) for pat in branches)


PRIORITY_TABLE: Dict[str, EventPriority] = {
    "release.published": EventPriority.IMMEDIATE,
    "pull_request.review_requested": EventPriority.IMMEDIATE,
    "workflow_run.completed": EventPriority.NORMAL,  # 渲染器按 conclusion 覆盖为 IMMEDIATE/IGNORE
    "repository.renamed": EventPriority.IMMEDIATE,
    "repository.transferred": EventPriority.IMMEDIATE,
    "repository.archived": EventPriority.IMMEDIATE,
    "repository.deleted": EventPriority.IMMEDIATE,
    "dependabot_alert": EventPriority.IMMEDIATE,
    "secret_scanning_alert": EventPriority.IMMEDIATE,
    "code_scanning_alert": EventPriority.IMMEDIATE,
    "push": EventPriority.NORMAL,
    "issues.opened": EventPriority.NORMAL,
    "issues.closed": EventPriority.NORMAL,
    "issues.reopened": EventPriority.NORMAL,
    "issue_comment.created": EventPriority.NORMAL,
    "pull_request.opened": EventPriority.NORMAL,
    "pull_request.reopened": EventPriority.NORMAL,
    "pull_request.closed": EventPriority.NORMAL,
    "pull_request.synchronize": EventPriority.NORMAL,
    "pull_request_review.submitted": EventPriority.NORMAL,
    "discussion": EventPriority.NORMAL,
    "issues.labeled": EventPriority.DIGEST,
    "issues.unlabeled": EventPriority.DIGEST,
    "issues.assigned": EventPriority.DIGEST,
    "issues.edited": EventPriority.DIGEST,
    "pull_request.labeled": EventPriority.DIGEST,
    "pull_request.assigned": EventPriority.DIGEST,
    "pull_request.edited": EventPriority.DIGEST,
    "pull_request_review_comment": EventPriority.DIGEST,
    "commit_comment": EventPriority.DIGEST,
    "star": EventPriority.DIGEST,
    "watch": EventPriority.DIGEST,
    "fork": EventPriority.DIGEST,
    "create": EventPriority.DIGEST,
    "delete": EventPriority.DIGEST,
    "public": EventPriority.DIGEST,
    "member": EventPriority.DIGEST,
    "gollum": EventPriority.DIGEST,
    "issue_comment.edited": EventPriority.DIGEST,
    "issue_comment.deleted": EventPriority.DIGEST,
    "ping": EventPriority.IGNORE,
    "check_run": EventPriority.DIGEST,
    "check_suite": EventPriority.DIGEST,
    "status": EventPriority.DIGEST,
}


def base_priority(name: str, action: str) -> EventPriority:
    """查优先级表(name.action → name → 默认 NORMAL)。"""
    key = f"{name}.{action}" if action else name
    return PRIORITY_TABLE.get(key) or PRIORITY_TABLE.get(name) or EventPriority.NORMAL



from .renderers import RENDERERS, _render_generic


def render_event(raw: RawEvent, *, body_limit: int = 500) -> Optional[GitHubEvent]:
    """渲染入口:分派渲染器并定级(渲染器 override 优先,否则查表)。"""
    if not raw.name or raw.name == "ping":
        return None
    renderer = RENDERERS.get(raw.name, _render_generic)
    try:
        event = renderer(raw, body_limit=body_limit)
    except Exception:
        event = _render_generic(raw, body_limit=body_limit)
    if event is None:
        return None
    event.priority = event.priority_override or base_priority(raw.name, raw.action)
    event.title = sanitize_body(event.title, 500).replace("\n", " ")
    event.extra.update(event_context(raw))
    return event



def event_context(raw: RawEvent) -> Dict[str, Any]:
    """提取定位、回复和认证所需的对象标识。"""
    context: Dict[str, Any] = {"account": raw.account}
    installation = raw.payload.get("installation") or {}
    if installation.get("id"):
        context["installation_id"] = installation["id"]
    for key in ("issue", "pull_request", "discussion", "comment", "review", "workflow_run"):
        value = raw.payload.get(key)
        if not isinstance(value, dict):
            continue
        for name in ("id", "node_id", "number", "path", "line", "side", "start_line", "commit_id", "in_reply_to_id"):
            if value.get(name) is not None:
                context[f"{key}_{name}"] = value[name]
        if key in ("issue", "pull_request", "discussion") and value.get("number"):
            kind = "discussion" if key == "discussion" else "issue"
            context["subject"] = f"{kind}:{value['number']}"
    for key in ("before", "after", "head", "ref"):
        if raw.payload.get(key):
            context[key] = raw.payload[key]
    return context


def mention_text(raw: RawEvent) -> str:
    """提取未截断的标题和正文供提及匹配。"""
    parts: list[str] = []
    for key in ("issue", "pull_request", "discussion", "comment", "review"):
        value = raw.payload.get(key)
        if isinstance(value, dict):
            parts.extend(str(value.get(name) or "") for name in ("title", "body"))
    return "\n".join(parts)
