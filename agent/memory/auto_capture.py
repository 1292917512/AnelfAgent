"""自动记忆捕获管线：对话 → 长期记忆的有界提取（不依赖 LLM 自觉调 memorize）。

每个心跳 tick 检查各会话 scope 的新消息，命中任一触发条件即提取：

1. 轮数阈值：未提取的新对话轮数达到当前阈值（memory_auto_capture_every_n）；
2. 空闲防抖：有待提取内容且会话静默超过 memory_auto_capture_idle_seconds
   （对话告一段落时自动整理）；
3. Warm-up：新 scope 的提取阈值按 1→2→4→…→every_n 指数爬升，
   会话早期（信息最密集的阶段）记忆被更快固化。

提取流程：完整新消息 → LLM 提取并校验 JSON → 逐条语义裁决 → 保存出处与检查点。
每批最多 12 条，字符预算只在消息之间切分；失败最多尝试 5 次，明确记入审计。
游标、冻结材料与提取结果按 scope 持久化，重启恢复已保存进度；关停取消不消耗失败额度。

发言者归属：消息行渲染时从元数据标签确定性提取身份（称呼[uid:xxx]），
提取输出的 speaker 字段经批次内真实 uid 集合校验后挂为 user:{adapter}:{uid}
记忆标签。材料会话、消息 ID 和时间窗口由系统另行保存；标签是提炼出的
归属线索，不把摘要当作逐字原话（读取侧见 recall_format）。

Model Experience：内部提取器收到完整新消息、有限旧背景和真实发言者标记；
平台事件单独标明，AI 自述不得作为执行证据；可选既有主题词表只帮助复用标签，不改变事实归属。
Token：每批最多 12 条、通常不超过 12000 字符；单条超长消息保持完整，词表默认最多 80 词。
缓存：内部辅助调用，不改变主对话稳定前缀；已保存结果恢复时不重复提取。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Dict, List, Tuple

from agent.messages import build_entity_scope, parse_entity_scope
from core.config import get_config_bool, get_config_int, register_configs_safe
from core.latebind import LateBinding
from core.log import log
from core.tags import tag_label

from .capture_state import CaptureBatch, CaptureItem, CaptureJournal, CaptureOutcome, CaptureState
from .dedup import (
    apply_update,
    gather_dedup_candidates,
    judge_write,
    light_llm,
    retain_duplicate_origin,
)
from .memory_types import MemoryEntry, MemoryType
from .ports import MemoryHost
from .provenance import capture_origin
from .tag_gate import enforce_tag_gate, topic_vocab_line

_MAX_NEW_MESSAGES = 12
_BATCH_MAX_CHARS = 12000
_MAX_BATCH_FAILURES = 5
_PENDING_BATCH_SIZE = 40
_MAX_PENDING_PER_TICK = 200


_EXTRACT_PROMPT = """\
你是记忆提取器。从以下对话片段中提取值得长期记住的信息，输出 JSON 数组。

## 输入格式
每行一条消息：[时间] 发言者: 内容。发言者带身份标注（称呼[uid:xxx] 或 [uid:xxx]），
群聊中不同 uid 是不同的人——必须分清每句话是谁说的，不得张冠李戴。
平台事件、平台通知和系统消息不是用户发言，不据其会话身份填写 speaker。
AI 的计划、推测和自述不是执行证据；保留条件、否定与不确定性，不把「准备做」写成「已完成」。
本批最多输出 {max_items} 条；没有值得提取的内容时输出 []。

## 提取标准
- fact：稳定的偏好、个人信息、约定、计划、知识（如"小明[uid:123] 说自己不吃辣"，主体是小明）
- event：发生的具体事件，带时间锚点（如"小李[uid:456] 周日去看了演唱会"）
- 不提取：寒暄客套、情绪性短句、常识、一次性指令、正在进行的任务过程

## importance 校准（结合实体记忆：主体是已识别的人时，身份/关系级信息从高档取值）
- 0.9-1.0：身份级事实——姓名/生日/住址/职业/重要人际关系，以及明确说"记住这个/帮我记住"
- 0.8：长期偏好与习惯、重要约定与承诺
- 0.7：阶段性计划与近期动态
- 0.6：一般偏好线索、次要观察
- 0.5：弱线索（不确定的照实标低分，不要在提取侧预过滤——下游有裁决）
importance 仅决定记忆的重要程度与留存优先级，不代表内容已经核实，请严格对照上表，不要一律给中间值。
{tag_vocab}
## 输出格式（只输出 JSON 数组，没有值得提取的内容时输出 []）
[{"content": "一两句话的记忆内容（用称呼写明主体是谁，禁止「用户」「有人」这类模糊指代）",
   "speaker": "该信息发言者的 uid（取自消息行的 [uid:xxx]；AI 说的或无法确定时省略）",
   "type": "fact" 或 "event",
   "topic": "主题词（一两个字）",
   "importance": 0.5到1.0（按上方校准表取值）,
   "sensitivity": "normal" 或 "private"（个人隐私/悄悄话标 private）,
   "date": "事件发生的日期 YYYY-MM-DD（仅 event 且能从对话确定时填写，否则省略）",
   "temporal_scope": "仅 event 填写：episode=一次性事件 / state=持续状态（会结束的：出差/养猫/减肥） / pattern=反复模式（拿不准用 pattern）"}]

【旧对话（仅供理解，不重复提取）】
{background}

【新对话（提取对象）】
{new_messages}
"""


def _extract_speaker(content: str) -> Tuple[str, str]:
    """从消息元数据标签确定性提取发言者身份，返回 (展示标签, uid)。

    标签在入库时由频道渲染（[uid:][name:] 前缀），只解析连续标签前缀，
    正文中用户手打的类标签语法不构成身份。无身份标签返回 ("", "")。
    """
    from core.tags import etag_all

    uid = ""
    name = ""
    kind = ""
    for key, value in etag_all(content, prefix_only=True):
        if key == "uid" and not uid:
            uid = value.strip()
        elif key == "name" and not name:
            name = value.strip()
        elif key == "kind" and not kind:
            kind = value
    event_label = {"event": "平台事件", "notification": "平台通知", "system": "系统消息"}.get(kind)
    if event_label:
        return event_label, ""
    if not uid:
        return "", ""
    label = name + tag_label("uid", uid)
    return label, uid


def should_extract(messages: List[Dict[str, Any]]) -> bool:
    """存在实质性正文即可提取，单条约定不依赖后续回复。"""
    from core.tags import strip_message_meta_tags

    return any(
        re.search(r"[一-鿿A-Za-z0-9]", strip_message_meta_tags(str(msg.get("content") or ""))) for msg in messages
    )


def parse_extraction(raw: str, *, max_items: int) -> list[CaptureItem]:
    """校验完整提取结果；有效空数组与解析失败严格区分。"""
    items = json.loads(raw)
    if not isinstance(items, list) or len(items) > max_items:
        raise ValueError("提取结果必须是预算内的 JSON 数组")
    return [CaptureItem.model_validate(item) for item in items]


class AutoCapturePipeline:
    """按会话串行消费消息，恢复已保存的提取结果与逐条写入进度。"""

    def __init__(self, memory: MemoryHost) -> None:
        self._memory = memory
        self._scope_locks: Dict[str, asyncio.Lock] = {}

    @property
    def _journal(self) -> CaptureJournal:
        store = self._memory.memory_store
        if store is None:
            raise RuntimeError("记忆存储未初始化")
        return CaptureJournal(store)

    async def run(self) -> None:
        """按预算扫描会话；未消费消息及在途批次在后续心跳继续。"""
        if not get_config_bool("memory_auto_capture_enabled", True):
            return
        store = self._memory.memory_store
        if not store:
            return
        try:
            from agent.runtime.singleton import require_runtime

            sqlite = require_runtime().data_center.sqlite
            # 轻量枚举（无 COUNT）：每 tick 调用，避免全表 GROUP BY
            scopes = await sqlite.list_conversation_scope_keys()
        except Exception as exc:
            log(f"自动捕获 scope 枚举失败: {exc}", "DEBUG", tag="记忆")
            return

        every_n = max(1, get_config_int("memory_auto_capture_every_n", 5))
        idle_seconds = max(30, get_config_int("memory_auto_capture_idle_seconds", 600))
        max_scopes = max(1, get_config_int("memory_auto_capture_max_scopes", 5))

        processed = 0
        now = time.time()
        for scope in scopes:
            if processed >= max_scopes:
                break
            try:
                outcome = await self._process_scope(
                    sqlite,
                    scope["scope_type"],
                    scope["scope_id"],
                    every_n=every_n,
                    idle_seconds=idle_seconds,
                    now=now,
                )
                if outcome is not CaptureOutcome.IDLE:
                    processed += 1
            except Exception as exc:
                log(
                    f"自动捕获失败 [{scope['scope_type']}:{scope['scope_id']}]: {exc}",
                    "DEBUG",
                    tag="记忆",
                )

    async def _fetch_pending(
        self,
        sqlite: Any,
        scope_type: str,
        scope_id: str,
        after_id: int,
    ) -> List[Dict[str, Any]]:
        """按提取游标分批拉取待处理消息（id 升序），单 tick 上限 _MAX_PENDING_PER_TICK。"""
        rows: List[Dict[str, Any]] = []
        cursor = after_id
        while len(rows) < _MAX_PENDING_PER_TICK:
            batch = await sqlite.fetch_conversation_with_id(
                scope_type=scope_type,
                scope_id=scope_id,
                limit=_PENDING_BATCH_SIZE,
                after_id=cursor,
            )
            if not batch:
                break
            rows.extend(batch)
            cursor = int(batch[-1]["id"])
            if len(batch) < _PENDING_BATCH_SIZE:
                break
        return rows[:_MAX_PENDING_PER_TICK]

    async def _process_scope(
        self,
        sqlite: Any,
        scope_type: str,
        scope_id: str,
        *,
        every_n: int,
        idle_seconds: int,
        now: float,
    ) -> CaptureOutcome:
        """串行消费一个会话批次，返回实际消费结果。"""
        lock = self._scope_locks.setdefault(f"{scope_type}:{scope_id}", asyncio.Lock())
        async with lock:
            return await self._process_scope_locked(
                sqlite,
                scope_type,
                scope_id,
                every_n=every_n,
                idle_seconds=idle_seconds,
                now=now,
            )

    async def _process_scope_locked(
        self,
        sqlite: Any,
        scope_type: str,
        scope_id: str,
        *,
        every_n: int,
        idle_seconds: int,
        now: float,
    ) -> CaptureOutcome:
        """消费一个有界批次；失败检查点保留，终止的批次写入审计。"""
        scope_key = f"{scope_type}:{scope_id}"
        journal = self._journal
        state = await journal.load(scope_key)
        if state.batch is None:
            rows = await self._fetch_pending(sqlite, scope_type, scope_id, state.last_msg_id)
            if not rows:
                return CaptureOutcome.IDLE
            uncounted = [r for r in rows if int(r["id"]) > state.counted_msg_id]
            state.pending_turns += sum(r["role"] == "user" for r in uncounted) or len(uncounted)
            state.counted_msg_id = max(state.counted_msg_id, int(rows[-1]["id"]))
            threshold = min(state.warmup_threshold, every_n)
            idle = now - int(rows[-1]["ts_ns"]) / 1e9 >= idle_seconds
            if state.pending_turns < threshold and not idle and len(rows) < _MAX_NEW_MESSAGES:
                await journal.save(scope_key, state)
                return CaptureOutcome.IDLE
            selected: list[dict[str, Any]] = []
            chars = 0
            for row in rows:
                size = len(str(row.get("content") or ""))
                if selected and (len(selected) >= _MAX_NEW_MESSAGES or chars + size > _BATCH_MAX_CHARS):
                    break
                selected.append(row)
                chars += size
            background = await sqlite.fetch_conversation_with_id(
                scope_type=scope_type,
                scope_id=scope_id,
                limit=3,
                before_id=int(selected[0]["id"]),
            )
            state.batch = CaptureBatch(
                rows=selected, background=[r for r in background if r["role"] in ("user", "assistant")]
            )
            await journal.save(scope_key, state)
        batch = state.batch
        messages = [r for r in batch.rows if r["role"] in ("user", "assistant")]
        from . import metrics

        metrics.incr("capture.batches")
        try:
            stored = await self._consume_batch(scope_key, state, messages)
        except asyncio.CancelledError:
            # 关停不是提取失败；重复重启不能耗尽重试额度或推进消息游标。
            await journal.save(scope_key, state)
            raise
        except Exception as exc:
            batch.failures += 1
            metrics.incr("capture.failed")
            detail = json.dumps(
                {
                    "scope": scope_key,
                    "message_ids": [r["id"] for r in batch.rows],
                    "attempt": batch.failures,
                    "next_item": batch.next_item,
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                    "abandoned": batch.failures >= _MAX_BATCH_FAILURES,
                },
                ensure_ascii=False,
            )
            if batch.failures >= _MAX_BATCH_FAILURES:
                self._advance(state, batch)
            await journal.save(scope_key, state, failure=detail)
            log(f"自动捕获失败: {detail}", "WARNING", tag="记忆")
            return CaptureOutcome.FAILED
        self._advance(state, batch)
        state.warmup_threshold = min(state.warmup_threshold * 2, every_n)
        await journal.save(scope_key, state)
        metrics.incr("capture.extracted", stored)
        if stored:
            from .embedding import wake_embedding_worker

            wake_embedding_worker()
        if get_config_bool("memory_auto_capture_extract_relations", True):
            try:
                await self._extract_relations(scope_key, messages)
            except Exception as exc:
                log(f"关系提取失败 [{scope_key}]: {exc}", "WARNING", tag="记忆")
        return CaptureOutcome.COMPLETED

    @staticmethod
    def _advance(state: CaptureState, batch: CaptureBatch) -> None:
        """结束已处理或明确终止的批次，保留尚未消费的计数。"""
        state.last_msg_id = int(batch.rows[-1]["id"])
        consumed = sum(r["role"] == "user" for r in batch.rows) or len(batch.rows)
        state.pending_turns = max(0, state.pending_turns - consumed)
        state.batch = None

    async def _consume_batch(
        self,
        scope_key: str,
        state: CaptureState,
        messages: list[dict[str, Any]],
    ) -> int:
        """先保存提取结果，再按出处回执恢复写入，反馈独立于新事实数量。"""
        batch = state.batch
        assert batch is not None
        journal = self._journal
        if batch.items is None:
            batch.items = await self._extract(messages, batch.background) if should_extract(messages) else []
            await journal.save(scope_key, state)
        stored = 0
        while batch.next_item < len(batch.items):
            key = batch.item_key(scope_key, batch.next_item)
            if not await journal.has_item(key):
                stored += await self._store_item(
                    scope_key, [*batch.background, *messages], batch.items[batch.next_item], key
                )
            batch.next_item += 1
            await journal.save(scope_key, state)
        if not batch.feedback_done:
            if get_config_bool("memory_reflection_feedback_enabled", True):
                from .reflection_lifecycle import classify_reflection_feedback

                await classify_reflection_feedback(journal.store, scope_key, messages)
            batch.feedback_done = True
            await journal.save(scope_key, state)
        return stored

    async def _extract(self, messages: list[dict[str, Any]], background: list[dict[str, Any]]) -> list[CaptureItem]:
        """发送整条消息，保持时间、真实发言者及正文中的限定条件。"""
        from core.sanitizer import truncate_middle
        from core.tags import strip_message_meta_tags

        def render(msg: dict[str, Any], *, background: bool = False) -> str:
            day = time.strftime("%m-%d %H:%M", time.localtime(int(msg["ts_ns"]) / 1e9))
            who = (_extract_speaker(msg["content"])[0] or "用户") if msg["role"] == "user" else "AI"
            content = strip_message_meta_tags(msg["content"])
            return f"[{day}] {who}: {truncate_middle(content, 2000) if background else content}"

        lines = [render(msg) for msg in messages]
        previous = "\n".join(render(msg, background=True) for msg in background)
        limit = max(1, get_config_int("memory_auto_capture_max_per_batch", 6))
        prompt = (
            _EXTRACT_PROMPT.replace("{max_items}", str(limit))
            .replace("{tag_vocab}", await self._topic_vocab_section())
            .replace("{background}", previous or "（无）")
            .replace("{new_messages}", "\n".join(lines))
        )
        raw = await light_llm(prompt, temperature=0.2, timeout=120.0)
        return parse_extraction(raw, max_items=limit)

    async def _topic_vocab_section(self) -> str:
        """高 df 主题词表段（topic 生成期复用抑制；未启用/无词表返回空串）。"""
        if not get_config_bool("memory_tag_gate_vocab_injection", True):
            return ""
        store = self._memory.memory_store
        if store is None:
            return ""
        try:
            tag_df = await store.list_tags()
        except Exception as exc:
            log(f"提取主题词表不可用: {exc}", "DEBUG", tag="记忆")
            return ""

        line = topic_vocab_line(tag_df)
        if not line:
            return ""
        return "\n【既有主题标签词表（topic 优先从中选取原词；确属新话题才造新词，禁止造表内词的近义变体）】\n" + line

    async def _store_item(
        self,
        scope_key: str,
        messages: list[dict[str, Any]],
        item: CaptureItem,
        key: str,
    ) -> int:
        """保存一个提取结果及其出处；写入失败交由批次检查点处理。"""
        store = self._journal.store
        scope_type, _, scope_id = scope_key.partition(":")
        adapter, sep, base_id = scope_id.partition(":")
        scope = build_entity_scope(scope_type, adapter if sep else "", base_id if sep else adapter)
        _, adapter, _, _ = parse_entity_scope(scope)
        metadata = capture_origin(scope, messages, capture_key=key)
        if item.sensitivity != "normal":
            metadata["sensitivity"] = item.sensitivity
        if item.type == "event":
            if item.date:
                metadata["activity_date"] = item.date
            if item.temporal_scope:
                metadata["temporal_scope"] = item.temporal_scope
        tags = [scope_key, f"type:{item.type}"]
        if item.topic:
            tags.append(f"topic:{item.topic}")
        speakers = {_extract_speaker(m["content"])[1] for m in messages if m["role"] == "user"}
        if item.speaker and item.speaker in speakers and adapter:
            speaker_tag = f"user:{adapter}:{item.speaker}"
            if speaker_tag not in tags:
                tags.append(speaker_tag)
        # 写入闸门：新 topic 标签的确定性归一与校验（fail-open，不阻塞写入）
        gate = await enforce_tag_gate(tags, store=store)
        tags = gate.tags
        if gate.rewrites or gate.dropped:
            log(
                f"标签闸门改写 [{scope_key}]: {gate.rewrites} dropped={gate.dropped}",
                "DEBUG",
                tag="记忆",
            )
        candidates = await gather_dedup_candidates(store, self._memory.embedder, item.content)
        decision = await judge_write(item.content, candidates, tags=tags)
        action = decision.get("action", "store")
        from . import metrics

        metrics.incr(f"write.dedup_llm_{action}")
        if action == "skip" and decision.get("target_id"):
            if await retain_duplicate_origin(store, int(decision["target_id"]), metadata):
                return 0
        if action == "update" and decision.get("target_id"):
            updated = await apply_update(
                store,
                int(decision["target_id"]),
                str(decision.get("content") or item.content),
                tags,
                actor="auto_capture",
                metadata=metadata,
            )
            if updated is not None:
                return 1
        await store.add(
            MemoryEntry(
                memory_type=MemoryType.EPISODIC if item.type == "event" else MemoryType.SEMANTIC,
                content=item.content,
                source="auto_capture",
                tags=tags,
                importance=item.importance,
                metadata=metadata,
            ),
            actor="auto_capture",
        )
        return 1

    async def _extract_relations(
        self,
        scope_key: str,
        messages: List[Dict[str, Any]],
    ) -> int:
        """从提取批次中抽取关系事实落库到图谱（复用 graph.extract 的 prompt/解析）。"""
        from .graph.extract import (
            build_extract_prompt,
            parse_relation_candidates,
            render_material,
        )

        store = self._memory.memory_store
        assert store is not None
        material = render_material(messages)
        if len(material) < 30:
            return 0
        parts = scope_key.split(":")
        adapter = parts[1] if len(parts) >= 3 else ""
        prompt = build_extract_prompt(scope_key, scope_key, adapter, material)
        try:
            raw = await light_llm(prompt, temperature=0.2, timeout=120.0)
        except Exception as exc:
            log(f"关系抽取调用失败 [{scope_key}]: {exc}", "DEBUG", tag="记忆")
            return 0
        candidates = parse_relation_candidates(raw or "")
        stored = 0
        for cand in candidates:
            try:
                await store.graph.add_relation(
                    cand["subject"],
                    cand["predicate"],
                    cand["object"],
                    subject_label=cand["subject_label"],
                    object_label=cand["object_label"],
                    symmetric=cand["symmetric"],
                    strength=cand["strength"],
                    evidence=cand["evidence"],
                    origin="auto_capture",
                )
                stored += 1
            except ValueError as exc:
                log(f"关系候选落库跳过: {cand.get('predicate')} -> {exc}", "DEBUG", tag="记忆")
        return stored


auto_capture_port: LateBinding["AutoCapturePipeline"] = LateBinding("memory.auto_capture")
"""管线端口：Mind 构造时创建管线实例，agent.runtime.wiring 统一施绑。"""


def _pipeline() -> "AutoCapturePipeline":
    """取自动捕获管线（bootstrap 接线前访问抛 WireError）。"""
    return auto_capture_port.get()


async def run_auto_capture(mind: MemoryHost) -> None:
    """心跳入口；按轮数、空闲门槛和会话预算恢复持久化进度。"""
    await _pipeline().run()


async def flush_scope_capture(mind: MemoryHost, scope_key: str) -> bool:
    """单 scope 强制提取：上下文压缩前抢跑沉淀（PreCompact flush）。

    压缩前消费一个待定批次，降低未沉淀细节离开上下文的风险。
    idle_seconds=0：有待提取内容即视为到期，剩余积压由后续捕获继续消费。

    fail-open：未启用 / 无待提取内容 / 运行时不可用均返回 False，
    失败由管线记录。实际消费批次返回 True。
    """
    if not mind.memory_store or not get_config_bool("memory_auto_capture_enabled", True):
        return False
    scope_type, _, scope_id = scope_key.partition(":")
    if scope_type not in ("user", "group") or not scope_id:
        return False
    try:
        from agent.runtime.singleton import require_runtime

        sqlite = require_runtime().data_center.sqlite
    except Exception:
        return False
    every_n = max(1, get_config_int("memory_auto_capture_every_n", 5))
    outcome = await _pipeline()._process_scope(
        sqlite,
        scope_type,
        scope_id,
        every_n=every_n,
        idle_seconds=0,
        now=time.time(),
    )
    return outcome is CaptureOutcome.COMPLETED


_CAPTURE_CONFIGS = {
    "memory/capture": {
        "memory_auto_capture_enabled": {
            "description": "是否在对话达阈值/空闲时自动提取事实进长期记忆",
            "default": True,
        },
        "memory_auto_capture_every_n": {
            "description": "每积累 N 轮新对话触发一次提取",
            "default": 5,
            "advanced": True,
            "unit": "次",
        },
        "memory_auto_capture_idle_seconds": {
            "description": "会话静默超过该时长后提取待定内容（空闲防抖）",
            "default": 600,
            "advanced": True,
            "unit": "秒",
        },
        "memory_auto_capture_max_scopes": {
            "description": "单次心跳最多处理的会话数",
            "default": 5,
            "advanced": True,
            "unit": "个",
        },
        "memory_auto_capture_max_per_batch": {
            "description": "单批最多提取的记忆条数",
            "default": 6,
            "advanced": True,
            "unit": "条",
        },
        "memory_auto_capture_extract_relations": {
            "description": "是否在提取事实的同时抽取实体关系进图谱",
            "default": True,
            "advanced": True,
        },
        "memory_precompact_flush_enabled": {
            "description": "上下文压缩前先强制提取该会话的待定记忆（防止压缩摘要丢失未沉淀的细节）",
            "default": True,
        },
        "memory_precompact_flush_timeout": {
            "description": "压缩前记忆提取的最大等待时长（超时直接压缩，不阻塞对话）",
            "default": 20,
            "advanced": True,
            "unit": "秒",
        },
    },
}

register_configs_safe(_CAPTURE_CONFIGS)
