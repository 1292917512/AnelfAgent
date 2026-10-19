"""执行过程的连接级增量编码：先发基线，后续只传变化字段和新增正文。

模型不可见；不增加模型 token，也不改变模型上下文与缓存前缀。
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from services.workspace_activity import MAX_RUNS


class ActivityStreamEncoder:
    """对实际出队的完整快照编码，基线随连接释放，队列合并不丢增量。"""

    def __init__(self) -> None:
        self._epoch = ""
        self._runs: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def encode(self, event: dict[str, Any]) -> dict[str, Any]:
        """保留非过程事件；过程首次发送完整基线，之后按前一发送修订生成增量。"""
        kind = event.get("event")
        if kind not in {"activity", "activity_end", "activity_snapshot"}:
            return event
        epoch = str(event["epoch"])
        if epoch != self._epoch or kind == "activity_snapshot":
            self._epoch = epoch
            self._runs.clear()
        if kind == "activity_snapshot":
            for run in event["runs"]:
                self._remember(run)
            return event

        run = event["run"]
        previous = self._runs.get(run["id"])
        self._remember(run)
        if previous is None:
            return event

        entries = {entry["id"]: entry for entry in previous["entries"]}
        changed = []
        appended = []
        for entry in run["entries"]:
            old = entries.get(entry["id"])
            if old == entry:
                continue
            if (old is not None and entry["kind"] in {"text", "thinking"}
                    and entry["content"].startswith(old["content"])
                    and {key: value for key, value in entry.items() if key != "content"}
                    == {key: value for key, value in old.items() if key != "content"}):
                appended.append({"id": entry["id"], "text": entry["content"][len(old["content"]):]})
            else:
                changed.append(entry)

        patch: dict[str, Any] = {
            "event": "activity_delta", "epoch": epoch, "base_revision": previous["revision"],
            "run": {key: value for key, value in run.items()
                    if key in {"id", "revision"} or (key != "entries" and value != previous.get(key))},
            "entries": changed, "appends": appended,
        }
        order = [entry["id"] for entry in run["entries"]]
        if order != list(entries):
            patch["entry_order"] = order
        return patch

    def _remember(self, run: dict[str, Any]) -> None:
        """有界保留已经发送的不可变快照。"""
        self._runs[run["id"]] = run
        self._runs.move_to_end(run["id"])
        while len(self._runs) > MAX_RUNS:
            self._runs.popitem(last=False)
