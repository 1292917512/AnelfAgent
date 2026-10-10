# 前端结构

React/TypeScript 前端的页面组织、模块插件体系与工作区对话联动的现状说明。修改
`web/frontend/src/` 时按需阅读；模块 UI 扩展点契约见 [ui-contributions.md](ui-contributions.md)。

## 页面结构

页面采用壳组件 + 子面板目录拆分模式，通用 TabBar 切换：

```text
pages/
├── Chat.tsx             # 对话工作台（首页，三栏：文件树/对话流/功能 Dock）→ chat/
├── Retrieval.tsx        # 检索（/retrieval：能力×提供者矩阵 + 抓取设置）→ retrieval/
├── Dashboard.tsx        # 总览 → dashboard/
├── Memory.tsx           # 记忆 → memory/
├── Config.tsx           # 配置中心 → config/（左侧模块树 + 检索 + 基础/高级分区 + 详情抽屉；
│                        #   数据驱动自 /config/meta，⌘K 与 /config?key= 深链定位）
├── Tasks.tsx            # 任务管理（独立页面）
├── Heartbeat.tsx        # 心跳 → heartbeat/（状态 + 配置与调度）
├── Models.tsx           # 模型 → models/
├── Channels.tsx         # 频道 → channels/
├── Thinking.tsx         # 思维链路
└── ...

components/common/TabBar.tsx  # 统一标签栏（溢出横向滚动 + 边缘渐隐提示）
lib/types.ts / api.ts         # API 接口类型（api 实例已导出供插件复用）
lib/core-routes.ts            # 核心路由注册表（Sidebar 据此识别插件导航项）
lib/channel-plugins.ts        # 频道前端插件注册表
lib/plugin-i18n.ts            # 插件 i18n 自注册
lib/utils.ts                  # cn() 类名合并工具
i18n/locales/{zh,en}/         # 核心 namespace（zh/en key 一一对应；插件文案不进核心 locale）
```

**新增核心页面五处同步**（缺一即「页面在但入口不可见」）：① `pages/<Page>.tsx`（App.tsx 经
import.meta.glob 按文件名懒加载）② `lib/core-routes.ts` 加路由行 ③ `components/layout/Sidebar.tsx`
的 FALLBACK_NAV 加导航项（图标须先在 ICON_MAP）④ `i18n/locales/{zh,en}/nav.json` 加标签键 +
`<page>.json` 页面文案 ⑤ **运行态导航优先取 `config/webui.json` 的 navigation 覆盖表**
（gitignored 用户配置，FALLBACK_NAV 仅兜底）——已有部署的机器须同步补项。

## 模块前端插件体系（热插拔）

频道/实体的前端与后端收敛到同一模块目录，核心框架只做通用加载，删除模块目录即整体拔出
（UI/API/文案/路由零残留）：

- **频道前端**：`channels/<id>/frontend/` 自持清单、组件、API、类型和翻译。`module-links.mjs`
  生成懒加载清单，构建以 `@channels` 别名直接消费源码。`index.ts` 声明 `login` / `panel` /
  `hiddenInChannelList`；整页内容经 `contributions.ts` 的 `app.routes` 插槽注册。核心频道管理面由
  注册表驱动，禁止按具体频道 ID 硬编码
- **实体面板**：`entities/<name>/panel.tsx`（+ `panels/` 子目录）经 `module-links.mjs` **代码生成**
  接入——扫描生成 `src/generated/entity-panels.ts`（懒加载表）与 `entity-panel-locales.ts`（locale
  eager 表），面板源码经 `@entities` 别名以真实路径被 tsc/vite/eslint 直接消费（无软链、无提交
  残留，生成文件 gitignored，prebuild/dev watcher 自动重写）；面板专属 i18n 放
  `panels/locales/{zh,en}.json`，由 `lib/entity-plugin-locales.ts` eager 注册（locale 静态打入主
  chunk）；locale 保留键 `_registry` 以显式映射声明全局词汇（`groups` / `configSections`）——
  实体组名翻译一律自持于模块目录，核心 tools.json/config.json 不写实体条目；无面板的实体也可只建
  `panels/locales/`（locale-only 实体同样被代码生成收录）；面板专属 API/类型放 `panels/api.ts` /
  `panels/types.ts`（不污染核心 lib）
- **跨页面扩展**：核心仅提供类型化插槽与渲染宿主，禁止直接导入具体实体/频道组件、API 或协议类型。
  模块在 `contributions.ts` 声明工作区工具、总览卡片、整页路由、数据页签、重启控件、音频区域和
  消息渲染器，详见 [ui-contributions.md](ui-contributions.md)
- 插件 API 复用核心 axios 实例（`import { api, apiErrorMessage } from "@/lib/api"`）

## 工作区对话联动

| 机制 | 位置 | 说明 |
|------|------|------|
| @提及 | `chat/mention/` + ChatInput | 输入框 `@` 触发文件模糊搜索（200ms 防抖 + 陈旧结果丢弃）；选中写 `[name](./path)` markdown 链接（路径信号，不预读内容，AI 用 read_file 自取）；`./` 前缀防渲染器误当协议；气泡侧 MentionMarkdown 还原为可点击文件 chip |
| 粘贴占位符 | ChatInput.handlePaste | 大段文本（≥400 字符）转 `[Pasted Content N chars]` 占位符 + 全文作为 txt 附件 |
| 工作区上下文注入 | `services/workspace_context.py` + workbench-store + FileEditor | 发送时把「当前文件/选中代码（行列+内容）/打开标签页」渲染为固定前缀块注入；格式与回放清洗同一约定（REQUEST_DELIMITER 分隔符，历史渲染 rsplit 取用户原文）；预算：选区 40k/标签 100/合计 20k 字符。注入放消息动态区不触碰前缀缓存；失败降级不阻塞发送 |
| 改动集面板 | `stores/changes-store.ts` + `render/ChangesCard.tsx` | EVENT_FILE_DIFF 按 turn 累积为「本轮改动 N 个文件」可折叠卡片沉淀进消息历史；点击单文件展开 diff、「open」跳编辑器 |
| 编辑器联动刷新 | FileEditor 订阅 fileVersions | AI 改文件后已打开标签自动刷新磁盘内容（有未保存草稿时不覆盖） |
| 审批语义化 | ApprovalPreview + ApprovalDialog | 审批弹窗按工具类型分发渲染：edit/write 出极简 diff（红绿行）、shell 出命令全文+cwd、delete/create 出受影响路径列表；识别不出走通用参数兜底 |

## 工作区展示层

| 机制 | 位置 | 说明 |
|------|------|------|
| 树的活性（变更装饰） | `stores/tree-changes-store.ts` + FileTreeNode 徽章/闪现 | file_diff SSE → 树装饰 store（path→+a/-r 累计，200 条 FIFO；与 changes-store 分工：后者面向「一轮回复」turn 结束清空，本 store 面向「工作区当前状态」不消退）。文件行尾 +a/-r、目录聚合子树计数、变更节点高亮闪现（reduced-motion 降级）、已加载目录防抖 600ms 局部刷新（上限 8 目录/批）、「只看变更」过滤。**AI 编辑只落 workspace 根，project 根一律不装饰**（防同名路径误标） |
| 树手感 | `file-tree-utils.ts`（compactChains/chainTailPath）+ FileTree 派生渲染 | 单子目录链压缩（节点 id 取最深路径，懒加载/右键/拖拽语义自动指向链尾）；吸顶面包屑；滚动渐隐 mask（onScroll 直写 CSS 变量零重渲染）。store 始终保存真实树结构，压缩/过滤仅为渲染期 useMemo 派生 |
| 思考进思维链（不落上下文） | llm_invoker LLM_END 载荷 + `render/ThinkingBlock.tsx` | 思考全文（8000 字符有界截断）随 LLM_END 进 tracer 内存会话——**不落 conversation_messages、不进 LLM 上下文**。流式区：思考中展开跟随滚动，正文到达自动折叠；reply/media/turn_end 时随消息固化（纯前端内存态，仅留最近 5 轮，刷新即消失） |
| 思维链面板可用性 | thinking-store + TracePanel 会话切换器 | **心跳/内省永不自动抢占面板**（autoFollow 只跟用户正看的对话链路）；会话切换器（最近 20 条，类型徽标：对话/心跳/内省/子代理），历史会话经 REST 拉全量（切换竞态守卫） |
| turn 工作记录不丢 | chat-sse-handlers turn_end 兜底 + ToolCallsCard 总耗时 | reply 未到达时 turn_end 合成仅卡片消息承接工具/改动/思考；工具卡片头部加本轮总耗时 |
| 流式累积器归并 | chat-sse-handlers.mergeStreaming | **tool_call/file_diff 帧后端不带 turn_id**——按「turn_id 缺失或一致即复用，仅非空新 turn_id 开新累积器」归并；reply 后到达的 TOOL_END 回填最后一条消息卡片的工具状态 |
| 状态胶囊 | `StatusCapsule.tsx`（取代 StatusBar） | 对话区右上角浮动：思维链会话态 > 对话工作态（「工作中 Xs」计时）> 空闲零占位；计时 tabular-nums 防抖动 |
| 引用标注 | FileEditor.quoteToChat | 有选区引选区（`[name:L3-L7](./path)` + 代码块），无选区引全文带路径标注；project 根带 `project:` 前缀 |
| file_diff 路径契约 | `adapter._on_file_diff` + `_relativize_workspace` + DiffView 解析 | 工具层 safe_path 产出**绝对路径**，而前端树/编辑器/改动集以工作区相对路径为键——统一在 adapter 出帧处相对化（区内转 posix 相对、区外审批编辑保留绝对）；同时透传 move_from/binary；rename 局部刷新覆盖源父目录。DiffView：`\ No newline at end of file` 标记不当 context 行计数、末尾空串幻影行、截断标记切首字符三处解析修正 |
| 工具行文件 chip | `render/ToolBlocks.tsx` | 工具参数带工作区**相对**路径时标题渲染为可点击链接（点击跳编辑器）；绝对路径不可点（前端没有工作区绝对根，避免同文件双标签） |
| 运行态 shimmer | `components/common/Shimmer.tsx` + ActivityBar | 300% 渐变 + background-clip:text 扫光；深浅主题各一组变量；reduced-motion 降级为静态 |
| 紧凑耗时 / 数字压缩 | `lib/format.ts`（formatElapsedCompact / formatTokensCompact） | 0s→59s→1m 05s→1h 02m；1.2K/3.4M/5.6B（<10 留一位小数）；ContextChip 与 ToolBlock 统一走这两个 |
| diff 美学 | `chat/DiffView.tsx` | 行号槽底色比行底色深半档、hunk 间 `⋮` 省略行、+/- 符号列与内容列定宽对齐、亮暗主题各一套语义色 |
| 长用户消息折叠 | `render/CollapsibleUserMessage.tsx` | >120px 折叠 + 底部 mask 渐隐 + 悬浮展开钮；ResizeObserver+rAF 合并测量、aria-expanded |
| 时间戳 hover 浮现 | MessageList | 行级 hover 浮现 + focus-within 兜底（键盘可达） |
| SSE 断线可见性与恢复 | `chat-store.ts`（sseConnected + refreshAfterReconnect）+ MessageList 横幅 | es.onopen/onerror 置连接状态——聊天流顶部「正在重连」横幅（i18n）；断线后重连自动补拉当前会话最近一页历史，按消息 id 尾部对齐合并；sending 卡死由既有发送看门狗兜底 |

## 前端开发约定

- 页面超过 300 行拆为子面板目录、统一用 TabBar、i18n 覆盖所有文本、`Record<string, unknown>`
  替换为 `lib/types.ts` 接口
- 分层保持 stores ← pages 单向；Tailwind 构建同时扫描实体面板和频道前端源码，模块内声明的完整
  工具类名直接生效，不要用字符串拼接生成类名
- 前端架构测试机械校验「核心不导入具体模块组件」的边界
