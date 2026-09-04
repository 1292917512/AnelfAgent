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
| `agent/delegation/` | 子代理调度（delegate_task / 并行 fan-out / 深度限制） | 经 `mind.reflect()` 隔离执行 |
| `agent/security/` | 安全防护（会话令牌 / 威胁扫描） | 脱敏核心在 `core/sanitizer.py` |
| `agent/task/` | 独立任务系统（定义 / 注册表 / 执行器） | 纯内容定义，不含调度逻辑 |
| `agent/heartbeat/` | 心跳调度（引擎 / 配置 / 日志 / 内置维护） | 管理何时执行任务，持久化计数器 |
| `agent/planning/` | 自主规划（目标 CRUD / 执行追踪） | 依赖 memory |
| `channels/` | 频道适配器（目录自动发现） | 继承 BaseChannel |
| `entities/` | 工具实体（目录自动发现） | 通过 `@tool`/`entity()` 注册，通过 `_sdk.py` 桥接 LLM |
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
  频道配置独立走 `adapter/<id>` 组（不进 ConfigRegistry，经 `/adapters/configs` 读写）
- **ConfigItem 展示元数据**：`advanced`（高级项，UI 折叠；`*_enabled` 主开关等保持基础项）、
  `value_type: "range"` + `min`/`max`/`step`（滑条+数字复合控件）、`unit`（单位展示）
- 保存时按类型强转 + `clamp` 边界收敛；MindConfig 字段自动路由 `save_mind_config` 双轨同步
- 新增配置项只需在所属模块注册（含 description/advanced/unit），配置中心自动出现，
  不要在前端硬编码字段

#### 标签系统（core/tags.py）

`[key:value]` 统一数据编码。函数：`tag_label` / `etag` / `etag_all` / `batch_remove_tags`。
内置标签：time / uid / group_id / name / channel / platform / media_file / reply_to / to_me / push 等
（to_me 标识"群消息 @ 了机器人"，仅 @ 时渲染，无此标签的群消息 = 群员间对话而非对她的请求；
push 为实体推送通知标签，标识"非用户消息"；两者出站时随元数据标签一并剥离）。

#### 会话 scope 格式（agent/messages/everything.py）

entity_scope 含频道 adapter 维度，跨频道同号实体（如 QQ uid 与 WebUI uid）天然隔离：
`user_{adapter}:{uid}` / `group_{adapter}:{gid}` / `user_{adapter}:{uid}#{chat_id}`
（如 `user_qq:123`、`user_webui:web_user#chat_1`）。构造一律用 `build_entity_scope()`，
解析一律用 `parse_entity_scope()`（返回 scope_type/adapter/base_id/session_id，兼容无 adapter 旧格式），
禁止手工 f-string 拼接。记忆标签同构：`user:{adapter}:{uid}`。存量数据由
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
  2. 遍历 task_schedules，递增 beat_count
  3. 选取一个到期任务 → TaskExecutor.run() → 结果记入心跳日志
  4. 持久化计数器到 config/heartbeat.json
```

四种触发模式：heartbeat（每 N 次心跳）/ scheduled（每天指定时间）/
idle（连续 N 次心跳无思考活动后，全局仅一条）/ manual（仅手动）。

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
| 用户 hook 事件面 | `agent/hooks/` | `config/hooks.json` 声明 tool_pre/tool_post/reply_end 脚本；exit 2 阻塞（stderr 为理由）、串行、deny 胜过一切；空配置零开销 |
| 长任务交接 | `agent/task/handoff.py` | 任务定义 `handoff: true` 时：输出末尾 `# HANDOFF` 块持久化，下次运行注入（确定性接力） |
| 消息来源打标 | `_source` 键 | 系统注入消息带 `{"origin": ...}`，`normalize_for_send` 与 `_layer` 一并剥离（LLM 不可见，供归因） |
| 子代理统一注册表 | `LLMManager._sub_agents` + `delegate_task(agent_name=...)` | 一套档案体系（llm_clients.json 顶层 `sub_agents` 键）：名称 → 有序模型候选池（前者不可用依次回退）。**内置难度档 easy/medium/hard（tier 1-3，受保护）就是 difficulty 1/2/3 的语法糖**，与自定义档案（tier 0）同构存储、同套 CRUD；解析优先级 agent_name > difficulty > 默认，本档全不可用降挡。AI 经 model_control 组 4 个工具增删改查（list/create/update/delete_sub_agents，update 的 models 参数整池替换），Web 经 `/models/sub-agents`，双路径同 LLMManager 内存态 + 原子落盘即热生效；legacy `delegation_tiers` 键加载时自动迁移；档案内容不注入 prompt 层（AI 按需 list 查询，零缓存影响） |
| idle 空闲调度 | `agent/heartbeat/` IDLE 模式 | 连续 N 拍无思考活动（`mind.last_activity_ts` 锚点，任务自身执行也刷新）触发唯一空闲任务（反思+自由活动，如 self_reflection）；确定性调度优先，REFLECT 元决策延迟登记由其消费；`validate_schedules` 强校验全局仅一条 |
| 心跳忙碌延后 | `assistant._heartbeat_loop` | 回复/反思/上轮 tick 未收尾时不跳过整轮，按 `heartbeat_busy_defer_seconds`（默认 60s）短间隔轮询、空闲即补跑；延后期间不递增任何计数器 |
| 同任务排队去重 | `HeartbeatEngine._task_inflight` | tick/manual/AI 四路径共用的执行中集合，排队里同一种任务只允许一条 |
| 崩溃守护与通报 | `start.sh`/`start.bat` 守护循环 + `core/crash_report.py` + `crash_recovery` | 致命信号退出（SIGSEGV 等，退出码 128+n；SIGKILL/SIGTERM 不重启）自动退避重启（5×次数秒，上限 60s），崩溃状态落盘 `logs/crash_state.json`，连续 5 次崩溃停止拉起防崩溃循环（稳定运行 ≥600s 后崩溃重置计数）；重启后 crash_recovery 消费崩溃状态并关联 macOS DiagnosticReports（.ips）生成崩溃上下文——有回复检查点则随中断元消息注入对应会话，无检查点则经 PushHub 写全局通知并唤醒一轮思维（重启报到技能接管向主人报平安）；状态标记 reported 只通报一次。AI 详情查询走 devops `get_crash_report` 工具 / 面板 `/crash-info` |
| ladybug native 串行门 | `agent/memory/cognee/client.py` `_apply_native_gate` | 进程级线程锁串行所有 ladybug native 执行：锁包在提交到线程池的查询任务上（execute + 结果消费全程），由执行线程持有——wait_for 超时取消协程不会提前放锁，孤儿 native 查询跑完才放行下一条；`_drop_native_resources` 同锁保护，拆除句柄前等在途执行结束。修复 2026-08 SIGSEGV（NodeTableScanState::scanNext 空指针，孤儿查询与后续查询/拆除并发使用同一 connection） |
| 技能治理决策协议 | `agent/skills/`（skill_index 事实层 + tools 决策协议） | 事实归系统、决策归 AI：create/update 在事实层检测到显著信号（语义相近≥`skills_similar_threshold` / 触发词碰撞≥`skills_trigger_collision_limit` / 容量水位 / 无实质变化）时**不拒绝**，返回 needs_decision 诊断报告，AI 带 decision 回执重呼写入（rationale 落盘问责）或改走 merge/放弃；评审上下文由 SkillIndex 供给（语义相近 top10 + 库健康摘要）；use/match 信号分离（检索注入不刷活动时间，get_skill 计数不刷活动，策展重力因此可触发）；检索端近重复折叠（≥`skills_match_redundancy` 折叠并入合并信号）；merge_skills 可逆合并（源 ARCHIVED 带 merged_into）；重力含试用期快筛（零参与 14 天降级）与 stale 软保留（仍被检索到不归档）。向量生命周期：缓存键 = 模型名 + 文本 hash（模型切换即全库失效重嵌，防跨模型余弦混算）；交互路径预算化补算（`skills_embed_budget`，advisory 收紧 8），心跳 `warm()` 批量预热；死键清理时机 = 嵌入完成后（warm/embed_now）+ 删除时（service 直调），列表重建不清理（防误杀待嵌入键）；Web 经 `services._runtime` 拿 Mind 侧索引展示 embedded 状态与覆盖统计，CRUD 后 embed_now 即时重嵌；Mind 构造时重绑定工具依赖避免双向量缓存。向量构建状态机（Web 可观测/可操作/可配置）：`build_state()` 暴露 idle/warming/rebuilding + 进度 + 上次重建记录；`skills_warm_batch_size`（心跳每拍批量）/ `skills_rebuild_batch_size`（全量重建批量）可调；Web 经 `POST /skills/vectors/rebuild` 手动触发重建（幂等，进行中返回当前进度）；每个技能行内 `POST /skills/{name}/embed` 单技能生成/重新生成（不等全库重建）。向量持久化：`skill_vectors.sqlite3`（主库同目录独立文件，短连接 schema 自治，pack_embedding float32 BLOB）——嵌入即 upsert，首次访问懒加载恢复（模型+文本 hash 双因子校验，失配行清除并标记重建），**重启零重嵌**；模型切换内存与 DB 同步清空 |
| 思考等级配置驱动下发 | `agent/llm/reasoning.py`（契约引擎）+ `llm_client._apply_thinking_payload` + 模型配置 `thinking` 字段 | **全代码库对模型名零特判**：每个模型在 `llm_clients.json` 里声明思考契约（`{"param": 目标字段, "map": 档位映射, "on": 开启值, "off": 关闭值}`），LLMClient 只做"读契约填值"，不认识任何模型名/供应商。档位能力不写代码——模型该用哪档由配置 `reasoning_effort` 决定，发了端点不认的档由端点自己报错（参考 cursor-byok）。下发载体按 api_type 区分（litellm 行为差异）：openai 兼容通道 extra_body 由 SDK 展开进请求体顶层；anthropic 兼容通道直发 body 不展开 extra_body、未收录模型顶层字段又被能力表卡住，故填顶层字段 + allowed_openai_params 白名单放行。无契约模型走通用 reasoning_effort 透传。effort 为空时开关型契约（无 map）用 on 值默认开启。litellm 1.95 暗坑：未收录模型顶层 reasoning_effort 被 drop_params 静默丢弃，必须走 extra_body/白名单透传 |
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
| 装配重建触发器贯通 | `think_loop` 工具集版本元组 | 版本元组新增 `EntityRegistry.version()`（注意是 classmethod 调用而非属性）——热同步/reload/重载/WebUI 开关等注册表增删后，**回复进行中**的下一轮即重建 active_tools（此前仅 (assembly, activation) 双版本，粘性激活的常驻服务要等下一个版本事件）；每个新回复本就重新装配。重建经追加式冻结保持前缀字节稳定 |

#### 审计 / 扫描 / 限流退避 / 用量归属 / TTFT（第五轮新增）

| 机制 | 位置 | 说明 |
|------|------|------|
| 审批审计持久化 | `agent/approval/audit.py` + `approval_audit` 表 | 所有**非默认放行**的审批决策落账本（人工 approve/deny/cancel/expire、规则拒绝、信任放行、超时放行；常态 rule_allow 高频无信息量不记）。`trust_after_n_approvals` 计数改从账本统计（outcome=approved 累计）——**重启不再从零重数**；trusted 不计入 approved（防自动放行自我强化）。内存 `_decision_history` 已删除，`/approvals/history` 读表分页（offset+tool_name 过滤），stats 走 outcome 聚合。写失败 fail-open 仅记日志 |
| 文件扫描剪枝 | `entities/filesystem/scan.py`（新模块） | os.walk 按目录名剪枝（默认 .git/node_modules/__pycache__/.venv/dist/build/各类缓存，`search_exclude_dirs` 可配置）——不再进结果也不再向下遍历；glob 语义对齐 Claude Code（裸 `*.png` 任意深度、`**/` 零目录语义补齐）；内容模式跳过二进制扩展名与 >2MB 大文件；结果 path 保持绝对路径（直接可喂 read_file） |
| 二进制嗅探 | `scan.looks_binary`（前 8KB NUL 采样） | read_file 扩展名表之外的内容级防线——文本读取走 `errors="replace"` 永不抛解码异常，无扩展名/冷门扩展名二进制文件此前乱码灌上下文；命中返回既有 `{"type":"binary"}` JSON 引导媒体工具 |
| Retry-After 采信 | `agent/llm/retry.py::parse_retry_after` | litellm RateLimitError 携带 headers（本机已验证）；支持秒数/HTTP 日期/毫秒变体。限流退避取 max(服务端指令, 本地抖动指数)；服务端要求 >60s（`RETRY_AFTER_WAIT_CAP`）视为本轮放弃当前候选转回退链——不白烧请求与配额 |
| 用量归属与口径 | `scope_usage.bind_usage_scope` + `_is_ephemeral_scope` | ① 委托链经 ContextVar 绑定父会话 scope，子代理 reflect 的 LLM 用量归属父会话（/status/usage 可见委托成本）；② `reflect:{uuid}` 一次性 scope 不建统计行——此前每个子代理落孤儿行，累积挤爆容量上限后**新会话用量被整体静默丢弃**；③ list/summary 输出 `prompt_miss_tokens = prompt - cache_read`（DeepSeek 口径 prompt 含缓存命中，防消费方相加重复计）。scope 解析链：anything.entity_scope > usage_scope 绑定 > 激活上下文 |
| WebUI 聊天广播 | `core.event_bus.EVENT_CHAT_BROADCAST` + web/routers/chat.py SSE 桥接 | channels/webui 经事件总线推帧（`_broadcast`/`_broadcast_scoped` 发射 EVENT_CHAT_BROADCAST），web 层订阅桥接 SSE 订阅者——频道不反向依赖 web 层（旧 `channels.webui → web.routers.chat` 环已拆）；健康探针改查 `event_bus.has_listeners` |
| TTFT 首 token 计时 | `ChatResult.ttft_ms` + `EVENT_THINKING_LLM_END` | 流式路径记首 delta 到达时刻（毫秒）；与 duration_ms 相减即输出生成耗时——"排队慢"与"生成长"两个独立延迟源分别可诊断（对齐 dsh trajectory TTFT）。非流式为 None |
| 一次性通知历史固化 | `scheduler.enqueue_scope_reply`（async）+ `_append_one_shot_history` | 一次性事件（后台任务完成/实体推送/定时提醒/重启补回/会话切换/委托完成）写目标会话**对话历史**（system，trigger_mind=False）而非短期记忆——此前驻留 volatile 层：每轮重复催促已处理完的事项，且每条新通知重写会话层前缀反复打断 prompt cache，清理全靠模型自觉。await 返回即历史落库，随后的回复周期拉取必含（无竞态）；写入失败回退短期记忆兜底。push 的 seq/inflight 随投递完成后登记（水位只统计已固化事实）。委托轮内会合的完整详情同样固化历史（`_append_one_shot_history` 直达），轮外完成由 registry unclaimed 回调统一负责不双投递；回调支持协程（`_finish` 总在主循环 ensure_future）。短期记忆回归纯持续提醒语义 |
| 子代理转向（Steer） | `agent/delegation/steer.py` + `DelegationManager.steer` + `round_helpers._merge_steered_messages` + 工具 `send_to_agent` | 对齐 dsh steer 语义（2026-08 adjacent-agent-steer-messaging）：运行中委托可在**步骤边界**收到追加指令、改变进行中的工作——不取消重开、已完成部分保留。寻址按 delegation_id（前台/后台统一，只要在 _running）；消息暂存 `SteerInbox`（单委托上限 8 条、单条 4000 字符截断），SubAgent.run 经 `bind_steer_drain` ContextVar 绑定 drain 闭包（create_task 复制进整个执行树），think_loop 轮顶 drain 注入工具链尾部（user 角色 + [转向指令] 标记 + `_source:steer`），当轮 LLM 即见；主会话未绑定 drain 恒空零开销（用户插话本有 _merge_new_messages 机制）。委托结束（成败/超时）finally 清箱防残留误入后续同名委托。工具返回结构化错误：不存在（not_found，引导 check_background_tasks）/空消息/超上限 |
| SSE 断线可见性与恢复 | `chat-store.ts`（sseConnected + refreshAfterReconnect）+ MessageList 横幅 | 对齐 dsh 连接恢复指示器：`es.onopen` 置连上、`onerror` 置断开——聊天流顶部显示琥珀色"正在重连"横幅（i18n zh/en）；断线后重连（_wasConnected 区分初次）自动补拉当前会话最近一页历史，按消息 id 尾部对齐合并（保留本地已加载的更早消息），修复断线窗口内落地的回复帧（delta/turn_end）静默缺失需整页刷新的问题。sending 卡死由既有发送看门狗兜底，不误复位进行中回复 |
| 反思产出语义（纯结论） | `think_loop._handle_tool_round` 工具轮边界清空 | REFLECT 模式下模型发起工具调用即判定此前纯文本为中间独白（"我先分析一下…"）——从 collected_text 移除（字符数归档进 execution_steps 可追溯），产出只保留收束前**最后一个未被工具调用打断的连续文本段**。此前全轮合并 + 聚合截断（头75%尾25%）会让中间噪音挤占 [2000,24000] 预算、稀释关键结论——子代理结果、任务产出（存记忆）、元决策 REFLECT 输入三处同时受益。REPLY 模式 collected_text 无消费方，零影响 |
| 委托结束原因贯通 | `think_loop completion 容器` → `mind.reflect(completion=)` → `SubAgentResult.completed_reason` | 结束原因三值：completed / budget_exhausted（轮次预算用尽，产出可能只是中途状态）/ interrupted（协作中断），经调用方传入的 completion 字典带出（不传容器零影响）。聚合结果对 budget_exhausted 条目附 `hint`（"拆小任务重新委托"），后台完成通知同样标注——父代理可区分"完整结论"与"半成品"并决策续委托。空产出时 no_output |
| 前台委托注册表化 | `delegate()` registry 登记 + killer + `complete(claimed=True)` | 前台/嵌套委托同样登记 BackgroundTaskRegistry：check_background_tasks 可见（含耗时）、terminate_background_task 可单独停止（killer 走 _cancel_marks + 桥回主循环 cancel，转"用户取消"结果返回父级，不再只能中断整个回复或等 600s 超时）。完成走 `complete(..., claimed=True)`（调用方声明结果已被工具返回值消费，跳过轮外完成回调防双投递；异常路径也收尾防条目滞留 running）。`registry.complete` 新增 claimed 覆盖参数（None=按等待者判定，不变行为） |
| 压缩文件清单累积 | `context_compressor._extract_file_operations` + `[已操作文件]` 行 | 从被压缩中间段的工作链**规则提取**"读过/改过"的文件清单（确定性，不经 LLM 保真），作为独立 `[已操作文件]` system 消息在摘要区追加——不随摘要文本的有损转述衰减、不占 summary_max_chars 预算；下次压缩时从摘要文本回读合并（`_extract_file_operations_from_summary`，单调增长的事实链）。LLM 摘要管语义（任务/决定/实体），规则清单管文件事实——两层分离。上限 30 条/类 |
| hook REPLACE 解析 | `agent/hooks/runner._extract_replace` | hook stdout 输出一行前缀 `REPLACE:<json-string>` 即返回替换内容（`HookOutcome.replace`，串行取第一个）；非字符串/非 JSON 静默忽略（stdout 是日志通道，向后兼容）。**当前无消费方**——reply_end 的 replace 消费经核验无效（complete_reply 的 content 恒空，出站文本已投递），已在实现中回退，解析层保留供未来 tool_post 等场景复用 |
| 外围工具细节 | `web_download(timeout=300)` / `python_exec` 落盘 / 只读并发标记 | ① web_download 注册 timeout=300——AI 参数（默认 30s）在此范围内生效，此前未声明落入全局默认 60s 提前掐断（AI 传 120s+ 也是死配置，与 MCP call_timeout 同款错配）；② python_exec stdout 超 30000 字符经 `shell_state.truncate_or_persist` 落盘（`.tool-results/` + persisted 路径，模型 read_file 分段取回）——与 run_shell_command 对称，不再截断丢弃；stderr 仍 1000 字符小限截断（多为回溯/警告）；③ 18 个纯读工具补标 `concurrency_safe`（模型查询三件套/系统查询组/实体查询组/get_crash_report/ui_get_state/list_voices/repo_docs/rerank_search/get_entity_config）——与写工具同轮混发时不再被切进串行批，并行机会不流失 |
| cognee LanceDB 物理压缩与存储统计 | `cognee/storage.py`（物理存储维护模块：压缩 + 统计 + 快照 + 调度）+ coordinator 空闲窗口调度 | cognee 删除/更新只在 Lance 追加 tombstone 新版本，历史版本物理数据永不回收（磁盘单调膨胀的根因）。`compact_lance_tree` 遍历 `system/databases/**/*.lance.db` 逐表 `optimize(cleanup_older_than)`（碎片合并+索引优化+清理早于 `compact_retention_days` 的版本，最新版本永远保留、逻辑数据零影响），压缩前后用同一条统计遍历实测占用；worker 队列排空后的空闲窗口按 `compact_interval_seconds`（默认 86400s）自动执行（与写入单消费者天然互斥），失败仅记日志下个周期重试。手动触发三入口同路径 `coordinator.request_compact()`：AI 工具 `compact_cognee_storage`（memory 组）/ `POST /memory/cognee/compact` / Web 记忆页「压缩存储」按钮；worker 存活时登记请求待空闲执行，未运行则内联执行。状态经 sync.last_compact_at/last_compact_summary 暴露。`StorageStatsTracker`（单例 `cognee_storage_stats`）：大库遍历可达数十秒，请求路径永不遍历——内存 TTL → 磁盘快照（`<data_root>/storage_stats.json`，重启即恢复真实值）→ 空统计三级返回，过期仅调度后台单任务刷新；所有缓存写入携带单调代际号，invalidate/adopt/新刷新使在途旧遍历结果被丢弃（防压缩后数字被旧遍历回写）；coordinator 启动预热、压缩尾声 `adopt(after_stats)` 直接收录实测值免二次遍历、rebuild 清场后 `invalidate(root)` 连快照删除。`/cognee/status` 的 storage 字段与数据库管理页 cognee 条目（size_bytes=整个数据目录，此前仅 stat 元数据库文件曾 177M 显示 vs 30G 实际）共用该 tracker |
| 存储卷（数据平面模块化管理） | `core/storage_volume.py`（注册表 + 位置指派 + 主库路径权威 `main_sqlite_path`）+ `agent/storage/volume_restore.py`（重启落盘交换）+ `services/volume_ops.py`（备份/恢复/迁移/SQL 导出导入） | 所有持久化数据统一登记为存储卷（8 卷：agent 主库 / memory / skill_vectors / stickers / voiceprints / share 六个 SQLITE + cognee 树 + 便签树），各存储模块 import 时自注册 VolumeDescriptor（惰性 default_path 保持测试隔离）；同族库路径均由 `main_sqlite_path()`（env > 项目根 ConfigPaths.SQLITE_DB）派生 stem，放在 core 使 entities 无需依赖 agent。路径解析优先级：env_override > 位置指派（`config/storage_volumes.json`，cognee 卷转发 cognee.json data_root 单一权威）> 模块默认派生——**无指派文件时所有路径与历史完全一致，数据零移动**。能力按形态派生：SQLITE 全量（备份/恢复/迁移/SQL 导出导入）、cognee 树无 SQL 传输、便签树（路径即数据根）仅备份/恢复（占用也只计卷成员：根级 *.md + events/groups/profile_backups，不计数据根其余内容）。备份：SQLite 走 Backup API 在线热备（`services.database.online_sqlite_backup` 唯一实现，整目录迁移同源复用）、树走 tgz（cognee 经 coordinator `run_in_idle_window` 空闲窗口与写入互斥，manifest 的 consistency 如实标注）；保留数 `volume_backup_retention`（storage/backup 组，默认 5）自动清理。恢复与迁移均为「校验 + 拷贝 + 指派/标记 + 重启生效」：恢复写 pending 标记（`<data_dir>/backups/volumes/.pending-restore.json`），bootstrap `init_storage` 最早消费（任何连接打开前交换文件；旧库 -wal/-shm 必清除防回放；现文件留 `.pre-restore-<ts>.bak` 滚动保留 3 份）；便签树恢复为选择性覆盖，cognee 树整树替换。迁移目标校验复用 `data_migration.validate_target_dir`（i18n 标识 tokens 共用），尺寸估计异步分口径（cognee 走统计缓存/便签走成员/SQLite 走 stat），不在事件循环上遍历大目录。外部 SQL 为备份/转移通道（运行时各库仍本地 SQLite）：`SqlTransferClient`（与只读浏览适配器分离的写通道）做 DDL 方言翻译 + rowid 窗口流式批量传输，导出登记清单表 `_anelf_export`、导入仅认清单（快照往返闭环）；派生索引（FTS5/vec0 影子表）不传输，导入后由各存储建表逻辑重建。Web 面板：数据管理页「存储卷」Tab（`pages/database/volumes/`），API 前缀 `/database/volumes`；`services.database.ensure_volume_modules()` 兜底触发卷登记，库注册表由卷驱动（share 库由此补登），cognee 浏览路径仍指元数据库文件。目录遍历/占用统一走 `core.file_utils.walk_files/directory_size` |

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

### 前端结构

页面采用壳组件 + 子面板目录拆分模式，通用 TabBar 切换：

```
pages/
├── Chat.tsx             # 对话工作台（首页，三栏：文件树/对话流/功能 Dock）→ chat/
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

components/common/TabBar.tsx  # 统一标签栏
lib/types.ts / api.ts         # API 接口类型（接口集中在 types.ts，api.ts 引用；api 实例已导出供插件复用）
lib/core-routes.ts            # 核心路由注册表（App.tsx 引用；Sidebar 据此识别插件导航项）
lib/channel-plugins.ts        # 频道前端插件注册表（清单驱动频道卡片登录入口/展开面板/整页路由/列表隐藏）
lib/plugin-i18n.ts            # 插件 i18n 自注册（addResourceBundle 双语 deep 合并）
lib/utils.ts                  # cn() 类名合并工具（样式走 Tailwind 内联类，无独立 styles.ts）
i18n/locales/{zh,en}/         # 核心 namespace（zh/en key 须一一对应；插件文案不进核心 locale）
```

#### 模块前端插件体系（热插拔）

频道/实体的前端与后端收敛到同一模块目录，核心框架只做通用加载，删除模块目录即整体拔出（UI/API/文案/路由零残留）：

- **频道前端**：`channels/<id>/frontend/`（index.ts 清单 + components/ + api.ts + types.ts + locales/{zh,en}.json），经 `moduleFrontendsPlugin`（vite.config.ts）/ `scripts/link_entity_panels.py` 整目录软链到 `src/plugins/channels/<id>/`（**软链须提交 git**——CI 中 `tsc -b` 先于 vite buildStart）。index.ts 为轻量 eager 清单：`registerPluginI18n("channel-<id>", {zh, en})` 自注册文案 + 组件 loader 动态 import。清单字段：`login`（频道卡片登录入口）/ `panel`（卡片展开区自定义面板）/ `route`+`page`（整页路由，App.tsx 动态注册）/ `hiddenInChannelList`（频道列表隐藏）。频道页（AdapterCard/UnmatchedGroupCard/ChannelsPanel/ChannelTestPanel/Sidebar）全部经 `lib/channel-plugins.ts` 注册表驱动，**禁止 `key === "xxx"` 硬编码**
- **实体面板**：`entities/<name>/panel.tsx`（+ `panels/` 子目录拆分）软链到 `src/pages/entities/panels/`；面板专属 i18n 放 `panels/locales/{zh,en}.json` 由 panel.tsx 顶部 `registerPluginI18n(<ns>)` 自注册（ns 名不变）；面板专属 API/类型放 `panels/api.ts` / `panels/types.ts`（不污染核心 lib/api.ts、lib/types）。**共享型例外**（被核心页面消费的实体功能留核心）：sticker（核心表情包库页）、share（聊天 ShareCard）、mcp / graph / devops（核心管理页共用其 API）
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
| `agent/delegation/sub_agent.py` | 子代理（leaf/orchestrator 角色 + 深度限制） |
| `agent/delegation/delegation_manager.py` | 委托调度（并发上限/预算/聚合/后台模式） |
| `agent/delegation/delegate_tool.py` | delegate_task 工具（agent_name 直指子代理档案；difficulty 1/2/3 为内置档案语法糖） |
| `agent/mind/work_memory.py` | 工作记忆数据面（消息队列 / 待办持久化 / 短期记忆（溢出晋升 events 便签）/ 态势路由，PFC 组件） |
| `agent/mind/tool_assembly.py` | 工具装配（召回 / tag 激活 / schema 合并门控，PFC 组件） |
| `agent/mind/context_assembly.py` | 上下文组装（系统提示 / Prompt 分层缓存 / 执行上下文，PFC 组件） |
| `agent/mind/context_pipeline.py` | 上下文构建管线（@context_block 声明层+变动率 / 变动率排序组装 / 缓存断点注入 / legacy 布局覆盖表；新增内容块只需声明式注册） |
| `agent/mind/tools/decision_executor.py` | 决策执行分发（REPLY/REFLECT/PLAN 等） |
| `agent/mind/push.py` | 实体推送中枢 PushHub（[push:] 标签包装 + 短期记忆 + 入队唤醒 + 轮内弹窗 drain_inflight；entities 经 _sdk.push_notify 桥接） |
| `agent/mind/tools/media_pipeline.py` | 媒体标签转换 |
| `agent/memory/memory_store.py` | 长期记忆存储（SQLite + FTS5 + Embedding；软归档遗忘 + importance 松弛回归） |
| `agent/memory/graph/store.py` | 关系图谱权威存储（graph_nodes/graph_edges；(s,p,o) 唯一 upsert + 别名归一 + 软删 + cognee 投影入队） |
| `agent/memory/graph/tools.py` | 关系图谱工具组（graph_add_relation / graph_query / graph_path / graph_merge_nodes 等，group=graph） |
| `agent/memory/graph/extract.py` | 心跳关系抽取（对话 → JSON 候选解析 → 落库，origin=heartbeat_extract） |
| `agent/memory/store/tag_intel.py` | 标签智能（df/共现图谱/提及词表 TTL 缓存；IDF 评分、共现与图谱邻居联想、查询提及识别的统一驱动层） |
| `agent/storage/scope_migrate.py` | scope 迁移（旧格式键回填 adapter 维度，user_version 幂等 + 自动备份） |
| `agent/memory/tools.py` | 记忆工具（memorize / recall（source 标志 + depth 浅深 + filter_tags 硬过滤）/ forget 软归档） |
| `agent/memory/notes.py` | 便签文件系统 |
| `agent/task/model.py` | 任务数据模型（TaskDefinition / TaskResult） |
| `agent/task/registry.py` | 任务注册表（config/tasks/*.json 加载/CRUD） |
| `agent/task/executor.py` | 任务执行器（LLM 调用 + 结果存储；`task_lean_context` 精简上下文：人设+工具+永久记忆+任务指令，环境便签/召回/状态由任务按规则经工具取回——任务间共享稳定前缀、每轮 prompt 更小；`extra_note` 尾部追加动态备注，idle 反思原因注入不破前缀） |
| `agent/task/tools.py` | 任务/调度自管理工具（create_task / update_task / delete_task / set_task_schedule，与 Web 管理面同路径热重载） |
| `agent/heartbeat/engine.py` | 心跳调度引擎（tick 循环 + 内置维护 + 主便签 AUTO:memory-status 状态区块） |
| `agent/heartbeat/config.py` | 心跳配置（HeartbeatConfig + TaskSchedule） |
| `agent/heartbeat/log.py` | 心跳日志读写 |
| `agent/planning/tools.py` | 规划工具（create_goal/update_goal/delete_goal） |
| `agent/runtime/bootstrap.py` | 启动流程（初始化 → 组装 → 启动 → 健康检查） |
| `agent/runtime/state_restore.py` | 启动状态恢复（工具覆盖/实体启停/自定义标签回放，纯 core 操作；services 同名方法委托于此） |
| `agent/runtime/singleton.py` | AgentRuntime 全局单例（get_runtime Optional 读 / require_runtime 未就绪抛错；services._runtime 为其 web 侧门面） |
| `entities/_sdk.py` | 工具注册 + LLM 桥接 |
| `agent/channel/manager.py` | 频道管理（register / route / activate_channel 动态加载未注册频道 / set_channel_enabled 启停意图落盘 / list_configured_channels 目录扫描） |
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
| `core/context_provider.py` | 上下文提供者注册表（实体 → volatile 层实时快照注入；provider 以 group 声明所属工具分组，分组工具全禁用时停止采集与注入，与实体目录可见性同口径） |

### 工具分组体系

#### group key 规范

工具分组 key 是全局标识符，**必须使用英文**，前端通过 i18n 翻译展示中文/英文名称。

- 后端注册：`group="thinking"` / `entity("web", "...")`
- 前端翻译：`i18n/locales/{zh,en}/tools.json` → `groups.thinking` → "思维工具"
- 前端使用：`t(`groups.${g.group}`, { defaultValue: g.group })`

修改分组名时必须同步更新：
1. 后端 `@tool(group=...)` / `@deferred_tool(group=...)` / `entity(group, ...)` / `activate_group(group, ...)`
2. 前端 `i18n/locales/zh/tools.json` 和 `en/tools.json` 的 `groups` 对象
3. `core/entity.py` 的 `_DEFAULT_GROUP_ORDER`（LLM 工具目录排序）
4. `services/tool.py` 的 `_GROUP_ORDER`（WebUI 工具列表排序）

#### 当前分组索引

| group key | 中文名 | 注册文件 | tags |
|---|---|---|---|
| `output` | 消息输出 | `channel/output_tools.py` | always |
| `memory` | 记忆管理 | `agent/memory/tools.py` | always/core/heartbeat |
| `graph` | 关系图谱 | `agent/memory/graph/tools.py` | always/core/heartbeat |
| `notes` | 便签记忆 | `agent/memory/notes.py` | core/heartbeat |
| `thinking` | 思维工具 | `agent/mind/mind.py` + `agent/mind/tool_activation.py` + `agent/mind/context_compressor.py` + `agent/mind/tools/short_term_tools.py`（短期记忆自管理） | always |
| `planning` | 目标规划 | `agent/planning/tools.py` + `agent/task/tools.py`（任务/调度自管理） | planning/goal/heartbeat |
| `skills` | 技能 | `agent/skills/tools.py` | always |
| `delegation` | 子代理 | `agent/delegation/delegate_tool.py` | always |
| `ui` | 界面交互 | `entities/ui/tools.py`（经 event_bus `EVENT_UI_COMMAND` → 聊天 SSE 桥接） | always |
| `web` | 网络工具 | `entities/web/tools.py`（接口层）+ `providers/`（能力×提供者矩阵：检索/网页读取/仓库文档为统一 Protocol，提供者实现子集、可启停；builtin 本地直连/minimax/bigmodel；凭据链=实体配置→llm_clients.json 供应商回退→环境变量）+ `fetcher.py`（直连抓取设施）+ `router.py`（/api/entity/web 矩阵管理面） | web/search/fetch |
| `media` | 多媒体 | `entities/media/tools.py` | media:* |
| `os` | 操作系统 | `entities/filesystem/tools.py` | media:file |
| `ssh` | SSH 远程管理 | `entities/ssh/tools.py` | —（整组 allow_sleep 沉睡，`activate_tool_group` 唤醒） |
| `voiceprint` | 音源库 | `entities/voiceprint/tools.py` | always/core/media:voice/media:audio |
| `sticker` | 表情包 | `entities/sticker/tools.py` | always/media:image（部分工具 allow_sleep） |
| `environment` | 环境信息 | `entities/system/tools.py` | — |
| `model_control` | 模型控制 | `entities/model_control/tools.py` | core |
| `ollama` | Ollama | `entities/model_control/tools.py` | — |
| `logs` | 日志查询 | `entities/logs/tools.py` | — |
| `channel_ops` | 频道操作 | `agent/channel/tool_bridge.py`（@channel_tool 动态）+ `agent/channel/manage_tools.py`（频道启停 start_channel/stop_channel，敏感门控 + risk=CRITICAL，启停意图落盘 channel_config.json 的 enabled） | capability/channel_id/core |
| `entity` | 实体管理 | `entities/entity_query/tools.py` | always/core |
| `mcp_manage` | MCP 管理 | `entities/mcp/bridge.py`（动态） | — |
| `mcp:*` | MCP 服务 | 动态注册 | — |
| `devops` | 运维管理 | `entities/devops/tools.py`（重启/构建/git 更新/崩溃信息查询 get_crash_report，核心逻辑在 `service.py`，Web 面板经 `router.py` + `panel.tsx` 复用同一实现） | — |

### 缓存命中率排查手册（ZCode 排障）

LLM 前缀缓存命中率是本项目的核心成本/性能指标。缓存工程分三层责任，排查时**先定位层再下结论**，不要默认"缓存崩了"：

1. **客户端字节稳定性**（完全可控）：变动率排序组装 + tools 冻结 + 摘要窗口 + 单一装饰点。验证 = 快照 section 哈希 diff + **PrefixGuard 运行时哈希链**（records.jsonl 的 `prefix_drift` 字段定位首个断裂消息）。
2. **供应商缓存行为**（不可控）：磁盘缓存传播延迟/驱逐/节点亲和。判读特征 = prefix_stable=True 而 read 浮动、1~2 轮自愈。
3. **统计与展示口径**：kind 分桶 / age_sec 回声 / unobservable / 单次钳制率平均。

> **完整手册**（诊断决策树、PrefixGuard、断点预算、压缩前缀复用、e2e 回归、供应商字段）见 [`docs/cache-troubleshooting.md`](docs/cache-troubleshooting.md)。

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

**系统注入消息必须带 `_source` 来源标记**：think_loop / round_helpers / context_compressor 向消息链注入的 system 元消息（压缩反馈、rehydration、超时恢复、长度恢复、后台任务、实体推送等）须附 `"_source": {"origin": "<词汇>"}`，发送前由 `normalize_for_send` 与 `_layer` 一并剥离（LLM 不可见，供快照归因/审计）。已用词汇：`compression` / `rehydration` / `timeout_recovery` / `length_recovery` / `background_task` / `push`；新增注入点复用或扩充词汇表，勿省略标记。注意 `_source` 不进 DB（对话历史只存 role/content），仅作用于内存消息链。

**Model Experience 三行声明（新功能必答）**：任何影响模型输入/输出的新功能，须在其模块 docstring 或本表登记三件事——① 模型看到什么（注入了什么内容、走哪个通道）② token 影响（增量还是节省、量级）③ 缓存影响（是否触碰前缀层；volatile/tool_chain 尾部动态区则注明不破前缀）。对齐 dsh 每 README 必答 "Model Experience / Token effect / KV Cache effect" 的纪律——缓存是本项目一等指标，新功能不声明即视为未评估。

**频道开发**：继承 BaseChannel、6 个必需接口（channel_id / display_name / capabilities / start / stop / send_text）

**前端**：页面超过 300 行拆为子面板目录、统一用 TabBar、i18n 覆盖所有文本、`Record<string, unknown>` 替换为 `lib/types.ts` 接口

**生命周期**：有状态单例与长驻服务一律 `Lifecycle.register(name, instance, on_start=start_fn, cleanup=close_fn)` 注册（注册顺序 = 启动顺序，逆序 = 关停顺序）；关闭时由 Application 宿主统一 `Lifecycle.shutdown_all()`，禁止在入口脚本手工编排服务清理

**包管理**：项目依赖由 uv 管理（`pyproject.toml` + `uv.lock`），安装依赖用 `uv add`，临时操作用 `uv pip install`；禁止对 `.venv` 使用 `pip install` / `ensurepip`（uv 创建的 venv 默认不含 pip，属正常状态而非故障，不要"修复"它）

**litellm 版本**：当前 1.95。1.96/1.97 在本项目（Python 3.10）下 `ModelResponse()` 崩溃——根因是新版引用了未 import 的符号，在 py3.10 的注解求值下暴露（py3.11 下 1.97 正常）。**升级路径：先把项目升到 Python 3.11（`requires-python` 已允许 `<3.12`），再升 litellm**；1.98(main) 起 litellm 已要求 py3.11+。升级前验证 `litellm.ModelResponse()` 可实例化即可。我们用的官方机制（`extra_body` 透传 / `allowed_openai_params` 白名单 / `register_model` 能力声明 / `drop_params`）在 1.97 仍在维护（#35885 修了 allowed_openai_params 经 bridge 转发）。

**测试体系**：测试面分两类——**分层套件** `tests/`（`tests/unit/` 纯 mock/纯函数/tmp_path 快速单测，按被测层分目录 core/agent/services/web；`tests/integration/` 真实应用组装或需外部凭证，需凭证的用例 env-gated 自动 skip）与**模块内套件** `<模块>/tests/`（`entities/<name>/tests`、`channels/<id>/tests`，测试随模块目录走，删除模块即整体拔出零残留；跨模块共享件测试留在 `tests/unit/entities/`）。目录归属由仓库根 `conftest.py` 自动打 `unit`/`integration` marker（模块内 tests/ 自动 unit），无需手写。根 conftest 全局隔离 ConfigManager（指向 tmp_path），新测试不得读写真实 `config/`。运行：`uv run pytest`（全量）/ `uv run pytest tests/unit`（分层单测）/ `uv run pytest entities/web/tests`（单模块）/ `uv run pytest -m integration`；加 `-n auto` 并行（已装 pytest-xdist，全量约 64s→26s，CI 已启用；单测调试/`--pdb` 时去掉）。CI（`.github/workflows/ci.yml`）模块化三 job：`changes`（原生 shell 归类：push 对 HEAD^、PR 对目标分支基点对比）→ `lint`（ruff + import-linter + mypy core 三平台必过/全量观察，静态门禁与改动面无关始终全仓）+ `tests`（**模块动态矩阵**：实体/频道改动只跑对应 `模块/tests` 腿并附带跨实体共享套件，`core/`、`agent/`、`tests/`、根 `conftest.py`、依赖锁定等横切改动触发 `all` 全量腿；services/web 后端各有专属腿；`fail-fast: false` 保留完整失败面，GitHub UI 按模块独立呈现红绿）+ `frontend`（lint/build；模块前端 `channels/*/frontend/`、实体 `*.tsx` 面板同属前端改动面）；文档类提交全跳过，`workflow_dispatch` 手动触发全量；各 job 均有 timeout-minutes 挂起护栏，覆盖率产物按腿上传（保留 7 天）。

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