"""Cognee 全量构建编排：保留旧数据集、原生快照、续跑与逐项验收。

模型看到什么：构建期间继续使用原生召回，验收后启用新图谱证据。
token 影响：显式重建对活跃记忆和关系证据重新抽取及嵌入；覆盖修复仅重投影差异条目。
缓存影响：不改主对话稳定前缀。
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from agent.storage.volume_restore import backups_root
from core.log import log

from ..memory_store import MemoryStore
from ..projection import PROJECTION_VERSION, canonical_tags, projection_dataset
from ..projection_documents import expand_mapping
from ..store.projection_state import ProjectionState
from .config import CogneeConfig
from .documents import stored_node_tags
from .node_sets import projected_node_tags
from .ports import ProjectionBackend
from .preflight import check_legacy_documents


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
        if self.state.base_prefix == config.dataset_prefix and self.state.root != config.absolute_data_root:
            await self._relocate_root(config)
        self.validate_config(config)
        if self.state.version != PROJECTION_VERSION:
            self.state.phase = "required"
        self.bind()

    async def _relocate_root(self, config: CogneeConfig) -> None:
        """迁移承接复用后端验收；空库、错库或残缺索引不能接管原生映射。"""
        state = self.state
        if state is None:
            raise RuntimeError("构建状态尚未初始化")
        mappings = await self.store.projection_state.mappings()
        if mappings and Path(config.absolute_data_root).is_dir():
            verified = await self.verify_backend(state, mappings)
        elif (not mappings and state.version == PROJECTION_VERSION and state.phase == "ready"
              and not state.verified and not state.previous_namespaces and not state.legacy_preserved):
            # 尚无已发布投影时也需确认目标为空，不能接管未知历史文档。
            for dataset in await self.client.list_datasets():
                if await self.client.list_data(str(_field(dataset, "id"))):
                    raise ValueError("数据目录与投影映射不匹配：目标含未知文档，原记忆和队列已保留")
            verified = 0
        else:
            raise ValueError("数据目录与投影映射不匹配：新位置缺少可核验的已提交索引，原记忆和队列已保留")
        relocated = state.model_copy(update={"root": config.absolute_data_root})
        await self.store.projection_state.save(relocated)
        self.state = relocated
        log(f"Cognee 迁移索引验收通过，已承接 {verified} 个文档并更新数据目录", tag="记忆")

    def validate_config(self, config: CogneeConfig, *, selected_root: str | None = None) -> None:
        """配置编辑可保留启动时已选择的位置；位置承接仍须 initialize 的后端验收。"""
        if self.state and self.state.base_prefix != config.dataset_prefix:
            raise ValueError("数据集前缀与投影映射不匹配，请保留已有前缀或使用完整存储卷迁移/恢复")
        if (self.state and self.state.root != config.absolute_data_root
                and selected_root != config.absolute_data_root):
            raise ValueError("数据目录与投影映射不匹配，请完整迁移后重启并验收原索引")

    def bind(self) -> None:
        if self.state:
            self.client.projection_namespace = self.state.namespace
            self.client.projection_ready = self.state.phase == "ready"

    @property
    def can_consume(self) -> bool:
        return self.state is None or self.state.phase in {"ready", "scanning", "building", "verifying"}

    async def request(self, config: CogneeConfig, *, restart: bool = False) -> ProjectionState:
        await self._validate_request(config)
        state = self.state
        if state is None:
            raise RuntimeError("构建状态尚未初始化")
        state.rebuild_requested = False
        restart = restart or state.rebuild_from_scratch
        state.rebuild_from_scratch = False
        if not restart and state.phase in {"requested", "scanning", "building", "verifying"}:
            await self.store.projection_state.save(state)
            return state
        if not restart and state.phase == "failed":
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
                previous_namespaces=list(dict.fromkeys([*state.previous_namespaces, state.namespace])),
                phase="requested", started_at=time.time(),
            )
        self.state = state
        await self.store.projection_state.save(state)
        self.bind()
        return state

    async def repair_coverage(self, config: CogneeConfig, namespace: str) -> int:
        """修复当前失败轮次的权威覆盖差异，保留同一代际并重新走完整验收。"""
        await self._validate_request(config)
        state = self.state
        if (state is None or state.namespace != namespace or state.phase != "failed"
                or not state.memory_scanned or not state.graph_scanned or state.rebuild_requested):
            raise ValueError("仅支持已完成扫描的当前失败轮次，请刷新状态后重试")
        audit = await self.store.projection_state.audit()
        count = await self.store.projection_state.repair_coverage(state, audit)
        self.bind()
        return count

    async def schedule(self, config: CogneeConfig, *, restart: bool = False) -> ProjectionState:
        """持久化请求后立即返回；worker 在当前批次结束后切换构建代际。"""
        await self._validate_request(config)
        if self.state is None:
            raise RuntimeError("构建状态尚未初始化")
        self.state.rebuild_requested = True
        self.state.rebuild_from_scratch = self.state.rebuild_from_scratch or restart
        await self.store.projection_state.save(self.state)
        return self.state

    async def _validate_request(self, config: CogneeConfig) -> None:
        if not config.enabled:
            raise ValueError("全量构建需要启用 Cognee；同步暂停时请求保留至继续同步")
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
                await check_legacy_documents(self.store.projection_state, self.client, state)
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
                if state.error:
                    state.error = ""
                    await self.store.projection_state.save(state)
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
        verified = await self.verify_backend(state, mappings)
        if not await self.store.projection_state.finish(state, verified, revision=audit["revision"]):
            state.phase = "building"
            await self.store.projection_state.save(state)

    async def verify_backend(self, state: ProjectionState, mappings: list[dict[str, Any]]) -> int:
        """核验内容、标签、抽取标记及实际图谱覆盖；构建与清理共用同一验收门。"""
        mappings = [document for mapping in mappings for document in expand_mapping(mapping)]
        documents: dict[tuple[str, str], Any] = {}
        for dataset_id in sorted({str(row["dataset_id"]) for row in mappings}):
            for item in await self.client.list_data(dataset_id):
                documents[(dataset_id, str(_field(item, "id", "")))] = item
        if len(documents) != len(mappings):
            raise RuntimeError(f"后端文档数与映射不一致: {len(documents)} != {len(mappings)}")
        for mapping in mappings:
            document = documents.get((str(mapping["dataset_id"]), str(mapping["data_id"])))
            if document is None:
                raise RuntimeError(f"后端文档缺失: {mapping['entry_kind']}:{mapping['entry_id']}")
            metadata = _field(document, "external_metadata", {}) or {}
            if metadata.get("anelf_content_hash") != mapping["content_hash"]:
                raise RuntimeError(f"后端文档缺失或指纹不一致: {mapping['entry_kind']}:{mapping['entry_id']}")
            if not self.client.document_processed(document, str(mapping["dataset_id"])):
                raise RuntimeError(f"后端文档尚未完成抽取: {mapping['entry_kind']}:{mapping['entry_id']}")
            tags = projected_node_tags(canonical_tags(metadata.get("anelf_attribution", {}).get("tags", [])))
            if stored_node_tags(_field(document, "node_set", [])) != tags:
                raise RuntimeError(f"后端标签索引不完整: {mapping['entry_kind']}:{mapping['entry_id']}")
            expected_dataset = projection_dataset(state.namespace, graph=mapping["entry_kind"] == "graph_node")
            if mapping["dataset_name"] != expected_dataset:
                raise RuntimeError("发现其他构建代际的映射")
        for dataset_id in sorted({str(row["dataset_id"]) for row in mappings}):
            await self.client.verify_graph_documents(dataset_id, [
                str(row["data_id"]) for row in mappings if str(row["dataset_id"]) == dataset_id
            ])
        return len(documents)
