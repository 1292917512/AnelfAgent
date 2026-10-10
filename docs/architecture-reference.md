# 项目架构参考

本文承接原工作区指令中较详细的架构速查。它不是全量注入规则；修改对应模块时按需阅读。具体行为以当前源码和测试为准，历史轮次说明保存在 `docs/history/AGENTS-legacy.md`。

## 核心框架

### 进程宿主与生命周期

`launch.py` 是组合根。`core/flow.py::FlowMachine` 承担一次性初始化节点：节点用 `depends_on` 声明依赖，执行前做拓扑校验，同层节点可并发，节点自身声明重试和超时。`core/application.py::Application` 负责启动流程、等待关停信号和调用前置关停钩子。

长驻服务和有状态单例统一注册到 `core/lifecycle.py::Lifecycle`。注册顺序就是启动顺序，关停按逆序执行；调用方不要在入口脚本另写清理编排。服务状态可从 `/api/status/services` 查看，启动时间线可从 `/api/status/startup` 查看。

### 实体注册

`core/entity.py::EntityRegistry` 是工具、模型、频道适配器、服务、存储、数据库和 MCP 服务的注册与发现中心。实体模块通过注册表声明工具和分组，不应把具体实体名称硬编码到核心路由。

### 路径与配置

`core/path.py::ConfigPaths` 是配置和数据路径的唯一入口：

- `ANELF_CONFIG_DIR` 迁移 JSON 配置目录。
- `ANELF_DATA_DIR` 迁移 SQLite、便签、向量和 Cognee 数据目录，优先级高于 `app_config.json:data_root`。
- `APP_CONFIG`、`WEBUI_CONFIG`、`MCP_SERVERS`、`HEARTBEAT_CONFIG`、`TASKS_DIR`、`DB_CONNECTIONS`、`STORAGE_VOLUMES` 和 `UPLOAD_DIR` 均从 `ConfigPaths` 获取。
- 配置值支持 `${ENV_VAR}` 引用；`ANELF_<KEY>` 只覆盖生效值，不回写 JSON。

配置元数据在模块内通过 `register_configs` 声明，驱动 `/api/config/meta` 和 Web 配置中心。保存入口统一做类型转换、范围收敛、密码掩码和模型字段处理；不要在前端重新硬编码配置字段。

频道配置是外部存储后端的一个实例：`channels/<id>/config.py` 暴露 `CONFIG_MODEL`，字段进入 `adapter/<id>` 配置组，值保存到频道目录的 `channel_config.json`。频道内部写入统一使用 `set_channel_config`；手工编辑文件由配置监听器转成同一套变更通知。

### 标签、作用域和 SDK

- `[key:value]` 标签由 `core.tags` 的 `tag_label`、`etag`、`etag_all` 和 `batch_remove_tags` 构造/解析。工具唤醒只使用明确映射，不能让任意用户标签泛化激活工具。
- 会话作用域用 `build_entity_scope`、`parse_entity_scope` 和 `is_conversation_scope` 构造与校验，包含频道维度；不能手工拼接 `user_`、`group_` 或频道前缀。
- `entities/_sdk.py` 是实体层唯一的运行时桥接面，负责工具注册、LLM、媒体、embedding、通知、配置和统一出站发送；实体不得直接导入 `agent`。
- 系统注入消息在内存中带 `_source` 和 `_layer` 供追踪，发送前必须经 `normalize_for_send` 清理；这两个字段不应进入供应商请求或持久化历史。

## 思维、记忆与任务

思维周期、上下文、工具装配和缓存的现状见 [思维系统架构](mind-architecture.md)。这里补充跨模块边界：

| 子系统 | 责任 | 维护入口 |
|---|---|---|
| 判断 | Choice、Score、Noul 三种结构化判断；Jev 原生通道失败时回退普通模型，并统一输出概率/置信度 | `agent/judgment/types.py`、`engine.py`、`bridge.py` |
| 记忆 | SQLite/FTS5/embedding 存储、召回、摘要、画像、标签、关系图谱和遗忘治理 | `agent/memory/` |
| 技能 | 技能目录、事实索引、混合匹配、后台评审、恢复和可逆合并 | `agent/skills/` |
| 委托 | 子代理档案、模型候选池、工具选择器、并发预算、续跑、交接和运行日志 | `agent/delegation/` |
| 工作流 | 可恢复 DAG、journal、修订导入、审批门和断点续跑 | `agent/workflow/` |
| 任务 | 任务定义、注册、执行历史和交接；调度属于 heartbeat，不属于 task 定义 | `agent/task/` |
| 心跳 | 维护、调度、idle/scheduled/heartbeat/manual 触发和任务收尾 | `agent/heartbeat/` |
| LLM 钩子 | tool_pre、tool_post、reply_end 等事件驱动的异步 LLM 工作 | `agent/hooks_llm/` |
| 安全 | 会话令牌、威胁扫描、结果脱敏和统一权限/审计 | `agent/security/`、`core/sanitizer.py` |

记忆层不依赖思维层的具体实现；任务执行结果经统一存储和消息路由回到 scope。委托和钩子使用独立反思/后台执行，不把辅助调用混入主回复的上下文或用量账本。

## 工具分组与前置门控

分组 key 是全局英文标识，展示名由前端 i18n 提供。修改分组 key 必须同步后端注册、前端翻译和分组排序声明；实体分组的翻译放在实体自身的 `panels/locales/`，不要写进核心 locale。

常见分组和注册位置：

| group | 用途 | 主要注册处 |
|---|---|---|
| `output` | 消息发送 | `channel/output_tools.py` |
| `thinking` | 思维、压缩和短期记忆 | `agent/mind/` |
| `memory` / `graph` / `notes` | 记忆、图谱和便签 | `agent/memory/` |
| `planning` | 目标、任务和调度管理 | `agent/planning/`、`agent/task/` |
| `skills` | 技能读写与治理 | `agent/skills/tools.py` |
| `delegation` | 子代理委托、续跑和停止 | `agent/delegation/` |
| `retrieval` | 搜索、网页、仓库文档、下载和重排序 | `agent/retrieval/` |
| `audio` / `voice` | 声音、声纹、转写和实时会话 | `agent/audio/`、`agent/realtime/` |
| `ui` | 工作台和 UI 命令 | `entities/ui/` |
| `os` / `ssh` | 本地文件与 SSH | `entities/filesystem/`、`entities/ssh/` |
| `entity` / `model_control` | 实体和模型管理 | `entities/entity_query/`、`entities/model_control/` |
| `channel_ops` | 频道启停和能力桥接 | `agent/channel/` |
| `mcp_manage` / `mcp:*` | MCP 管理与动态服务工具 | `entities/mcp/` |
| `plugins` | 插件发现、安装和激活 | `core/plugins/`、`entities/plugins/` |

工具从 `always`、MCP、频道能力、消息标签、热工具、动态发现和已激活沉睡分组汇合后，依次经过 `check_fn` 门控和沉睡/激活状态机。新增工具应声明所属分组、风险等级、能力标签和必要的可用性检查。

## 前端结构与模块插件

核心页面位于 `web/frontend/src/pages/`，页面壳负责路由和布局，复杂页面拆到子面板目录；公共交互放 `components/`，请求类型集中在 `lib/api/` 与 `lib/types.ts`，路由集中在 `lib/core-routes.ts`。

频道和实体前端自持有模块目录：

- 频道使用 `channels/<id>/frontend/`，自持清单、组件、API、类型和翻译。
- 实体使用 `entities/<name>/panel.tsx` 与 `panels/`，构建脚本生成懒加载表和面板 locale 表。
- 模块通过贡献点注册导航、工作台卡片、路由和渲染器；核心不能直接导入具体实体/频道组件。
- 删除模块目录后，工具、配置、路由和文案应由热插拔/生成机制一并消失；不要把模块名称写死在核心页面。

核心页面新增或改名时检查页面文件、核心路由、侧栏兜底导航、双语 locale 和运行时导航覆盖表；模块页面按自身贡献点注册，不修改核心业务列表。

## 关键文件索引

| 文件 | 责任 |
|---|---|
| `agent/mind/mind.py` | 思维宿主、自主周期和回复入口 |
| `agent/mind/tools/think_loop.py` | 多轮 LLM、工具编排和回复循环 |
| `agent/mind/context_assembly.py` | 系统提示、上下文和执行状态组装 |
| `agent/mind/context_pipeline.py` | 上下文层声明、变动率排序和 `_layer` 标记 |
| `agent/mind/tool_assembly.py` | 工具召回、标签激活、schema 合并和门控前置 |
| `agent/mind/prompt_layers.py` | PromptCacheManager 与内容寻址缓存 |
| `agent/mind/guardrails.py` | 重复、连续失败和无进展循环防护 |
| `agent/mind/context_compressor.py` | 窗口溢出检测和压缩收尾 |
| `agent/mind/result_budget.py` | 工具结果预算与截断 |
| `agent/mind/tools/decision_executor.py` | REPLY、REFLECT、PLAN 等决策分发 |
| `agent/memory/memory_store.py` | 长期记忆 SQLite/FTS5/embedding 存储 |
| `agent/delegation/delegation_manager.py` | 子代理并发、预算、聚合和后台执行 |
| `agent/heartbeat/engine.py` | 心跳维护、任务调度和状态写入 |
| `core/context_provider.py` | 实时上下文提供者注册与收集 |
| `core/tool_gate.py` | 工具 `check_fn` 缓存和瞬态故障宽限 |
| `core/tool_errors.py` | 统一错误原因、重试标记和操作提示 |
| `agent/runtime/bootstrap.py` | 启动初始化、装配、启动和健康检查 |
| `agent/runtime/wiring.py` | 跨层 LateBinding 的统一施绑入口 |

## 前端和后端的边界检查

新增后端能力先放在 `agent` 或 `services` 的正确层，再由 `web/routers` 暴露；`web/routers` 不直接装配实体、频道或思维组件。新增前端模块通过公共 API、类型和贡献点接入，不从页面直接访问实体内部实现。

修改完本参考涉及的模块后，先运行对应模块测试，再按影响面运行 `uv run lint-imports`、类型检查和前端静态检查。