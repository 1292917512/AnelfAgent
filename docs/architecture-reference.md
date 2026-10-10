# 项目架构参考

核心框架与跨模块契约的速查。修改对应模块时按需阅读；具体行为以当前源码和测试为准。
子系统专题见各分册：[思维](mind-architecture.md) / [记忆](memory-system.md) / [模型](llm-and-models.md) /
[委托与工作流](delegation-and-workflow.md) / [任务与心跳](tasks-heartbeat-planning.md) /
[频道与会话](channels-and-scopes.md) / [实时语音](realtime-voice.md) / [声纹音频](audio-voiceprint.md) /
[视觉与能力路由](vision-and-capabilities.md) / [安全审批](security-and-approval.md) /
[运维设施](operations.md) / [前端](frontend.md)。

## 目录职责

| 目录 | 职责 | 关键约定 |
|------|------|---------|
| `core/` | 基础框架（EntityRegistry / ConfigManager / Application+Lifecycle / FlowMachine / PathManager+ConfigPaths / 标签 / 事件 / 日志 / 存储卷注册表） | 不依赖任何业务模块 |
| `agent/` | 智能体内核（Mind / LLM / Storage / Channel / Runtime / Memory / Task / Heartbeat / Planning） | 不依赖 web |
| `agent/mind/` | 思维核心（自主决策 / 多轮推理 / 跨频道感知） | 工具编排在 `mind/tools/` |
| `agent/memory/` | 语义记忆（FTS5 + Embedding 混合检索 / 便签 / 文件索引） | 不依赖 mind |
| `agent/skills/` | 技能自学习（事实索引 / 匹配 / 后台评审 / 策展；事实归系统、决策归 AI） | 文件存储在 `workspace/skills/` |
| `agent/delegation/` | 子代理调度（档案 schema / 并行 fan-out / 续跑 / 双档转向 / 运行日志） | 经 `mind.reflect()` 隔离执行 |
| `agent/workflow/` | 工作流引擎（journal 化 DAG：断点续跑 / 修订导入 / 门控重跑） | ask 步复用 DelegationManager，tool 步走统一审批门 |
| `services/workspace_context.py` | 工作区上下文注入 | 发送时把打开文件/选区/标签页渲染为消息前缀块；历史按分隔符清洗用户原文 |
| `agent/hooks_llm/` | LLM 钩子面（事件驱动的异步 LLM 工作统一注册原语） | 与心跳/任务平行；经 `mind.reflect()` 隔离执行，治理复用 |
| `agent/security/` | 安全防护（会话令牌 / 威胁扫描） | 脱敏核心在 `core/sanitizer.py` |
| `agent/task/` | 独立任务系统（定义 / 注册表 / 执行器） | 纯内容定义，不含调度逻辑 |
| `agent/heartbeat/` | 心跳调度（引擎 / 配置 / 日志 / 内置维护） | 管理何时执行任务，持久化计数器 |
| `agent/planning/` | 自主规划（目标 CRUD / 执行追踪 / 终态即清，存续决策归 AI） | 依赖 memory |
| `agent/judgment/` | 结构化判断（Choice/Score/Noul 三原语；Jev 原生通道 + 普通模型回退） | 配置走 `judgment/core` 组；引擎无构造期依赖 |
| `channels/` | 频道适配器（目录自动发现 + 热插拔 sync_channels） | 继承 BaseChannel，display_order 自声明排序 |
| `entities/` | 工具实体（目录自动发现 + 热插拔 sync_entities） | 通过 `@tool`/`entity()` 注册，经 `_sdk.py` 桥接 LLM |
| `services/` | 业务封装层（model/chat/task/heartbeat/approval/context/config/system/ui/filesystem/mcp 等；mcp 为 entities.mcp 薄门面） | 供 Web API 调用，不依赖 web |
| `web/routers/` | FastAPI 路由 | 共享模型放 `schemas.py` |
| `web/frontend/src/` | React 前端 | 页面壳组件 + 子面板目录拆分 |
| `config/` | JSON 配置 + SQLite 数据 + Markdown 便签 | 路径统一用 `ConfigPaths` |

## 依赖方向

```
web/frontend → web/routers → services → agent → core/
entities → entities._sdk → core.entity
channels/ → agent.channel → core.entity

agent.mind → agent.memory / agent.heartbeat / agent.task / agent.planning
agent.heartbeat → agent.task + agent.memory + agent.mind（调度执行）
agent.task → agent.memory（结果存储）
agent.planning → agent.memory

禁止: agent → entities/channels（仅 agent/runtime 组合根允许装配）/web/services | core → 业务层 |
services → web | channels → web/services | entities → agent/services/web/channels
（entities 经 _sdk 桥接 agent，_sdk 是唯一豁免）| web/routers → agent/entities/channels（经 services 收口）
```

以上方向由 import-linter 机械守卫（pyproject.toml `[tool.importlinter]` 八条 forbidden 契约，
`uv run lint-imports`，CI 红绿门禁；`_sdk → agent.**` 为唯一豁免通道，web/routers 契约允许经
services 的间接依赖）。注意：本地与 CI 的 import-linter 版本可能不同（新版会检出惰性
in-function import 违层），实体里写 `from agent.` 之前先想到 _sdk 桥，并以新版工具验证。

## 进程宿主与生命周期

入口 `launch.py` 是薄组合根：装配 `Application` 后 `app.run()` 三段式运行（启动流程 → 等待关停信号
→ 逆序关停）。职责分层：

- **一次性初始化步骤** → `FlowMachine`（`core/flow.py`）：`@machine.node` 注册，`depends_on`
  显式声明强依赖（上游未 SUCCESS 则 UPSTREAM_FAILED 跳过；未声明则弱链前驱保持顺序语义），
  execute 前 graphlib 拓扑静态校验（环/重名/未知依赖 → FlowCycleError 拒启动），同层节点并发执行；
  `retries`/`retry_delay`（标量或查表 list）/`timeout` 声明式重试超时；skip_on_error 只吞 FAILED，
  CRASHED（BaseException）记录后穿透
- **长驻服务** → `Lifecycle` 唯一宿主：web_server / channels / channel_supervisor / config_watcher /
  mcp_bridge 与全部单例统一 `Lifecycle.register(name, instance, on_start=..., cleanup=...)`。
  **注册顺序 = 启动顺序（start_all 正序），逆序 = 关停顺序（shutdown_all，per_timeout 单组件限时降级）**，
  注册顺序因此自然获得 drain 语义（web/频道等进水口先停，思考与资源后收）；调用方不得自行编排服务清理顺序
- **关停前置钩子** → `Application.on_pre_shutdown`（launch 注入：记忆兜底 / 日志静音 / bootstrap
  后台任务取消），在 shutdown_all 之前执行，钩子失败降级为日志

可观测：`GET /api/status/services`（Lifecycle.snapshot 注册表快照）、`GET /api/status/startup`
（Application.startup_timeline 启动时间线），前端 Dashboard「系统服务」面板展示。

## EntityRegistry（core/entity.py）

中央注册枢纽。所有模块以实体方式注册、发现、调用。

| EntityType | 用途 |
|-----------|------|
| SERVICE | LLMManager, ChannelManager |
| TOOL | entities/* 工具 |
| MODEL | LLMClient |
| ADAPTER | BaseChannel 子类 |
| STORAGE | DataCenter |
| DATABASE | MemoryStore |
| MCP_SERVER | MCPBridge |

## 路径与配置（core/path.py + core/config.py）

`ConfigPaths`（元类动态解析）是配置与数据路径的唯一入口，避免硬编码分散。默认布局
（`config/` 配置 + `config/memory/` 数据）支持整体搬迁：

- `ANELF_CONFIG_DIR` 环境变量：配置目录（纯 JSON 配置）
- `ANELF_DATA_DIR` 环境变量：数据目录（SQLite / 便签 / cognee），优先级最高
- app_config.json 的 `data_root`：数据目录，优先级低于 `ANELF_DATA_DIR`

```python
ConfigPaths.APP_CONFIG          # config/app_config.json
ConfigPaths.WEBUI_CONFIG        # config/webui.json
ConfigPaths.MCP_SERVERS         # config/mcp_servers.json
ConfigPaths.HEARTBEAT_CONFIG    # config/heartbeat.json
ConfigPaths.TASKS_DIR           # config/tasks
ConfigPaths.DB_CONNECTIONS      # config/db_connections.json（外部 SQL 连接注册表）
ConfigPaths.SQLITE_DB           # <data_dir>/data/agent.sqlite3
ConfigPaths.STORAGE_VOLUMES     # config/storage_volumes.json（存储卷位置指派）
ConfigPaths.UPLOAD_DIR          # workspace/uploads
```

配置值支持 `${ENV_VAR}` 引用语法（密钥外置到环境变量，`expand_env_refs` 展开，回写时保留引用语法）；
`ANELF_<KEY>` 环境变量可覆盖 app_config.json 中已存在的同名配置项（仅生效层，不回写文件）。

### 配置元数据体系（ConfigRegistry）

各模块以 `register_configs({group: {key: {...}}})` 声明式注册配置项，驱动
`web/routers/config_meta.py`（`GET/PUT /api/config/meta`）与前端配置中心（/config，纯数据驱动）：

- **group 规范**：全英文两级路径 `module/section`（如 `mind/core`、`memory/embedding`），
  展示名走前端 i18n；频道配置走 `adapter/<id>` 组
- **ConfigItem 展示元数据**：`advanced`（高级项折叠）、`value_type: "range"` + `min`/`max`/`step`
  （滑条+数字复合控件）、`value_type: MODEL`（统一 ModelSelect 下拉，空默认值即「跟随默认」）、
  `unit`、`tag`（条件显示标记）；`password` 类型 GET 掩码返回，PUT 提交掩码占位符保留现值
- 保存时经 `ConfigItem.coerce_value` 类型强转 + `clamp` 边界收敛（Web PUT 与 AI
  `update_entity_config` 共用同一入口）；MindConfig 字段自动路由 `save_mind_config` 双轨同步
- **新增配置项只需在所属模块注册**（含 description/advanced/unit），配置中心自动出现，
  不要在前端硬编码字段
- **AI 配置工具面**（entity 组）：`list_config_groups` / `get_entity_config`（PASSWORD 掩码）/
  `update_entity_config`（统一写入口，risk=CRITICAL 供审批拦截）

### 外部存储后端（ConfigStore 协议）

`ConfigManager.register_store(prefix, store)` 把值留在模块自身目录、同时接入统一配置面。频道配置
（`ChannelConfigStore`，见[频道分册](channels-and-scopes.md)）是该机制的实例，任何模块可用同模式。

## 标签、作用域与 SDK 桥

- **标签系统**（`core/tags.py`）：`[key:value]` 统一数据编码，函数 `tag_label`/`etag`/`etag_all`/
  `batch_remove_tags`。内置标签含 time/uid/group_id/name/channel/session_id/message_id/kind/
  media_file/reply_to/to_me/push 等。to_me 标识「群消息 @ 了机器人」、push 标识「非用户消息的实体推送」，
  出站时随元数据剥离名单一并剥离——**出站清洗名单是标签外泄的唯一防线，新增渲染标签必须同步名单**。
  消息标签的工具唤醒是显式映射（`work_memory._scan_message_tags`）：media_type/media_file →
  `media:{类型}` 工具组、channel → 频道专属工具；其余元数据标签不承载工具路由（防用户可控值泛化激活）
- **会话 scope**（`agent/messages/everything.py`）：格式 `user_{adapter}:{uid}` /
  `group_{adapter}:{gid}` / `user_{adapter}:{uid}#{chat_id}`，跨频道同号实体天然隔离。构造一律
  `build_entity_scope()`、解析一律 `parse_entity_scope()`、合法性判据 `is_conversation_scope()`，
  **禁止手工 f-string 拼接**。记忆标签同构：`user:{adapter}:{uid}`；存量数据由
  `agent/storage/scope_migrate.py` 启动自动迁移。详情见[频道分册](channels-and-scopes.md)
- **entities/_sdk.py**：工具注册 SDK + LLM 桥接层（工具装饰器归 `core.tool_registry`，上下文装饰器归
  `core.context_provider`）。entities 层通过此模块访问 LLM 能力，不直接依赖 agent：

```python
from entities._sdk import tool, entity                     # 工具注册
from entities._sdk import get_llm_manager                   # LLM 访问
from entities._sdk import push_notify                       # 向 AI 推送系统通知（[push:] 标签）
from entities._sdk import load_image_from_path              # 图片加载
from entities._sdk import get_image_content_class, get_model_type_enum
from entities._sdk import get_embedder, wake_embedding_worker, register_embedding_backlog
from entities._sdk import download_media_to_uploads         # URL 媒体落盘 uploads
from entities._sdk import execute_send_action               # 频道统一发送管道
from entities._sdk import set_default_model, get_active_llm_client, get_llm_client_class
from entities._sdk import get_session_llm_params, canonical_efforts  # 会话参数 / 思考档位表
```

## 晚绑定端口（core/latebind.py）

进程级类型化晚绑定：端口由消费方所在层声明（`LateBinding[T]`，名称全局唯一、`[None]` 施绑合法），
`agent/runtime/wiring.py::wire_runtime()` 在 assemble 尾部统一施绑（mind 工具组 / 思维子系统实例 /
记忆存储族 / 会话数据 / embedding worker / cognee 可选后端 / sticker worker / agent→entities
函数桥；多依赖端口以 NamedTuple 承载如 `MemoryToolDeps`/`SkillToolDeps`/`WorkspacePathFns`），
check_health 经 `assert_wired()` 把漏接线暴露为启动红字；未施绑 `get()` 抛 WireError。

**准入**：仅限 import 时工具注册拿不到构造参数 / 循环初始化 / 跨层桥三种成因，`set()` 只许组合根
调用；DI 容器与装饰器注册表方案已否决（见[设计决策](design-decisions.md)）。

## 模块热插拔（entities/hotplug.py + agent/channel/hotplug.py）

reconcile 对账范式（对齐插件装卸载）：监听目录结构（子目录 + tools.py/adapter.py 标记文件快照，
防抖合并）或手动触发（实体/频道/工具页「热同步」按钮，`POST /tools/reload` / `/adapters/reload`）：

- **新增目录**即时注册（工具/分组/配置 schema/lifecycle/路由）
- **删除目录**完整拆除（注册表 unregister → 独占分组回收（含 manifest/权重）→ ConfigRegistry/Store
  回收 → Lifecycle 组件注销 → sys.modules 清理 → 路由摘除）
- **手动刷新**（reload_existing=True）对存续模块做代码热更（实体先按归属注销旧工具再 re-import，
  被删除的工具不回归；频道停→清模块→重建，连接断一次）
- 路由挂载/摘除经 EVENT_MODULE_ADDED/REMOVED 事件总线通知 web 层；监听开关
  `hotplug_watch_enabled`（system/hotplug 组）；失败目录不进已知集合下轮自动重试；两域各自单飞护栏防并发同步

## 存储卷（core/storage_volume.py）

所有持久化数据统一登记为存储卷（8 卷：agent 主库 / memory / skill_vectors / stickers / audio /
share 六个 SQLITE + cognee 树 + 便签树），各存储模块 import 时自注册 VolumeDescriptor（惰性
default_path 保持测试隔离）；同族库路径均由 `main_sqlite_path()`（env > 项目根 ConfigPaths.SQLITE_DB）
派生 stem，放在 core 使 entities 无需依赖 agent。路径解析优先级：env_override > 位置指派
（`config/storage_volumes.json`，cognee 卷转发 cognee.json data_root）> 模块默认派生——
**无指派文件时所有路径与历史一致，数据零移动**。

- 能力按形态派生：SQLITE 全量（备份/恢复/迁移/SQL 导出导入）、cognee 树无 SQL 传输、便签树仅备份/恢复
- 备份：SQLite 走 Backup API 在线热备（`services.database.online_sqlite_backup` 唯一实现）、树走 tgz；
  保留数 `volume_backup_retention`（storage/backup 组，默认 5）自动清理
- 恢复与迁移均为「校验 + 拷贝 + 指派/标记 + 重启生效」：恢复写 pending 标记
  （`<data_dir>/backups/volumes/.pending-restore.json`），bootstrap `init_storage` 最早消费（任何连接
  打开前交换文件；旧库 -wal/-shm 必清除防回放）
- 外部 SQL 为备份/转移通道：`SqlTransferClient` 做 DDL 方言翻译 + rowid 窗口流式批量传输，导出登记
  清单表 `_anelf_export`、导入仅认清单；派生索引（FTS5/vec0 影子表）不传输，导入后由各存储建表逻辑重建
- Web 面板：数据管理页「存储卷」Tab，API 前缀 `/database/volumes`；目录遍历/占用统一走
  `core.file_utils.walk_files/directory_size`

## 工具分组体系

### group key 规范

分组 key 是全局英文标识，前端经 i18n 翻译展示。修改分组名必须同步三处：

1. 后端 `@tool(group=...)` / `@deferred_tool(group=...)` / `entity(group, ...)` / `activate_group(...)`
2. 前端翻译：核心分组（agent 层）改 `i18n/locales/{zh,en}/tools.json` 的 `groups` 对象；
   **实体分组改其实体 `panels/locales/{zh,en}.json` 的 `_registry.groups` / `_registry.configSections`**
   （启动时 eager 自注册，核心 locale 不写实体条目）
3. 组归属模块的 `EntityRegistry.register_group_order(group, 权重)` 自声明（实体在 `entity_manifest(order=)`，
   agent 侧在工具模块顶层调用处）

排序权重单一权威在 EntityRegistry，core 不内置业务分组名表；权重按类别分段（0-9 输出思维 /
10-19 记忆 / 20-29 规划执行 / 30-49 能力感知 / 50-59 模型运维 / 60-69 管理集成 / 70-79 界面会话），
未注册 1000 字母序排尾；消费面统一 `group_sort_key`。频道经类属性 `display_order`（默认 100）自声明。

### 当前分组索引

| group key | 中文名 | 注册文件 | tags |
|---|---|---|---|
| `output` | 消息输出 | `channel/output_tools.py` | always |
| `voice` | 语音会话与本地模型 | `agent/realtime/tools.py` + `agent/model_assets.py` | always/core |
| `memory` | 记忆管理 | `agent/memory/tools.py` | always/core/heartbeat |
| `graph` | 关系图谱 | `agent/memory/graph/tools.py`（含 graph_curation_agenda 治理议程） | always/core/heartbeat |
| `notes` | 便签记忆 | `agent/memory/notes.py` | core/heartbeat |
| `thinking` | 思维工具 | `agent/mind/mind.py` + `agent/mind/tool_activation.py` + `agent/mind/context_compressor.py` + `agent/mind/tools/short_term_tools.py` | always |
| `planning` | 目标规划 | `agent/planning/tools.py` + `agent/task/tools.py` | planning/goal/heartbeat |
| `skills` | 技能 | `agent/skills/tools.py` | always |
| `delegation` | 子代理 | `agent/delegation/delegate_tool.py` | always |
| `ui` | 界面交互 | `entities/ui/tools.py`（经 event_bus `EVENT_UI_COMMAND` → 聊天 SSE 桥接） | always |
| `retrieval` | 检索 | `agent/retrieval/tools.py` + `providers/`（能力×提供者矩阵）+ `fetcher.py` + `rerank.py` | always/core/web |
| `minimax` | MiniMax | `entities/minimax/`（组件包，无 AI 工具） | — |
| `os` | 操作系统 | `entities/filesystem/tools.py` | media:file |
| `ssh` | SSH 远程管理 | `entities/ssh/tools.py` | —（整组沉睡） |
| `audio` | 声音 | `agent/audio/tools.py` + `agent/audio/gen_tools.py` | always/core/media:voice/media:audio |
| `audiosync` | 音源同步 | `entities/audiosync/tools.py` | always/core |
| `vault` | 密码本 | `entities/vault/tools.py` | —（整组沉睡；reveal/totp/delete 标 risk=CRITICAL） |
| `sticker` | 表情包 | `entities/sticker/tools.py` | always/media:image |
| `dashscope` | 阿里百炼语音 | `entities/dashscope/` | — |
| `environment` | 环境信息 | `entities/system/tools.py` | — |
| `model_control` | 模型控制 | `entities/model_control/tools.py` | core |
| `ollama` | Ollama | `entities/model_control/tools.py` | — |
| `channel_ops` | 频道操作 | `agent/channel/tool_bridge.py` + `agent/channel/manage_tools.py`（启停 risk=CRITICAL） | capability/channel_id/core |
| `entity` | 实体管理 | `entities/entity_query/tools.py` | always/core |
| `mcp_manage` | MCP 管理 | `entities/mcp/bridge.py`（动态） | — |
| `mcp:*` | MCP 服务 | 动态注册 | — |
| `plugins` | 插件管理 | `entities/plugins/tools.py` + `activation.py` + `router.py` + 核心引擎 `core/plugins/` | — |
| `ai_desktop` | AI 桌面 | `entities/ai_desktop/tools.py` + `modules/calendar/tools.py` | — |
| `vision` | 视觉感知与生成 | `agent/vision/`（deferred 组 bootstrap 激活） | always/core/media:image/media:video |
| `devops` | 运维管理 | `entities/devops/tools.py` | — |
| `code` | 代码编排 | `entities/codebox/`（沉睡分组，order 27） | — |

## 关键文件索引

| 文件 | 职责 |
|------|------|
| `agent/mind/mind.py` | 思维核心、自主循环 |
| `agent/mind/prefrontal_cortex.py` | 工作记忆门面（组合 work_memory / tool_assembly / context_assembly） |
| `agent/mind/autonomous.py` | 决策类型、态势模型、元决策 prompt |
| `agent/mind/prompt_layers.py` | Prompt 分层缓存（stable/context/volatile + PromptCacheManager） |
| `agent/mind/context_pipeline.py` | 上下文构建管线（@context_block 声明层+变动率 / 变动率排序组装） |
| `agent/mind/guardrails.py` | 工具调用守卫（死循环检测 warn/block/halt） |
| `agent/mind/context_compressor.py` | 上下文压缩（溢出检测 + 保头保尾 + LLM 摘要） |
| `agent/mind/result_budget.py` | 工具结果预算截断（按模型窗口动态计算） |
| `agent/mind/tool_activation.py` | 工具沉睡/激活状态机（activate_tool_group） |
| `agent/mind/tools/think_loop.py` | 统一思维循环（多轮 LLM + 工具编排 + reply_entry/reply_loop） |
| `agent/mind/tools/reply_finalize.py` | 思维收尾块（finish_think/complete_reply/执行摘要） |
| `core/tool_results.py` | 工具结果宽松 JSON 解析 + 错误文本提取 |
| `agent/mind/message_schema.py` | 内部消息契约 + 发送边界规整 + 推理字段回传 |
| `agent/llm/resilience/classifier.py` | LLM 错误分类（驱动重试/压缩/回退策略） |
| `agent/llm/reasoning.py` | 思考等级单一权威（7 级规范词汇 + 档位表 + 下发通道分派） |
| `agent/llm/prompt_cache.py` | Anthropic 缓存断点唯一权威 |
| `agent/llm/retry.py` | 自适应退避（指数 + 抖动 + Retry-After 采信） |
| `agent/security/session_token.py` | 一次性会话令牌（防注入伪造历史） |
| `agent/security/threat_scanner.py` | 威胁模式扫描（prompt 注入检测） |
| `core/sanitizer.py` | 敏感信息脱敏（API Key/Token/密码） |
| `core/tool_gate.py` | 工具门控（check_fn TTL 缓存 + 瞬态宽限） |
| `core/tool_errors.py` | 工具错误统一设施（tool_error / error_from_exception + ErrorCause） |
| `agent/skills/skill_store.py` | 技能存储（use/match 信号分离 + merge 可逆合并 + restore） |
| `agent/skills/skill_index.py` | 技能事实索引（向量/相似度/写入诊断/库健康） |
| `agent/skills/skill_matcher.py` | 技能匹配（多查询车道 + 混合评分 + 近重复折叠） |
| `agent/skills/catalog.py` | 技能目录（active+stale 全量进 stable 工具块） |
| `agent/skills/curator.py` | 技能策展（重力：闲置降级/归档 + 试用期快筛） |
| `agent/skills/sources/` | 外部技能源（SkillSource 抽象 + 注册表热插拔） |
| `agent/voice/turn_detection.py` | 端点检测协议与梯队实现 |
| `agent/voice/preprocess.py` | 麦克风输入预处理链（谱减降噪 → AGC → 限幅） |
| `agent/model_assets.py` | 本地模型资产注册表与下载管理 |
| `agent/delegation/profile.py` | 子代理档案 schema 单一权威 |
| `agent/delegation/sub_agent.py` | 子代理（角色 + 深度限制 + facets 消费 + 续跑） |
| `agent/delegation/delegation_manager.py` | 委托调度（并发/预算/聚合/后台/续跑/用量归集） |
| `agent/delegation/journal.py` | 委托运行日志（进度流/transcript/崩溃 ledger） |
| `agent/delegation/recovery.py` | 委托崩溃恢复 |
| `agent/delegation/delegate_tool.py` | delegate_task / send_to_agent / follow_up_agent / 后台任务工具组 |
| `agent/mind/work_memory.py` | 工作记忆数据面（消息队列 / 待办 / 短期记忆 / 态势路由） |
| `agent/mind/tool_assembly.py` | 工具装配（召回 / tag 激活 / schema 合并门控） |
| `agent/mind/context_assembly.py` | 上下文组装（系统提示 / 分层缓存 / 执行上下文） |
| `agent/mind/tools/decision_executor.py` | 决策执行分发（REPLY/REFLECT/PLAN 等） |
| `agent/mind/push.py` | 实体推送中枢 PushHub |
| `agent/capabilities.py` | 能力提供者路由框架 |
| `agent/retrieval/tools.py` | 检索工具面 |
| `agent/memory/memory_store.py` | 长期记忆存储（SQLite + FTS5 + Embedding） |
| `agent/memory/doc_extract.py` | 文档文本提取（PDF/Word/Excel/PPT/纯文本） |
| `agent/memory/probe.py` | 异步深探 + 召回账本 |
| `agent/memory/recall_format.py` | 召回行格式化权威 |
| `agent/memory/graph/store.py` | 关系图谱权威存储 |
| `agent/memory/graph/tools.py` | 关系图谱工具组 |
| `agent/memory/store/tag_intel.py` | 标签智能（df/共现/提及词表 TTL 缓存） |
| `agent/storage/scope_migrate.py` | scope 迁移（user_version 幂等 + 自动备份） |
| `agent/memory/tools.py` | 记忆工具（memorize / recall / forget） |
| `agent/memory/notes.py` | 便签文件系统 |
| `agent/task/model.py` | 任务数据模型（TaskDefinition / TaskResult） |
| `agent/task/registry.py` | 任务注册表（config/tasks/*.json） |
| `agent/task/executor.py` | 任务执行器（LLM 调用 + 结果存储 + lean 上下文） |
| `agent/task/history.py` | 任务执行历史 |
| `agent/task/tools.py` | 任务/调度自管理工具 |
| `agent/heartbeat/engine.py` | 心跳调度引擎 |
| `agent/planning/tools.py` | 规划工具（create_goal/update_goal/delete_goal） |
| `agent/runtime/bootstrap.py` | 启动流程（初始化 → 组装 → 启动 → 健康检查） |
| `agent/runtime/state_restore.py` | 启动状态恢复 |
| `agent/runtime/singleton.py` | AgentRuntime 全局单例 |
| `agent/channel/manager.py` | 频道管理（register / route / 动态加载 / 启停意图落盘） |
| `agent/channel/config.py` | 频道配置统一接入 |
| `agent/channel/tool_bridge.py` | 频道工具桥接（@channel_tool） |
| `agent/channel/context.py` | 当前会话频道 ContextVar |
| `web/routers/config_meta.py` | 统一配置元数据 API |
| `web/routers/workspace.py` | 工作区文件 API |
| `web/routers/database.py` | 数据管理 API |
| `web/routers/search.py` | 全局搜索聚合 API |
| `services/db_connections.py` | 外部 SQL 连接（注册表 + 只读适配器 + 写通道） |
| `services/data_migration.py` | 数据目录迁移 |
| `core/path.py` | PathManager + ConfigPaths 动态路径 |
| `core/lifecycle.py` | 长驻服务与单例统一宿主 |
| `core/application.py` | 进程宿主 Application |
| `core/flow.py` | 异步流程状态机 FlowMachine |
| `core/latebind.py` | 晚绑定端口原语 |
| `agent/runtime/wiring.py` | 运行时统一施绑点 |
| `core/crash_report.py` | 崩溃状态设施（crash_state.json + macOS .ips 关联） |
| `agent/mind/crash_recovery.py` | 崩溃尾部修复 |
| `core/context_provider.py` | 上下文提供者注册表（实时快照注入；priority=变动率排序：10-19 状态级 / 20-29 摘要级 / 30-39 会话操作态势 / 40+ 实时快照；两道门控热读取——inject_key 注入开关（约定 `<组名>_context_inject`）与 group 实体启停联动） |

## 修改后检查

先运行对应模块测试，再按影响面运行 `uv run lint-imports`、类型检查和前端静态检查。新增后端能力
先放在 `agent` 或 `services` 的正确层，再由 `web/routers` 暴露；`web/routers` 不直接装配实体、
频道或思维组件。
