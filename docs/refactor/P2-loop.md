# P2 子计划：对话循环与上下文对标（流式 / 配对铁律 / 错误恢复 / 压缩增强）

> 对应总计划矩阵 B1–B9。源码依据：`02-claudecode-loop.md`。
> 前置：P1 完成（工具结果落盘、并发分级在此被消费）。
> **红线**：改动 think_loop 必须保持多通道语义（send_message/end_reply 纯工具模式）不变。

## 任务 1：流式 think_loop（B1，本阶段核心）

**深读**：`Claude-Code/src/query.ts:654-863`（流式消费与 tool_use 提取）；Anelf `agent/llm/llm_client.py:952 chat_stream`、`agent/mind/tools/think_loop.py:472`

**实现**：
- think_loop 每轮迭代从 `chat()` 切换为 `chat_stream()`，累积：
  - `text_delta` → 聚合为 assistant 文本（同时**转发为流式事件**，P3 消费）
  - `tool_call_delta` → 按 index 聚合完整 tool_calls
  - reasoning delta → 转发思考事件（thinking_tracer 已有通道）
- 续轮信号 = **tool_calls 非空**（不信 stop_reason，对齐 CC）
- 流式中途出错：丢弃部分输出，走 chat_with_fallback 降级重试（Anelf 已有）
- 新增事件类型到 tracer/SSE：`assistant_delta`、`reply_delta`（send_message 工具参数流式转发 —— 用户可见回复的 token 级流式，P3 的 C1 依赖此）

**兼容性**：provider 不支持流式 → 自动回退非流式（probe_capabilities 已有能力探测）

**测试**：`tests/agent/mind/test_think_loop_streaming.py`（mock stream：增量聚合、中断、降级）

## 任务 2：tool_use/tool_result 配对铁律（B2）

**深读**：`Claude-Code/src/query.ts`（yieldMissingToolResultBlocks、ensureToolResultPairing）；Anelf `agent/mind/message_schema.py`

**实现**：
- 审计 think_loop 所有退出路径（异常/打断/护栏熔断/空输出/压缩触发），为未执行的 tool_calls 合成错误 tool_result（"执行被中断/取消"）
- 发送前 `normalize_for_send` 增加配对校验修复（孤儿 tool_calls 补结果、孤儿结果剔除）
- 单测覆盖每种退出路径

## 任务 3：错误恢复对齐（B3、B4）

**深读**：`Claude-Code/src/query.ts:1062-1358`、`withRetry.ts`；Anelf `agent/llm/resilience/`、`llm_manager.py:405`

**实现**：
- 413/context-overflow：先 microcompact（任务 4）再紧急压缩重试（现有），加**单次守卫**防循环
- max_output_tokens 截断：注入元消息"输出达到上限。直接续写，不道歉不复述，从中断处继续。把剩余工作拆小。"（≤3 次）
- chat_with_fallback：保留多客户端降级；补指数退避参数（500ms 起步、≤32s、抖动）与 529 连续计数切模型语义对齐

## 任务 4：工具结果落盘 + Microcompact（B5，消费 P1 成果）

**深读**：`Claude-Code/src/utils/toolResultStorage.ts:205,924`、`services/compact/microCompact.ts`；Anelf `agent/mind/tools/result_pipeline.py`、`result_budget.py`

**实现**：
- 单结果 >50K 字符：落盘 `workspace/.tool-results/`，替换为 `<persisted-output>` 预览+路径（保留 result_budget 会话预算作为第二道闸）
- 空结果 → `(工具名 completed with no output)` 占位
- Microcompact：距上次 assistant 消息超阈值（如 10 分钟，prompt 缓存已冷）→ 旧只读工具结果（read_file/search/shell/web）替换为 `[旧工具结果已清理]`，保留最近 1-2 个
- 可清理工具白名单元数据挂在 @tool 注册上（`compactable=True`）

## 任务 5：压缩器增强（B6）

**深读**：`Claude-Code/src/services/compact/{compact.ts,autoCompact.ts,prompt.ts}`；Anelf `agent/mind/context_compressor.py`

**实现**（保留 Anelf 保首尾+用户消息逐字的算法优势）：
- 连续失败 3 次熔断（本轮不再尝试，告知用户）
- **压缩后 rehydration**：压缩后自动重读最近编辑/读取的 ≤5 个文件（接 P1 ReadFileState 记录），每个 ≤5K token
- 摘要 prompt 补 9 段式结构要点（主要意图/文件与代码/错误与修复/待办/当前工作/下一步），保留中文
- 阈值参数对齐：窗口−预留输出−13K buffer

## 任务 6：nag 提醒注入（B7、A11）

**深读**：`Claude-Code/src/utils/attachments.ts:3266`（TODO_REMINDER_CONFIG）；Anelf `prefrontal_cortex.py:864 build_execution_context`

**实现**：
- goal/planning 工具 ≥10 轮未使用 且 ≥10 轮未提醒 → exec_context 注入提醒+当前 goal 列表（"不要向用户提及此提醒"）
- 计数器挂在 think session 状态

## 任务 7：中断语义对齐（B9）

- 区分「用户新消息打断」（合并进当前轮，不输出"已打断"）与「显式取消」（esc/指令，输出取消态工具结果）
- Anelf 已有打断关键词+合并，补齐取消路径的配对处理（与任务 2 联动）

## P2 验收标准

- [x] 配对铁律：执行路径 pipeline 异常加固 + 发送边界 `ensure_tool_result_pairing`（孤儿调用合成错误结果、孤儿结果剔除）
- [x] 错误恢复：max_output_tokens 截断注入续写（≤3 次，截断轮 tool_calls 丢弃防 JSON 断裂）；退避/fallback 经审计已对标（分类退避+抖动+窗口感知跳过，强于 CC）
- [x] Microcompact：工具链 ≥40 条时旧只读工具结果（白名单 11 个工具）→ 占位符，保留最新 6 条
- [x] 压缩熔断：连续失败 3 次熔断；**压缩后 rehydration**：重读最近 ≤5 个文件（5K/个、50K 总量）恢复工作现场
- [x] goal nag：10 轮未用 + 10 轮未提醒才注入（仅在该 scope 曾建目标时），"请勿向用户提及"
- [x] 流式循环：按用户决策**未实施**（归通道能力，不动 think_loop 骨架）
- [x] `pytest tests/` 805 全绿
