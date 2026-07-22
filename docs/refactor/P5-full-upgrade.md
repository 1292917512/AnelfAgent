# P5 全量升级计划：Anelf 自发能力 × Claude Code 完备性

> 制定：2026-07-22。前置：P1-P4 + 权限统一已完成。
> 总原则（用户决策）：
> 1. **能力是智能体自发的** — Plan 模式由 Agent 自己决定进入/退出，流式是 Agent 的输出能力，不是用户开关
> 2. **多频道语义不变** — 消息收发逻辑同现状；富渲染是 webui 通道的增强，其他通道自然降级为文本
> 3. **过程性内容只活在对话/思维流里** — 工具块/流式文本/diff 是过程展示，不写入持久对话历史（Anelf 是记忆模式）
> 4. **沙箱长在现有工作区体系上** — 不引 seatbelt，用应用层路径策略 + 权限规则联动
> 5. **实体+标签体系融合** — 新能力注册为实体/标签，复用两级发现、沉睡激活、通道能力映射
> 6. **代码优雅、模块化** — 每层单一职责，便于后续开发

---

## Batch A：差距修补（已批准的 4 项）

### A1 路径规范化防权限绕过 🔴安全
- 新模块 `core/path_normalize.py`：`normalize_matchable_path(path, cwd)` → 绝对、normpath、`~` 展开
- `policy.py::extract_matchable_arg`：文件类工具的路径参数先规范化再匹配（接 shell_state 当前 cwd 解析相对路径）
- edit_file/write_file 等工具内 `_safe_path` 已做解析 → 权限层与执行层**用同一份解析结果**（权限匹配移到解析后，或两处共用函数）
- 测试：`./config/x`、`config/../config/x`、`~/x` 绕过尝试全部命中规则

### A2 空工具结果占位
- `result_pipeline.py`：空/纯空白结果 → `(工具名 执行完成，无输出)`（对齐 CC 防空结果诱发 stop 序列）

### A3 通用工具结果落盘 persisted-output
- `result_pipeline.py` 截断层改造：>50K 字符的结果不再硬截断，落盘 `workspace/.tool-results/`（复用 `shell_state.persist_output`/`truncate_or_persist`，泛化为 `core` 级或 filesystem 级公共函数）
- 返回 `<persisted-output>` 预览 2KB + 路径 + read_file 查看指引
- 图片/媒体结果永不落盘（对齐 CC）

### A4 run_in_background 后台执行
- `run_shell_command` 新增 `run_in_background: bool = False` 参数
- 复用 `agent/mind/background_tasks.py` 基建：注册后台任务，立即返回 task_id + 输出文件路径
- 完成通知：沿用现有后台任务完成注入机制（think_loop 挂起等待已支持）
- 输出文件：写入 `.tool-results/`，超 30K 部分按 A3 规则可见
- 超时转后台（可选参数 `auto_background_on_timeout`，默认 False，先不做自动）

---

## Batch B：自发能力层（Agent-native）

### B1 流式循环（内核流式，通道可订阅）
- think_loop 每轮 LLM 调用从 `chat()` 切换为 `chat_stream()`（不支持流式的 provider 自动回退）
- 增量聚合：text_delta 聚合为 assistant 文本；tool_call_delta 按 index 聚合（续轮信号 = tool_calls 非空，不信 stop_reason）
- **多频道语义不变**：send_message/end_reply 仍是回复出口；流式只产生**过程事件**
- 新事件（core/event_bus）：`EVENT_ASSISTANT_DELTA`、`EVENT_TOOL_CALL_START/DELTA/END`，携带 scope + turn_id
- 流式中途出错：丢弃部分输出走 chat_with_fallback 降级（已有）

### B2 token 级渲染（webui 通道）
- webui 通道订阅 `EVENT_ASSISTANT_DELTA` → SSE `delta` 事件（50ms 合帧节流）
- 前端 `StreamingBubble`：流式文本是消息数组的**尾随兄弟**（对齐 CC），send_message 落地时替换为正式气泡；**不写入历史**（符合记忆模式）
- 稳定前缀 markdown：已闭合块 memo，只重渲染尾部

### B3 内联工具块（过程展示，不落历史）
- webui SSE `tool_call` 事件（来自 B1 事件流）：前端在流式区下方渲染工具块序列
- 每工具一行式标题（`userFacingName` 映射表：edit_file(path)、run_shell_command(cmd)）+ 状态灯（脉冲/绿/红/灰）
- 连续 ≥3 只读工具折叠为 "读取了 N 个文件…"
- 思维流程页（Trace）保持现状，两者并存

### B4 diff 渲染
- edit_file 工具结果附加 metadata（经 thinking 事件 `data.diff`）：unified diff（P1 edit_utils.unified_diff 已有）
- 前端 `DiffView`：红绿增删 + 行号，复用 react-syntax-highlighter
- webui 工具块展开可见 diff；其他通道工具结果不变（文本摘要）

### B5 自发 Plan 模式（Agent 自己决定）
- Mind 元决策已有 PLAN 类型 — 强化为完整闭环：
  1. 复杂任务 → 元决策选 PLAN → agent 用思考轮产出**结构化计划**（步骤+涉及文件+风险）
  2. 计划经审批/确认机制呈现给用户（webui 弹窗 / IM 文本，复用统一权限的 ask 通道，`plan_approval` 规则默认 ask 可配置）
  3. 用户批准/修改/拒绝 → agent 按计划执行，计划注入 exec_context 作为当前工作大纲
  4. 计划完成或用户插话 → 自动退出
- 不是用户切换的模式，无 Plan 权限模式；全部发生在现有元决策+审批体系内
- 新实体分组 `planning_mode`（标签 `planning` 已有，复用）

### B6 工作区沙箱强化（应用层，非 seatbelt）
- `entities/filesystem/sandbox.py` 策略模块（从 tools.py 抽出 `_safe_path` 逻辑，模块化）
- shell 命令**预检**（启发式）：检测命令中的绝对路径写操作（`>`、`mv`、`rm`、`cp` 目标）漂出 workspace 时 → 要求权限规则放行（走统一权限 ask/deny 管线，`run_shell_command(*写路径*)` 模式提示）
- `python_exec` 同等 cwd 约束（默认 workspace）
- 配置：`sandbox_shell_write_check`（默认开）、与 `sandbox_enabled` 联动

### B7 NotebookEdit
- `entities/filesystem/notebook.py`：`notebook_edit(path, cell_index, new_source, cell_type?)` — cell 级替换/插入/删除
- read_file 对 .ipynb 分发：cell 列表摘要（索引+类型+前 5 行），edit_file 对 .ipynb 报错并引导 notebook_edit（对齐 CC）
- 注册 os 组，标签随 filesystem

### B8 上下文 % 状态
- compressor 暴露 `context_usage(scope) -> {tokens, threshold, percent}`（token 计数优化：usage 锚定 + 增量估算，见 C1）
- think_loop 每轮随 exec_context 事件发出 → webui 状态栏显示 "上下文 73%"
- 其他频道：仅在压缩触发时文本提示（现状已有）

---

## Batch C：架构与模块化

### C1 token 计数优化（usage 锚定）
- 对齐 CC `tokenCountWithEstimation`：上次 API usage（prompt_tokens）+ 其后消息增量估算
- think_loop 已有 `last_prompt_tokens` → 传入 compressor.should_compress（现状已是），扩展为 `estimate_incremental(messages, since_usage)` 供 B8 使用

### C2 工具结果管线分层（result_pipeline 重构）
- 现状 sanitize→threat scan→guardrail→truncate 已管线化；A3 落盘作为独立 stage 加入
- 每 stage 纯函数 + 配置，便于单测和扩展

### C3 事件契约集中
- `core/events.py`（或 event_bus 扩展）：流式/UI 事件名+payload schema 集中定义（TypedDict/dataclass），webui 通道与前端共享契约文档
- 前端 `src/lib/events.ts` 对应类型

### C4 实体 tag 融合
- webui 通道能力声明新增 `stream`/`rich_display` capability → 映射标签 `stream`、`rich:diff`
- B1/B3/B4 的事件发射检查订阅通道能力（无 webui 在线时零开销，不发事件）
- `activate_tool_group` 体系不变；planning_mode（B5）注册为可沉睡分组，PLAN 决策时激活

---

## 实施顺序与验收

| 序 | 内容 | 依赖 | 验收 |
|---|---|---|---|
| 1 | A1+A2（安全+小项） | 无 | 绕过测试全拦截；空结果有占位 |
| 2 | A3（通用落盘） | A 系列基建 | >50K 结果落盘可查，回归全绿 |
| 3 | A4（后台执行） | A3 | 后台任务返回 id、完成注入、输出可查 |
| 4 | C2+C3（管线/事件契约） | — | 契约集中，旧行为不变 |
| 5 | B1+B2（流式内核+渲染） | C3 | webui 见 token 流；IM 频道行为不变 |
| 6 | B3+B4（工具块+diff） | B1 | 工具块状态灯/折叠/diff 展示；不落历史 |
| 7 | B8+C1（上下文 %） | — | 状态栏显示准确 |
| 8 | B6（沙箱强化） | A1 | 漂出写操作被权限管线拦截 |
| 9 | B7（NotebookEdit） | — | cell 编辑/ipynb 分发正确 |
| 10 | B5（自发 Plan） | 权限 ask 通道 | 复杂任务自动出计划→确认→执行闭环 |
| 11 | 全量回归 + 优势验证清单 + 文档 | 全部 | pytest 全绿 + 前端构建过 |

**优势保留红线**（每步验证）：实体注册/标签注入/Mind 双层决策/多通道/混合记忆/心跳/技能/委派/三层缓存/ui_* 反向驱动/统一权限。

---

## 实施记录（2026-07-22 全部完成 · 868 测试全绿 + 前端构建通过）

| 项 | 交付 |
|---|---|
| A1 路径防绕过 | `entities/filesystem/paths.py`（执行层与权限层同一解析）；规则匹配双候选（绝对+相对）；`./`、`../`、`~` 绕过全部拦截 |
| A2 空结果占位 | result_pipeline stage 0：`(工具名 执行完成，无输出)` |
| A3 通用落盘 | result_pipeline >50K 落盘 `.tool-results/` + 预览 |
| A4 后台执行 | `shell_background.py`（Popen+等待线程）；`BackgroundTaskRegistry` 线程安全化（bind_loop + call_soon_threadsafe）；`run_in_background` 参数 |
| C2/C3 | 管线 5 stage 纯函数化；`core/stream_events.py` 事件契约集中（内核事件 + SSE 帧 TypedDict） |
| B1 流式内核 | `Mind._llm_chat_stream_once`（聚合+on_delta+wait_for 兼容 3.10）；`_invoke_llm_unified(stream=True)` 失败回退非流式；think_loop 签名探测兼容替身 Mind；配置 `mind_streaming_enabled` 默认开 |
| B2 token 渲染 | webui 订阅 delta（50ms 合帧）→ SSE → `StreamingArea` 尾随兄弟气泡（不落历史） |
| B3 内联工具块 | thinking_tool_start/end 转发 SSE；状态灯/耗时/结果展开/≥3 只读折叠；不落历史 |
| B4 diff 渲染 | edit_file 发 file_diff 事件（工作线程经注册表绑定循环桥回）；`DiffView` 红绿增删+行号 |
| B8+C1 上下文 % | usage 锚定（API 真实用量优先）→ context_usage 事件 → `ContextChip`（>70% 黄 >90% 红） |
| B6 沙箱强化 | `shell_guard.py` 启发式预检（写动词参数+重定向目标；/tmp、/dev/null 良性放行）；违规返回可操作中文错误；`sandbox_shell_write_check` 可关 |
| B7 NotebookEdit | `notebook.py`（replace/insert/delete cell）；read_file ipynb 摘要分发；edit_file ipynb 重定向（code 5） |
| B5 自发 Plan | `present_plan` 工具复用统一权限 ask 管线（零新决策管道）；默认规则 ask（timeout 300s）；webui 弹窗/IM 按钮/approve 路由全通；脱敏截断 200→2000 字符 |

**架构亮点**：present_plan 证明统一权限管线的复用价值 —— 用户确认流不需要任何新的决策管道；
file_diff/后台 shell 证明 bind_loop 线程桥是实体层发事件的通用模式。
