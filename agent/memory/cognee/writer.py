"""消费已认领条目：文档投影、幂等替换、删除与失败重试。

只处理一个批次，不创建任务、持有调度锁或修改重建状态。

模型看到什么：沿用原有投影正文及 Cognee 提示词，按实际文档变更量划分发布范围。
token 影响：不增加单文档抽取调用；已提交小组不因后续失败重复抽取。
缓存影响：只调整后台投影调度，不触碰对话提示词前缀。
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterator
from typing import Any

from core.log import log

from ..memory_store import MemoryStore
from ..projection import PROJECTION_VERSION, canonical_tags, memory_is_projectable, projection_dataset
from ..projection_documents import MappedDocument, expand_mapping, mapped_documents
from ..store._shared import entry_projection_payload
from ..store.cognee_queue import ENTRY_KIND_GRAPH_NODE
from .adapters import retryable
from .config import CogneeConfig
from .documents import ProjectionDocument, memory_document
from .errors import error_text
from .ports import ProjectionBackend
from .progress import ProjectionProgress
from .wire import InputDocument

ProjectionEntry = tuple[dict[str, Any], ProjectionDocument]


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
        self.last_error = ""
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
                item = {**item, "payload": {**entry_projection_payload(latest, item["entry_id"]),
                                           "force": bool(item["payload"].get("force"))}}
            if item["entry_kind"] == ENTRY_KIND_GRAPH_NODE:
                if self.config.project_graph_enabled:
                    graph_upserts.append(item)
                else:
                    await self._complete_without_projection(item)
            elif not memory_is_projectable(item["payload"]):
                await self._process_delete({**item, "operation": "delete"})
            elif self.config.project_memories_enabled:
                memory_upserts[self.memory_dataset].append(item)
            else:
                await self._complete_without_projection(item)
        for dataset_name, items in memory_upserts.items():
            await self._process_upsert_group(dataset_name, items)
        if graph_upserts:
            await self._process_graph_upserts(graph_upserts)

    async def _complete_without_projection(self, item: dict[str, Any]) -> None:
        """无需投影的条目完成出队，保留已发布映射。"""
        await self.store.complete_cognee_sync(
            item["queue_id"], item["entry_id"], entry_kind=item["entry_kind"],
        )

    async def _process_delete(self, item: dict[str, Any]) -> None:
        with self.progress.batch([item]):
            await self._delete_projection(item)

    async def _delete_projection(self, item: dict[str, Any]) -> None:
        self.progress.stage("delete")
        try:
            targets = await self._superseded_targets(item)
            if targets:
                async with self.client.write_transaction():
                    await self._delete_targets(targets)
            await self.store.complete_cognee_sync(
                item["queue_id"], item["entry_id"],
                entry_kind=item["entry_kind"], delete_mapping=True,
            )
        except Exception as exc:
            await self._fail(item, exc)

    async def _delete_targets(self, targets: set[tuple[str, str]]) -> None:
        """去重并预取本次清理范围，SDK 串行删除及逐项进度均位于原发布事务。"""
        ordered = sorted(targets)
        self.progress.cleanup(len(ordered))
        if not ordered:
            return
        started = time.monotonic()
        await self.client.prepare_deletions(ordered)
        for dataset_id, data_id in ordered:
            await self.client.delete_data(dataset_id, data_id, delete_dataset_if_empty=False)
            self.progress.deleted()
        log(f"Cognee 旧文档清理完成: {len(ordered)} 篇，耗时 {time.monotonic() - started:.1f}s", "DEBUG", tag="存储")

    async def _superseded_targets(
        self, item: dict[str, Any], *, keep: set[tuple[str, str]] | None = None,
    ) -> set[tuple[str, str]]:
        """在协调器互斥窗口内解析实际删除目标；空集合不启动索引写事务。"""
        previous = await self.store.get_cognee_mapping(item["entry_id"], entry_kind=item["entry_kind"])
        keep = keep or set()
        targets: set[tuple[str, str]] = set()
        for document in expand_mapping(previous) if previous else []:
            if (document["dataset_name"], document["data_id"]) not in keep:
                if not document.get("dataset_id") or not document.get("data_id"):
                    raise RuntimeError("缺少 Cognee dataset/data ID，无法安全删除")
                targets.add((document["dataset_id"], document["data_id"]))
        attempts = [row for row in await self.store.projection_state.staged(item["entry_kind"], item["entry_id"])
                    if (row["dataset_name"], row["data_id"]) not in keep]
        if attempts:
            datasets = {str(_value(row, "name", "")): str(_value(row, "id", ""))
                        for row in await self.client.list_datasets()}
            for row in attempts:
                if dataset_id := datasets.get(row["dataset_name"]):
                    targets.add((dataset_id, row["data_id"]))
        return targets

    async def _process_upsert_group(
        self,
        dataset_name: str,
        items: list[dict[str, Any]],
    ) -> None:
        entries: list[ProjectionEntry] = []
        for item in items:
            document = memory_document(item["payload"])
            mapping = await self.store.get_cognee_mapping(item["entry_id"], entry_kind=item["entry_kind"])
            if (not item["payload"].get("force") and mapping
                    and mapping.get("content_hash") == document.fingerprint
                    and not await self.store.projection_state.staged(item["entry_kind"], item["entry_id"])):
                await self._complete_without_projection(item)
            else:
                entries.append((item, document))
        if entries:
            await self._push_projection_groups(dataset_name, entries, graph=False)

    @property
    def relations_dataset(self) -> str:
        """当前代际的关系文档分区，归属保留在文档标签及出处。"""
        return projection_dataset(self.client.projection_namespace or self.config.dataset_prefix, graph=True)

    async def _process_graph_upserts(self, items: list[dict[str, Any]]) -> None:
        """读取一致的邻域及出处快照；证据变化重投影，强度变化不重投影。"""
        grouped: dict[str, list[ProjectionEntry]] = defaultdict(list)
        for item in items:
            mapping = await self.store.get_cognee_mapping(item["entry_id"], entry_kind=item["entry_kind"])
            projection = await self.store.graph.projection_fragments_snapshot(
                item["entry_id"], fragmented=bool(mapped_documents(mapping)))
            if projection is None:
                # 节点已归档/不存在：按删除处理
                await self._process_delete({**item, "operation": "delete"})
                continue
            fingerprint, fragments = projection
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
            for key, text, digest, attribution in fragments:
                grouped[dataset].append(({**item, "projection_hash": fingerprint},
                    ProjectionDocument(text, digest, attribution, part_key=key)))
        for dataset_name, entries in grouped.items():
            await self._push_projection_groups(dataset_name, entries, graph=True)

    async def _push_projection_groups(
        self, dataset_name: str, entries: list[ProjectionEntry], *, graph: bool,
    ) -> None:
        """按文档量与重试次数限制发布组；同一来源保留完整事务边界。"""
        published = False
        sources = {int(item["queue_id"]): item for item, _ in entries}
        mappings = {int(item["queue_id"]): await self.store.get_cognee_mapping(
            item["entry_id"], entry_kind=item["entry_kind"])
            for item in sources.values()}
        for group in _publication_groups(entries, mappings, self.config):
            group_sources = {int(item["queue_id"]): item for item, _document in group}
            with self.progress.batch(list(group_sources.values())):
                published |= await self._push_projection_group(dataset_name, group, graph=graph)
        if published:
            with self.progress.batch(list({int(item["queue_id"]): item for item, _document in entries}.values())):
                self.progress.stage("enhance", dataset_name=dataset_name, timeout=self.config.pipeline_timeout_seconds)
                try:
                    await self._after_write(dataset_name)
                except Exception as exc:
                    log(f"Cognee 发布后增强失败（投影已提交，不重试同步）: {error_text(exc)}", "WARNING", tag="思维")

    async def _push_projection_group(
        self,
        dataset_name: str,
        entries: list[ProjectionEntry],
        *,
        graph: bool,
    ) -> bool:
        """先完成新投影再替换旧映射；稳定文档 ID 使失败重试不新增副本。"""
        metadata_key = "anelf_graph_node_id" if graph else "anelf_memory_id"
        label_kind = "graph-node" if graph else "memory"
        stage = "prepare"
        started = time.monotonic()
        self.progress.stage(stage, dataset_name=dataset_name)
        try:
            data_items: list[InputDocument] = []
            sources: dict[int, list[int]] = defaultdict(list)
            existing: set[tuple[str, str]] = set()
            for index, (item, document) in enumerate(entries):
                queue_id = int(item["queue_id"])
                if queue_id not in sources:
                    mapping = await self.store.get_cognee_mapping(item["entry_id"], entry_kind=item["entry_kind"])
                    if mapping:
                        existing.update((str(row["dataset_id"]), str(row["data_id"])) for row in expand_mapping(mapping))
                sources[queue_id].append(index)
                payload = item["payload"]
                metadata = {
                    "anelf_projection_version": PROJECTION_VERSION,
                    "anelf_content_hash": document.fingerprint,
                    metadata_key: str(item["entry_id"]),
                    "memory_type": "graph_relation" if graph else str(payload.get("type", "semantic")),
                    "source": "graph" if graph else str(payload.get("source", "")),
                    "anelf_attribution": document.attribution,
                }
                if document.part_key:
                    metadata["anelf_part_key"] = document.part_key
                data_item = await self.client.make_data_item(
                    document.text, label=f"anelf-{label_kind}-{item['entry_id']}",
                    dataset_name=dataset_name, node_set=canonical_tags(document.attribution.get("tags", [])),
                    external_metadata=metadata,
                )
                data_id = str(_value(data_item, "data_id", ""))
                if not data_id:
                    raise RuntimeError("Cognee 投影缺少稳定文档 ID")
                data_items.append(data_item)
            existing_ids = {identity for _, identity in existing}
            additions: list[InputDocument] = []
            for (item, document), data_item in zip(entries, data_items, strict=True):
                data_id = str(_value(data_item, "data_id", ""))
                if not document.part_key or item["payload"].get("force") or data_id not in existing_ids:
                    await self.store.projection_state.stage(item["entry_kind"], item["entry_id"], dataset_name, data_id)
                    additions.append(data_item)
            self._observe_write()
            async with self.client.write_transaction():
                dataset_id = next(iter(existing))[0] if existing else ""
                if additions:
                    stage = "add"
                    self.progress.stage(stage, dataset_name=dataset_name, timeout=self.config.pipeline_timeout_seconds)
                    ingestion = await self.client.add(additions, dataset_name=dataset_name, incremental_loading=True)
                    dataset_id = str(_value(ingestion, "dataset_id", ""))
                    if not dataset_id:
                        raise RuntimeError("Cognee 摄入未返回 dataset_id，保留旧投影")
                    stage = "cognify"
                    self.progress.stage(stage, dataset_name=dataset_name, dataset_id=dataset_id,
                                        timeout=self.config.pipeline_timeout_seconds,
                                        documents={str(_value(data_item, "data_id", "")):
                                                   bool(_value(data_item, "external_metadata", {}).get("anelf_part_key"))
                                                   for data_item in additions})
                    await self.client.cognify(datasets=[dataset_name], incremental_loading=True)
                    self._observe_write()
                if not dataset_id:
                    raise RuntimeError("投影缺少目标数据集，保留旧映射")
                stage = "verify"
                self.progress.stage(stage, dataset_name=dataset_name)
                await self.client.verify_graph_documents(dataset_id,
                    [str(_value(data_item, "data_id", "")) for data_item in data_items])
                stage = "replace_projection"
                self.progress.stage(stage, dataset_name=dataset_name)
                targets: set[tuple[str, str]] = set()
                for indices in sources.values():
                    item = entries[indices[0]][0]
                    keep = {(dataset_name, str(_value(data_items[index], "data_id", ""))) for index in indices}
                    targets.update(await self._superseded_targets(item, keep=keep))
                await self._delete_targets(targets)
            # 发布前绝不更新权威映射；分片清单与来源总指纹在原生事务内一起确认。
            for indices in sources.values():
                item, document = entries[indices[0]]
                documents = [MappedDocument(part_key=entries[index][1].part_key,
                    data_id=str(_value(data_items[index], "data_id", "")),
                    content_hash=entries[index][1].fingerprint).model_dump() for index in indices] if document.part_key else []
                await self.store.complete_cognee_sync(
                    item["queue_id"], item["entry_id"], entry_kind=item["entry_kind"],
                    dataset_name=dataset_name, dataset_id=dataset_id,
                    data_id=str(_value(data_items[indices[0]], "data_id", "")),
                    content_hash=item.get("projection_hash", document.fingerprint),
                    documents_json=json.dumps(documents, ensure_ascii=False),
                )
            return True
        except Exception as exc:
            self.last_error = error_text(exc)
            log(
                f"Cognee 同步失败 [stage:{stage}][dataset:{dataset_name}] "
                f"{len({int(item['queue_id']) for item, _ in entries})} 条来源 / {len(entries)} 篇文档，"
                f"耗时 {time.monotonic() - started:.1f}s: {self.last_error}",
                "WARNING", tag="思维",
            )
            failed: set[int] = set()
            for item, _document in entries:
                if item["queue_id"] not in failed:
                    await self._fail(item, exc)
                    failed.add(item["queue_id"])
            return False

    async def _fail(self, item: dict[str, Any], error: str | Exception) -> None:
        self.last_error = error_text(error) if isinstance(error, Exception) else error
        attempts = int(item.get("attempts", 0))
        delay = min(300.0, 2.0 ** min(attempts, 8))
        await self.store.fail_cognee_sync(
            item["queue_id"],
            self.last_error,
            max_retries=self.config.max_retries if not isinstance(error, Exception) or retryable(error) else 1,
            retry_delay_seconds=delay,
        )


def _publication_groups(
    entries: list[ProjectionEntry], mappings: dict[int, dict[str, Any] | None], config: CogneeConfig,
) -> Iterator[list[ProjectionEntry]]:
    """按变更文档数及正文长度装箱；单个超限来源独占一组，不拆散原子映射。"""
    sources: dict[int, list[ProjectionEntry]] = defaultdict(list)
    for entry in entries:
        sources[int(entry[0]["queue_id"])].append(entry)
    cohorts: dict[int, list[list[ProjectionEntry]]] = defaultdict(list)
    for source in sources.values():
        cohorts[max(0, int(source[0][0].get("attempts", 0)))].append(source)
    for attempts, cohort in cohorts.items():
        divisor = 1 << min(attempts, len(cohort).bit_length())
        width = max(1, (len(cohort) + divisor - 1) // divisor)
        group: list[ProjectionEntry] = []
        source_count = documents = characters = 0
        for source in cohort:
            added, chars = _source_cost(source, mappings[int(source[0][0]["queue_id"])])
            if group and (source_count >= width or documents + added > config.sync_max_documents_per_group
                          or characters + chars > config.sync_max_characters_per_group):
                yield group
                group, source_count, documents, characters = [], 0, 0, 0
            group.extend(source)
            source_count += 1
            documents += added
            characters += chars
        if group:
            yield group


def _source_cost(entries: list[ProjectionEntry], mapping: dict[str, Any] | None) -> tuple[int, int]:
    """只计算变化分片的摄入和退役；保留分片不占模型和删除预算。"""
    previous = {row.part_key: row.content_hash for row in mapped_documents(mapping)}
    current = {document.part_key: document.fingerprint for _, document in entries}
    changed = [document for item, document in entries if item["payload"].get("force")
               or not document.part_key or previous.get(document.part_key) != document.fingerprint]
    retired = (sum(current.get(key) != digest for key, digest in previous.items())
               if previous else int(mapping is not None))
    return len(changed) + retired, sum(len(document.text) for document in changed)


def _value(item: Any, key: str, default: Any) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)
