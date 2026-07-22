# P1 子计划：工具层对标（edit_file / ReadFileState / Bash / 并发分级 / 权限）

> 对应总计划矩阵 A1–A10。源码依据：`01-claudecode-tools.md`。
> 实施顺序即下列任务顺序；每个任务先深读源码再动手。

## 任务 1：ReadFileStateCache（A2，基础设施，最先做）

**深读**：`Claude-Code/src/utils/fileStateCache.ts`；Anelf `entities/filesystem/tools.py`

**实现**：新增 `entities/filesystem/file_state.py`
- `FileState` dataclass：`content, mtime, offset, limit, is_partial_view, timestamp`
- 每会话（scope）一份缓存，挂在 think session 上（参考 PFC 会话对象如何传递）
- API：`record_read(path, content, mtime, offset, limit)` / `check_writable(path) -> Ok | NotRead | PartialView | Stale`
- 过期判定：mtime > 记录时间 且 内容逐字节不一致 → Stale（内容一致则放行并刷新时间戳）

**测试**：`tests/entities/test_file_state.py`

## 任务 2：edit_file 工具（A1，本阶段核心）

**深读**：`Claude-Code/src/tools/FileEditTool/{FileEditTool.ts,utils.ts,prompt.ts}` 原文（重点：utils.ts:73-93 findActualString、104-199 preserveQuoteStyle、206-228 applyEditToFile、262-350 getPatchForEdits、531-657 归一化）

**实现**：`entities/filesystem/edit_tool.py` + 注册 `edit_file`
- 输入：`file_path, old_string, new_string, replace_all=False`（bool 容忍 "true"/"false" 字符串）
- 校验顺序（全部返回可操作中文错误消息，对齐 CC 编号语义）：
  1. old == new → 报错
  2. 文件 >1GiB 拒绝；编码嗅探（BOM→utf-16，否则 utf-8）；CRLF→LF 内存归一化
  3. 不存在 + 空 old_string = 创建；不存在 + 非空 → "是否想编辑：…"相似路径建议
  4. read-before-write（任务 1）；部分读取不授权
  5. mtime/内容过期检查
  6. `find_actual_string`：直接匹配 → 弯引号归一（`' ' " "`→`' "`）再匹配（从原文切片）
  7. 唯一性：多处匹配且未 replace_all → 报错并教学（更多上下文 or replace_all）
- 执行：
  - `preserve_quote_style`：弯引号匹配成功时把 new_string 直引号还原为弯引号
  - 删行特例：`new_string==''` 且 old_string 不带尾换行但 `old+'\n'` 存在 → 连带删换行
  - new_string 逐行去尾空格（.md/.mdx 除外）
  - 写盘恢复原行尾（CRLF 文件写回 CRLF，防 `\r\r\n`）
  - 更新 ReadFileState；产出 structured diff（`difflib.unified_diff`）供 UI（结果文本只回一句"文件已更新"，**diff 不进模型上下文**，存入工具结果 metadata 供 UI/Trace 展示）
- 注册标签：`always`（与 run_shell_command 同级），check_fn 无需

**测试**：`tests/entities/test_edit_tool.py`（覆盖：唯一性/弯引号/CRLF/删行/过期/replace_all/创建/尾空格）

## 任务 3：read_file / write_file 增强（A3、A4）

**深读**：`Claude-Code/src/tools/FileReadTool/{FileReadTool.ts,limits.ts,prompt.ts}`、`FileWriteTool/FileWriteTool.ts`；Anelf 现有实现

**read_file 增强**：
- 输出加行号（`cat -n` 风格）；新增 `offset`/`limit` 参数
- 上限：≤2000 行、≤256KB、估算 token ≤25000（超限报错并指导用 offset/limit）
- 读重去重：相同路径+范围+未修改 → 返回存根"内容未变，参考此前读取"
- 图片扩展名 → 走现有 media_segments 机制（对齐 CC 的 image block）
- 每次读取记录 ReadFileState（offset/limit → is_partial_view）

**write_file 增强**：
- 已存在文件：强制 read-before-write + 过期检查；新文件直接写
- prompt 里引导：修改已有文件优先用 edit_file

## 任务 4：run_shell_command 对齐（A5）

**深读**：`Claude-Code/src/tools/BashTool/BashTool.tsx:624-820`、`src/utils/Shell.ts:181-442`、`bashProvider.ts`（cwd 持久机制）、`outputLimits.ts`、`timeouts.ts`；Anelf `entities/filesystem/tools.py` 中 run_shell_command

**改造点**：
- 每命令新进程（现状若已是则确认）；**cwd 持久**：命令尾追加 `pwd -P > tmpfile`，结束后读回更新会话 cwd；漂出 workspace 自动重置+附注
- 输出：stdout/stderr 合并按时间序；模型可见上限 30,000 字符，超出**落盘** `workspace/.tool-results/<id>.txt` 并给 2KB 预览+路径
- 超时：新增 `timeout` 参数（默认 120s，上限 600s）；超时行为：先发 SIGTERM 再 SIGKILL（超时转后台列为 backlog，依赖后台任务基建）
- 新增 `run_in_background` 参数（复用 Anelf 后台任务基建，若接入成本高则降级为 backlog 项并在计划中注明）

**测试**：`tests/entities/test_shell_tool.py`

## 任务 5：工具并发安全分级（A6）

**深读**：`Claude-Code/src/services/tools/toolOrchestration.ts:91-177`；Anelf `agent/mind/tools/think_loop.py:1121 execute_tool_calls`、`core/entity.py execute_tool`

**实现**：
- `entities/_sdk.py` 的 `@tool` 增加 `concurrency_safe: bool | Callable = False` 元数据（默认 False，fail-closed）
- read_file/search_files/web_* /recall 等只读工具标 True
- `execute_tool_calls` 改造：按连续段分区 —— 连续安全调用 gather 并行（上限 10，asyncio.Semaphore），非安全调用串行；保持结果顺序与 tool_calls 顺序一致
- 注意：结果回写 tool_chain 顺序必须与调用顺序一致（配对铁律）

**测试**：`tests/agent/mind/test_tool_concurrency.py`

## 任务 6：权限规则匹配增强（A10）

**深读**：`Claude-Code/src/utils/permissions/permissions.ts:1158-1319`、`filesystem.ts:1030-1400`；Anelf `agent/approval/{gate.py,policy.py,manager.py}`

**改造点**（保留通道化审批交互，补规则层）：
- 策略规则从 tool 名 glob 扩展为 `工具(参数模式)` 形式：如 `run_shell_command(npm test*)`、`edit_file(/src/**)`
- 决策增加 `decision_reason` 字段（rule/mode/trust/timeout…）写入审计
- "本次会话不再询问"（session 级规则）已在 trust 机制有雏形 → 补齐 "永久不再询问"（写入 policies 文件）
- 保持 RiskLevel / 热重载 / 超时 deny 语义不变

## 任务 7：关键工具长 prompt（A8）

**深读**：`Claude-Code/src/tools/{FileEditTool,FileWriteTool,FileReadTool,BashTool}/prompt.ts` 原文

**实现**：Anelf schema 从 docstring 生成 → 为 edit_file/write_file/read_file/run_shell_command 增加长 prompt 机制（如 `@tool(prompt=...)` 覆盖 docstring），内容对齐 CC：
- edit_file：必须先 Read、保持精确缩进、唯一性失败语义、replace_all 用法、优先编辑而非新建
- shell：工具偏好表（搜文件用 search_files、读文件用 read_file、编辑用 edit_file，别用 cat/sed/echo>）、引号包裹路径、避免 cd

## 任务 8：search_files 增强（A9）

- 对齐 Glob/Grep 语义：文件名 glob 模式 + 内容正则搜索两个参数模式；结果按修改时间排序、限量

## P1 验收标准

- [x] edit_file 全套测试通过，模型编辑场景不再依赖 write_file 全文覆盖
- [x] 未读先写/过期写全部被拦截且错误消息可操作
- [x] shell 输出超限落盘可查；cwd 跨命令持久（沙箱开启时漂出重置，关闭时放行）
- [x] 并行批次只含只读工具；结果顺序不乱
- [x] 权限规则 `工具(模式)` 匹配生效且审计含 decision_reason（matched_rule + 结构化日志）
- [x] `pytest tests/` 全绿（764 通过；tests/web/test_config_meta.py 6 个失败为用户 WIP 改动所致，与本阶段无关）

## 实施记录（2026-07-22 完成）

| 任务 | 交付物 |
|---|---|
| T1 ReadFileState | `entities/filesystem/file_state.py`（LRU/scope 隔离/mtime+内容过期判定）+ `_sdk.get_current_scope()` 桥接 |
| T2 edit_file | `entities/filesystem/edit_utils.py`（弯引号/引号保持/删行特例/尾空格/diff）+ `tools.py:edit_file`（编号错误 1-14） |
| T3 read/write 增强 | read_file：行号→/offset/limit/2000行·256KB·25K token/读重去重/二进制分发；write_file：read-before-write |
| T4 shell 对齐 | `entities/filesystem/shell_state.py`（pwd 捕获 cwd 持久/30K 截断落盘 `.tool-results/`/超时 120s-600s）；`core/command.py` 增加 cwd 参数 |
| T5 并发分级 | `_sdk` 增加 `concurrency_safe`（fail-closed）；`think_loop._partition_tool_calls` 连续安全批并行（Semaphore 10）；read_file/list_directory/file_info/search_files/web_* 标记安全 |
| T6 权限规则 | `policy.py`：`工具名(参数glob)` 解析与匹配（fail-closed）、`extract_matchable_arg`；`gate.py` 传参匹配 + reason 审计；`session.py` matched_rule 字段 |
| T7 长 prompt | `_READ/_WRITE/_EDIT/_SHELL_PROMPT` 经 description 入 schema（发现 description 只取 docstring 首行的问题并绕过） |
| T8 search_files | content_pattern 正则搜内容（grep 语义）、文件名模式按 mtime 倒序 |

测试：`tests/entities/test_file_state.py`、`test_edit_utils.py`、`test_file_edit_tools.py`、`test_shell_tool.py`、`test_search_and_prompts.py`、`tests/agent/mind/test_tool_concurrency.py`、`tests/unit/approval/test_policy_arg_patterns.py`（共 100+ 新用例）
