# Anelf × Claude Code 对标重构 · 总计划（Master Plan）

> 目标：充分吸收 Claude Code 源码的成熟设计，系统性改进 Anelf 智能体，**对话窗口能力比肩 Claude Code**，同时**完整保留 Anelf 独有优势**。
> 依据：`00-anelf-baseline.md`（Anelf 基线）、`01-claudecode-tools.md`（工具/编辑）、`02-claudecode-loop.md`（循环/上下文）、`03-claudecode-ui.md`（对话窗口）。

---

## 一、总体原则

1. **架构范式不盲换**：Anelf 是「多通道自主智能体」（IM 通道 + 心跳 + 元决策），Claude Code 是「单终端编程助手」。Anelf 的**纯工具模式**（回复走 `send_message`/`end_reply`）是多通道场景的根基 —— 保留。Claude Code 的**流式文本直接输出**只作为一种新的「通道输出模式」引入 WebUI/CLI 通道。
2. **机制移植，不是代码移植**：Claude Code 是 TS/终端，Anelf 是 Python/Web。移植的是算法与交互设计（编辑归一化、并发分级、压缩策略、流式渲染）。
3. **每个子计划实施前做深读**：先读 Claude Code 对应源码原文，再读 Anelf 对应实现，再动手。
4. **每一步都可回归**：改动配测试（pytest + 前端测试），遵守 AGENTS.md（严格类型注解、最小 diff、中文注释）。

---

## 〇、用户决策（2026-07-22，优先级高于初始计划）

1. **执行与 Agent 本身 > 本地代码编辑 > 对话窗口**。工具层（P1）先行。
2. **流式循环属于「通道接口功能」**：不改变核心 think_loop 范式；流式输出只作为通道（如 webui）的可选输出能力实现。P2 任务 1 相应缩窄为「流式事件输出通道」，不动循环骨架。
3. **Anelf 是记忆模式（类人），不是对话窗口模式**：对话窗口条目受限是设计使然。P3 降级为「完善既有机制」（内联工具块/审批弹窗/加载行等轻量增强），不做大规模对标重做。

---

## 二、功能一一对比矩阵

### A. 工具层

| # | Claude Code | Anelf 现状 | 判定 | 阶段 |
|---|---|---|---|---|
| A1 | Edit：精确替换+弯引号容忍+引号风格保持+行尾往返+唯一性可操作报错 | 无（仅 write_file 全文覆盖） | **移植，新增 `edit_file`** | P1 |
| A2 | Read-before-write + mtime/内容过期检查（readFileState） | 无 | **移植** `ReadFileStateCache` | P1 |
| A3 | Read：行号输出/offset/limit/256KB+25K token 上限/读重去重/图片块 | read_file 基础版 | **增强** | P1 |
| A4 | Write：新文件直写、已存在强制 read-first、LF 逐字 | write_file 无防护 | **增强**（接 A2） | P1 |
| A5 | Bash：stateless 进程+cwd 持久（pwd 尾部文件）/30K 输出截断+落盘/超时转后台/默认 2min 上限 10min | run_shell_command 基础版 | **对齐** | P1 |
| A6 | 工具并发分级：isConcurrencySafe(input) 分区，并行≤10，写串行 | 全量 asyncio.gather | **移植** | P1 |
| A7 | 工具结果 >50K 落盘 + `<persisted-output>` 预览；空结果占位文案 | result_budget 会话预算截断 | **对齐增强** | P2 |
| A8 | 工具长 prompt（prompt.ts 行为约束文案） | docstring 直出 | **补齐关键工具** | P1 |
| A9 | Glob/Grep 专用搜索工具 | search_files（基础） | 增强 search_files | P1 |
| A10 | 权限管线：deny→ask→工具自检→safety→模式→allow 规则，带 decisionReason，规则如 `Bash(npm test:*)` 可持久化 | ApprovalGate：glob 策略+风险等级+通道轮询 | **合并**：保留通道化审批，补规则匹配/不再询问 | P1 |
| A11 | TodoWrite/Task 工具 + 10 轮 nag 提醒注入 | planning goal CRUD（常驻情况待查） | 保留 Anelf 规划，引入 nag 注入模式 | P2 |
| A12 | Agent 子代理工具 | delegate_task（更强：并行/后台/深度限制） | **Anelf 优势，保留** | — |
| A13 | WebFetch/WebSearch | web_fetch/web_search 等 4 个（更强） | **Anelf 优势，保留** | — |
| A14 | AskUserQuestion 工具 | ui_ask（阻塞模态，更强） | **Anelf 优势，保留** | — |
| A15 | Skill 工具 | 技能自学习全套（更强） | **Anelf 优势，保留** | — |
| A16 | NotebookEdit | 无 | 不移植（低优先） |  backlog |

### B. 对话循环与上下文

| # | Claude Code | Anelf 现状 | 判定 | 阶段 |
|---|---|---|---|---|
| B1 | 流式驱动循环（text+tool_use 增量收集） | think_loop 非流式 chat() | **切换流式**（`chat_stream` 已有） | P2 |
| B2 | tool_use/tool_result 配对铁律：所有退出路径合成孤儿错误结果 | 部分（需审计） | **审计+补齐** | P2 |
| B3 | 413→反应式压缩重试（单次守卫）；max_output_tokens→升级 64K→注入续写（≤3 次） | resilience 分类恢复（有雏形） | **对齐** | P2 |
| B4 | 429/529 指数退避×10、连续 529×3 切 fallback 模型、剥离 thinking 签名 | chat_with_fallback（多客户端降级，更强） | **保留 Anelf**，补退避参数 | P2 |
| B5 | Microcompact：时间触发清理旧只读工具结果为占位符 | 无（只有全量压缩） | **移植** | P2 |
| B6 | Auto-compact：阈值=窗口−13K，3 次失败熔断，9 段式摘要 prompt，压缩后 rehydration（重读≤5 文件） | context_compressor：75% 阈值，保首尾+中间摘要，用户消息逐字保留（更强） | **合并**：保留 Anelf 算法，补熔断/rehydration/工具结果清理 | P2 |
| B7 | system-reminder attachments：每轮状态注入（todo nag/排队消息/文件变更） | exec_context 状态消息 + 轮中消息合并（更强） | **保留 Anelf**，补 nag 模式 | P2 |
| B8 | System prompt 分层缓存（静态/动态分界，≤4 cache_control 块） | 三层 prompt 缓存（stable/context/volatile，等价且更细） | **Anelf 优势，保留** | — |
| B9 | 中断语义：interrupt（提交新消息）vs abort（Esc）区分 | 打断关键词 + 消息合并 | **对齐语义** | P2 |
| B10 | 轮中 drain：排队消息转为当前轮上下文 | 有（消息合并） | **保留** | — |
| B11 | 元决策（decide 工具） | 无此概念（CC 是单轮反应式） | **Anelf 优势，保留** | — |

### C. 对话窗口（WebUI 对标终端 UI）

| # | Claude Code | Anelf 现状 | 判定 | 阶段 |
|---|---|---|---|---|
| C1 | **token 级流式渲染**（尾随兄弟+稳定前缀增量 markdown） | 整消息推送 + 三点动画 | **移植（核心目标）** | P3 |
| C2 | **内联工具调用块**：工具自带一行式调用渲染 + 状态灯（闪烁/灰/绿/红）+ ⎿ 结果缩进 | 工具调用只在 Thinking 页 | **移植** | P3 |
| C3 | **Diff 渲染**（语法高亮+行号） | 无（有 CodeMirror 编辑器） | **移植** | P3 |
| C4 | 折叠组："Read 3 files…" 连续只读调用折叠 | 无 | 移植 | P3 |
| C5 | 加载行：动词+计时+token 计数+卡死变红 | sending 三点 | **移植** | P3 |
| C6 | 权限对话框：工具专用（命令预览/diff）+ "不再询问"规则持久化 + 批准前改输入 | ui_ask 通用模态 | **专门化**（保留 ui_ask） | P3 |
| C7 | 排队消息显示（忙时输入排队可见） | 无 | 移植 | P3 |
| C8 | todo/任务固定面板 + footer pill | Tasks 独立页 | **内联到对话窗口** | P3 |
| C9 | 状态栏：模型/上下文剩余 %/耗时 | 无 | 移植 | P3 |
| C10 | 输入框增强：历史（上下键+搜索）、粘贴大文本芯片、@文件提及、多行 | 基础输入+文件上传 | 增强 | P3 |
| C11 | markdown/代码高亮/表格 | 已有（react-markdown+syntax-highlighter） | **保留** | — |
| C12 | AI 反向驱动 UI（ui_* 工具） | 无此概念 | **Anelf 优势，保留** | — |
| C13 | 三栏工作台（文件树/编辑器/Dock） | 无此概念 | **Anelf 优势，保留** | — |

### D. 明确不移植

- Ink 终端渲染层（Anelf 是 Web）、feature-gated 内部功能（BUDDY/KAIROS/coordinator/swarm）
- Claude Code 的纯文本回复范式（与 Anelf 多通道冲突）
- NotebookEdit（低价值）

---

## 三、阶段路线（子计划索引）

| 阶段 | 子计划文档 | 内容 | 状态 |
|---|---|---|---|
| **P1** 工具层对标 | `P1-tools.md` | A1–A10：edit_file 全套、ReadFileState、Read/Write/Bash 增强、并发分级、权限规则、工具 prompt | ✅ 2026-07-22 完成（764 测试全绿） |
| **P1.5** 权限体系统一 | 见下方专项记录 | 三套白名单合并为单一 PermissionRule 引擎 + 频道规则 + SSE 审批弹窗 + 规则编辑器 | ✅ 2026-07-22 完成 |
| **P2** 循环与上下文对标 | `P2-loop.md` | 配对铁律、max_tokens 续写、microcompact、压缩熔断、rehydration、goal nag | ✅ 2026-07-22 完成（805 测试全绿；流式按用户决策归通道能力，未动循环骨架） |
| **P3** 对话窗口（轻量版） | `P3-ui.md` | SSE 审批弹窗、活动加载行（动词+计时+工具活动）、排队消息气泡 | ✅ 2026-07-22 完成（前端构建通过；按用户决策不做大规模对标重做） |
| **P4** 回归与打磨 | `P4-regression.md` | 优势保留验证、全量测试、文档收尾 | ✅ 2026-07-22 完成 |

## 三之一、P1.5 权限体系统一专项（用户追加需求）

**背景**：盘点发现 15 套权限/放行/门控机制并存（ApprovalGate、三套白名单、ToolGate、频道准入等），且存在「被拒绝但看不到原因」的黑洞（QQ 白名单静默丢弃、黑名单拒绝无通知、ToolGate 隐藏工具零解释），频道审批回复实际无人解析（`parse_approval_command` 孤儿）、Web 审批靠 2 秒轮询。

**统一方案**（单一引擎 + 单一文件 + 单一弹窗）：
- `agent/approval/rules.py`：`PermissionRule{pattern(可带参数glob), effect(allow/ask/deny), scope(global/频道id), users, risk, timeout}` + `PermissionRuleSet.evaluate` 六段求值（用户deny → 频道deny → 全局deny → 用户allow → 频道ask → 全局ask → 频道allow → 全局allow → 默认），每个决策产出带命中规则的 Verdict
- 存储 `config/permission_rules.json`；旧 `approval_policies.json` 自动转换（热重载保留）
- `gate.py` 重写：auto_deny 主动通知用户原因；ask 超时/拒绝后回执；`remember=session/always` 三档放行（对齐 Claude Code "不再询问"）
- 频道内 `approve/deny <id>`（含 Telegram 按钮 `approve:<id>`）经 `agent_app` 路由真正生效
- WebUI 频道审批 → SSE `approval_request` 事件 → 全局 `ApprovalDialog` 弹窗（倒计时/风险徽标/三档放行/拒绝附言）
- 规则配置页 `PermissionRulesEditor`（求值顺序说明、会话规则标注）；历史记录显示命中规则
- 死代码清理：RoutePolicy（从未调用）、telegram group_access.py；QQ 白名单拦截补 WARNING 日志
- **顺带修复一个休眠真实 bug**：`_wait_for_decision` 先查 status 后查 decision，导致频道/Web 决策永远落入超时分支

## 四、Anelf 优势保留清单（P4 回归验证用）

1. 实体注册系统 + 两级能力发现
2. 标签驱动工具注入（always/core/media:image/…）
3. Mind 双层决策（元决策 + 执行决策）+ 纯工具模式
4. 多通道（cli/telegram/qq/feishu/webui/http_api）+ 监督看门狗 + 通道化审批
5. 混合语义记忆（FTS5+embedding）+ Cognee + 笔记蒸馏
6. 心跳自主任务 / 规划 / 技能自学习 / 子代理委派
7. 会话 token 防注入 + 威胁扫描
8. 三层 prompt 缓存
9. UI 反向驱动（ui_* 工具）
10. 多模型 fallback + 轮中消息合并
