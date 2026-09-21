---
description: "AnelfAgent 项目指令 — 开发规范与架构速查（对所有提示全量注入）"
---

# AnelfAgent — 项目指令

> **定位说明**：本文件是供 AI 代码编辑器（ZCode）读取的**工作区指令文件**，
> 用于向编辑器描述本项目的开发规范与架构，**不属于 AnelfAgent 产品的运行时代码或交付内容**，
> 不会被程序加载，也不影响任何运行逻辑。修改本文件仅改变编辑器对项目的理解。

---

## 一、开发规范

- 先主动审查所有依赖与相关文件，再规划实现方案
- 禁止假定、猜测任何实现
- 除非用户要求否则保持最小化修改
- 对参考信息有困惑时主动提问
- 永远保持项目工程化、整洁性、可维护性，合理拆分功能模块
- 执行严格地类型注解开发！慎用类型断言！
- 尽可能使用主流的成熟的框架和组件开发，非必要不要自己造轮子
- 始终处理因为修改产生的衍生 Linter 警告/错误，非必要禁止忽略它们！！！
- 修改完成后，审查一遍所有依赖的逻辑是否存在且正确，是否存在未处理的 Linter 警告/错误！
- 永远以严谨负责的态度完成任务，认真思考任务要求，处理好所有细节！以最高标准要求自己的代码！
- 注释不要添加版本和修改描述，保持注释只形容类或函数本身，非必要不用修改备注
- 要求代码简洁高效，不要过度设计，要求代码优雅不为局部做妥协，解决根本问题

---

## 二、架构速查

### 目录职责

| 目录 | 职责 | 关键约定 |
|------|------|---------|
| `core/` | 基础框架（EntityRegistry / ConfigManager / Application+Lifecycle / FlowMachine / PathManager+ConfigPaths / 标签 / 事件 / 日志 / 存储卷注册表） | 不依赖任何业务模块 |
| `agent/` | 智能体内核（Mind / LLM / Storage / Channel / Runtime / Memory / Task / Heartbeat / Planning） | 不依赖 web |
| `agent/mind/` | 思维核心（自主决策 / 多轮推理 / 跨频道感知） | 工具编排在 `mind/tools/` |
| `agent/memory/` | 语义记忆（FTS5 + Embedding 混合检索 / 便签 / 文件索引） | 不依赖 mind |
| `agent/skills/` | 技能自学习（事实索引 / 匹配 / 后台评审 / 策展；事实归系统、决策归 AI） | 文件存储在 `workspace/skills/` |
| `agent/delegation/` | 子代理调度（档案 schema / 并行 fan-out / 续跑 / 双档转向 / 运行日志） | 经 `mind.reflect()` 隔离执行 |
| `agent/hooks_llm/` | LLM 钩子面（事件驱动的异步 LLM 工作统一注册原语；技能评审/任务事件触发/实体钩子经此并行拉起） | 与心跳/任务平行；经 `mind.reflect()` 隔离执行，治理复用 |
| `agent/security/` | 安全防护（会话令牌 / 威胁扫描） | 脱敏核心在 `core/sanitizer.py` |
| `agent/task/` | 独立任务系统（定义 / 注册表 / 执行器） | 纯内容定义，不含调度逻辑 |
| `agent/heartbeat/` | 心跳调度（引擎 / 配置 / 日志 / 内置维护） | 管理何时执行任务，持久化计数器 |
| `agent/planning/` | 自主规划（目标 CRUD / 执行追踪 / 终态即清，存续决策归 AI） | 依赖 memory |
| `channels/` | 频道适配器（目录自动发现 + 热插拔 sync_channels） | 继承 BaseChannel，display_order 自声明排序 |
| `entities/` | 工具实体（目录自动发现 + 热插拔 sync_entities） | 通过 `@tool`/`entity()` 注册，通过 `_sdk.py` 桥接 LLM |
| `services/` | 业务封装层（model/chat/task/heartbeat/approval/context/config/sticker/system/ui/filesystem/mcp 等；mcp 为 entities.mcp 薄门面） | 供 Web API 调用，不依赖 web |
| `web/routers/` | FastAPI 路由 | 共享模型放 `schemas.py` |
| `web/frontend/src/` | React 前端 | 页面壳组件 + 子面板目录拆分 |
| `config/` | JSON 配置 + SQLite 数据 + Markdown 便签 | 路径统一用 `ConfigPaths` |

### 依赖方向

```
web/frontend → web/routers → services → agent → core/
entities → entities._sdk → core.entity
channels/ → agent.channel → core.entity

agent.mind → agent.memory / agent.heartbeat / agent.task / agent.planning
agent.heartbeat → agent.task + agent.memory + agent.mind（调度执行）
agent.task → agent.memory（结果存储）
agent.planning → agent.memory

禁止: agent → web/services | core → 业务层 | services → web | channels → web/services | entities → agent/services/web/channels（entities 经 _sdk 桥接 agent，_sdk 是唯一豁免）| web/routers → agent/entities/channels（经 services 收口）

以上方向由 import-linter 机械守卫（pyproject.toml `[tool.importlinter]` 六条 forbidden 契约，`uv run lint-imports`，CI python job 红绿门禁；`_sdk → agent.**` 为唯一豁免通道，web/routers 契约允许经 services 的间接依赖）
```

### 核心系统

#### 进程宿主（core/application.py + core/lifecycle.py + core/flow.py）

入口 `launch.py` 是薄组合根：装配 `Application` 后 `app.run()` 三段式运行
（启动流程 → 等待关停信号 → 逆序关停）。职责分层：

- **一次性初始化步骤** → `FlowMachine`（`core/flow.py`）：`@machine.node` 注册，
  `depends_on` 显式声明强依赖（上游未 SUCCESS 则 UPSTREAM_FAILED 跳过；未声明则
  弱链前驱保持顺序语义），execute 前 graphlib 拓扑静态校验（环/重名/未知依赖 →
  FlowCycleError 拒启动），同层节点并发执行；`retries`/`retry_delay`（标量或查表 list）/
  `timeout` 声明式重试超时；skip_on_error 只吞 FAILED，CRASHED（BaseException）记录后穿透
- **长驻服务** → `Lifecycle` 唯一宿主：web_server / channels / channel_supervisor /
  config_watcher / mcp_bridge 与全部单例统一 `Lifecycle.register(name, instance,
  on_start=..., cleanup=...)`。**注册顺序 = 启动顺序（start_all 正序），逆序 = 关停顺序
  （shutdown_all，per_timeout 单组件限时降级）**，注册顺序因此自然获得 drain 语义
  （web/频道等进水口先停，思考与资源后收）；调用方不得自行编排服务清理顺序
- **关停前置钩子** → `Application.on_pre_shutdown`（launch 注入：记忆兜底 / 日志静音 /
  bootstrap 后台任务取消），在 shutdown_all 之前执行，钩子失败降级为日志

可观测：`GET /api/status/services`（Lifecycle.snapshot 注册表快照）、
`GET /api/status/startup`（Application.startup_timeline 启动时间线），
前端 Dashboard「系统服务」面板展示。

#### EntityRegistry（core/entity.py）

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

#### ConfigPaths（core/path.py）

配置路径集中管理（元类动态解析），避免硬编码分散。默认布局与历史一致
（`config/` 配置 + `config/memory/` 数据），支持整体搬迁：

- `ANELF_CONFIG_DIR` 环境变量：配置目录（纯 JSON 配置）
- `ANELF_DATA_DIR` 环境变量：数据目录（SQLite / 便签 / cognee），优先级最高
- app_config.json 的 `data_root`：数据目录，优先级低于 `ANELF_DATA_DIR`

```python
ConfigPaths.APP_CONFIG          # config/app_config.json（随 ANELF_CONFIG_DIR 变化）
ConfigPaths.WEBUI_CONFIG        # config/webui.json
ConfigPaths.MCP_SERVERS         # config/mcp_servers.json
ConfigPaths.HEARTBEAT_CONFIG    # config/heartbeat.json
ConfigPaths.TASKS_DIR           # config/tasks
ConfigPaths.DB_CONNECTIONS      # config/db_connections.json（外部 SQL 连接注册表）
ConfigPaths.SQLITE_DB           # <data_dir>/data/agent.sqlite3（随 ANELF_DATA_DIR/data_root 变化）
ConfigPaths.STORAGE_VOLUMES     # config/storage_volumes.json（存储卷位置指派，见运行时机制表「存储卷」）
ConfigPaths.UPLOAD_DIR          # workspace/uploads
```

配置值支持 `${ENV_VAR}` 引用语法（密钥外置到环境变量，core/config.py 的
`expand_env_refs` 展开，ConfigManager 与 LLMManager 回写时保留引用语法）；
`ANELF_<KEY>` 环境变量可覆盖 app_config.json 中已存在的同名配置项
（仅生效层，不回写文件）。

#### 配置元数据体系（core/config.py ConfigRegistry）

各模块以 `register_configs({group: {key: {...}}})` 声明式注册配置项，驱动
`web/routers/config_meta.py`（`GET/PUT /api/config/meta`）与前端配置中心（/config，纯数据驱动）：

- **group 规范**：全英文两级路径 `module/section`（如 `mind/core`、`memory/embedding`、
  `network/proxy`、`entity/ssh`），展示名走前端 i18n `config.json` 的 `modules.*` / `sections.*`；
  频道配置走 `adapter/<id>` 组，与其他组同一注册体系（值存频道目录文件，见下「频道配置统一接入」）
- **ConfigItem 展示元数据**：`advanced`（高级项，UI 折叠；`*_enabled` 主开关等保持基础项）、
  `value_type: "range"` + `min`/`max`/`step`（滑条+数字复合控件）、`unit`（单位展示）、
  `tag`（条件显示标记，如频道 ws_mode 的 forward/reverse 卡片过滤）；
  `password` 类型 GET 掩码返回（`mask_secret`），PUT 提交掩码占位符保留现值
- 保存时经 `ConfigItem.coerce_value` 类型强转 + `clamp` 边界收敛（Web PUT 与 AI
  `update_entity_config` 共用同一入口）；MindConfig 字段自动路由 `save_mind_config` 双轨同步
  （AI 侧经 `_sdk.save_config_value` 桥接同纪律）
- 新增配置项只需在所属模块注册（含 description/advanced/unit），配置中心自动出现，
  不要在前端硬编码字段
- **AI 配置工具面**（entity 组）：`list_config_groups`（按分类浏览全部配置组）/
  `get_entity_config`（实体+频道模糊解析，未启用频道经组名直查；PASSWORD 掩码）/
  `update_entity_config`（统一写入口，risk=CRITICAL 供审批拦截）

#### 频道配置统一接入（agent/channel/config.py）

频道配置与全系统同一套注册与读写面，值文件留在频道目录（模块自持有、可插拔）：

- **声明约定**：`channels/<id>/config.py` 暴露标准符号 `CONFIG_MODEL`（ChannelConfig
  子类，pydantic 模型即唯一声明源；`Field(description=...)` + `json_schema_extra`
  直通 value_type/options/advanced/unit/min/max/step/tag；`Literal` 注解自动转 ENUM）。
  适配器 `from .config import XxxConfig`，禁止在 adapter.py 再定义配置类（双份真相）
- **键前缀与值存储**：统一配置面的键为 `<id>_<field>`（组 `adapter/<id>`），值存
  `channels/<id>/channel_config.json`（文件内字段名无前缀，格式与历史一致）——
  `ChannelConfigStore` 经 `ConfigManager.register_store(<id>_, store)` 接入，
  get/set/has/save 自动路由，app_config.json 不存频道键；env 覆盖
  `ANELF_<ID>_<FIELD>` 由 store 读取时生效（与历史语义一致）
- **外部存储后端是通用机制**：core/config.py `ConfigStore` 协议 +
  `ConfigManager.register_store(prefix, store)`，任何模块都可用同模式把值
  留在自己目录下，同时接入统一注册面
- **注册时机**：bootstrap `register_channels` 节点先调 `register_channel_schemas()`
  ——扫描 channels/ 注册各频道 store + schema（仅子类声明字段，ChannelConfig 基类
  通用字段不进配置面），幂等
- **热更双路径**：进程内写入（Web /config/meta、AI update_entity_config、频道内部
  `set_channel_config`）经 ConfigManager.set 命中前缀监听器即时热更；手工编辑
  channel_config.json 经 ConfigWatcher mtime 轮询 → store diff →
  `ConfigManager.notify_external` 上报同一批监听器（无 diff 不重复触发）
- **频道内部写配置**：一律 `set_channel_config(<id>, field=value)`（登录回填/直播开关等），
  禁止直写文件；频道需要对变更做 diff 应用时覆盖 `_on_config_changed`（参考
  acfun/bilibili 委托 reload_config 的写法）

#### 标签系统（core/tags.py）

`[key:value]` 统一数据编码。函数：`tag_label` / `etag` / `etag_all` / `batch_remove_tags`。
内置标签：time / uid / group_id / name / channel / session_id / message_id / kind / media_file / reply_to / to_me / push 等
（to_me 标识"群消息 @ 了机器人"，仅 @ 时渲染，无此标签的群消息 = 群员间对话而非对她的请求；
push 为实体推送通知标签，标识"非用户消息"；两者出站时随元数据标签一并剥离——
kind/speaker_scope/face_scope 同属元数据剥离名单，出站清洗名单是标签外泄的唯一防线，新增渲染标签必须同步名单）。
消息标签的工具唤醒是显式映射（`work_memory._scan_message_tags`）：media_type/media_file → `media:{类型}` 工具组、
channel → 该频道专属工具；其余元数据标签不承载工具路由（防用户可控值泛化激活）。

#### 会话 scope 格式（agent/messages/everything.py）

entity_scope 含频道 adapter 维度，跨频道同号实体（如 QQ uid 与 WebUI uid）天然隔离：
`user_{adapter}:{uid}` / `group_{adapter}:{gid}` / `user_{adapter}:{uid}#{chat_id}`
（如 `user_qq:123`、`user_webui:web_user#chat_1`）。构造一律用 `build_entity_scope()`，
解析一律用 `parse_entity_scope()`（返回 scope_type/adapter/base_id/session_id，兼容无 adapter 旧格式），
会话合法性判据用 `is_conversation_scope()`（可路由 = user_/group_ 且含频道前缀；投递面守卫：
待回复队列/持久化提醒/一次性通知拒绝不可路由 scope），禁止手工 f-string 拼接。
元决策 decide 的 target 是 LLM 自由文本，一律经 `decision_executor.normalize_target_scope`
规范化后才可用：规范 scope 直接采信，旧格式/裸 id/频道前缀（`qq:123`、`qq_123`）形态按
PFC `known_scopes()`（待回复队列 + 路由登记）唯一解析回填，无匹配或歧义时 REPLY 回退
`pop_next_reply_target`、PROACTIVE 放弃告警——畸形 target 不再拼出影子会话
（2026-09-14~16 内部触发轮操作摘要落 `qq:qq:`/裸 id 影子 scope 事故的根因收口）。
记忆标签同构：`user:{adapter}:{uid}`。存量数据由
`agent/storage/scope_migrate.py` 启动时自动迁移（`legacy_adapter_default` 配置归属频道，默认 qq）；
别名实体的跨频道历史合并由 `alias_merge_history` 配置（默认开）。

#### entities/_sdk.py

工具注册 SDK + LLM 桥接层。entities 层通过此模块访问 LLM 能力，不直接依赖 agent：

```python
from entities._sdk import tool, entity                     # 工具注册
from entities._sdk import get_llm_manager                   # LLM 访问
from entities._sdk import push_notify                       # 向 AI 推送系统通知（[push:] 标签，手机弹窗语义）
from entities._sdk import load_image_from_path              # 图片加载
from entities._sdk import get_image_content_class, get_model_type_enum  # 类型获取
from entities._sdk import get_embedder, wake_embedding_worker, register_embedding_backlog  # 向量设施
from entities._sdk import download_media_to_uploads         # URL 媒体落盘 uploads
from entities._sdk import execute_send_action               # 频道统一发送管道（校验/目标解析/结果归因）
from entities._sdk import set_default_model, get_active_llm_client, get_llm_client_class  # 模型控制
from entities._sdk import get_session_llm_params, canonical_efforts  # 会话参数覆盖 / 思考档位表
```

### 思维系统

#### 自主循环

```
消息入队 → PFC.add_task → _autonomous_cycle
  → 心跳: heartbeat_engine.tick()（维护 + 调度任务）
  → 收集态势（消息/任务/记忆/目标/通道）
  → 简单场景 fast-path REPLY / 复杂场景元决策
  → REPLY → 记忆召回 → _think_loop（while 迭代 + 工具调用）
  → end_reply → 完成
```

#### 心跳引擎（HeartbeatEngine）

```
tick() 单次心跳：
  1. 内置维护：日志合并 + 实体计数持久化 + 记忆健康检查 + 实体画像分析
     + 空闲自动折叠（连续 conversation_fold_idle_beats 个心跳无新消息
     且积压 ≥ conversation_fold_idle_min 的会话 → 后台折叠 + 折后预热，
     把缓存断点移到无人时段）
     + 目标停滞概况（situation.stale_goal_line：updated_at ≥7 天未更新的活跃
     目标一行事实，呈现给 AI 决策续期/删除——目标规划适应长期工作，
     系统不做基于时间的自动清理；目标关闭只由完成事实驱动：update_goal
     终态即删、全部步骤完成后自动收口、delete_goal 显式删除）
  2. 遍历 task_schedules，递增 beat_count
  3. 选取一个到期任务 → TaskExecutor.run() → 结果记入心跳日志
  4. 持久化计数器到 config/heartbeat.json
```

四种触发模式：heartbeat（每 N 次心跳）/ scheduled（每天指定时间）/
idle（连续 N 次心跳无思考活动后，全局仅一条）/ manual（仅手动）。

**心跳间隔单一真源 = `mind.heartbeat_interval`**（mind/core 配置组）：
`assistant._heartbeat_loop` 实际按其休眠，且 sleep 可被 ConfigManager 变更
监听唤醒——间隔热更后立即按新值重排休眠，不等旧周期到期（同值同步不唤醒，
不重置 sleep 进度）。heartbeat.json 不存间隔副本（双源写读分离曾是"配置改了
不生效 + 两处显示分裂"的根因）：引擎态势文案/调度节奏折算/scheduled 跨午夜
窗口与 Web 心跳页展示统一读 `heartbeat/config.current_interval_seconds()`；
Web 心跳页保存 interval_seconds 经 `HeartbeatService._save_interval_seconds`
路由 `save_mind_config`（与 PUT /config/meta 同 coerce+clamp 纪律，下限 60s）。

**scheduled 槽位去重以执行历史为唯一事实源**：调度配置只存定义与节拍计数，
不记 last_run 标记——`task_history.get_last_good_runs()`（终态即原子落盘，
success/no_output 计入、error 保留重试）给出各任务最近一次非失败执行时间戳，
调度重绑、执行期 reload 换配置对象、进程重启/取消都不再导致重复追跑
（2026-09 两轮事故根因：标记混存于调度对象，set_task_schedule 重建条目与
tick 缓存引用都会把它抹掉）。判定采用 occurrence 锚点语义（对齐 dsh
resolveEveryOccurrence）：每个调度时刻取最近一个已到期 occurrence（今日已过点，
或昨日深夜跨午夜窗口点）与上次执行时间戳比较——多时刻槽位逐点独立
（09:00 跑过不抑制 21:30）、停机只补最近一次不枚举积压、跨午夜补跑不吞今日
正当槽位。连续失败达上限的定时任务经 `_task_giveup_dates` 台账当日放弃、跨日
自动恢复；`HeartbeatConfig.set_schedule` 重绑时继承同名条目 beat_count（定义
调整不抹进度），tick 收尾按任务名现取条目复位计数（不写执行前缓存的引用），
无调度任务的失败/放弃台账随 `_prune_stale_runtime_state` 对账清理。

**idle 空闲调度**：计数维度是"距上次思考的连续空闲心跳数"——`mind.last_activity_ts`
锚点（`reply()`/`reflect()` 入口刷新，覆盖对话/任务/子代理/反思，**含 idle 任务自身**；
心跳元决策的 LLM 调用不经 reflect 故不计）。本 tick 无确定性到期任务时才评估触发，
空闲窗口让位给 scheduled/heartbeat；元决策 REFLECT 不再立即执行，改为
`engine.mark_reflection_pending(reason)` 登记，由 idle 任务在空闲窗口消费（原因经
`TaskExecutor.run(extra_note=...)` 尾部追加注入，缓存前缀不动）。思考刷新计数保证
idle 天然单例串行——写入侧 `validate_schedules` 强校验仅一条，AI/Web 双路径同规则。

**心跳忙碌延后**（`assistant._heartbeat_loop`）：`is_reply / is_reflecting /
_heartbeat_running` 任一为真时不整轮跳过，按 `heartbeat_busy_defer_seconds`
（默认 60s，热读取）短间隔轮询，空闲后立即补跑；被延后的 tick 不递增任何计数器。

**同任务排队去重**：引擎 `_task_inflight` 集合（asyncio 单线程 check-then-set 无竞态）
——`run_task` 锁前查重拒绝重复触发，tick 选 task 时跳过 inflight 名称，
保证排队里同一种任务只有一条（手动连点/Web trigger/AI 触发/tick 四路径共用）。

对话折叠三入口共用 `ConversationData.schedule_fold`：窗口滞回触发（取数路径）/
心跳空闲折叠 / AI 工具 `fold_conversations`（memory 组，整理当前或全部会话）。
折叠成功后经注入的预热钩子（`mind.prewarm_scope_cache`，bootstrap 注册，
`conversation_fold_prewarm` 开关）发 1-token 轻调用写热新前缀。预热在
`decorate_messages` 之后必须经 `normalize_for_send` 再发（与 `_invoke_llm_unified`
一致），否则 `_layer` 内部分类标签会泄露给供应商（严格校验端点潜在 400）。
窗口配置收敛为两个：**总条数** `max_conversation_size` + **保留百分比**
`conversation_raw_keep_percent`（保留条数 x 与滞回 H 均派生：x=M×百分比、H=x，
窗口在 x~M+x 波动、每批折 M 条）；Web 配置页以一行复合组件呈现
（数字输入 + 滑条 + 折叠段比例条，见 `pages/config/ConversationWindowRow.tsx`）。

#### 工具注入（PFC 多路合并 + 门控）

| 来源 | 说明 |
|------|------|
| always | 永驻工具（end_reply, send_message 等） |
| mcp:* | MCP 服务工具 |
| channel | 频道能力匹配 |
| tag_match | 消息标签激活（如 media:image） |
| hot_recall | 热门工具 top-N |
| discovered | 动态发现 |
| activated | 已激活的沉睡分组（activate_tool_group） |

合并结果经两道门控过滤（`core/tool_gate.py` + `agent/mind/tool_activation.py`）：
1. **check_fn 门控**：工具声明的前置条件检查（30s TTL 缓存 + 60s 瞬态故障宽限），不通过则不出现在 schema
2. **沉睡/激活**：`allow_sleep=True` + `sleep_brief` 的工具默认沉睡（目录中仅展示 brief），AI 调用 `activate_tool_group` 唤醒，按 scope 隔离、按轮次消耗

#### 上下文组装（Prompt 分层缓存）

```
1. stable 层（人设 + 工具提示）—— 对话内冻结，PromptCacheManager 按 scope 缓存，
   字节级稳定供 Anthropic/OpenAI 前缀缓存复用（Anthropic 注入 cache_control 断点）
2. summary 层（对话摘要）—— 折叠周期内字节固定，历史前缀锚点
3. 对话历史（实时从 DB 获取，禁止缓存；水位线后纯追加）
4. context 层（便签 + 文件索引）—— 尾部动态区最前：心跳任务/技能评审会写便签，
   放前缀锚点位会让每次漂移作废其后 20-40K 历史缓存；内容寻址缓存保证未变时字节稳定
5. volatile 层（状态/画像/短期记忆/召回/技能注入等）—— 每会话构建
6. provider 层（上下文提供者实时注入）—— think_loop 每轮发送组装时收集最新快照，
   置于工具链之后、exec_context 之前（工具链前缀字节稳定，实时内容逐轮新鲜）
```

#### 思维循环防护（think_loop）

| 机制 | 文件 | 说明 |
|------|------|------|
| 工具守卫 | `agent/mind/guardrails.py` | 精确失败重复/同工具连续失败/无进展循环检测，动作 warn/block/halt；分级提醒（首次温和、后续附参数预览）；用户插话重置计数 |
| 错误分类 | `agent/llm/resilience/classifier.py` | LLM 错误分类（rate_limit/context_overflow/auth 等）驱动重试策略 |
| 自适应重试 | `agent/llm/retry.py` | 指数退避 + 抖动（jittered_backoff） |
| 上下文压缩 | `agent/mind/context_compressor.py` | 溢出检测（真实 usage 优先）→ 保头保尾 + LLM 摘要 → 压缩反馈注入；摘要调用复用主前缀命中 KV 缓存 |
| 结果预算 | `agent/mind/result_budget.py` | 按模型窗口动态截断工具结果（15% 单条 / 30% 整轮） |
| 会话令牌 | `agent/security/session_token.py` | 一次性令牌标记可信历史，泄露即 SECURITY 停止 |
| 威胁扫描 | `agent/security/threat_scanner.py` | 注入模式扫描（工具结果标记 / 记忆写入拦截） |
| 结果脱敏 | `core/sanitizer.py` | API Key/Token/密码自动遮盖（工具结果 + 日志） |
| 崩溃尾部修复 | `agent/mind/crash_recovery.py` | 回复检查点落盘（`reply_checkpoints` 表），启动扫描崩溃残留注入"上次被中断"元消息；上次为崩溃退出时随元消息附带崩溃上下文（消费崩溃状态 + macOS .ips 关联） |
| reasoning 条件回传 | `think_loop.preserve_reasoning_fields` | `reasoning_details` 仅工具轮回传（DeepSeek 官方规则：普通轮服务端忽略），纯文本轮省 token；`thinking_blocks` 无条件保留 |

#### 运行时机制速览（第三轮新增）

| 机制 | 文件 | 说明 |
|------|------|------|
| 后台任务增量输出 | `background_tasks.read_task_output` | 单游标消费型：`check_background_tasks(task_id=...)` 每次只返回新增输出，轮询长任务不再全量重读日志 |
| 唤醒预算 | `agent/mind/wake_budget.py` | 连续自动唤醒超 `background_wake_budget`（默认 3）不再触发新周期（防自我激励循环），真人输入重置 |
| 会话用量统计 | `agent/mind/scope_usage.py` | per-scope 累计 LLM 用量与 turns（`scope_usage` 表增量累加），`GET /api/status/usage` 查询 |
| /name 技能手势 | `agent/skills/gesture.py` | 真实用户消息以 `/技能名` 开头 → 绕过语义评分确定性注入（防伪造：仅外部消息路径检测） |
| 用户 hook 事件面 | `agent/hooks/` + `services/hooks.py` + `web/routers/hooks.py` | `config/hooks.json` 声明 tool_pre/tool_post/reply_end 脚本；exit 2 阻塞（stderr 为理由）、串行、deny 胜过一切；空配置零开销。管理面：设置页「钩子」标签可视化编辑（保存即热生效），样例 `config/hooks.example.json`；校验与运行时同源（`parse_hooks_data`） |
| 长任务交接 | `agent/task/handoff.py` | 任务定义 `handoff: true` 时：输出末尾 `# HANDOFF` 块持久化，下次运行注入（确定性接力） |
| 消息来源打标 | `_source` 键 | 系统注入消息带 `{"origin": ...}`，`normalize_for_send` 与 `_layer` 一并剥离（LLM 不可见，供归因） |
| 子代理统一注册表 | `agent/delegation/profile.py`（schema 单一权威）+ `LLMManager._sub_agents`（存储宿主）+ `delegate_task(agent_name=...)` | 一套档案体系（llm_clients.json 顶层 `sub_agents` 键）：**模型面**（名称 → 有序模型候选池，前者不可用依次回退）+ **执行面** AgentFacets（instructions 专职守则 / tool_tags reflect 工具选择器 / blocked_tools 追加屏蔽 / output_schema 结构化产出契约——注入子代理 reflect 临时上下文，不进任何 stable 前缀层）。**内置难度档 easy/medium/hard（tier 1-3，受保护）就是 difficulty 1/2/3 的语法糖**，恒为纯模型池（难度语义只是换模型，不收执行面）；与自定义档案（tier 0）同构存储、同套 CRUD；解析优先级 agent_name > difficulty > 默认，本档全不可用降挡。AI 经 model_control 组 4 个工具增删改查（update 的 instructions/output_schema 传 "clear" 清除、tool_tags/blocked_tools 传空列表清除），Web 经 `/models/sub-agents`（模型页子代理面板可编辑执行面），双路径同 LLMManager 内存态 + 原子落盘即热生效；legacy `delegation_tiers` 键加载时自动迁移。output_schema 是提示词契约 + 产出宽容提取校验（`extract_json_output`：原文/剥围栏/平衡大括号三候选），schema_ok 事实报告进聚合结果——系统不替 AI 拒绝产出 |
| idle 空闲调度 | `agent/heartbeat/` IDLE 模式 | 连续 N 拍无思考活动（`mind.last_activity_ts` 锚点，任务自身执行也刷新）触发唯一空闲任务（反思+自由活动，如 self_reflection）；确定性调度优先，REFLECT 元决策延迟登记由其消费；`validate_schedules` 强校验全局仅一条 |
| 心跳忙碌延后 | `assistant._heartbeat_loop` | 回复/反思/上轮 tick 未收尾时不跳过整轮，按 `heartbeat_busy_defer_seconds`（默认 60s）短间隔轮询、空闲即补跑；延后期间不递增任何计数器 |
| 跨周期双答防护 | `heartbeat/engine._tick_inner` 回复优先门控 + `agent/channel/outbound_guard.py` 出站哨兵（挂 `execute_send_action`）| 回归自 2026-09-14 两次双发事故：思维周期消息链在启动时刻冻结（对话历史周期内不重读），回复周期与任务/反思周期并发时互相看不到对方出站——反思按「主人 6 分钟未获回复」的过期快照代答、慢速回复随后再交卷，或闹钟回复刚交付、反思按旧待办重发。两层防线：①调度层——`mind.is_reply` 为真时 tick 只做维护与提醒不启动任务（不递增计数器、保留待反思标记，空闲后补跑）；②出站层——反思/任务上下文（think scope `reflect:` 前缀，经 `_sdk.get_current_scope` 读取）的 send 工具出站前查活状态：目标会话正有回复在飞（`Mind.active_reply_scopes` 经 wiring 施绑的读取器，单一事实源）或近窗口内已有**其他**思维链出站（进程内记录，`outbound_guard_recent_seconds` 默认 180s，0=关）即拒，拒绝 JSON 带 guard 归因 + 近期出站预览供模型自适应；回复周期自身与系统路径豁免（多段回复合法），同思维链连续出站放行。配置组 `channel/outbound`（`outbound_guard_enabled` 可关） |
| 同任务排队去重 | `HeartbeatEngine._task_inflight` | tick/manual/AI 四路径共用的执行中集合，排队里同一种任务只允许一条 |
| 空会话防护与整合 | `agent/channel/outbound_guard.py::guard_empty_conversation`（挂 `execute_send_action`，出站层）+ `decision_executor.execute_proactive`/`execute_tool_action`（决策层前置判定）+ `sqlite_backend.sweep_empty_conversations`（存量整合，心跳 `_sweep_empty_conversations_if_due` 周期触发） | 空会话 = 从未收到过 user 角色消息的会话（AI 主动搭话历史缺陷与一次性通知写入产生，会话列表呈现为无用户内容的空会话）。三层：①出站层——AI 思维上下文（回复/反思任一，thinker 非空非 `_global`）向空会话发送（send_message/send_photo/… 与纯文本轮末投递 `deliver_text` 共用 `execute_send_action`）返回结构化错误（guard=empty_conversation，hint 引导等待对方先开口或改记待办），正常回复周期由真实用户消息触发必有历史、天然不命中；②决策层——PROACTIVE 主动搭话与工具操作结果投递在进入回复周期前判定空会话直接放弃（心跳日志留痕，不浪费 LLM 轮次）；③存量整合——心跳维护按 `conversation_empty_sweep_interval_seconds`（默认 86400s，0=关；启动后首个心跳必执行一次）清除空会话的消息/摘要/回复检查点（1h 宽限窗口内的新会话不动，NOT EXISTS 单语句守卫防与首条用户消息竞态）。判定事实源 `conversation_has_user_message`（索引存在性探测）；查询失败 fail-open（行为护栏而非安全边界）。配置：`channel/outbound` 组 `outbound_guard_empty_enabled` + `heartbeat/maintenance` 组清理间隔 |
| 待回复队列毒丸防护 | `agent/messages/everything.py::is_conversation_scope`（单点判据）+ `scheduler.enqueue_scope_reply`/`add_reminder`（校验）+ `decision_executor.pop_next_reply_target`（就地清除）+ `work_memory.consume_scope_task`（双队列消费） | 回归自 2026-09-12 事故：日历提醒在无会话上下文（心跳任务内建日程）落 scope=`_global`，到期经 enqueue_scope_reply 直入 pending_user，回复路径解析不了只能跳过，自主循环 0 退避无限空转（fast-path 刷屏、日志 8GB）。三层防线：①源头——`add_reminder` 拒绝持久化不可路由 scope 的提醒（ValueError；`_sdk.add_persistent_reminder` 桥接如实记日志返回空串，事件降级为不提醒）；②入口——`enqueue_scope_reply` 拒绝非会话 scope 入队（退化为全局短期记忆桶，对齐 PushHub 兜底）；③兜底——回复消费点对解析失败或缺频道前缀的队列条目就地清除 + WARNING，任何坏条目最多空转一轮即收敛。`consume_scope_task` 双队列都查（不按前缀路由），对落错队列的条目同样健壮 |
| 单实例守卫与重启保底 | `core/instance_guard.py` + `entities/devops/service.py` 重启看门狗 + `restart.sh` | 实例守卫：启动写 `logs/anelf.pid`（项目目录天然按检出副本隔离实例身份），PID 文件指向的活进程经 cmdline 校验（本项目 launch.py）判定为残留实例时 SIGTERM→10s 宽限→SIGKILL 清场接管端口，cmdline 不匹配只警告不误杀（防 PID 复用）；僵尸进程经 psutil status 判定视为已死。重启看门狗：restart_app 排定关停后 90s 进程仍存活（优雅关停卡死）→ 无条件 `os._exit(42)` 保底，守护脚本必然接管；等空闲路径在关停请求发出后才布防（防等空闲误触发）。restart.sh 优先 PID 文件精准终止，pkill 兜底模式收紧到 `$ROOT/.*launch`（旧版 `python.*launch` 会误杀其他项目）。修复 2026-09 实证：8/29 残留进程占面板端口 10 天，restart_app 协作式重启对其无管辖权 |
| 崩溃守护与通报 | `start.sh`/`start.bat` 守护循环 + `core/crash_report.py` + `crash_recovery` | 致命信号退出（SIGSEGV 等，退出码 128+n；SIGKILL/SIGTERM 不重启）自动退避重启（5×次数秒，上限 60s），崩溃状态落盘 `logs/crash_state.json`，连续 5 次崩溃停止拉起防崩溃循环（稳定运行 ≥600s 后崩溃重置计数）；重启后 crash_recovery 消费崩溃状态并关联 macOS DiagnosticReports（.ips）生成崩溃上下文——有回复检查点则随中断元消息注入对应会话，无检查点则经 PushHub 写全局通知并唤醒一轮思维（重启报到技能接管向主人报平安）；状态标记 reported 只通报一次。AI 详情查询走 devops `get_crash_report` 工具 / 面板 `/crash-info` |
| ladybug native 串行门 | `agent/memory/cognee/client.py` `_apply_native_gate` | 进程级线程锁串行所有 ladybug native 执行：锁包在提交到线程池的查询任务上（execute + 结果消费全程），由执行线程持有——wait_for 超时取消协程不会提前放锁，孤儿 native 查询跑完才放行下一条；`_drop_native_resources` 同锁保护，拆除句柄前等在途执行结束。修复 2026-08 SIGSEGV（NodeTableScanState::scanNext 空指针，孤儿查询与后续查询/拆除并发使用同一 connection） |
| 技能治理决策协议 | `agent/skills/`（skill_index 事实层 + tools 决策协议） | 事实归系统、决策归 AI：create/update 在事实层检测到显著信号（语义相近≥`skills_similar_threshold` / 触发词碰撞≥`skills_trigger_collision_limit` / 容量水位 / 无实质变化）时**不拒绝**，返回 needs_decision 诊断报告，AI 带 decision 回执重呼写入（rationale 落盘问责）或改走 merge/放弃；评审上下文由 SkillIndex 供给（语义相近 top10 + 库健康摘要）；use/match 信号分离（检索注入不刷活动时间，get_skill 计数不刷活动，策展重力因此可触发）；检索端近重复折叠（≥`skills_match_redundancy` 折叠并入合并信号）；merge_skills 可逆合并（源 ARCHIVED 带 merged_into）；重力含试用期快筛（零参与 14 天降级）与 stale 软保留（仍被检索到不归档）。向量生命周期：缓存键 = 模型名 + 文本 hash（模型切换即全库失效重嵌，防跨模型余弦混算）；交互路径预算化补算（`skills_embed_budget`，advisory 收紧 8），心跳 `warm()` 批量预热；死键清理时机 = 嵌入完成后（warm/embed_now）+ 删除时（service 直调），列表重建不清理（防误杀待嵌入键）；Web 经 `services._runtime` 拿 Mind 侧索引展示 embedded 状态与覆盖统计，CRUD 后 embed_now 即时重嵌；Mind 构造时重绑定工具依赖避免双向量缓存。向量构建状态机（Web 可观测/可操作/可配置）：`build_state()` 暴露 idle/warming/rebuilding + 进度 + 上次重建记录；`skills_warm_batch_size`（心跳每拍批量）/ `skills_rebuild_batch_size`（全量重建批量）可调；Web 经 `POST /skills/vectors/rebuild` 手动触发重建（幂等，进行中返回当前进度）；每个技能行内 `POST /skills/{name}/embed` 单技能生成/重新生成（不等全库重建）。向量持久化：`skill_vectors.sqlite3`（主库同目录独立文件，短连接 schema 自治，pack_embedding float32 BLOB）——嵌入即 upsert，首次访问懒加载恢复（模型+文本 hash 双因子校验，失配行清除并标记重建），**重启零重嵌**；模型切换内存与 DB 同步清空 |
| 思考等级配置驱动下发 | `agent/llm/reasoning.py`（契约引擎）+ `llm_client._apply_thinking_payload` + 模型配置 `thinking` 字段 | **全代码库对模型名零特判**：每个模型在 `llm_clients.json` 里声明思考契约（`{"param": 目标字段, "map": 档位映射, "on": 开启值, "off": 关闭值}`），LLMClient 只做"读契约填值"，不认识任何模型名/供应商。档位能力不写代码——模型该用哪档由配置 `reasoning_effort` 决定，发了端点不认的档由端点自己报错（参考 cursor-byok）。下发载体按 api_type 区分（litellm 行为差异）：openai 兼容通道 extra_body 由 SDK 展开进请求体顶层；anthropic 兼容通道直发 body 不展开 extra_body、未收录模型顶层字段又被能力表卡住，故填顶层字段 + allowed_openai_params 白名单放行。无契约模型走通用 reasoning_effort 透传。effort 为空时开关型契约（无 map）用 on 值默认开启。litellm 暗坑：未收录模型顶层 reasoning_effort 可能被 drop_params 静默丢弃，必须走 extra_body/白名单透传。**Responses 路径（chat_protocol=responses/auto）不使用 thinking 契约**——effort 统一映射为 Responses 的 `reasoning.effort` 下发（`_build_responses_kwargs`），契约仅作用于 chat_completions 通道 |
| 对话协议路由（chat_protocol） | `agent/llm/protocol.py`（能力矩阵）+ `agent/llm/responses/router.py`（native/bridge 路由）+ `llm_client._should_fallback_from_responses`（auto 回退） | 三值语义：`responses` = **绝对走官方 /responses 接口**（openai/azure 一律 native 直连，不支持是配置错误、404 原样上抛；anthropic 等无官方端点的 api_type 经 litellm bridge 桥接）；`auto` = openai/azure 优先 native Responses，端点未实现（404，经 classifier NOT_FOUND 判定）时记客户端级标记 `_responses_native_blocked` 并回退 chat_completions（本进程内后续直连，流式路径已产出增量则禁止回退）；`chat_completions` = 传统通道。base_url 以 `/responses`、`/chat/completions` 结尾时 URL 推断优先于配置（`resolved_chat_protocol`）。bridge 的唯一正当用途 = 非 openai 系 api_type 的 Responses 暴露（含本项目自身 /v1/responses 服务面） |
| 晚绑定端口 | `core/latebind.py`（原语）+ `agent/runtime/wiring.py`（唯一施绑点） | 进程级类型化晚绑定：端口由消费方所在层声明（`LateBinding[T]`，名称全局唯一、`[None]` 施绑合法——bound 标志即事实），`wire_runtime()` 在 assemble 尾部统一施绑（mind 工具组 / 思维子系统实例（compressor·delegation·auto_capture·skills deps）/ 记忆存储族（memory·graph·planning）/ 会话数据（output·fold）/ embedding worker / cognee 可选后端 / sticker worker / agent→entities 函数桥（workspace 路径·结果落盘·文件状态缓存·图片索引投递）+ prewarm/scope_usage 回调；多依赖端口以 NamedTuple 承载如 `MemoryToolDeps`/`SkillToolDeps`/`WorkspacePathFns`），check_health 经 `assert_wired()` 把漏接线暴露为启动红字；未施绑 `get()` 抛 WireError，可选消费以 bound 守卫保持旧 None 语义。准入：仅限 import 时工具注册拿不到构造参数 / 循环初始化 / 跨层桥三种成因，`set()` 只许组合根调用；DI 容器与装饰器注册表方案均已否决（解析图无消费场景；RuntimePorts 无法跨 entities/agent 分层定型） |

#### MCP 工具面细节（第四轮新增；已拆分为 entities/mcp/ 模块群：bridge.py=连接生命周期核心，config.py=配置注册/沉睡策略/MCPServerStore 配置域，manage_tools.py=管理工具，transport.py=传输工厂+env 白名单，schema.py=参数 schema 解析/名整形，render.py=结果渲染，retry.py=重连预算）

| 机制 | 位置 | 说明 |
|------|------|------|
| 结果内容块分派 | `MCPBridge._render_call_result` | text 拼接；**image 落盘（uploads/mcp/）+ `_multimodal` 约定**——视觉模型经 think_loop 注入直接"看到"MCP 截图（chrome-devtools 等场景），非视觉模型读路径占位；base64 原文绝不进上下文（此前 `str(item)` 倾倒整段 pydantic repr）；audio/resource_link/embedded resource 短占位；无文本时 structuredContent 兜底。模型可见性：有图输出 `{"_multimodal": true, "text", "images"}` JSON，无图输出纯文本（与旧版一致） |
| 工具列表热同步 | `message_handler` 注入 + `_sync_server_tools` | SDK 默认静默丢弃 `ToolListChangedNotification`；经 ClientSession 公开 `message_handler` 参数拦截（旧版 SDK 无此参数自动跳过）→ 1s 防抖 → **增量**增删注册（同名描述变更不动，避免无谓 tools 前缀缓存失效）。`mcp_tool_list_sync` 可关 |
| 注册超时对齐 | `_register_tool_entries(call_timeout=...)` | server 的 `call_timeout` 透传为工具执行超时 meta——修复此前落入全局默认 60s、在 bridge 超时（默认 300s）之前被提前掐断的错配（用户配置的 call_timeout 曾是死配置） |
| 参数 schema 保真 | `_parse_param_schema` | anyOf/oneOf 可选参数解引用取非 null 分支的 type；`default/items/minimum/...` 经 `schema_extra` 直通 wire schema（模型看到默认值与数组元素结构，不再按 string 兜底猜） |
| 注册名整形 | `_sanitize_tool_name` | 冲突检测在整形后的名字上进行；非法字符替换下划线 + 64 字符上限（超限截断 + SHA-256 前 8 位防撞）——OpenAI 风格端点会拒绝整组 tools 数组，一个坏名字曾可导致全会话不可用 |
| 结构化错误 | `call_tool`/`_do_call_tool` | 未命中 → not_found；超时 → timeout + `code=TOOL_TIMEOUT` + retryable；断线 → network + retryable（对齐 core/tool_errors 纪律，供守卫与模型重试决策消费） |
| 重连预算复位 | `_RetryBudget`（稳定窗口 300s） | 连接稳定运行超窗口后失败，重试计数清零重计——长期服务偶发抖动不再累计耗尽 5 次预算而永久死亡；退避序列 1/2/4/8/16s 与旧行为一致 |
| 连接存活探测 | `bridge._wait_with_liveness`（lifecycle 等待段） | stdio 子进程退出/网络静默断开时 SDK 不通知等待方，纯 `stop_event.wait()` 会让死连接永远显示"已连接"（状态虚报）。按 `mcp_liveness_ping_seconds`（entity/mcp 组，默认 60，0=关闭）周期 `send_ping`（10s 超时），失败抛 ConnectionError 进既有断线分支——enabled 走自动重连、disabled/已删除退出清理，connected 状态一个周期内收敛为真实死活；`_try_reconnect` 带 enabled 守卫，禁用 server 调用失败只报错不复活拉起 |
| Web 启停切换语义 | `services.mcp.toggle_server`（`PUT /mcp/{name}/toggle`） | 以配置文件 enabled 为准而非连接状态：已启用（无论是否连上）→ 禁用并断开（重启不再自动连接）；已禁用 → 启用 + 热重载连接，连不上保持启用落盘并如实回报 last_error（重启自动重试）。此前按连接状态判断，已启用但目标不可用的 server 在前端只能反复尝试连接、永远无法禁用 |
| 装配重建触发器贯通 | `think_loop` 工具集版本元组 | 版本元组新增 `EntityRegistry.version()`（注意是 classmethod 调用而非属性）——热同步/reload/重载/WebUI 开关等注册表增删后，**回复进行中**的下一轮即重建 active_tools（此前仅 (assembly, activation) 双版本，粘性激活的常驻服务要等下一个版本事件）；每个新回复本就重新装配。重建经追加式冻结保持前缀字节稳定 |

#### 审计 / 扫描 / 限流退避 / 用量归属 / TTFT（第五轮新增）

| 机制 | 位置 | 说明 |
|------|------|------|
| 审批审计持久化 | `agent/approval/audit.py` + `approval_audit` 表 | 所有**非默认放行**的审批决策落账本（人工 approve/deny/cancel/expire、规则拒绝、信任放行、超时放行；常态 rule_allow 高频无信息量不记）。`trust_after_n_approvals` 计数改从账本统计（outcome=approved 累计）——**重启不再从零重数**；trusted 不计入 approved（防自动放行自我强化）。内存 `_decision_history` 已删除，`/approvals/history` 读表分页（offset+tool_name 过滤），stats 走 outcome 聚合。写失败 fail-open 仅记日志 |
| 工具元数据风险层 | `agent/approval/rules.py::tool_meta_risk_rule`（求值管线第 6 层） | `@tool(risk="CRITICAL")` 声明落地为审批兜底：声明式/会话级规则未命中且默认放行时，CRITICAL 工具合成 ask 规则（guardian 先行评审，危险才升级人工）——修复 risk 元数据全链路无消费者、声明与行为相悖的问题。仅 CRITICAL 升级（MEDIUM/HIGH 只作 guardian 评审与审计的风险标注，write_file 等高频工具不受影响）；显式 allow 规则天然优先（求值顺序保证）；审计 matched_rule=meta:risk 可归因。历史 `shell.*`/`system.*` 等永不命中的死模式已从默认策略集与示例模板清除（换真实工具名） |
| 文件扫描剪枝 | `entities/filesystem/scan.py`（新模块） | os.walk 按目录名剪枝（默认 .git/node_modules/__pycache__/.venv/dist/build/各类缓存，`search_exclude_dirs` 可配置）——不再进结果也不再向下遍历；glob 语义（裸 `*.png` 任意深度、`**/` 零目录语义补齐）；内容模式跳过二进制扩展名与 >2MB 大文件；结果 path 保持绝对路径（直接可喂 read_file） |
| 二进制嗅探 | `scan.looks_binary`（前 8KB NUL 采样） | read_file 扩展名表之外的内容级防线——文本读取走 `errors="replace"` 永不抛解码异常，无扩展名/冷门扩展名二进制文件此前乱码灌上下文；命中返回既有 `{"type":"binary"}` JSON 引导媒体工具 |
| Retry-After 采信 | `agent/llm/retry.py::parse_retry_after` | litellm RateLimitError 携带 headers（本机已验证）；支持秒数/HTTP 日期/毫秒变体。限流退避取 max(服务端指令, 本地抖动指数)；服务端要求 >60s（`RETRY_AFTER_WAIT_CAP`）视为本轮放弃当前候选转回退链——不白烧请求与配额 |
| 用量归属与口径 | `scope_usage.bind_usage_scope` + `_is_ephemeral_scope` + `UsageInfo.prompt_includes_cache` | ① 委托链经 ContextVar 绑定父会话 scope，子代理 reflect 的 LLM 用量归属父会话（/status/usage 可见委托成本）；② `reflect:{uuid}` 一次性 scope 不建统计行——此前每个子代理落孤儿行，累积挤爆容量上限后**新会话用量被整体静默丢弃**；③ 记账口径归一：提取层按 `usage_prompt_includes_cache` 判定 prompt 是否含缓存（details 包装/DeepSeek 命中字段=含；仅原生 Anthropic 字段=不含），命中率一律 `cache_read / total_input_tokens`（修复原生 Anthropic 口径下 read>prompt 被钳成 100% 的虚报），scope_usage 累计前补回缓存量，list/summary 输出 `prompt_miss_tokens = prompt - cache_read` 在两种口径下均成立。scope 解析链：anything.entity_scope > usage_scope 绑定 > 激活上下文。④ 流式 usage 旁路全字段优先（`response_parsing.install_usage_tap`/`_merge_sink`）：litellm 1.100 对未收录模型（openai/glm-5.3 等）的流式 chunk 用本地 tiktoken 估算**伪造 usage**（prompt 虚高 ~1.8 倍、completion 清零、缓存 details 丢弃），旁路只补缓存字段会形成"真实 read ÷ 伪造 prompt"尺度混血（命中率 ~50% 假象 + 口径守卫误翻，2026-09 实证）；旁路捕获原始 chunk 全量字段，见过原始 usage 即全字段以其为准（主路缺失时据其构造）。非流式与 native Responses 路径 usage 透传正常不受影响。⑤ 占用锚点全面归一（2026-09 第十五轮）：上下文占用（压缩触发 `should_compress` 锚 / `_emit_context_usage` / token 预算提醒 / `usage_percent`）一律用归一化 `total_input_tokens`（+completion）——独占口径（原生 Anthropic）下原始 prompt_tokens 不含缓存读/写，以其为锚会低估占用、压缩触发偏晚；think_loop 状态字段收敛为单一 `last_input_tokens`（原 `last_prompt_tokens` 与 `last_total_input_tokens` 双字段冗余合并）。⑥ 记账单一权威：`chat_with_fallback(record_usage=False)` 供主对话路径关闭管理器侧记账——此前每次主对话调用在缓存命中统计（phantom internal 桶、样本翻倍）与 scope 成本账本（委托调用双倍计数）双写；内部辅助调用（guardian/summarize 等）默认仍由管理器记账。⑦ Responses 路径 `cache_observable` 动态判定（同 Chat Completions 纪律：无缓存字段=不可观测，不谎报可测 0%）；context_usage 事件键统一 `cache_creation_input_tokens`（修复 webui 状态栏恒 0） |
| WebUI 聊天广播 | `core.event_bus.EVENT_CHAT_BROADCAST` + web/routers/chat.py SSE 桥接 | channels/webui 经事件总线推帧（`_broadcast`/`_broadcast_scoped` 发射 EVENT_CHAT_BROADCAST），web 层订阅桥接 SSE 订阅者——频道不反向依赖 web 层（旧 `channels.webui → web.routers.chat` 环已拆）；健康探针改查 `event_bus.has_listeners` |
| TTFT 首 token 计时 | `ChatResult.ttft_ms` + `EVENT_THINKING_LLM_END` | 流式路径记首 delta 到达时刻（毫秒）；与 duration_ms 相减即输出生成耗时——"排队慢"与"生成长"两个独立延迟源分别可诊断（对齐 dsh trajectory TTFT）。非流式为 None |
| 一次性通知历史固化 | `scheduler.enqueue_scope_reply`（async）+ `_append_one_shot_history` | 一次性事件（后台任务完成/实体推送/定时提醒/重启补回/会话切换/委托完成）写目标会话**对话历史**（system，trigger_mind=False）而非短期记忆——此前驻留 volatile 层：每轮重复催促已处理完的事项，且每条新通知重写会话层前缀反复打断 prompt cache，清理全靠模型自觉。await 返回即历史落库，随后的回复周期拉取必含（无竞态）；写入失败回退短期记忆兜底。push 的 seq/inflight 随投递完成后登记（水位只统计已固化事实）。委托轮内会合的完整详情同样固化历史（`_append_one_shot_history` 直达），轮外完成由 registry unclaimed 回调统一负责不双投递；回调支持协程（`_finish` 总在主循环 ensure_future）。短期记忆回归纯持续提醒语义 |
| 子代理指令双档（steer/after） | `agent/delegation/steer.py` + `DelegationManager.steer` + `round_helpers._merge_steered_messages`/`merge_after_messages` + 工具 `send_to_agent(deliver_as=)` | 对齐 dsh steer 语义（2026-08 adjacent-agent-steer-messaging）与 pi 的 steer/followUp 拆分：**steer 档**（默认）在**步骤边界**注入追加指令、改变进行中的工作；**after 档**在**收束边界**注入（think_loop REFLECT 连续纯文本达上限本要结束时 `merge_after_messages` 消费，重置计数续跑）——「做完这批后顺便…」型追加不取消不重开、已完成部分保留。寻址按 delegation_id（前台/后台统一，只要在 _running）；消息暂存 `SteerInbox` 按档位分离（单委托上限 8 条两档合并计、单条 4000 字符截断），SubAgent.run 经 `bind_steer_drain` ContextVar 绑定 mode 参数化 drain 闭包（create_task 复制进整个执行树），主会话未绑定 drain 恒空零开销。委托结束（成败/超时）finally 清箱防残留误入后续同名委托。工具返回结构化错误：不存在（not_found，有 transcript 时引导 follow_up_agent）/空消息/非法档位/超上限 |
| SSE 断线可见性与恢复 | `chat-store.ts`（sseConnected + refreshAfterReconnect）+ MessageList 横幅 | 对齐 dsh 连接恢复指示器：`es.onopen` 置连上、`onerror` 置断开——聊天流顶部显示琥珀色"正在重连"横幅（i18n zh/en）；断线后重连（_wasConnected 区分初次）自动补拉当前会话最近一页历史，按消息 id 尾部对齐合并（保留本地已加载的更早消息），修复断线窗口内落地的回复帧（delta/turn_end）静默缺失需整页刷新的问题。sending 卡死由既有发送看门狗兜底，不误复位进行中回复 |
| 反思产出语义（纯结论） | `think_loop._handle_tool_round` 工具轮边界清空 + end_reply 收束豁免 | REFLECT 模式下模型发起**工作工具**调用即判定此前纯文本为中间独白（"我先分析一下…"）——从 collected_text 移除（字符数归档进 execution_steps 可追溯），产出只保留收束前**最后一个未被工具调用打断的连续文本段**。此前全轮合并 + 聚合截断（头75%尾25%）会让中间噪音挤占 [2000,24000] 预算、稀释关键结论——子代理结果、任务产出（存记忆）、元决策 REFLECT 输入三处同时受益。REPLY 模式 collected_text 无消费方，零影响。**end_reply 是收束信号而非工作工具**（2026-09 子代理 no_output 事故修复）：纯 end_reply 批次不构成"打断"、已收集结论不清空；其同批正文即最终连续文本段纳入产出（REPLY 对称语义：同批正文为待投递尾文本）——修复前 reflect 唯一幸存路径是"连续纯文本撞上限自动收束"，照契约调 end_reply 必丢产出（子代理/任务/内省同病） |
| 委托结束原因贯通 | `think_loop completion 容器` → `mind.reflect(completion=)` → `SubAgentResult.completed_reason` | 结束原因三值：completed / budget_exhausted（轮次预算用尽，产出可能只是中途状态）/ interrupted（协作中断），经调用方传入的 completion 字典带出（不传容器零影响）。聚合结果对 budget_exhausted 条目附 `hint`（"拆小任务重新委托"），后台完成通知同样标注——父代理可区分"完整结论"与"半成品"并决策续委托。空产出时 no_output |
| 委托可续跑与运行日志 | `agent/delegation/journal.py`（进度流/transcript/ledger）+ `DelegationManager.follow_up` + 工具 `follow_up_agent` | 对齐 dsh continuable subagents：委托结束把最终消息链（completion 容器 `messages` = base+tool_chain，think_loop finally 带出）持久化为 transcript（`<data_dir>/delegations/<id>.json`，256KB 上限超出降级为不可续跑档案），`follow_up_agent(delegation_id, message)` 以消息链为 base_messages 追加 [续跑指令] 无损续跑（前台/后台两路，血缘 parent_delegation_id 贯通；运行中委托拒绝并引导 send_to_agent）。**进度流**：轮次/工具事件行追加 `<id>.log` 并 `attach_output_file` 接入注册表——`check_background_tasks(task_id=...)` 现有单游标增量管线立即可读子代理中间进展（与后台 shell 同构）。**用量归集**：EVENT_THINKING_LLM_END 按 ContextVar 归属 delegation_id 分桶（turns/input/output/duration），随 SubAgentResult.usage 进聚合结果与 resolved 事件，running_snapshot 实时可见——父 AI 可判断"烧了 30 轮才出这点结论，该拆任务了"。**崩溃账本**：ledger.jsonl started/closed 各一行，bootstrap recover_interrupted 节点扫未闭合条目 → 按归属会话聚合注入"后台委托被进程中断"元消息（at-most-once：扫描即闭合；同 scope 一条防轰炸），非会话域仅记日志。retention 滚动清理（`delegation_journal_retention_days` 默认 7 天，新事件顺带执行）；`delegation_transcript_enabled` 可关。**归属修复**：前台/嵌套委托注册表登记从 `_global` 改为 `_owner_scope`（usage_scope 绑定 > 激活上下文，与完成路由同链）——check_background_tasks 真正可见前台委托、嵌套委托用量归属父会话 |
| 前台委托注册表化 | `delegate()` registry 登记 + killer + `complete(claimed=True)` | 前台/嵌套委托同样登记 BackgroundTaskRegistry：check_background_tasks 可见（含耗时）、terminate_background_task 可单独停止（killer 走 _cancel_marks + 桥回主循环 cancel，转"用户取消"结果返回父级，不再只能中断整个回复或等 600s 超时）。完成走 `complete(..., claimed=True)`（调用方声明结果已被工具返回值消费，跳过轮外完成回调防双投递；异常路径也收尾防条目滞留 running）。`registry.complete` 新增 claimed 覆盖参数（None=按等待者判定，不变行为） |
| deferred 组激活时序纪律 | `entities/_sdk.py::activate_group` + `bootstrap.assemble_runtime` 提前导入清单 | activate_group 对空组**连组名都不登记**——deferred 工具组只有模块被 import 后才进 `_deferred_registry`，故 Mind 构造期激活的组（thinking/session/delegation）必须在 assemble_runtime 提前导入清单中显式 import。回归自 2026-09 潜伏缺陷：delegate_tool 全仓唯一导入方曾是 wire_runtime 函数体（Mind 构造之后才执行），delegation 整组生产环境从未注册（启动日志 `子代理工具已注册 (0 个)` 即信号），单测因直接 import delegate_tool 始终绿而未暴露；守卫测试 `tests/unit/agent/delegation/test_tool_registration.py` |
| 压缩文件清单累积 | `context_compressor._extract_file_operations` + `[已操作文件]` 行 | 从被压缩中间段的工作链**规则提取**"读过/改过"的文件清单（确定性，不经 LLM 保真），作为独立 `[已操作文件]` system 消息在摘要区追加——不随摘要文本的有损转述衰减、不占 summary_max_chars 预算；下次压缩时从摘要文本回读合并（`_extract_file_operations_from_summary`，单调增长的事实链）。LLM 摘要管语义（任务/决定/实体），规则清单管文件事实——两层分离。上限 30 条/类 |
| hook REPLACE 解析 | `agent/hooks/runner._extract_replace` | hook stdout 输出一行前缀 `REPLACE:<json-string>` 即返回替换内容（`HookOutcome.replace`，串行取第一个）；非字符串/非 JSON 静默忽略（stdout 是日志通道，向后兼容）。**当前无消费方**——reply_end 的 replace 消费经核验无效（complete_reply 的 content 恒空，出站文本已投递），已在实现中回退，解析层保留供未来 tool_post 等场景复用 |
| 外围工具细节 | `web_download(timeout=300)` / `python_exec` 落盘 / 只读并发标记 | ① web_download 注册 timeout=300——AI 参数（默认 30s）在此范围内生效，此前未声明落入全局默认 60s 提前掐断（AI 传 120s+ 也是死配置，与 MCP call_timeout 同款错配）；② python_exec stdout 超 30000 字符经 `shell_state.truncate_or_persist` 落盘（`.tool-results/` + persisted 路径，模型 read_file 分段取回）——与 run_shell_command 对称，不再截断丢弃；stderr 仍 1000 字符小限截断（多为回溯/警告）；③ 18 个纯读工具补标 `concurrency_safe`（模型查询三件套/系统查询组/实体查询组/get_crash_report/ui_get_state/list_voices/repo_docs/rerank_search/get_entity_config）——与写工具同轮混发时不再被切进串行批，并行机会不流失 |
| 模块热插拔（实体/频道目录增删） | `entities/hotplug.py::sync_entities` + `agent/channel/hotplug.py::sync_channels` + `config_watcher.watch_dir` | reconcile 对账范式（对齐插件装卸载）：监听目录结构（子目录 + tools.py/adapter.py 标记文件快照，防抖合并）或手动触发（实体/频道/工具页「热同步」按钮，`POST /tools/reload` / `/adapters/reload`）——新增目录即时注册（工具/分组/配置 schema/lifecycle/路由），删除目录完整拆除（注册表 unregister → 独占分组回收（含 manifest/权重）→ ConfigRegistry/Store 回收 → Lifecycle 组件注销 → sys.modules 清理 → 路由摘除）；工具归属按 `func.__module__` 前缀扫描，分组/配置组/Lifecycle 组件按导入前后差集记录。手动刷新（reload_existing=True）对存续模块做代码热更（实体先按归属注销旧工具再 re-import，被删除的工具不回归；频道停→清模块→重建，连接断一次）。路由挂载/摘除经 EVENT_MODULE_ADDED/REMOVED 事件总线通知 web 层（web/server.py 订阅，本层不反向依赖 web）；监听开关 `hotplug_watch_enabled`（system/hotplug 组，默认开）；失败目录不进已知集合下轮自动重试；两域各自单飞护栏防并发同步 |
| 分组排序实体自决 | `entity_manifest(order=)` / 组归属模块 `EntityRegistry.register_group_order` 自声明 + `BaseChannel.display_order` | 排序权重单一权威在 EntityRegistry：每个工具组的排序权重由其归属模块在 import 时自声明注册（实体经 manifest order；agent 侧工具组在各自工具模块顶层调用 register_group_order），core 不内置任何业务分组名表，删除模块即权重随之消失；权重按类别分段（0-9 输出思维 / 10-19 记忆 / 20-29 规划执行 / 30-49 能力感知 / 50-59 模型运维 / 60-69 管理集成 / 70-79 界面会话）让同类相邻，未注册 1000 字母序排尾；消费面统一 `group_sort_key`（LLM 工具目录 / /tools/grouped / /entities/ 列表）。频道经类属性 `display_order`（默认 100）自声明，`list_adapters` 按 (order, key) 排序，未实例化频道经 `load_channel_class` 读类属性（CHANNEL_CLASS 优先 + 子类扫描回退，与 activate_channel 共用解析） |
| cognee LanceDB 物理压缩与存储统计 | `cognee/storage.py`（物理存储维护模块：压缩 + 统计 + 快照 + 调度）+ coordinator 空闲窗口调度 | cognee 删除/更新只在 Lance 追加 tombstone 新版本，历史版本物理数据永不回收（磁盘单调膨胀的根因）。`compact_lance_tree` 遍历 `system/databases/**/*.lance.db` 逐表 `optimize(cleanup_older_than)`（碎片合并+索引优化+清理早于 `compact_retention_days` 的版本，最新版本永远保留、逻辑数据零影响），压缩前后用同一条统计遍历实测占用；worker 队列排空后的空闲窗口按 `compact_interval_seconds`（默认 86400s）自动执行（与写入单消费者天然互斥），失败仅记日志下个周期重试。手动触发三入口同路径 `coordinator.request_compact()`：AI 工具 `compact_cognee_storage`（memory 组）/ `POST /memory/cognee/compact` / Web 记忆页「压缩存储」按钮；worker 存活时登记请求待空闲执行，未运行则内联执行。状态经 sync.last_compact_at/last_compact_summary 暴露。`StorageStatsTracker`（单例 `cognee_storage_stats`）：大库遍历可达数十秒，请求路径永不遍历——内存 TTL → 磁盘快照（`<data_root>/storage_stats.json`，重启即恢复真实值）→ 空统计三级返回，过期仅调度后台单任务刷新；所有缓存写入携带单调代际号，invalidate/adopt/新刷新使在途旧遍历结果被丢弃（防压缩后数字被旧遍历回写）；coordinator 启动预热、压缩尾声 `adopt(after_stats)` 直接收录实测值免二次遍历、rebuild 清场后 `invalidate(root)` 连快照删除。`/cognee/status` 的 storage 字段与数据库管理页 cognee 条目（size_bytes=整个数据目录，此前仅 stat 元数据库文件曾 177M 显示 vs 30G 实际）共用该 tracker |
| 存储卷（数据平面模块化管理） | `core/storage_volume.py`（注册表 + 位置指派 + 主库路径权威 `main_sqlite_path`）+ `agent/storage/volume_restore.py`（重启落盘交换）+ `services/volume_ops.py`（备份/恢复/迁移/SQL 导出导入） | 所有持久化数据统一登记为存储卷（8 卷：agent 主库 / memory / skill_vectors / stickers / audio / share 六个 SQLITE + cognee 树 + 便签树），各存储模块 import 时自注册 VolumeDescriptor（惰性 default_path 保持测试隔离）；同族库路径均由 `main_sqlite_path()`（env > 项目根 ConfigPaths.SQLITE_DB）派生 stem，放在 core 使 entities 无需依赖 agent。路径解析优先级：env_override > 位置指派（`config/storage_volumes.json`，cognee 卷转发 cognee.json data_root 单一权威）> 模块默认派生——**无指派文件时所有路径与历史完全一致，数据零移动**。能力按形态派生：SQLITE 全量（备份/恢复/迁移/SQL 导出导入）、cognee 树无 SQL 传输、便签树（路径即数据根）仅备份/恢复（占用也只计卷成员：根级 *.md + events/groups/profile_backups，不计数据根其余内容）。备份：SQLite 走 Backup API 在线热备（`services.database.online_sqlite_backup` 唯一实现，整目录迁移同源复用）、树走 tgz（cognee 经 coordinator `run_in_idle_window` 空闲窗口与写入互斥，manifest 的 consistency 如实标注）；保留数 `volume_backup_retention`（storage/backup 组，默认 5）自动清理。恢复与迁移均为「校验 + 拷贝 + 指派/标记 + 重启生效」：恢复写 pending 标记（`<data_dir>/backups/volumes/.pending-restore.json`），bootstrap `init_storage` 最早消费（任何连接打开前交换文件；旧库 -wal/-shm 必清除防回放；现文件留 `.pre-restore-<ts>.bak` 滚动保留 3 份）；便签树恢复为选择性覆盖，cognee 树整树替换。迁移目标校验复用 `data_migration.validate_target_dir`（i18n 标识 tokens 共用），尺寸估计异步分口径（cognee 走统计缓存/便签走成员/SQLite 走 stat），不在事件循环上遍历大目录。外部 SQL 为备份/转移通道（运行时各库仍本地 SQLite）：`SqlTransferClient`（与只读浏览适配器分离的写通道）做 DDL 方言翻译 + rowid 窗口流式批量传输，导出登记清单表 `_anelf_export`、导入仅认清单（快照往返闭环）；派生索引（FTS5/vec0 影子表）不传输，导入后由各存储建表逻辑重建。Web 面板：数据管理页「存储卷」Tab（`pages/database/volumes/`），API 前缀 `/database/volumes`；`services.database.ensure_volume_modules()` 兜底触发卷登记，库注册表由卷驱动（share 库由此补登），cognee 浏览路径仍指元数据库文件。目录遍历/占用统一走 `core.file_utils.walk_files/directory_size` |

#### 每轮动态区预算与纪律归一（第十二轮新增）

| 机制 | 位置 | 说明 |
|------|------|------|
| 纪律单一权威源 | `agent/memory/rules_doc.py`（铁律）+ memorize/recall schema + hub 骨架/注入头 + 召回块头 | 同一纪律只讲一遍：路由/纪律归铁律（stable 唯一来源），工具 schema 只留参数语义，hub 骨架只声明段结构，召回块头训诫压为一行指针；铁律新增 hub 即时段与便签「当前状态」的分工句（两个"当前在做什么"写入口不再含糊）。铁律 1993→1628 字符，memorize/recall description 去重后合计省 ~250 字符（均属 stable 前缀，一次性重建后恢复冻结） |
| reflect 工具族合并 | `mind.reflect` + `think_loop` 工具集重建 + 配置 `reflect_share_reply_tools`（默认开） | 无选择器的反思/任务循环复用回复级装配（get_active_tool_schemas，同族追加式冻结）：实测默认 reflect 目录已膨胀至与 reply 趋同（101-126 vs 106-125 工具），"精简"前提失效，两族交替即整段 30K+ 缓存重写（OpenAI 式隐式缓存按 tools 数组+消息整条做键，实验实证 tools 一字节变化≈全损）；合并后 reply/默认 reflect 共享单一冻结数组族。带选择器的子代理档案仍走精简目录（真实精简 + 一次性 scope 无结转价值） |
| exec_context 步骤预算 | `context_assembly._MAX_RENDERED_STEPS`（12） | `[已完成步骤]` 渲染只保留最近 12 步 + 省略行（"此前 N 步已省略"）；exec_context 每轮全量重建，无界清单在长回复下按轮次平方膨胀 token，防重复操作只需近期步骤；finish_think 的最终执行摘要仍消费全量清单（一次性） |
| 执行摘要入库瘦身 + 执行日志工具 | `reply_finalize._compact_summary_for_history` + `agent/mind/tools/execution_log.py`（per-scope 环形缓冲 + `get_execution_log` 工具，thinking 组） | 对话历史中的 `[已执行操作摘要]` 只入库统计头 + 最近 5 条 + 查询指引（历史每次窗口加载都计费，25 次工具级长轮次不再以千字符摘要挤占窗口）；完整清单进进程内环形缓冲（每会话 8 轮），AI 经 `get_execution_log(turns=)` 按需取回。完整版仍随 EVENT_AFTER_REPLY 发射（SkillReviewer 种子）。缓存影响：只改历史层新写入条目的字节（水位线后纯追加区，旧前缀不动），工具注册走追加式冻结一次重建 |
| 缓存命中状态行 | `round_helpers._cache_status_hint` + `build_execution_context(cache_hint=)`（budget_hint 同族先例） | 上轮真实 usage 的命中率与 read/输入 tokens（口径归一后的 total_input 为分母）注入 exec_context，AI 自感知前缀缓存健康；注入准入 `last_input_tokens > 0`（首轮/压缩重置轮天然抑制）且端点可观测（不可观测静默缺席不谎报 0%）；配置 `cache_status_hint_enabled`（cache/prompt 组） |
| 非输出提示独白信号驱动 | `think_loop._handle_tool_round` | "工具结果仅你可见"提示只在**本轮工具调用伴随文本独白**时注入（独白 = 模型误以为文字可达用户的信号）；静默工具轮零注入——exec_context 每轮已有输出契约，重复追加是纯 token 烧耗 |
| 规划态势轮内注入 | `agent/planning/situation.py`（版本化快照 + provider `plan_ops`，priority 30 会话操作态势档） | 活跃目标/计划快照（goal_id/标题/步骤进度/步骤状态标记，当前 scope 的执行计划置顶）经 provider 层**每轮**注入，取代旧 recollection 每回复一次的 `_blk_goals`——修复回复中途目标被删除/收敛后快照过期、AI 拿过期 goal_id 连续打 update_goal not_found 的回归。单一数据源：全部规划写路径（tools 的 goal CRUD / tracker 的 `_persist`·`submit_plan`）变更后调 `situation.invalidate()`，快照按版本失配（+60s 再同步窗口兜底越轨写入）单飞重建，稳态渲染零 I/O；reflect 前缀 scope（任务/子代理）不注入保持 lean 语义；goal CRUD 的 not_found 错误附 `active_goals` 简报供 AI 一次自纠，update_goal 越界 step_index/非法 step_status 返回 PARAM 错误（此前静默 no-op 返回 success）；配置沿用 `goals_inject_enabled`（planning/core 组），group=planning 随工具组启停联动 |

#### 记忆枢纽化：LLM 检索规划 + 异步深探 + 图谱治理（第十三轮新增）

召回链路升级为记忆系统的枢纽：首轮同步召回由 LLM 规划驱动（非被动关键词匹配），LLM 思考期间异步深检索增量注入（一块连续记忆面、全程防重复），cognee 检索面全量接入，图谱获得遗忘与 AI 策展。

| 机制 | 位置 | 说明 |
|------|------|------|
| LLM 检索规划 | `memory_retriever.plan_retrieval`（公开 API，升级自 `_rewrite_query`，仍受 `memory_query_rewrite_enabled` 门控） | 轻量 LLM（light_llm 通道，可经 `memory_light_model` 指定专用轻量模型降低关键路径延迟）把对话尾部转成结构化计划 `{queries(1-3 互补), entities, deep_needed, rationale}`，失败/超时回退原查询单发（预算 `memory_plan_budget_seconds`，0 = 按召回总超时 40% 份额派生、召回路径中至多占总超时减 1s，独立直调缺省 8s；LLM 调用与实体解析合计单一 wait_for 收口）。多计划查询并行经 federated_search 后 `merge_consensus`（retriever 公开 API）融合：同键（fusion `dedupe_key`：anelf_memory_id / 内容哈希）取最高分，≥2 查询命中 ×1.1 共识加成（确定性可靠性信号）。计划实体经 `GraphStore.resolve_nodes_for_tags` 解析为图谱节点 → 原生一跳邻域 + cognee `node_name` 定向检索 |
| 异步深探 + provider 注入 | `agent/memory/probe.py`（DeepProbeHub + RecallLedger + provider `memory_deep_probe`） | 回复路径（recollection `fire_probe=True`，心跳/任务/子代理零影响）检索完成后：基底层产物入召回账本 → 规划判定 `deep_needed` 或已解析实体节点时 `spawn()` 异步启动深探（在 5s 召回超时之外与 LLM 首轮思考并行）：cognee GRAPH_COMPLETION / GRAPH_COMPLETION_CONTEXT_EXTENSION / node_name 定向 + 原生图谱邻域。分两阶段刷新（类脑唤醒：快段原生图谱邻域毫秒级先渲染注入，慢段 cognee LLM 检索完成后并入更新）；增量行经 `recall_format` 行格式化（与基底层召回同构的 💡 归属标注 正文（时间 记）），分节条理化（▸ 标题 · 说明 + 缩进条目）；完成后写入 hub 持久渲染缓存（`state.rendered`），经上下文提供者 `memory_deep_probe`（priority 34、group=memory、`memory_probe_inject` 门控）每轮读取注入 provider 层——异步完成前为空零注入，完成后每轮在场且字节稳定（无新产物拿旧值，不消失），新回复 begin_reply 重置（不跨回复持久，防与基底层召回常驻重复）。provider 消息不进压缩历史（每轮重新收集、逐字存活），位于最新工具结果之后注意力最强处。触发按需非被动（LLM 规划判定），单飞防重，per-scope 新回复替换 + 惰性 TTL 清扫；配置 `memory/probe` 组（enabled/max_chars 1600/timeout 60s），指标 probe.* |
| 召回账本（三键防重复） | `probe.RecallLedger`（键归一权威 `memory_types.normalized_content_key`） | per-reply 三键集合（结果 id / 图谱边 id / 内容归一前缀），三条召回通道共用：基底层注入（recall_split 末尾 + load_relation_snippets 边 id）、AI recall 工具返回（tools.py 记账，探针不重复 AI 已取回内容）、异步深探渲染前查账——一次回复内同一事实只出现一次（"一块连续记忆面"的机械保证）；`begin_reply` 重置 |
| cognee 检索面全量接入 | `cognee/fusion.py` + `cognee/config.py` | ① `federated_search`/`search_cognee`（公开）新增 `node_names` → cognee `recall(node_name=...)` 定向检索通道（每数据集一次，recall 工具 deep 模式/探针/规划实体定向三处共用）；`parse_memory_projection` 在边界解析投影文档头（干净正文入 snippet、Tags 回填结果标签——归属标注/上下文加权/联想种子对 cognee 结果同样生效，所有消费面一次受益）；② `deep_search_types` 默认追加 GRAPH_COMPLETION_CONTEXT_EXTENSION、SUMMARIES（存量配置=旧默认时一次性迁移升级，自定义列表原样保留；不支持类型运行时静默跳过）；③ `cognee_weight` 0.8→1.0 平权（来源优先级已保证原生胜出）；④ 被动路径补传 `query_tags`（scope 数据集推导缺口）；⑤ 深类型只经探针（异步）与 recall 工具（显式）发生，被动召回保持轻量 |
| 关系投影 scope 隔离 | `coordinator.graph_dataset_for_node` + `fusion.datasets_for_scope` | 实体型节点（user:/group:）投影入 per-scope 数据集 `{prefix}_relations_{type}_{hash}`（scope id 派生与记忆数据集同构），自由型节点（topic/person/concept…）仍入全局 `{prefix}_relations`——实体私人关系网络按 scope 隔离检索（消除跨 scope 稀释），公共知识保持全局共享；删除按 mapping 数据集路由，存量经既有 rebuild_cognee 重建迁移 |
| 图谱遗忘与衰减 | `graph/store.py`（relax_edge_strength / forget_weak_edges / archive_orphan_nodes + access_count/last_accessed_ns 列与 `_record_edge_access`）+ consolidator 第 9 步 | 检索命中即计数（edges_for_scopes/query_relations 批量记录）；边强度向基线 0.5 松弛（访问护盾 ÷(1+ln(access_count))，与记忆 importance 松弛同公式）、强度 <0.25 且超 90 天软归档（复用 set_relation_archived 自动触发 cognee 投影更新）、孤立自由型节点超期归档（user/group 会话锚点永不自动归档）；阈值保守（松弛 30 天起/归档 90 天起），配置 `memory/graph` 组，报告字段进 ConsolidationReport，指标 graph.relaxed/forgotten |
| 图谱治理议程（AI 策展） | `graph/curation.py`（阈值配置 + 议程组装；数据访问在 `GraphStore.curation_facts`）+ 工具 `graph_curation_agenda`（group=graph, tags=core/heartbeat）+ 任务 `config/tasks/graph_curation.json` + 心跳 `[图谱治理议程]` 摘要行 | 事实归系统、决策归 AI（对齐技能 curator 范式）：确定性事实生产（弱边/陈旧边/同主语同谓词歧义对/同类型同称呼疑似重复节点/超阈度数枢纽异常，阈值 `memory/graph` 组可调）——心跳维护段渲染摘要进心跳日志，AI 经 `graph_curation_agenda` 读完整议程、用 graph_merge_nodes/graph_remove_relation/graph_update_relation 执行治理（graph_curation 任务 heartbeat 模式定期消费，处置摘要写心跳日志；user/group 锚点与人工强关系受 prompt 保护） |
| 整理回收节奏放缓 | `memory_consolidate_every_n_ticks` 默认 12→48 | 心跳 300s 下约 1 小时→4 小时一轮全量整理（记忆遗忘/松弛 + 图谱衰减/遗忘同频），可配置中心热调 |
| 召回测试面板 | `POST /api/memory/recall-test`（编排归 services/memory.py，复用 retriever 公开 API：plan_retrieval/merge_consensus） + 前端 `pages/memory/RecallTester.tsx`（Memory 页「召回测试」Tab + CogneePanel 顶部嵌入 cognee 预设） | 与真实召回同管线（规划→多查询共识融合→关系/遗忘层）的无副作用执行：展示 LLM 检索计划（queries/entities/deep_needed/rationale）、检索通道数据集与类型、按来源分组结果（完整 provenance/score）、关系网络、遗忘层、各阶段耗时（plan/search/total ms）；不记访问不触发探针；`search_types` 参数可逐类型测试 cognee 检索 |
| cognee 版本注记 | `pyproject.toml` | 1.5.4（2026-09-04）锁 `litellm<1.97.0` 与本项目 litellm 1.100 不可共存，**停在 1.5.3**（1.5.4 为无 API 变更的补丁版，无升级收益）；升级前必须检查其 litellm 上界 |

> Model Experience（第十三轮）：① 模型看到什么——检索规划驱动的多查询召回结果（共识命中更可靠）、`[记忆召回·续]` 异步增量（关系/新增记忆/图谱综合分节，与首轮零重复）、recall deep 的 node_name 定向结果、graph_curation_agenda 议程（心跳任务消费）；② token 影响——规划 1 次轻量 LLM 调用（替代原改写，零增量）+ 深探增量每回复 ≤1600 字符一次性 + 图谱综合 LLM 调用仅探针路径（指定廉价模型）；③ 缓存影响——全部落在 tool_chain 尾部动态区（轮顶 merge，append-once）与工具通道，不触碰任何 prompt 前缀缓存层。

#### 子代理可观测性（第十四轮新增）

| 机制 | 位置 | 说明 |
|------|------|------|
| 日志 actor 归因（主 AI vs 子代理） | `core/log.py`（`_log_actor` ContextVar + `bind_log_actor`/`reset_log_actor`/`current_log_actor`）+ `DelegationManager.delegate` 绑定点（`_actor_label`：`子代理@{agent\|role}#{id尾6位}`） | core 持有的通用执行主体标签原语：`log()` 内在脱敏后把非空 actor 渲染为 `[actor] ` 消息前缀——console/文件/环形缓冲区（AI 日志查询工具）/监听器全链路一致，未绑定（主 AI/系统路径）零前缀零开销。委托在 `bind_delegation_id` 同位绑定、同 finally 复位；ContextVar 经 create_task 复制进整个子代理执行树，其 think_loop/LLM/工具日志全部自动带前缀；嵌套委托内层覆盖外层（归因到最内层执行者）；follow_up/后台路径同函数自动覆盖 |
| 全局子代理总览与面板操作 | `DelegationManager.running_snapshot_all`（+ `is_running`；进度 hook 顺带把 iteration/current_tool 写入 `_running` 条目）+ `journal.recent_history`/`read_progress_tail` + `services/delegation.py`（Web 侧唯一入口）+ `web/routers/delegation.py`（`/delegations/overview|history|{id}/progress|{id}/steer|{id}/cancel`）+ 前端 `pages/dashboard/DelegationsPanel.tsx`（Dashboard 概况「子代理」卡片） | 全 scope 运行快照：归属维度（scope/chat_id/started_at）+ 实时进度（展示轮次从 1 起，对齐前端口径）+ 事件归集用量；`running_snapshot(scope)` 重构为共享 `_snapshot_item` + 过滤。历史由账本 started/closed 配对折叠（含 lost），按结束时间倒序。面板操作汇入既有闭环零新机制：**指令**（steer/after 双档，消息标注「来自 Web 面板」回执子代理 AI）、**停止**（cancel 级联，后台委托经注册表完成通知、前台经工具结果归因回馈父 AI）、**进度**（Drawer 轮询进度流尾部）。`ChatService.list_delegations/cancel_delegation` 收编为 DelegationService 委托（单一路径）；聊天页 DelegationCard/单会话端点保持原样，面板为纯增量 |
| 子代理独立思维会话 | `SubAgent.run`（`thinking_session(is_delegation=...)` 包裹 reflect）+ `core/tracer.py`（`TraceSession.is_delegation` + 「子代理 @agent: goal」标签）+ 前端 `SessionList` 徽标 | 思维链路页区分主 AI 与子代理：此前子代理节点经 ContextVar 继承混入父会话——并发委托共享父 `_SessionFlow` 致 round/llm 节点配对错配，后台委托在父会话结束后产生永不收束的孤儿运行中节点；独立会话后配对状态天然隔离（嵌套委托各自再开），tracer 未启用时事件无订阅者近零开销（与心跳内省会话同模式） |
| tracer 稳定性硬化 | `core/tracer.py`（`_MAX_SESSION_NODES`=500 触顶截断标记 + `set_enabled(False)` 收束在途会话）+ 前端（`flow-layout` parent_id 环守卫 / thinking-store SSE 断线重连 REST 全量对齐 + CLOSED 延迟重建 / `TraceNode.status` 补 warning 类型） | 单会话节点数上限防长会话无界增长拖垮内存与布局；关闭开关时在途会话以 `tracer_disabled` 收束（此前处理器注销后永远等不到 SESSION_END，前端永久显示运行中）；布局递归加路径集合防 parent_id 环撑爆栈；SSE 断线窗口事件（含 session_end）丢失后经 onReconnect 补拉会话列表与当前详情（对齐 chat-store 模式） |

#### LLM 钩子面（第十五轮新增）

「在 LLM 思考边界派生带上下文的异步 LLM 工作」的统一注册原语，与心跳/任务系统平行、与 shell 钩子（`agent/hooks`，同步阻塞守门）分层——本面是**异步并行扩员**：同一钩子位置（事件）可挂多个钩子，命中后 `asyncio.gather` 并发拉起，各自独立的治理桶，互不阻塞互不挤占。

| 机制 | 位置 | 说明 |
|------|------|------|
| 上下文快照带出 | `Mind.reply`（创建 completion 容器）→ `think_loop.reply_entry/reply_loop`（透传）→ `think_loop` finally（`completion["messages"]=base+tool_chain`）→ `reply_finalize.complete_reply`（EVENT_AFTER_REPLY payload 增 `messages`） | REPLY 全链路以 completion 容器带出完整消息链（base + tool_chain，follow_up 续跑同款合法 base_messages 形态）；`complete_reply` 将其逐条浅拷贝冻结后随 after_reply 事件发射，error/缺失为空——钩子面以 transcript 档位消费，评审/分析不再只看 ~3K 字符摘要。completion 仅多两行赋值，主对话零行为变化 |
| 钩子注册表与装饰器 | `agent/hooks_llm/spec.py`（`LLMHookSpec` / `HookContextMode` / `HookContext` / `HookRegistry`（event 多值索引，priority 排序）/ `@llm_hook`） | 声明式注册：event（白名单 after_reply/context_pressure/delegation_resolved/llm_end）+ 上下文档位（none/lean/transcript）+ `when` 条件门控（不满足零开销跳过）+ 治理参数（max_concurrent/cooldown/debounce/priority/model/tool_tags）+ owner/source 归属。**llm_end 是高频事件**（每次 LLM 调用后触发，一次多轮回复十余次）：装饰器与 _sdk 桥都强制最小冷却 `LLM_END_MIN_COOLDOWN_SECONDS`（20s，声明更小值也钳到下限），防"每轮 LLM 后都拉起 LLM 工作"的成本失控。同名覆盖幂等，按 owner 批量注销（模块/实体卸载清理） |
| llm_end 快照源 | `agent/mind/llm_invoker.py`（EVENT_THINKING_LLM_END payload 增 `messages`）+ `core/tracer.py::_on_llm_end`（节点 data 剔除 messages） | llm_invoker 发射 LLM_END 时附带**本次调用实际发送的消息链**（已规整）——钩子消费「LLM 刚用完的那份上下文」，无需现场抓取；scope 由 executor 从思维 ContextVar（`ToolActivationManager.current_scope`）推（payload 不带）。tracer 节点 data 剔除 messages：逐轮一份完整上下文驻留内存并经 SSE 广播到思维面板是不可接受的副作用，诊断字段（model/usage/duration/tool_calls…）全保留 |
| 上下文快照与护栏 | `agent/hooks_llm/snapshot.py`（`freeze_messages` / `cap_snapshot_chars` / `prepare_hook_messages`） | 快照 = 浅拷贝列表 + 逐条 dict 浅拷贝（冻结引用，主对话后续 mutate 不污染）；发送边界经 `normalize_for_send` 剥 `_layer/_source`（与主对话同口径）。transcript 受 `hooks_llm_transcript_enabled` 门控（关闭降级无快照）与 `hooks_llm_transcript_max_chars` 字符护栏（超限保头 70% 尾 30% 截断） |
| 并行执行与治理 | `agent/hooks_llm/executor.py`（`HookExecutor.dispatch`（后台调度立即返回 + `_RecursionGuard` 防递归）+ `_spawn`（冷却锚点同步记录 + task 登记 `_running`）+ per-hook/per-scope cooldown/debounce + 全局池 semaphore）+ `runtime.py`（事件装配 + `hooks_llm_runtime_port`）+ `configs.py`（`hooks_llm/*` 组） | 治理件全复用：**异步扩员**——dispatch 同步调度、钩子在独立 task 执行，emit 方（complete_reply/llm_invoker）不被钩子 LLM 阻塞；防递归（钩子执行树 ContextVar 标 origin，跨 create_task 仍拦截其派生事件，防自我激励）；并发（全局池默认 2 + per-hook 默认 1）；频控（**cooldown 锚点在调度受理时同步记录**——连续同步触发不漏判，per-hook 信号量天然串行覆盖池排队；**debounce 用 call_later 句柄 + 最新快照槽**——未触发句柄取消是确定性的，合并为一次取最后快照是结构保证）；观测（`bind_log_actor`/`bind_usage_scope`）；路由（`BackgroundTaskRegistry` 完成走既有 unclaimed→wake_budget 通道）。runtime 经 LateBinding 由 bootstrap 实例化、wiring 施绑后 `start()` 订阅事件（低 priority + owner=hooks_llm），并注册为 Lifecycle 组件——**关停 drain**（`runtime.drain`：先 stop 停订阅切断新触发，再等运行中的钩子自然收尾、超时才取消，对齐全局"进水口先停、思考后收"drain 语义，进行中的评审不被硬取消写一半） |
| 技能后台评审迁移 | `agent/skills/background_review.py`（`SkillReviewer` 经钩子面注册 `skill_review` 钩子） | 评审留在技能系统内部，钩子面是执行载体：event=after_reply + context=transcript + when=无错且有快照 + tool_tags=["skills"] + 6 轮上限。评审材料从「执行摘要」升级为「完整 transcript + 四问框架」——工具结果细节不再被摘要蒸馏丢弃，任务方法/排障经验类技能的沉淀判断材料更完整；防抖（per-hook 并发=1）与库健康/相近候选供给不变 |
| 任务事件触发 | `agent/task/model.py`（`trigger_event` 字段 + 序列化）+ `agent/task/event_trigger.py`（`sync_task_event_hooks` reconcile 装配，owner=task.events）+ `agent/heartbeat/engine.py::_sync_event_triggers`（构造/reload 调用）+ `agent/task/tools.py`（create/update/list 暴露） | 任务的第五种触发方式（与 heartbeat/scheduled/idle/manual 正交）：时间调度管"何时跑"，事件触发管"发生了什么之后跑"。带 trigger_event 的任务经钩子面注册 `task_event:<name>` 钩子（context=none 不继承触发会话上下文），命中后调 `HeartbeatEngine.run_task`——复用 `_task_inflight` 同任务去重与执行历史落盘；不进 heartbeat.json 调度。reconcile 语义：任务 CRUD 经 reload 即时重建、无孤儿钩子。AI 经 create_task/update_task 配置 |
| 实体桥接 | `entities/_sdk.py::register_entity_llm_hook`（`**spec_overrides` 直转 LLMHookSpec，治理字段新增无需改桥签名；延迟 import，try/except 返回 bool） | 实体经 _sdk 桥注册钩子（owner 缺省取实体模块名），达到事件条件即并行拉起、不卡主思考；entities→agent 唯一豁免通道同 push_notify 模板 |
| Web 观测与配置 | `services/hooks_llm.py` + `web/routers/hooks_llm.py`（`GET /api/hooks-llm` 只读总览）+ 前端 `pages/settings/LlmHooksPanel.tsx`（设置页「LLM 钩子」Tab）+ `pages/config/TaskForm.tsx`（trigger_event 编辑） | 面板列出全部已注册钩子（事件/档位/来源/治理参数/归属）与治理配置；钩子的开关与治理参数经统一配置面 `hooks_llm/*` 组（`/api/config/meta`、AI `get/update_entity_config`）热调，Web 不另设写路径。任务 UI 可编辑 trigger_event |

> Model Experience：① 模型可经统一配置面（`get/update_entity_config`，hooks_llm 组）热调钩子面治理参数（启用/并发/transcript 护栏）；② AI 经 create_task/update_task 的 trigger_event 给任务配事件触发；③ 钩子产出不进主对话上下文（经后台任务注册表 + wake_budget 路由），评审/分析在思考边界并行完成、不卡主思考；④ 缓存：transcript 快照复用刚写热的主对话前缀（评审前缀命中），尾部动态区漂移只损自身。

#### 记忆投影防护（第六轮新增）

| 机制 | 位置 | 说明 |
|------|------|------|
| cognee 投影内容指纹跳过 | `store/_shared.py::projection_content_hash` + `cognee_queue.enqueue_sync` + coordinator `_process_graph_upserts` | 防记忆写入风暴打爆磁盘写盘配额（2026-08 实证：24h 211 次无效 update 逐条重跑 cognify，Kùzu checkpoint 5 分钟刷 2.1GB 撞爆 macOS 单日配额致进程卡死）。`cognee_entry_map` 新增 `content_hash` 列（投影稳定字段 type/content/source/metadata/tags 的 canonical sha256 前 16 位，**刻意不含 importance**——召回强化 +0.02/松弛回归不再触发重投影；投影文档的 Importance 行陈旧至下次真实变更，可接受）。三层跳过：① memory upsert 入队时指纹与上次成功同步一致 → 作废残留 pending/failed 条目后不入队；在途 processing 批次持有更新负载时不跳过（防"改 B→回退 A"竞态：在途批次完成会覆写映射，回退必须重新排队）；`enqueue_backfill` 走 force=True 保持显式修复语义，rebuild 前本就 reset 清映射。② graph_node 载荷仅是快照触发器，消费时经 `graph/store.render_node_projection` 渲染邻域文档 + **结构指纹**（节点身份 + 各边谓词/方向/对端，**不含强度与证据文本**——重复提及的强化/证据刷新不再触发整篇重投影，仅邻域结构真实变化才重跑，文档中的强度/证据随下次真实变更刷新）。③ 源头：`dedup.apply_update` 合并内容与标签均无变化时直接返回不落库（version/审计/cognee 全不动，对齐 update_memory 工具既有短路）。既有映射迁移后 hash 为空串永不匹配，首次真实变更重投影一次自愈 |
| cognee 写盘熔断 | `cognee/write_breaker.py`（WriteBreaker）+ coordinator `_projection_allowed` | 进程自身磁盘写入速率超阈值时暂停投影认领与自动压缩（两者都是写盘大户），冷却到期重评、仍超限续停，自调节。`WriteBreaker` 按滑动窗口采样 `psutil.Process().io_counters().write_bytes`（cognee/Kùzu 均在本进程内，口径完整；平台不支持时恒放行 fail-open），速率口径 = 字节增量/时长，短时风暴无需等满窗口；采样点在 worker 轮次 + add/cognify 管线边界（防长批次内风暴被整批时长平均掉）。配置随 cognee.json：`write_breaker_enabled`（默认 true）/`write_breaker_threshold_mb`（500）/`write_breaker_window_seconds`（300，即 500MB/5min）/`write_breaker_cooldown_seconds`（1800）；状态经 `CogneeSyncStatus.paused/paused_until/pause_reason` 暴露（cognee_status 工具与 /cognee/status 自动带出），心跳记忆状态行追加暂停提示；`run_in_idle_window` 用户显式作业（备份/迁移）不受熔断阻断 |
| 重启交接闭环 | `entities/devops/service.py`（交接落盘 + wait_idle 重启）+ `entities/devops/tools.py`（RestartHandoffWatcher provider）+ `_sdk` 桥（`is_mind_busy`/`get_current_channel`） | AI 调 restart_app / build_and_restart / update_and_restart 可传 `message` 给重启后的自己留言；重启确认排定后交接（owner scope / 回复路由 adapter_key / 留言）落盘 `<data_dir>/restart_handoff.json`（拒绝时不写，防残留误触发；已排定重复调用仅在留言非空时更新），返回值指示 AI 立即 end_reply；`wait_idle=True` 路径等思维空闲（`is_mind_busy` 轮询 reply/reflect，120s 上限强制关停防死等）让当前回复轮自然收尾——检查点正常清除，重启后无"被意外中断"元消息。bootstrap 末尾 provider `on_start` 消费交接（**读即删文件只消费一次**；超 1h TTL 的陈旧残留仅清理不投递），延迟 5s 经 `push_notify` 向原会话推送"重启成功 + 留言"一次性通知（陈述式措辞显式标注一次性，固化对话历史一条 system 消息，水位机制防历史/轮内双份）并唤醒一轮思维；provider 仅借 on_start 生命周期做启动钩子，provide 恒 None 不注入 volatile 层。Web/API 重启路径不写交接、不等待，行为与历史一致 |

#### 内部调用空闲超时与摘要专用模型（第七轮新增）

| 机制 | 位置 | 说明 |
|------|------|------|
| 内部调用流式空闲超时 | `llm_manager.chat_with_fallback(stream=True)` → `_chat_candidate_stream` + `agent/llm/stream_aggregate.py`（StreamAggregator） | 内部辅助调用（折叠/压缩摘要）可切流式通道：**每 chunk 独立空闲超时**（= 客户端 timeout 配置，思考增量/正文增量都算活动），思考/输出中不设墙钟，完全静默才判死；deadline 仅在尝试开始前/重试决策时检查，不限制单次流总时长。流式失败同样进错误分类/退避/回退链（整次重发）；聚合含 TTFT 与 usage（stream_options.include_usage 同口径记账）。对齐主对话 `llm_invoker._llm_chat_stream_once` 的既有空闲语义 |
| 摘要专用模型与思考档 | `mind.summarize_text` + 配置 `conversation_summary_model` / `conversation_summary_reasoning_effort`（cache/prompt 组，prompt_layers 注册） | 折叠/压缩摘要可指定更轻量模型与低思考档（内部小任务无需深度思考，省时省 token）：模型经 `get_enabled_client` 解析（不存在/停用 WARNING 回落默认），effort 走 per-call options（`_resolve_effort` 优先级：调用方 > 模型配置；模型不支持思考自动忽略；空 = 跟随模型配置），失败仍走默认回退链韧性不降级。compressor 前缀复用路径刻意不动（KV 命中是其核心设计）。Web 配置中心特判复合行（`pages/config/SummaryModelRow.tsx`：ModelSelect + ReasoningEffortSelect） |
| 折叠看门狗分段化 | `conversation_fold.py`（删除 `_FOLD_WATCHDOG=300` 整体墙钟） | 修复"看门狗以 CancelledError 取消整个折叠 → 绕过 drop_on_failure 丢批降级 → 水位线不推进 → 60s 退避后重试 → 无限循环空烧上游"的卡死模式（2026-08 实证：供应商挂死时 300s 看门狗必然早于 270s×N 的链路自身最坏耗时开火）。分段设防：DB 读/写段各 60s 短护栏（`_DB_OP_TIMEOUT`，兜 sqlite 锁等待悬挂占用 scope 锁）；摘要段总护栏 `conversation_summary_llm_timeout`（默认 900s，兜"无限流"病理）——**超时以普通 TimeoutError（Exception 子类）进入既有丢批路径推进水位线**，一次失败即收敛。流式空闲语义见上行 |

#### embedding 成本治理（第八轮新增）

背景（2026-09 实证）：qwen3.7-text-embedding 单周 2285 万 token / 4.8 万次调用，根因是 cognee 管线重复嵌入——cognify 的 `add_data_points`/`index_graph_edges` 每次重投影都把当批实体名/关系文本重新 embedding 并**追加**进 Lance 索引（无按内容去重，EdgeType 索引堆积 21 万+ 行），放大器是 goal 高频重写（`updated_at` 漂移，单 goal 一周 47 次）与关系强化（强度/证据变化使邻域文档 hash 必变）。

| 机制 | 位置 | 说明 |
|------|------|------|
| improve 默认禁用 | `cognee/config.py` `improve_interval_seconds`（默认 0） | cognee improve/memify 默认任务对全图三元组重新 embedding 且无去重，CHUNKS 类召回不依赖它；同步路径不再自动触发，手动 `improve_cognee_dataset` 保留 |
| goal 不投影 cognee + 追踪器去抖 | `store/cognee_queue.enqueue_sync`（source=='goal' 拦截）+ `planning/tracker._persist` | 计划状态 JSON 不是知识：updated_at 高频漂移使投影永不稳定，图谱抽取只产噪音实体；goal upsert 不入队（存量映射转 delete 清理），原生 FTS/向量检索已覆盖召回。`_persist` 比较 updated_at 之外的语义内容，未变不落库（version/embedding/投影全不动） |
| 投影开关 | cognee.json `project_memories_enabled` / `project_graph_enabled`（默认均 true） | coordinator `_process_batch` 按开关直接 complete 出队；memory 投影与主向量库同源（重复嵌入），graph 投影是原生检索没有的增量，可按需关停 |
| 批量对齐与缓存容量 | `embedding/worker._batch_size` + `engine.max_batch_size` + `embed_query_cache_size`（默认 256） | worker 批次取 min(配置, 客户端 embedding_max_batch)，避免 llm_client 内部拆批（32 → 20+12 两次请求）；查询向量缓存容量可配（TTL 另由 `embed_query_cache_ttl_seconds` 控制） |
| embedding 用量账本 | `agent/memory/embedding/usage.py` + `GET /status/usage` 的 `embedding` 段 | 引擎级埋点（查询/批量/多模态全覆盖）：日级 calls/texts/chars，内存累加 + 防抖落盘 `<data_dir>/embedding_usage.json`（保留 90 天，worker close 落盘）。cognee 自带引擎不在此口径（token 数以供应商控制台为准） |
| cognee 向量索引清理 | `scripts/dedupe_cognee_vector_index.py` | 一次性治理脚本（幂等，应用运行中可执行，冲突自动重试）：EdgeType_relationship_name/Entity_name/EntityType_name 按 text 精确去重 + 存量 goal 投影注入 delete 退场；tombstone 由 cognee 自动压缩回收 |
| cognee 1.4.1 → 1.5.3 | `pyproject.toml` | requires_python >=3.10 兼容，litellm>=1.83.7 与锁定 1.95 兼容，ladybug 0.17.1→0.19.0（native 串行门仍生效）；官方声明 1.5.x 无破坏性变更，集成面（add/cognify/search/improve/DataItem/prune）经单测验证 |

#### 实体操作态势注入（第九轮新增）

| 机制 | 位置 | 说明 |
|------|------|------|
| 操作回报装饰器 | `entities/_ops.py`（ToolOp / track_ops） | 工具执行后把操作事实（scope/工具/目标/成败/耗时）回报给实体的态势追踪器：目标参数按名提取、成败按统一错误契约判定（error 键/ok 键/非 JSON 视为成功）、同步异步包装器保种类（iscoroutinefunction 不受影响）、回报异常仅 DEBUG 绝不影响主流程。实体侧以 `track_fs_op`/`track_ssh_op` 绑定各自追踪器，单点接入 |
| 文件操作态势 | `entities/filesystem/ops_context.py`（provider `fs_ops`，group=os，inject_key=`os_context_inject`） | 按会话追踪：当前 Shell 目录（复用 shell_state cwd）+ 活跃目录（路径类目标自动提取，命令类跳过）+ 最近操作流水；停止操作超 `os_context_ttl_seconds`（默认 600s）渲染返回 None 注入自动消失。**目录说明文档规则**：当前目录优先、最近活跃目录其次，按 `os_context_doc_names`（默认 AGENTS.md,README.md，仅纯文件名防路径引导）注入，mtime 缓存自动失效，配额 `os_context_doc_max_files`×`os_context_doc_max_chars`；`os_context_docs_enabled` 可关 |
| SSH 操作态势 | `entities/ssh/ops_state.py`（provider `ssh_ops`，inject_key=`ssh_ops_context_inject`）+ `context.py` | 按 (会话, 连接) 追踪：只展示本会话近期操作过的主机（连接状态/远程目录/最近操作），与全局花名册 provider `ssh_status` 分工；远程目录说明文档在操作成功后经 `manager.run_capture` 旁路通道后台抓取（30s 最小复用窗口 + `ssh_remote_doc_cache_seconds` 缓存），渲染零远程 I/O；TTL `ssh_ops_ttl_seconds` 到期自动消失 |
| SSH 远程目录持久跟踪 | `entities/ssh/manager.py`（compose_exec_command / extract_captured_pwd 纯函数 + ManagedConnection.work_dir） | ssh_exec 命令经 POSIX pwd 捕获尾块包装（brace 组内 cd 失败则无标记、原退出码语义不变），捕获目录对后续命令生效（与本地 shell cwd 语义对齐），结果新增 work_dir 键；标记缺失且有目标目录 = cd 失败/提前退出 → 清除持久目录自愈；显式断开重置；非 POSIX 远端可关 `ssh_work_dir_tracking`（退化回简单 cd 前缀）。snapshot 带 work_dir（Web/API 可见） |

> Model Experience：① 模型看到本会话的实时操作态势（目录/约定文档/操作流水），仅注入正在操作的会话与主机，其他会话零感知；② token 仅活跃窗口存在（默认 10 分钟无操作即消失），稳态为零，上限受 provider max_tokens 与文档配额约束；③ 注入走 think_loop 尾部 provider 层（工具链之后、exec_context 之前，每轮实时收集），不触碰任何前缀缓存层；配置中心/AI 配置工具经 entity/os、entity/ssh 组键热调全部参数

#### 记忆遗忘治理（第十轮新增）

| 机制 | 位置 | 说明 |
|------|------|------|
| 遗忘层兜底召回 | `store/search.py`（search_forgotten / search_archived / search_tombstones）+ recall 工具 `forgotten` 字段 + `restore_memory` 工具 | 归档记忆（向量余弦强信号 + 关键词 LIKE 弱信号 0.35 基准，归档表无索引走分批扫描）与墓碑 gist（关键词命中比例 × 0.3，最低权重）统一检索，与主检索并行执行。采纳规则：归档向量强匹配（≥ `memory_archive_recall_min_score`，默认 0.5）随时浮现；弱命中与全部墓碑仅主检索无果时出现（"似曾相识"而非干扰）。归档项 `restorable: true`，AI 经 `restore_memory(id)` 恢复（向量/访问记录原样回填零重嵌，审计落 restore 事件）；墓碑 `restorable: false`，hint 引导基于梗概重新 memorize。附带条数上限 `memory_forgotten_recall_limit`（默认 3） |
| 遗忘墓碑表 | `memories_tombstone`（connection schema）+ `purge_archived_memories` | 归档物理删除前把 gist（内容截断 200 字符 + 标签 + 来源/原因，**不存向量**）留入墓碑表——实体虽删，"曾经知道什么"的元记忆仍在。行数硬上限 `memory_tombstone_max_rows`（默认 5 万，0=不限），超限 FIFO 淘汰最老（purged_at_ns + id 序），长期运行严格有界；purge 按归档时间最旧优先。memory_stats 健康状态带 archived_memories/tombstones 计数 |
| 检索练习效应 | `relax_importance` 访问护盾 | 重要性松弛有效速率 ÷ (1 + ln(access_count))：历史访问越多的记忆向基线回归越慢（10 次 ≈ ÷3.3，25 次 ≈ ÷4.2），常被想起的记忆更抗遗忘；纯公式调整零新增数据，访问 0/1 次行为与旧版一致 |

> Model Experience：① recall 返回可能附带 forgotten 字段（归档可恢复项 / 墓碑梗概 + forgotten_hint 恢复指引），restore_memory 常驻 memory 组；② token 仅召回工具返回时按需出现（≤3 条 × 300 字符），无命中零开销，稳态为零；③ 全部为存储/检索层改动，不触碰任何 prompt 前缀缓存层；配置中心经 memory/recall、memory/consolidation 组键热调

#### 记忆标签系统（第十一轮新增）

| 机制 | 位置 | 说明 |
|------|------|------|
| 记忆体系铁律（系统级提示词文档） | `agent/memory/rules_doc.py`（文档持有：种子/读取/保存）+ `config/memory_rules.md`（文件载体，ConfigPaths.MEMORY_RULES，随配置目录搬迁）+ `context_assembly._memory_rules_text` | 写入路由（五系统 + 技能分流，一条信息只进一个系统、出处用指针）/ 标签纪律（前缀语义、打标即入联想网络、写前 memory_index 查既有形态、tags 软加权 vs filter_tags 硬过滤）/ 主标签记忆用法 / 披露边界 / 查询路由 / 落盘诚实 / 检索纪律。独立 Markdown 文档：缺失时以模块常量 DEFAULT_RULES 种子落盘（人类直接拿到完整默认文档）；AI 无写入路径，人类经 Web 记忆页「规则」标签整文档编辑（GET/PUT /api/memory/rules，经 services/memory.py 收口）或手编文件。读取走 mtime 缓存（stable 指纹计算每周期仅一次 stat），生效文本参与 stable 指纹门控（编辑后工具块重建一次再冻结） |
| 主标签记忆（main:hub） | `agent/memory/hub.py`（骨架/自愈/渲染）+ `_blk_hub`（context_assembly，vol 36 独立块）+ tools 侧三守卫 | 带保留标签 `main:hub` 的 PERMANENT 记忆，每回复周期置顶注入（完整与 lean 模式同口径）：AI 经 memorize（type:permanent + main:hub）整段 upsert 维护——`_upsert_permanent` 对 hub 仅按 HUB_TAG 单标签匹配防重复；`_load_permanent_pins` 排除 hub 防霸占 pin 名额；`forget` 拦截 hub 归档；心跳维护段 `ensure_hub` 自愈重建骨架。注入预算 `memory_hub_inject_max_chars`（默认 3000，保索引段截尾部） |
| 便签受管区块硬保护 | `agent/memory/notes.py`（`_MANAGED_BLOCK_RE` + `_assert_managed_blocks_intact`） | `<!-- AUTO:name BEGIN/END -->` 标记对圈定的系统受管区块，便签写工具（write_notes/save_notes_content/write_memory_file/patch/edit_lines/write_section/delete_section）写入前校验逐字节保留，改动/删除即拒绝；系统写入路径（update_memory_status_block 直走 `_atomic_write`）天然豁免。同时修复「当前状态」分界容错：`_STATUS_HEADING_RE` 锚定标题文本而非精确字节（`## 四、当前状态` 等编号子标题同样命中），静态指南正确归 stable 层、状态块不再双重注入 |
| 标签索引观测 | `agent/heartbeat/engine.py::_write_memory_status` | 状态区块仅保留 AI 可行动项（库容/cognee 同步与熔断/最近整理/便签超标），注入准入 = 看到能改变行为；标签膨胀提醒条件化（总数超 `memory_tag_bloat_threshold`（默认 400，0=关）才注入归并提醒行）；运维遥测（召回通道计数/写入去重分布/24h 变更审计/高频标签明细）不进 prompt，由 memory_stats 工具按需查询（`metrics.snapshot()` + `get_audit_summary` 组合进返回值） |
| 心跳态势注入 | `agent/heartbeat/engine.py::_write_heartbeat_status` + `agent/memory/notes.py`（通用受管区块 `update_managed_block`/`read_managed_block`，AUTO:heartbeat-status）+ `context_assembly._blk_heartbeat`（heartbeat 层，变动率 39 与 status 同族、层名序在前） | AI 自我感知通道：心跳节奏（enabled/间隔）、任务规模（**计数级**——具体内容对决策无价值，经 list_tasks/task_history 按需取）、每条调度的节奏折算与最近一次执行（时间/状态/耗时，消费执行历史 `get_summary`）、最近失败任务告警行；区块头指向详情与操作工具（list_tasks/task_history/get_heartbeat_log + create/update_task/set_task_schedule/execute_task）。缓存纪律：写入仅在内容变化时落盘（任务未执行期间字节冻结），刻意不含 total_ticks/beat_count 逐拍计数；注入走尾部动态区 status 族（对 stable 前缀/摘要/历史零影响）；任务/调度 CRUD 经 engine.reload() 即时刷新（不等下个心跳拍）；lean 任务上下文不注入 |

> Model Experience：① 模型看到 stable 工具块的完整记忆铁律（写入路由/标签纪律/主标签用法 + hub 即时段与便签「当前状态」的分工）/ context 层 vol 36 的 `[主标签记忆]` 独立块 / 状态区块（仅行动项，标签膨胀超阈值才多一行提醒）；② token：铁律 ~1600 字符（stable 恒定摊销为零）+ hub 块 ≤3000 字符可配 + 状态区块常态 <150 字符，遥测经 memory_stats 按需取；③ 缓存：铁律字节恒定永久命中，hub 独立消息只损自身，分界修复后心跳状态改写不再击穿 stable 人设块（净收益）；配置中心经 memory/recall（hub 预算）、memory/consolidation（膨胀阈值）组键热调

#### 视频理解链路与能力声明（第十七轮新增）

| 机制 | 位置 | 说明 |
|------|------|------|
| supports_video 能力声明 | `agent/llm/config.py`（`LLMClientConfig.supports_video`，序列化往返）+ `agent/vision/capabilities.py::_run_video_understand`（严格候选过滤） | 视频识别链**仅投送声明 supports_video 的视觉模型**——整段视频 base64 体积大且多数视觉端点不接受 video block，未声明即视为不支持，无声明模型时报配置缺失引导显式开启（不做全链喷洒试错）。声明入口三处同源：llm_clients.json / Web 模型编辑器「视频理解」开关（勾选视觉后出现）/ AI `update_model_config`（model_control 白名单）；视觉页「生成能力」页签经 provider `status_details` 钩子展示声明模型清单 |
| describe_video 直发 HTTP | `agent/llm/llm_client.py::describe_video` + `_parse_video_describe_text` | 不经过对话协议层与 litellm——两侧转换层都不认识 video block（litellm Anthropic 转换层校验拒绝；Responses 转换层无 video 映射，不识别部件透传后被端点静默忽略，表现为"没收到视频"）。anthropic 端点原生 video block（/v1/messages），其余端点 OpenAI 兼容 video_url block（/chat/completions），端点不支持即以 HTTP 错误显式暴露供候选链回退。MiniMax 视频理解仅官方 Anthropic 兼容端点（type="video" block，仅 M3，内联 ≤50MB）——openai/responses 通道的 M3 条目不可声明 supports_video，须挂独立 anthropic 供应商条目（model_types=["vision"] 的纯视觉条目经 `_ensure_priorities_complete` 通用循环进 vision 优先级、不进 chat 链） |
| 任务 memory_type 单一权威 | `agent/task/model.py::TASK_MEMORY_TYPES` + `_parse_memory_type`（严格解析）+ `agent/task/tools.py`（校验同源派生） | 任务产出允许的记忆类型集合单点定义（reflection/semantic/episodic），from_dict 与 create/update_task 校验同源；非法值**抛错显式暴露**（注册表逐文件容错降级 WARNING）——修复静默钳制事故：旧 `_MEMORY_TYPE_MAP` 缺 episodic，全部 episodic 任务文件加载时被无迹回退为 reflection |

> Model Experience：① 模型可调 recognize_video（media:video 标签激活）与 media_config 能力指南（vision 双工具 + 实时声明模型清单）；② AI 经 update_model_config(model, supports_video, true/false) 自助开关视频能力（与 Web 面板同源持久化）；③ 视频不直注主模型上下文（无本地 block 注入路径），识别结果以文字描述返回，不触碰任何前缀缓存层

#### 实时传输与语音接入（第十八轮新增）

| 机制 | 位置 | 说明 |
|------|------|------|
| 统一实时枢纽 | `core/realtime_hub.py`（取代旧 `core/sse_hub.py`，已删除） | SSE（`web/routers/chat.py:/stream`）与 WebSocket（`web/routers/chat_ws.py:/chat/ws`）共享同一订阅/分发面。订阅者带身份（connection_id/client_kind=web\|desktop）与 topic 过滤；帧按类型分级背压——增量帧（delta 等）队列满丢旧保新，终态帧（reply/media/turn_end/ui_command/approval_request/share）不可静默丢，腾不出位即判死订阅者（`Subscriber.dead`，连接侧断开由客户端重连 + /history 重同步） |
| WS envelope 协议 | `web/routers/chat_ws.py` | 上行文本帧 JSON `{"action", "request_id"?}`，二进制帧仅承载麦克风 PCM；下行 `{"type", ...}` 数据帧（事件名与 SSE 帧一致）+ `{"type":"status","message":{"code","details"}}` 结构化错误帧（错误码封闭集见模块头部常量）。action v1：ping/send_message/interrupt/ui_answer/ui_state_report/voice_start/voice_end + 音频会话别名 start_session(input_type=audio)/end_session。client=desktop 单槽位"最新连接赢"（CONNECTION_SUPERSEDED 踢旧连接防双写），client=web 多开不踢。鉴权自校验（BaseHTTPMiddleware 不覆盖 WS）：cookie `_anelf_token` 或 `?token=`，与 HTTP 面同一密码体系 |
| 二进制音频帧 | `core/audio_frames.py` | 麦克风上行二进制帧格式（magic 4 字节标识 + LE uint32 采样率 + PCM16），采样率白名单 {16000,24000,48000}，单帧 120ms 漂移门；坏帧只丢帧不关连接。Web 实时语音与桌面壳共用同一帧格式 |
| 语音会话模块 | `agent/voice/`（独立可插拔） | MicLease 式租约（同 owner 同时一条语音连接，冲突抛 VoiceLeaseBusy 显式拒绝）+ 输入预处理链（见第二十四轮：降噪/AGC/限幅，成段前 flush 冲刷）+ 段内端点检测用 `core/audio_frames.py::EnergyVad`（滑窗 10 分位数噪声地板 + 上行限速防起始追平/语音内慢速防切断长句、迟滞双门限、onset 连续帧确认、DC 阻断滤波；配置键 voice_vad_floor_min）+ 看门狗兜底收束（无条件，防租约泄漏）+ 成段写 WAV（workspace/uploads/voice/）。会话开启时绑定 VoiceDelivery（用户/会话/落点频道），成段随 utterance 交 `voice_sink_port`（**LateBinding 端口**，跨层桥成因；组合根 `agent/runtime/wiring.py` 施绑 `deliver_utterance`）。配置组 `voice`（turn_detector/onset_ms/silence_ms/smart_turn_*/denoise/agc/min_utterance_ms/max_utterance_s/vad_floor_min 热读取）。成段广播 EVENT_VOICE_UTTERANCE（钩子面/未来 Realtime 管线消费）；transcript 字段为 Realtime/ASR 预留 |
| 语音接入统一入口 | `agent/voice/deliver.py` + `services/voice.py` | deliver_utterance 把语音段作为 VOICE MessageSegment 经 **AgentApp.send_message 统一入口**（→ Everything → pipeline → Mind）投递，与频道语音消息完全同路径（[media_type:voice] 标签 + voice 工具链），不发明第二条语音路径；services/voice 是 web 层纯门面（只封 VoiceDelivery 绑定租约，无投递逻辑），VoiceLeaseBusy 经此再导出（web/routers 不得直接依赖 agent，经 services 收口） |
| 富媒体上下文注入 | `core/context_provider.py` + `agent/mind/tools/think_loop.py::_collect_provider_messages` + `agent/mind/tools/vision.py::build_provider_media_message` | ProviderSnapshot 携带 `media: List[ContextMedia]`（core 中立表达，kind=image/audio/video）；collect() 产出 VolatileClip{text, media, source}。组装点按 kind 分派（协议物理约束）：clip 文本走 system 消息；image 在视觉模型下集中组 user 角色多模态消息（image block 仅 user 角色可靠；等距抽样 ≤3 张保头中尾；图在文前），非视觉降级为 [media_type:image] 标签；**audio/video 一律降级为 [media_type:xxx][media_path:...] 标签**并入 clip 文本（litellm 对话转换层不接受这两类 block，视频直发 HTTP 教训见 describe_video），AI 经既有媒体工具链处理——不静默丢媒体。位置在工具链之后、exec_context 之前，`_layer: provider`，不触碰 stable/历史前缀 |
| ai_desktop 媒体契约 | `entities/ai_desktop/framework.py` | DesktopModule 新增 `render_media() -> List[ContextMedia]`（默认空）；`render_context()`（文本）不变，`render_media()` 汇总各组件媒体；文本必须自足（降级路径只文本+标签生效） |
| 段分发失败显式化 | `agent/channel/base.py::_forward_via_segment_map` | 无映射段类型与 send_* 失败不再静默跳过：逐段记入失败清单，任一失败整体 success=False 且 error 列明原因（已成功段 message_ids 仍返回）；message_id 收集尽力而为——无平台 ID 的频道（如 webui 广播）success 即送达不误判。qq 频道接入模板（`_SEGMENT_SENDERS` 声明 text/image/voice/file，修复 voice/file 段静默丢失） |
| media 帧 URL 契约 | `channels/webui/adapter.py::_media_frame_url` + `core/path.py::upload_url_for/upload_path_to_url/parse_upload_url` | URL 规则单点定义在 core.path（web 上传端点建 URL / webui media 帧改写 / services 反解三方共用，禁止各处手拼）；media 帧只传可服务 URL：上传目录本地路径改写 `/api/chat/files/{type}/{name}`，http(s)/\|/api/ 透传，其余原样透传并记 DEBUG |

> Model Experience：① 模型看到的 provider 注入不变（文本 system 消息），视觉模型额外看到一条 user 角色画面消息（"[系统提示] 以上 N 张图片是环境信息附带的实时画面"）；② token 影响：每轮增量 ≤3 张压缩图（ensure_base64_report 归一化），非视觉模型仅一行提示文本；③ 缓存影响：画面消息位于 provider 层（工具链后、exec_context 前），字节随轮变化但不触碰 stable/summary/conversation 前缀

#### 记忆进阶：证据体系 / 反思生命周期 / 人格双层 / 积极性（第十九轮新增）

| 机制 | 位置 | 说明 |
|------|------|------|
| 证据数学 | `agent/memory/evidence.py`（纯函数零依赖） | 记忆/反思的「可信度」维度（与 consolidator 的「记不记得住」分维，不并存两套衰减）：rein/disp 双通道各有独立时钟与半衰期（负向 180 天刻意长于正向 30 天）、读时衰减不改存储、importance 阶梯种子（≥0.9 直通 0.8）、用户确认连击加成、protected（PERMANENT/metadata protected）恒 +inf。信号回流在 `dedup.apply_evidence_signals`：skip=用户复述确认（+1.0 金标准）/update/merge=谱系存活（+0.5），memorize 与 auto_capture 两条写入路径共用 |
| 反思生命周期 | `agent/memory/reflection_lifecycle.py` + consolidator 第 2.5 步 | 对象是 `type:reflection` 标签的记忆（self_reflection 任务产出经 `seed_reflection` 播种，memorize/任务结果两写入点接入）。状态机 pending→confirmed（score≥1.0）→晋升（score≥2.0 且过冷却）/denied；负分进 sub_zero 每日倒计时到期归档（走既有归档/墓碑）。晋升 = LLM 合并裁决（promote/merge/reject，复用 dedup.light_llm）写入目标画像——**晋升是人格生长的唯一通道**；LLM 失败只退避重试绝不静默晋升，5 次死信。终态幂等，重入安全 |
| 人格双层 | `agent/memory/self_profile.py` + `agent/mind/recollection.py` | 宪法层=personas/*.json（用户编辑，stable 层冻结）；生长层=`agent:self` 自画像（personality 表 + ENTITY 记忆镜像双写，覆盖式更新前备份）——随画像区每轮注入（profile_msgs 首位）；已确认反思块紧随其后（`load_confirmed_block`，score>0 才呈现）。关系动态走图谱 `agent:self` 节点（NODE_TYPES 已注册 agent）。铁律（config/memory_rules.md）已登记路由：自我认知只能写反思等晋升，禁止直接改自画像 |
| 积极性频率 | `agent/mind/proactivity.py` | `proactivity_level`（0-1 默认 0.5，mind 组热读取）：元决策 prompt 注入档位指导（`proactivity_guidance`）、心跳 idle 调度拍数按 `1.5-level` 缩放（`idle_beats_factor`）；**AI 可自调**——经 update_entity_config 依据用户反馈调整 |
| 事件时间本体 | `agent/memory/auto_capture.py` | event 提取输出新增 `temporal_scope`（episode/state/pattern，白名单校验，拿不准 pattern）落 metadata；date 落 `activity_date`（既有） |

> Model Experience：① 模型看到画像区新增两块——[系统注入·自我画像]（生长层）与 [系统注入·已确认认知]（证据达标的反思），元决策多一行积极性指导；② token 影响：自画像/反思块各 ≤数百字符（随画像预算截断），积极性一行；③ 缓存影响：均在 profile 层（vol 37）与元决策独立调用，不触碰 stable/conversation 前缀

#### 实时语音对话与声音/视觉能力收敛（第二十二轮新增）

| 机制 | 位置 | 说明 |
|------|------|------|
| 实时语音引擎 | `agent/realtime/`（engine/session/playback） | 全双工语音对话：会话状态机 LISTENING/THINKING/SPEAKING + 轮次令牌（turn_id 贯穿 ASR 定稿→思维→TTS→播放帧）+ barge-in（播放/思考中开口即 turn_id+1、取消 TTS、清空播放队列、发 interrupted 收束帧）+ 租约（同 owner 显式拒绝）。播放链 `PcmResampler` 边界连续重采样（相位/尾样本跨块延续，任意源率→播放率默认 48k）+ 有界 `PlaybackQueue`（满时丢最旧音频帧；audio_chunk/audio_done 帧对，final 帧永不被挤掉） |
| 级联管线（默认模式） | `agent/realtime/engine.py::user_turn/_on_delta/_run_tts` | 麦克风帧 → 端点检测 → 流式 ASR 定稿 → **AgentApp 统一入口**进思维（人格/记忆/工具全量生效，与文字消息同一条大脑路径）→ event_bus 回复增量（scope 匹配的 `assistant_delta`/`after_reply` 仲裁，barge-in 后旧轮增量不再送 TTS）→ TTS 管线 → 下行音频帧；回复文本照常走频道事件流（语音只是第二种呈现，不产生第二条对话路径）。ASR 定稿片段同步入核心音频库 |
| 原生三方言 | `agent/realtime/native/`（base/openai/gemini/qwen） | realtime_mode=native 时启用：OpenAI Realtime（session.update/pcm16/server_vad/response.cancel）、Gemini Live（setup/media_chunks/interrupted）与千问实时语音（qwen-audio-3.0-realtime-plus，见第三十八轮）三方言客户端，openai/gemini 凭据取 llm_clients 供应商链（openai/googleapis 匹配）；提供方 speech_started 事件驱动引擎打断；引擎侧麦克风按 input_rate 重采样喂入；系统指令缺省从人格档案组装（realtime_native_instructions 可覆盖） |
| 端点检测 | `agent/voice/turn_detection.py` | TurnDetector 协议 + 梯队实现（voice_turn_detector 默认 auto：smart_turn→silero→energy 缺失自动降级）：smart_turn=语义端点（见第二十四轮）、silero=v5/v6 模型级 VAD（512 样本窗+64 上下文+sr/state 输入）、energy=零依赖兜底；模型经本地模型资产解析（workspace/models/） |
| 流式 ASR | `agent/audio/streaming.py`（asr_stream 类别）+ `entities/audiosync/funasr_stream.py` | StreamingAsrSession 协议（accept_pcm → partial/final 事件）；FunASR 组件滚动窗部分转写（step/window 配置，仅展示辅助）+ 收束定稿（带结构化分段）；无流式提供者时引擎退化为整段缓冲转写 |
| TTS 管线 | `agent/tts/`（providers/sentences/pipeline/decode/builtin）+ `entities/minimax/`（HTTP/WS 组件） | 流式提供者协议（TtsStream=PCM 块流+采样率声明）+ 优先级链运行时降级（首字节前无缝切换、首字节后截断该句）；`SentenceSplitter` 流式断句（CJK 标点/英文缩写与小数豁免/超长软切/短尾合并）+ `strip_for_speech` 朗读清洗（markdown/旁白剥离、CJK 空格规范化、emoji 剔除）；句级管线预取（tts_prefetch_sentences）；压缩流经 ffmpeg 管道解码 PCM16。内置提供者：openai 风格（audio/speech 流式，模型配置 tts 链凭据）/ edge-tts（MP3→解码，可选库）；组件：minimax HTTP（t2a_v2 流式 PCM 直出）/ minimax WS（T2A WebSocket 双工长连接），经 `_sdk.register_tts_provider` 注册同链互备 |
| 通话入口（工作区） | `web/frontend/src/pages/chat/RealtimeCallBar.tsx` + `lib/realtime-voice.ts` | 聊天输入区上方通话条：开关（启停通话）/状态灯（收听·思考·说话）/实时转写/输入电平条（麦克风 RMS）/输出音量滑块（播放链 gain）。通话=频道实时形态，绑定当前会话 tab；声音页不再承载通话入口 |
| WS 语音数据面 | `web/routers/chat_ws.py` + `services/voice.py` | voice_start 增加 mode=realtime：sink 挂本连接订阅队列（JSON 事件走下行泵，音频帧以 __audio__ 标记转二进制 PCM 帧直发，满时丢最旧保最新）；voice_end/断连统一收尾（实时优先）；**麦克风单会话纪律**（成段录音与实时通话互斥，双向 VoiceLeaseBusy） |
| 语音 AI 工具 | `agent/realtime/tools.py`（voice 组） | 仅 `realtime_status`（会话/状态/端点档位/默认音色）。**语音呈现形态归频道**：AI 统一经 send_message 发消息，出口层（`output_tools._speak_if_on_call` → `engine.speak_to_scope`）自动路由——通话会话的消息同步 TTS 播出（她在说→追加队列；思考/空闲→新播报；用户说话中→不插播仅文字送达，结果注记 voice 字段）。AI 无需感知通话状态，无专用语音工具 |
| 说话人标注 | `agent/realtime/engine.py::_identify_speaker` + `user_turn` | 语音轮次附说话人标注：声纹只读识别（match_vector 查询，不建档不累积——入库仍由轮次末尾 ingest 统一负责），命中已知人标注 `[语音 说话人:名 置信:x]`，未命中标注 `未注册`；识别失败/无声纹提供者静默跳过。多环境多人对话中标记"谁在说话"的事实，识别建档与应对方式由 AI 自行决定（realtime_speaker_annotate 可关） |
| 通话状态注入 | `agent/realtime/context.py`（RealtimeCallProvider，volatile 层） | 通话期间注入挂载的通话频道信息（频道/会话/状态）与自动播报说明（消息自动以语音播出）；无通话零注入，realtime_context_inject 可关 |

| barge-in 回声防护 | `agent/realtime/engine.py::_arm_barge_in_check` | 播放/思考中听到的语音不立即打断：先持续观察 realtime_barge_in_onset_ms（默认 250ms），仍是语音才确认打断；确认窗内收束的短促碎响按回声丢弃（不成用户轮）——扬声器回声多为碎响，持续开口才是真打断 |
| 静默失败纪律 | `agent/realtime/engine.py` | 一切失败必须下行成事件：无 ASR 启动拒绝、无 TTS 降级 rt_error 警告、空转写 rt_final(discarded) 收帧、思维投递失败 rt_error+状态回收、回复出错 rt_error 提示、原生通道错误 rt_error+收会话、TTS 零产出 rt_error；纯工具轮（无增量文本）after_reply 后状态必回收 LISTENING；TTS 句级超时（tts_sentence_timeout_s）防提供者挂起拖死会话；客户端 start 等 voice_ack 握手（拒绝即失败不激活） |
| 视觉源激活体系 | `agent/vision/framework.py`（disabled_sources/set_enabled）+ `vision_source_set` 工具 | 视觉源持久化启停（vision_disabled_sources 配置，热读取）：停用即 watcher 拒启/vision_look 拒取/上下文注入排除/外部推送拒收；AI 经 `vision_source_set` 启停，Web 页签同源开关（POST /vision/watch action=enable/disable）——同一配置，两侧一致 |
| 声音能力页 | `web/frontend/src/pages/Sound.tsx` + `lib/realtime-voice.ts` | 前端音频能力收敛更名"声音"（/sound）：总览/实时对话/生成/说话人/时间线/话语检索/识别入库/语音会话八页签；实时面板=AudioWorklet 16k 采集上行 + 48k PCM 播放调度 + 状态条/部分转写/轮次日志 |

> Model Experience：① 语音对话与文字对话共用人格/记忆/工具（统一入口），native 模式仅注入系统指令；② token 影响：cascade 与文字轮等价；③ 缓存影响：无新增前缀扰动

#### 媒体库溶解与能力路由框架（第二十三轮新增）

媒体库实体整体拆分进核心三域（视觉/声音/检索），网络工具实体核心化为检索域；实体层只留平台组件包（内部模型利用与第三方组件引用统一规范化）：

| 机制 | 位置 | 说明 |
|------|------|------|
| 能力路由框架 | `agent/capabilities.py` | CapabilityProvider 协议（name/capabilities/is_configured/run）+ CapabilityRouter（注册表 + 配置化优先级链 + 失败降级 + 错误聚合归因）。链语义：配置键（JSON 字典 {能力: [提供者]}）显式给出非空链时严格按配置；否则默认链 + 声明该能力的已注册组件自动入链（即插即用）。内部模型利用 = 各领域内置 `models` 提供者桥接 llm_clients.json 对应类型模型优先级链；第三方组件经 `_sdk` 注册桥接入同一路由 |
| 视觉能力域 | `agent/vision/capabilities.py` + `gen_tools.py` + `guide.py` | 能力：understand（图片/视频理解，视频仅投送 supports_video 声明模型）/ image_gen / image_edit / video（生成+任务管理）。工具组 vision（recognize_image/recognize_video/generate_image/edit_image/generate_video 常驻 always + 任务管理与 vision_config 配置工具 core）；配置键 vision_provider_priority / vision_default_* / vision_style_presets（vision 组） |
| 声音能力域 | `agent/audio/capabilities.py` + `gen_tools.py` + `guide.py` + `models_asr.py` | 能力：tts（一次性合成，实时流式走 agent.tts 注册表互补）/ voice_mgmt / music。voice_to_text 统一进核心 ASR 链（内部 ModelsAsrProvider 桥接 asr 模型链，默认 priority 50 兜底本地组件）。工具组 audio（voice_to_text/text_to_voice/generate_music/generate_lyrics 常驻 always + 音色管理与 voice_preset/sound_config 配置工具 core）；配置键 sound_provider_priority / sound_voice_default / sound_voice_realtime / audio_models_asr_priority（audio 组） |
| 检索核心域 | `agent/retrieval/` | 联网检索/网页读取/仓库文档/HTTP 请求/文件下载/文档重排序核心化：providers（能力×提供者矩阵：Provider ABC + SearchCap/ReaderCap/RepoCap Protocol；builtin 本地直连/bigmodel 内置，组件经 `_sdk.register_retrieval_provider` 接入）+ fetcher（SSRF 防护直连抓取）+ extractor（三层正文提取）+ robots + rerank（rerank 模型链）。工具组 retrieval（web_search/web_fetch/repo_docs/web_request/extract_page_links/web_download 常驻 always + retrieval_providers/rerank_search core）；配置统一走 ConfigManager（retrieval_proxy/retrieval_active/retrieval_disabled_providers/retrieval_bigmodel_api_key(password)/retrieval_ssrf_protection，retrieval 组） |
| 平台组件包 | `entities/minimax/` | MiniMax 直连客户端封装为四类组件注册进核心：MiniMaxVisualProvider（understand+image_gen）/ MiniMaxSoundProvider（tts+voice_mgmt）/ MiniMaxSearchProvider（检索）/ 流式 TTS（HTTP+WS 双传输）。配置自管于实体 config.json；删除目录即整体拔出（核心路由注册随之消失） |
| 工作区路径统一 | `agent/utils/workspace.py` + `agent.approval.policy.workspace_paths_port`（allowed 判定） | 核心层工具的路径解析/沙箱校验/产物落盘（uploads）统一入口；沙箱准入经端口由 entities.filesystem 施绑（sandbox_enabled + check_sandbox 组合判定），未施绑时仅接受绝对路径 |
| 旧配置一次性迁移 | `agent/runtime/config_migrate.py` + bootstrap `migrate_legacy_configs` 节点 | 老部署残留的 entities/media|web/config.json（gitignored 本地文件）启动时导入 ConfigManager 对应键（media vision→understand 改名/默认音色预设建档/风格预设/优先级链拆分、web proxy/active/disabled/provider_keys.bigmodel + web_ssrf_protection 键名迁移），目标键已有非空值不覆盖，导入后源文件归档 .migrated（幂等） |
| 能力数据面 | `services/retrieval.py` + `web/routers/retrieval.py`（/api/retrieval/*）+ GET /api/vision/capabilities + GET /api/audio/capabilities | 检索页矩阵/切换/启停/凭据/连通性测试/抓取设置；视觉与声音页的提供者状态与生效链。旧的 /api/entity/media/*、/api/entity/web/*、/api/config/web-tools 端点整体移除 |
| 能力前端 | `pages/Retrieval.tsx`（/retrieval，检索导航项）+ Vision「生成能力」页签 + Sound「生成」页签 | 检索页：能力×提供者矩阵 + 抓取设置；视觉页：生成能力链排序（共用 `components/common/CapabilityChainPanel.tsx`）+ 风格预设 CRUD（默认参数在配置 tab 热编辑）；声音页：音色预设库（增删改查+场景指派，见第三十三轮）+ 声音能力链。媒体库/网络工具实体面板随目录删除零残留 |

> Model Experience：① 核心能力主用工具全部 tags=["always"] 常驻 schema（识别/生成/检索/下载），管理工具 core 标签按需激活，能力矩阵经 vision_config/sound_config/retrieval_providers 自发现（含实时可用状态与调用示例）；② token 影响：常驻工具 +18 schema（前缀缓存摊薄）；③ 缓存影响：always 工具并入既有 always 区块，排序冻结机制不变

#### 通话频道化与语音形态数据（第二十七轮新增）

| 机制 | 位置 | 说明 |
|------|------|------|
| 频道实时能力 | `agent/channel/channel_types.py`（REALTIME_VOICE）+ `agent/realtime/protocol.py` | 实时语音是频道能力（channel capability），webui 频道第一个声明并实现；对外接入契约单一定义点（WS 控制流 + PCM 帧格式 + 下行事件表 + 对话数据划分约定），外部客户端按协议连 /api/chat/ws 即可接入，能力探测经 /api/adapters 的 capabilities |
| 通话自动语音路由 | `agent/channel/output_tools.py::_speak_if_on_call` → `agent/realtime/engine.py::speak_to_scope` | **呈现形态归频道**：AI 统一经 send_message 发消息，出口层自动路由——通话会话的消息同步 TTS 播出（她在说→追加；思考/空闲→新播报；用户说话中→不插播仅文字），结果注记 voice 字段；AI 无专用语音工具 |
| 语音形态消息 | `engine._broadcast_transcript`（voice_transcript 事件）+ `output_tools._broadcast_voice_spoken`（voice_spoken 事件） | 对话数据划分：用户语音转写定稿与 AI 语音播出均进频道聊天流（SSE 事件 → 前端消息气泡，voice=transcript/spoken 形态徽标）；对话历史同桶（语音轮与文字轮对记忆/上下文一视同仁） |
| 通话入口（工作区） | `web/frontend/src/pages/chat/RealtimeCallBar.tsx` | 聊天输入区通话条：开关/状态灯/实时转写/输入电平/输出音量；声音页只保留能力管理面 |

> Model Experience：① 通话中 AI 收到频道信息注入（RealtimeCallProvider：频道/会话/状态+自动播报说明），无通话零注入；② token 影响：无新增工具 schema（realtime_reply/realtime_say 已随自动路由退役，voice 组仅 realtime_status）；③ 缓存影响：注入在 volatile 层，不破前缀

#### 记忆代谢闭环：反馈回路 / 时间语义 / 话题纪律 / 防复读（第二十八轮新增）

对标补强四项记忆能力，全部长在既有心跳/auto_capture/上下文管线上，零新循环零新工具：

| 机制 | 位置 | 说明 |
|------|------|------|
| 证据反馈回路 | `agent/memory/reflection_lifecycle.py`（load_verification_block / classify_reflection_feedback / record_user_feedback）+ `auto_capture._extract_and_store` 尾部 | 证据系统此前只进不出（apply_disputation 零调用方）。闭环：pending/confirmed 反思经画像区"待验证认知"块呈现进对话（呈现即登记 surfaced_at，24h 冷却防追问骚扰）→ auto_capture 周期把该 scope 用户新消息对着"已呈现未裁决"条目跑 light_llm 分类（confirmed +1.0 / denied +1.0 反驳 / ignored -0.2）→ 证据回流驱动晋升/归档；Web 记忆页 LTM 反思条目 ✓/👎 按钮走同一 record_user_feedback（人审直改）。**可见性规则**：带 user:/group: 标签的反思只在对应 scope（含群聊参与人）可见，#session 后缀按用户级对齐，无实体标签的自认知全局可见——A 的反思绝不注入 B 的对话（已确认块同步收敛到此规则，原为全局注入） |
| temporal_scope 时间语义 | `store/_shared.py::temporal_is_past/temporal_weight` + `store/search.py` 评分 + `recall_format.py` 过去时尾注 | 提取侧早有 episode/state/pattern 输出但全库无消费方（写而不读）。激活：读时判定超期（state 7 天 / episode 3 天，activity_date 优先于写入时刻，pattern 永不）→ 注入行追加"过去时·可能已变"尾注（防"现在时"误述）+ 检索整体降权（memory_temporal_expired_weight 0.5，聊往事仍可低权命中，淘汰仍归遗忘曲线）；memorize 工具补 temporal_scope 参数、events 归档提炼天然带 episode 语义 |
| 用户话题指令 | `agent/memory/user_directives.py`（新）+ `Mind.accept_feel` 钩子 + context 管线 discipline 块（VOL_LOW）+ 心跳清扫 | ban-topic：纯正则抽取"别再提X"（零 LLM），挂说出来时的会话 scope（私聊管私聊、群聊管群，不跨会话扩大）；TTL 随命中线性增长（3 天×次数，上限 30 天）沉默不续期自然淡忘；注入稳定前缀区（仅指令增删变化），任务精简模式同样注入（主动消息更不能踩线） |
| 防复读 | `agent/memory/anti_repeat.py`（新）+ context 管线 freshness 块（VOL_SESSION+2）+ `execute_send_action` 闸门 | 两级防线作用面刻意不同：软提示（近期 AI 回复 df≥3 的高频 ngram top5，随最新回复缓存）引导全部回复周期换角度——提醒触发的 REPLY 必须送达只能引导；硬闸门（字符 2-gram Dice ≥0.55）仅拦 reflect: 任务上下文的主动发送，用户触发的回复不设闸（问什么答什么是用户的选择） |
| importance 校准 | `auto_capture._EXTRACT_PROMPT` 校准表 + memorize 工具描述 + `self_profile.PROFILE_MEMORY_IMPORTANCE` | 提取侧锚点表（0.9+ 身份级/明确"记住这个"，0.8 长期偏好与承诺，0.7 阶段动态，0.6 线索，0.5 弱线索不预过滤）结合实体记忆——主体是已识别的人时身份/关系级从高档；画像 ENTITY 镜像 importance 统一 0.8→0.9（身份级事实，四处写入点共用单点常量），种子阶梯 initial_reinforcement 的精度由此对齐 |

> Model Experience：① AI 无新增工具 schema（呈现/分类/降权全在管线内）；② token 影响：discipline/freshness 两块按需注入（无指令/无重复话题零字节）；③ 缓存影响：discipline 在稳定前缀区（低频变化），freshness 在尾部动态区；④ 反馈回路让"她记错了"第一次有了纠正通道——用户否认即负向证据，14 天 sub_zero 归档倒计时通电

#### GPU 模型层启停：显存释放面板（第三十八轮新增）

voicehub（:10096）与 face 服务（:10097）升级为模型层启停——进程常驻不死、模型显存可释放、下次推理自动重载（跑大模型前一键腾显存）。本轮把服务面接进 Web，跑图铁律从此有界面入口。

| 机制 | 位置 | 说明 |
|------|------|------|
| GPU 状态/释放客户端 | `entities/audiosync/client.py::gpu_status/gpu_unload` | GET /gpu/status 聚合四组件加载态（moss 带 model/device/vram_gb）；POST /gpu/unload 走 JSON body {"targets":[...]}（缺省全部 moss/asr/diarize/embedder）——**必须走 body**：query 形态传无效 targets 会被静默当作全部卸载 |
| 声音页 GPU 卡 | `web/routers/audio.py`（GET /funasr/gpu、POST /funasr/gpu/unload）+ OverviewPanel | 四 worker 行（加载徽章/model/显存/单组件释放）+ 全部释放（ConfirmDialog）；查询仅在 funasr reachable 时启用（15s 轮询），旧版服务报"未支持模型层启停" |
| face 引擎释放 | `agent/vision/face/engine.py::unload` + `EngineHealth.loaded` + POST /face/engine/unload + FacePanel | /health 的 loaded 字段解析（None=老服务未上报）；人脸页引擎卡显示模型加载态 + 释放按钮（face 独立服务，不在 voicehub 四组件内，单独释放） |
| 归因纪律 | `services/audio.py` 再导出 FunAsrError/FunAsrNotConfigured；`services/vision.py::engine_unload` 门面 | web 层错误映射一致：未配置→503、服务失败→502 |

> Model Experience：① 释放自愈——进程不死、模型懒重载，释放操作无数据风险；② AI 暂无对应工具（面板操作即可；后续若要 AI 在跑大模型前自动腾显存，可在 audio 工具组补 gpu_release）

#### 千问实时语音方言：native 三方言补齐（第三十八轮新增）

百炼 qwen-audio-3.0 系列（asr-flash / tts-plus / realtime-plus）中，asr/tts 早已在提供者链（dashscope SDK 组件，asr.py/tts.py），唯独 realtime 一体化通道无方言——native 模式只有 OpenAI/Gemini。本轮按既有方言插槽补第三方言，协议事件模型与 OpenAI Realtime 同构（DashScope `/api-ws/v1/realtime`）。

| 机制 | 位置 | 说明 |
|------|------|------|
| 千问实时方言 | `agent/realtime/native/qwen.py`（QwenRealtimeClient） | `wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=qwen-audio-3.0-realtime-plus`，Bearer 鉴权；session.update（pcm 双端：输入 16k / 输出 24k、server_vad、系统指令）；上行 input_audio_buffer.append（base64）；下行 response.audio.delta（base64 PCM 24k）/ response.audio_transcript.*（回复字幕）/ conversation.item.input_audio_transcription.*（用户转写）/ speech_started / response.done；打断 response.cancel。凭据走组件凭据中心 dashscope 条目（→ 环境变量 DASHSCOPE_API_KEY），遵守"实时语音凭据不经大模型配置"约定（与 openai/gemini 从 llm_clients 取凭据不同源——那两家只有 llm_clients 一个真源） |
| 错误分级 | `qwen._dispatch` | 协议规定 invalid_request_error 不断连：DEBUG 记录后忽略（典型场景：server_vad 已自动取消响应后引擎再发 response.cancel 的空取消）；server_error 才作为通道错误上抛引擎收线 |
| 用户转写增量 | `qwen._dispatch` | input_audio_transcription.delta 携带 text（已确定）+ stash（暂存）两段，拼接为 partial 下发展示；completed 为定稿（与流式 ASR 链的 partial/final 事件面等价） |

> Model Experience：① 启用：realtime_mode=native + realtime_native_provider=qwen（配置中心声音组自动可见，无前端改动）；② cascade 仍是默认——native 为最低延迟形态但人格/记忆不经思维链，由会话指令注入人格简述（_native_instructions 从人格档案组装）；③ 音色默认 longanqian（系统音色或复刻 voice_id），voice_presets 的 realtime 场景指派经 realtime_voice() 直通；④ 引擎零改动——方言实现 NativeRealtimeClient 协议即插即用（input_rate=16000 与麦克风采样率一致，无需重采样）

#### 声纹即 AI 的耳朵：标签化召回与精确对比（第三十七轮新增）

补齐"声音服务于 AI"的最后一公里：声纹识别结果接入消息标签体系驱动记忆召回，AI 获得说话人/音频级的精确对比工具。

| 机制 | 位置 | 说明 |
|------|------|------|
| speaker_scope 标签 | `core/tags.py` 新标签 + `engine.user_turn` 标注升级 | 通话中声纹命中且说话人绑定了实体时，标注追加机器可解析的 `[speaker_scope:user:qq:456]`（此前只有给人看的"[语音 说话人:张三]"文本——AI 认出了人，记忆系统却不知情） |
| 声纹→实体自动召回 | `recollection._speaker_scopes` + `_extract_related_scopes`/`_extract_scopes_from_anything` | 召回层解析标签并归一为权威 entity_scope 格式（`user:qq:123`→`user_qq:123`，音频库绑定格式与思维层 scope 方言的单点转换）——**私聊/通话同样生效**（原 [uid:] 解析仅群聊），听到谁说话就自动召回谁的画像/关系网络/相关记忆；agent: 自绑定跳过（自我画像恒注入） |
| 精确对比 | `matcher.compare` + AI 工具 `speaker_compare` | 说话人 vs 说话人：锚对锚（长期身份）+ 样本最佳配对（峰值证据）+ 同信道模板交叉（排除信道因素）+ 相对合并阈值的判读——合并/绑定前的终极核查，不再靠 similarity_map 全景图人肉找 |
| 音频对比 | AI 工具 `voice_compare` | 两段音频是否同一人：各自转写提声纹 → 质心相似度 + 最佳段配对，按全局阈值给判读（认亲核查/怀疑录错人时用） |
| 实体维度检索 | `store.list_segments/search_segments(entity_scope=)` + `transcript_search(entity=)` | "这个实体说过什么"直达：按声纹绑定过滤话语时间线与混合检索（子查询过滤，count/主查询同路径）；entity 参数校验 scope 前缀 |

> Model Experience：① AI 视角闭环成形：听到声音→认出说话人→（标签）自动带出其画像与记忆→需要核查时 speaker_compare/voice_compare 给数值证据→transcript_search(entity=) 查其全部话语——声音从"转写文本的来源标注"升级为"驱动记忆召回的身份通道"；② token 影响：+2 工具 schema（core 按需激活）；③ 缓存影响：工具数组变化一次重建；④ 格式纪律：音频库绑定用 `user:qq:123`（记忆图谱节点 key 同族），思维层 scope 用 `user_qq:123`，转换只发生在 `_speaker_scopes` 单点

#### 声纹引擎 v2.1：numpy 向量化 + AS-Norm 分离度门（第三十六轮新增）

对照业界生产级工具链（WeSpeaker / SpeechBrain / 3D-Speaker 的打分后端实践）补齐两块：效率（numpy 矩阵运算）与准确度（自适应打分归一化）。

| 机制 | 位置 | 说明 |
|------|------|------|
| numpy 向量代数 | `vectors.py`（cosine/cosine_many/unit_rows/pairwise_sims/blend/weighted_centroid，numpy 2.x，pyproject 显式依赖） | 全库锚扫描 = 行归一化矩阵 × 查询向量一次矩阵积（`store.speaker_anchor_matrix` 返回 [N,D] 归一矩阵）；聚类两两相似度 = M·Mᵀ。200 说话人库实测单次匹配 0.71ms（含 SQL 与短名单精评）。对外签名/返回类型不变（纯 Python 标量），np 标量在 matcher 出口统一转换（防 json 序列化炸） |
| AS-Norm 分离度门 | `matcher._cohort_separation` + 配置 `audio_match_separation`（默认 2.0，0=关闭） | 业界标准打分后端（WeSpeaker 等生产配方标配）：候选锚得分对其余说话人 top-K 冒充分布做 z 归一——"好几个人都像"的模糊查询即使过了余弦阈值也降级为临时说话人待确认（防投毒优先于防分裂），cohort <3 人时门自动不启用。相似度语义不变（用户阈值标定不受影响），separation 作为独立判据字段返回（AI/Web 可解释"为什么不敢认"） |
| 候选可解释性 | `IdentifyCandidate.separation` + api.ts + 识别面板 Badge | 候选四元组：similarity（决策分）/anchor/sample/channel（三判据）/separation（置信分离度），前端识别面板展示分离度 |

> Model Experience：① AI 视角：候选带 separation 后，"0.76 但分离度 1.8"与"0.90 且分离度 3.7"有了明确的可信度差异；② token 影响：无新增 schema；③ 缓存影响：无；④ 已评估未采纳：嵌入级均值减除/白化（WeSpeaker 配方可用但会移动余弦阈值标定，需重训标定不适用动态小库）、PLDA（需标注数据）、活体检测（本地单用户场景无需求）——最大剩余杠杆在嵌入模型本身（CAM++ → ERes2NetV2，属音源组件侧升级）
>
> 受控仿真基准（workspace/tmp/vpbench/bench.py：v1=be989b0 决策逻辑忠实重演 vs v2=当前真实代码；30 说话人、强失真、共享信道漂移模型）：已知信道识别两者持平且零错误认亲；**新信道经"碎片→合并"闭环后 v1 0% vs v2 100%**（v1 的 outlier 淘汰在合并时拒收远离心质心的新信道样本，永远无法适应；v2 收进池建模板）；单发投毒（cos≈0.78 超阈值他人声音）两者均 0 入池；**适应性迭代投毒（0.78→0.956 逐步逼近档案表示）v1 0/96 入池 vs v2 96/96 入池——但受害者识别率 100%→100% 不动摇**（锚的加权历史 + 同信道 FIFO 淘汰保护信道模板共同兜底，池污染可由 speaker_refine 重建复位）。设计权衡明示：相干门（0.45）的"宽"正是新信道适应的前提——新信道样本对锚 cos≈0.61，任何能拦 0.78 攻击样本的门都会先拦死它；适应性投毒要求攻击者持有受害者足量语音，本地单用户场景在威胁模型外，恢复通道（删样本+重建）AI/Web 双面可用

#### 声纹库 v2：一人一档案与信道感知（第三十五轮新增）

按工业界主流方案（多段注册 → 聚合鲁棒表示 → 信道补偿 → 低学习率动态更新）完全重构声纹子系统；旧声纹数据按授权清除（`PRAGMA user_version` 版本门自动重建，转写/录制登记保留、片段归属重置未知），全部一次性迁移代码（旧实体声纹库导入 / v1 段表升级 / 库文件搬迁 / ALTER 补列）删除。

| 机制 | 位置 | 说明 |
|------|------|------|
| 声纹锚 | `speakers.vector + anchor_weight` + `vectors.py::blend/weighted_centroid` | 锚 = 全部历史合格样本的**时长加权质心**（权重=时长秒截断 [0.5,10]），每次合格采样在 `add_sample` 内增量折叠——学习率 1/(n+1) 自然衰减即"采样越多越准"的低学习率动态更新；加权质心满足结合律 → 合并可精确合成（`matcher.merge` 按累计权重 blend，与重放两档案全部历史样本等价），无需重放 |
| 信道感知 | `voice_samples.channel/duration_ms` + `channels.py::normalize_channel` + `matcher.match_vector(channel=)` | 同一人经微信/电话/麦克风提取的嵌入有信道漂移：样本带信道标注（voip=通话、mic=录音同步、web=上传、enroll=注册、chat=语音消息、phone），匹配评分 = max(锚, **同信道加权质心模板**, 最佳样本)；通话识别与入库贯通 voip 信道。池满淘汰同信道最早样本（信道涌入只挤占自己，多样性自保持） |
| 防投毒门控 | `store.add_sample` 相干门 + 配置 `audio_sample_coherence_floor`（默认 0.45） | 与锚余弦低于门限的样本拒入（错认人/噪音），远低于匹配阈值不拦信道漂移；同名 enroll 累积也过门（返回 sample_rejected，防张冠李戴）。淘汰策略配置（outlier/fifo）与质心开关（audio_centroid_match）废止——单一策略，死键已从配置文件清除 |
| 匹配架构 | `matcher.match_vector`：锚全量扫描入围（阈值-0.25 或 TopN）→ 候选三判据精评 | 样本级 vec0 索引与 KNN 检索删除（锚扫描即完整候选来源，说话人量级小）——vec 索引收敛为转写文本向量专用；候选返回 anchor/sample/channel 三判据分值（可解释"为什么认成他"） |
| enroll 防分裂 | `matcher.enroll` 同名已确认档案直接累积样本 | 一人一档案：重复注册同名不再裂出新档案；consolidate 聚类从池质心改为锚（更稳定的长期表示），`refine` 语义改为**以当前样本池重建锚**（剔除坏样本后复位、锚被带偏后回收——池=全史时漂移恒 1，删样本后重建才见漂移） |
| schema 版本门 | `store._sync_schema`（`PRAGMA user_version`，当前 2） | 版本落后（含未标记 0）先 DROP 声纹表再按当前布局重建（旧列布局下建索引会失败）；声纹可再生，转写片段/录制登记/FTS 保留。**纪律：声纹表结构变更必须 bump 版本号** |
| Web/i18n | 声纹页签信道分布 chips（`list_speakers.channels`）+ 详情样本行信道/时长 + 声纹锚权重行 + channels 词典 | 说话人卡片按信道-计数展示采样分布；详情 Modal 样本行显示信道 Badge 与时长；档案区显示锚权重（等效语音秒数） |

> Model Experience：① AI 视角：识别候选带三判据分值（anchor_similarity/channel_similarity/sample_similarity），"听起来像但信道对不上"有了数据可依；speaker_refine 变为复位手段（平时不需要，锚自动进化）；② token 影响：无新增工具 schema；③ 缓存影响：无；④ 稳定性：相干门在数学上排除了"信道模板独救"的病态池（门内锥体中可被平均救回的样本其最佳样本必先过阈）——测试锁定该不变量

#### 声纹即记忆：实体关联与质心精化（第三十四轮新增，第三十五轮演进出 v2 模型）

| 机制 | 位置 | 说明 |
|------|------|------|
| 质心锚 | speakers.vector + anchor_weight（第三十五轮起随采样自动折叠，不再依赖手动精化）+ `store.get/set/list_speaker_anchors` | 声纹的长期身份记忆：锚=历史合格样本加权质心，超出样本池窗口的采样历史不丢，身份连续 |
| 声纹精化 | `matcher.refine` + AI 工具 `speaker_refine` + `POST /audio/speakers/{id}/refine` + 声纹页签按钮 | 第三十五轮起语义为**重建**：以当前样本池重立锚（剔除坏样本后复位），返回漂移；匹配判据 max(锚, 信道模板, 最佳样本)，锚兼独立检索来源（样本池整体更迭后仍能把人找回）；合并改为锚精确合成（不再触发精化） |
| 实体关联 | `speakers.entity_scope` 绑定（speaker_bind AI/Web 双面）+ `store.summary.entity_bindings` + AudioStatusProvider 注入 | 每轮注入"声纹关联实体：张三→user:webui:u1"——AI 无需调工具即知谁的声纹对应哪个实体画像，语音检索/通话标注按此归属 |
| 声纹定位 | 设计定位 | 声纹是"听过的声音"的记忆：样本池=带信道标注的经历采样、锚=加权聚合的长期身份、entity_scope=与实体记忆的关联边；AI 闭环=识别（voice_identify）→ 关联（speaker_bind）→ 重建（speaker_refine）→ 合并（speaker_merge） |

> Model Experience：① AI 视角：通话里听到熟人→标注即实体归属；样本攒多了 speaker_refine 一下声纹更准（漂移接近 1 说明已稳定）；两个人实为一人→speaker_merge（合并即精化）；② token 影响：注入多一行绑定清单（有声纹绑定时）；③ 缓存影响：无 schema 变化外的重建；④ 稳定性：既有库 ALTER 补列幂等，空池精化 422 结构化报错

#### 音色预设制：AI 与 Web 共用的音色库（第三十三轮新增）

| 机制 | 位置 | 说明 |
|------|------|------|
| 预设注册表 | `agent/tts/presets.py` + `config/voice_presets.json`（gitignored 用户状态） | 预设 = 命名的音色单元（预置音色 ID 或克隆参考对二选一 + 注释），增删改查线程锁+原子写；预设名全库查重（AI/Web 建重名预设即拒）；被场景指派的预设拒绝删除（先解除指派）；指派悬空（文件被外部编辑）按未指派处理不炸 |
| 场景指派 | 配置键 `sound_voice_default` / `sound_voice_realtime`（audio 组，值为预设 ID） | 指派与预设分离：default 全局默认（克隆对供一次性合成、voice_id 供流式管线），realtime 通话专用（空=跟随默认；克隆型预设不适用流式通话，落协议音色）。取代三十二轮的平铺键（tts_default_voice/realtime_tts_voice/tts_default_reference_* 已从配置文件直接清除并手工建档指派——数据迁移落在配置上，代码零迁移逻辑；预设名全库查重） |
| 解析链 | `agent/tts/voice.py`（default_preset/realtime_preset/default_voice/realtime_voice/resolve_voice） | 场景指派 → 预设内容 → 提供者协议音色；全部合成入口（text_to_voice/级联通话/native 会话/主动播报/内置提供者）经此单一决策链 |
| 实体桥接与频道语音 | `entities/_sdk.py::default_tts_voice / realtime_tts_voice` | 实体组件经 _sdk 取统一音色（entities 不直依赖 agent 的合法通道，与核心/AI/Web 同一预设决策链）——核心能力对所有层开放，AI 用核心功能+实体组件闭环。发往各频道的语音消息 = text_to_voice（默认预设解析）→ send_voice，与实时通话同源音色。例外：小度音箱保留设备协议音色（edge-tts 音色域与人格音色互斥，跟随默认预设会在该设备上合成失败） |
| AI 工具面 | `voice_preset`（audio 组 core：list/save/delete/apply） | AI 与 Web 同库同权：list 带指派与生效音色；save 新建/更新（校验：名称必填、二选一、克隆需参考文本）；delete 带指派保护；apply 场景指派。clone_voice/design_voice 后 save+apply 即全链路换声（guide 示例同步）；sound_config 不再收音色键（get/set 收敛为 funasr/优先级链，funasr_endpoint set 修复为写凭据中心） |
| Web 面 | `services/audio.py` → `web/routers/audio.py`（/audio/voice-presets CRUD+assign，ValueError→422）→ `pages/sound/VoicePresetPanel.tsx`（声音页「音色」页签） | 场景指派卡（默认/通话下拉）+ 预设库列表（类型/指派 Badge、行内设为默认/设为通话、编辑/删除）+ 新建/编辑 Modal；「生成」页签只留能力链（旧默认音色卡移除） |

> Model Experience：① AI 视角：音色是有名字的库——voice_preset list 看库存与指派，造新声音（clone/design）后存预设再 apply，主人 Web 改的与 AI 改的是同一个库；② token 影响：+1 工具 schema（core 按需），sound_config schema 收窄；③ 缓存影响：工具数组变化一次重建；④ 稳定性：指派保护防误删生效音色，悬空指派自动按未指派降级

#### 声音系统统一收口 + 通话实时性（第三十二轮新增）

| 机制 | 位置 | 说明 |
|------|------|------|
| 音色解析单一入口 | `agent/tts/voice.py`（default_voice / realtime_voice / resolve_voice） | 链序：调用点显式音色 → 场景覆盖 `realtime_tts_voice`（空=跟随默认）→ 全局默认 `tts_default_voice` → 提供者协议音色（代码内持有，仅全局未配置时生效）。此前"默认音色"六处平行（tts 组与 audio 组双重注册 / realtime 专用键 / dashscope_tts_voice / minimax 私有 config.json default_voice_id / 幽灵键 tts_edge_voice）各读各的；收敛后用户面只有两个键（声音页与 sound_config 工具同一入口）：tts_default_voice 单点注册（audio 组），dashscope_tts_voice 转协议音色语义，minimax 私键读取删除，幽灵键消除。全部合成入口（text_to_voice/级联通话/native 会话/主动播报/内置提供者）经同一解析链（第三十三轮演进出音色预设制，平铺键废止） |
| 声音页音色入口 | `pages/sound/GenerationPanel.tsx` + sound i18n | 生成页签补通话音色字段（留空跟随默认）——realtime_tts_voice 不再只能进配置中心或靠 AI 工具改 |
| 通话应答节奏训诫 | `agent/realtime/context.py`（RealtimeCallProvider，max_tokens 120→220） | 注入增"先应声再干活"：工具/长任务前先 send_message 应一声（出口层自动语音播出）再执行、结束后再正式回复——工具轮静默即通话卡顿；provider 层每轮 LLM 调用携带，无需引擎/工具面改动 |
| 声纹识别并行 | `engine._on_speech_end` | 声纹只读识别与 ASR 定稿并行执行（二者都只依赖本段音频快照，互不依赖）——定稿→思维启动的关键路径收敛为一段网络往返（此前串行定稿→声纹→入轮） |
| 首句快速断句 | `agent/tts/sentences.py`（首句窗口 6~36 字） | 首句未产出前在软切点提前断句——TTS 首请求不等第一个完整句，开声延迟从整句缩到首个分句；窗口内无软切点不硬切（短应答/无标点串回落常规规则，等句末或超长软切） |
| 抢占清场 | `playback.drop_pending` + `engine._on_delta` 提交回复时 | 回复抢占在播主动播报时清空其未播帧（含收束帧，不放过打断哨兵——非打断语义），回复音频紧跟当前已下发帧直落；pending_finals 计数同步收敛（speak_to_scope 的 appending 判据不受影响） |
| 召回预算与规划并行 | `memory_retriever` recall 路径 | 检索规划（轻 LLM）与多路检索**并行**：原查询 lane 先行（复用预计算 query_vec 零 embed），规划慢/超预算只损失多查询增强、不再挤占检索预算（2026-09 回归：规划串行在最前，主模型 low 档仍 9s，把检索挤到 1s 致总超时空手回退）；计划 lanes 在规划完成后增量追加（同串跳过、首条异于原查询的带实体定向）。规划段独立预算 `memory_plan_budget_seconds`（0 = 占 `memory_recall_timeout_seconds` 总超时四成份额派生，收敛至多总超时减 1s；超预算回退原查询单发），规划+提及+多路检索全程一个 wait_for——被动召回墙钟真正有界（对通话首响与文字回复同效） |

> Model Experience：① 通话首响四段提速：声纹并行（省一次网络往返）+ 首句快断（开声提前约一个分句）+ 抢占清场（回复不被主动播报残余帧拖住）+ 召回预算（规划不再无限前置）；工具轮不再长时间静默（先应声训诫 + send_message 自动播出）；② 音色一致性：声音页一处配置全链路生效；③ token 影响：无新增工具 schema，通话注入上限 120→220（仅通话中占用）；④ 缓存影响：通话注入文案变化触发一次前缀重建（此后稳定）

#### 操作实体：桌面操控 + MCP 操作关联（第三十一轮新增；实体化迁移——全部能力归纳到 `entities/operation/`，含 Web 面板与数据自持有）

| 机制 | 位置 | 说明 |
|------|------|------|
| 操作注册表 | `entities/operation/framework.py` + 实体目录 `operations.json`（gitignored 用户状态） | 操作 = 可注释/可停用的能力单元：内置桌面动作（desktop.*，九个 pyautogui 动作，不可删）+ MCP 关联操作（mcp.{server}.{工具}）；注释与启停对两类一视同仁（线程锁+原子写，provider_keys 同款纪律）。历史 `config/operations.json` 导入期一次性迁移（拷贝不删源，幂等） |
| **关联 ≠ 执行通道** | 设计定位 | MCP 关联是**语义索引**：把常用工具提升为带注释的一等操作注入操作态势，让 AI 在操作语境直接知道"有哪些语义化能力、属于哪个工具组"；**实际执行仍走 mcp:<server> 工具组**（activate_tool_group 激活）——不设第二执行通道（execute_operation 已删，冗余）。浏览器操控 = 关联 Playwright/browser-use MCP 的工具（零代码，AI 或 Web 均可） |
| 桌面执行器 | `entities/operation/desktop.py` + pyproject 声明依赖 pyautogui | 实体能力走声明依赖（onnxruntime 教训：不声明会被 uv sync 卸载）；探测保留但语义为环境异常提示（uv sync 恢复 / macOS 辅助功能授权）。全部动作 to_thread + 30s 超时；FAILSAFE 开启（鼠标猛移屏幕左上角物理中止）；type 仅 ASCII（中文提示剪贴板+hotkey 粘贴路线） |
| 看屏验证联动 | `tools.py::desktop_act(verify=auto/on/off)` → `_sdk.vision_look("screen")` 视觉桥 | 操作↔视觉联动闭环：动作成功后自动截屏，结果按多模态契约（顶层 _multimodal+images）附最新画面帧——AI 直接"看到"操作后果做下一步决策；verify 参数逐次覆盖 + operation_desktop_verify 全局开关（配置中心/Web 可控）；视觉源不可用静默回退纯文本结果（验证是增强不是依赖）。vision_look 经 `entities/_sdk.py` 桥延迟导入 agent.vision.tools（_sdk→agent 唯一豁免通道） |
| 按需态势注入 | `entities/operation/context.py`（provider：operation，priority 36=视觉之后，operation 组） | **注入不是常态**：仅操作活跃窗口内有执行才注入（operation_context_window_seconds 默认 600s，每次执行滑动续期），静默期零注入。内容三段静态前动态后：桌面纪律（看屏→操作→验证循环/高危确认/ASCII/急停）→ 关联操作（注释+参数+所属工具组 mcp:<server>；有注释在前、最近执行在前排序）→ 近期执行 ✓/✗（行动连续性）。分段字符预算 + skip-not-stop（超预算跳条不半截截断，末尾汇总省略数）。**未关联 server 的工具清单不进注入**（194 工具列表烧 token 无意义） |
| 执行与 MCP 网关 | `entities/operation/executor.py` | 统一执行入口（停用/缺运行时/网关未就绪均结构化失败）+ 环形执行历史（AI 与 Web 共用，驱动活跃窗口）；MCP 调用经 `entities.mcp.bridge.get_mcp_bridge` 现查单例（entities 内直连，桥未初始化/热拔除返回 None，无晚绑定端口） |
| AI 工具面 | `entities/operation/tools.py`（operation 组：desktop_act/list_operations/register_mcp_operation/remove/update/operation_status） | desktop_act 统一入口（九动作+verify）；register_mcp_operation 校验已连接+快照参数 schema（返回注明"执行走 mcp:<server> 工具组"）；operation_status 含桌面态/活跃窗口/工具清单/历史。@tool 即时注册（实体发现机制），配置组 `entity/operation`（键名不变） |
| Web 面 | `entities/operation/router.py`（/api/entity/operation）+ `panel.tsx` + `panels/` | 实体详情页双页签：操作目录（执行器/看屏验证/态势注入/关联数四状态卡 + 目录管理 + 操作纪律卡）+ MCP 联动（**已关联列表为主体**：注释行内编辑/Switch 启停/取消关联/行内展开测试执行 + Modal 批量添加 + server 状态卡 + 执行历史）。Web 测试执行仅限 MCP 关联（验证关联有效）；桌面动作由 AI 在对话中执行。侧边栏直达经 manifest nav（/entities/operation，group_ability） |
| 接入 | 实体目录自动发现（tools.py 标记文件），零 bootstrap 装配 | 热插拔机制纳管（目录装卸即整体增删）；服务面/路由/面板/i18n/数据全部实体自持有 |

> Model Experience：① AI 操作闭环：vision_look 看屏定位 → desktop_act 执行（默认自动回看验证，画面直接进多模态结果）→ 按验证结果决定下一步；MCP 能力经 operation_status 查清单 → register_mcp_operation 关联常用工具（语义注释）→ 操作语境自动获得态势注入 → activate_tool_group("mcp:<server>") 执行；② token 影响：+6 工具 schema（3 个 always）；态势注入仅操作活跃窗口内出现（≤800 token），静默期零成本；③ 缓存影响：provider 层 priority 36（视觉后），动态段在尾部

#### 通话会话续命：断连重挂与宽限收线（第三十轮新增）

| 机制 | 位置 | 说明 |
|------|------|------|
| 会话重挂 | `engine._reattach` + `_by_user` 身份索引（adapter:user_id → owner） | 同一用户重新 voice_start 时接续既有会话而非重建：轮次令牌、端点检测与预处理状态、播放队列（掉线期间生产的音频接续播放）、车道与挂起回复全保留（chat_id 变化时挂起回复随迁到新 scope——通话中切会话不断线）；sink 与播放写任务换到新连接；旧连接此后一切帧/voice_end 因 owner 不符 no-op（多端接管安全）。采样率变化不支持重挂（检测器/预处理链按率构建）走全新会话；重挂以下行 rt_state(resumed=true) 确认 |
| 断连宽限 | `engine.handle_disconnect`（WS 断开走此，voice_end 显式挂断仍立即收线）+ `realtime_reconnect_grace_seconds`（默认 5s，0=立即） | 断开后会话保留等重挂，超窗自动收线；宽限任务按用户身份去重，重挂即取消 |
| 客户端自动重连 | `web/frontend/src/lib/realtime-voice.ts` | 意外断连（wantActive 通话意图仍在）按 400ms×次数 退避重连三次，重挂成功状态由 rt_state 同步；穷尽才真正关断。显式挂断不重连 |

> Model Experience：① 网络抖动/页面刷新不再打断通话（"喂？还在吗"场景消失）；② 对比说明：NEKO 的 hot-swap（预热备胎客户端+原子 promote）解决的是"原生协议会话与模型绑定"问题——Anelf 级联管线的大模型/TTS/ASR 均按调用解析、切模型天然不断线，故不搬预热机制，只取"会话续命不丢状态"的价值内核；③ token 影响：无新增工具 schema；④ 缓存影响：无（连接层变更）

#### 实时响应仲裁与进程级健壮性（第二十九轮新增）

| 机制 | 位置 | 说明 |
|------|------|------|
| 播报车道 | `agent/realtime/arbiter.py`（SpeakLane/Utterance）+ `RealtimeSession.lane` | 会话内全部 TTS 播报的串行化、优先级与归因（对标实时响应仲裁的通用内核，协议特有部分不搬）：①单工车道——同一时刻至多一个"生产中"单元写播放队列，多来源（回复流/send_message 自动路由）绝不交错；②优先级抢占——回复（PRIORITY_REPLY）抢占在播主动消息（取消其生产并清空未播音频，回复即时开声；三十二轮起不再排空残余），主动消息绝不抢回复、彼此 FIFO 全播；③归因收尾——单元持单调 uid 逐块自检，被取代者不写帧/不迁状态/不重复 audio_done，收尾权独占 |
| 回复完成归因 | `reply_finalize.py`（EVENT_AFTER_REPLY 带 turn_id，与增量事件同源）+ `engine._on_after_reply/_settle_reply/_arm_settle_fallback` | 完成事件按 mind turn 归因结算：对得上立即收尾语音流；归因不上（旧轮迟到/子会话代发）宽限观察（1.5s，新增量到达即取消）后兜底结算——绝不因归因失败让回复"说不停"，也不误杀新一轮语音流；pending 不弹出，连续语音轮的增量可重开语音流 |
| 语音收束离线化 | `engine._spawn_finalize` + `session.finalize_task` | ASR 定稿/声纹识别/入轮在后台任务执行，麦克风帧流不再被定稿阻塞（收束期间到达的新语音进新一轮，不再丢帧）；先就地摘下 ASR 会话并快照缓冲再走网络调用；收束任务串行链防两段语音乱序 |
| 通话路由补全 | `engine.session_for_scope`（基座匹配，#session 后缀不阻断）+ voice_spoken 移交引擎 | 多会话通话中主动消息不再静默不播；voice_spoken 在实际播出完成时广播（取消/失败不标记），形态标记与听感对齐。轮末纯文本**不**在 deliver_text 重复路由——级联模式下回复增量已经 `_on_delta` 流入 TTS 车道，出口层再播会双重发声（语音出口唯二：增量流 / send_message 自动路由） |
| 关停总预算 | `core/lifecycle.py::shutdown_all(deadline=)` + `core/application.py`（shutdown_budget_seconds 默认 45s，system/shutdown 组） | 逆序清理按剩余预算裁剪单项上限，耗尽即跳过余下组件记名——有序关停不因个别组件卡死无限期拖长（二次信号强杀仍是最终兜底）；实例锁释放移到后置钩子（全部服务回收之后），消除"正在退出的旧实例"双实例窗口 |
| Web 绑定 fail-fast | `web/server.py::WebServerService._serve` | uvicorn 绑定失败不抛异常（serve 静默返回）→ 检测 `server.started` 请求优雅关停——半活实例（Agent 在跑而 WebUI 不可达）不再苟活 |
| 实时引擎入 Lifecycle | `bootstrap.register_channel_services` 注册 `realtime_engine`（cleanup=shutdown_all_sessions） | 进程退出不再依赖 WS 断连的隐式清理（Web 关停超时强取消路径下 finally 里的 await 不可靠）；每会话有界（5s）收尾 |
| 子进程看护 | `entities/filesystem/child_guard.py` + shell_background 登记 + `entities/filesystem/__init__.register_lifecycle` | 后台 shell（独立进程组）的 owner-death 守卫：pgid 登记 logs/shell_children.json，启动清扫上次实例孤儿（SIGTERM→5s→SIGKILL 整组），关停终止全部在册子进程；裸 create_task 收编（心跳 tick/热插拔/bilibili → async_helper.spawn 受管） |

> Model Experience：① 通话听感：回复到来即抢过话头（主动播报未播部分让位），主动消息排队不抢话、不混音，两条提醒按序全播；② token 影响：无新增工具 schema；③ 缓存影响：无（车道完全在语音链路内）；④ 稳定性：Ctrl+C 卡死有 45s 总预算兜底、端口占用快速失败由守护重拉、崩溃不留孤儿 shell

#### 语音链路核心质量（第二十四轮新增）

语义端点检测 + 输入预处理链 + 本地模型资产双通道管理（Web 与 AI 工具同一能力面）：

| 机制 | 位置 | 说明 |
|------|------|------|
| 语义端点检测 | `agent/voice/turn_detection.py::SmartTurnTurnDetector` | VAD 提议、语义裁决的融合端点：基座检测器触发 SPEECH_END 后进入候选等待，对尾部 8 秒音频本地推理"说完概率"（SmartTurn ONNX，Whisper log-mel 特征纯 numpy 提取），≥ voice_smart_turn_threshold 才收束，否则按 voice_smart_turn_eval_interval_ms 复评、voice_smart_turn_max_silence_ms 硬上限兜底（绝不挂死）；候选期用户重新开口即取消（基座重复 START 被吞掉，同一段语音不误开新轮）；模型缺失或连续 3 次推理失败熔断退化为纯 VAD。运行时进程内单例共享（会话间无状态） |
| 输入预处理链 | `agent/voice/preprocess.py` | 谱减降噪 → 自动增益 → 限幅：流式 STFT（sqrt-Hann 50% 重叠 OLA，帧长 32ms，任意采样率）纯 numpy 零依赖。降噪静音帧学稳态噪声谱（绝对电平门 + 连续帧确认防开头语音误学，建立期被响帧打断即弃半成品）、语音帧超减抑制（-12dB 单频点地板防音乐噪声）；AGC 语音帧向目标响度自适应、静音帧保持增益（不放大底噪）；限幅峰值软顶（瞬时攻击慢恢复）。voice_denoise / voice_agc 独立开关，全关字节直通零开销；flush() 收尾冲刷吐尽内部滞留样本 |
| 预处理接线 | `agent/realtime/session.py` + `agent/realtime/engine.py` + `agent/voice/session.py` | 实时会话与段会话的麦克风帧统一先过预处理链再进端点检测/ASR/缓冲；轮次收束（转写兜底与声纹识别吃到 flush 后的完整音频）与段成段（写 WAV 前）冲刷；打断 reset 同步重置链状态 |
| 本地模型资产 | `agent/model_assets.py` + `services/model_assets.py` + `web/routers/model_assets.py`（/api/local-models） | 登记制资产（id/来源/版本/SHA-256 固定，silero_vad v6.2.1 与 smart_turn v3.2-cpu）；落盘 workspace/models/（AI 工作路径内，文件工具可直接查看）；流式下载 + 哈希校验 + 原子替换 + 进度可查，同资产并发请求合并单飞，下载中拒绝删除；直连受限网络自动回退镜像源（model_asset_mirror：auto 回退 hf-mirror / off / 自定义前缀）。Web 设置页「本地模型」页签（下载/进度/删除/运行时安装）与 AI 工具 `list/download/delete_local_model`（voice 组 core 标签）同一管理面——语音端点降级时 AI 可自主补装模型恢复满配 |
| 运行时安装 | `entities/system/python_service.py::install_packages/uninstall_packages` + 工具 `install_python_packages/uninstall_python_packages`（environment 组）+ POST /api/local-models/runtime/install | uv 管理环境自动走 uv pip、其余走 pip；模型缺运行时依赖（onnxruntime）时 Web 面板一键安装、AI 工具返回值附安装提示 |

> Model Experience：① 端点检测梯队与预处理开关全部为 voice 组配置键（配置中心热调），AI 可经配置工具调节收束灵敏度与降噪开关；② token 影响：+3 个 core 标签工具 schema（按需激活，不常驻）；③ 缓存影响：无——语音链路完全在思维循环之外

#### 组件凭据中心与配置归位（第二十六轮新增）

| 机制 | 位置 | 说明 |
|------|------|------|
| 凭据中心 | `core/provider_keys.py` + `config/provider_keys.json`（gitignored，跟踪 example 模板） | 外部平台 API Key 的统一存取面：组件经 `_sdk.register_provider_key` 登记条目（name/domain/title/描述/附加字段），`get/set_provider_key` 读写，`list_provider_keys` 脱敏列举（含文件手填但未登记的条目）。三面配置等价：Web（**模型配置页「组件凭据」页签**，全域凭据一张表，卡按 `domains` 多域透显、`/api/provider-keys`）、配置文件（直接编辑 JSON）、AI（`list_provider_keys`/`set_provider_key` 工具，environment 组 core）——同一存储无第二真源；FunASR 服务地址与本地模型资产摘要并入同一外部依赖管理面 |
| 配置归位 | minimax / dashscope 组件 | 原则：大模型类生成继续走 llm_clients 模型配置；实时/特殊协议的语音等模块凭据只走凭据中心（dashscope 脱离 llm_clients 扫描，环境变量兜底），非凭据参数（模型名/音色/优先级）留各自配置组。MiniMax 凭据从实体 config.json 迁到凭据中心（参数留在 config.json）；启动迁移 `_migrate_provider_keys` 一次性归拢（minimax 实体键 + llm_clients dashscope Key + 旧 dashscope_api_key 配置键，只搬缺失项） |
| 领域分层 | 域 ↔ 组件 | 声音/视觉域只提炼能力接口（能力路由 + 提供者注册表），具体平台由组件实现并登记凭据；无组件/无凭据时链自动降级，不影响其余用户 |

> Model Experience：① AI 侧凭据自助：`list_provider_keys` 脱敏查看 → `set_provider_key` 补齐即组件转可用；② token 影响：+2 个 core 标签工具 schema（environment 组按需激活）；③ 缓存影响：无

#### 阿里百炼语音组件（第二十五轮新增）

| 机制 | 位置 | 说明 |
|------|------|------|
| 百炼语音组件 | `entities/dashscope/` | 四类组件注册进核心：流式 ASR（Recognition 流式会话，SDK 回调线程经 call_soon_threadsafe 桥入事件循环 → AsrEvent partial/final；dashscope_stream_asr_model 默认 fun-asr-realtime）→ asr_stream 链；非流式转写（Recognition.call 整段 WAV，segments 与 FunASR 客户端同构）→ asr 链；流式 TTS（SpeechSynthesizer 双向流式 PCM 直出 24k/16k）→ 核心 TTS 注册表；声音能力（一次性合成 + 音色管理：VoiceEnrollmentService 复刻/列表/删除，复刻需公网音频 URL——源 URL 直用、本地文件经 dashscope_clone_upload_url 上传端点换直链）→ 声音能力路由 |
| 密钥与依赖 | `entities/dashscope/sdk.py` | dashscope SDK 为可选依赖（未装/无 Key 组件整体不可用，链自动沿用其余提供者）；Key 解析顺序：dashscope_api_key → llm_clients 中 dashscope 兼容提供者 → DASHSCOPE_API_KEY 环境变量。配置组 entity/dashscope（模型名/音色/优先级均可调，asr/tts 默认 priority 20：本地 FunASR 10 之后、内部模型链 50 之前） |

> Model Experience：① 声音能力矩阵（sound_config capabilities）自动多出 dashscope 提供者与实时可用状态；② token 影响：无新增 schema（复用既有 voice_to_text/text_to_voice/clone_voice 工具，路由层多一个提供者）；③ 缓存影响：无

#### 音频核心层与能力页签（第二十轮新增，第二十一轮重构）

| 机制 | 位置 | 说明 |
|------|------|------|
| 音频提供者注册表 | `agent/audio/providers.py` | ASR 转写 / 声纹提取的接口抽象（Protocol + kind/priority/check_available），具体实现以组件形式注册（entities 经 `entities._sdk.register_audio_provider` 桥接）；`registry.resolve(kind)` 按优先级链取首个可用者（回退天然由链构成）。FunASR 组件在 `entities/audiosync/funasr_provider.py`（实体包导入时注册） |
| 音频核心库 | `agent/audio/store.py`（storage_volume "audio"，默认主库同族 `{stem}_audio.sqlite3`） | **音频库本体全部核心化**：audio_segments（转写/归属/未读/录制归属）+ speakers（声纹身份 + `entity_scope` 实体绑定列）+ voice_samples + recordings 四表一体；FTS5 走预分词列（transcript_tokens，与记忆库同一 tokenizer），sqlite-vec 派生索引缺失时 Python 余弦降级。旧库迁移链：v1 库文件位置搬迁（data/audio.sqlite3→同族派生）+ v1 表结构重建 + 旧实体声纹库（`{stem}_voiceprints.sqlite3`）说话人/样本/片段/录制一次性幂等迁入（`migrate_legacy`） |
| 声纹身份 ↔ 实体绑定 | `agent/audio/store.py::bind_entity / speakers_for_entity` + 工具 `speaker_bind` + 路由 `POST /api/audio/speakers/{id}/bind`、`GET /api/audio/speakers/by-entity/{scope}` | speakers.entity_scope 关联实体画像 scope（user:/group:/agent: 前缀校验），双向可查；片段查询结果 JOIN 携带 entity_scope——AI 检索话语即知"这是哪个实体说的" |
| 匹配/入库/整理/复听 | `agent/audio/matcher.py` / `ingest.py` / `consolidate.py` / `listen.py` | 全部核心化（配置键 audio_*）：阈值链（单人覆盖全局）、质心匹配、outlier 样本淘汰、噪音/短段门、未识别段挂接、质心聚类合并、源音源回听订正。回听的源文件取回走 `source_fetch.py` 取回器链（本地直读兜底；远程下载由 audiosync 实体经 `register_audio_source_fetcher` 注册） |
| 音频核心服务 | `agent/audio/service.py` | transcribe / speaker_embed 经提供者链；`ingest_payload` / `transcribe_and_store` 走核心入库管线（噪音过滤 → 声纹识别 → 落库 → 向量回填 wake）；实体经 `_sdk` 桥（audio_transcribe/audio_ingest_payload/audio_*_recording 系列）访问 |
| 音源同步实体 | `entities/audiosync/` | 只做"音源 → 核心库"搬运：目录镜像同步（**来源组件化**——`framework.AudioSyncSource` 契约 + `sources/` 扫描注册，local_dir/OpenList 两组件，priority 链选取生效源）+ 上游推送（/api/entity/audiosync/ingest，audiosync_ingest_token 自校验 fail-closed）+ FunASR 组件注册（服务地址配置归声音系统：funasr_endpoint/funasr_timeout，audio 组；可用性经真实可达性探测 + 短 TTL 缓存）+ 出站 webhook（outbound.py）；配置键 audiosync_*（entity/audiosync 组） |
| 视觉核心能力 | `agent/vision/`（第二十一轮核心化） | framework（VisualSource 基类 + 注册表，组件经 `_sdk.register_vision_source` 接入）/ buffer（分源判变）/ watcher（轮询循环）/ capture（帧契约 + 分块 dHash）/ context（变化驱动注入 + 注入轨迹）/ tools（vision_look/watch/sources，deferred 组 bootstrap 激活）；视觉源配置 vision_*（vision 组）。屏幕只是来源之一：`entities/screen` 把 mss 截屏注册为 ScreenSource（screen_monitor 配置） |
| 核心工具组激活 | `agent/runtime/bootstrap.py::register_internal_tools` | activate_group("audio")（24 工具：speaker_*/transcript_*/voice_*/audio_*）+ activate_group("vision")（3 工具）；`agent.audio.worker.register_backlog` 挂接片段向量回填（EmbeddingWorker 消化）；`init_audio` 节点注册 audio_store 生命周期（建库 + 迁移） |
| 能力页签 | `web/frontend/src/pages/Audio.tsx` / `Vision.tsx` | 音频页六 tab：总览（提供者链/库统计/注入情况/文件解析）+ 说话人（声纹身份管理/实体绑定/合并治理）+ 时间线 + 话语检索（编辑/合并/拆分/回听）+ 识别入库 + 语音会话；视觉页：源清单与监视开关 + 最新帧预览 + **注入情况**（开关/最近文本与画面注入轨迹）+ vision_* 配置。数据面 `/api/audio/*`、`/api/vision/*`（services/audio.py、services/vision.py 收口，请求模型经门面再导出守 web→services→agent 契约） |

> Model Experience：① 音频库摘要经 audio_status provider 注入（audio_context_inject 可关），视觉状态行与变化帧经 vision provider 注入（vision_context_inject 可关）——与实体时代语义一致；② token 影响：音频摘要 ≤300 token（缓存摘要零 I/O），视觉变化帧仅变化轮携带；③ 缓存影响：均在 volatile 尾部动态区，不触碰 stable/conversation 前缀

### 前端结构
### 前端结构
### 前端结构

页面采用壳组件 + 子面板目录拆分模式，通用 TabBar 切换：

```
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

components/common/TabBar.tsx  # 统一标签栏（溢出横向滚动 + 按滚动位置渲染边缘渐隐提示）
lib/types.ts / api.ts         # API 接口类型（接口集中在 types.ts，api.ts 引用；api 实例已导出供插件复用）
lib/core-routes.ts            # 核心路由注册表（App.tsx 引用；Sidebar 据此识别插件导航项）
lib/channel-plugins.ts        # 频道前端插件注册表（清单驱动频道卡片登录入口/展开面板/整页路由/列表隐藏）
lib/plugin-i18n.ts            # 插件 i18n 自注册（addResourceBundle 双语 deep 合并）
lib/utils.ts                  # cn() 类名合并工具（样式走 Tailwind 内联类，无独立 styles.ts）
i18n/locales/{zh,en}/         # 核心 namespace（zh/en key 须一一对应；插件文案不进核心 locale）
```

**新增核心页面五处同步**（缺一即"页面在但入口不可见"）：① `pages/<Page>.tsx`（App.tsx 经 import.meta.glob 按文件名懒加载）② `lib/core-routes.ts` 加路由行 ③ `components/layout/Sidebar.tsx` 的 FALLBACK_NAV 加导航项（图标须先在 ICON_MAP）④ `i18n/locales/{zh,en}/nav.json` 加标签键 + `<page>.json` 页面文案 ⑤ **运行态导航优先取 `config/webui.json` 的 navigation 覆盖表**（gitignored 用户配置，FALLBACK_NAV 仅兜底）——已有部署的机器须同步补项，否则新页面侧边栏不出现。

#### 模块前端插件体系（热插拔）

频道/实体的前端与后端收敛到同一模块目录，核心框架只做通用加载，删除模块目录即整体拔出（UI/API/文案/路由零残留）：

- **频道前端**：`channels/<id>/frontend/`（index.ts 清单 + components/ + api.ts + types.ts + locales/{zh,en}.json），经 `moduleFrontendsPlugin`（vite.config.ts）/ `web/frontend/scripts/module-links.mjs` 整目录软链到 `src/plugins/channels/<id>/`（**软链须提交 git**——CI 中 `tsc -b` 先于 vite buildStart）。index.ts 为轻量 eager 清单：`registerPluginI18n("channel-<id>", {zh, en})` 自注册文案 + 组件 loader 动态 import。清单字段：`login`（频道卡片登录入口）/ `panel`（卡片展开区自定义面板）/ `route`+`page`（整页路由，App.tsx 动态注册）/ `hiddenInChannelList`（频道列表隐藏）。频道页（AdapterCard/UnmatchedGroupCard/ChannelsPanel/ChannelTestPanel/Sidebar）全部经 `lib/channel-plugins.ts` 注册表驱动，**禁止 `key === "xxx"` 硬编码**。频道在配置中心的分组展示名也由频道自注册：`registerPluginI18n("config", {sections: {"adapter/<id>": ...}})`（deep 合并进核心 config 命名空间），核心 locale 不写具体频道文案
- **实体面板**：`entities/<name>/panel.tsx`（+ `panels/` 子目录拆分）经 `web/frontend/scripts/module-links.mjs` **代码生成**接入——扫描生成 `src/generated/entity-panels.ts`（懒加载表）与 `entity-panel-locales.ts`（locale eager 表），面板源码经 `@entities` 别名（vite alias + tsconfig paths）以真实路径被 tsc/vite/eslint 直接消费（**无软链、无提交残留**，生成文件 gitignored，prebuild/dev watcher 自动重写；`entities/node_modules` 为指向前端依赖树的自愈解析桥）；panel.tsx 内以 `./panels/...` 相对导入引用子目录；面板专属 i18n 放 `panels/locales/{zh,en}.json`，由 `lib/entity-plugin-locales.ts` 在 i18n 初始化后 **eager 注册**（面板组件懒加载，locale 静态打入主 chunk，panel.tsx 无需再自行 registerPluginI18n）；locale 文件的保留键 `_registry` 以显式映射声明全局词汇——`groups: {groupKey: 展示名}`（工具页分组名，合入 tools 命名空间）与 `configSections: {"entity/<key>": 展示名}`（配置中心分组名，合入 config 命名空间），实体目录名与分组 key 不必相同、一个实体可拥有多个分组；**实体的组名翻译一律自持于模块目录（热拔出零残留），核心 tools.json/config.json 不写实体条目**；无面板的实体也可只建 `panels/locales/` 目录（locale-only 实体同样被代码生成收录）；面板专属 API/类型放 `panels/api.ts` / `panels/types.ts`（不污染核心 lib/api.ts、lib/types）。**共享型例外**（被核心页面消费的实体功能）：实现代码一律归实体目录，核心只持路由薄壳/协议类型并经 `@entities/<name>/panels/` 别名路径引用——sticker（库管理组件群在实体 `panels/library/`，核心 `pages/Stickers.tsx` 与 Data 页为薄壳引用）、share（ShareCard 与链接管理在实体 `panels/`，核心仅留 SSE 协议类型 `lib/types/share.ts`）、devops（`panels/api.ts`+`types.ts`，核心数据库/记忆页经别名引用）；mcp / graph 为纯核心管理页（无实体面板，类型与 API 留核心）
- 插件 API 复用核心 axios 实例（`import { api, apiErrorMessage } from "@/lib/api"`），类型放插件 types.ts，不进 lib/types

### 关键文件索引

| 文件 | 职责 |
|------|------|
| `agent/mind/mind.py` | 思维核心、自主循环 |
| `agent/mind/prefrontal_cortex.py` | 工作记忆门面（组合 work_memory / tool_assembly / context_assembly 三组件） |
| `agent/mind/autonomous.py` | 决策类型、态势模型、元决策 prompt |
| `agent/mind/prompt_layers.py` | Prompt 分层缓存（stable/context/volatile + PromptCacheManager） |
| `agent/mind/guardrails.py` | 工具调用守卫（死循环检测 warn/block/halt） |
| `agent/mind/context_compressor.py` | 上下文压缩（溢出检测 + 保头保尾 + LLM 摘要） |
| `agent/mind/result_budget.py` | 工具结果预算截断（按模型窗口动态计算） |
| `agent/mind/tool_activation.py` | 工具沉睡/激活状态机（activate_tool_group） |
| `agent/mind/tools/think_loop.py` | 统一思维循环（多轮 LLM + 工具编排 + 回复入口 reply_entry/reply_loop） |
| `agent/mind/tools/reply_finalize.py` | 思维收尾块（finish_think/complete_reply/执行摘要；入口在 think_loop，单向依赖无环） |
| `agent/mind/tools/result_parse.py` | 工具结果宽松 JSON 解析 + 错误文本提取（叶子模块，think_loop/round_helpers/vision/compressor 共用） |
| `agent/mind/message_schema.py` | 内部消息契约 + 发送边界规整 + 真用户消息判定（is_genuine_user_message）+ 推理字段回传（preserve_reasoning_fields） |
| `agent/llm/resilience/classifier.py` | LLM 错误分类（驱动重试/压缩/回退策略） |
| `agent/llm/reasoning.py` | 思考等级单一权威（7 级规范词汇 + GLM/MiniMax/Kimi 专项档位表 + 下发通道分派；litellm 未收录模型的参数透传修复见运行时机制表） |
| `agent/llm/prompt_cache.py` | Anthropic 缓存断点唯一权威（线型判定 / 发送边界装饰 decorate_messages / 锚点表 / strip 副本 / TTL marker / CACHEABLE_PREFIX_LAYERS 分析口径） |
| `agent/llm/retry.py` | 自适应退避（指数 + 抖动） |
| `agent/security/session_token.py` | 一次性会话令牌（防注入伪造历史） |
| `agent/security/threat_scanner.py` | 威胁模式扫描（prompt 注入检测） |
| `core/sanitizer.py` | 敏感信息脱敏（API Key/Token/密码） |
| `core/tool_gate.py` | 工具门控（check_fn TTL 缓存 + 瞬态宽限） |
| `core/tool_errors.py` | 工具错误返回统一设施（tool_error / error_from_exception + ErrorCause 归因） |
| `agent/skills/skill_store.py` | 技能存储（workspace/skills/SKILL.md；use/match 信号分离 + merge 可逆合并） |
| `agent/skills/skill_index.py` | 技能事实索引（向量/相似度/写入诊断/库健康快照/聚类——只产事实不做策略，决策协议与评审感知的数据底座） |
| `agent/skills/skill_matcher.py` | 技能匹配（关键词 + 语义混合评分 + 近重复折叠，折叠记入合并信号） |
| `agent/skills/background_review.py` | 技能后台评审（感知完备：语义相近候选 + 库健康摘要；沉淀/合并/治理由 LLM 自主决策） |
| `agent/skills/curator.py` | 技能策展（重力：闲置降级/归档 + 试用期快筛；议程：治理事实供 AI 消费） |
| `agent/skills/sources/` | 外部技能源（可插拔：SkillSource 抽象 + 注册表热插拔；内置 SkillHub 源，删模块即卸载） |
| `agent/voice/turn_detection.py` | 端点检测协议与梯队实现（energy / silero v6 / smart_turn 语义端点融合 + auto 工厂降级） |
| `agent/voice/preprocess.py` | 麦克风输入预处理链（谱减降噪 → AGC → 限幅，流式 STFT 纯 numpy；feed/flush/reset） |
| `agent/model_assets.py` | 本地模型资产注册表与下载管理（SHA-256 校验/进度/单飞合并）+ AI 工具面 |
| `agent/delegation/profile.py` | 子代理档案 schema 单一权威（模型面 + 执行面 AgentFacets；内置档/名称校验/归一化） |
| `agent/delegation/sub_agent.py` | 子代理（leaf/orchestrator 角色 + 深度限制 + facets 消费 + schema 提取 + 续跑 base_messages） |
| `agent/delegation/delegation_manager.py` | 委托调度（并发上限/预算/聚合/后台模式/续跑/用量归集/运行日志） |
| `agent/delegation/journal.py` | 委托运行日志（进度流/transcript/崩溃 ledger/retention 清理） |
| `agent/delegation/recovery.py` | 委托崩溃恢复（账本未闭合条目 → 中断元消息注入） |
| `agent/delegation/delegate_tool.py` | delegate_task / send_to_agent(steer·after) / follow_up_agent / check·terminate_background_tasks 工具组 |
| `agent/mind/work_memory.py` | 工作记忆数据面（消息队列 / 待办持久化 / 短期记忆（溢出晋升 events 便签）/ 态势路由，PFC 组件） |
| `agent/mind/tool_assembly.py` | 工具装配（召回 / tag 激活 / schema 合并门控，PFC 组件） |
| `agent/mind/context_assembly.py` | 上下文组装（系统提示 / Prompt 分层缓存 / 执行上下文，PFC 组件） |
| `agent/mind/context_pipeline.py` | 上下文构建管线（@context_block 声明层+变动率 / 变动率排序组装 / 缓存断点注入 / legacy 布局覆盖表；新增内容块只需声明式注册） |
| `agent/mind/tools/decision_executor.py` | 决策执行分发（REPLY/REFLECT/PLAN 等） |
| `agent/mind/push.py` | 实体推送中枢 PushHub（[push:] 标签包装 + 短期记忆 + 入队唤醒 + 轮内弹窗 drain_inflight；entities 经 _sdk.push_notify 桥接） |
| `agent/mind/tools/media_pipeline.py` | 媒体标签转换 |
| `agent/capabilities.py` | 能力提供者路由框架（注册表 + 配置化优先级链 + 失败降级 + 错误归因，视觉/声音域共用） |
| `agent/retrieval/tools.py` | 检索工具面（联网检索/网页读取/仓库文档/HTTP/下载/重排序） |
| `agent/memory/memory_store.py` | 长期记忆存储（SQLite + FTS5 + Embedding；软归档遗忘 + importance 松弛回归） |
| `agent/memory/doc_extract.py` | 文档文本提取（PDF/Word/Excel/PPT/纯文本，记忆索引与 read_file 文档路由共用） |
| `agent/memory/probe.py` | 异步深探 + 召回账本（DeepProbeHub / RecallLedger：回复期间分阶段深度检索经 provider 注入持久渲染缓存，三键防重复） |
| `agent/memory/recall_format.py` | 召回行格式化权威（归属标注/时间尾注/记忆行组装，被动召回与深探共用） |
| `agent/memory/graph/store.py` | 关系图谱权威存储（graph_nodes/graph_edges；(s,p,o) 唯一 upsert + 别名归一 + 软删 + cognee 投影入队 + 访问追踪/衰减/弱边遗忘/孤立节点归档） |
| `agent/memory/graph/curation.py` | 图谱治理议程（确定性事实生产：弱边/陈旧/歧义/疑似重复/枢纽，供 AI 策展决策） |
| `agent/memory/graph/tools.py` | 关系图谱工具组（graph_add_relation / graph_query / graph_path / graph_merge_nodes 等，group=graph） |
| `agent/memory/graph/extract.py` | 心跳关系抽取（对话 → JSON 候选解析 → 落库，origin=heartbeat_extract） |
| `agent/memory/store/tag_intel.py` | 标签智能（df/共现图谱/提及词表 TTL 缓存；IDF 评分、共现与图谱邻居联想、查询提及识别的统一驱动层） |
| `agent/storage/scope_migrate.py` | scope 迁移（旧格式键回填 adapter 维度，user_version 幂等 + 自动备份） |
| `agent/memory/tools.py` | 记忆工具（memorize / recall（source 标志 + depth 浅深 + filter_tags 硬过滤）/ forget 软归档） |
| `agent/memory/notes.py` | 便签文件系统 |
| `agent/task/model.py` | 任务数据模型（TaskDefinition / TaskResult） |
| `agent/task/registry.py` | 任务注册表（config/tasks/*.json 加载/CRUD；reload 跳过 `*.handoff.json` 等运行数据文件） |
| `agent/task/executor.py` | 任务执行器（LLM 调用 + 结果存储；`task_lean_context` 精简上下文：人设+工具+永久记忆+任务指令，环境便签/召回/状态由任务按规则经工具取回——任务间共享稳定前缀、每轮 prompt 更小；`extra_note` 尾部追加动态备注，idle 反思原因注入不破前缀） |
| `agent/task/history.py` | 任务执行历史（每任务保留最近 N 条：开始时间/耗时/状态/触发来源/产出摘要；`<data_dir>/task_history.json`，executor 各终态唯一写入方，Web 任务列表 last_run + `GET /tasks/{name}/history` 与 AI list_tasks/task_history 只读消费；任务删除时清理） |
| `agent/task/tools.py` | 任务/调度自管理工具（create_task / update_task / delete_task / set_task_schedule，与 Web 管理面同路径热重载） |
| `agent/heartbeat/engine.py` | 心跳调度引擎（tick 循环 + 内置维护 + 主便签 AUTO:memory-status / AUTO:heartbeat-status 状态区块） |
| `agent/heartbeat/config.py` | 心跳配置（HeartbeatConfig + TaskSchedule） |
| `agent/heartbeat/log.py` | 心跳日志读写 |
| `agent/planning/tools.py` | 规划工具（create_goal/update_goal/delete_goal） |
| `agent/runtime/bootstrap.py` | 启动流程（初始化 → 组装 → 启动 → 健康检查） |
| `agent/runtime/state_restore.py` | 启动状态恢复（工具覆盖/实体启停/自定义标签回放，纯 core 操作；services 同名方法委托于此） |
| `agent/runtime/singleton.py` | AgentRuntime 全局单例（get_runtime Optional 读 / require_runtime 未就绪抛错；services._runtime 为其 web 侧门面） |
| `entities/_sdk.py` | 工具注册 + LLM 桥接（操作回报框架独立为 `entities/_ops.py`，实体态势注入的数据源） |
| `entities/filesystem/ops_context.py` | 文件操作态势（按会话追踪当前目录/活跃目录/最近操作 + 目录说明文档注入 + provider fs_ops） |
| `entities/ssh/ops_state.py` | SSH 操作态势（按 会话×连接 追踪 + 远程说明文档后台抓取 + 渲染，provider 在 context.py） |
| `agent/channel/manager.py` | 频道管理（register / route / activate_channel 动态加载未注册频道 / set_channel_enabled 启停意图落盘统一配置 / list_configured_channels 目录扫描） |
| `agent/channel/config.py` | 频道配置统一接入（CONFIG_MODEL 扫描注册 adapter/<id> 组 / ChannelConfigStore 频道目录文件存储后端 / set_channel_config 频道内部写入口 / config_key 键前缀约定） |
| `agent/channel/tool_bridge.py` | 频道工具桥接（@channel_tool 扫描注册 / 通用能力路由 / 敏感门控 / 按频道接口开关 channel_tool_states） |
| `agent/channel/context.py` | 当前会话频道 ContextVar（通用工具默认路由目标） |
| `web/routers/config.py` | 心跳/任务 API + Mind 配置 API |
| `web/routers/config_meta.py` | 统一配置元数据 API（ConfigRegistry 驱动，数据驱动配置中心） |
| `web/routers/workspace.py` | 工作区文件 API（目录树 / 读写 / 搜索，沙箱复用 entities.filesystem） |
| `web/routers/database.py` | 数据管理 API（SQLite 浏览/维护/备份 + 外部连接 CRUD + 数据目录迁移） |
| `web/routers/search.py` | 全局搜索聚合 API（记忆 / 日志 / 文件 / 会话） |
| `services/db_connections.py` | 外部 SQL 连接（注册表 + PG/MySQL 只读适配器 + SqlTransferClient 导出导入写通道，config/db_connections.json） |
| `services/data_migration.py` | 数据目录迁移（在线热备份拷贝 + 校验 + data_root 切换；`validate_target_dir` 为目录迁移类目标校验的共用实现） |
| `core/storage_volume.py` | 存储卷注册表（VolumeDescriptor 自注册 / 位置指派解析 / needs_restart 观测） |
| `agent/storage/volume_restore.py` | 卷恢复重启落盘（pending 标记 + bootstrap 启动交换 + pre-restore 安全副本） |
| `services/volume_ops.py` | 卷管理操作（备份/恢复/迁移/外部 SQL 导出导入 + 每卷单飞状态机） |
| `entities/ui/tools.py` | 界面交互工具组（ui_notify / ui_ask / ui_open_panel / ui_compose / ui_get_state） |
| `web/frontend/src/pages/chat/` | 对话工作凳子面板（Dock / StatusBar / FileEditor / UiCommandHost / render） |
| `web/frontend/src/stores/chat-store.ts` | 对话状态 + 聊天 SSE（含 ui_command 分发） |
| `web/frontend/src/stores/workbench-store.ts` | 工作台状态（Dock / 编辑器 / UI 命令收件箱 / 状态上报） |
| `core/path.py` | PathManager + ConfigPaths 动态路径（config_dir/data_dir 可搬迁） |
| `core/lifecycle.py` | 长驻服务与单例的统一宿主（register(on_start/cleanup/on_tick) / start_all / shutdown_all(per_timeout) / snapshot；注册顺序=启动顺序，逆序=关停顺序） |
| `core/application.py` | 进程宿主 Application（三段式 run：启动 FlowMachine → start_all → 等信号 → 前置钩子+逆序关停；信号布防 / 启动时间线） |
| `core/flow.py` | 异步流程状态机 FlowMachine（depends_on 拓扑分层 + 同层并发 / retries·retry_delay·timeout 声明式 / NodeState 状态机（FAILED·SKIPPED·UPSTREAM_FAILED·CRASHED）/ FlowCycleError 静态校验） |
| `core/latebind.py` | 晚绑定端口原语（LateBinding / WireError / assert_wired / reset_all） |
| `agent/runtime/wiring.py` | 运行时统一施绑点（wire_runtime：bootstrap 组装尾部唯一接线入口） |
| `core/crash_report.py` | 崩溃状态设施（守护脚本崩溃状态 logs/crash_state.json 读写 + macOS .ips 崩溃报告解析关联 + AI 可注入摘要渲染） |
| `agent/mind/crash_recovery.py` | 崩溃尾部修复（回复检查点残留注入中断元消息 + 崩溃上下文收集消费） |
| `core/context_provider.py` | 上下文提供者注册表（实体实时快照注入，think_loop 每轮发送组装时经 collect() 取当前最新快照——缓存超 2s 新鲜度阈值即内联并发重收（provider 契约零 I/O + 各 1s 硬超时），注入位置在工具链之后、exec_context 之前；**priority 语义为变动率排序**（越小越靠前，与管线 volatility 教义同构）：10-19 状态级（连接/解锁/在线清单）/ 20-29 摘要级 / 30-39 会话操作态势 / 40+ 实时快照（含时间/秒计数），预算超限时大值先截断；两道门控均热读取：①inject_key 注入开关——会产出注入内容的 provider 必须声明，约定 `<组名>_context_inject`，_sdk 装饰器兜底注册进 `entity/<group>` 组（实体自行声明的定义优先），频道 provider 走 CONFIG_MODEL 字段；②group 实体启停联动——分组工具全禁用时停止采集与注入，与实体目录可见性同口径） |

### 工具分组体系

#### group key 规范

工具分组 key 是全局标识符，**必须使用英文**，前端通过 i18n 翻译展示中文/英文名称。

- 后端注册：`group="thinking"` / `entity("web", "...")`
- 前端翻译：`i18n/locales/{zh,en}/tools.json` → `groups.thinking` → "思维工具"
- 前端使用：`t(`groups.${g.group}`, { defaultValue: g.group })`

修改分组名时必须同步更新：
1. 后端 `@tool(group=...)` / `@deferred_tool(group=...)` / `entity(group, ...)` / `activate_group(group, ...)`
2. 前端翻译：核心分组（agent 层）改 `i18n/locales/zh/tools.json` 和 `en/tools.json` 的 `groups` 对象；**实体分组改其实体 `panels/locales/{zh,en}.json` 的 `_registry.groups` / `_registry.configSections`**（启动时 eager 自注册，核心 locale 不写实体条目）
3. 组归属模块中的 `EntityRegistry.register_group_order(group, 权重)` 自声明（实体在其 `entity_manifest(order=)`，agent 侧在其工具模块顶层调用处）

#### 当前分组索引

| group key | 中文名 | 注册文件 | tags |
|---|---|---|---|
| `output` | 消息输出 | `channel/output_tools.py` | always |
| `voice` | 语音会话与本地模型 | `agent/realtime/tools.py` + `agent/model_assets.py` | always/core |
| `memory` | 记忆管理 | `agent/memory/tools.py` | always/core/heartbeat |
| `graph` | 关系图谱 | `agent/memory/graph/tools.py`（含 graph_curation_agenda 治理议程） | always/core/heartbeat |
| `notes` | 便签记忆 | `agent/memory/notes.py` | core/heartbeat |
| `thinking` | 思维工具 | `agent/mind/mind.py` + `agent/mind/tool_activation.py` + `agent/mind/context_compressor.py` + `agent/mind/tools/short_term_tools.py`（短期记忆自管理） | always |
| `planning` | 目标规划 | `agent/planning/tools.py` + `agent/task/tools.py`（任务/调度自管理） | planning/goal/heartbeat |
| `skills` | 技能 | `agent/skills/tools.py` | always |
| `delegation` | 子代理 | `agent/delegation/delegate_tool.py` | always |
| `ui` | 界面交互 | `entities/ui/tools.py`（经 event_bus `EVENT_UI_COMMAND` → 聊天 SSE 桥接） | always |
| `retrieval` | 检索 | `agent/retrieval/tools.py`（接口层）+ `providers/`（能力×提供者矩阵：检索/网页读取/仓库文档为统一 Protocol，提供者实现子集、可启停；builtin 本地直连/bigmodel 内置，第三方组件经 `_sdk.register_retrieval_provider` 接入）+ `fetcher.py`（直连抓取设施）+ `rerank.py`（rerank 模型链） | always/core/web |
| `minimax` | MiniMax | `entities/minimax/`（组件包：视觉理解/图像生成、语音合成/音色管理、联网检索、流式 TTS 四类组件注册进核心路由；无 AI 工具） | — |
| `os` | 操作系统 | `entities/filesystem/tools.py`（read_file 对 .pdf/.docx/.xlsx/.pptx 提取纯文本经 _sdk 文档桥，.doc/.xls/.ppt 旧版格式给转换指引） | media:file |
| `ssh` | SSH 远程管理 | `entities/ssh/tools.py` | —（整组 allow_sleep 沉睡，`activate_tool_group` 唤醒） |
| `audio` | 声音（声纹身份/转写检索/语音合成/音色管理/音乐生成） | `agent/audio/tools.py` + `agent/audio/gen_tools.py`（核心组，bootstrap 激活） | always/core/media:voice/media:audio |
| `audiosync` | 音源同步 | `entities/audiosync/tools.py` | always/core |
| `vault` | 密码本 | `entities/vault/tools.py` | —（整组 allow_sleep 沉睡；reveal/totp/delete 标 risk=CRITICAL） |
| `sticker` | 表情包 | `entities/sticker/tools.py` | always/media:image（部分工具 allow_sleep） |
| `dashscope` | 阿里百炼语音 | `entities/dashscope/`（流式/非流式识别、CosyVoice/Qwen-TTS 合成、音色复刻） | — |
| `environment` | 环境信息 | `entities/system/tools.py`（含 install/uninstall_python_packages 包安装） | — |
| `model_control` | 模型控制 | `entities/model_control/tools.py` | core |
| `ollama` | Ollama | `entities/model_control/tools.py` | — |
| `channel_ops` | 频道操作 | `agent/channel/tool_bridge.py`（@channel_tool 动态）+ `agent/channel/manage_tools.py`（频道启停 start_channel/stop_channel，敏感门控 + risk=CRITICAL，启停意图落盘统一配置 `<id>_enabled` 键） | capability/channel_id/core |
| `entity` | 实体管理 | `entities/entity_query/tools.py` | always/core |
| `mcp_manage` | MCP 管理 | `entities/mcp/bridge.py`（动态） | — |
| `mcp:*` | MCP 服务 | 动态注册 | — |
| `plugins` | 插件管理 | `entities/plugins/tools.py`（AI 管理面）+ `activation.py`（激活编排：技能入库 workspace/skills + MCP 合并带 plugin 来源标记 + tools.py 差集注册）+ `router.py`（/api/entity/plugins）+ 核心引擎 `core/plugins/`（清单多点发现解析、plugins.json 注册表、git/local 负载获取与原子替换、PluginManager 编排，激活经钩子外置） | — |
| `ai_desktop` | AI 桌面 | `entities/ai_desktop/tools.py`（组件管理 + 天气预报查询）+ `modules/calendar/tools.py`（日程增删/标注/ICS 订阅，提醒经 _sdk 桥接 mind 持久化提醒） | — |
| `vision` | 视觉感知与生成 | `agent/vision/`（源框架 buffer/watcher/context + tools vision_look/watch/sources + gen_tools recognize/generate/edit/video 系列 + capabilities 能力路由，deferred 组 bootstrap 激活）；来源组件化——`entities/screen`（mss 截屏）等经 `_sdk.register_vision_source` 接入，外部帧经 POST /api/vision/push 汇入统一缓冲；生成能力组件经 `_sdk.register_visual_provider` 接入 | always/core/media:image/media:video |
| `devops` | 运维管理 | `entities/devops/tools.py`（重启/构建/git 更新/崩溃信息查询 get_crash_report，核心逻辑在 `service.py`，Web 面板经 `router.py` + `panel.tsx` 复用同一实现） | — |

### 缓存命中率排查手册（ZCode 排障）

LLM 前缀缓存命中率是本项目的核心成本/性能指标。缓存工程分三层责任，排查时**先定位层再下结论**，不要默认"缓存崩了"：

1. **客户端字节稳定性**（完全可控）：变动率排序组装 + tools 冻结 + 摘要窗口 + 单一装饰点。验证 = 快照 section 哈希 diff + **PrefixGuard 运行时哈希链**（records.jsonl 的 `prefix_drift` 字段定位首个断裂消息）。
2. **供应商缓存行为**（不可控）：磁盘缓存传播延迟/驱逐/节点亲和。判读特征 = prefix_stable=True 而 read 浮动、1~2 轮自愈，列表以「平台波动」徽标标识。**合法断裂单独标识**：折叠/压缩是已知的前缀整体重写，完成点经 `prefix_guard.note_legal_break(scope, reason)` 登记（conversation_fold 成功路径 reason=fold、`_compress_context` 成功路径 reason=compress）——清空该 scope 全部基线（折后首轮校验不误报漂移），并在 120s 窗口内（覆盖首轮失败的重试链）让快照记录携带 `legal_break`，列表以「折叠/压缩」徽标与「平台波动」区分。
3. **统计与展示口径**：kind 分桶 / age_sec 回声 / unobservable / 单次钳制率平均。

**litellm 升级同步规范（机械门禁）**：litellm 的流式 usage 处理是脆弱契约（1.100 曾对未收录模型用 tiktoken 估算伪造 usage：prompt 虚高 ~1.8 倍、completion 清零、details 丢弃），因此 ① pyproject 精确锁定版本（`litellm==x.y.z`，禁止范围符）；② 版本变动必须运行全模型缓存与用量健康门并全绿后才放行：`LLM_CACHE_E2E=1 uv run pytest tests/integration/test_llm_cache_hit_e2e.py`（`LLM_CACHE_E2E_MODELS=a,b` 可子集）——对配置内全部启用 chat 模型经真实 LLMClient 管线发两次同前缀流式调用，断言：两次 usage 均回报 / completion>0（伪造指纹）/ 含缓存口径 read+creation ≤ prompt（尺度混血指纹）/ 可观测时第二次 cached>0 且命中 ≥0.7（判别区间：伪造尺度混血 ≈0.55、平台粒度损失实测下限 ≈0.78；暖调用按 2/4/8s 递增窗口吸收供应商写读传播滞后）。

**记忆系统红线清单**（改动记忆/召回/画像注入时逐条自查；前四条有不变量测试锁定，见 `tests/unit/agent/mind/test_cache_layer_invariants.py`）：
1. **vol ≤ 30 禁入**：记忆召回/画像/关系/技能/状态/短期记忆内容块的 volatility 必须 > VOL_HISTORY(30)（stable/summary/conversation 是缓存前缀，一个字节变化即断裂）。新增 `@context_block` 时先想"这块多久变一次"。
2. **pin 块独立成消息**：永久记忆块与召回/检索块必须分消息返回（`_format_unified_results`），recollection 的 startswith 提升只捕获纯永久块；合并成一条会把每轮变化的召回内容带进 context 层。
3. **时间戳只准日期粒度**：召回渲染用 `%m-%d`/`%Y-%m-%d`（`_format_memory_time`）；秒级/计数器类易变字段不得进入任何注入块（状态计数器隔离在 status 层是刻意设计）。
4. **fail-open 不注入错误文案**：召回/画像/关系/技能匹配任一异常 → 该块为空（管线跳过空内容），禁止把异常文本写进上下文——错误文案每轮不同，等效于注入易变内容。
5. **session_token 暗坑**：`security_session_token_enabled` 开启后历史消息逐条包裹每轮随机的令牌，conversation 层字节全变、历史锚点恒失效——排查命中率时先确认该开关状态。
6. **legacy 布局暗坑**：`context_tail_injection_enabled=false` 时动态块移到历史之前，召回结果直接击穿历史前缀——缓存友好布局依赖 tail injection 保持开启。
7. **PreCompact flush 只写 DB**：压缩前的记忆抢跑提取（`_precompact_flush`）只写记忆库，不触碰任何 prompt 分层内容；压缩本身的 invalidate+prewarm 走既有机制，任何记忆侧改动不得在这条路径上新增 prompt 层写入。


### 开发约定

**import**：`from core.log import log` / `from core.entity import EntityRegistry` / `from core.path import ConfigPaths` / `from agent.memory.memory_store import MemoryStore` / `from agent.heartbeat.engine import HeartbeatEngine` / `from agent.task.registry import TaskRegistry`

**日志**：`log("内容")` / `log("调试", "DEBUG")` / `log(f"错误: {exc}", "ERROR")`

**异常处理**：关键路径（数据库连接、关闭）保持 `except Exception`；工具函数返回 JSON error；其他 `pass` 场景补充 DEBUG 日志

**工具开发**：返回 `str`（JSON）、完整类型注解、Google docstring、内部捕获异常；错误返回统一用 `core.tool_errors`（entities 经 `_sdk` 导入 `tool_error` / `error_from_exception` / `ErrorCause`），禁止裸 `{"error": str(e)}`

**晚绑定准入**：模块级运行时引用一律用 `core.latebind.LateBinding` 声明端口（消费方所在层声明、`agent/runtime/wiring.py` 统一施绑、check_health 经 `assert_wired()` 校验），禁止新增 `set_xxx` / `_xxx_ref` 式模块全局；仅限三种成因（import 时装饰器注册的工具拿不到构造参数 / 循环初始化 / 跨层桥），其余一律构造注入

**系统注入消息必须带 `_source` 来源标记**：think_loop / round_helpers / context_compressor 向消息链注入的 system 元消息（压缩反馈、rehydration、超时恢复、长度恢复、后台任务、实体推送等）须附 `"_source": {"origin": "<词汇>"}`，发送前由 `normalize_for_send` 与 `_layer` 一并剥离（LLM 不可见，供快照归因/审计）。已用词汇：`compression` / `rehydration` / `timeout_recovery` / `length_recovery` / `background_task` / `push` / `context_provider`（含异步深探增量）；新增注入点复用或扩充词汇表，勿省略标记。注意 `_source` 不进 DB（对话历史只存 role/content），仅作用于内存消息链。

**Model Experience 三行声明（新功能必答）**：任何影响模型输入/输出的新功能，须在其模块 docstring 或本表登记三件事——① 模型看到什么（注入了什么内容、走哪个通道）② token 影响（增量还是节省、量级）③ 缓存影响（是否触碰前缀层；volatile/tool_chain 尾部动态区则注明不破前缀）。对齐 dsh 每 README 必答 "Model Experience / Token effect / KV Cache effect" 的纪律——缓存是本项目一等指标，新功能不声明即视为未评估。

**频道开发**：继承 BaseChannel、6 个必需接口（channel_id / display_name / capabilities / start / stop / send_text）

**前端**：页面超过 300 行拆为子面板目录、统一用 TabBar、i18n 覆盖所有文本、`Record<string, unknown>` 替换为 `lib/types.ts` 接口

**生命周期**：有状态单例与长驻服务一律 `Lifecycle.register(name, instance, on_start=start_fn, cleanup=close_fn)` 注册（注册顺序 = 启动顺序，逆序 = 关停顺序）；关闭时由 Application 宿主统一 `Lifecycle.shutdown_all()`，禁止在入口脚本手工编排服务清理

**包管理**：项目依赖由 uv 管理（`pyproject.toml` + `uv.lock`），安装依赖用 `uv add`，临时操作用 `uv pip install`；禁止对 `.venv` 使用 `pip install` / `ensurepip`（uv 创建的 venv 默认不含 pip，属正常状态而非故障，不要"修复"它）

**litellm 版本**：当前 1.100.1（Python 3.12，`requires-python >=3.11,<3.13`；litellm 1.98 起官方要求 py3.11+，关键原生依赖 ladybug/cognee/tiktoken/tokenizers 均已验证 cp312 wheel 可用）。历史教训：1.96/1.97 曾在 py3.10 下 `ModelResponse()` 崩溃（新版引用未 import 符号，py3.10 注解求值下暴露）——**litellm 升级前必须验证 `litellm.ModelResponse()` 可实例化**，且 py3.10 已不可回退。另注意 1.96 起 `AsyncHTTPHandler.client` 变为已关闭即惰性重建的属性（取引用再判 `is_closed`，重复访问会拿到新池）。我们用的官方机制（`extra_body` 透传 / `allowed_openai_params` 白名单 / `register_model` 能力声明 / `drop_params`）在 1.100 仍在维护。

**测试体系**：测试面分两类——**分层套件** `tests/`（`tests/unit/` 纯 mock/纯函数/tmp_path 快速单测，按被测层分目录 core/agent/services/web；`tests/integration/` 真实应用组装或需外部凭证，需凭证的用例 env-gated 自动 skip）与**模块内套件** `<模块>/tests/`（`entities/<name>/tests`、`channels/<id>/tests`，测试随模块目录走，删除模块即整体拔出零残留；跨模块共享件测试留在 `tests/unit/entities/`）。目录归属由仓库根 `conftest.py` 自动打 `unit`/`integration` marker（模块内 tests/ 自动 unit），无需手写。根 conftest 全局隔离 ConfigManager（指向 tmp_path），新测试不得读写真实 `config/`。运行：`uv run pytest`（全量）/ `uv run pytest tests/unit`（分层单测）/ `uv run pytest entities/minimax/tests`（单模块）/ `uv run pytest -m integration`；加 `-n auto` 并行（已装 pytest-xdist，全量约 64s→26s，CI 已启用；单测调试/`--pdb` 时去掉）。CI（`.github/workflows/ci.yml`）模块化三 job：`changes`（原生 shell 归类：push 对 HEAD^、PR 对目标分支基点对比）→ `lint`（ruff + import-linter + mypy core 三平台必过/全量观察，静态门禁与改动面无关始终全仓）+ `tests`（**模块动态矩阵**：实体/频道改动只跑对应 `模块/tests` 腿并附带跨实体共享套件，`core/`、`agent/`、`tests/`、根 `conftest.py`、依赖锁定等横切改动触发 `all` 全量腿；services/web 后端各有专属腿；`fail-fast: false` 保留完整失败面，GitHub UI 按模块独立呈现红绿）+ `frontend`（lint/build；模块前端 `channels/*/frontend/`、实体 `*.tsx` 面板同属前端改动面）；文档类提交全跳过，`workflow_dispatch` 手动触发全量；各 job 均有 timeout-minutes 挂起护栏，覆盖率产物按腿上传（保留 7 天）。

**测试防膨胀规约**（写新测试前逐条自查）：
1. **先查共享层再动手**：think_loop 替身用 `tests/helpers/think_loop_fakes.py`（FakeMind/FakePfc/text_result/tool_result/run_think_loop），禁止在新文件复制这些样板；Mind 替身的特化行为以子类扩展实现
2. **fixture 分层复用**：`tests/unit/conftest.py` 提供 `store`（MemoryStore），`tests/unit/agent/conftest.py` 提供 `sqlite` 基座，`tests/unit/agent/mind/conftest.py` 提供 `anything`/`deliver_mock`——同名需求直接注入，禁止本地重建同构 fixture
3. **同主题微测试并入既有文件**，不新建文件；实体/频道的测试放模块内 `<模块>/tests/`（随模块整体拔出），分层套件内跨目录测别的模块的测试放在被测模块目录下（如 think_loop 集成测试归 mind/，不进 llm/）
4. **被新用例取代的旧用例必须删除**；死代码（生产零调用的函数）不保留测试覆盖
5. 合并 = 移动 + 去样板，断言语义不缩水；语义各异的替身/工厂不强行合并（合并产物比各自更复杂时不合并）

**禁止**：直接 import openai/anthropic SDK（用 litellm）/ entities 直接 import agent（用 _sdk 桥接）

---

## 已否决的设计替代方案（防止重新发明轮子）

记录对比 deepseek-harness 等成熟项目时**评估过但否决**的方案，以及重审条件。决策依据详见 git 历史与 `docs/`。

1. **事件溯源架构（Model-visible ⟺ Logged，append-only 事件日志 + 纯函数投影）**：dsh 用它让"前缀缓存稳定"成为涌现性质、resume/fork/replay 免费。否决理由：AnelfAgent 的心跳/便签/多通道/记忆召回带来高动态性，全量事件溯源成本远超收益。已吸收其结论（崩溃尾部修复 `crash_recovery.py`、PrefixGuard 观测），不搬实现。**重审条件**：若未来收敛为单会话低动态模型。
2. **exec_context 跨轮去重**：dsh runtime-context"值不变不写入"。否决理由：AnelfAgent 的 exec_context 含 `elapsed:.2f` 时间戳与轮次号，每轮字节必变，去重无命中空间。
3. **Code Mode（run_code 折叠工具目录为生成 SDK）**：dsh 用它压缩模型侧 tool-catalog 体积。否决理由：IM 场景工具调用短平快，引入新执行面（代码生成 + 子 dispatch）复杂度不划算。
4. **os 级沙箱（sandbox-exec/bwrap/landlock/Windows ACL）**：否决理由：个人助理跑在自有机器、为单一用户服务，`shell_guard` 应用层预检 + 统一审批规则引擎（allow/ask/deny + 参数 glob）是更匹配的信任模型。
5. **数字错误 code 体系**：dsh `HarnessError.code` 数字契约。否决理由：`ErrorCause` 字符串枚举（`core/tool_errors.py`）+ edit_file 的 context 数字 code 已够用，双体系并行是负担；已对齐其"按 cause 路由、不解析 message"的核心纪律（工具超时/异常统一走 `tool_error`/`error_from_exception`）。
6. **write_file/edit_file 自动建基线（refresh 放宽 read-before-write）**：曾拟在缓存缺失时自动读入文件解除"尚未读取"拒绝。否决理由：会架空 read-before-write 防盲写语义（已有测试 `test_existing_file_requires_read` 锁定严格门）；最终仅 append_file 与 write_file 保持严格门，`file_state` 不提供自动放宽。

---

## 三、项目级 skill 扩展点

未来如需新增项目级 skill，放置位置（按优先级从高到低）：

1. `<repo>/.zcode/skills/<name>/SKILL.md`
2. `<repo>/.agents/skills/<name>/SKILL.md`

仅放置规则「按文件路径条件触发」无法实现——ZCode 没有该机制，需要走 AGENTS.md（全量注入）或 SKILL.md（按 description 关键词触发）两条路径。