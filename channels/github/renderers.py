"""GitHub 事件摘要的确定性渲染器。"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from .event_models import EventPriority, GitHubEvent, RawEvent
from .formatting import sanitize_body

Renderer = Callable[..., Optional[GitHubEvent]]


def _make(raw: RawEvent, title: str, *, url: str = "", body: str = "",
          priority_override: Optional[EventPriority] = None) -> GitHubEvent:
    return GitHubEvent(
        repo_full_name=raw.repo_full_name,
        event_name=raw.name,
        action=raw.action,
        actor=raw.actor,
        occurred_at=raw.occurred_at,
        title=title,
        url=url,
        body_digest=body,
        event_id=raw.event_id,
        source=raw.source,
        priority_override=priority_override,
    )


def _first_line(text: Any, limit: int = 80) -> str:
    line = str(text or "").strip().splitlines()[0] if str(text or "").strip() else ""
    return line[:limit] + ("…" if len(line) > limit else "")


def _render_push(raw: RawEvent, *, body_limit: int) -> Optional[GitHubEvent]:
    p = raw.payload
    ref = str(p.get("ref") or "")
    branch = ref.removeprefix("refs/heads/")
    commits = p.get("commits") or []
    size = int(p.get("size") or p.get("distinct_size") or len(commits))
    head = str(p.get("after") or p.get("head") or "")
    before = str(p.get("before") or "")
    if size == 0 and not head and not p.get("deleted") and not p.get("created"):
        return None
    lines = []
    for c in commits[:3]:
        msg = _first_line(c.get("message"), 60)
        author = (c.get("author") or {}).get("name") or (c.get("author") or {}).get("username") or ""
        lines.append(f"· {msg}" + (f"({author})" if author else ""))
    if size > 3:
        lines.append(f"· …其余 {size - 3} 个 commit")
    detail = f"{size} 个 commit" if size else (f"{before[:8]} → {head[:8]}" if head else "分支已删除")
    title = f"push {branch} · {detail}"
    event = _make(raw, title, url=str(p.get("compare") or ""), body="\n".join(lines))
    event.extra["branch"] = branch
    event.extra["size"] = size
    return event


def _render_issues(raw: RawEvent, *, body_limit: int) -> Optional[GitHubEvent]:
    issue = raw.payload.get("issue") or {}
    number = issue.get("number") or raw.payload.get("number") or "?"
    title = str(issue.get("title") or "")
    labels = ",".join(str(x.get("name")) for x in (issue.get("labels") or []) if isinstance(x, dict))
    head = f"issue #{number} {title}".strip()
    if labels:
        head += f" [{labels}]"
    return _make(
        raw, f"{head}",
        url=str(issue.get("html_url") or ""),
        body=sanitize_body(issue.get("body"), body_limit) if raw.action == "opened" else "",
    )


def _render_issue_comment(raw: RawEvent, *, body_limit: int) -> Optional[GitHubEvent]:
    comment = raw.payload.get("comment") or {}
    issue = raw.payload.get("issue") or {}
    number = issue.get("number") or "?"
    is_pr = bool(issue.get("pull_request"))
    kind = "PR" if is_pr else "issue"
    return _make(
        raw, f"评论 {kind} #{number} {str(issue.get('title') or '')}".strip(),
        url=str(comment.get("html_url") or ""),
        body=sanitize_body(comment.get("body"), body_limit),
    )


def _render_pull_request(raw: RawEvent, *, body_limit: int) -> Optional[GitHubEvent]:
    pr = raw.payload.get("pull_request") or {}
    number = pr.get("number") or raw.payload.get("number") or "?"
    title = str(pr.get("title") or "")
    base = (pr.get("base") or {}).get("ref") or ""
    head = (pr.get("head") or {}).get("ref") or ""
    override: Optional[EventPriority] = None
    verb = raw.action
    if raw.action == "closed":
        if pr.get("merged"):
            verb = "merged"
            stat = ""
            additions, deletions = pr.get("additions"), pr.get("deletions")
            if isinstance(additions, int) and isinstance(deletions, int):
                stat = f" (+{additions}/-{deletions})"
            verb = f"merged{stat}"
        else:
            override = EventPriority.DIGEST  # 未合并关闭 = 低价值
    branch_info = f" {head}→{base}" if base or head else ""
    body = ""
    if raw.action in ("opened", "reopened", "review_requested"):
        body = sanitize_body(pr.get("body"), body_limit)
        reviewer = (raw.payload.get("requested_reviewer") or pr.get("requested_reviewer") or {}).get("login") or ""
        if raw.action == "review_requested" and reviewer:
            title = f"请求 {reviewer} 审查: {title}"
    if pr.get("draft") and raw.action == "opened":
        title = f"[draft] {title}"
    return _make(
        raw, f"PR #{number} {verb}: {title}{branch_info}",
        url=str(pr.get("html_url") or ""), body=body, priority_override=override,
    )


def _render_pr_review(raw: RawEvent, *, body_limit: int) -> Optional[GitHubEvent]:
    review = raw.payload.get("review") or {}
    pr = raw.payload.get("pull_request") or {}
    state = str(review.get("state") or "")
    state_cn = {"approved": "批准", "changes_requested": "要求修改", "commented": "评论"}.get(state, state)
    return _make(
        raw, f"PR #{pr.get('number', '?')} 审查-{state_cn}: {str(pr.get('title') or '')}".strip(),
        url=str(review.get("html_url") or ""),
        body=sanitize_body(review.get("body"), body_limit),
    )


def _render_release(raw: RawEvent, *, body_limit: int) -> Optional[GitHubEvent]:
    rel = raw.payload.get("release") or {}
    tag = str(rel.get("tag_name") or "")
    name = str(rel.get("name") or "")
    title = f"发布 {tag}" + (f": {name}" if name and name != tag else "")
    return _make(
        raw, title,
        url=str(rel.get("html_url") or ""),
        body=sanitize_body(rel.get("body"), body_limit),
    )


def _render_star(raw: RawEvent, *, body_limit: int) -> Optional[GitHubEvent]:
    verb = "取消星标" if raw.action == "deleted" else "标星"
    return _make(raw, f"{verb} ⭐")


def _render_fork(raw: RawEvent, *, body_limit: int) -> Optional[GitHubEvent]:
    forkee = raw.payload.get("forkee") or {}
    return _make(raw, "fork", url=str(forkee.get("html_url") or ""))


def _render_workflow_run(raw: RawEvent, *, body_limit: int) -> Optional[GitHubEvent]:
    run = raw.payload.get("workflow_run") or {}
    conclusion = str(run.get("conclusion") or "")

    name = str(run.get("name") or "workflow")
    branch = str(run.get("head_branch") or "")
    title = f"CI {conclusion}: {name}" + (f" [{branch}]" if branch else "")
    return _make(
        raw, title,
        url=str(run.get("html_url") or ""),
        priority_override=(EventPriority.IMMEDIATE if conclusion in ("failure", "timed_out", "action_required") else EventPriority.DIGEST),
    )


def _render_repository(raw: RawEvent, *, body_limit: int) -> Optional[GitHubEvent]:
    changes = raw.payload.get("changes") or {}
    old = ((changes.get("old_name") or {}) if isinstance(changes.get("old_name"), dict) else None)
    extra = f"(原 {old.get('name')})" if old else ""
    return _make(raw, f"仓库 {raw.action} {extra}".strip())


def _render_security_alert(raw: RawEvent, *, body_limit: int) -> Optional[GitHubEvent]:
    alert = raw.payload.get("alert") or {}
    kind = {
        "dependabot_alert": "Dependabot",
        "secret_scanning_alert": "密钥泄露扫描",
        "code_scanning_alert": "代码扫描",
    }.get(raw.name, "安全")
    summary = str(alert.get("summary") or alert.get("secret_type") or "")
    return _make(
        raw, f"{kind} 告警 #{alert.get('number', '?')} {summary}".strip(),
        url=str(alert.get("html_url") or ""),
    )


def _render_discussion(raw: RawEvent, *, body_limit: int) -> Optional[GitHubEvent]:
    d = raw.payload.get("discussion") or {}
    category = (d.get("category") or {}).get("name") or ""
    head = f"讨论 #{d.get('number', '?')} {str(d.get('title') or '')}".strip()
    if category:
        head += f" [{category}]"
    return _make(
        raw, head,
        url=str(d.get("html_url") or ""),
        body=sanitize_body(d.get("body"), body_limit) if raw.action == "created" else "",
    )


def _render_create_delete(raw: RawEvent, *, body_limit: int) -> Optional[GitHubEvent]:
    ref_type = str(raw.payload.get("ref_type") or "")
    ref = str(raw.payload.get("ref") or "")
    verb = "创建" if raw.name == "create" else "删除"
    return _make(raw, f"{verb}{ref_type} {ref}".strip())


def _render_generic(raw: RawEvent, *, body_limit: int) -> Optional[GitHubEvent]:
    """未知事件降级渲染(前向兼容 GitHub 新事件):一行摘要。"""
    head = f"{raw.name}.{raw.action}" if raw.action else raw.name
    url = str((raw.payload.get("repository") or {}).get("html_url") or "")
    subject = next((value for key, value in raw.payload.items()
                    if isinstance(value, dict) and key not in ("repository", "sender", "installation")
                    and any(field in value for field in ("body", "title", "name", "html_url"))), {})
    title = str(subject.get("title") or subject.get("name") or "")
    return _make(raw, f"{head} {title}".strip(), url=str(subject.get("html_url") or url),
                 body=sanitize_body(subject.get("body"), body_limit))


def _render_comment(raw: RawEvent, *, body_limit: int) -> Optional[GitHubEvent]:
    """保留代码审查、讨论和提交评论的正文与位置。"""
    comment = raw.payload.get("comment") or {}
    subject = raw.payload.get("pull_request") or raw.payload.get("discussion") or {}
    location = ":".join(str(comment[key]) for key in ("path", "line") if comment.get(key) is not None)
    title = f"{raw.name} #{subject.get('number', comment.get('commit_id', '?'))}"
    if location:
        title += f" · {location}"
    return _make(raw, title, url=str(comment.get("html_url") or subject.get("html_url") or ""),
                 body=sanitize_body(comment.get("body"), body_limit))


RENDERERS: Dict[str, Renderer] = {
    "push": _render_push,
    "issues": _render_issues,
    "issue_comment": _render_issue_comment,
    "pull_request": _render_pull_request,
    "pull_request_review": _render_pr_review,
    "pull_request_review_comment": _render_comment,
    "discussion_comment": _render_comment,
    "commit_comment": _render_comment,
    "release": _render_release,
    "star": _render_star,
    "watch": _render_star,       # watch.started = 标星(旧语义)
    "fork": _render_fork,
    "workflow_run": _render_workflow_run,
    "repository": _render_repository,
    "dependabot_alert": _render_security_alert,
    "secret_scanning_alert": _render_security_alert,
    "code_scanning_alert": _render_security_alert,
    "discussion": _render_discussion,
    "create": _render_create_delete,
    "delete": _render_create_delete,
}
