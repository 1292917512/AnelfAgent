# 子代理委托与工作流引擎

`agent/delegation/`、`agent/workflow/` 与后台任务注册表的现状说明。修改委托、续跑、后台任务或
工作流编排时按需阅读。

## 子代理档案体系

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
| steer 档（默认） | `agent/delegation/steer.py` + `DelegationManager.steer` + `round_helpers._merge_steered_messages` + 工具 `send_to_agent` | 在**步骤边界**注入追加指令、改变进行中的工作 |
| after 档 | `round_helpers.merge_after_messages` + `think_loop` end_reply 分支 | 在**收束边界**注入（REFLECT 连续纯文本达上限本要结束时消费，重置计数续跑；end_reply 收束路径同样先消费 after 档——无追加走原收束）——「做完这批后顺便…」型追加不取消不重开、已完成部分保留 |
| 收件箱 | `SteerInbox`（按档位分离） | 单委托上限 8 条两档合并计、单条 4000 字符截断；SubAgent.run 经 `bind_steer_drain` ContextVar 绑定 mode 参数化 drain 闭包（create_task 复制进整个执行树），主会话未绑定 drain 恒空零开销；委托结束（成败/超时）finally 清箱 |
| 可续跑 | `agent/delegation/journal.py`（transcript）+ `DelegationManager.follow_up` + 工具 `follow_up_agent` | 委托结束把最终消息链（completion 容器 `messages` = base+tool_chain）持久化为 transcript（`<data_dir>/delegations/<id>.json`，256KB 上限超出降级为不可续跑档案），`follow_up_agent(delegation_id, message)` 以消息链为 base_messages 追加 [续跑指令] 无损续跑（前台/后台两路，血缘 parent_delegation_id 贯通；运行中委托拒绝并引导 send_to_agent） |
| 结束原因贯通 | `think_loop completion 容器` → `mind.reflect(completion=)` → `SubAgentResult.completed_reason` | 三值：completed / budget_exhausted（轮次预算用尽，产出可能只是中途状态）/ interrupted；聚合结果对 budget_exhausted 条目附 hint（拆小任务重新委托），后台完成通知同样标注；空产出 no_output |

## 运行日志与可观测性

| 机制 | 位置 | 说明 |
|------|------|------|
| 进度流 | journal `<id>.log` + `attach_output_file` 接入注册表 | 轮次/工具事件行追加；`check_background_tasks(task_id=...)` 单游标增量管线立即可读子代理中间进展（与后台 shell 同构：每次只返回新增输出，轮询长任务不再全量重读） |
| 用量归集 | EVENT_THINKING_LLM_END 按 ContextVar 归属 delegation_id 分桶 | turns/input/output/duration 随 SubAgentResult.usage 进聚合结果与 resolved 事件——父 AI 可判断「烧了 30 轮才出这点结论，该拆任务了」 |
| 崩溃账本 | ledger.jsonl started/closed 各一行 + bootstrap recover_interrupted 节点 | 扫未闭合条目 → 按归属会话聚合注入「后台委托被进程中断」元消息（at-most-once：扫描即闭合；同 scope 一条防轰炸）；retention 滚动清理（`delegation_journal_retention_days` 默认 7 天）；`delegation_transcript_enabled` 可关 |
| 前台委托注册表化 | `delegate()` registry 登记 + killer + `complete(claimed=True)` | 前台/嵌套委托同样登记 BackgroundTaskRegistry：check_background_tasks 可见（含耗时）、terminate_background_task 可单独停止（killer 走 _cancel_marks + 桥回主循环 cancel，转「用户取消」结果返回父级）；完成走 `complete(..., claimed=True)`（结果已被工具返回值消费，跳过轮外完成回调防双投递）。归属登记从 `_global` 改为 `_owner_scope`（usage_scope 绑定 > 激活上下文）——嵌套委托用量归属父会话 |
| 日志 actor 归因 | `core/log.py`（`bind_log_actor` ContextVar）+ DelegationManager 绑定 | 委托在 `bind_delegation_id` 同位绑定 `[子代理@{agent|role}#id尾6位]` 前缀，console/文件/环形缓冲/监听器全链路一致；嵌套委托内层覆盖外层（归因到最内层执行者） |
| 全局总览与面板操作 | `DelegationManager.running_snapshot_all` + `journal.recent_history` + `services/delegation.py` + `web/routers/delegation.py` + 前端 `pages/dashboard/DelegationsPanel.tsx` | 全 scope 运行快照（归属/实时进度/用量）；历史由账本 started/closed 配对折叠。面板操作汇入既有闭环：**指令**（steer/after 双档，消息标注「来自 Web 面板」）、**停止**（cancel 级联）、**进度**（Drawer 轮询进度流尾部） |
| 子代理独立思维会话 | `SubAgent.run`（`thinking_session(is_delegation=...)` 包裹 reflect）+ `core/tracer.py` | 思维链路页区分主 AI 与子代理（并发委托共享父会话曾致 round/llm 节点配对错配与孤儿节点；独立会话后配对状态天然隔离，嵌套委托各自再开） |

## 工作流引擎（agent/workflow/）

声明式 DAG + journal 化断点续跑：

| 机制 | 位置 | 说明 |
|------|------|------|
| 工作流规格 | `agent/workflow/spec.py` | 步骤 kind=ask（goal/agent_name/continue_from 续聊）/ tool（tool/args/gate 门控）；校验一次性收集全部错误（key 唯一、依赖已知、无环、gate 仅 tool、continue_from 指向 ask 且构成隐式依赖边）；canonical JSON + sha256 指纹 |
| journal | `agent/workflow/journal.py`（`<data_dir>/workflow.sqlite3`，WAL） | 断点恢复唯一事实源，三表：run（**终态一笔写**——status 与 stop_reason/failure/result/finished_at 同笔 UPDATE）、node（step_key×ordinal，**准入先落 running**；latest_nodes 为重放事实源）、event（写锁内 max+1 单调序；run-settled 必为最后一条）。retention 按天清理终态 run |
| 编排引擎 | `engine.py::WorkflowEngine`（Mind 持有，经 `workflow_engine_port` 施绑） | graphlib 分层 + 层内并发（workflow_max_parallel_steps 闸门）；**缓存结算**——同 run 续跑时 completed 行经 input_hash 防御性比对后直接复用（不重付费）、running 行重派、failed 复现；**修订导入**——start(resume_of=父) 把父 run 输入一致的 completed 成果入账，规格分歧自然级联重跑；**门控重跑**——tool 步 gate 不满足时先委托子代理按失败上下文修复（独立 repair 节点行）再重跑（有界轮次）；**取消二分**——用户停止转化为步骤 cancelled 结局（节点行保持 running 供续跑重派）、run 结算 stopped(user) 可续跑。执行面零新机制：ask=DelegationManager.delegate（预登记 delegation_id 供停止级联与续聊 transcript）、tool=统一审批门（channel=None 的 Guardian 路径）+ EntityRegistry.execute_tool |
| 可见性 | BackgroundTaskRegistry 登记（kind=workflow，killer→stop）+ AI 工具组 `workflow`（workflow_start/status/stop/resume）+ `services/workflow.py` + `/api/workflow/runs*` + 前端「工作流」页 | 完成通知自动路由（轮内会合/轮外新 REPLY）；启动工具返回 run_id 供续跑 |
| 崩溃收敛 | `agent/workflow/recovery.py`（bootstrap recover_interrupted 节点） | running 残留 → stopped(interrupted)（不合成步骤结局、不重派）；**只收敛上一进程遗留**——跳过引擎在飞表中的 run 与引擎构造之后创建的 run；start/resume 开账-派发段持 `_lifecycle_lock`；归属会话注入中断元消息引导 workflow_resume（at-most-once） |
