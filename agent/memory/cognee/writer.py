"""消费已认领条目：文档投影、幂等替换、删除与失败重试。

只处理一个批次，不创建任务、持有调度锁或修改重建状态。
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

from core.log import log

from ..memory_store import MemoryStore
from ..projection import PROJECTION_VERSION, canonical_tags, memory_is_projectable, projection_dataset
from ..store._shared import entry_projection_payload
from ..store.cognee_queue import ENTRY_KIND_GRAPH_NODE
from .adapters import retryable
from .config import CogneeConfig
from .documents import ProjectionDocument, memory_document
from .errors import error_text
from .ports import ProjectionBackend
from .progress import ProjectionProgress


class ProjectionWriter:
    """由协调器提供互斥窗口；状态仅限当前批次和最后错误。"""

    def __init__(self, store: MemoryStore, client: ProjectionBackend, config: CogneeConfig, *,
                 observe_write: Callable[[], None], after_write: Callable[[str], Awaitable[None]]) -> None:
        self.store, self.client, self.config = store, client, config
        self._observe_write = observe_write
        self._after_write = after_write
        self.last_error = ""
        self.progress = ProjectionProgress()

    @property
    def memory_dataset(self) -> str:
        return projection_dataset(self.client.projection_namespace or self.config.dataset_prefix)

    async def process(self, batch: list[dict[str, Any]]) -> None:
        if not batch:
            return
        with self.progress.batch(batch):
            await self._process(batch)

    async def _process(self, batch: list[dict[str, Any]]) -> None:
        availability = await self.client.initialize()
        if not availability.ready:
            for item in batch:
                await self._fail(item, availability.reason or "Cognee 未就绪")
            return

        deletes = [item for item in batch if item["operation"] == "delete"]
        upserts = [item for item in batch if item["operation"] == "upsert"]
        for item in deletes:
            await self._process_delete(item)

        memory_upserts: dict[str, list[dict[str, Any]]] = defaultdict(list)
        graph_upserts: list[dict[str, Any]] = []
        for item in upserts:
            if item["entry_kind"] != ENTRY_KIND_GRAPH_NODE:
                latest = await self.store.get(item["entry_id"])
                if latest is None:
                    await self._process_delete(item)
                    continue
                item = {**item, "payload": entry_projection_payload(latest, item["entry_id"])}
            if item["entry_kind"] == ENTRY_KIND_GRAPH_NODE:
                if self.config.project_graph_enabled:
                    graph_upserts.append(item)
                else:
                    await self._skip_disabled(item)
            elif not memory_is_projectable(item["payload"]):
                await self._process_delete({**item, "operation": "delete"})
            elif self.config.project_memories_enabled:
                memory_upserts[self.memory_dataset].append(item)
            else:
                await self._skip_disabled(item)
        for dataset_name, items in memory_upserts.items():
            await self._process_upsert_group(dataset_name, items)
        if graph_upserts:
            await self._process_graph_upserts(graph_upserts)

    async def _skip_disabled(self, item: dict[str, Any]) -> None:
        """投影开关关闭的条目直接完成出队（不入 cognee，也不算失败）。"""
        await self.store.complete_cognee_sync(
            item["queue_id"], item["entry_id"], entry_kind=item["entry_kind"],
        )

    async def _process_delete(self, item: dict[str, Any]) -> None:
        self.progress.stage("delete")
        try:
            await self._delete_superseded(item)
            await self.store.complete_cognee_sync(
                item["queue_id"], item["entry_id"],
                entry_kind=item["entry_kind"], delete_mapping=True,
            )
        except Exception as exc:
            await self._fail(item, exc)

    async def _delete_superseded(self, item: dict[str, Any], *, keep: tuple[str, str] = ("", "")) -> None:
        """清理旧映射和失败写入副本；成功提交前保留日志，重复删除幂等。"""
        previous = await self.store.get_cognee_mapping(item["entry_id"], entry_kind=item["entry_kind"])
        targets: set[tuple[str, str]] = set()
        if previous and (previous["dataset_name"], previous["data_id"]) != keep:
            if not previous.get("dataset_id") or not previous.get("data_id"):
                raise RuntimeError("缺少 Cognee dataset/data ID，无法安全删除")
            targets.add((previous["dataset_id"], previous["data_id"]))
        attempts = [row for row in await self.store.projection_state.staged(item["entry_kind"], item["entry_id"])
                    if (row["dataset_name"], row["data_id"]) != keep]
        if attempts:
            datasets = {str(_value(row, "name", "")): str(_value(row, "id", ""))
                        for row in await self.client.list_datasets()}
            for row in attempts:
                if dataset_id := datasets.get(row["dataset_name"]):
                    targets.add((dataset_id, row["data_id"]))
        for dataset_id, data_id in sorted(targets):
            await self.client.delete_data(dataset_id, data_id, delete_dataset_if_empty=False)

    async def _process_upsert_group(
        self,
        dataset_name: str,
        items: list[dict[str, Any]],
    ) -> None:
        entries = [(item, memory_document(item["payload"])) for item in items]
        await self._push_projection_group(dataset_name, entries, graph=False)

    @property
    def relations_dataset(self) -> str:
        """当前代际的关系文档分区，归属保留在文档标签及出处。"""
        return projection_dataset(self.client.projection_namespace or self.config.dataset_prefix, graph=True)

    async def _process_graph_upserts(self, items: list[dict[str, Any]]) -> None:
        """读取一致的邻域及出处快照；证据变化重投影，强度变化不重投影。"""
        grouped: dict[str, list[tuple[dict[str, Any], ProjectionDocument]]] = defaultdict(list)
        for item in items:
            projection = await self.store.graph.projection_snapshot(item["entry_id"])
            if projection is None:
                # 节点已归档/不存在：按删除处理
                await self._process_delete({**item, "operation": "delete"})
                continue
            document, fingerprint, attribution = projection
            mapping = await self.store.get_cognee_mapping(
                item["entry_id"], entry_kind=item["entry_kind"],
            )
            if (not item["payload"].get("force") and mapping
                    and str(mapping.get("content_hash") or "") == fingerprint
                    and not await self.store.projection_state.staged(item["entry_kind"], item["entry_id"])):
                # 投影已是最新：仅移除队列条目，映射与投影保持原样
                await self.store.complete_cognee_sync(
                    item["queue_id"],
                    item["entry_id"],
                    entry_kind=item["entry_kind"],
                )
                continue
            dataset = self.relations_dataset
            grouped[dataset].append((item, ProjectionDocument(document, fingerprint, attribution)))
        for dataset_name, entries in grouped.items():
            await self._push_projection_group(dataset_name, entries, graph=True)

    async def _push_projection_group(
        self,
        dataset_name: str,
        entries: list[tuple[dict[str, Any], ProjectionDocument]],
        *,
        graph: bool,
    ) -> None:
        """先完成新投影再替换旧映射；稳定文档 ID 使失败重试不新增副本。"""
        metadata_key = "anelf_graph_node_id" if graph else "anelf_memory_id"
        label_kind = "graph-node" if graph else "memory"
        stage = "prepare"
        started = time.monotonic()
        self.progress.stage(stage, dataset_name=dataset_name)
        try:
            data_items: list[Any] = []
            for item, document in entries:
                payload = item["payload"]
                data_items.append(await self.client.make_data_item(
                    document.text,
                    label=f"anelf-{label_kind}-{item['entry_id']}",
                    dataset_name=dataset_name,
                    node_set=canonical_tags(document.attribution.get("tags", [])),
                    external_metadata={
                        "anelf_projection_version": PROJECTION_VERSION,
                        "anelf_content_hash": document.fingerprint,
                        metadata_key: str(item["entry_id"]),
                        "memory_type": "graph_relation" if graph else str(payload.get("type", "semantic")),
                        "source": "graph" if graph else str(payload.get("source", "")),
                        "anelf_attribution": document.attribution,
                    },
                ))
                data_id = str(_value(data_items[-1], "data_id", ""))
                if not data_id:
                    raise RuntimeError("Cognee 投影缺少稳定文档 ID")
                await self.store.projection_state.stage(item["entry_kind"], item["entry_id"], dataset_name, data_id)
            self._observe_write()
            stage = "add"
            self.progress.stage(stage, dataset_name=dataset_name, timeout=self.config.pipeline_timeout_seconds)
            ingestion = await self.client.add(
                data_items, dataset_name=dataset_name, incremental_loading=True,
            )
            dataset_id = str(_value(ingestion, "dataset_id", ""))
            if not dataset_id:
                raise RuntimeError("Cognee 摄入未返回 dataset_id，保留旧投影")
            stage = "cognify"
            self.progress.stage(stage, dataset_name=dataset_name, dataset_id=dataset_id,
                                timeout=self.config.pipeline_timeout_seconds)
            await self.client.cognify(datasets=[dataset_name], incremental_loading=True)
            self._observe_write()
            stage = "replace_projection"
            self.progress.stage(stage, dataset_name=dataset_name)
            for (item, document), data_item in zip(entries, data_items, strict=True):
                data_id = str(_value(data_item, "data_id", ""))
                if not data_id:
                    raise RuntimeError("Cognee 投影缺少稳定文档 ID")
                try:
                    await self._delete_superseded(item, keep=(dataset_name, data_id))
                except Exception as exc:
                    await self._fail(item, exc)
                    continue
                await self.store.complete_cognee_sync(
                    item["queue_id"], item["entry_id"], entry_kind=item["entry_kind"],
                    dataset_name=dataset_name, dataset_id=dataset_id, data_id=data_id,
                    content_hash=document.fingerprint,
                )
            self.progress.stage("enhance", dataset_name=dataset_name, timeout=self.config.pipeline_timeout_seconds)
            await self._after_write(dataset_name)
            self.last_error = ""
        except Exception as exc:
            self.last_error = error_text(exc)
            log(
                f"Cognee 同步失败 [stage:{stage}][dataset:{dataset_name}] "
                f"{len(entries)} 条，耗时 {time.monotonic() - started:.1f}s: {self.last_error}",
                "WARNING", tag="思维",
            )
            for item, _document in entries:
                await self._fail(item, exc)

    async def _fail(self, item: dict[str, Any], error: str | Exception) -> None:
        attempts = int(item.get("attempts", 0))
        delay = min(300.0, 2.0 ** min(attempts, 8))
        await self.store.fail_cognee_sync(
            item["queue_id"],
            error_text(error) if isinstance(error, Exception) else error,
            max_retries=self.config.max_retries if not isinstance(error, Exception) or retryable(error) else 1,
            retry_delay_seconds=delay,
        )



def _value(item: Any, key: str, default: Any) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)
