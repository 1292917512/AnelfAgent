# P3 子计划：对话窗口对标（核心目标 —— 比肩 Claude Code）

> 对应总计划矩阵 C1–C10。源码依据：`03-claudecode-ui.md`。
> 前置：P2 完成（流式事件源）。前端：`web/frontend/src`（React 18 + zustand + Tailwind 4）。
> 原则：移植 Claude Code 的**信息架构与交互**，保留 Anelf 三栏工作台与 ui_* 反向驱动优势。

## 任务 1：SSE 流式通道扩展（C1 后端）

**深读**：Anelf `web/routers/chat.py`（/chat/stream）、`channels/webui/adapter.py`、`core/tracer.py`

**实现**：
- `/chat/stream` 新增事件类型：
  - `delta`（回复 token 增量，来自 P2 的 reply_delta）
  - `tool_call_start / tool_call_delta / tool_call_end`（内联工具块）
  - `thinking_delta`（推理流，可选展示）
  - `status`（动词/计时/token 计数/上下文 %）
  - `queue_update`（排队消息）、`approval_request`（对接任务 5）
- 事件携带 `turn_id` + `message_id`（确定性生成，保证前端 React key 稳定，对齐 CC 的 deriveUUID）

## 任务 2：流式消息渲染（C1 前端，本阶段核心）

**深读**：`Claude-Code/src/components/Markdown.tsx`（StreamingMarkdown 稳定前缀）；Anelf `stores/chat-store.ts`、`pages/Chat.tsx`、`pages/chat/render/Markdown.tsx`

**实现**：
- chat-store：`streamingMessage` 状态（当前轮的增量文本，**独立于消息数组**，对齐 CC"尾随兄弟"设计），delta 到达追加；`reply` 事件到达时落盘为正式消息
- **稳定前缀增量 markdown**：已闭合的 markdown 块 memo 化不重渲染，只重解析尾部未完成块（React：`StreamingMarkdown` 组件，按块边界切分，稳定段用 `React.memo`）
- 打字机式渲染 + 自动吸底滚动（用户上翻则暂停吸底）

## 任务 3：内联工具调用块（C2、C4）

**深读**：`Claude-Code/src/components/messages/AssistantToolUseMessage.tsx`、`ToolUseLoader.tsx`、`MessageResponse.tsx`；Anelf `components/thinking/ToolsPanel.tsx`（已有工具展示逻辑可复用样式）

**实现**：
- 新组件 `components/chat/ToolCallBlock.tsx`：
  - 一行式调用标题（每工具映射 `userFacingName(input)`：如 `run_shell_command(npm test)`、`edit_file(path)`）
  - **状态灯**：进行中=脉冲动画，成功=绿点，失败=红点，被拒=灰
  - 点击展开结果（⎿ 缩进语义用左边框实现）；超长结果截断+"展开全部"
- **折叠组**：连续 ≥3 个只读工具（read_file/search）折叠为 "读取了 3 个文件…"，可展开
- 工具块按时间序内联在消息流中（assistant 文本与工具块交错，对齐 CC 的消息模型）

## 任务 4：Diff 渲染（C3）

**实现**：
- P1 的 edit_file 结果 metadata 已带 unified diff → 前端 `DiffView.tsx`：
  - 语法高亮（复用 react-syntax-highlighter）+ 行号排水沟 + 红绿增删行
  - edit_file 工具块默认展开 diff 预览（前 N 行），写文件显示 "新增文件 +N 行"

## 任务 5：权限对话框（C6）

**深读**：`Claude-Code/src/components/permissions/{PermissionRequest.tsx,PermissionDialog.tsx}`；Anelf `agent/approval/`、`UiCommandHost`、`ui_ask`

**实现**：
- 审批事件走 SSE `approval_request` → 前端 `ApprovalDialog.tsx`：
  - 工具专用正文：shell→命令预览；edit_file→**DiffView**；write_file→路径+行数；其他→参数 JSON
  - 选项：允许 / **本次会话不再询问** / **永久不再询问**（写策略，P1 任务 6 已支持）/ 拒绝并附言
  - （backlog）批准前修改输入
- 保留 `ui_ask` 通用模态不变；审批走独立通道（webui 通道的 render_approval_prompt → SSE 而非聊天气泡）

## 任务 6：加载行与状态栏（C5、C9）

**实现**：
- `LoadingRow.tsx`：随机动词（中文动词表，如 "思考中/打磨中/编织中…"， shimmer 微光动画）+ 耗时计时 + token 计数（status 事件）+ 5 秒无新 token 变暗红
- 轮结束显示过去式（"工作了 12s"）
- 对话窗口标题栏/输入框上方状态条：当前模型、上下文剩余 %（compressor token 计数）、进行中后台任务数

## 任务 7：排队消息显示（C7）

**实现**：
- 忙时发送的消息进入队列态（灰色虚线气泡 + "排队中"标记），agent 消费后转为正式气泡
- 后端：AgentApp 入队/出队事件 → SSE `queue_update`

## 任务 8：Todo/Goal 面板内联（C8）

**实现**：
- 对话窗口右 Dock 增加常驻 "任务" 面板（复用 Tasks 页数据），有活跃 goal 时输入框上方显示 pill（"3 个目标 · 点击查看"）
- 对齐 CC：goal 工具调用时面板实时刷新

## 任务 9：输入框增强（C10）

**实现**：
- 历史：上下键翻阅本会话输入历史（localStorage 持久）
- 大段粘贴（>20 行）→ 折叠为芯片 `[粘贴的文本 #1 · +N 行]`，发送时展开
- @ 触发文件提及补全（workspace 文件树已有数据源）
- Shift+Enter 换行 / Enter 发送（现有行为确认保留）

## P3 验收标准

- [ ] 回复逐 token 流出，markdown 无闪烁重排（稳定前缀生效）
- [ ] 工具调用内联展示：状态灯、结果展开、diff 高亮、只读折叠组
- [ ] 审批弹窗工具专用化 + 不再询问生效
- [ ] 加载行（动词/计时/token）、状态条（模型/上下文 %）、排队气泡可见
- [ ] Thinking 页/Trace 面板功能不回归（内联块与它并存）
- [ ] 前端 `npm run build` 通过；关键组件有测试

---

## 实施记录（2026-07-22，轻量版 — 按用户决策：完善既有机制，不做大规模重做）

- [x] C6 权限对话框：SSE `approval_request` → 全局 `ApprovalDialog`（倒计时/风险徽标/允许一次·本会话·永久三档/拒绝附言），SSE 提升为 App 级启动
- [x] C5 加载行：`ActivityRow`（随机动词轮换 + 耗时计时 + 当前工具活动（thinking SSE 运行中节点）+ 8s 卡死变暗红），瞬时不落历史
- [x] C7 排队消息：忙时发送的用户消息虚线气泡 + "排队中"标记，agent 回复后自动转正式
- [x] 规则配置：`PermissionRulesEditor` 替代旧策略编辑器；历史显示 matched_rule
- [⏭️] C1 token 级流式 / C2 内联工具块 / C3 diff：按用户决策不实施（Anelf 记忆模式、条目受限；流式归通道能力 backlog）
