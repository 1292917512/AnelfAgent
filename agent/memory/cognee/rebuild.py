"""Cognee 全量构建编排：保留旧数据集、原生快照、续跑与逐项验收。

模型看到什么：构建期间继续使用原生召回，验收后启用新图谱证据。
token 影响：显式重建对活跃记忆和关系证据重新抽取及嵌入。
缓存影响：不改主对话稳定前缀。
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from agent.storage.volume_restore import backups_root

from ..memory_store import MemoryStore
from ..projection import PROJECTION_VERSION, canonical_tags, projection_dataset
from ..store.projection_state import ProjectionState
from .config import CogneeConfig
from .documents import stored_node_tags
from .ports import ProjectionBackend


def _field(value: Any, key: str, default: Any = None) -> Any:
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


class CogneeRebuild:
    """由协调器的生命周期和互斥锁托管，无独立后台任务或全局单例。"""

    def __init__(self, store: MemoryStore, client: ProjectionBackend) -> None:
        self.store = store
        self.client = client
        self.state: ProjectionState | None = None

    async def initialize(self, config: CogneeConfig) -> None:
        self.client.projection_ready = False
        self.state = await self.store.projection_state.load()
        if self.state is None:
            counts = await self.store.get_cognee_sync_status()
            existing = counts["synced"] > 0 or (Path(config.absolute_data_root) / "system").exists()
            self.state = ProjectionState(
                root=config.absolute_data_root, base_prefix=config.dataset_prefix,
                namespace=config.dataset_prefix,
                phase="required" if existing else "ready",
            )
            await self.store.projection_state.save(self.state)
        self.validate_config(config)
        if self.state.version != PROJECTION_VERSION:
            self.state.phase = "required"
        self.bind()

    def validate_config(self, config: CogneeConfig) -> None:
        if self.state and (self.state.root != config.absolute_data_root
                           or self.state.base_prefix != config.dataset_prefix):
            raise ValueError("数据目录和数据集前缀与投影映射不匹配，请使用完整存储卷迁移/恢复")

    def bind(self) -> None:
        if self.state:
            self.client.projection_namespace = self.state.namespace
            self.client.projection_ready = self.state.phase == "ready"

    @property
    def can_consume(self) -> bool:
        return self.state is None or self.state.phase in {"ready", "scanning", "building", "verifying"}

    async def request(self, config: CogneeConfig) -> ProjectionState:
        await self._validate_request(config)
        state = self.state
        if state is None:
            raise RuntimeError("构建状态尚未初始化")
        state.rebuild_requested = False
        if state.phase in {"requested", "scanning", "building", "verifying"}:
            await self.store.projection_state.save(state)
            return state
        if state.phase == "failed":
            state.error = ""
            # 验收失败不能仅重验同一份坏索引：分页面向权威源重放，保留已写日志和映射。
            state.phase = "scanning" if state.backup_path else "requested"
            state.memory_cursor = state.graph_cursor = 0
            state.memory_scanned = state.graph_scanned = False
            await self.store.retry_failed_cognee_sync()
        else:
            state = ProjectionState(
                root=config.absolute_data_root, base_prefix=config.dataset_prefix,
                namespace=f"{config.dataset_prefix}_p{PROJECTION_VERSION}_{uuid.uuid4().hex[:12]}",
                previous_namespaces=[*state.previous_namespaces, state.namespace],
                phase="requested", started_at=time.time(),
            )
        self.state = state
        await self.store.projection_state.save(state)
        self.bind()
        return state

    async def schedule(self, config: CogneeConfig) -> ProjectionState:
        """持久化请求后立即返回；worker 在当前批次结束后切换构建代际。"""
        await self._validate_request(config)
        if self.state is None:
            raise RuntimeError("构建状态尚未初始化")
        self.state.rebuild_requested = True
        await self.store.projection_state.save(self.state)
        return self.state

    async def _validate_request(self, config: CogneeConfig) -> None:
        if not config.enabled or not config.sync_enabled:
            raise ValueError("全量构建需要启用 Cognee 与同步消费")
        if not config.project_memories_enabled or not config.project_graph_enabled:
            raise ValueError("全量构建需要同时启用记忆与关系投影")
        if self.state is None:
            await self.initialize(config)
        self.validate_config(config)

    async def advance(self) -> None:
        """每轮最多处理一页；调用方持投影锁，API 返回入队状态而非虚假完成。"""
        state = self.state
        if state is None or state.phase in {"ready", "required", "failed"}:
            return
        try:
            if state.phase == "requested":
                # 来源不在权威映射中的文档可能包含独有内容，禁止静默丢出检索面。
                mappings = await self.store.projection_state.mappings()
                sources = await self.store.projection_state.preserved_sources()
                known = {(str(r["dataset_id"]), str(r["data_id"])) for r in mappings
                         if (str(r["entry_kind"]), int(r["entry_id"])) in sources}
                staged = {(str(r["dataset_name"]), str(r["data_id"]))
                          for r in await self.store.projection_state.staged()
                          if (str(r["entry_kind"]), int(r["entry_id"])) in sources}
                unknown = 0
                active_prefix = state.previous_namespaces[-1] if state.previous_namespaces else state.base_prefix
                for dataset in await self.client.list_datasets():
                    name = str(_field(dataset, "name", ""))
                    dataset_id = str(_field(dataset, "id", ""))
                    mapped_dataset = any(str(row["dataset_id"]) == dataset_id for row in mappings)
                    if not mapped_dataset and not name.startswith(active_prefix + "_"):
                        continue
                    for item in await self.client.list_data(dataset_id):
                        data_id = str(_field(item, "id", ""))
                        # SDK 会拆分历史跨数据集共享行；legacy_id 是上游迁移保留的身份。
                        legacy_id = str(_field(item, "legacy_id", ""))
                        if ((dataset_id, data_id) not in known and (dataset_id, legacy_id) not in known
                                and (name, data_id) not in staged):
                            unknown += 1
                if unknown:
                    raise RuntimeError(f"发现 {unknown} 个未被原生映射覆盖的旧文档；需先确认或导入其独有内容，旧数据未清理")
                backup = backups_root() / "cognee-rebuild" / state.namespace / f"memory-{uuid.uuid4().hex[:12]}.sqlite3"
                await self.store.projection_state.begin(state, backup)
            if state.phase == "scanning":
                await self.store.projection_state.scan_page(state)
            elif state.phase in {"building", "verifying"}:
                status = await self.store.get_cognee_sync_status()
                if status["failed"]:
                    state.error = f"{status['failed']} 条投影失败，重试成功前不切换"
                    await self.store.projection_state.save(state)
                    return
                if status["pending"]:
                    return
                await self._verify(state)
        except Exception as exc:
            state.phase = "failed"
            state.error = f"{type(exc).__name__}: {exc}"
            await self.store.projection_state.save(state)
        finally:
            self.bind()

    async def _verify(self, state: ProjectionState) -> None:
        state.phase = "verifying"
        await self.store.projection_state.save(state)
        audit = await self.store.projection_state.audit()
        if not audit["consistent"] or audit["pending"] or audit["failed"]:
            state.phase = "building"
            await self.store.projection_state.save(state)
            return
        if audit["missing_or_stale"] or audit["extra"]:
            raise RuntimeError(f"权威覆盖不一致: {audit}")
        mappings = await self.store.projection_state.mappings()
        documents: dict[tuple[str, str], Any] = {}
        for dataset_id in sorted({str(row["dataset_id"]) for row in mappings}):
            for item in await self.client.list_data(dataset_id):
                documents[(dataset_id, str(_field(item, "id", "")))] = item
        if len(documents) != len(mappings):
            raise RuntimeError(f"后端文档数与映射不一致: {len(documents)} != {len(mappings)}")
        for mapping in mappings:
            item = documents.get((str(mapping["dataset_id"]), str(mapping["data_id"])))
            metadata = _field(item, "external_metadata", {}) or {}
            if metadata.get("anelf_content_hash") != mapping["content_hash"]:
                raise RuntimeError(f"后端文档缺失或指纹不一致: {mapping['entry_kind']}:{mapping['entry_id']}")
            if not self.client.document_processed(item, str(mapping["dataset_id"])):
                raise RuntimeError(f"后端文档尚未完成抽取: {mapping['entry_kind']}:{mapping['entry_id']}")
            tags = canonical_tags(metadata.get("anelf_attribution", {}).get("tags", []))
            if stored_node_tags(_field(item, "node_set", [])) != tags:
                raise RuntimeError(f"后端标签索引不完整: {mapping['entry_kind']}:{mapping['entry_id']}")
            expected_dataset = projection_dataset(state.namespace, graph=mapping["entry_kind"] == "graph_node")
            if mapping["dataset_name"] != expected_dataset:
                raise RuntimeError("发现其他构建代际的映射")
        if not await self.store.projection_state.finish(state, len(documents), revision=audit["revision"]):
            state.phase = "building"
            await self.store.projection_state.save(state)
