"""自动记忆捕获管线：对话 → 长期记忆的确定性提取（不依赖 LLM 自觉调 memorize）。

每个心跳 tick 检查各会话 scope 的新消息，命中任一触发条件即提取：

1. 轮数阈值：未提取的新对话轮数达到当前阈值（memory_auto_capture_every_n）；
2. 空闲防抖：有待提取内容且会话静默超过 memory_auto_capture_idle_seconds
   （对话告一段落时自动整理）；
3. Warm-up：新 scope 的提取阈值按 1→2→4→…→every_n 指数爬升，
   会话早期（信息最密集的阶段）记忆被更快固化。

提取流程：质量门（过滤纯寒暄批次）→ 单次 LLM 提取（JSON 数组）→
逐条经 dedup 模块语义裁决（store/skip/update）→ 写库。
游标按 scope 持久化（capture_cursors 表），进程重启后从未处理消息继续。

发言者归属：消息行渲染时从元数据标签确定性提取身份（称呼[uid:xxx]），
提取输出的 speaker 字段经批次内真实 uid 集合校验后挂为 user:{adapter}:{uid}
记忆标签——每条记忆天生携带"谁说的"，下游召回/任务可核验归属（读取侧的
归属标注渲染见 memory_retriever._humanize_entity_tags）。

Model Experience（提取 prompt 说话人身份）:
- 模型看到什么：消息行从"[时间] 用户: 内容"变为"[时间] 称呼[uid:xxx]: 内容"，
  prompt 新增输入格式说明与 speaker 输出字段（群聊多人可区分）
- token 影响：每条消息 +8~15 字符，prompt 指令 +约 60 字符（每批提取一次）
- 缓存影响：无（内部小调用，不共享主对话前缀）
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from core.config import get_config_bool, get_config_int, register_configs_safe
from core.latebind import LateBinding
from core.log import log

from .dedup import apply_update, gather_dedup_candidates, judge_write, light_llm
from .memory_types import MemoryEntry, MemoryType

if TYPE_CHECKING:
    from agent.mind.mind import Mind

_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)

# 单条消息送入提取 prompt 的截断长度
_MSG_MAX_CHARS = 300
# 提取 prompt 中新消息/背景消息条数上限
_MAX_NEW_MESSAGES = 12
_MAX_BACKGROUND_MESSAGES = 3
# 连续提取失败上限：达到后放弃本批（游标推进，避免毒批次卡死管线）
_MAX_BATCH_FAILURES = 5
# 待提取消息的单批拉取窗口与单 tick 处理上限（积压超限时后续 tick 继续消费）
_PENDING_BATCH_SIZE = 40
_MAX_PENDING_PER_TICK = 200


def _batch_signature(rows: List[Dict[str, Any]]) -> str:
    """批次指纹：消息 id 序列哈希，用于崩溃恢复后的重复提取识别。"""
    if not rows:
        return ""
    ids = ",".join(str(int(r["id"])) for r in rows)
    return hashlib.sha1(ids.encode()).hexdigest()

_EXTRACT_PROMPT = """\
你是记忆提取器。从以下对话片段中提取值得长期记住的信息，输出 JSON 数组。

## 输入格式
每行一条消息：[时间] 发言者: 内容。发言者带身份标注（称呼[uid:xxx] 或 [uid:xxx]），
群聊中不同 uid 是不同的人——必须分清每句话是谁说的，不得张冠李戴。

## 提取标准
- fact：稳定的偏好、个人信息、约定、计划、知识（如"小明[uid:123] 说自己不吃辣"，主体是小明）
- event：发生的具体事件，带时间锚点（如"小李[uid:456] 周日去看了演唱会"）
- 不提取：寒暄客套、情绪性短句、常识、一次性指令、正在进行的任务过程

## 输出格式（只输出 JSON 数组，没有值得提取的内容时输出 []）
[{"content": "一两句话的记忆内容（用称呼写明主体是谁，禁止「用户」「有人」这类模糊指代）",
   "speaker": "该信息发言者的 uid（取自消息行的 [uid:xxx]；AI 说的或无法确定时省略）",
   "type": "fact" 或 "event",
   "topic": "主题词（一两个字）",
   "importance": 0.5到1.0（重要约定/承诺 0.8 以上）,
   "sensitivity": "normal" 或 "private"（个人隐私/悄悄话标 private）,
   "date": "事件发生的日期 YYYY-MM-DD（仅 event 且能从对话确定时填写，否则省略）"}]

【背景（最近的旧对话，仅供理解）】
{background}

【新对话（提取对象）】
{new_messages}
"""

# 消息元数据标签头部扫描窗口（标签由频道渲染在内容前缀处）
_META_HEAD_CHARS = 200


def _extract_speaker(content: str) -> Tuple[str, str]:
    """从消息元数据标签确定性提取发言者身份，返回 (展示标签, uid)。

    标签在入库时由频道渲染（[uid:][name:][nickname:] 前缀），只扫描头部窗口，
    正文中用户手打的类标签语法不构成身份。无身份标签返回 ("", "")。
    """
    from core.tags import etag_all

    uid = ""
    name = ""
    for key, value in etag_all(content[:_META_HEAD_CHARS]):
        if key == "uid" and not uid:
            uid = value.strip()
        elif key == "nickname" and value.strip():
            name = value.strip()  # 群昵称优先于用户名
        elif key == "name" and not name:
            name = value.strip()
    if not uid:
        return "", ""
    label = f"{name}[uid:{uid}]" if name else f"[uid:{uid}]"
    return label, uid


def should_extract(messages: List[Dict[str, Any]]) -> bool:
    """质量门：实质性消息（非寒暄/表情/过短）不足 2 条的批次不值得提取。"""
    substantive = 0
    for msg in messages:
        content = (msg.get("content") or "").strip()
        # 至少 8 个字符且不只是标点/表情符号
        if len(content) >= 8 and re.search(r"[一-鿿A-Za-z0-9]{4}", content):
            substantive += 1
    return substantive >= 2


def parse_extraction(raw: str, *, max_items: int) -> List[Dict[str, Any]]:
    """解析 LLM 提取输出（提取首个 JSON 数组 + 字段校验 + 数量截断）。"""
    m = _JSON_ARRAY_RE.search(raw or "")
    if not m:
        return []
    try:
        items = json.loads(m.group(0))
    except json.JSONDecodeError:
        try:
            items = json.loads(re.sub(r",\s*([}\]])", r"\1", m.group(0)))
        except json.JSONDecodeError:
            return []
    if not isinstance(items, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_content = item.get("content")
        if not isinstance(raw_content, str):
            continue
        content = raw_content.strip()
        if len(content) < 4:
            continue
        try:
            importance = max(0.3, min(1.0, float(item.get("importance", 0.6))))
        except (TypeError, ValueError):
            importance = 0.6
        sensitivity = str(item.get("sensitivity", "normal")).strip().lower()
        date = str(item.get("date", "")).strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            date = ""
        # 发言者 uid（与批次内真实出现的 uid 的交叉校验在写库侧进行）
        speaker = str(item.get("speaker", "")).strip()[:64]
        out.append({
            "content": content,
            "type": "event" if item.get("type") == "event" else "fact",
            "topic": str(item.get("topic", "")).strip(),
            "importance": importance,
            "sensitivity": sensitivity if sensitivity in ("private", "secret") else "normal",
            "date": date,
            "speaker": speaker,
        })
        if len(out) >= max_items:
            break
    return out


class _ScopeState:
    """单个 scope 的提取状态（capture_cursors 表一行）。

    双游标：counted_msg_id 统计已计入 pending 的消息（防重复计数），
    last_msg_id 标记已提取完成的位置（提取批次取 id > last_msg_id 的消息）。
    """

    __slots__ = ("last_msg_id", "counted_msg_id", "pending_turns",
                 "warmup_threshold", "failures", "last_batch_sig")

    def __init__(
        self,
        last_msg_id: int = 0,
        counted_msg_id: int = 0,
        pending_turns: int = 0,
        warmup_threshold: int = 1,
        failures: int = 0,
        last_batch_sig: str = "",
    ) -> None:
        self.last_msg_id = last_msg_id
        self.counted_msg_id = counted_msg_id
        self.pending_turns = pending_turns
        self.warmup_threshold = warmup_threshold
        self.failures = failures
        # 最近成功提取批次的消息 id 指纹：崩溃恢复后同批次跳过重复提取
        self.last_batch_sig = last_batch_sig


class AutoCapturePipeline:
    """自动捕获调度器：每 tick 扫描各 scope，满足触发条件即提取。"""

    def __init__(self, mind: "Mind") -> None:
        self._mind = mind
        self._states: Dict[str, _ScopeState] = {}
        # 每 scope 一把锁：心跳 tick 与压缩前抢跑（PreCompact flush）可能并发
        # 处理同一 scope，串行化避免同批次被重复提取
        self._scope_locks: Dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------
    # 游标持久化（memory DB 的 capture_cursors 表）
    # ------------------------------------------------------------------

    async def _load_state(self, scope_key: str) -> _ScopeState:
        if scope_key in self._states:
            return self._states[scope_key]
        state = _ScopeState()
        store = self._mind.memory_store
        if store:
            try:
                db = await store._get_db()
                cursor = await db.execute(
                    "SELECT last_msg_id, counted_msg_id, pending_turns, warmup_threshold, last_batch_sig "
                    "FROM capture_cursors WHERE scope_key=?",
                    (scope_key,),
                )
                row = await cursor.fetchone()
                if row:
                    state = _ScopeState(
                        last_msg_id=int(row["last_msg_id"]),
                        counted_msg_id=int(row["counted_msg_id"]),
                        pending_turns=int(row["pending_turns"]),
                        warmup_threshold=int(row["warmup_threshold"]),
                        last_batch_sig=str(row["last_batch_sig"] or ""),
                    )
            except Exception as exc:
                log(f"捕获游标读取失败 [{scope_key}]: {exc}", "DEBUG", tag="记忆")
        self._states[scope_key] = state
        return state

    async def _save_state(self, scope_key: str, state: _ScopeState) -> None:
        store = self._mind.memory_store
        if not store:
            return
        try:
            db = await store._get_db()
            async with store._tx(db):
                await db.execute(
                    "INSERT OR REPLACE INTO capture_cursors"
                    "(scope_key, last_msg_id, counted_msg_id, pending_turns, warmup_threshold, last_batch_sig, updated_ns) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (scope_key, state.last_msg_id, state.counted_msg_id,
                     state.pending_turns, state.warmup_threshold, state.last_batch_sig,
                     time.time_ns()),
                )
        except Exception as exc:
            log(f"捕获游标保存失败 [{scope_key}]: {exc}", "DEBUG", tag="记忆")

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """扫描所有会话 scope，对命中触发条件的执行提取。"""
        if not get_config_bool("memory_auto_capture_enabled", True):
            return
        store = self._mind.memory_store
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
                if await self._process_scope(
                    sqlite, scope["scope_type"], scope["scope_id"],
                    every_n=every_n, idle_seconds=idle_seconds, now=now,
                ):
                    processed += 1
            except Exception as exc:
                log(
                    f"自动捕获失败 [{scope['scope_type']}:{scope['scope_id']}]: {exc}",
                    "DEBUG", tag="记忆",
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
                scope_type=scope_type, scope_id=scope_id,
                limit=_PENDING_BATCH_SIZE, after_id=cursor,
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
    ) -> bool:
        """处理单个 scope（每 scope 串行）；实际执行了提取返回 True。"""
        lock = self._scope_locks.setdefault(f"{scope_type}:{scope_id}", asyncio.Lock())
        async with lock:
            return await self._process_scope_locked(
                sqlite, scope_type, scope_id,
                every_n=every_n, idle_seconds=idle_seconds, now=now,
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
    ) -> bool:
        """处理单个 scope；实际执行了提取返回 True。"""
        scope_key = f"{scope_type}:{scope_id}"
        state = await self._load_state(scope_key)

        # 按提取游标分批拉取（id 升序）：积压超过单批窗口时逐批消费，
        # 游标只推进到实际处理的最大 id，不会因窗口截断永久漏提
        rows = await self._fetch_pending(
            sqlite, scope_type, scope_id, state.last_msg_id,
        )
        uncounted = [r for r in rows if int(r["id"]) > state.counted_msg_id]
        if not uncounted and not state.pending_turns:
            return False
        latest_id = int(rows[-1]["id"]) if rows else state.counted_msg_id
        pending_rows = rows
        if not pending_rows:
            return False
        latest_ts = int(pending_rows[-1]["ts_ns"]) / 1e9

        # 新消息轮数（一条 user 消息≈一轮；只统计未计过数的消息）
        new_turns = sum(1 for r in uncounted if r["role"] == "user") or len(uncounted)
        pending_turns = state.pending_turns + new_turns
        state.counted_msg_id = max(state.counted_msg_id, latest_id)

        threshold = min(state.warmup_threshold, every_n)
        triggered_by_turns = pending_turns >= threshold
        triggered_by_idle = bool(pending_turns) and (now - latest_ts) >= idle_seconds

        if not (triggered_by_turns or triggered_by_idle):
            # 未触发：只累计轮数（提取游标不动，下轮仍需拿到这批消息）
            state.pending_turns = pending_turns
            await self._save_state(scope_key, state)
            return False

        # 触发（或质量门放弃）：本批消息处理完毕，提取游标推进到最新
        messages = [
            {"role": r["role"], "content": r["content"], "ts_ns": r["ts_ns"]}
            for r in pending_rows
            if r["role"] in ("user", "assistant")
        ]
        from . import metrics
        if not should_extract(messages):
            metrics.incr("capture.skipped_low_quality")
            state.last_msg_id = latest_id
            state.pending_turns = 0
            await self._save_state(scope_key, state)
            return False

        batch_sig = _batch_signature(pending_rows)
        if batch_sig and batch_sig == state.last_batch_sig:
            # 崩溃恢复：该批次已提取落库但游标未及保存，直接推进避免重复提取
            state.last_msg_id = latest_id
            state.pending_turns = 0
            state.failures = 0
            await self._save_state(scope_key, state)
            log(f"自动捕获 [{scope_key}]: 批次已提取（崩溃恢复），跳过重复提取", "DEBUG", tag="记忆")
            return False

        extracted = await self._extract_and_store(scope_key, messages)
        metrics.incr("capture.batches")
        if extracted is None:
            # 提取失败：提取游标不推进，pending 保留，下轮重试（有限次）
            state.failures += 1
            state.pending_turns = pending_turns
            metrics.incr("capture.failed")
            if state.failures >= _MAX_BATCH_FAILURES:
                log(f"自动捕获连续 {state.failures} 次失败，放弃本批 [{scope_key}]", "WARNING", tag="记忆")
                state.pending_turns = 0
                state.failures = 0
                state.last_msg_id = latest_id
            await self._save_state(scope_key, state)
            return False

        # 成功：游标推进、重置计数，warm-up 阈值指数爬升直至 every_n
        metrics.incr("capture.extracted", extracted)
        state.last_msg_id = latest_id
        state.pending_turns = 0
        state.failures = 0
        state.last_batch_sig = batch_sig
        state.warmup_threshold = min(state.warmup_threshold * 2, every_n)
        await self._save_state(scope_key, state)
        if extracted:
            log(f"自动捕获 [{scope_key}]: 提取 {extracted} 条记忆", tag="记忆")
        return True

    async def _extract_and_store(
        self, scope_key: str, messages: List[Dict[str, Any]],
    ) -> Optional[int]:
        """LLM 提取 + 逐条语义裁决写库。失败返回 None，成功返回写入/更新条数。"""
        from core.tags import strip_message_meta_tags

        # 批次内真实出现的发言者 uid 集合：写库时校验 LLM 输出的 speaker，
        # 不在集合内的视为幻觉丢弃（身份只能来自消息元数据，不信模型编造）
        speaker_uids: set[str] = set()

        def _fmt(msg: Dict[str, Any]) -> str:
            day = time.strftime("%m-%d %H:%M", time.localtime(int(msg["ts_ns"]) / 1e9))
            if msg["role"] == "user":
                label, uid = _extract_speaker(msg["content"])
                if uid:
                    speaker_uids.add(uid)
                who = label or "用户"
            else:
                who = "AI"
            content = strip_message_meta_tags(msg["content"])[:_MSG_MAX_CHARS]
            return f"[{day}] {who}: {content}"

        new_part = messages[-_MAX_NEW_MESSAGES:]
        background_part = messages[:-_MAX_NEW_MESSAGES][-_MAX_BACKGROUND_MESSAGES:]
        prompt = _EXTRACT_PROMPT.replace(
            "{background}", "\n".join(_fmt(m) for m in background_part) or "（无）"
        ).replace(
            "{new_messages}", "\n".join(_fmt(m) for m in new_part)
        )

        try:
            raw = await light_llm(prompt, temperature=0.2, timeout=120.0)
        except Exception as exc:
            # LLM 未配置/超时/故障：返回 None 走失败重试计数（有界），
            # 避免异常直接穿透导致每个 tick 无限重试毒 scope
            log(f"自动捕获提取调用失败 [{scope_key}]: {exc}", "DEBUG", tag="记忆")
            return None
        max_items = max(1, get_config_int("memory_auto_capture_max_per_batch", 6))
        items = parse_extraction(raw, max_items=max_items)
        if not items:
            return 0

        store = self._mind.memory_store
        assert store is not None
        scope_tag = scope_key  # scope_key 即 "user:qq:123" / "group:qq:456" 标签格式
        parts = scope_key.split(":")
        adapter = parts[1] if len(parts) >= 3 else ""
        stored = 0
        for item in items:
            tags = [scope_tag, f"type:{item['type']}"]
            if item["topic"]:
                tags.append(f"topic:{item['topic']}")
            # 发言者归属标签：每条记忆天生携带"谁说的"，下游召回/任务可核验归属
            speaker = item.get("speaker", "")
            if speaker:
                if speaker in speaker_uids and adapter:
                    speaker_tag = f"user:{adapter}:{speaker}"
                    if speaker_tag != scope_tag:
                        tags.append(speaker_tag)
                else:
                    log(
                        f"自动捕获 [{scope_key}]: speaker {speaker!r} 不在批次发言者中，已丢弃",
                        "DEBUG", tag="记忆",
                    )
            try:
                candidates = await gather_dedup_candidates(
                    store, self._mind.embedder, item["content"],
                )
                decision = await judge_write(item["content"], candidates)
                action = decision.get("action", "store")
                from . import metrics
                metrics.incr(f"write.dedup_llm_{action}")
                if action == "skip":
                    continue
                if action == "update" and decision.get("target_id"):
                    updated = await apply_update(
                        store, int(decision["target_id"]),
                        str(decision.get("content") or item["content"]), tags,
                    )
                    if updated is not None:
                        stored += 1
                        continue
                if action == "merge" and decision.get("target_ids"):
                    new_id = await store.merge_memories(
                        [int(i) for i in decision["target_ids"]],
                        str(decision.get("content") or item["content"]),
                    )
                    if new_id:
                        stored += 1
                        continue
                metadata: Dict[str, Any] = {}
                if item["sensitivity"] != "normal":
                    metadata["sensitivity"] = item["sensitivity"]
                if item["date"] and item["type"] == "event":
                    metadata["activity_date"] = item["date"]
                entry = MemoryEntry(
                    memory_type=(
                        MemoryType.EPISODIC if item["type"] == "event"
                        else MemoryType.SEMANTIC
                    ),
                    content=item["content"],
                    source="auto_capture",
                    tags=tags,
                    importance=item["importance"],
                    metadata=metadata,
                )
                await store.add(entry)
                stored += 1
            except Exception as exc:
                log(f"自动捕获写入失败 [{scope_key}]: {exc}", "DEBUG", tag="记忆")
        if stored:
            from .embedding import wake_embedding_worker
            wake_embedding_worker()

        # 关系抽取联动：同一批对话材料顺手提炼结构化关系进图谱
        # （此前关系只在画像分析 60 轮阈值时抽取，跨人交叉知识沉淀太慢）
        if get_config_bool("memory_auto_capture_extract_relations", True):
            try:
                relations = await self._extract_relations(scope_key, messages)
                if relations:
                    from . import metrics
                    metrics.incr("capture.relations", relations)
                    log(f"自动捕获 [{scope_key}]: +{relations} 条图谱关系", tag="记忆")
            except Exception as exc:
                log(f"自动捕获关系抽取失败 [{scope_key}]: {exc}", "DEBUG", tag="记忆")
        return stored

    async def _extract_relations(
        self, scope_key: str, messages: List[Dict[str, Any]],
    ) -> int:
        """从提取批次中抽取关系事实落库到图谱（复用 graph.extract 的 prompt/解析）。"""
        from .graph.extract import (
            build_extract_prompt,
            parse_relation_candidates,
            render_material,
        )

        store = self._mind.memory_store
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
                    cand["subject"], cand["predicate"], cand["object"],
                    subject_label=cand["subject_label"],
                    object_label=cand["object_label"],
                    symmetric=cand["symmetric"], strength=cand["strength"],
                    evidence=cand["evidence"], origin="auto_capture",
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


async def run_auto_capture(mind: "Mind") -> None:
    """心跳集成入口：每个 tick 调用一次。"""
    await _pipeline().run()


async def flush_auto_capture(mind: "Mind") -> None:
    """进程退出前的兜底提取：所有有待提取内容的 scope 立即处理（不等阈值/空闲）。

    对应触发器的第三轨（shutdown flush）：未达轮数阈值且未满空闲时间的
    对话批次，在优雅关停时强制提取，避免短期会话的记忆随进程退出丢失。
    """
    pipeline = _pipeline()
    store = mind.memory_store
    if not store or not get_config_bool("memory_auto_capture_enabled", True):
        return
    try:
        from agent.runtime.singleton import require_runtime
        sqlite = require_runtime().data_center.sqlite
        scopes = await sqlite.list_conversation_scope_keys()
    except Exception:
        return
    every_n = max(1, get_config_int("memory_auto_capture_every_n", 5))
    now = time.time()
    for scope in scopes:
        try:
            state = await pipeline._load_state(f"{scope['scope_type']}:{scope['scope_id']}")
            if state.pending_turns <= 0:
                continue
            # idle_seconds=0：有待提取内容即视为到期
            await pipeline._process_scope(
                sqlite, scope["scope_type"], scope["scope_id"],
                every_n=every_n, idle_seconds=0, now=now,
            )
        except Exception as exc:
            log(f"关停兜底提取失败 [{scope.get('scope_id')}]: {exc}", "DEBUG", tag="记忆")


async def flush_scope_capture(mind: "Mind", scope_key: str) -> bool:
    """单 scope 强制提取：上下文压缩前抢跑沉淀（PreCompact flush）。

    上下文压缩只裁剪内存中的消息链，但被裁掉的细节若尚未经 auto_capture
    沉淀，本会话后续轮次就无法召回——抢在压缩前把该 scope 的待定对话
    提取为长期记忆（信息不失帧）。idle_seconds=0：有待提取内容即视为
    到期（与关停兜底路径同语义）。

    fail-open：未启用 / 无待提取内容 / 运行时不可用均返回 False，
    是否记录日志由调用方决定。实际执行了提取返回 True。
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
    return await _pipeline()._process_scope(
        sqlite, scope_type, scope_id,
        every_n=every_n, idle_seconds=0, now=time.time(),
    )


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
