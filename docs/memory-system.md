# 记忆系统

记忆存储、召回枢纽、图谱与 cognee 集成、遗忘治理、证据与反思生命周期的现状说明。修改
`agent/memory/` 相关模块时按需阅读；缓存红线见[上下文缓存分册](context-cache.md)。

## 存储与检索基座

文件召回的全文、LIKE 和向量通道共用查询相关的原文窗口，沿用中文查询分词；
窗口不超过 500 字符，省略以 `…` 标明，行号对应实际展示内容。原分块范围保留在
`provenance.chunk_start_line/chunk_end_line`，文件路径与 chunk ID 不变，便于按需读取全文。
选择窗口只处理最终候选，不改变排序、标签过滤或向量空间，也不新增模型请求。

文件同步的文档解析与分块在工作线程执行，格式支持统一来自文档提取器。
文件元数据、分块和全文索引在同一事务内替换，失败或取消时回滚。同步返回
`synced/unchanged/failed/removed/chunks`，单文件失败不会计入未变化或中断其他文件；
Web 索引面板显示本次同步统计。失败的文件在下一次同步中重试。

分块缓存按内容哈希去重批查，每批最多 500 个哈希；未命中仍由后台 worker 补全。
后台向量回填只更新内容未变且仍缺向量的记录，避免请求期间的改写、删除或其他回填被旧结果覆盖。
重排失败保留原候选与原评分，成功时生成独立结果，不修改其他检索分支共用的候选对象。

- **`agent/memory/memory_store.py`**：长期记忆存储（SQLite + FTS5 + Embedding 混合检索；
  软归档遗忘 + importance 松弛回归）。重要性松弛带访问护盾：有效速率 ÷ (1 + ln(access_count))，
  历史访问越多的记忆向基线回归越慢（常被想起的记忆更抗遗忘）。`update()` 契约：`entry.embedding`
  是读取快照不代表写入意图，只有 `new_embedding` 显式替换向量；正文变化时 Store 事务内统一失效
  旧向量（权威 BLOB 与 vec 投影同清）并唤醒后台回填，`expected_version` 让基于旧快照的语义决策
  安全失效；ANN 读取拒绝权威向量已清空但派生索引残留的行。显式 `rebuild_embeddings` 同时失效
  归档向量（恢复为活跃后进入常规回填）
- **写入判重**（`agent/memory/dedup.py::judge_write`）：判断段（关系四选一 novel/covered/
  evolution/fragments + skip/update 目标选择 + merge 逐候选是非）经判断引擎一次调用并行评判，
  仅 update/merge 再经 light_llm 合成合并文本（store/skip 占写入绝大多数，热路径零 LLM 调用）；
  关系/目标置信度低于 `memory_dedup_min_confidence`（默认 0.25）或任何判断/合成失败一律保守退回
  直接写入——**去重永不阻塞写入路径**。判断模型只看 1..N 候选序号（真实 id 转述易抄错），
  裁决返回前映射回真实 id。候选召回**全库不按身份预过滤**：所有频道的记忆同属 AI 个体，
  跨群/跨私聊的相似候选连同其 user:/group: 标签一起进入判断段——不依赖说话人的通用事实
  再次出现判 covered（内容已覆盖），同一句话涉及不同对象判 novel（不同人各自的事实）。
  skip 必须选中有效候选，只为该条目追加出处与更严格的隐私级别；内容重复不代表用户确认，
  update/merge 也不自动增加证据分或用户确认次数。
- **recall 工具**：source 标志 + depth 浅深 + filter_tags 硬过滤 + forgotten 字段（见遗忘层兜底召回）

## 召回归属与出处

人物/群标签描述记忆涉及的对象，不单凭标签推断谁说了这句话。`metadata.origins` 保存实际
材料出处：`conversation_summary` 为对话提炼，记录材料 scope、数据库消息 ID 和时间窗口；
`agent_note` 为 AI 主动记录，保留工具执行时的 scope，包括内部隔离会话 `reflect:<8位十六进制ID>`；
内部会话只表示 AI 记录的执行来源，不能作为 `conversation_summary` 的对话材料来源，
也不从触发会话或实体标签推断发言者。材料窗口用于追溯，不是逐句引用证明。
事件日期 `activity_date`、记录时间和材料时间分开呈现；旧数据缺少出处时明确标注未知，
不从当前会话、查询标签或数据集名称回填猜测值。

原生、快速、精确标签、关联、近期回退与后台补充共用结果映射和格式化。
每条自动召回先显示 mem ID、涉及对象、出处和隐私边界，再显示有界正文，截断用省略号标明。
`recall` 的主结果和关联结果携带相同的出处字段；`get_memory` 可读取完整材料指针。
Web 召回测试的「归属与出处」默认收起，展开后显示材料会话、时间、消息 ID 和完整标签。
文件保持实际路径与片段行号；归档保留已有出处，已删除墓碑只表示梗概，不冒充完整历史。

Cognee 文档外部元数据携带出处，分块保留元数据时可恢复身份、时间和私密度；
返回内容缺少这些信息时标注未知。图谱综合明确标为非原话，不按单个引用 ID 当作原始事实。
投影格式未变时不自动重建已有向量；格式变更须经下述构建状态机验收后启用。

融合与召回账本优先用可确认的原始记忆 ID；内容去重同时核对人物/群、材料会话、事件日期、
时效与隐私，文件还核对路径和行号。不同归属或相同前文但结论不同的记忆不得互相覆盖。
规则判重（零成本拦截）仅在完整身份集合一致时生效，跨身份一律交给判断段结合标签裁决；
更新与合并保留出处谱系，隐私取最严格级别，多个日期不一致时不把其中一个日期冒充全部
内容的发生时间。
这些信息仅进入既有召回层和工具结果，不增加 LLM 请求或改变主对话稳定前缀的位置。
首轮召回只登记候选；最终上下文完成预算裁剪后，完整呈现的事实才进入消费账本，
被裁掉或仅部分呈现的事实不会阻止后续补充召回。

## 自动捕获与失败恢复

`auto_capture.py` 按会话加锁，按消息 ID 从旧到新消费；`capture_state.py` 在现有
`capture_cursors.pending_json` 保存冻结材料、提取结果、逐项进度和失败次数。每批最多 12 条，
12000 字符预算只在消息间切分；单条超长消息完整发送，不裁掉结尾的否定与条件。
旧背景最多 3 条且单条有界，不作为重复提取对象。单条有正文的约定也可以触发捕获。

有效 `[]` 表示没有新事实；非 JSON、越界数量或无效字段属于失败。提取结果先落检查点，
写入出处的 `capture_key` 与记忆一同提交，恢复时核对活跃和归档条目的出处，跳过已提交项。
该键不进入模型召回文本。捕获、工具记忆和任务总结的重复内容不自动充当用户确认。

每批最多尝试 5 次，重启和新消息到达不重置失败次数；失败审计 `capture_failed` 保留材料
消息 ID、进度和原因，达到上限明确放弃该批再处理后续消息，不报捕获成功。压缩前 flush 仅在
成功消费批次时返回成功。原始对话仍可经消息工具查证；审计沿用记忆库的保留策略。
关系提取在事实批次提交后独立执行，失败记录日志，不重跑事实写入。

进程关停不再发起额外模型提取。已落盘对话、游标、冻结批次及提取结果由后续心跳继续处理；
取消只保存当前检查点，不增加失败次数或推进游标，反复重启不会耗尽批次失败额度。

反思反馈独立于新事实数量：空提取仍分析该批用户反馈，平台事件、通知不充当用户确认。
完整用户发言和候选保留结尾限定条件；候选读取失败、分类格式错误、缺项或写入冲突均进入
同一有界失败流程，不默认为 ignored。只有有效的明确反馈分类才更新对应证据。

## 自动召回策略

初始召回在首轮上下文准备阶段执行。输入先筛选用户和助手对话正文，再取近期窗口；
系统注入、工具回执、调用参数、思考块与媒体载荷不进入检索查询或召回预判，正文中的身份标签保留。
深探复用已有查询计划，预判只读取对话正文和会话归属，经
[Jev 可选工作预判](llm-and-models.md#可选工作的原生预判) 判断是否需要；无配置或判断不可用时
保留原检索流程。执行中只有并入本会话的新用户对话才能触发有界的本地补充召回；
工具结果、内部独白和跨频道通知不会启动补查。补充查询只取最新问题与少量前文，
本地记忆和技能并行匹配；不重新规划或调用 Cognee。Jev 同批逐条筛选短候选，
明确有用且概率达到 `judgment_recall_confidence`（默认 0.8）才采用；不确定候选跳过。
未配置或判断不可用时沿用本地匹配阈值，不额外调用普通模型。
实时跨频道消息仍逐轮进入动态上下文，手动 `recall` 与技能工具始终可用。

首轮上下文读取由 `mind/recall/inputs.py` 统一管理生命周期：查询向量、检索规划、
关系、画像补充和会话摘要同时准备；记忆规划完成即释放技能匹配，
技能不等待记忆检索或其他频道检索收尾。画像与检索结果整理独立执行，全部就绪后按固定位置装配。
语义召回、跨频道与技能复用同一个在途查询向量，失败后各自降级，不重复发起同一向量请求。
技能的互补查询向量并行准备。结果始终按既定分层与来源顺序装配，不按完成先后重排；
调用方中断或关键读取失败时取消并等待所属任务，借用向量的子任务不取消共享请求。
交互式向量请求在获得端点执行权后再次检查有界查询缓存，合并并发同文本请求产生的重复网络调用；
保留原有优先级、并发和超时规则，配置失效期间完成的旧请求不回填文本查询缓存。

低延迟召回的画像/精确标签读取与本地混合检索并行，标签过滤与固定记忆规则保持一致。
每批结果的身份名称统一批量查询，同步召回与后台补充共用格式化实现；查询失败仍保留
频道、身份 ID、时间和隐私标记。不跨轮缓存身份映射，避免名称或归属变更后继续使用旧值。

全局默认来自 `memory/recall`：`memory_recall_level`（默认 deep）与
`memory_recall_background`（允许首轮并行深探及新对话后台补充）。首轮检索在准备结束前收束；
补充召回由 `mind/recall/refresh.py` 随当前回复托管，最多 `memory_dialogue_refresh_limit` 批，
每批共用 `memory_dialogue_refresh_timeout` 总预算。0 批次可关闭轮中补充。
Web 的记忆页和钩子页共用配置入口；AI 的全局修改沿用
`update_entity_config`，单轮修改用 `set_recall_policy`。

| 档位 | 首轮准备 | 深探补充 |
|---|---|---|
| off | 只保留固定纪律、主标签索引与永久记忆 | 无 |
| tags | 指定标签精确交集；未指定时取参与实体画像 | 无 |
| fast | 本地混合检索，向量预取最多 2 秒后退回文本检索 | 新增对话可在后台匹配本地记忆与技能；指定标签时关闭 |
| deep | 完整规划、跨频道、联邦检索与技能匹配 | 首轮预算内完成记忆与图谱补充；新增对话仅触发本地补查 |

技能匹配独立于记忆档位，低延迟档直接按对话匹配候选，不等待记忆计划；技能目录与手动 recall 保持可用。
精确标签只允许 tags/fast，带标签时不做越界后台深探。记忆结果沿用统一归属标签、
mem ID 和召回账本；首轮深探结果在 provider 尾部注入，不修改 stable/summary/历史前缀。

首轮深探读取 CHUNKS 证据与 GRAPH_COMPLETION 上下文，不执行多轮生成式扩展；
只解析出实体且未要求深探时直接查本地图谱。深探沿用 `memory_recall_timeout_seconds` 的剩余
准备预算，并受 `memory_probe_timeout_seconds` 上限约束，不额外增加一段完整等待时间。
到期回收未完成读取并保留已返回事实；首轮迟到结果不进入执行阶段。
AI 显式 `recall(depth="deep")` 仍可按 Cognee 深检索配置使用多轮扩展。

SDK 可导入 `RecallPolicy`、`recall_options`、`set_turn_recall_policy`。
直接调用回复入口时使用 `with recall_options(RecallPolicy(level="fast"))`；对于入队消息，
应在 `Everything.recall_policy` 上设置策略，确保消息出队后仍带有覆盖。此字段不序列化
为用户正文，也不从用户文本标签解析。覆盖仅限本轮，不改变其他频道/身份/子会话。

回复开始建立独立账本，准备阶段收束检索，回复结束清空快照。账本按消费结果的会话归属，
不按查询标签中的实体记账，也不向其他会话广播。补充快照共享字符预算，区分已准备与已装配账本；
只有实际进入模型输入的事实才确认可见，被 provider 预算省略的内容仍是待装配。
已推荐技能与成功 `get_skill` 读取共用去重标识；推荐不等于执行。
新对话替换待查线索，过时结果不发布；策略改变、回复结束取消并回收在途工作。
后台完成不唤醒主模型，只供下一次正常调用读取。渲染与缓存契约见[召回与轮内快照](context-cache.md#异步召回与轮内快照)。

## 召回枢纽：检索规划与首轮深探

| 机制 | 位置 | 说明 |
|------|------|------|
| LLM 检索规划 | `memory_retriever.plan_retrieval`（公开 API，受 `memory_query_rewrite_enabled` 门控） | 轻量 LLM（light_llm 通道，可经 `memory_light_model` 指定）把对话尾部转成结构化计划 `{queries(1-3 互补), entities, deep_needed, rationale}`，失败/超时回退原查询单发（预算 `memory_plan_budget_seconds`，0 = 按召回总超时 40% 份额派生）。多计划查询并行经 federated_search 后 `merge_consensus` 融合：同键（anelf_memory_id / 内容哈希）取最高分，≥2 查询命中 ×1.1 共识加成。计划实体经 `GraphStore.resolve_nodes_for_tags` 解析为图谱节点 → 原生一跳邻域 + cognee `node_name` 定向检索 |
| 召回预算与规划并行 | `memory_retriever` recall 路径 | 检索规划与多路检索**并行**：原查询 lane 先行（复用预计算 query_vec 零 embed），规划慢/超预算只损失多查询增强；总时限经 `asyncio.wait` **非破坏式收尾**——到点只收割已落地 lane，已落地的基础/焦点/计划 lane 照常融合返回；联邦 lane 通过增量快照保留先完成的原生/远端结果，慢通道取消不丢弃已完成事实，全空才回退近期记忆 |
| 首轮深探 + provider 快照 | `agent/memory/probe.py`（DeepProbeHub + RecallLedger + provider `memory_deep_probe`） | 回复路径（`fire_probe=True`）复用已有规划，Jev 仅依据对话正文判断后在剩余召回预算内并发读取本地关系与 Cognee CHUNKS / GRAPH_COMPLETION。`collect` 返回前收束全部读取，超时保留已完成事实。轮中补充由 mind 协调；provider（priority 34、`memory_probe_inject` 门控）只读内存快照，回复结束清理，不改稳定前缀。 |
| 召回账本（三键防重复） | `probe.RecallLedger`（记忆键由 `MemorySearchResult.content_key` 定义） | per-reply 三键集合（结果 id / 图谱边 id / 完整片段与归属组合），三条召回通道共用：基底层注入、AI recall 工具返回（tools.py 记账）、首轮深探渲染前查账——一次回复内同一事实只出现一次；`begin_reply` 重置 |
| cognee 检索面 | `cognee/fusion.py` + `cognee/config.py` | 查询标签与完整 node_key 参与 NodeSet 定向通道；`recall_projection` 只从结构化元数据恢复归属，不把用户正文解析成管理字段。深类型（GRAPH_COMPLETION 等）只经首轮探针与 recall 工具（显式）发生，被动召回保持轻量 |
| 召回测试面板 | `POST /api/memory/recall-test` + 前端 `pages/memory/RecallTester.tsx` | 与真实召回同管线（规划→多查询共识融合→关系/遗忘层）的无副作用执行：展示检索计划、通道与数据集、按来源分组结果、关系网络、遗忘层、各阶段耗时；不记访问不触发探针 |

## 关系图谱（agent/memory/graph/）

- **权威存储** `graph/store.py`：graph_nodes/graph_edges，(s,p,o) 唯一 upsert + 别名归一 + 软删 +
  cognee 投影入队；检索命中即计数（access_count/last_accessed_ns）
- **关系投影与标签**：每个构建代际只有记忆、关系两个内容分区。实体节点使用完整 node_key，
  关系投影带端点标签、来源记忆标签与出处；不按人物或群复制数据集。scope 和 NodeSet 用于
  归属与检索提示，不是访问控制，披露仍须遵循项目隐私规则。
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

Cognee 默认安装并启用：依赖由 `uv sync` 按锁文件安装，默认使用本地 SQLite / Ladybug / LanceDB。
聊天与 Embedding 模型默认从模型管理映射，也可在 Web 的 Cognee 面板单独配置。
配置未齐时客户端返回 `configuration_required=true`，不导入 Cognee、不回落到库的隐式模型；
协调器在就绪前不认领同步队列，记录保持 pending，不消耗重试次数。首次配置完成后后台循环自动恢复，
Cognee 面板保存先校验模型参数和向量空间，再原子写入配置，HTTP 不等待图谱抽取。
协调器在投影锁内应用最新配置，连续保存合并到同一个受生命周期管理的任务，
当前批次不中途切换模型。状态接口的 `configuration_pending` / `configuration_error`
分别表示等待应用和应用失败；失败原因同时记日志，页面显示具体校验错误并保留编辑草稿。
既有 `enabled=false` 保留，图谱抽取与向量化按所选模型计费。

抽取模型的自动映射、指定模型和自定义来源均支持独立设置思考等级与输出上限。
指定/自动模型使用模型管理中的 `thinking` 契约，不支持的显式等级在保存前返回错误；
关闭思考时不残留原有 thinking token 预算。未声明思考契约和自定义来源使用 LiteLLM 标准参数，
具体支持范围由服务商决定。原生结构化输出与 Instructor 的边界参照
[Cognee 官方配置说明](https://docs.cognee.ai/setup-configuration/structured-output-backends)。
客户端初始化串行执行；导入期间发生配置变更时重新解析最新配置，避免后台同步与召回重复初始化或应用旧配置。

### 召回与模型预算

- 同步召回由原生 FTS/向量检索与 Cognee `CHUNKS` 互补；旧的默认
  `CHUNKS + CHUNKS_LEXICAL` 配置读取时迁移为 `CHUNKS`。Cognee 词法检索会全量读取
  数据集并计算 BM25，保留为显式深检索选项，不在默认同步路径重复原生 FTS。
- `CHUNKS`、`CHUNKS_LEXICAL`、`SUMMARIES` 返回逐条证据；生成式检索使用 `only_context`
  跳过最终回答。同一通道的重复结果只计一次，跨通道逐榜计算 RRF。单条证据沿用 700 字符预算，
  不把全部 chunk 拼接后截断；外部元数据回填原记忆 ID，已有 chunk 可直接复用。
- 数据集目录短缓存并随摄入/删除失效；不存在的数据集不发起检索。远端读取最多 4 路并发，
  同次召回内同一 embedding 引擎与文本复用在途向量，取消单路不取消共享请求；结束时统一回收。
- 结构化输出默认 `litellm_native`，显式选择 `instructor_mode` 时才启用 Instructor。
  自动映射、指定模型、自定义来源共用输出预算；默认保持 16384 token，显式上限同步传到实际请求。
  输出上限不是目标输出长度，过低会截断 JSON。切片、模型能力与思考预算应共同评估，不能仅降低上限。
  映射来源遵循模型思考契约，自定义来源使用 LiteLLM 的规范思考参数。
- 指定/自动模型保留所选模型的对话协议与端点。OpenAI/Azure Responses 通过 LiteLLM
  `provider/responses/model` 桥接到 `/responses`，结构化校验仍由 Cognee 负责；完整端点 URL
  不重复拼接。协议为 `auto` 时保持 Cognee 默认 Chat 入口；Responses 专用服务须显式选择
  `responses` 或填写完整端点。`tools` 在边界转换为 Instructor 的 `tool_call`，无需改写已保存的界面选项。
  Responses 传输沿用全局网络规则；适配边界补齐依赖异步桥接遗漏的显式客户端参数，
  请求结束或取消后关闭客户端，不修改第三方安装文件。
- 抽取请求继承所选模型的 `timeout`；自定义来源使用主模型框架的默认请求时限，
  `chat.extra_args.timeout` 可显式覆盖，必须是有限正数。模型请求、结构化校验与重试共用
  `pipeline_timeout_seconds` 流水线总时限，不另加与模型配置冲突的固定生成截止时间。
  适配边界复用 Tenacity，暂时性失败最多 3 次，最多 4 路生成并发。鉴权、参数、校验等
  永久失败不进行外层重试；投影队列将其标记 failed，修正配置后可显式重试。
  同步失败记录数据集、执行阶段、耗时与底层错误；内部步骤超时与流水线总超时分别报告。

### 已有数据与模型变更

原生 SQLite 是内容权威，Cognee 是可再生的图谱与向量索引。模块职责如下：

| 模块 | 契约 |
|---|---|
| `projection.py` | 投影格式版本、精确标签归一、完整内容指纹、活跃记录资格；无 SDK 依赖 |
| `cognee/documents.py` / `projection.py` | 纯文档转换；`graph/store.py` 只读取一致的关系及出处快照 |
| `tag_query.py` | 跨后端 AND/OR/NOT 与显式层级契约，不自动创造别名 |
| `store/cognee_queue.py` | 与业务写入同事务的 outbox、原子认领、失败重试和映射 |
| `store/projection_state.py` | SQLite 快照、分页游标、构建状态、逐项覆盖审计与提交门 |
| `cognee/rebuild.py` / `cognee/preflight.py` | 构建代际与后端验收 / 旧文档归属核验和历史留存，不另开 worker |
| `cognee/coordinator.py` | 单 worker 调度、配置切换、维护互斥与生命周期 |
| `cognee/writer.py` | 已认领批次的摄入、稳定 ID、替换删除与重试；不创建后台任务 |
| `cognee/ports.py` / `records.py` / `sdk_contract.py` | 最小后端 Protocol、类型化返回记录与 SDK 签名、并发保护、引擎释放契约 |
| `cognee/client.py` / `manifest.py` / `checkpoints.py` | 宿主事务、独立变更清单、进程互斥、关闭后验收、索引快照提交及恢复 |
| `cognee/process.py` / `worker.py` / `wire.py` | 子进程生命周期、限定方法的 JSON RPC、输入对象与完整 ORM 列序列化 |
| `cognee/model_config.py` / `sdk_client.py` / `adapters.py` | 纯配置解析 / 子进程内 SDK 输入输出、模型协议和原生引擎资源边界 |
| `cognee/fusion.py` / `evidence.py` / `query_scope.py` | 多路检索、证据归一、预算内融合及跨 RPC 查询向量复用 |
| `cognee/diagnostics.py` | 有界工作进程日志、凭据脱敏；不记录 RPC 参数 |

每代索引使用 `<namespace>_global` 和 `<namespace>_relations`，完整精确标签保留在权威库与
`anelf_attribution.tags`，去空白、去重、排序，不合并大小写、别名或不同实体。DataItem 的
`node_set` 由 Cognee 按其节点身份去重：大小写、ASCII 空格与下划线、单引号可能映射到同一
NodeSet，保留输入中的首个拼写。`node_sets.py` 集中适配该规则，SDK 初始化核对持久身份，
提交及全量验收只允许这一明确的索引转换，原始标签和内容指纹仍逐字校验。新增标签前缀不需
新增数据集或改表；旧内容缺失的标签、发言者和出处不凭空补齐。`filter_tags` 是严格交集，
`filter_any` 要求至少一个，`filter_not` 排除任一个；显式 `project:a/*` 匹配自身及 `/` 后代，
不会命中 `project:ab`。原生 FTS/向量先过滤再限量；Cognee 硬过滤只返回有可靠结构化标签的
分块并复核标签，不混入无出处的图谱综合、文件、关系联想或遗忘层。别名需经既有标签治理
确认，不根据称呼自动扩张过滤。NodeSet 只是索引，不是 ACL。

精确 AND/OR 条件优先下推到 NodeSet；结果不足时撤下可能合并拼写的 NodeSet 预过滤，并始终
按完整来源标签复核 AND/OR/NOT。复杂层级、排除条件复核后不足时，同样最多补充两轮候选，
上限为初始数量的四倍且通常不超过 2048，仍受整体检索超时约束。候选预算用尽时 coverage
标为 partial，不把未覆盖解释为不存在。一次召回的跨通道、跨 RPC 查询向量只缓存到本次结束，
由宿主显式释放；不跨用户请求保留查询内容。

文档 ID 按数据集、条目身份、正文和元数据稳定派生。网络写入前持久化目标 ID 到
`cognee_projection_attempts`，抽取、图谱覆盖核验、关闭后重新打开验收及索引快照提交成功后，
才提交原生映射；被替代文档的删除也在候选事务内，失败回退。失败重试和期间改写可以
回收未提交副本。记忆更新会同时标脏有来源关联的关系节点，标签、出处和证据变化不会漏同步。
关闭同步消费仍记录变更；关闭整个 Cognee 则不捕获，重新启用后须显式回填停用期间的内容。

知识抽取输入与管理信息分离：记忆正文逐字保留，ID、类型、时间、来源及完整标签放在
`external_metadata` / `node_set`，不再把管理文档头送去抽取为实体。关系正文只含节点、关系和
证据文本，边 ID、来源记忆、出处及边元数据保存在结构化归属中，并参与指纹。召回移除 SDK 的
NodeSet 结构连接 `belongs_to_set`，保留实际关系；缺少或无效归属的内容保守标为私密。

### 全量构建与恢复

#### 批次保护与原生崩溃

原生 SQLite 是正文、标签、关系、历史归档及 outbox 的权威来源，重建后也必须保留。
`client.py` 管理事务与宿主生命周期，`process.py` 管理有界 JSON RPC，`worker.py` 在独立
进程中运行 `sdk_client.py`；Ladybug/Lance 的地址空间不再与主服务共享。原生崩溃使当前调用
失败并熔断 Cognee 后端，主服务及原生检索继续工作；检查原因后用「重试失败同步」解除熔断。
宿主门面不继承 SDK 实现，模块导入不加载原生数据库。输入文档 ID 由唯一的纯函数派生，
版本化 RPC 只传数据；SDK 特有的模型和 ORM 对象在子进程适配边界转换。

`checkpoints.py` 将 Cognee `system`（图、向量、关系元数据）、`data` 和向量身份文件作为
同一提交单元。SDK 使用原有路径的可丢弃工作区，`.publication` 内封存的恢复点永不交给 SDK
打开，避免只读操作隐含的建表/统计写入污染恢复点，也不需要改写已存储的文档 URI。
每次打开 SDK 前持久化 dirty 标记。变更计划以最近发布清单为基准，首次接入才读取旧索引建立
基线；以原生已确认映射交叉核验，已持久化的在途尝试由幂等重试处理。宿主显式登记新增、替换和
删除；提交时核对所有数据集与文档清单、标签、正文 SHA-256、完成标记，并检查当前代际已完成
文档的真实分块覆盖。新增正文须与输入逐字节一致，不仅相信 SDK 元数据。再正常关闭、重新打开
并核对同一清单，最后复制完整派生状态、原子更新提交记录，才确认对应 outbox 条目。
整库退役、压缩、增强同样经过上述提交门；不因没有新增文档而跳过。仅打开当前代际及本次写入的
图库，不为验收强行打开待清理的旧坏图。清单不替代图数据库的全量结构校验或业务召回验收。
发布后、确认队列前中断会按稳定文档 ID 重试；不会把原生内容或后来新增的队列回退。

大关系节点使用增量文档分片：超过 6000 字符的邻域按边 ID 的固定 32 条区间分组，再按完整行
分成至多 6000 字符的文档；单条超长证据连续切片，不截断丢弃。区间由 ID 决定，不因插入或
删除其他区间的边而重新编号。每片保留节点标题、该区间的边、来源和标签，避免把整个节点的
所有标签广播到每篇文档。原生总指纹继续核验完整邻域，`cognee_entry_map.documents_json`
保存各片的稳定标识和内容指纹；发布清单、重建验收、历史归属核对均展开到逐文档。

升级只添加原生映射字段，不修改 namespace、投影版本或旧索引，不自动回填。未变化的既有
单文档保留原样；大节点首次真实内容变化时转换，之后只摄入变化的分片。小节点沿用单文档，
已拆分节点保持分片形式以避免大小波动反复转换。新文档核验成功后在同一候选事务内清理被
替换的旧文档，全部索引发布成功才确认总指纹、分片清单和队列；失败或取消走已有恢复机制。
显式重建/修复仍可要求重放，不能把“内容相同”当作损坏索引无需修复的依据。

后端沿用 Cognee 的 `add(..., incremental_loading=True)` 和
`cognify(..., incremental_loading=True)`，完成标记使未变化文档跳过再次抽取。分片是宿主的
持久增量单位，不是修改 Cognee 内部分块、直接写图库或缩减原生关系。
上游契约参见 [cognify](https://github.com/topoteretes/cognee/blob/v1.6.5/cognee/api/v1/cognify/cognify.py)
及 [逐文档增量执行](https://github.com/topoteretes/cognee/blob/v1.6.5/cognee/modules/pipelines/operations/run_tasks_data_item.py)。

候选失败先回收工作进程，再从封存快照恢复全部派生组件。恢复中断后按同一记录重做，恢复完成
前不启动 SDK。宿主退出不必等模型抽取或 native 查询完成；工作区可丢弃，下次启动先恢复再续跑。
POSIX 工作进程使用独立会话，回收覆盖专用进程组；关闭前记录辅助子进程，Windows 也需确认其退出。
进程退出、辅助进程退出和管道回收分别限时检查，避免辅助进程持有日志管道导致 `Process.wait()`
永久等待。取消期间保留句柄直至回收完成；无法确认退出时保留所有权、阻止提交并明确报错。
读取与批次写入互斥，维护窗口内 Cognee 通道可能超时，现有原生召回不受影响。首次接入保留
现有索引作为基线，不重抽取已有数据；这不能修好升级前已经损坏的文件，旧坏库仍需隔离和新建代际。

数据集观测与索引读取分离：启动及配置生效时读取目录，成功发布批次后从已验收清单更新内存视图。
Web `GET /memory/cognee/datasets` 返回 `items/ready/busy/updated_at/error`，AI 目录工具复用同一视图，
不等读写锁、不启动工作进程。`ready=false` 表示尚未观测，不能当作空库；同步中保留最近确认的目录，
新建、删除只有成功提交后才反映到视图。故障信息与旧目录同时可见，实际清理及验收仍读取真实索引。

恢复点封存文件清单、大小、内容清单及依赖版本，并在提交记录保存清单校验和。恢复前发现缺失、
大小异常或清单损坏便停止，不覆盖工作区。文件大小校验不是全库位腐败检测；版本记录用于审计，
普通 SDK 升级不自动重建。旧恢复点在下一次正常提交时补齐清单，不更改投影格式、文档 ID 或向量空间。
升级 SDK 时运行 `sdk_contract.py` 契约、原生故障恢复测试与真实模型集成验收；投影格式升级另行评估。
历史归档识别明确支持的格式版本，不以“当前版本”代替可读取历史版本清单。

`.publication/worker.log` 记录 PID、恢复点、RPC 方法耗时及原生退出码，保存脱敏后的 SDK 错误输出；
最多两个 1 MiB 文件，超长单行省略。排查时结合当前提交与原生崩溃报告，不仅根据 exit_code 猜原因。

APFS 优先 `clonefile`、Linux 优先 reflink，共享未变块；不能使用硬链接充当快照。
不支持块共享时使用有界复制，单次完整复制预算 2 GiB，并保留磁盘余量；超限拒绝本次写入，
保留原恢复点，不无界吃满磁盘。快照不保证只有几 MB：checkpoint 重写的大文件可能占用大量新块。
常态保留一个当前恢复点，构建新快照期间临时并存两个；仅内部过期保护快照自动回收。
旧 Cognee 数据集与用户备份仍经人工选择清理。Web 单列保护快照逻辑大小，不把块共享后的
逻辑字节合计称为独占磁盘空间；备份/迁移须经关闭工作进程后的静默窗口。

Web「Cognee → 全量构建 / 续跑」和 `POST /api/memory/cognee/rebuild` 共用持久化请求。
接口只确认请求已保存，最终以状态 `ready` 为准。首次遇到旧格式映射进入 `required`，暂时使用
原生召回；不会直接把旧数据声明成新格式，也不保留另一套旧运行实现。
图库损坏或需完全重新抽取时，选择「重新开始全量构建」（同接口 `?restart=true`）：
持久化全新代际请求，即使当前处于 building/failed 也不复用原图或完成标记，保留旧代际。
普通续跑只用于继续当前构建，不能替代受损图库的全量重建。
同步已暂停时也可保存构建请求；点击「继续同步」后才执行，不暗中启用同步。

1. 预检当前旧数据集，识别上游拆分共享行时保留的 `legacy_id`；旧映射须能回查原文、归档或
   明确删除记录（墓碑用 `memory_id`，不能用墓碑行号）。映射之外的旧文档须同时通过应用标签、
   元数据 ID、原生来源及旧正文格式核验；关系文档还核对节点 key/type。完整读取 Cognee data
   目录内的原文，将正文、元数据、标签和原始位置追加保存到原生库 `cognee_legacy_documents`，
   读回校验后才继续。该表按数据集、文档 ID、快照 SHA-256 幂等留存，重建不会清空。
   重复正文和更新前历史都保留，历史不会覆盖当前事实或重新激活已归档/删除的记忆。无法确认
   归属、正文缺失、越界或留存失败仍阻止重建，错误包含待核验文档 ID；不静默跳过。
2. 对原生 SQLite 做在线完整备份并执行 `quick_check`，再原子切换队列与映射。原始内容表不清空。
   该快照也包含上述历史留存表；Web 显示本轮留存数量，可在数据库浏览器的记忆库查看完整
   `document_json`。历史内容只供审计和人工恢复，不作为当前事实自动进入新索引。
3. 分页重放活跃记忆和未归档关系节点，新代际使用独立 namespace；旧数据集继续保留。
4. 对照每条当前原始记录核对映射指纹，再核对后端文档 ID、数量、外部元数据指纹、完整 NodeSet
   和所属代际，并确认每篇文档的 cognify 完成标记及真实图库的文档节点、DocumentChunk
   分块和 is_part_of 关联（`graph_validation.py` 隔离 SDK 图格式）。逐项计算在写锁外执行；内容及映射变更
   由 SQLite 触发器递增审计版本，计算两端及最终提交比较版本。最终事务不全表重扫，
   验收期间有新写入则先追平队列并重新审计。
5. 全部通过才恢复 Cognee 召回。中断按持久化游标续跑，单消费者重新启动时立即回收在途任务；
   处理失败可点击续跑分页重放；图文件损坏或实际节点缺失应重新开始全量构建。
   未知额外文档不会为凑齐数量被删除。投影失败则修正模型/网络问题后重试，未成功前不切换。

Web 构建面板区分原生映射已提交数、排队/处理中数量和 Cognee 流水线报告篇数。扫描分页会使
待同步数增加；整批抽取成功后才提交映射，所以流水线完成若干篇时已提交数可以保持不变。
流水线进度通过公开 `datasets.get_progress` 只读获取，数据集可能含先前未完成的文档，不能把它
当作当前宿主批次已提交数量。最近报告阶段也不代表所有并行文档都停在该阶段。开始时预计数量
只作参考；并发写入会改变最终数量，以逐项验收 `ready` 为准。

进度读取有短超时及短缓存，不持投影写锁，不调用模型，也不参与成功判定或重试调度。页面显示
最近读取、最近观察到变化、当前阶段耗时/总时限及未解决错误；读取失败保留快照并明确标记。
「暂停同步」复用持久化 `sync_enabled` 开关：正在执行的批次结束后才应用，不再领取下一批；
暂停期间新变更继续进入 outbox，重启后保持暂停。「继续同步」恢复同一队列和代际，不调用重建
或清场。单独暂停不校验模型可用性，以免失效配置阻止停止消费；继续及模型编辑仍执行完整校验。
长时间没有变化只是排查提示，不能作为卡死证据。队列重试次数和错误持久化，重启仍可查看。
同步开启时正常重启后不需再次点击全量构建；中断批次可能重新抽取并产生额外调用，但原始记录、旧数据集、
快照与稳定文档 ID 保持。不要同时运行两个进程写同一存储卷；突然断电或文件损坏仍需依赖完整
存储卷备份恢复，不能把正常重启可续跑等同于任何硬故障下都不会丢失数据。
关停不等待整个抽取批次：先停止接单、取消上层抽取/召回，再回收隔离的候选索引进程树。
未提交条目保留在 SQLite outbox，重启恢复候选区后立即回收重试，不将取消误记成同步成功。
原生记忆库仍须正常关闭；候选进程无法确认退出或关键存储关闭报错时，保留宿主和实例锁，
禁止自动超时强杀宿主。日志记录每个组件开始清理与完成耗时，等待告警标明阻塞组件。

全量指「全部可投影的活跃内容」。归档、合并后的原始行、importance=0、计划、墓碑、原始对话、
文件和便签继续留在所属系统，不把它们复制为活跃记忆，也不删除它们。已经历史物删的正文无法
靠重建恢复。验收核对文档与索引归属完整性，不能证明 LLM 抽取的每条语义必然正确，部署验收仍须
用实际业务问题抽查原文、标签和关系；不把总数相等或 API 无异常当作召回质量证明。

构建快照只包含原生记忆 SQLite，**不等于完整回滚备份**。上线前停止写入并备份实际配置目录、
整个数据卷（包含原生数据库、Cognee data/system、文件/便签）以及代码与锁文件。回滚时停止服务，
将配套代码、配置、原生库和 Cognee 卷一起恢复；不能单独拿旧映射配新图库。恢复会覆盖备份之后
的写入，应事先保存这些增量。

Web 存储明细展示整个 Cognee 目录的图、向量、元数据、原文及其他文件占用，含新旧代际，
不含目录外的原生库和备份。独立图/向量文件按 SDK 数据集 UUID 归属；共享原文和元数据不作
人为摊分。统计复用后台目录扫描与持久化快照，附实测时间；没有测量不能解释为占用为零。

Web 按构建 namespace 展示当前和保留的历史轮次，整轮汇总文档数、抽取完成标记及独立图/向量
逻辑大小，详情弹窗列出成员数据集。数量来自已提交清单，目录轮询不打开 SDK、不逐库读取；
未确认的数量/大小显示未知。历史验收状态未持久化时不推断成功或失败；抽取标记不代表整轮验收。
共享原文、元数据和恢复快照不摊分到轮次，逻辑大小不等同于可释放的物理空间。

清理必须先暂停同步，等待批次和配置切换结束。`DELETE /api/memory/cognee/generations/{namespace}`
显式选择整个旧轮次；单数据集入口 `/datasets/{id}` 复用相同规则。当前和外部轮次均不可清理。
本轮 `building` 或扫描完成后的 `failed` 状态，仅在原生扫描、备份已完成，目标没有当前映射
或未提交写入记录引用，且还会保留另一轮有内容的旧索引时允许清理。空目录不算保留的旧数据，
这条保留规则也不表示历史索引已经通过验收。当前轮次 `ready`、队列排空且覆盖一致时，重新
核验内容、标签、抽取状态及实际图谱后，可清理全部旧轮次；不要求永久保留两轮。

后台 `DatasetRetirement` 与投影/配置/压缩共用互斥锁，锁内重新检查目标代际。先逐篇核对原生
归属，将所选轮次全部原文、标签和来源存入 `cognee_legacy_documents`；完整原生 SQLite 备份
通过 `quick_check` 后，才在同一个索引发布事务内调用公开 SDK `datasets.empty_dataset` 删除
所有成员。发布前核对其余数据集不变，正常关闭并重新打开验收后才替换恢复点；部分删除失败
或取消时恢复整轮索引；若关停打断恢复，则在下次启动使用索引前完成恢复。已留存的原文和备份
不回退，本轮队列及映射不重置。

同一当前代际复用一份累计退役备份，先生成、校验临时文件再原子替换。状态经
`cognee/status.retirement` 返回整轮文档留存进度及已提交的数据集数；失败不自动续跑删除。
SDK 按引用计数释放共享原文，禁止绕过 SDK 手工删除文件。清理后重新统计占用，历史恢复点
释放引用后才可能回收共享块；元数据库空闲页不一定立即缩小。UI 操作前再次确认轮次与范围，
目录或运行状态刷新失败时禁止提交清理请求，后端仍独立执行安全校验。
该入口只清理 SDK 可识别的旧数据集，不清理 crash-quarantine 故障隔离目录或重建备份；这些文件
保留作恢复依据，确认业务召回正常并完成独立备份后再按备份维护流程处理。

| 变更 | 是否需要重建 |
|---|---|
| 普通依赖升级、检索权重/预算、API key 轮换 | 不因版本号自动重建；须通过 SDK 契约与真实模型门禁 |
| 新标签前缀、新内容、已有记录标签修正 | 增量投影；不推断旧内容应有的新标签 |
| 聊天/抽取模型更换 | 旧索引可继续读取；需要统一抽取效果时再选择重建 |
| 项目投影格式或上游存储格式不兼容 | 明确迁移或构建；项目格式版本独立于依赖版本 |
| Embedding 模型、端点或维度改变 | 不允许混写，需要隔离的新向量空间和完整迁移 |

数据卷的 `anelf-embedding.json` 仅记录向量空间身份（provider/model/endpoint/dimensions），
不保存 API key。旧卷首次沿用当前有效模型登记，已有数据原样保留；历史上未记录的模型切换
无法仅凭向量反推，维护者仍需确保首次接入时所选模型与旧数据一致。后续保存不兼容模型时拒绝
混写并保留原配置；启动时也检查身份。API key 轮换、聊天模型与召回参数调整无需重建。
已绑定的根目录和前缀禁止在线改写，避免映射指向另一卷。此全量构建按钮在同一向量空间创建新
代际，不支持跨模型换空间。更换 Embedding 须另行准备完整离线迁移：保留旧卷和原生快照，在隔离
配置中创建新库，从权威原文导入并核对所有记录、标签及关系后再切换服务；不可删除身份文件
绕过检查。自动维度探测后记录实际维度并同步到 SDK 公共配置，重启和重新配置从卷身份恢复
已测维度；不因由显式维度切回自动而误要求重建，也不退回 SDK 的默认维度。

### Cognee 能力与项目边界

| 能力 | 当前接入方式及限制 |
|---|---|
| DataItem、增量 add/cognify、LanceDB、Ladybug | 统一文档和 outbox，稳定 ID、标签、元数据、重试日志及安全构建 |
| CHUNKS / CHUNKS_LEXICAL / SUMMARIES | 默认 CHUNKS，词法与摘要可显式深检；与本地 FTS/向量按独立通道融合 |
| GRAPH_COMPLETION / CONTEXT_EXTENSION | 自动深探取关系证据，显式深检可迭代扩展；后者仍可能调用 LLM |
| only_context、references、conflicts | 只取结构化关系证据，保留引用/冲突提示；不把上游的系统或用户提示模板注入记忆 |
| NodeSet | 完整标签与 node_key 定向召回，标签增加不扩张数据集数量；不是权限边界 |
| improve 的全局上下文索引 | 手动或限频构建、逐阶段检查；`recall(depth="overview")` 显式使用新鲜摘要综合回答。增删改立即失效，重启保守退回事实检索，刷新后恢复；普通/后台召回不自动追加摘要生成 |
| improve 的冲突审查 | Web 与 `improve_cognee_dataset(action="conflicts")` 按数据集审查并保留引用；不自动裁决或覆盖权威原文，冲突回到现有图谱/记忆治理 |
| provenance、trace | 召回携带引用与冲突，项目出处、活动日志及原生审计为权威；未使用的上游诊断包装不列入业务接口 |
| session、agent_memory、agents、skills、代码图、MCP、蒸馏/策展 | 上游可选系统，未替换现有会话、技能、任务与图谱治理；需具体需求、权限和质量评估后接入 |

不要为“用满功能”同时开启所有生成式检索或第二套自治治理。新增能力须声明模型输入、调用量与
缓存影响，且应证明业务问题上的收益。公开 API 的存在不表示对应路径已完成业务验收。
Cognee 工作进程由宿主 Lifecycle 托管：成功发布必须正常关闭图、向量和关系引擎；取消或崩溃
可以丢弃候选工作区并回收进程，不能把异常退出当作提交成功。

验证入口：`uv run pytest tests/unit/agent/memory tests/unit/services/test_memory_cognee.py`；
设置 `COGNEE_INTEGRATION=1` 运行 `tests/integration/test_cognee_integration.py`，使用配置模型和
临时数据库验证全量构建、正反标签过滤、关系证据、全局摘要、冲突审查、更新旧事实消失、
共享实体删除隔离及资源关闭；同文件的真实模型用例检查生产工具 schema 的选择及关键参数。
LiteLLM 升级另须运行[缓存健康门](context-cache.md#用量与版本变更)；本地测试模型的通过不替代
部署端全部真实模型的检查。离线测试不能冒充真实服务验收。


### AI 工具使用闭环

- `recall`：明确事实默认 shallow；跨人物/项目关联选 deep；主题总览选 overview。
  `tags` 只加权，硬范围用 filter_tags/filter_any/filter_not。未知标签先 `memory_index`，不猜测旧内容的标签。
- 工具返回 `retrieval` 区分完整、部分与仅原生结果，报告超时与完成通道；降级空结果不证明事实不存在。
  overview 只有实际返回摘要通道结果才标记 summary_used；不可用时返回事实及明确的刷新指引。
- 每条结果包含 evidence_kind、truncated 和可用时的数字 memory_id，供 `get_memory` 回查完整原文、
  来源和变更史。图谱综合不是原话，排序分不是可信度；冲突信息不会因融合选中原生结果而消失。
- `list_cognee_datasets` 只返回当前代际及摘要就绪状态；增强动作 summary/conflicts 明确区分。
  普通事实召回不要求先增强。协调器在投影锁内再次核验当前代际已就绪，Web 与 AI 共用此边界。
  维护有真实模型成本，失败阶段不得报成功。
- 写入先查重，修订走 update_memory；Cognee 异步跟进原生事务，不把综合结果重新存为独立证据。
  自动召回仍与工具、原生关系查询共享去重账本。扩展业务标签有界显示，去重保留不同标签归属。

工具说明属于稳定 schema，发布时变化一次；检索诊断和证据只进入动态工具结果，不注入运行态到稳定前缀。


| 机制 | 位置 | 说明 |
|------|------|------|
| 投影内容指纹跳过 | `store/_shared.py::projection_content_hash` + `cognee_queue.enqueue_sync` | 完整 SHA256 包含投影版本、type/content/source/metadata/tags/timestamp，不含 importance；一致且无在途任务/未提交写入日志才跳过。显式回填 force=True。关系指纹包含节点、边、证据、来源记忆与归属，不含强度；强度变化不重建，证据或标签变化须重新投影 |
| cognee 写盘熔断 | `cognee/write_breaker.py` + coordinator `_projection_allowed` | 进程自身磁盘写入速率超阈值（默认 500MB/5min）时暂停投影认领与自动压缩，冷却到期重评、自调节；平台不支持时 fail-open。状态经 CogneeSyncStatus.paused 暴露；`run_in_idle_window` 用户显式作业不受熔断阻断 |
| ladybug native 监督 | `agent/memory/cognee/ladybug_guard.py`，`install()` 幂等 | 在独立 SDK 进程中串行 native 执行；成功发布前正常排空并关闭，关闭失败不能提交。纯线程看门狗在 SDK 组合根绑定为退出候选进程，宿主用封存快照恢复；不调用不可靠的 native interrupt。WAL 容错不能防原生段错误，也不能修复接入保护前已经损坏的图库 |
| LanceDB 物理压缩 | `cognee/storage.py` + coordinator 空闲窗口调度 | 宿主是唯一维护调度者，适配器边界关闭 Cognee 自带的构图收尾压缩，库内无后台清理任务。`compact_lance_tree` 复用上游有界算法，全数据树每轮最多 4 个碎片合并任务和 1000 个历史版本，轮换起点避免表长期得不到维护；仅清理后继版本已超过 `compact_retention_days` 的旧版本，保留最新数据与保留期内快照。worker 排空后按 `compact_interval_seconds`（默认 86400s）自动执行。AI 工具 `compact_cognee_storage` / `POST /memory/cognee/compact` / Web 按钮统一进入 `request_compact()`，关闭自动维护仍可手动请求；两者均服从写盘熔断与投影锁，成功发布前正常关闭，取消时回收独立工作进程并恢复候选区。`StorageStatsTracker` 以内存 TTL → 磁盘快照 → 空统计返回，请求路径不遍历大库，代际号防旧遍历回写 |
| improve 默认禁用 | `cognee/config.py` `improve_interval_seconds`（默认 0） | 全局摘要有额外模型成本，CHUNKS 不依赖它；显式增强调用 improve 的 global_context_index 阶段，不默认执行 memify 全图重嵌入 |
| goal 不投影 cognee | `store/cognee_queue.enqueue_sync`（source=='goal' 拦截）+ `planning/tracker._persist` | 计划状态 JSON 不是知识：updated_at 高频漂移使投影永不稳定；goal upsert 不入队，原生 FTS/向量检索已覆盖召回；`_persist` 比较 updated_at 之外的语义内容，未变不落库 |
| 投影开关 | cognee.json `project_memories_enabled` / `project_graph_enabled`（默认均 true） | memory 投影与主向量库同源（重复嵌入），graph 投影是原生检索没有的增量，可按需关停 |
| embedding 用量账本 | `agent/memory/embedding/usage.py` + `GET /status/usage` 的 embedding 段 | 引擎级埋点（查询/批量/多模态）：日级 calls/texts/chars，内存累加 + 防抖落盘。cognee 自带引擎不在此口径 |
| 批量对齐与缓存 | `embedding/worker._batch_size` + `engine.max_batch_size` + `embed_query_cache_size` | worker 批次取 min(配置, 客户端 embedding_max_batch)，避免 llm_client 内部拆批；查询向量缓存容量/TTL 可配 |
| 派生索引维护边界 | `cognee/client.py` | 删除、压缩和增强统一进入发布事务；禁止维护脚本直接打开 Lance/Ladybug 写入，绕过工作进程锁和恢复点 |
| 依赖约束 | `pyproject.toml` + `uv.lock` | Cognee、LiteLLM 精确锁定，不覆盖 Cognee 的 LiteLLM 约束；onnxruntime override 为宿主音频栈保留，Cognee 不走 fastembed。Lance/PyLance 压缩前检查内核匹配；Ladybug 沿用 native 监督边界；投影显式 `literal_text`，避免正文被当成 URL 或路径 |

## 遗忘治理

| 机制 | 位置 | 说明 |
|------|------|------|
| 遗忘层兜底召回 | `store/search.py`（search_forgotten / search_archived / search_tombstones）+ recall 工具 `forgotten` 字段 + `restore_memory` 工具 | 归档记忆（向量余弦强信号 + 关键词弱信号）与墓碑 gist 统一检索，与主检索并行执行。采纳规则：归档向量强匹配（≥ `memory_archive_recall_min_score`，默认 0.5）随时浮现；弱命中与全部墓碑仅主检索无果时出现（「似曾相识」而非干扰）。归档项 `restorable: true`，AI 经 `restore_memory(id)` 恢复（向量/访问记录原样回填零重嵌）；墓碑 `restorable: false`，hint 引导基于梗概重新 memorize。附带条数上限 `memory_forgotten_recall_limit`（默认 3） |
| 遗忘墓碑表 | `memories_tombstone` + `purge_archived_memories` | 归档物理删除前把 gist（内容截断 200 字符 + 标签 + 来源/原因，不存向量）留入墓碑表——「曾经知道什么」的元记忆仍在。行数硬上限 `memory_tombstone_max_rows`（默认 5 万，0=不限），超限 FIFO 淘汰；purge 按归档时间最旧优先 |
| 检索练习效应 | `relax_importance` 访问护盾 | 见「存储与检索基座」——常被想起的记忆更抗遗忘 |

## 标签、铁律与受管区块

| 机制 | 位置 | 说明 |
|------|------|------|
| 记忆体系铁律 | `agent/memory/rules_doc.py`（文档持有）+ `config/memory_rules.md`（文件载体，ConfigPaths.MEMORY_RULES）+ `agent.context.assembly._memory_rules_text` | 写入路由（五系统 + 技能分流，一条信息只进一个系统、出处用指针）/ 标签纪律（前缀语义、打标即入联想网络、写前 memory_index 查既有形态、tags 软加权 vs filter_tags 硬过滤）/ 主标签记忆用法 / 披露边界 / 查询路由 / 落盘诚实 / 检索纪律 / 口头纠正（用户说「记错了」时 recall 定位 → update_memory 原地修正 / forget 归档，禁止新旧矛盾并存）/ 图谱治理路由条。缺失时以 DEFAULT_RULES 种子落盘；AI 无写入路径，人类经 Web 记忆页「规则」标签整文档编辑或手编文件；读取走 mtime 缓存，生效文本参与 stable 指纹门控 |
| 主标签记忆（main:hub） | `agent/memory/hub.py` + provider `memory_hub`（priority 31） | 每轮模型调用读取权威条目，工具链后的动态区注入；初始上下文、自动召回（含近期兜底与深探）、人物画像及永久置顶均不保存 hub 副本。显式工具查询仍可读取 hub，工具历史保留查询时的结果。AI 经 memorize（type:permanent + main:hub）整段 upsert；心跳 `ensure_hub` 自愈骨架；forget 拦截归档。`memory_hub_inject_enabled` 控制注入，字符预算 `memory_hub_inject_max_chars` 默认 3000，超长保留首尾并标记省略。hub 维护资产指针与接力线索，目标进度归 planning |
| 便签受管区块硬保护 | `agent/memory/notes.py`（`_MANAGED_BLOCK_RE` + `_assert_managed_blocks_intact`） | `<!-- AUTO:name BEGIN/END -->` 标记对圈定的系统受管区块，便签写工具写入前校验逐字节保留，改动/删除即拒绝；系统写入路径直走 `_atomic_write` 天然豁免。通用读写原语 `update_managed_block`/`read_managed_block`（AUTO:memory-status / AUTO:heartbeat-status） |
| 标签索引观测 | `agent/heartbeat/engine.py::_write_memory_status` | 状态区块仅保留 AI 可行动项（库容/cognee 同步与熔断/最近整理/便签超标）；标签膨胀提醒条件化（总数超 `memory_tag_bloat_threshold`（默认 400，0=关）才注入归并提醒行）；运维遥测不进 prompt，由 memory_stats 工具按需查询 |
| 标签智能 | `agent/memory/store/tag_intel.py` | df/共现图谱/提及词表 TTL 缓存；IDF 评分、共现与图谱邻居联想、查询提及识别的统一驱动层 |

标签写入闸门仅处理文档频率为零的新标签。既有形态（包括只出现一次的标签）原样保留；
新 topic 写法变体只向唯一且 df≥2 的既有标签改写，并保留词间空格；治理候选及已有豁免签名
沿用较宽松的去空白口径，不直接执行归并。完整泛义词可拒收，中文词缀不剥除，
包含关系仅提示，语义归并由 AI 治理。新实体标签须包含频道与非空 ID；显式实体标签非法时，
memorize 返回参数错误，不把其事实转挂到当前会话。旧格式保留给存量迁移与治理，不猜测频道。
update_memory 使用咨询模式，保留传入标签并返回建议，不报告未执行的改写/拒收。

写入闸门和归并议程按权威数据库修订号复核标签统计，读取等待当前写事务结束，避免 TTL 缓存
或回滚前的中间状态影响治理；普通召回继续复用 TTL。统计或规则处理失败放行，取消向上传播。
标签豁免先过滤再截取候选，全部生效规则参与过滤；memory_index 的 tag_exemptions 每页最多
20 条，使用 tag_exemptions_next_offset 续页，按返回签名经 tag_curation_exempt 撤销。
分页仅限制工具输出，不限制规则生效范围。召回通道胜出计数只统计最终返回且有正贡献的结果。
这些建议与豁免只进入动态工具结果，提取词表只进入内部辅助调用，不改主对话稳定前缀。

自然语言提及只映射到唯一的标签：同名实体不自动择一，拉丁词不匹配其他单词内部；
明确提供的标签仍按原有接口检索。文档频率按每条记忆去重计数，结构性标签不参与联想。
多路召回共识按独立查询计数，同一路中的重复结果不叠加置信；融合不改写原候选的分数、标签或归属。

## 证据体系与反思生命周期

| 机制 | 位置 | 说明 |
|------|------|------|
| 证据数学 | `agent/memory/evidence.py`（纯函数零依赖） | 记忆/反思的「可信度」维度（与「记不记得住」分维）：rein/disp 双通道各有独立时钟与半衰期（负向 180 天刻意长于正向 30 天）、读时衰减不改存储、importance 阶梯种子（≥0.9 直通 0.8）、用户确认连击加成、protected 恒 +inf。证据回流来自反思反馈分类与显式人审；去重、合并、任务总结和 AI 自述不作为用户确认 |
| 反思生命周期 | `agent/memory/reflection_lifecycle.py` + consolidator 集成 | 对象是 `type:reflection` 标签的记忆（self_reflection 任务产出播种，memorize/任务结果两写入点接入）。状态机 pending→confirmed（score≥1.0）→晋升（score≥2.0 且过冷却）/denied；负分进 sub_zero 每日倒计时到期归档。晋升 = LLM 合并裁决（promote/merge/reject，复用 dedup.light_llm）写入目标画像——**晋升是人格生长的唯一通道**；LLM 失败只退避重试绝不静默晋升，5 次死信。终态幂等，重入安全 |
| 证据反馈回路 | `reflection_lifecycle.load_verification_block` / `classify_reflection_feedback` / `record_user_feedback` + auto_capture 尾部 | pending/confirmed 反思经画像区「待验证认知」块呈现进对话（呈现即登记 surfaced_at，24h 冷却防追问骚扰）→ auto_capture 周期把该 scope 用户新消息对着「已呈现未裁决」条目跑 light_llm 分类（confirmed +1.0 / denied +1.0 反驳 / ignored -0.2）→ 证据回流驱动晋升/归档；Web 记忆页 ✓/👎 按钮走同一 record_user_feedback（人审直改）。**可见性规则**：带 user:/group: 标签的反思只在对应 scope（含群聊参与人）可见，无实体标签的自认知全局可见——A 的反思绝不注入 B 的对话 |
| 人格双层 | `agent/memory/self_profile.py` + `agent/mind/recall/coordinator.py` | 宪法层=personas/*.json（用户编辑，stable 层冻结）；生长层=`agent:self` 自画像（personality 表 + ENTITY 记忆镜像双写，覆盖式更新前备份）——随画像区每轮注入；已确认反思块紧随其后（score>0 才呈现）。关系动态走图谱 `agent:self` 节点。铁律已登记路由：自我认知只能写反思等晋升，禁止直接改自画像 |
| 积极性频率 | `agent/mind/proactivity.py` | `proactivity_level`（0-1 默认 0.5）：元决策 prompt 注入档位指导、心跳 idle 调度拍数按 `1.5-level` 缩放；AI 可经 update_entity_config 自调 |

## 索引完整性：合并统一 + mem:ID + 谱系审计

以「一条信息只进一个系统，他处需要时用指针 mem:ID 引用」为标尺：

| 机制 | 位置 | 说明 |
|------|------|------|
| 合并语义统一 | `memory_store.merge_into_keep`（唯一底层） | keep（有效分最高者）原地演进（标签并集/访问累加/max 重要性/可选合成内容，version+1），drop 软标记 importance=0 + `metadata.merged_into` 退出全部召回后入既有归档/物删生命周期。protected/ENTITY 画像/规划条目双向准入（不可作 keep 也不可作 drop） |
| 自动去重边界 | `consolidator` + `merge_into_keep(expected_versions=...)` | 向量相似仅用于寻找候选；正文、来源、标签和元数据均一致才自动合并。事务内复核所有候选版本，期间有修改则取消合并，保留新事实与不同出处 |
| mem:ID 分层解析 | `memory_store.resolve` + get/update/forget 工具 | 五态：active（含重定向落地）/ merged（链过深）/ archived（可恢复）/ tombstone（gist + redirect_to）/ missing——被合并的旧 id 沿 `merged_into` 链（限 3 跳防环）解析到存活条目，便签与图谱中的 mem:ID 永不失效；变更类操作遇非活跃条目显式引导（防改僵尸） |
| 谱系审计 | `memory_audit` 表（`actor` 列）+ `list_audit` | 全变更路径（add/update/delete/archive/merge/restore）带触发方归因（tool:*/auto_capture/consolidator/heartbeat_*/task:*/web/planning/reflection*）；出口：get_memory 附最近 5 条事件、`GET /ltm/{id}/audit`；30 天保留（长期谱系走 metadata merged_from/into） |
| 条目级语义链接 | memorize `linked_to` 参数 + recall 联想带出 | 「本条纠正/补充/依赖哪条」的显式声明（上限 5，存在性校验）；recall 命中关联方时 related 一跳带出（hop="link" 优先于标签共现）。与标签共现职责切分：具体关系用 linked_to，同主题关联靠标签 |
| 标签归并议程 | `tag_intel.merge_candidates` + consolidator/心跳 memory-status 区块 + `memory_index` 输出 | 确定性候选两类：写法变体（NFKC/小写/空白归一同形）与 topic 包含关系对（df≥2，归并高频方）——事实归系统、决策归 AI（经 update_memory 执行）；无候选零注入；僵尸条目（importance=0）不进统计 |

## 代谢与纪律

| 机制 | 位置 | 说明 |
|------|------|------|
| temporal_scope 时间语义 | `store/_shared.py::temporal_is_past/temporal_weight` + 检索评分 + 行尾注 | event 提取输出 temporal_scope（episode/state/pattern 白名单）+ `activity_date`。读时判定超期（state 7 天 / episode 3 天，pattern 永不）→ 注入行追加「过去时·可能已变」尾注 + 检索整体降权（`memory_temporal_expired_weight` 0.5，聊往事仍可低权命中，淘汰仍归遗忘曲线）；memorize 工具补 temporal_scope 参数 |
| 防复读 | `agent/memory/anti_repeat.py` + freshness 块（VOL_SESSION+2） | 生成侧软提示：近期 AI 回复 df≥3 的高频 ngram top5 引导全部回复周期换角度；出站侧的重复内容由「出站事实」provider 动态注入覆盖（见[频道分册](channels-and-scopes.md)） |
| importance 校准 | `auto_capture._EXTRACT_PROMPT` 校准表 + memorize 工具描述 + `self_profile.PROFILE_MEMORY_IMPORTANCE` | 提取侧锚点表（0.9+ 身份级/明确「记住这个」，0.8 长期偏好与承诺，0.7 阶段动态，0.6 线索，0.5 弱线索不预过滤）；画像 ENTITY 镜像 importance 统一 0.9（身份级事实，四处写入点共用单点常量） |
| 事件时间本体 | `agent/memory/auto_capture.py` | event 提取输出 temporal_scope（白名单校验，未提供则留空）落 metadata；date 落 `activity_date` |
