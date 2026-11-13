# 子代理委托与工作流引擎

`agent/delegation/`、`agent/workflow/` 与后台任务注册表的现状说明。修改委托、续跑、后台任务或
工作流编排时按需阅读。

委托管理器由 `Lifecycle` 持有：关停先封闭受理，取消并等待前台、后台、排队及嵌套调用，
然后关闭后台注册表和底层存储。后台注册的同步入口与关闭共享准入锁；不依赖具体频道业务。
父会话快照使用完整 scope，保留频道和子会话标识，不把不同窗口合成同一份上下文。
跨频道实时通知只注入主回复，内部反思与子代理不接收其他会话的新指令；需要背景时由调用方
显式传入 context/fork_context，运行期间的调整继续走 steer/after。

## 通用后台任务

`agent/mind/background/tasks.py` 负责按会话登记与等待；同目录 `routing.py` 处理完成通知，`evidence.py` 保存操作证据，生命周期由宿主管理。
查询、等待和终止工具统一放在 `agent/mind/tools/background.py`，不依赖子代理是否启用。
`wait_background_tasks(task_id, timeout)` 等待指定任务，超时只结束此次等待，不取消执行。
进度提醒会以 `alert` 原因唤醒等待；消息正文仍由原有通知路由写入所属会话，不启动并行主思维循环。
查询有无日志文件均返回同一套阶段、耗时、操作证据和提醒字段；UI 读取日志尾部不推进模型的增量游标。
新私聊或群聊 @ 到达会让等待返回，由 AI 在下一轮动态上下文中判断安排；不会自动改换回复目标。
指定等待仅领取对应任务，其余完成事实仍按各自通知策略路由；等待被打断时未领取的结果转为通知。
无独立工作时可用 `end_reply` 收束，后台完成后按原会话续接。状态摘要有界，完整结果按 ID 查询。
任务登记保留发起轮次 ID，完成后继续更新该轮的 Web 状态，不挂到后来开始的其他会话。

## 子代理档案体系

模型工具 `delegate_task`（含批量）与 `follow_up_agent` 从受理时就把执行交给管理器托管。
`background=true` 立即返回 ID；默认最多等待 30 秒，完成则返回结果，否则返回 `pending_ids` 和进度。
工具等待到期或被新输入打断不取消子任务；完成结果由等待者或后台通知领取，有界等待退出后释放认领。
工作流内部 `delegate()` 仍等待执行结束，工作流显式 deadline、轮次预算和服务关停保持原有约束。

`delegation_timeout_seconds` 保留原配置键，语义为获得并发槽后的总执行耗时提醒（默认 600 秒），
不再强制结束子代理。`delegation_stall_seconds` 控制无可观察活动提醒（默认 600 秒，0 关闭）；
相同活动水位的提醒合并，有新活动后可再次提醒。排队准入超时独立报告“尚未开始执行”。
`progress.py` 投影模型、工具及流式活动，主 AI 查询、动态尾部、SSE 和刷新快照共用同一份事实；
超时不变更执行终态，前端不根据本地计时判定失败。

主 AI 经 `terminate_background_task`、UI 经取消接口请求停止，均汇入管理器并级联后代。
运行中的 reflect 使用其精确执行中断信号，停止发起后续调用，待在途操作收尾后确认取消。
取消保留停止来源、部分操作回执和 transcript；续跑沿用既有消息链并创建新的委托 ID。
未执行的排队任务保留原始任务记录。transcript 仍遵循原有开关、大小上限、脱敏与保留期。

子代理默认不另设轮次上限：`delegation_default_iterations=0` 沿用主思维的
`max_tool_iterations`（默认 `0`，不限轮次），`delegation_max_iterations_cap=0` 关闭硬上限。
调用参数 `max_iterations` 或默认预算设为正数时限制本次执行；配置正数硬上限时，
未设预算的执行采用该上限，已设预算取两者较小值。续跑默认沿用上次调用的预算。
执行观测只报告实际约束的配置键；单次调用直接指定预算时明确其来源，修改全局默认不覆盖调用预算。

一套档案体系（llm_clients.json 顶层 `sub_agents` 键，schema 单一权威在 `agent/delegation/profile.py`，
存储宿主 `LLMManager._sub_agents`）：

- **模型面**：名称 → 有序模型候选池，前者不可用依次回退
- **执行面** AgentFacets：instructions 专职守则 / tool_tags reflect 工具选择器 / blocked_tools
  追加屏蔽 / output_schema 结构化产出契约——注入子代理 reflect 临时上下文，不进任何 stable 前缀层
- **内置难度档 easy/medium/hard（tier 1-3，受保护）就是 difficulty 1/2/3 的语法糖**，恒为纯模型池
  （难度语义只是换模型，不收执行面）；与自定义档案（tier 0）同构存储、同套 CRUD；解析优先级
  agent_name > difficulty > 默认，本档全不可用降挡
- AI 经 model_control 组 4 个工具增删改查（update 的 instructions/output_schema 传 "clear" 清除、
  tool_tags/blocked_tools 传空列表清除），Web 经 `/models/sub-agents`（模型页子代理面板），
  双路径同 LLMManager 内存态 + 原子落盘即热生效；legacy `delegation_tiers` 键加载时自动迁移
- output_schema 是提示词契约 + 产出宽容提取校验（`extract_json_output`：原文/剥围栏/平衡大括号
  三候选），schema_ok 事实报告进聚合结果——系统不替 AI 拒绝产出

## 指令双档（steer / after）与续跑

| 机制 | 位置 | 说明 |
|------|------|------|
| steer 档（默认） | `agent/delegation/steer.py` + `DelegationManager.steer` + `execution.messages._merge_steered_messages` + 工具 `send_to_agent` | 在**步骤边界**注入追加指令、改变进行中的工作 |
| after 档 | `execution.messages.merge_after_messages` + `think_loop` end_reply 分支 | 在**收束边界**注入（REFLECT 连续纯文本达上限本要结束时消费，重置计数续跑；end_reply 收束路径同样先消费 after 档——无追加走原收束）——「做完这批后顺便…」型追加不取消不重开、已完成部分保留 |
| 收件箱 | `SteerInbox`（按档位分离） | 单委托上限 8 条两档合并计、单条 4000 字符截断；SubAgent.run 经 `bind_steer_drain` ContextVar 绑定 mode 参数化 drain 闭包（create_task 复制进整个执行树），主会话未绑定 drain 恒空零开销；委托结束（成败/超时）finally 清箱 |
| 可续跑 | `agent/delegation/journal.py`（transcript）+ `DelegationManager.follow_up` + 工具 `follow_up_agent` | 委托结束把最终消息链（completion 容器 `messages` = base+tool_chain）持久化为 transcript（`<data_dir>/delegations/<id>.json`，256KB 上限超出降级为不可续跑档案），`follow_up_agent(delegation_id, message)` 以消息链为 base_messages 追加 [续跑指令] 无损续跑（前台/后台两路，血缘 parent_delegation_id 贯通；运行中委托拒绝并引导 send_to_agent） |
| 结束原因贯通 | `think_loop completion 容器` → `mind.reflect(completion=)` → `SubAgentResult.completed_reason` | 三值：completed / budget_exhausted（轮次预算用尽，产出可能只是中途状态）/ interrupted；聚合结果对 budget_exhausted 条目附 hint（拆小任务重新委托），后台完成通知同样标注；空产出 no_output |

## 运行日志与可观测性

| 机制 | 位置 | 说明 |
|------|------|------|
| 进度流 | journal `<id>.log` + `attach_output_file` 接入注册表 | 轮次/工具事件行追加；`check_background_tasks(task_id=...)` 单游标增量管线立即可读子代理中间进展（与后台 shell 同构：每次只返回新增输出，轮询长任务不再全量重读） |
| 用量归集 | EVENT_THINKING_LLM_END 按 ContextVar 归属 delegation_id 分桶 | turns/input/output/duration 随 SubAgentResult.usage 进聚合结果与 resolved 事件——父 AI 可判断「烧了 30 轮才出这点结论，该拆任务了」 |
| 崩溃账本 | ledger.jsonl started/closed 各一行 + bootstrap recover_interrupted 节点 | 扫未闭合条目 → 按归属会话聚合注入「后台委托被进程中断」元消息（at-most-once：扫描即闭合；同 scope 一条防轰炸）；retention 滚动清理（`delegation_journal_retention_days` 默认 7 天）；`delegation_transcript_enabled` 可关 |
| 委派与等待分离 | `dispatch.py` + `DelegationManager` + `BackgroundTaskRegistry.reserve_results` | 模型工具只做有界等待，任务由管理器持有；等待退出后未领取的结果恢复通知。内部阻塞调用仍以 `complete(claimed=True)` 收尾，工作流控制不变。归属按 usage_scope 绑定 > 激活上下文解析 |
| 日志 actor 归因 | `core/log.py`（`bind_log_actor` ContextVar）+ DelegationManager 绑定 | 委托在 `bind_delegation_id` 同位绑定 `[子代理@{agent|role}#id尾6位]` 前缀，console/文件/环形缓冲/监听器全链路一致；嵌套委托内层覆盖外层（归因到最内层执行者） |
| 全局总览与面板操作 | `DelegationManager.running_snapshot_all` + `journal.recent_history` + `services/delegation.py` + `web/routers/delegation.py` + 前端 `components/delegation/DelegationsPanel.tsx` | 全 scope 运行快照（归属/实时进度/用量）；历史由账本 started/closed 配对折叠。面板操作汇入既有闭环：**指令**（steer/after 双档，消息标注「来自 Web 面板」）、**停止**（cancel 级联）、**进度**（Drawer 轮询进度流尾部） |
| 子代理独立思维会话 | `SubAgent.run`（`thinking_session(is_delegation=...)` 包裹 reflect）+ `core/tracer.py` | 思维链路页区分主 AI 与子代理（并发委托共享父会话曾致 round/llm 节点配对错配与孤儿节点；独立会话后配对状态天然隔离，嵌套委托各自再开） |

## 工作流引擎（agent/workflow/）

步骤输入绑定由 `contracts.py` 管理：完整 SHA-256 覆盖派发输入、gate、显式依赖和
`continue_from` 隐式依赖。依赖同时绑定输入、完整结果摘要与原始执行身份；修订导入保留原始身份，
实际重跑则产生新证据，即使回执文本相同也会使下游重新验证。绑定不注入模型，依赖背景仍按
`workflow_upstream_context_chars` 有界呈现，不改变实时消息并入、跨频道感知和稳定缓存前缀。

工具准入与派发分离：排队或审批期间为 `pending`，通过权限检查后先以 WAL/FULL 提交
`dispatched` 再调用工具。回执丢失的调用仅在原调用和当前工具都声明 `replay_safe=True` 时重放；
该声明由工具实现者通过 `tool`/`deferred_tool` 元数据提供，表示只读或具有幂等保障，与
`concurrency_safe` 独立，工作流规格不能自行授予。其他未知结果保留为失败证据，续跑或连续修订
均不会自动重派相同调用；须先核验外部状态再决定后续操作。ask 仍复用委托的既有停止与续跑逻辑。

journal 保存完整的非敏感回执，模型背景、运行概要和详情预览分别限长。敏感工具结果仅在当前
运行中传递，持久化使用占位且标记为不完整；不以占位或历史截断文本复验门控、冒充完整结果或
触发隐式重跑。gate 区分字段缺失与 null，并递归区分布尔值和数字，JSON 数字保持数值比较。
旧库增量迁移保留记录；旧输入绑定只在源运行未经过修订导入、原规格及完整依赖回执齐全时补算。
旧格式修订缺少原始执行身份，不能从当前依赖图反推旧验证有效，也不能隐式重派相同调用。

声明式 DAG + journal 化断点续跑：

并行容量同时约束 ask、tool 和 repair。工具仅在实体声明 `concurrency_safe` 时与同层安全步骤
并行；其余工具形成顺序边界，前序失败不再启动后续边界。TaskGroup 持有每组子任务，框架异常
或关停先回收子任务再发布终态。步骤超时覆盖排队、权限检查和执行；尚未执行与结果未知分别报告，
未知效果不自动重试。显式 `context` 与依赖结果一起进入委托及输入指纹，续聊也传入当前背景。

| 机制 | 位置 | 说明 |
|------|------|------|
| 工作流规格 | `agent/workflow/spec.py` | 步骤 kind=ask（goal/agent_name/continue_from 续聊）/ tool（tool/args/gate 门控）；校验一次性收集全部错误（key 唯一、依赖已知、无环、gate 仅 tool、continue_from 指向 ask 且构成隐式依赖边）；canonical JSON + sha256 指纹 |
| 可复用定义 | `workflow_definition` + `WorkflowEngine.save_definition` | 保存时统一规格校验，不执行；可编辑、删除、分页查询，通过 `workflow_id` 重复启动。每次运行保存独立规格快照，后续编辑/删除定义不改变已有运行；定义不受运行历史 retention 影响 |
| journal | `agent/workflow/journal.py`（`<data_dir>/workflow.sqlite3`，WAL） | 断点恢复唯一事实源：run（**终态一笔写**——status 与 stop_reason/failure/result/finished_at 同笔 UPDATE）、node（step_key×ordinal，**准入先落 running**；latest_nodes 为重放事实源）、event（写锁内 max+1 单调序；run-settled 必为最后一条），同库独立保存 definition。retention 在启动时按天清理终态 run |
| 编排引擎 | `engine.py::WorkflowEngine`（Mind 持有，经 `workflow_engine_port` 施绑） | graphlib 分层 + 层内并发（workflow_max_parallel_steps 闸门）；同 run completed 按输入与完整回执缓存结算，running 按派发事实恢复，failed 复现；修订只检查源运行每步最新尝试，不用旧成功覆盖后续失败或未知结果。gate 不满足时按有界轮次委托修复再执行，各轮独立 ordinal。用户停止结算 stopped(user)，保留派发事实；ask 复用 DelegationManager，tool 复用统一审批门与 EntityRegistry.execute_tool |
| 管理与可见性 | BackgroundTaskRegistry 登记（kind=workflow，killer→stop）+ AI 工具组 `workflow` + `services/workflow.py` + `/api/workflow/definitions*`、`/api/workflow/runs*` + 前端「工作流」页 | Web 分定义与运行两页签；定义可增删改查、重复启动，历史可另存为定义。AI 经 `workflow_save/definitions` 保存与查询定义，`workflow_start` 接受 spec 或 workflow_id 二选一；`workflow_status/stop/resume` 管理运行，`workflow_delete` 删除定义或运行，`workflow_cleanup` 按天清理历史。完成通知自动路由（轮内会合/轮外新 REPLY），启动返回 run_id；列表支持 limit/offset 分页 |
| 删除与清理 | `WorkflowEngine.delete_run/cleanup_runs` + journal 事务 | 与 start/resume 共用生命周期锁；单次删除及清理在一个事务中删除 run/node/event，失败回滚。执行或结算中的运行不可删除；保留运行中或仍需续跑的修订所依赖的源记录，自动 retention 同样遵循保护规则。手动清理默认保留 stopped，include_stopped 才包含，days=0 清理全部符合条件的历史。删除运行后不能续跑或导入其结果；已启动修订的独立规格快照不变 |
| 崩溃收敛 | `agent/workflow/recovery.py`（bootstrap recover_interrupted 节点） | running 残留 → stopped(interrupted)（不合成步骤结局、不重派）；**只收敛上一进程遗留**——跳过引擎在飞表中的 run 与引擎构造之后创建的 run；start/resume 开账-派发段持 `_lifecycle_lock`；归属会话注入中断元消息引导 workflow_resume（at-most-once） |
