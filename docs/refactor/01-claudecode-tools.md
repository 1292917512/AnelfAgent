# Claude Code 工具与代码编辑机制（侦察摘要）

> 来源：`/Users/wangchenglong/projects/Claude-Code/src` 深度侦察（2026-07-22）。
> 用于 Anelf 工具层对标改造。

## 1. 工具框架（`src/Tool.ts`）

- 工具 = 普通对象（无基类），核心成员：
  - `inputSchema`：Zod v4 `strictObject`
  - `description()`：UI 用一句话（不发 API）
  - `prompt()`：**长文本，作为 API 的 description**（prompt 工程都在这里）
  - `validateInput()`：语义校验，失败消息以 `<tool_use_error>` 原样回给模型
  - `checkPermissions()`：allow/ask/deny/passthrough
  - `call(args, context, canUseTool, parentMessage, onProgress)`
  - 行为标志：`isConcurrencySafe(input)`（默认 **false**）、`isReadOnly`、`isDestructive`
  - `maxResultSizeChars`：超限结果落盘 + 给模型预览和路径
  - `backfillObservableInput`：把 `~`/相对路径展开为绝对路径，防止权限/hook 被绕过（且不改 API 入参，保住 prompt 缓存）
- 执行管线（toolExecution.ts）：解析工具 → abort 检查 → Zod 校验 → validateInput → PreToolUse hooks（可改输入/注入上下文/直接给权限结论）→ 权限管线 → call → PostToolUse hooks → 结果映射（超限持久化）
- **并发编排**（toolOrchestration.ts）：按 `isConcurrencySafe(input)` 把一个 assistant 消息里的工具调用切成「安全批次（并行，上限 10）」和「非安全批次（严格串行）」；`StreamingToolExecutor` 甚至能在 assistant 消息还在流式输出时就开始执行安全工具
- 工具列表**按名排序**且内置工具保持连续前缀 → prompt 缓存稳定

## 2. Edit 工具（`src/tools/FileEditTool/`）—— 精确字符串替换 + 层层归一化

输入：`{file_path, old_string, new_string, replace_all=false}`（布尔容忍字符串）

校验与算法（失败消息全部可操作、编号、指导模型修正）：
1. `old_string === new_string` → 报错
2. 文件 >1GiB 拒绝；编码嗅探（UTF-16 BOM/UTF-8）；**CRLF→LF 内存归一化**
3. 文件不存在 + 空 old_string = 创建文件；不存在 + 非空 → "Did you mean …?" 建议
4. **Read-before-write**：`readFileState` 中必须有该文件的完整读取记录，否则报错
5. **过期检查**：mtime > 读取时间戳 → 拒绝（除非内容逐字节一致，防云同步/杀软误报）
6. **匹配 `findActualString`**：先直接 `includes`；失败则把弯引号 `' ' " "` 归一为直引号再匹配，从原文切片
7. **唯一性**：多处匹配且未 `replace_all` → 报错并教模型「提供更多上下文或开 replace_all」
8. 执行时：`preserveQuoteStyle`（弯引号文件里把 new_string 的直引号改回弯引号）、删除整行时连带删尾换行、写盘时**恢复原行尾风格**（CRLF 文件写回 CRLF）
9. `normalizeFileEditInput`：new_string 逐行去尾空格（.md 除外）；API 消毒 token 反归一化（`<fnr>`→`<function_results>` 等）
10. **原子区段**：写盘前同步重读+重校验，防 TOCTOU
11. 模型收到的结果只有一句 "The file … has been updated successfully." —— **diff 只给 UI，不给模型**

## 3. Write 工具

- 已存在文件同样要求 read-before-write + 过期检查
- 按模型内容**逐字写 LF**（因为 Write 是全量替换，尊重模型显式行尾）
- prompt 强调：优先用 Edit（只发 diff）；除非明确要求否则不建 .md/README

## 4. Read 工具

- `cat -n` 风格行号输出；≤2000 行、≤256KB、≤25000 token（超限报错并指导用 offset/limit）
- 按扩展名分发：图片→base64 块（token 预算内压缩）、ipynb→cell JSON、pdf→文档块
- 每次读取记入 `readFileState`（供 Edit/Write 校验）；**部分读取不授权写入**
- **读重去重**：相同范围未变文件 → 返回存根"内容未变，参考之前的读取"

## 5. Bash 工具（`src/tools/BashTool/`）

- 输入：`{command, timeout?, description?, run_in_background?, dangerouslyDisableSandbox?}`
- **每命令新进程，无持久 shell；只有 cwd 持久**（命令尾部追加 `pwd -P >| tmpfile`，Node 读后 setCwd）
- 环境 = 一次性登录 shell 快照；cwd 漂出项目目录自动重置并附注
- stdout/stderr 合并写入同一文件 fd（天然时序交错）；前台最多回读 **30,000 字符**，超出落盘给预览+路径；后台任务 5GB 看门狗
- 超时：默认 2 分钟、上限 10 分钟；超时时**自动转后台**而非杀死（sleep 除外，独立 sleep ≥2s 直接拒绝并引导用 run_in_background）
- 权限：tree-sitter bash AST 解析，复合命令按 `&& ; |` 拆分逐个子命令评估取最严结论；只读判定决定能否并行
- prompt 内的工具偏好表：搜文件用 Glob 不用 find、搜内容用 Grep 不用 grep、读文件用 Read 不用 cat、编辑用 Edit 不用 sed、写文件用 Write 不用 echo>

## 6. 权限系统（`src/utils/permissions/`）

- 模式：`default | acceptEdits | plan | bypassPermissions | dontAsk`
- 规则：`Bash(npm run test:*)`、`Edit(/src/**)` 形式，来源分层（user/project/local/policy/cli/session）
- 管线顺序：deny 规则 → ask 规则 → 工具自身 checkPermissions → requiresUserInteraction → 内容规则/safetyCheck（**bypass 免疫**）→ bypass 模式 → allow 规则 → 默认 ask
- 每个决定带机器可读 `decisionReason`；审批对话框可**修改输入后再批准**（模型会被告知"用户在批准前修改了你的提议"），可持久化新规则

## 对 Anelf 的启示（初步）

| 机制 | Anelf 现状 | 方向 |
|---|---|---|
| Edit 精确替换+归一化 | 无 Edit 工具，只有 write_file 全文覆盖 | **新增**（Phase 1 核心） |
| read-before-write + 过期检查 | 无 | 新增 `ReadFileState` 缓存 |
| 行尾/编码往返 | 无 | 新增 |
| 结果超限落盘+预览 | result_budget 按会话预算截断 | 增强为落盘+路径 |
| 工具并发安全分级 | 全部 gather 并发 | 引入 `is_concurrency_safe` 分区 |
| Bash cwd 持久/stateless | run_shell_command 待查 | 对齐 |
| 权限管线+decisionReason | ApprovalGate 通道轮询 | 保留通道化，补规则匹配 |
| 工具 prompt 工程 | docstring 直出 schema | 为关键工具写长 prompt |
