# Claude Code 对话窗口（终端 UI）侦察摘要

> 来源：`/Users/wangchenglong/projects/Claude-Code/src` 深度侦察（2026-07-22）。
> Claude Code 只有终端 UI（定制 Ink fork），无 Web UI —— 但其**信息架构与交互模式**完全可移植到 Anelf 的 React WebUI。

## 1. 渲染架构要点（理念层，Web 可借鉴）

- **双渲染策略**：全屏模式（虚拟化滚动列表 ScrollBox + VirtualMessageList）/ 经典模式（内联渲染，滚出视口的内容冻结 + 200 条上限切片）
- **消息管线 = 纯变换链**（Messages.tsx）：normalize → 过滤 → 重排 → 分组（连续工具调用 → grouped_tool_use）→ 折叠（"Read 3 files…"）→ 建立 tool_use↔tool_result 索引 → 傻瓜式行组件按类型分发
- **每种工具自带渲染函数**：`renderToolUseMessage` / `renderToolResultMessage`（如 `Bash(npm test)` 一行式调用展示）
- **流式文本是消息数组的"尾随兄弟"**，不进数组：`StreamingMarkdown` 用**单调前进的稳定前缀**做增量解析 —— 已稳定部分永不重解析，每个 delta 只重解析最后一个块
- 合成流式消息用**确定性 UUID** 保证 React key 稳定；OffscreenFreeze 让滚出视口的行零开销

## 2. 消息渲染细节

- 行类型分发：user（❯ 提示符样式）/ assistant（⏺ + Markdown）/ thinking（斜体暗色）/ tool_use / tool_result / system / 折叠组
- **工具调用状态灯**（ToolUseLoader）：未决=闪烁 ⏺，排队=灰，成功=绿，失败=红
- **工具结果缩进展示**：`⎿` 排水沟（选中时跳过），截断由工具自报，可展开
- **Diff 渲染**：原生 Rust color-diff 模块，语法高亮 + 行号排水沟，WeakMap 缓存
- **加载行**：随机动词（~300 个，如 "Pondering…"）+ 闪烁微光动画 + 3 秒无新 token 变红 + **耗时计时器 + token 计数器（↓ N tokens 平滑递增）** + "esc to interrupt" 提示
- 轮结束用过去式动词（"Worked for 5s"）

## 3. 输入框

- readline 状态机：kill ring（ctrl+k/u/w/y）、undo、词级移动、多行
- **大段粘贴 → 引用芯片**（`[Pasted text #1 +N lines]`），提交时展开；图片粘贴 → `[Image #N]`
- 历史：上下键 + ctrl+r 增量搜索
- slash 命令内联高亮 + 自动补全列表；@ 文件提及补全
- 模式指示符（`!` bash 模式、`#` 记忆模式、plan 模式），边框颜色随模式变

## 4. 权限弹窗

- 每个工具一个专用对话框组件（Bash/Edit/Write/WebFetch/ExitPlanMode/AskUserQuestion…）
- 对话框 = 标题 + 工具特定正文（命令预览 / **StructuredDiff** / URL）+ 选项列表：
  - "Yes" / "Yes, 且以后不再询问 `npm test`"（**从权限规则建议生成，可持久化**）/ "No, 并告诉 Claude 怎么做"（打开反馈输入）
- 用户可在批准前**修改提议的输入**（模型会被告知）
- 自动批准显示 "✓ Auto-approved"

## 5. 状态栏 / 通知 / 后台任务

- 输入框下方 footer：模式提示、任务 pill（"N tasks · Enter to view"）、快捷键提示
- 右侧 StatusLine：模型、cwd、成本、**上下文剩余百分比**、速率限制
- 一行轮换通知区（更新/连接状态/提示）
- 展开的 todo 列表固定在输入框上方；后台任务对话框可浏览详情

## 对 Anelf WebUI 的移植映射

| Claude Code（终端） | Anelf WebUI 对应物 | 差距 |
|---|---|---|
| 流式尾随兄弟 + 稳定前缀 Markdown | SSE delta 事件 + 前端增量 markdown | **待实现（Phase 3 核心）** |
| 内联工具调用块 + 状态灯 + ⎿ 结果 | 聊天气泡内联工具块 | **待实现**（现在工具调用只在 Thinking 页） |
| StructuredDiff | react-diff-view / 自研 diff 组件 | 待实现 |
| 加载动词+计时+token 计数 | sending 三点动画 | 待增强 |
| 权限对话框（工具专用+规则持久化+改输入） | ui_ask 模态（通用） | 待专门化 |
| todo 固定面板 / 任务 pill | Tasks 页 | 待内联到对话窗口 |
| 排队消息显示 | 无 | 待实现 |
| 上下文剩余 % 状态栏 | 无 | 待实现 |
| 折叠组（"Read 3 files…"） | 无 | 待实现 |
