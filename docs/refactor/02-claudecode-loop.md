# Claude Code 对话循环与上下文管理（侦察摘要）

> 来源：`/Users/wangchenglong/projects/Claude-Code/src` 深度侦察（2026-07-22）。

## 1. 主循环（`src/query.ts`，1729 行）

`queryLoop()` = 单 `while(true)`，每次迭代 = 一次 API 往返。每次迭代顺序：

1. **按压缩边界切片**：只发送最近一个 `compact_boundary` 之后的消息（UI 历史不动，只切 API 视图）
2. **工具结果预算**：超 200K 字符的消息里最大的旧工具结果替换为落盘引用
3. **Microcompact**：清理旧的只读工具结果（`[Old tool result content cleared]`）
4. System prompt 组装 + git 状态附加
5. **Auto-compact 检查**（见 §3）
6. **流式消费**：收集 text + tool_use 块；`stop_reason` 不可靠，**tool_use 块的存在是唯一续轮信号**；`StreamingToolExecutor` 可在流式中途就开始执行并发安全工具
7. **可恢复错误暂不上报**（withhold）：413→反应式压缩重试；max_output_tokens→先升级到 64K 重试，再注入"直接续写"元消息（≤3 次）
8. 无 tool_use → stop hooks / token 预算续推 → 结束
9. 有 tool_use → 分批执行（安全批并行 ≤10，其余串行）→ 追加 [assistant…, toolResults…, attachments…] → maxTurns 检查 → 续轮

**铁律：任何退出路径（异常/中断/降级/未知工具）都必须为孤儿 tool_use 合成错误 tool_result，保持配对。**

### 重试（withRetry.ts）
- 指数退避（500ms 起步，≤32s，25% 抖动），尊重 Retry-After，最多 10 次
- 529 连续 3 次 → 有 fallback 模型则切换，重发前剥离 thinking 签名 + 为已发出的 tool_use 合成错误结果
- 401 → 刷新 token 重建客户端；流式 404 → 非流式兜底（此前部分消息打墓碑丢弃）

### 中断/中途消息
- abort reason 区分：`interrupt`（用户提交新消息，不输出"已打断"，新消息本身即上下文）vs Esc
- 消息队列三级优先级 now > next > later；**轮中 drain**：工具批次后把排队消息转为 attachment 注入当前轮
- 全部在执行工具可取消（interruptBehavior=cancel）时，提交新消息立即 abort 当前轮

## 2. 消息/上下文构建

### System prompt（constants/prompts.ts）
**有序块数组，为 prompt 缓存排序**：
1. 静态可缓存：intro、系统段、任务执行、工具使用、语气风格、输出效率
2. `SYSTEM_PROMPT_DYNAMIC_BOUNDARY` 分界
3. 动态段（注册表）：会话指引、记忆、**环境信息**（cwd/git/平台/shell/OS/模型）、语言、输出风格、MCP 指令
- 最终重组成 ≤4 个带 cache_control 作用域的块

### 注入点
- `appendSystemContext`：git 快照（分支+status 截断 2000 字符+最近 5 提交）追加为 system 块，注明"这是会话开始时的快照"
- `prependUserContext`：**合成首条 meta user 消息**，`<system-reminder>` 包裹 CLAUDE.md + 当前日期 + "除非高度相关否则不要回应此上下文"
- **每轮 attachments**（attachments.ts，3997 行）：todo 提醒、plan 模式、文件变更、排队消息、相关记忆（≤200 行/≤4KB 每文件/≤5 文件每轮）……全部包成 `<system-reminder>` meta user 消息，附在工具结果 user 消息上

### 请求时归一化（normalizeMessagesForAPI）
- 合并连续 user 消息、合并同 message.id 的拆分 assistant 记录
- 剥离空白 assistant 消息、尾部 thinking
- 每轮调用前 `ensureToolResultPairing` 修复配对
- 超限媒体剥离（≤100 项/请求）

### 工具结果序列化
- 空结果 → `(X completed with no output)`（防空结果诱发 stop 序列）
- 超阈值（默认 50K 字符）→ 写盘 `tool-results/<id>.txt`，替换为 `<persisted-output>` 预览 2KB + 路径
- Edit/Write 成功只回一句话，diff 不给模型

## 3. 上下文窗口管理

### Token 计数
`上次 API usage（input+cache_*+output）+ 之后消息的粗估（~4 字符/token）`

### Auto-compact（services/compact/）
- 有效窗口 = 模型窗口（200K/1M）− min(maxOutput, 20K)
- **阈值 = 有效窗口 − 13K**；UI 在 −20K 处告警；关闭 auto-compact 时在 −3K 处阻断（给手动 /compact 留空间）
- 连续失败 3 次熔断

### 压缩流程（compact.ts）
1. 摘要 prompt 9 段式：主要意图/关键概念/文件与代码/错误与修复/问题解决/**全部用户消息**/待办/当前工作/下一步（带原文引用）；`NO_TOOLS` 前后夹逼；`<analysis>` 草稿 + `<summary>`
2. **缓存共享 fork 优先**：相同 system prompt/工具/模型的 fork agent 复用主线程 prompt 缓存，禁全部工具，maxTurns=1；兜底用直接调用
3. 压缩请求本身 413 → 砍最旧 20% 轮次重试（≤3 次）
4. 重建上下文：boundary 标记 + 摘要 meta 消息（"这是上轮对话的延续…直接继续，不要确认摘要"）+ **rehydration**：重读最近 ≤5 个文件（每个 ≤5K token，总预算 50K）+ plan/技能/hook 重注入
5. 压缩后清空 `readFileState`（强制重新读文件）

### Microcompact
- 时间触发：距上条 assistant 消息超过阈值（prompt 缓存反正冷了）→ 直接把旧的只读工具结果替换为 `[Old tool result content cleared]`
- 可压缩工具白名单：Read/shell/Grep/Glob/WebSearch/WebFetch/Edit/Write

## 4. Todo 追踪

- v2 任务为文件存储（JSON），字段：id/subject/description/status/owner/blocks/blockedBy
- **不持续注入上下文**，用"nag 启发式"：距上次 TodoWrite ≥10 轮 且 距上次提醒 ≥10 轮 → 注入 `<system-reminder>` 提醒 + 当前列表（"绝不要向用户提及此提醒"）

## 对 Anelf 的启示（初步）

| 机制 | Anelf 现状 | 方向 |
|---|---|---|
| 流式驱动循环 | think_loop 用非流式 chat() | **切换为流式循环**（Phase 2 核心） |
| 工具结果落盘+persisted-output | result_budget 截断 | 对齐 |
| Microcompact 清理旧工具结果 | context_compressor 全量摘要 | 先做轻量清理，再做摘要 |
| 压缩阈值 window−13K / 熔断 | (ctx−max_output)×0.75 | 对齐参数+熔断 |
| 压缩后 rehydration | 无 | 新增（重读关键文件） |
| system-reminder attachments | exec_context 状态消息 | 保留并扩展（todo nag、排队消息注入已有） |
| 轮中消息合并 | 已有（think_loop:587） | **Anelf 优势，保留** |
| 轮中 drain 排队消息为当前轮上下文 | 有打断+合并 | 对齐语义 |
| 429/529 fallback 模型 | chat_with_fallback 已有 | **Anelf 优势，保留并对齐** |
| 配对修复 ensureToolResultPairing | 待查 | 新增/验证 |
