"""频道轮询游标 — 各频道通知/私信轮询共用的防重放游标存储。

每类通知维护已见键的有界集合（LRU），持久化到数据目录（原子写）；
类别首次轮询只播种不派发，避免启动时把历史通知重放给思维。
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Set

from core.log import log


class PollCursorStore:
    """轮询游标：按类别登记已见键，落盘防重启重放。"""

    def __init__(self, path: str, *, channel: str, max_per_kind: int = 200) -> None:
        self._path = path
        self._channel = channel
        self._max_per_kind = max_per_kind
        self._seen: Dict[str, List[str]] = {}
        self._seeded: Set[str] = set()
        self._dirty = False

    def load(self) -> None:
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            self._seen = {str(k): [str(x) for x in v] for k, v in data.get("seen", {}).items()}
            self._seeded = {str(x) for x in data.get("seeded", [])}
        except Exception as exc:
            log(f"{self._channel}: 轮询游标解析失败，按空状态重建: {exc}", "DEBUG", tag="通道")
            self._seen = {}
            self._seeded = set()

    def save(self) -> None:
        """落盘（仅在发生变更后由轮询循环调用）。"""
        if not self._dirty:
            return
        payload = {"seen": self._seen, "seeded": sorted(self._seeded)}
        tmp = self._path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp, self._path)
            self._dirty = False
        except OSError as exc:
            log(f"{self._channel}: 轮询游标保存失败: {exc}", "WARNING", tag="通道")

    def collect_pending(
        self,
        kind: str,
        keys: List[str],
        *,
        seed_key: Optional[str] = None,
        newest_first: bool = True,
    ) -> Optional[List[str]]:
        """登记并返回未见键；首次轮询（seed 未播种）全部播种并返回 None（历史不派发）。

        seed_key 覆盖播种单元（默认 kind，私信会话按 类别:对端 前缀逐会话播种）。
        newest_first=True 表示输入列表最新在前，返回未见键时反转为最旧先派发。
        """
        seed = seed_key or kind
        if seed not in self._seeded:
            for key in keys:
                self.mark(kind, key)
            self._seeded.add(seed)
            self._dirty = True
            return None
        ordered = reversed(keys) if newest_first else keys
        pending = [key for key in ordered if key not in self._seen.get(kind, [])]
        for key in pending:
            self.mark(kind, key)
        return pending

    def is_seeded(self, seed: str) -> bool:
        """播种单元（类别或 类别:对端 前缀）是否已完成首次播种。"""
        return seed in self._seeded

    def is_seen(self, kind: str, key: str) -> bool:
        """键是否已登记过（测试与白名单旁路查询用）。"""
        return key in self._seen.get(kind, [])

    def mark(self, kind: str, key: str) -> None:
        """登记已见键（最新在尾部，超出上限淘汰最旧）。"""
        seen = self._seen.setdefault(kind, [])
        if key in seen:
            return
        seen.append(key)
        del seen[: max(0, len(seen) - self._max_per_kind)]
        self._dirty = True
