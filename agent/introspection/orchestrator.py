"""内省编排器：统一管理反思单元与任务单元的加载、执行与结果存储。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.messages import EntityData
from agent.memory.memory_types import MemoryEntry, MemoryType

from core.log import log

from .config import get_introspection_config
from .introspection_unit import (
    IntrospectionContext,
    IntrospectionResult,
    IntrospectionUnit,
    UnitMode,
    UnitScope,
)
from .units import (
    EntityAnalysisUnit,
    MemoryHealthUnit,
    PromptBasedUnit,
    SelfReflectionUnit,
)

_CONFIG_UNITS_DIR = Path("config/introspection")
_TASK_UNITS_DIR = Path("config/tasks")


class Introspection:
    """内省编排器：统一管理反思单元与任务单元的加载、执行与结果存储。"""

    def __init__(self, mind: Any) -> None:
        self.mind = mind
        self.config = get_introspection_config()
        self._units: List[IntrospectionUnit] = self._build_default_units()
        self._memory_health_unit = self._find_unit(MemoryHealthUnit)
        self._load_config_units()
        self._load_task_units()
        self._discover_external_units()

    def _build_default_units(self) -> List[IntrospectionUnit]:
        units: List[IntrospectionUnit] = []
        for cls in (SelfReflectionUnit, EntityAnalysisUnit, MemoryHealthUnit):
            unit = cls()
            unit.enabled = self.config.get_unit(unit.name).enabled
            units.append(unit)
        return units

    def _load_json_units(
        self, directory: Path, *, force_mode: Optional[UnitMode] = None,
    ) -> None:
        """从指定目录加载 JSON 配置型单元，跳过已注册的同名单元。"""
        if not directory.is_dir():
            return
        existing_names = {u.name for u in self._units}
        for json_file in sorted(directory.glob("*.json")):
            try:
                data: Dict[str, Any] = json.loads(json_file.read_text("utf-8"))
                unit = PromptBasedUnit.from_dict(data, force_mode=force_mode)
                if unit.name in existing_names:
                    continue
                cfg_unit = self.config.get_unit(unit.name)
                if unit.name in self.config.units:
                    unit.enabled = cfg_unit.enabled
                self.register_unit(unit)
                existing_names.add(unit.name)
            except Exception as exc:
                log(f"单元加载失败 [{json_file.name}]: {exc}", "WARNING", tag="内省")

    def _load_config_units(self) -> None:
        self._load_json_units(_CONFIG_UNITS_DIR)

    def _load_task_units(self) -> None:
        self._load_json_units(_TASK_UNITS_DIR, force_mode=UnitMode.TASK)

    def reload_config_units(self) -> None:
        """热重载所有配置型单元（反思 + 任务）。"""
        self._units = [u for u in self._units if not isinstance(u, PromptBasedUnit)]
        self._load_config_units()
        self._load_task_units()
        reflect_count = sum(
            1 for u in self._units
            if isinstance(u, PromptBasedUnit) and u.mode == UnitMode.REFLECT
        )
        task_count = sum(
            1 for u in self._units
            if isinstance(u, PromptBasedUnit) and u.mode == UnitMode.TASK
        )
        log(f"配置型单元热重载: {reflect_count} 个反思 + {task_count} 个任务", tag="内省")

    def _discover_external_units(self) -> None:
        import importlib
        import inspect
        import sys

        units_dir = Path("introspection_units")
        if not units_dir.is_dir():
            return

        existing_names = {u.name for u in self._units}

        for py_file in sorted(units_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            module_name = f"introspection_units.{py_file.stem}"
            try:
                module = sys.modules.get(module_name) or importlib.import_module(module_name)
                for _, cls in inspect.getmembers(module, inspect.isclass):
                    if (
                        cls is not IntrospectionUnit
                        and issubclass(cls, IntrospectionUnit)
                        and not inspect.isabstract(cls)
                        and getattr(cls, "__module__", "") == module.__name__
                        and isinstance(getattr(cls, "name", None), str)
                        and cls.name not in existing_names
                    ):
                        unit = cls()
                        unit.enabled = self.config.get_unit(unit.name).enabled
                        self.register_unit(unit)
                        existing_names.add(unit.name)
            except Exception as exc:
                log(f"外部单元加载失败 [{py_file.name}]: {exc}", "WARNING", tag="内省")

    def _find_unit(self, cls: type) -> Optional[IntrospectionUnit]:
        for u in self._units:
            if isinstance(u, cls):
                return u
        return None

    def register_unit(self, unit: IntrospectionUnit) -> None:
        existing = [u for u in self._units if u.name != unit.name]
        existing.append(unit)
        self._units = existing
        log(f"单元已注册: {unit.name} (scope={unit.scope.value}, mode={unit.mode.value})", tag="内省")

    # ------------------------------------------------------------------
    # 反思执行（自动触发，仅 REFLECT 模式单元）
    # ------------------------------------------------------------------

    async def run(
        self,
        entity: Optional[EntityData] = None,
        *,
        memory_warnings_checked: bool = False,
    ) -> List[IntrospectionResult]:
        """执行所有适用的反思单元（mode=REFLECT），返回结果列表。"""
        from core.event_bus import event_bus, EVENT_THINKING_INTROSPECTION

        if not self.config.enabled:
            log("反思系统已禁用", tag="内省")
            return []

        ctx = await self._build_context(entity, memory_warnings_checked=memory_warnings_checked)
        desc = entity.get_entity_desc() if entity else "全局"
        applicable = [u for u in self._units if u.should_run(ctx)]
        skipped = [u for u in self._units if u.enabled and u.mode == UnitMode.REFLECT and u not in applicable]

        if not applicable:
            log(f"无适用的反思单元: {desc}", tag="内省")
            return []

        unit_names = ", ".join(u.name for u in applicable)
        log(f"开始内省: {desc} -> [{unit_names}]", tag="内省")

        for sk in skipped:
            await event_bus.emit(EVENT_THINKING_INTROSPECTION, {
                "stage": "unit_skip", "unit": sk.name,
                "scope": sk.scope.value, "reason": "scope_mismatch",
            })

        results: List[IntrospectionResult] = []
        for unit in applicable:
            await event_bus.emit(EVENT_THINKING_INTROSPECTION, {
                "stage": "unit_start", "unit": unit.name,
                "scope": unit.scope.value, "entity": desc,
            })
            try:
                result = await unit.execute(ctx)
                if result:
                    await self._store_result(result, ctx)
                    results.append(result)
                await event_bus.emit(EVENT_THINKING_INTROSPECTION, {
                    "stage": "unit_end", "unit": unit.name,
                    "description": getattr(unit, "description", ""),
                    "has_output": result is not None,
                    "content_preview": result.content[:300] if result else "",
                    "memory_type": result.memory_type.value if result else "",
                    "tags": result.tags if result else [],
                    "source": result.source if result else "",
                    "importance": result.importance if result else 0.0,
                })
            except Exception as exc:
                log(f"单元 [{unit.name}] 异常: {exc}", "WARNING", tag="内省")
                await event_bus.emit(EVENT_THINKING_INTROSPECTION, {
                    "stage": "unit_error", "unit": unit.name, "error": str(exc),
                })

        log(f"内省完成: {desc}, {len(results)}/{len(applicable)} 个单元有产出", tag="内省")
        return results

    async def run_entity_only(self, entity: EntityData) -> Optional[IntrospectionResult]:
        """仅对单个实体执行画像分析。"""
        entity_unit = self._find_unit(EntityAnalysisUnit)
        if not entity_unit or not entity_unit.enabled:
            return None
        ctx = await self._build_context(entity)
        if not entity_unit.should_run(ctx):
            return None
        try:
            result = await entity_unit.execute(ctx)
            if result:
                await self._store_result(result, ctx)
            return result
        except Exception as exc:
            log(f"实体画像分析异常 [{entity.get_entity_desc()}]: {exc}", "WARNING", tag="内省")
            return None

    # ------------------------------------------------------------------
    # 任务执行（按名指定触发，仅 TASK 模式单元）
    # ------------------------------------------------------------------

    def list_tasks(self) -> List[Dict[str, Any]]:
        """列出所有已注册的任务单元信息。"""
        return [
            {
                "name": u.name,
                "description": getattr(u, "description", ""),
                "display_name": getattr(u, "display_name", u.name),
                "scope": u.scope.value,
                "enabled": u.enabled,
                "tool_tags": u.tool_tags,
            }
            for u in self._units
            if u.mode == UnitMode.TASK
        ]

    async def run_task(self, task_name: str, entity: Optional[EntityData] = None) -> Optional[IntrospectionResult]:
        """按名称执行指定任务单元。"""
        unit = next((u for u in self._units if u.name == task_name and u.mode == UnitMode.TASK), None)
        if not unit:
            log(f"任务 [{task_name}] 不存在或不是任务类型", "WARNING", tag="内省")
            return None
        if not unit.enabled:
            log(f"任务 [{task_name}] 已禁用", "WARNING", tag="内省")
            return None

        log(f"开始执行任务: {task_name}", tag="内省")
        ctx = await self._build_context(entity)
        try:
            result = await unit.execute(ctx)
            if result:
                await self._store_result(result, ctx)
            log(f"任务完成: {task_name} ({'有产出' if result else '无产出'})", tag="内省")
            return result
        except Exception as exc:
            log(f"任务 [{task_name}] 执行异常: {exc}", "WARNING", tag="内省")
            return None

    # ------------------------------------------------------------------
    # 公共方法
    # ------------------------------------------------------------------

    async def _build_context(
        self,
        entity: Optional[EntityData],
        *,
        memory_warnings_checked: bool = False,
    ) -> IntrospectionContext:
        conversation_list: List[Dict[str, Any]] = []
        if entity:
            min_conv = self.config.min_conversations_for_analysis
            conversation_list = await self.mind.get_conversation(entity)
            if len(conversation_list) < min_conv:
                desc = entity.get_entity_desc()
                log(f"对话不足: {desc} ({len(conversation_list)}/{min_conv})", tag="内省")

        active_channel_scopes: Dict[str, List[str]] = {}
        for key, snap in getattr(self.mind, "_channel_snapshots", {}).items():
            scopes = [s for s, a in snap.active_scopes.items() if a.last_time > 0]
            if scopes:
                active_channel_scopes[key] = scopes[:5]

        return IntrospectionContext(
            mind=self.mind,
            entity=entity,
            conversation_list=conversation_list,
            config=self.config,
            memory_warnings_checked=memory_warnings_checked,
            active_channel_scopes=active_channel_scopes,
        )

    async def _store_result(self, result: IntrospectionResult, ctx: IntrospectionContext) -> None:
        if not ctx.mind.memory_store or not result.content.strip():
            return
        if result.unit_name == MemoryHealthUnit.name:
            return
        if result.memory_type == MemoryType.REFLECTION:
            if await ctx.mind.memory_store.has_similar_content(result.content):
                log(f"内容与已有记忆高度相似，跳过存储: [{result.unit_name}]", tag="内省")
                return

        entry = MemoryEntry(
            memory_type=result.memory_type,
            content=result.content,
            source=result.source,
            tags=result.tags,
            importance=result.importance,
        )
        if ctx.mind.embedder.available:
            entry.embedding = await ctx.mind.embedder.embed_one(result.content)
        await ctx.mind.memory_store.add(entry)

        from agent.mind.heartbeat import append_entry as _hb_append
        _hb_append(f"[{result.unit_name}] {result.content[:120]}")
        log(f"记忆已存储: [{result.unit_name}] {result.source}", tag="内省")

    async def check_memory_thresholds(self) -> List[str]:
        if not self._memory_health_unit or not isinstance(self._memory_health_unit, MemoryHealthUnit):
            return []
        ctx = IntrospectionContext(
            mind=self.mind, entity=None, conversation_list=[], config=self.config,
        )
        return await self._memory_health_unit._check(ctx)
