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
| `core/` | 基础框架（EntityRegistry / ConfigManager / Lifecycle / PathManager+ConfigPaths / 标签 / 事件 / 日志） | 不依赖任何业务模块 |
| `agent/` | 智能体内核（Mind / LLM / Storage / Channel / Runtime / Memory / Task / Heartbeat / Planning） | 不依赖 web |
| `agent/mind/` | 思维核心（自主决策 / 多轮推理 / 跨频道感知） | 工具编排在 `mind/tools/` |
| `agent/memory/` | 语义记忆（FTS5 + Embedding 混合检索 / 便签 / 文件索引） | 不依赖 mind |
| `agent/skills/` | 技能自学习（SKILL.md 存储 / 匹配 / 后台评审 / 策展） | 文件存储在 `workspace/skills/` |
| `agent/delegation/` | 子代理调度（delegate_task / 并行 fan-out / 深度限制） | 经 `mind.reflect()` 隔离执行 |
| `agent/security/` | 安全防护（会话令牌 / 威胁扫描） | 脱敏核心在 `core/sanitizer.py` |
| `agent/task/` | 独立任务系统（定义 / 注册表 / 执行器） | 纯内容定义，不含调度逻辑 |
| `agent/heartbeat/` | 心跳调度（引擎 / 配置 / 日志 / 内置维护） | 管理何时执行任务，持久化计数器 |
| `agent/planning/` | 自主规划（目标 CRUD / 执行追踪） | 依赖 memory |
| `channels/` | 频道适配器（目录自动发现） | 继承 BaseChannel |
| `entities/` | 工具实体（目录自动发现） | 通过 `@tool`/`entity()` 注册，通过 `_sdk.py` 桥接 LLM |
| `services/` | 业务封装层 | 供 Web API 调用，不依赖 web |
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

禁止: agent → web | core → agent | services → web | entities → agent（通过 _sdk 桥接）
```

### 核心系统

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

#### MCP 工具面细节（第四轮新增，均在 `entities/mcp/bridge.py`）

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
lib/types.ts / api.ts         # API 接口类型（接口集中在 types.ts，api.ts 引用）
lib/utils.ts                  # cn() 类名合并工具（样式走 Tailwind 内联类，无独立 styles.ts）
i18n/locales/{zh,en}/         # 20 个 namespace（zh/en key 须一一对应）
```

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
| `agent/mind/tools/think_loop.py` | 统一思维循环（多轮 LLM + 工具编排） |
| `agent/llm/resilience/classifier.py` | LLM 错误分类（驱动重试/压缩/回退策略） |
| `agent/llm/prompt_cache.py` | Anthropic 缓存断点唯一权威（线型判定 / 发送边界装饰 decorate_messages / 锚点表 / strip 副本 / TTL marker / CACHEABLE_PREFIX_LAYERS 分析口径） |
| `agent/llm/retry.py` | 自适应退避（指数 + 抖动） |
| `agent/security/session_token.py` | 一次性会话令牌（防注入伪造历史） |
| `agent/security/threat_scanner.py` | 威胁模式扫描（prompt 注入检测） |
| `core/sanitizer.py` | 敏感信息脱敏（API Key/Token/密码） |
| `core/tool_gate.py` | 工具门控（check_fn TTL 缓存 + 瞬态宽限） |
| `core/tool_errors.py` | 工具错误返回统一设施（tool_error / error_from_exception + ErrorCause 归因） |
| `agent/skills/skill_store.py` | 技能存储（workspace/skills/SKILL.md） |
| `agent/skills/skill_matcher.py` | 技能匹配（关键词 + 语义混合评分） |
| `agent/skills/background_review.py` | 技能后台评审（对话后自动沉淀经验） |
| `agent/skills/curator.py` | 技能策展（自动降级/归档，挂心跳维护） |
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
| `entities/_sdk.py` | 工具注册 + LLM 桥接 |
| `agent/channel/manager.py` | 频道管理（register / route） |
| `agent/channel/tool_bridge.py` | 频道工具桥接（@channel_tool 扫描注册 / 通用能力路由 / 敏感门控 / 按频道接口开关 channel_tool_states） |
| `agent/channel/context.py` | 当前会话频道 ContextVar（通用工具默认路由目标） |
| `web/routers/config.py` | 心跳/任务 API + Mind 配置 API |
| `web/routers/config_meta.py` | 统一配置元数据 API（ConfigRegistry 驱动，数据驱动配置中心） |
| `web/routers/workspace.py` | 工作区文件 API（目录树 / 读写 / 搜索，沙箱复用 entities.filesystem） |
| `web/routers/database.py` | 数据管理 API（SQLite 浏览/维护/备份 + 外部连接 CRUD + 数据目录迁移） |
| `web/routers/search.py` | 全局搜索聚合 API（记忆 / 日志 / 文件 / 会话） |
| `services/db_connections.py` | 外部 SQL 连接（注册表 + PG/MySQL 只读适配器，config/db_connections.json） |
| `services/data_migration.py` | 数据目录迁移（在线热备份拷贝 + 校验 + data_root 切换） |
| `entities/ui/tools.py` | 界面交互工具组（ui_notify / ui_ask / ui_open_panel / ui_compose / ui_get_state） |
| `web/frontend/src/pages/chat/` | 对话工作凳子面板（Dock / StatusBar / FileEditor / UiCommandHost / render） |
| `web/frontend/src/stores/chat-store.ts` | 对话状态 + 聊天 SSE（含 ui_command 分发） |
| `web/frontend/src/stores/workbench-store.ts` | 工作台状态（Dock / 编辑器 / UI 命令收件箱 / 状态上报） |
| `core/path.py` | PathManager + ConfigPaths 动态路径（config_dir/data_dir 可搬迁） |
| `core/lifecycle.py` | 单例生命周期注册表（register / shutdown_all / reset） |
| `core/crash_report.py` | 崩溃状态设施（守护脚本崩溃状态 logs/crash_state.json 读写 + macOS .ips 崩溃报告解析关联 + AI 可注入摘要渲染） |
| `agent/mind/crash_recovery.py` | 崩溃尾部修复（回复检查点残留注入中断元消息 + 崩溃上下文收集消费） |

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
| `web` | 网络工具 | `entities/web/tools.py` | web/search/fetch |
| `media` | 多媒体 | `entities/media/tools.py` | media:* |
| `os` | 操作系统 | `entities/filesystem/tools.py` | media:file |
| `ssh` | SSH 远程管理 | `entities/ssh/tools.py` | —（整组 allow_sleep 沉睡，`activate_tool_group` 唤醒） |
| `voiceprint` | 音源库 | `entities/voiceprint/tools.py` | always/core/media:voice/media:audio |
| `sticker` | 表情包 | `entities/sticker/tools.py` | always/media:image（部分工具 allow_sleep） |
| `environment` | 环境信息 | `entities/system/tools.py` | — |
| `model_control` | 模型控制 | `entities/model_control/tools.py` | core |
| `ollama` | Ollama | `entities/model_control/tools.py` | — |
| `logs` | 日志查询 | `entities/logs/tools.py` | — |
| `channel_ops` | 频道操作 | `agent/channel/tool_bridge.py`（@channel_tool 动态） | capability/channel_id |
| `nonebot` | NoneBot 桥接 | `channels/nonebot_bridge/adapter.py`（@channel_tool，worker 子进程客户端） | always/nonebot_bridge（restart/install 敏感门控） |
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

**系统注入消息必须带 `_source` 来源标记**：think_loop / round_helpers / context_compressor 向消息链注入的 system 元消息（压缩反馈、rehydration、超时恢复、长度恢复、后台任务、实体推送等）须附 `"_source": {"origin": "<词汇>"}`，发送前由 `normalize_for_send` 与 `_layer` 一并剥离（LLM 不可见，供快照归因/审计）。已用词汇：`compression` / `rehydration` / `timeout_recovery` / `length_recovery` / `background_task` / `push`；新增注入点复用或扩充词汇表，勿省略标记。注意 `_source` 不进 DB（对话历史只存 role/content），仅作用于内存消息链。

**Model Experience 三行声明（新功能必答）**：任何影响模型输入/输出的新功能，须在其模块 docstring 或本表登记三件事——① 模型看到什么（注入了什么内容、走哪个通道）② token 影响（增量还是节省、量级）③ 缓存影响（是否触碰前缀层；volatile/tool_chain 尾部动态区则注明不破前缀）。对齐 dsh 每 README 必答 "Model Experience / Token effect / KV Cache effect" 的纪律——缓存是本项目一等指标，新功能不声明即视为未评估。

**频道开发**：继承 BaseChannel、6 个必需接口（channel_id / display_name / capabilities / start / stop / send_text）

**前端**：页面超过 300 行拆为子面板目录、统一用 TabBar、i18n 覆盖所有文本、`Record<string, unknown>` 替换为 `lib/types.ts` 接口

**生命周期**：bootstrap 中创建的有状态单例须调用 `Lifecycle.register(name, instance, cleanup=close_fn)` 注册；关闭时 `Lifecycle.shutdown_all()` 逆序清理

**包管理**：项目依赖由 uv 管理（`pyproject.toml` + `uv.lock`），安装依赖用 `uv add`，临时操作用 `uv pip install`；禁止对 `.venv` 使用 `pip install` / `ensurepip`（uv 创建的 venv 默认不含 pip，属正常状态而非故障，不要"修复"它）

**测试体系**：`tests/` 分两层——`tests/unit/`（纯 mock/纯函数/tmp_path，快速）与 `tests/integration/`（真实应用组装或需外部凭证，需凭证的用例 env-gated 自动 skip）；目录归属由根 `tests/conftest.py` 自动打 `unit`/`integration` marker，无需手写。根 conftest 全局隔离 ConfigManager（指向 tmp_path），新测试不得读写真实 `config/`。运行：`uv run pytest`（全量）/ `uv run pytest tests/unit`（快速）/ `uv run pytest -m integration`。CI（`.github/workflows/ci.yml`）在 push/PR 时执行 ruff + mypy（core 必过、全量观察）+ pytest（含覆盖率）+ 前端 lint/build。

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