# 记忆系统

记忆存储、召回枢纽、图谱与 cognee 集成、遗忘治理、证据与反思生命周期的现状说明。修改
`agent/memory/` 相关模块时按需阅读；缓存红线见[上下文缓存分册](context-cache.md)。

## 存储与检索基座

- **`agent/memory/memory_store.py`**：长期记忆存储（SQLite + FTS5 + Embedding 混合检索；
  软归档遗忘 + importance 松弛回归）。重要性松弛带访问护盾：有效速率 ÷ (1 + ln(access_count))，
  历史访问越多的记忆向基线回归越慢（常被想起的记忆更抗遗忘）
- **写入判重**（`agent/memory/dedup.py::judge_write`）：判断段（关系四选一 novel/covered/
  evolution/fragments + update 目标选择 + merge 逐候选是非）经判断引擎一次调用并行评判，
  仅 update/merge 再经 light_llm 合成合并文本（store/skip 占写入绝大多数，热路径零 LLM 调用）；
  关系/目标置信度低于 `memory_dedup_min_confidence`（默认 0.25）或任何判断/合成失败一律保守退回
  直接写入——**去重永不阻塞写入路径**。判断模型只看 1..N 候选序号（真实 id 转述易抄错），
  裁决返回前映射回真实 id
- **recall 工具**：source 标志 + depth 浅深 + filter_tags 硬过滤 + forgotten 字段（见遗忘层兜底召回）

## 召回枢纽：LLM 检索规划 + 异步深探

首轮同步召回由 LLM 规划驱动（非被动关键词匹配），LLM 思考期间异步深检索增量注入（一块连续记忆面、
全程防重复）：

| 机制 | 位置 | 说明 |
|------|------|------|
| LLM 检索规划 | `memory_retriever.plan_retrieval`（公开 API，受 `memory_query_rewrite_enabled` 门控） | 轻量 LLM（light_llm 通道，可经 `memory_light_model` 指定）把对话尾部转成结构化计划 `{queries(1-3 互补), entities, deep_needed, rationale}`，失败/超时回退原查询单发（预算 `memory_plan_budget_seconds`，0 = 按召回总超时 40% 份额派生）。多计划查询并行经 federated_search 后 `merge_consensus` 融合：同键（anelf_memory_id / 内容哈希）取最高分，≥2 查询命中 ×1.1 共识加成。计划实体经 `GraphStore.resolve_nodes_for_tags` 解析为图谱节点 → 原生一跳邻域 + cognee `node_name` 定向检索 |
| 召回预算与规划并行 | `memory_retriever` recall 路径 | 检索规划与多路检索**并行**：原查询 lane 先行（复用预计算 query_vec 零 embed），规划慢/超预算只损失多查询增强；总时限经 `asyncio.wait` **非破坏式收尾**——到点只收割已落地 lane，已落地的基础/焦点/计划 lane 照常融合返回，全空才回退近期记忆。超时不再丢弃整轮召回 |
| 异步深探 + provider 注入 | `agent/memory/probe.py`（DeepProbeHub + RecallLedger + provider `memory_deep_probe`） | 回复路径（recollection `fire_probe=True`，心跳/任务/子代理零影响）检索完成后：规划判定 `deep_needed` 或已解析实体节点时 `spawn()` 异步深探（在 5s 召回超时之外与 LLM 首轮思考并行）：cognee GRAPH_COMPLETION / GRAPH_COMPLETION_CONTEXT_EXTENSION / node_name 定向 + 原生图谱邻域。分两阶段刷新（快段原生图谱邻域毫秒级先渲染，慢段 cognee LLM 检索完成后并入）；完成后写入 hub 持久渲染缓存，经 provider `memory_deep_probe`（priority 34、`memory_probe_inject` 门控）每轮读取注入——完成前为空零注入，完成后每轮在场且字节稳定，新回复 begin_reply 重置。provider 消息不进压缩历史（每轮重新收集），位于最新工具结果之后注意力最强处。配置 `memory/probe` 组（enabled/max_chars 1600/timeout 60s） |
| 召回账本（三键防重复） | `probe.RecallLedger`（键归一权威 `memory_types.normalized_content_key`） | per-reply 三键集合（结果 id / 图谱边 id / 内容归一前缀），三条召回通道共用：基底层注入、AI recall 工具返回（tools.py 记账）、异步深探渲染前查账——一次回复内同一事实只出现一次；`begin_reply` 重置 |
| cognee 检索面 | `cognee/fusion.py` + `cognee/config.py` | `federated_search`/`search_cognee` 支持 `node_names` → cognee `recall(node_name=...)` 定向检索；`parse_memory_projection` 在边界解析投影文档头（归属标注/上下文加权/联想种子对 cognee 结果同样生效）；深类型（GRAPH_COMPLETION 等）只经探针（异步）与 recall 工具（显式）发生，被动召回保持轻量 |
| 召回测试面板 | `POST /api/memory/recall-test` + 前端 `pages/memory/RecallTester.tsx` | 与真实召回同管线（规划→多查询共识融合→关系/遗忘层）的无副作用执行：展示检索计划、通道与数据集、按来源分组结果、关系网络、遗忘层、各阶段耗时；不记访问不触发探针 |

## 关系图谱（agent/memory/graph/）

- **权威存储** `graph/store.py`：graph_nodes/graph_edges，(s,p,o) 唯一 upsert + 别名归一 + 软删 +
  cognee 投影入队；检索命中即计数（access_count/last_accessed_ns）
- **关系投影 scope 隔离**（`coordinator.graph_dataset_for_node` + `fusion.datasets_for_scope`）：
  实体型节点（user:/group:）投影入 per-scope 数据集，自由型节点（topic/person/concept…）入全局
  数据集——实体私人关系网络按 scope 隔离检索，公共知识全局共享
- **遗忘与衰减**（consolidator 集成）：边强度向基线 0.5 松弛（访问护盾 ÷(1+ln(access_count))，
  与记忆 importance 松弛同公式）、强度 <0.25 且超 90 天软归档（自动触发 cognee 投影更新）、
  孤立自由型节点超期归档（user/group 会话锚点永不自动归档）；阈值保守（松弛 30 天起/归档 90 天起），
  配置 `memory/graph` 组
- **治理议程（AI 策展）**：事实归系统、决策归 AI——确定性事实生产（弱边/陈旧边/同主语同谓词歧义对/
  同类型同称呼疑似重复节点/超阈度数枢纽异常）→ 心跳维护段渲染摘要，AI 经 `graph_curation_agenda`
  读完整议程、用 graph_merge_nodes/graph_remove_relation/graph_update_relation 执行治理
  （graph_curation 任务 heartbeat 模式定期消费）；user/group 锚点与人工强关系受 prompt 保护
- **议程豁免机制**（`graph_curation_exemptions` 表，kind+signature 唯一）：已裁决「真实扇出/设计使然」
  的常驻议程项登记豁免后永久消失（撤销标记 active=0 可恢复）；议程项附 `exempt_signature` 字段，
  AI 处置时原样回传 `graph_curation_exempt`——签名构造零歧义；重复登记更新理由并复活
- **图谱溯源双向**：`_edge_json` 输出 `source_memory_id` 机读指针；关系从记忆得出时必传
  source_memory_id，对话直出留 0
- **整理节奏**：`memory_consolidate_every_n_ticks`（默认 48，心跳 300s 下约 4 小时一轮），
  记忆遗忘/松弛与图谱衰减/遗忘同频，配置中心热调

## cognee 集成与写盘防护

| 机制 | 位置 | 说明 |
|------|------|------|
| 投影内容指纹跳过 | `store/_shared.py::projection_content_hash` + `cognee_queue.enqueue_sync` | 防记忆写入风暴打爆磁盘写盘配额。`cognee_entry_map` 的 content_hash（type/content/source/metadata/tags 的 canonical sha256，**刻意不含 importance**——召回强化/松弛回归不再触发重投影）一致 → 不入队；在途 processing 批次持有更新负载时不跳过（防「改 B→回退 A」竞态）；`enqueue_backfill` 走 force=True 保持显式修复语义。graph_node 载荷消费时渲染邻域文档 + **结构指纹**（节点身份 + 各边谓词/方向/对端，不含强度与证据文本）——重复提及的强化/证据刷新不触发整篇重投影 |
| cognee 写盘熔断 | `cognee/write_breaker.py` + coordinator `_projection_allowed` | 进程自身磁盘写入速率超阈值（默认 500MB/5min）时暂停投影认领与自动压缩，冷却到期重评、自调节；平台不支持时 fail-open。状态经 CogneeSyncStatus.paused 暴露；`run_in_idle_window` 用户显式作业不受熔断阻断 |
| ladybug native 监督 | `agent/memory/cognee/ladybug_guard.py`（门闸/循环安全/看门狗/WAL 容错四位一体，`install()` 幂等） | ①**并发门闸**：进程级线程锁串行所有 native 执行（execute+结果消费全程），wait_for 取消协程不提前放锁；②**循环安全拆除**：close/delete_graph 先 `to_thread` 预拆除再调原方法，门闸等待永不落在事件循环线程；③**失控看门狗**：纯线程实现，持有门闸超 `native_watchdog_restart_seconds`（默认 600）经端口请求守护重启（绝不直接退进程）；**刻意不做 native 中止**——ladybug 的 set_query_timeout/interrupt 对卡死扫描不可靠，隔离+重启是唯一安全语义；④**WAL 容错**：`throw_on_wal_replay_failure=False` 打开图库，损坏回放到最后提交点。配置在 cognee.json |
| LanceDB 物理压缩 | `cognee/storage.py` + coordinator 空闲窗口调度 | cognee 删除/更新只在 Lance 追加 tombstone 新版本，历史版本永不回收（磁盘单调膨胀）。`compact_lance_tree` 逐表 optimize（清理早于 `compact_retention_days` 的版本，最新版本永远保留）；worker 队列排空后的空闲窗口按 `compact_interval_seconds`（默认 86400s）自动执行。手动触发三入口同路径 `coordinator.request_compact()`：AI 工具 `compact_cognee_storage` / `POST /memory/cognee/compact` / Web 记忆页按钮。`StorageStatsTracker`：大库遍历可达数十秒，请求路径永不遍历——内存 TTL → 磁盘快照 → 空统计三级返回，缓存写入携带单调代际号防旧遍历回写 |
| improve 默认禁用 | `cognee/config.py` `improve_interval_seconds`（默认 0） | cognee improve/memify 对全图三元组重新 embedding 且无去重，CHUNKS 类召回不依赖它；同步路径不自动触发，手动 `improve_cognee_dataset` 保留 |
| goal 不投影 cognee | `store/cognee_queue.enqueue_sync`（source=='goal' 拦截）+ `planning/tracker._persist` | 计划状态 JSON 不是知识：updated_at 高频漂移使投影永不稳定；goal upsert 不入队，原生 FTS/向量检索已覆盖召回；`_persist` 比较 updated_at 之外的语义内容，未变不落库 |
| 投影开关 | cognee.json `project_memories_enabled` / `project_graph_enabled`（默认均 true） | memory 投影与主向量库同源（重复嵌入），graph 投影是原生检索没有的增量，可按需关停 |
| embedding 用量账本 | `agent/memory/embedding/usage.py` + `GET /status/usage` 的 embedding 段 | 引擎级埋点（查询/批量/多模态）：日级 calls/texts/chars，内存累加 + 防抖落盘。cognee 自带引擎不在此口径 |
| 批量对齐与缓存 | `embedding/worker._batch_size` + `engine.max_batch_size` + `embed_query_cache_size` | worker 批次取 min(配置, 客户端 embedding_max_batch)，避免 llm_client 内部拆批；查询向量缓存容量/TTL 可配 |
| 向量索引清理 | `scripts/dedupe_cognee_vector_index.py` | 一次性治理脚本（幂等，应用运行中可执行）：EdgeType/Entity 等索引按 text 精确去重 + 存量 goal 投影退场 |
| 版本注记 | `pyproject.toml` + `[tool.uv] override-dependencies` | cognee 1.6.0 与 litellm 1.100.1 + onnxruntime 经 override 共存（override 仅针对上游矩阵保守钉，上游放开后可移除）；ladybug 0.19.0 native 监督补丁锚点存续 |

## 遗忘治理

| 机制 | 位置 | 说明 |
|------|------|------|
| 遗忘层兜底召回 | `store/search.py`（search_forgotten / search_archived / search_tombstones）+ recall 工具 `forgotten` 字段 + `restore_memory` 工具 | 归档记忆（向量余弦强信号 + 关键词弱信号）与墓碑 gist 统一检索，与主检索并行执行。采纳规则：归档向量强匹配（≥ `memory_archive_recall_min_score`，默认 0.5）随时浮现；弱命中与全部墓碑仅主检索无果时出现（「似曾相识」而非干扰）。归档项 `restorable: true`，AI 经 `restore_memory(id)` 恢复（向量/访问记录原样回填零重嵌）；墓碑 `restorable: false`，hint 引导基于梗概重新 memorize。附带条数上限 `memory_forgotten_recall_limit`（默认 3） |
| 遗忘墓碑表 | `memories_tombstone` + `purge_archived_memories` | 归档物理删除前把 gist（内容截断 200 字符 + 标签 + 来源/原因，不存向量）留入墓碑表——「曾经知道什么」的元记忆仍在。行数硬上限 `memory_tombstone_max_rows`（默认 5 万，0=不限），超限 FIFO 淘汰；purge 按归档时间最旧优先 |
| 检索练习效应 | `relax_importance` 访问护盾 | 见「存储与检索基座」——常被想起的记忆更抗遗忘 |

## 标签、铁律与受管区块

| 机制 | 位置 | 说明 |
|------|------|------|
| 记忆体系铁律 | `agent/memory/rules_doc.py`（文档持有）+ `config/memory_rules.md`（文件载体，ConfigPaths.MEMORY_RULES）+ `context_assembly._memory_rules_text` | 写入路由（五系统 + 技能分流，一条信息只进一个系统、出处用指针）/ 标签纪律（前缀语义、打标即入联想网络、写前 memory_index 查既有形态、tags 软加权 vs filter_tags 硬过滤）/ 主标签记忆用法 / 披露边界 / 查询路由 / 落盘诚实 / 检索纪律 / 口头纠正（用户说「记错了」时 recall 定位 → update_memory 原地修正 / forget 归档，禁止新旧矛盾并存）/ 图谱治理路由条。缺失时以 DEFAULT_RULES 种子落盘；AI 无写入路径，人类经 Web 记忆页「规则」标签整文档编辑或手编文件；读取走 mtime 缓存，生效文本参与 stable 指纹门控 |
| 主标签记忆（main:hub） | `agent/memory/hub.py` + `_blk_hub`（vol 36 独立块） | 带保留标签 `main:hub` 的 PERMANENT 记忆每回复周期置顶注入：AI 经 memorize（type:permanent + main:hub）整段 upsert 维护；心跳维护段 `ensure_hub` 自愈重建骨架；forget 拦截 hub 归档。注入预算 `memory_hub_inject_max_chars`（默认 3000） |
| 便签受管区块硬保护 | `agent/memory/notes.py`（`_MANAGED_BLOCK_RE` + `_assert_managed_blocks_intact`） | `<!-- AUTO:name BEGIN/END -->` 标记对圈定的系统受管区块，便签写工具写入前校验逐字节保留，改动/删除即拒绝；系统写入路径直走 `_atomic_write` 天然豁免。通用读写原语 `update_managed_block`/`read_managed_block`（AUTO:memory-status / AUTO:heartbeat-status） |
| 标签索引观测 | `agent/heartbeat/engine.py::_write_memory_status` | 状态区块仅保留 AI 可行动项（库容/cognee 同步与熔断/最近整理/便签超标）；标签膨胀提醒条件化（总数超 `memory_tag_bloat_threshold`（默认 400，0=关）才注入归并提醒行）；运维遥测不进 prompt，由 memory_stats 工具按需查询 |
| 标签智能 | `agent/memory/store/tag_intel.py` | df/共现图谱/提及词表 TTL 缓存；IDF 评分、共现与图谱邻居联想、查询提及识别的统一驱动层 |

## 证据体系与反思生命周期

| 机制 | 位置 | 说明 |
|------|------|------|
| 证据数学 | `agent/memory/evidence.py`（纯函数零依赖） | 记忆/反思的「可信度」维度（与「记不记得住」分维）：rein/disp 双通道各有独立时钟与半衰期（负向 180 天刻意长于正向 30 天）、读时衰减不改存储、importance 阶梯种子（≥0.9 直通 0.8）、用户确认连击加成、protected 恒 +inf。信号回流在 `dedup.apply_evidence_signals`：skip=用户复述确认（+1.0 金标准）/update/merge=谱系存活（+0.5） |
| 反思生命周期 | `agent/memory/reflection_lifecycle.py` + consolidator 集成 | 对象是 `type:reflection` 标签的记忆（self_reflection 任务产出播种，memorize/任务结果两写入点接入）。状态机 pending→confirmed（score≥1.0）→晋升（score≥2.0 且过冷却）/denied；负分进 sub_zero 每日倒计时到期归档。晋升 = LLM 合并裁决（promote/merge/reject，复用 dedup.light_llm）写入目标画像——**晋升是人格生长的唯一通道**；LLM 失败只退避重试绝不静默晋升，5 次死信。终态幂等，重入安全 |
| 证据反馈回路 | `reflection_lifecycle.load_verification_block` / `classify_reflection_feedback` / `record_user_feedback` + auto_capture 尾部 | pending/confirmed 反思经画像区「待验证认知」块呈现进对话（呈现即登记 surfaced_at，24h 冷却防追问骚扰）→ auto_capture 周期把该 scope 用户新消息对着「已呈现未裁决」条目跑 light_llm 分类（confirmed +1.0 / denied +1.0 反驳 / ignored -0.2）→ 证据回流驱动晋升/归档；Web 记忆页 ✓/👎 按钮走同一 record_user_feedback（人审直改）。**可见性规则**：带 user:/group: 标签的反思只在对应 scope（含群聊参与人）可见，无实体标签的自认知全局可见——A 的反思绝不注入 B 的对话 |
| 人格双层 | `agent/memory/self_profile.py` + `agent/mind/recollection.py` | 宪法层=personas/*.json（用户编辑，stable 层冻结）；生长层=`agent:self` 自画像（personality 表 + ENTITY 记忆镜像双写，覆盖式更新前备份）——随画像区每轮注入；已确认反思块紧随其后（score>0 才呈现）。关系动态走图谱 `agent:self` 节点。铁律已登记路由：自我认知只能写反思等晋升，禁止直接改自画像 |
| 积极性频率 | `agent/mind/proactivity.py` | `proactivity_level`（0-1 默认 0.5）：元决策 prompt 注入档位指导、心跳 idle 调度拍数按 `1.5-level` 缩放；AI 可经 update_entity_config 自调 |

## 索引完整性：合并统一 + mem:ID + 谱系审计

以「一条信息只进一个系统，他处需要时用指针 mem:ID 引用」为标尺：

| 机制 | 位置 | 说明 |
|------|------|------|
| 合并语义统一 | `memory_store.merge_into_keep`（唯一底层） | keep（有效分最高者）原地演进（标签并集/访问累加/max 重要性/可选合成内容，version+1），drop 软标记 importance=0 + `metadata.merged_into` 退出全部召回后入既有归档/物删生命周期。protected/ENTITY 画像/规划条目双向准入（不可作 keep 也不可作 drop） |
| mem:ID 分层解析 | `memory_store.resolve` + get/update/forget 工具 | 五态：active（含重定向落地）/ merged（链过深）/ archived（可恢复）/ tombstone（gist + redirect_to）/ missing——被合并的旧 id 沿 `merged_into` 链（限 3 跳防环）解析到存活条目，便签与图谱中的 mem:ID 永不失效；变更类操作遇非活跃条目显式引导（防改僵尸） |
| 谱系审计 | `memory_audit` 表（`actor` 列）+ `list_audit` | 全变更路径（add/update/delete/archive/merge/restore）带触发方归因（tool:*/auto_capture/consolidator/heartbeat_*/task:*/web/planning/reflection*）；出口：get_memory 附最近 5 条事件、`GET /ltm/{id}/audit`；30 天保留（长期谱系走 metadata merged_from/into） |
| 条目级语义链接 | memorize `linked_to` 参数 + recall 联想带出 | 「本条纠正/补充/依赖哪条」的显式声明（上限 5，存在性校验）；recall 命中关联方时 related 一跳带出（hop="link" 优先于标签共现）。与标签共现职责切分：具体关系用 linked_to，同主题关联靠标签 |
| 标签归并议程 | `tag_intel.merge_candidates` + consolidator/心跳 memory-status 区块 + `memory_index` 输出 | 确定性候选两类：写法变体（NFKC/小写/空白归一同形）与 topic 包含关系对（df≥2，归并高频方）——事实归系统、决策归 AI（经 update_memory 执行）；无候选零注入；僵尸条目（importance=0）不进统计 |

## 代谢与纪律

| 机制 | 位置 | 说明 |
|------|------|------|
| temporal_scope 时间语义 | `store/_shared.py::temporal_is_past/temporal_weight` + 检索评分 + 行尾注 | event 提取输出 temporal_scope（episode/state/pattern 白名单）+ `activity_date`。读时判定超期（state 7 天 / episode 3 天，pattern 永不）→ 注入行追加「过去时·可能已变」尾注 + 检索整体降权（`memory_temporal_expired_weight` 0.5，聊往事仍可低权命中，淘汰仍归遗忘曲线）；memorize 工具补 temporal_scope 参数 |
| 用户话题指令 | `agent/memory/user_directives.py` + Mind.accept_feel 钩子 + discipline 块（VOL_LOW） | ban-topic：纯正则抽取「别再提X」（零 LLM），挂说出来时的会话 scope（私聊管私聊、群聊管群，不跨会话扩大）；TTL 随命中线性增长（3 天×次数，上限 30 天）沉默不续期自然淡忘；任务精简模式同样注入（主动消息更不能踩线） |
| 防复读 | `agent/memory/anti_repeat.py` + freshness 块（VOL_SESSION+2） | 生成侧软提示：近期 AI 回复 df≥3 的高频 ngram top5 引导全部回复周期换角度；出站侧的重复内容由「出站事实」provider 动态注入覆盖（见[频道分册](channels-and-scopes.md)） |
| importance 校准 | `auto_capture._EXTRACT_PROMPT` 校准表 + memorize 工具描述 + `self_profile.PROFILE_MEMORY_IMPORTANCE` | 提取侧锚点表（0.9+ 身份级/明确「记住这个」，0.8 长期偏好与承诺，0.7 阶段动态，0.6 线索，0.5 弱线索不预过滤）；画像 ENTITY 镜像 importance 统一 0.9（身份级事实，四处写入点共用单点常量） |
| 事件时间本体 | `agent/memory/auto_capture.py` | event 提取输出 temporal_scope（白名单校验，拿不准 pattern）落 metadata；date 落 `activity_date` |
