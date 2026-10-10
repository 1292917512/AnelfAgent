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
- tool_action 的 target 语义是「操作结果投递目标」而非「操作上下文」：填了即会把反思产出经统一
  管道投递到该会话，自查/核对类操作不得填；零产出即不投递
- **唤醒预算**（`agent/mind/wake_budget.py`）：连续自动唤醒超 `background_wake_budget`（默认 3）
  不再触发新周期（防自我激励循环），真人输入重置

## 二、回复与工具循环

`Mind.reply()` 进入 `agent/mind/tools/think_loop.py`。一次回复通常重复以下步骤，直到模型给出
正文、调用 `end_reply` 或达到停止条件：

1. 从当前 scope 取得上下文和可用工具
2. 调用统一 LLM 入口，保留必要的工具调用和 reasoning 字段
3. 执行工具并把结构化结果加入当前轮的工具链
4. 经过结果预算、错误分类、重试和循环守卫后决定继续、重试或停止
5. 完成回复，写入消息历史并释放本轮动态工具状态

### end_reply 与产出语义

- **REPLY 模式**：end_reply/[SILENT] 为静默收束——同批正文与暂存独白一律不投递，回复必须经
  send_message 发出；纯文本保底投递仅剩独白掐断/守卫中止/安全上限等强制收尾路径
- **REFLECT 模式**：模型发起**工作工具**调用即判定此前纯文本为中间独白——从 collected_text 移除
  （字符数归档进 execution_steps 可追溯），产出只保留收束前最后一个未被工具调用打断的连续文本段。
  **end_reply 是收束信号而非工作工具**：纯 end_reply 批次不构成「打断」，其同批正文即最终连续
  文本段纳入产出
- 结束原因经 completion 容器贯通：completed / budget_exhausted（轮次预算用尽）/ interrupted，
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
conversation 对话历史（实时从 DB 获取，禁止缓存；水位线后纯追加）
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
- **双通道引擎** `engine.py`（配置每次调用现读，天然热更）：有 `judgment_api_key` 走 `client.py`
  TypeSafe 原生 HTTP（POST /v1/systemone，答案严格校验）；未配置或失败时降级 `bridge.py` 普通
  聊天模型（严格 JSON 提示词 + 容错解析 + 本地合成概率/置信度，单题失败不拖垮整批记入 missing）
- **AI 调用面**：mind 核心工具 `judge`（group="judgment"，tags=["always"]）；上下文三档供给
  context_mode：none（默认，只评判显式传入的 state）/ conversation（注入当前会话最近消息）/
  full（窗口全量 + 折叠摘要，慎用档）；state 与非 none 模式互斥（同传报 PARAM）；护栏
  `judgment_context_messages` / `judgment_context_max_chars` / `judgment_full_max_chars`
- **Web 面**：模型页「判断 (Jev)」页签；写路径只有 /api/config/meta 一条（`judgment/core` 组）。
  刻意不进 llm_clients.json/ModelType——判断模型不满足 litellm 对话/媒体协议假设
- **内部接入点**：记忆写入判重（`agent/memory/dedup.py::judge_write`）——判断段经引擎一次调用
  并行评判，热路径零 LLM 调用；失败一律保守退回直接写入（见[记忆分册](memory-system.md)）

## 六、LLM 钩子面（agent/hooks_llm/）

「在 LLM 思考边界派生带上下文的异步 LLM 工作」的统一注册原语，与心跳/任务系统平行、与 shell 钩子
（`agent/hooks`，同步阻塞守门，见[运维分册](operations.md)）分层——本面是**异步并行扩员**：
同一钩子位置（事件）可挂多个钩子，命中后 `asyncio.gather` 并发拉起，各自独立治理桶。

| 机制 | 位置 | 说明 |
|------|------|------|
| 上下文快照带出 | `Mind.reply` completion 容器 → `think_loop` finally → `reply_finalize.complete_reply`（EVENT_AFTER_REPLY payload 增 `messages`） | REPLY 全链路带出完整消息链（base + tool_chain）；`complete_reply` 逐条浅拷贝冻结后发射，钩子面以 transcript 档位消费 |
| 钩子注册表与装饰器 | `agent/hooks_llm/spec.py`（`LLMHookSpec` / `HookRegistry` / `@llm_hook`） | 声明式注册：event（白名单 after_reply/context_pressure/delegation_resolved/llm_end）+ 上下文档位（none/lean/transcript）+ `when` 条件门控 + 治理参数（max_concurrent/cooldown/debounce/priority/model/tool_tags）+ owner/source 归属。**llm_end 是高频事件**：装饰器与 _sdk 桥都强制最小冷却 `LLM_END_MIN_COOLDOWN_SECONDS`（20s），防成本失控 |
| llm_end 快照源 | `agent/mind/llm_invoker.py`（EVENT_THINKING_LLM_END payload 增 `messages`） | 发射时附本次调用实际发送的消息链（已规整）；scope 由 executor 从思维 ContextVar 推。tracer 节点 data 剔除 messages（诊断字段保留） |
| 上下文快照与护栏 | `agent/hooks_llm/snapshot.py` | 快照 = 浅拷贝列表 + 逐条 dict 浅拷贝；发送边界经 `normalize_for_send` 剥 `_layer/_source`。transcript 受 `hooks_llm_transcript_enabled` 门控与字符护栏（超限保头 70% 尾 30% 截断） |
| 并行执行与治理 | `executor.py`（dispatch 后台调度 + `_RecursionGuard` 防递归 + per-hook/per-scope cooldown/debounce + 全局池 semaphore）+ `runtime.py` + `configs.py`（`hooks_llm/*` 组） | 异步扩员：emit 方不被钩子 LLM 阻塞；防递归（钩子执行树 ContextVar 标 origin，跨 create_task 拦截派生事件）；并发（全局池默认 2 + per-hook 默认 1）；频控（cooldown 锚点在调度受理时同步记录；debounce 用 call_later 句柄 + 最新快照槽）；路由（route_output 默认 True 走 unclaimed→wake_budget 通道唤醒主思维；False 内部治理类钩子以 claimed 完成）。runtime 经 LateBinding 由 bootstrap 实例化、注册为 Lifecycle 组件——关停 drain（先停订阅，再等运行中钩子收尾，超时才取消） |
| 技能后台评审 | `agent/skills/background_review.py`（`skill_review` 钩子） | event=after_reply + context=transcript + tool_tags=["skills"] + route_output=False + 6 轮上限；评审材料为完整 transcript + 四问框架 |
| 任务事件触发 | `agent/task/event_trigger.py` + `agent/heartbeat/engine.py::_sync_event_triggers` + `agent/task/tools.py` | 任务的第五种触发方式（与 heartbeat/scheduled/idle/manual 正交）：带 trigger_event 的任务经钩子面注册 `task_event:<name>` 钩子（context=none），命中后调 `HeartbeatEngine.run_task`（复用 inflight 去重与执行历史落盘）；任务 CRUD 经 reload 即时重建、无孤儿钩子 |
| 实体桥接 | `entities/_sdk.py::register_entity_llm_hook` | 实体经 _sdk 桥注册钩子（owner 缺省取实体模块名）；entities→agent 唯一豁免通道同 push_notify 模板 |
| Web 观测与配置 | `services/hooks_llm.py` + `web/routers/hooks_llm.py` + 前端设置页「LLM 钩子」Tab | 面板列出全部已注册钩子；治理参数经统一配置面 `hooks_llm/*` 组热调，Web 不另设写路径 |

## 七、修改思维系统前的检查顺序

1. 明确修改属于周期、回复循环、上下文、工具装配、心跳还是记忆边界
2. 先读对应组件及其测试，确认数据从哪里进入、在哪个层级变换、最终由谁持久化或投递
3. 检查 `_layer`、`_source`、scope 和工具版本是否会泄露到供应商或跨会话串线
4. 为新增行为补充最近组件的单元测试；涉及真实 LLM、频道或外部服务时再运行相应集成测试
5. 修改完成后检查上下文顺序、并发锁、取消收尾、错误结构和导入方向

不要把本页扩展成逐轮变更记录；需要记录一次验收结果时使用专门的报告或计划文件。
