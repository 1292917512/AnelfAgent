"""SQLite 权威存储到 Cognee 的持久化投影协调器。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import defaultdict
from typing import Any, Optional

from core.log import log

from ..memory_store import MemoryStore
from .client import CogneeClient
from .config import CogneeConfig
from .types import CogneeSyncStatus

_SAFE_DATASET_RE = re.compile(r"[^a-zA-Z0-9_-]+")


class CogneeCoordinator:
    """消费 MemoryStore outbox，并维护 Cognee 数据映射。"""

    def __init__(
        self,
        store: MemoryStore,
        client: CogneeClient,
        config: CogneeConfig,
    ) -> None:
        self.store = store
        self.client = client
        self.config = config.normalized()
        self._task: Optional[asyncio.Task[None]] = None
        self._wake = asyncio.Event()
        self._closing = False
        self._last_error = ""

    async def start(self) -> None:
        self.store.set_cognee_projection_enabled(
            self.config.enabled and self.config.sync_enabled,
        )
        if not self.config.enabled:
            return
        availability = await self.client.initialize()
        if not availability.ready:
            self._last_error = availability.reason
        if self.config.sync_enabled and self._task is None:
            self._task = asyncio.create_task(
                self._worker(),
                name="memory.cognee.sync",
            )

    async def close(self) -> None:
        self._closing = True
        self._wake.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def wake(self) -> None:
        self._wake.set()

    async def status(self) -> CogneeSyncStatus:
        counts = await self.store.get_cognee_sync_status()
        return CogneeSyncStatus(
            enabled=self.config.enabled and self.config.sync_enabled,
            running=bool(self._task and not self._task.done()),
            pending=counts["pending"],
            failed=counts["failed"],
            synced=counts["synced"],
            last_error=self._last_error,
        )

    async def retry_failed(self) -> int:
        count = await self.store.retry_failed_cognee_sync()
        self.wake()
        return count

    async def backfill(self, *, limit: int = 0, dry_run: bool = True) -> dict[str, int | bool]:
        if dry_run:
            total = await self.store.count()
            return {"dry_run": True, "eligible": min(total, limit) if limit > 0 else total}
        count = await self.store.enqueue_cognee_backfill(limit=limit)
        self.wake()
        return {"dry_run": False, "queued": count}

    async def improve(self, dataset_name: str) -> Any:
        return await self.client.improve(dataset=dataset_name)

    async def _worker(self) -> None:
        while not self._closing:
            try:
                batch = await self.store.claim_cognee_sync_batch(
                    self.config.sync_batch_size,
                )
                if batch:
                    await self._process_batch(batch)
                    continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = str(exc)
                log(f"Cognee 同步循环异常: {exc}", "WARNING", tag="思维")

            self._wake.clear()
            try:
                await asyncio.wait_for(
                    self._wake.wait(),
                    timeout=self.config.sync_interval_seconds,
                )
            except asyncio.TimeoutError:
                pass

    async def _process_batch(self, batch: list[dict[str, Any]]) -> None:
        availability = await self.client.initialize()
        if not availability.ready:
            for item in batch:
                await self._fail(item, availability.reason or "Cognee 未就绪")
            return

        deletes = [item for item in batch if item["operation"] == "delete"]
        upserts = [item for item in batch if item["operation"] == "upsert"]
        for item in deletes:
            await self._process_delete(item)

        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in upserts:
            grouped[self.dataset_for_payload(item["payload"])].append(item)
        for dataset_name, items in grouped.items():
            await self._process_upsert_group(dataset_name, items)

    async def _process_delete(self, item: dict[str, Any]) -> None:
        mapping = await self.store.get_cognee_mapping(item["memory_id"])
        if not mapping:
            await self.store.complete_cognee_sync(
                item["queue_id"], item["memory_id"], delete_mapping=True,
            )
            return
        if not mapping.get("dataset_id") or not mapping.get("data_id"):
            await self._fail(item, "缺少 Cognee dataset/data ID，无法安全删除")
            return
        try:
            await self.client.delete_data(
                mapping["dataset_id"],
                mapping["data_id"],
                delete_dataset_if_empty=False,
            )
            await self.store.complete_cognee_sync(
                item["queue_id"], item["memory_id"], delete_mapping=True,
            )
        except Exception as exc:
            await self._fail(item, str(exc))

    async def _process_upsert_group(
        self,
        dataset_name: str,
        items: list[dict[str, Any]],
    ) -> None:
        try:
            data_items: list[Any] = []
            for item in items:
                mapping = await self.store.get_cognee_mapping(item["memory_id"])
                if mapping and mapping.get("dataset_id") and mapping.get("data_id"):
                    await self.client.delete_data(
                        mapping["dataset_id"],
                        mapping["data_id"],
                        delete_dataset_if_empty=False,
                    )
                payload = item["payload"]
                data_items.append(await self.client.make_data_item(
                    self._render_memory(payload),
                    label=f"anelf-memory-{item['memory_id']}",
                    external_metadata={
                        "anelf_memory_id": str(item["memory_id"]),
                        "memory_type": str(payload.get("type", "semantic")),
                        "source": str(payload.get("source", "")),
                    },
                ))

            await self.client.add(
                data_items,
                dataset_name=dataset_name,
                incremental_loading=True,
            )
            await self.client.cognify(datasets=[dataset_name], incremental_loading=True)
            await self.client.improve(dataset=dataset_name)
            identifiers = await self._resolve_data_ids(dataset_name)

            for item in items:
                ids = identifiers.get(str(item["memory_id"]))
                if not ids:
                    raise RuntimeError(f"无法解析 memory {item['memory_id']} 的 Cognee 数据 ID")
                await self.store.complete_cognee_sync(
                    item["queue_id"],
                    item["memory_id"],
                    dataset_name=dataset_name,
                    dataset_id=ids[0],
                    data_id=ids[1],
                )
            self._last_error = ""
        except Exception as exc:
            self._last_error = str(exc)
            for item in items:
                await self._fail(item, str(exc))

    async def _resolve_data_ids(self, dataset_name: str) -> dict[str, tuple[str, str]]:
        datasets = await self.client.list_datasets()
        dataset = next(
            (item for item in datasets if str(_value(item, "name", "")) == dataset_name),
            None,
        )
        if dataset is None:
            return {}
        dataset_id = str(_value(dataset, "id", ""))
        records = await self.client.list_data(_value(dataset, "id", ""))
        result: dict[str, tuple[str, str]] = {}
        for record in records:
            metadata = _value(record, "external_metadata", {})
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except json.JSONDecodeError:
                    metadata = {}
            if not isinstance(metadata, dict):
                continue
            memory_id = str(metadata.get("anelf_memory_id", ""))
            data_id = str(_value(record, "id", ""))
            if memory_id and data_id:
                result[memory_id] = (dataset_id, data_id)
        return result

    async def _fail(self, item: dict[str, Any], error: str) -> None:
        attempts = int(item.get("attempts", 0))
        delay = min(300.0, 2.0 ** min(attempts, 8))
        await self.store.fail_cognee_sync(
            item["queue_id"],
            error,
            max_retries=self.config.max_retries,
            retry_delay_seconds=delay,
        )

    def dataset_for_payload(self, payload: dict[str, Any]) -> str:
        scope_type = "global"
        scope_id = ""
        for tag in payload.get("tags", []):
            if not isinstance(tag, str) or ":" not in tag:
                continue
            key, value = tag.split(":", 1)
            if key in {"user", "group"} and value:
                scope_type, scope_id = key, value
                break
        if not scope_id:
            return f"{self.config.dataset_prefix}_global"
        digest = hashlib.sha256(scope_id.encode("utf-8")).hexdigest()[:16]
        prefix = _SAFE_DATASET_RE.sub("_", self.config.dataset_prefix)
        return f"{prefix}_{scope_type}_{digest}"

    @staticmethod
    def _render_memory(payload: dict[str, Any]) -> str:
        tags = ", ".join(str(tag) for tag in payload.get("tags", []))
        metadata = json.dumps(payload.get("metadata", {}), ensure_ascii=False, sort_keys=True)
        return (
            f"Memory type: {payload.get('type', 'semantic')}\n"
            f"Source: {payload.get('source', '')}\n"
            f"Importance: {payload.get('importance', 0.5)}\n"
            f"Tags: {tags}\n"
            f"Metadata: {metadata}\n\n"
            f"{payload.get('content', '')}"
        )


def _value(item: Any, key: str, default: Any) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)
