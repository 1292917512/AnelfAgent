# Anelf 现状清单（对标重构用基线）

> 来源：对 `/Users/wangchenglong/projects/AnelfAgent` 的全面代码侦察（2026-07-22）。
> 本文档是「Claude Code 对标重构」的基线：列出 Anelf 现有能力，供逐功能对比取舍。

## 技术栈

- Python 3.10–3.12，FastAPI 后端，litellm 多供应商 LLM 接入
- 前端 React 18 + TypeScript + Vite 6 + Tailwind 4 + zustand + react-query + react-markdown + CodeMirror
- 测试：pytest + pytest-asyncio，62 个测试文件；无 ruff/mypy 配置
- 注释/文档/prompt 以中文为主，标识符英文

## 架构主线

```
通道适配器 → AgentApp(队列) → AgentAssistant → Mind(元决策) → think_loop(执行决策+工具循环)
                                        ↕                ↕
                                  MemoryStore        EntityRegistry(工具)
WebUI ← SSE(chat/stream, thinking/stream) ← 通道/Tracer 事件
```

## 现有能力清单（对比基线）

### A. 对话循环
- `Mind`（1282 行）：态势收集 → 元决策（decide 工具单独 LLM 调用）→ 分发（REPLY/REFLECT/…）
- `think_loop`（1437 行）：统一多轮工具循环；纯工具模式（`tool_choice=required`，回复必须走 `send_message`/`end_reply`）
- 并发工具执行（asyncio.gather）、消息到达合并、打断检测、护栏（GuardrailController）、后台任务挂起等待
- LLM 调用走 `LLMManager.chat_with_fallback`（多客户端重试+降级）
- `chat_stream()` 已实现但**对话循环未使用流式**（仅 Telegram draft / Responses API 用到）

### B. 工具系统
- `@tool` / `@deferred_tool` 装饰器 → `EntityRegistry`（分组/标签/休眠组/check_fn 门控）
- 签名+docstring 自动生成 OpenAI function schema；执行时 JSON 修复、类型强转、未知参数拒绝、超时
- 每轮工具选择：`always` 标签 + 通道能力 + 消息标签激活 + 热召回 top-N + 动态激活组
- 审批：`ApprovalGate` + glob 策略 + 风险等级，通过来源通道发审批消息轮询决定
- 安全：会话 token 防注入、工具结果威胁扫描（中英正则+NFKC）

### C. 工具目录（Claude Code 没有的 Anelf 优势项加粗）
- 文件系统：`read_file / write_file / append_file / list_directory / file_info / copy_file / move_file / delete_file / mkdir / search_files / run_shell_command / python_exec`
- 思考调度：`end_reply / schedule_reply / schedule_reminder / list_reminders / cancel_reminder / activate_tool_group`
- 通道输出：`list_channels / send_message / send_photo / send_voice / send_file`
- **记忆（~35 个）**：memorize/recall/forget/对话管理/实体画像/Cognee 知识图谱…
- **笔记**：read_notes/write_notes/memory 文件 CRUD
- **规划**：goal CRUD；**技能自学习**：create/update/search/list/get_skill
- **委派**：delegate_task（子代理并行/后台）、check_background_tasks
- 网络：web_search（百度）/web_fetch/extract_page_links/web_request
- **媒体**：识图/语音互转/绘图/视频/minimax 全家桶
- **模型控制**：模型列表/切换/会话参数/优先级；ollama 管理
- 系统：系统信息/git 配置/代理/日志查询
- **UI 驱动**：ui_notify/ui_ask（阻塞式模态提问）/ui_open_panel/ui_compose/ui_get_state
- devops：备份/自更新/重启；MCP 桥接（热重载）

### D. 消息与上下文
- 三层 prompt 缓存（stable/context/volatile，Anthropic cache_control 断点）
- 标签系统 `[key:value]` 贯穿消息元信息与工具路由
- 上下文压缩器（707 行）：token 跟踪、阈值 75%、保留首尾+中间 LLM 摘要、用户消息逐字保留、溢出紧急压缩
- 记忆召回：SQLite FTS5 + embedding 混合（0.7 语义/0.3 衰减）、跨通道召回、技能注入

### E. Web 对话窗口
- REST + 双 SSE：`/chat/stream`（完整消息事件）、`/thinking/stream`（思考轨迹事件）
- 三栏工作台：文件树 | 消息流 | Dock（Trace/Tasks/Search/Status/Settings）+ CodeMirror 文件编辑器
- **无 token 级流式渲染**；工具调用不在聊天气泡内联展示（在 Thinking 页/Trace 面板）
- AI 可反向驱动 UI（ui_* 工具 → SSE ui_command）

### F. Anelf 独有优势（重构必须保留）
1. 实体注册系统 + 两级能力发现
2. 标签驱动的工具注入（恰到好处的工具集）
3. Mind 双层决策（元决策 + 执行决策）
4. 多通道（cli/telegram/qq/feishu/webui/http_api）+ 监督者看门狗
5. 混合语义记忆 + Cognee 图谱 + 笔记蒸馏
6. 心跳自主任务、规划、技能自学习、子代理委派
7. 会话 token 防注入 + 威胁扫描
8. 三层 prompt 缓存（省 token）
9. UI 反向驱动（AI 操作工作台）
10. 审批策略热重载 + 通道化审批
