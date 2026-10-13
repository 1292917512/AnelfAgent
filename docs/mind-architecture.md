# 思维系统架构

面向维护者的现状说明，按当前源码组织。代码和测试是最终依据；修改思维链路时，先确认本文涉及的
边界是否仍然成立。

## 一、从消息到自主周期

```text
频道/服务消息
  → PFC（待处理消息与一般任务队列）
  → Mind.execute_mind()
  → cycle：廉价检查 / 态势收集 / 元决策
  → decision_executor：REPLY、REFLECT、PLAN、TASK、TOOL_ACTION 等
  → reply 或后台任务
```

`Mind` 是组合宿主：

| 组件 | 责任 | 入口 |
|---|---|---|
| `agent/mind/mind.py` | 组装运行时组件，提供周期、回复和反思的宿主接口 | `Mind.execute_mind()`、`Mind.reply()`、`Mind.reflect()` |
| `agent/mind/cycle.py` | 自主周期的锁、fast-path、态势收集、元决策和收尾 | `_cycle_body()` |
| `agent/mind/prefrontal_cortex.py` | 对外提供待办、工具、上下文和会话状态门面 | `PrefrontalCortex` |
| `agent/mind/work_memory.py` | 待办消息、短期状态和动态任务数据 | `WorkMemory` |
| `agent/mind/tools/decision_executor.py` | 把结构化决策分发到回复、反思、计划和任务执行器 | 决策执行函数 |
| `agent/mind/tools/think_loop.py` | 单次回复的多轮模型调用和工具调用循环 | `think_loop()` |

- 自主周期由 `_cycle_lock` 串行化；没有待处理消息、任务或分析项时直接结束
- 普通消息在没有复杂任务、目标或画像工作时走 **fast-path**，直接创建 `REPLY` 决策；复杂场景才
  收集完整态势并调用元决策模型
- 心跳周期会把长决策转为后台执行，避免阻塞新消息；`mind.is_reply` 为真时 tick 只做维护与提醒
  不启动任务
- 元决策 decide 的 target 是 LLM 自由文本，一律经 `decision_executor.normalize_target_scope`
  规范化后才可用（规范 scope 直接采信；裸 id/旧格式按 PFC `known_scopes()` 唯一解析回填；
  无匹配或歧义时 REPLY 回退 `pop_next_reply_target`、PROACTIVE 放弃告警）——畸形 target 不允许
  拼出影子会话
- tool_action 的 target 是显式通知的目标提示，不是普通正文的默认收件人。反思正文始终内部可见；
  需要通知时由 `send_message` 投递并核对回执，例行核对无需发消息。
- **唤醒预算**（`agent/mind/wake_budget.py`）：连续自动唤醒超 `background_wake_budget`（默认 3）
  不再触发新周期（防自我激励循环），真人输入重置

## 二、回复与工具循环

`Mind.reply()` 进入 `agent/mind/tools/think_loop.py`。一次回复通常重复以下步骤，直到模型给出
结束请求或达到停止条件；正文自身不代表已回复：

1. 从当前 scope 取得上下文和可用工具
2. 调用统一 LLM 入口，保留必要的工具调用和 reasoning 字段
3. 执行工具并把结构化结果加入当前轮的工具链
4. 经过结果预算、错误分类、重试和循环守卫后决定继续、重试或停止
5. 完成回复，写入消息历史并释放本轮动态工具状态

### end_reply 与产出语义

- **REPLY 模式**：用户触发轮次优先用 `send_message` 显式发送，不经过 Jev 正文审核。
  `send_interim_text` 控制工具轮与后台等待中的正文，`send_final_text` 控制独立最终正文；
  按开关允许的正文复用频道发送管道自动投递，等待进度在挂起前发送；未配置原生判断模型时
  按普通回复投递，配置后仅明确审核通过才发送，故障、离线与不确定不投递，也不回退慢模型。
  同批已有发送工具时不重复自动投递；工具结果、内部事件处理和
  `end_reply` 同批正文不自动投递；显式结束不拦截、不补发，之后的同批工具返回未执行回执。
  当前会话刚执行发送且没有新工作、后台完成事实或用户消息时，自动正文不重复发；未知发送结果须先核对。
  投递标签区分 `auto/channel/event/silent/internal` 方式及 `sent/failed/unknown/skipped/pending` 状态，
  以真实频道回执确认送达。自动投递沿用目标归一、会话隔离、防重复、实时历史和跨频道水位机制；
  审核结果及未送达事实在动态尾部与工作区可见，不改写稳定缓存前缀。
  工具轮的审核与工具并行，目标绑定生成时的 scope；本批工具与投递结束后再进入下一模型轮，
  回执接在完整工具批次之后。中断会取消并回收审核，发送中断标记结果未知，不自动重发。
  未发送正文保留在执行链中，不作为 assistant 消息写入持久会话；仅简短投递事实可留存。
- **REFLECT 模式**：模型发起**工作工具**调用即判定此前纯文本为中间独白——从 collected_text 移除
  （字符数归档进 execution_steps 可追溯），产出只保留收束前最后一个未被工具调用打断的连续文本段。
  **end_reply 是收束信号而非工作工具**：纯 end_reply 批次不构成「打断」，其同批正文即最终连续
  文本段纳入产出
- **内部空间与显式发送权限分离**：`reflect` scope 没有默认频道，不能继承发起会话的默认路由。
  自主反思和新建任务默认允许显式发送；任务 `allow_output_tools=false`、技能核验与子代理的
  限制仍由调用方声明。已有明确禁用的任务配置保持有效，可从任务页或 AI `update_task` 修改。
  `send_message` 不经过 Jev 正文审核，仍需通过目标校验并取得真实频道回执。
- **跨会话感知**：其他会话待回复的新私聊与群聊 @ 每轮刷新到 provider 动态区，位于
  工具链之后、执行状态之前，不在末尾执行提示中重复追加。它们不改变当前空间或默认收件人，
  也不会被写入当前会话历史。`switch_session` 打开目标 scope 的独立回复；当前执行仍属于原 scope。
- 结束原因经 completion 容器贯通：completed / failed / budget_exhausted（轮次预算用尽）/ interrupted，
  空产出 no_output

### 思维循环防护

| 机制 | 文件 | 说明 |
|------|------|------|
| 工具守卫 | `agent/mind/guardrails.py` | 精确失败重复/同工具连续失败/无进展循环检测，动作 warn/block/halt；分级提醒（首次温和、后续附参数预览）；用户插话重置计数 |
| 错误分类 | `agent/llm/resilience/classifier.py` | LLM 错误分类（rate_limit/context_overflow/auth 等）驱动重试策略 |
| 自适应重试 | `agent/llm/retry.py` | 指数退避 + 抖动（jittered_backoff） |
| 上下文压缩 | `agent/mind/context_compressor.py` | 溢出检测（真实 usage 优先）→ 保头保尾 + LLM 摘要 → 压缩反馈注入；摘要调用复用主前缀命中 KV 缓存；被压缩中间段的文件操作经规则提取为 `[已操作文件]` system 消息（单调增长的事实链，上限 30 条/类，不占摘要预算） |
| 结果预算 | `agent/mind/result_budget.py` | 按模型窗口动态截断工具结果（15% 单条 / 30% 整轮） |
| 会话令牌 | `agent/security/session_token.py` | 一次性令牌标记可信历史，泄露即 SECURITY 停止 |
| 威胁扫描 | `agent/security/threat_scanner.py` | 注入模式扫描（工具结果标记 / 记忆写入拦截） |
| 结果脱敏 | `core/sanitizer.py` | API Key/Token/密码自动遮盖（工具结果 + 日志） |
| 崩溃尾部修复 | `agent/mind/crash_recovery.py` | 回复检查点落盘（`reply_checkpoints` 表），启动扫描崩溃残留注入「上次被中断」元消息；崩溃退出时附带崩溃上下文（macOS .ips 关联） |
| reasoning 条件回传 | `think_loop.preserve_reasoning_fields` | `reasoning_details` 仅工具轮回传（DeepSeek 官方规则），纯文本轮省 token；`thinking_blocks` 无条件保留 |
| 新消息并入上限 | `round_helpers._merge_new_messages` | 消息洪峰分批并入（20 条/20000 字符每轮）：首条强制并入保证进度，超出部分不推进水位（下轮接续）且保留待处理队列条目——留存消息必有人回复 |

### 消息契约

- 系统注入消息（压缩反馈、rehydration、超时恢复、后台任务、实体推送等）须附
  `"_source": {"origin": "<词汇>"}`，发送前由 `normalize_for_send` 与 `_layer` 一并剥离
  （LLM 不可见，供归因）。已用词汇：`compression` / `rehydration` / `timeout_recovery` /
  `length_recovery` / `background_task` / `push` / `context_provider`；新增注入点复用或扩充词汇表。
  `_source` 不进 DB，仅作用于内存消息链
- 一次性事件（后台任务完成/实体推送/定时提醒/重启补回/会话切换/委托完成）写目标会话**对话历史**
  （system，trigger_mind=False）而非短期记忆：await 返回即历史落库，随后的回复周期拉取必含
  （无竞态）；写入失败回退短期记忆兜底。短期记忆回归纯持续提醒语义

## 三、上下文组装与缓存

`ContextAssembly.build_llm_context()` 使用 `ContextPipeline` 按变动频率从静到动组装消息，
并为每个块附加内部 `_layer` 标记：

```text
stable       人设、工具规则和稳定工具目录（对话内冻结，字节级稳定供前缀缓存复用）
summary      对话摘要（折叠周期内字节固定，历史前缀锚点）
conversation 对话历史（回复起点按折叠水位构建；轮内收发从有界内存窗口追加）
context      便签、文件索引等尾部上下文（内容寻址缓存保证未变时字节稳定）
session      画像、召回、短期记忆和技能（每会话构建）
message      本轮状态或安全提示
tool_chain   本轮工具调用（think_loop 管理，追加式冻结）
provider     上下文提供者实时注入（每轮发送组装时收集最新快照，置于工具链之后、exec_context 之前）
exec_context 本轮执行状态（每轮全量重建）
```

层的注册和排序由 `agent/mind/context_pipeline.py` 统一维护；新增上下文块应声明其层和变动率，
不要在调用点手工插入消息。发送前由 `normalize_for_send()` 清理内部层标记和来源字段。

### 每轮动态区预算纪律

| 机制 | 位置 | 说明 |
|------|------|------|
| exec_context 步骤预算 | `context_assembly._MAX_RENDERED_STEPS`（12） | `[已完成步骤]` 只保留最近 12 步 + 省略行；finish_think 的最终执行摘要仍消费全量清单 |
| 执行摘要入库瘦身 | `reply_finalize._compact_summary_for_history` | 对话历史中的 `[已执行操作摘要]` 只入库统计头 + 最近 5 条 + 查询指引；完整清单进进程内环形缓冲（每会话 8 轮），AI 经 `get_execution_log(turns=)` 按需取回 |
| 缓存命中状态行 | `round_helpers._cache_status_hint` | 上轮真实 usage 的命中率注入 exec_context（端点可观测且 `last_input_tokens > 0` 才注入，不谎报 0%）；配置 `cache_status_hint_enabled` |
| 非输出提示独白信号驱动 | `think_loop._handle_tool_round` | 「工具结果仅你可见」提示只在工具调用伴随文本独白时注入；静默工具轮零注入 |
| 纪律单一权威源 | `agent/memory/rules_doc.py` + 工具 schema + hub 骨架 | 同一纪律只讲一遍：路由/纪律归铁律（stable 唯一来源），工具 schema 只留参数语义 |

### 对话折叠与换向

- **窗口折叠**：三入口共用 `ConversationData.schedule_fold`（窗口滞回触发 / 心跳空闲折叠 /
  AI 工具 `fold_conversations`）。窗口配置两个：总条数 `max_conversation_size` + 保留百分比
  `conversation_raw_keep_percent`（保留条数 x 与滞回 H 均派生：x=M×百分比、H=x，窗口在
  x~M+x 波动、每批折 M 条）。折叠成功后经预热钩子（`mind.prewarm_scope_cache`）发 1-token
  轻调用写热新前缀；预热消息必须经 `normalize_for_send` 再发（否则 `_layer` 内部标签泄露给供应商）
- **折叠看门狗分段化**（`conversation_fold.py`）：DB 读/写段各 60s 短护栏；摘要段总护栏
  `conversation_summary_llm_timeout`（默认 900s）——超时以普通 TimeoutError 进入既有丢批路径
  推进水位线，一次失败即收敛
- **换向折叠**（web 域会话分支）：用户从某条消息换方向，其后消息段（id 区间）装配时过滤出上下文，
  原位插入含摘要的 system 标记（摘要走 `mind.summarize_text`，失败降级确定性摘要）；DB 保留可
  检索（recall 不受影响）可恢复（active=0 即回）；过滤在 `_fetch_window_rows` 统一收口
  （装配/压缩/折叠调度计数同口径）

## 四、工具集合如何形成

`agent/mind/tool_assembly.py` 维护当前 scope 的工具集合，候选来源：

| 来源 | 说明 |
|------|------|
| always | 永驻工具（end_reply, send_message 等） |
| mcp:* | MCP 服务工具 |
| channel | 频道能力匹配 |
| tag_match | 消息标签激活（如 media:image） |
| hot_recall | 热门工具 top-N |
| discovered | 动态发现 |
| activated | 已激活的沉睡分组（activate_tool_group） |

合并结果经两道门控过滤（`core/tool_gate.py` + `agent/mind/tool_activation.py`）：

1. **check_fn 门控**：工具声明的前置条件检查（30s TTL 缓存 + 60s 瞬态故障宽限），不通过则不出现在 schema
2. **沉睡/激活**：`allow_sleep=True` + `sleep_brief` 的工具默认沉睡（目录中仅展示 brief），AI 调用
   `activate_tool_group` 唤醒，`deactivate_tool_group` 显式关闭（双向阀；关闭重构 tools 数组，缓存
   前缀重写一次），按 scope 隔离、按轮次消耗

- 工具集合版本元组 = (assembly, activation, EntityRegistry.version())——热同步/reload/WebUI 开关等
  注册表增删后，**回复进行中**的下一轮即重建 active_tools；重建经追加式冻结保持前缀字节稳定
- **deferred 组激活时序纪律**：activate_group 对空组连组名都不登记——deferred 工具组只有模块被
  import 后才进 `_deferred_registry`，Mind 构造期激活的组（thinking/session/delegation）必须在
  assemble_runtime 提前导入清单中显式 import（守卫测试
  `tests/unit/agent/delegation/test_tool_registration.py`）
- 工具激活状态按会话 scope 隔离；会话结束时按既定生命周期清理
- **反思工具族合并**：无选择器的反思/任务循环复用回复级装配（`reflect_share_reply_tools` 默认开，
  同族追加式冻结）——reply/默认 reflect 共享单一冻结数组族；带选择器的子代理档案仍走精简目录

## 五、判断能力（agent/judgment，Jev 接入）

结构化评判三原语（Choice 选项 / Score 评分 / Noul 是非）的统一通道：

- **类型契约** `agent/judgment/types.py`（pydantic 判别联合，置信度公式 (n·p−1)/(n−1) 双通道共用）
- **双通道引擎** `engine.py`（配置每次调用现读，天然热更）：自动调用按首选及回退顺序选择可用模型，显式指定只用该原生模型，经 `client.py`
  System One 原生 HTTP（POST /v1/systemone，答案严格校验；TypeSafe 云端与自部署服务共用）；未配置或失败时降级 `bridge.py` 普通
  聊天模型（严格 JSON 提示词 + 容错解析 + 本地合成概率/置信度，单题失败不拖垮整批记入 missing）
- **AI 调用面**：mind 核心工具 `judge`（group="judgment"，tags=["always"]）；上下文三档供给
  context_mode：none（默认，只评判显式传入的 state）/ conversation（注入当前会话最近消息）/
  full（窗口全量 + 折叠摘要，慎用档）；state 与非 none 模式互斥（同传报 PARAM）；护栏
  `judgment_context_messages` / `judgment_context_max_chars` / `judgment_full_max_chars`
- **模型目录**：`ConfigPaths.JUDGMENT`（`config/judgment.json`）保存服务商和模型；`catalog.py`
  是 Web/AI 唯一校验与原子写入口。服务商持有 `name/base_url/api_key/models`，模型持有
  `name/model`（展示名/服务端模型名），模型 ID 全局唯一，`default_model` 为首选，`fallback_models` 为显式有序的回退候选。未加入候选的模型不参与自动选择。
  模型不进入 llm_clients.json/ModelType：System One 的概率评判协议与聊天/媒体协议不同。
  启动时仅在新目录不存在的情况下搬运旧单连接配置，保留环境变量引用，不再双轨读写。
- **Web 面**：模型页「判断 (Jev)」页签，管理多服务商、多模型、默认模型及逐模型状态/测速。
  `/api/judgment/providers` 与 `/providers/{id}` 提供目录读写，`/models/{id}` 修改单模型，
  `/priority` 原子设置 `model_ids` 顺序（首个为默认），`/default` 单独设置首选。
  `/test` 可传 `model_id` 进行无推理连通检查，仅 `inference=true` 才提交测试问题并消耗额度。超时、预判、回退和复查间隔等策略
  仍由 `/api/config/meta`（`judgment/core`）管理。
- **AI 配置面**：`list_judgment_models` 返回脱敏目录和健康状态；`configure_judgment`
  支持 `save_provider/delete_provider/save_model/delete_model/set_default/set_priority`；`set_priority`
  的 `config.model_ids` 按优先级排列，空数组关闭原生模型选择。`test_judgment_model` 默认仅 HEAD，
  显式 `inference=true` 才测试题型，不改变优先级。`judge(model_id=...)` 显式选模型，省略则自动故障回退。
  保存服务商可只传要改的字段，若传 `models` 则替换该服务商完整模型列表；保存单模型传
  `provider_id/model/name`。删除模型同步移除候选引用，首选删除时仅提升已经配置的下一候选。
- **服务连接**：服务商 `base_url` 可填根地址、`/v1` 或完整 `/v1/systemone`，保留反向代理路径。
  `api_key` 可选，留空不发 Authorization；支持 `${ENV_VAR}`，掩码或省略保留原始密钥/引用，
  显式空字符串清除。云端可用 `https://api.typesafe.ai` 和 `jev-latest`，本地填自身服务地址与模型名。
  测试不以普通模型回退掩盖已选服务的故障。网络沿用全局代理规则，本地与内网默认直连。
- **可用性与优先级**：故障模型暂时跳过，继续尝试已配置的下一原生候选；有效判断结果（包括拒绝）
  不触发换模型重审。首次调用中的并发请求不重复等待同一未知服务。正常模型不周期探测；实际调用
  失败后才进入冷却。`health.py` 经 Lifecycle 托管，每隔 `judgment_recheck_seconds`（默认 300 秒）
  只对离线服务发无密钥、无正文的 HEAD，每批最多四个。恢复后重新按原优先级选择，不改写配置。
  HEAD 仅证明 HTTP 服务可达；认证/协议是否正确由业务调用或显式推理测试验证，已知凭据错误
  不因 HEAD 成功而清除，须修正配置或显式推理测试成功。更改名称或顺序不清空可用性状态。
  `judgment_connect_timeout`（默认 2 秒）限制建连及连通探测；`judgment_timeout` 是调用总预算，
  自动回复/可选工作使用 `judgment_gate_timeout`（默认 5 秒）的更短总预算，候选数量不放大等待。
  总预算耗尽即返回失败，下次直接跳过已失败模型。普通模型回退由调用方独立允许；回复审核与
  可选工作均不转普通 LLM。可选工作判断失败保留原流程；普通正文仅在明确、一致的拒绝时拦截，
  审核不可用或不确定按投递开关正常发送，仍以频道成功回执确认送达。
  台账内存有界、不持久化；AI/Web 可见状态来源（连通/推理）、失败原因及下次检查时间，不改变稳定前缀。
- **StartLux GGUF**：按[官方部署文档](https://github.com/StartLuxLabs/StartLux-Decision/blob/main/docs/inference.md#gguf-and-llamacpp)
  启动 `startlux_decision.gguf_server`，将其服务地址（例如 `http://127.0.0.1:8090`）填入上面的配置。
  GGUF 是权重格式；普通 llama-server 的聊天接口不能代替返回概率分布的 System One 服务。
  地址由 Anelf 后端访问，远程部署时 localhost 指 Anelf 所在主机。无需在 Anelf 内加载权重或增加模型运行时依赖。
- **内部接入点**：记忆写入判重（`agent/memory/dedup.py::judge_write`）——判断段经引擎一次调用
  并行评判，热路径零 LLM 调用；失败一律保守退回直接写入（见[记忆分册](memory-system.md)）

## 六、LLM 钩子面（agent/hooks_llm/）

「在 LLM 思考边界执行带上下文的 LLM 工作」的统一注册原语，与心跳/任务系统平行、与 shell 钩子
（`agent/hooks`，同步阻塞守门，见[运维分册](operations.md)）分层——本面是**异步并行扩员**：
同一事件可挂多个钩子：mode=async 默认后台执行，mode=sync 按优先级等待。
LLM 钩子不设整体时间截止；单次模型/工具 I/O 仍走各自超时与重试。AI 经动态尾部获知排队、调用、耗时和最近进展，使用 check_background_tasks / terminate_background_task 检查及取消。同步模式不使用防抖延迟。
`stall_seconds` 控制无进展提醒（0 关闭），一次停滞仅提醒一次，有新进展后重新计时，不终止任务。
后台查询返回 `notification`：always 完成通知、on_result 有产出或失败通知、on_failure 仅失败通知、silent 不通知。
通知属于 AI 内部事实，唤醒仍受路由和预算约束；频道回复必须显式发送。内部钩子不参与主回复的自动等待。
`check_background_tasks(task_id, after_sequence)` 可读取脱敏操作证据；每任务保留最近 32 个事件，返回游标、截断和淘汰标记。
动态尾部只保留最近五分钟内的钩子终态摘要，历史仍可按任务查询；模型观察不会消费完成通知。
技能评审保持异步。事件订阅将超时治理交给钩子宿主，避免总线默认 5 秒提前取消。
快照在调度时冻结，后台队列有界；执行模式和结果共用 core/hooks 观测。

| 机制 | 位置 | 说明 |
|------|------|------|
| 上下文快照带出 | `Mind.reply` completion 容器 → `think_loop` finally → `reply_finalize.complete_reply`（EVENT_AFTER_REPLY payload 增 `messages`） | REPLY 全链路带出完整消息链（base + tool_chain）；携带触发轮工具目录，钩子调度深拷贝冻结，transcript 默认完整继承 |
| 钩子注册表与装饰器 | `agent/hooks_llm/spec.py`（`LLMHookSpec` / `HookRegistry` / `@llm_hook`） | 声明式注册：event（白名单 after_reply/context_pressure/delegation_resolved/llm_end）+ 上下文档位（none/lean/transcript）+ `when` 条件门控 + 治理参数（max_concurrent/cooldown/debounce/priority/model/tool_tags）+ owner/source 归属。**llm_end 是高频事件**：装饰器与 _sdk 桥都强制最小冷却 `LLM_END_MIN_COOLDOWN_SECONDS`（20s），防成本失控 |
| llm_end 快照源 | `agent/mind/llm_invoker.py`（EVENT_THINKING_LLM_END payload 增 `messages/tools`） | 保留本次调用消息的缓存分层与工具目录；scope 从触发会话解析。tracer 剔除 messages/tools，不复制完整上下文到诊断记录 |
| 上下文快照与护栏 | `agent/hooks_llm/snapshot.py` | 完整深拷贝消息、嵌套工具调用与结果，不按字符截去执行证据；保留 `_layer/_source` 至最终发送边界。transcript 受总开关门控，context 可选择 transcript / lean / none；模型窗口溢出仍交给统一压缩器 |
| 并行执行与治理 | `executor.py`（dispatch 后台调度 + `_RecursionGuard` 防递归 + per-hook/per-scope cooldown/debounce + 全局池 semaphore）+ `runtime.py` + `configs.py`（`hooks_llm/*` 组） | 异步扩员：emit 方不被钩子 LLM 阻塞；防递归（钩子执行树 ContextVar 标 origin，跨 create_task 拦截派生事件）；并发（全局池默认 2 + per-hook 默认 1）；频控（cooldown 锚点在调度受理时同步记录；debounce 用 call_later 句柄 + 最新快照槽）；路由（route_output 默认 True 走 unclaimed→wake_budget 通道唤醒主思维；False 内部治理类钩子成功静默、失败通知 AI）。runtime 经 LateBinding 由 bootstrap 实例化、注册为 Lifecycle 组件——关停 drain（先停订阅，再等运行中钩子收尾，超时才取消） |
| 技能后台评审 | `agent/skills/background_review.py`（`skill_review` 钩子） | event=after_reply + context=transcript + tool_tags=["skills"] + route_output=False + 6 轮上限；评审材料为完整 transcript + 四问框架 |
| 任务事件触发 | `agent/task/event_trigger.py` + `agent/heartbeat/engine.py::_sync_event_triggers` + `agent/task/tools.py` | 任务的第五种触发方式（与 heartbeat/scheduled/idle/manual 正交）：带 trigger_event 的任务经钩子面注册 `task_event:<name>` 钩子（context=none），命中后调 `HeartbeatEngine.run_task`（复用 inflight 去重与执行历史落盘）；任务 CRUD 经 reload 即时重建、无孤儿钩子 |
| 实体桥接 | `entities/_sdk.py::register_entity_llm_hook` | 实体经 _sdk 桥注册钩子（owner 缺省取实体模块名）；entities→agent 唯一豁免通道同 push_notify 模板 |
| Web 观测与配置 | `services/hooks_llm.py` + `web/routers/hooks_llm.py` + 前端设置页「LLM 钩子」Tab | 面板列出全部已注册钩子；治理参数经统一配置面 `hooks_llm/*` 组热调，Web 不另设写路径 |

钩子从受理进入执行时登记后台任务，排队期间也可查询与取消；只保存有界阶段事实，完整过程复用工作区的内存记录。完成或取消后释放进度回调与取消句柄。内部评审不触发新回复，其最近完成/失败摘要通过所属会话的动态尾部呈现，不改变历史或 stable 层。

每个代码钩子的可调参数注册为 `hooks_llm/<name>` 配置组，AI 与 Web 共用 `update_entity_config` / 配置元数据写入；既有 config_key 继续作为该钩子的启停真源。配置只影响新受理的执行。`get_hooks` 可发现声明及配置键，`set_script_hooks` 修改单个事件的脚本声明。召回全局参数在 `memory/recall`，单轮由 `set_recall_policy` 或 SDK 输入覆盖，手动 recall 不受自动档位限制。

## 七、修改思维系统前的检查顺序

1. 明确修改属于周期、回复循环、上下文、工具装配、心跳还是记忆边界
2. 先读对应组件及其测试，确认数据从哪里进入、在哪个层级变换、最终由谁持久化或投递
3. 检查 `_layer`、`_source`、scope 和工具版本是否会泄露到供应商或跨会话串线
4. 为新增行为补充最近组件的单元测试；涉及真实 LLM、频道或外部服务时再运行相应集成测试
5. 修改完成后检查上下文顺序、并发锁、取消收尾、错误结构和导入方向

不要把本页扩展成逐轮变更记录；需要记录一次验收结果时使用专门的报告或计划文件。
