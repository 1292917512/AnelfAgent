---
description: "AnelfAgent 项目指令 — 开发规范与架构速查（由 .cursor/rules/*.mdc 迁移而来，对所有提示全量注入）"
---

# AnelfAgent — 项目指令

本文件由 `.cursor/rules/global.mdc` 与 `.cursor/rules/architecture.mdc` 合并而来。
Cursor 用户继续读取 `.cursor/rules/*.mdc`，ZCode 读取本文件，互不影响。

> **定位说明**：本文件是供 AI 代码编辑器（ZCode / Cursor）读取的**工作区指令文件**，
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

配置路径常量集中管理，避免硬编码分散：

```python
ConfigPaths.APP_CONFIG          # config/app_config.json
ConfigPaths.WEBUI_CONFIG        # config/webui.json
ConfigPaths.MCP_SERVERS         # config/mcp_servers.json
ConfigPaths.HEARTBEAT_CONFIG    # config/heartbeat.json
ConfigPaths.TASKS_DIR           # config/tasks
ConfigPaths.UPLOAD_DIR          # workspace/uploads
```

#### 标签系统（core/tags.py）

`[key:value]` 统一数据编码。函数：`tag_label` / `etag` / `etag_all` / `batch_remove_tags`。
16 种内置标签：time / uid / group_id / name / channel / platform / media_file / reply_to 等。

#### entities/_sdk.py

工具注册 SDK + LLM 桥接层。entities 层通过此模块访问 LLM 能力，不直接依赖 agent：

```python
from entities._sdk import tool, entity                     # 工具注册
from entities._sdk import get_llm_manager                   # LLM 访问
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
  2. 遍历 task_schedules，递增 beat_count
  3. 选取一个到期任务 → TaskExecutor.run() → 结果记入心跳日志
  4. 持久化计数器到 config/heartbeat.json
```

三种触发模式：heartbeat（每 N 次心跳）/ scheduled（每天指定时间）/ manual（仅手动）

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
2. context 层（便签）—— 低频重建
3. volatile 层（短期记忆 + 溢出提示 + 安全标记 + 语义召回 + 技能注入）—— 每轮构建
4. 对话历史（实时从 DB 获取，禁止缓存）
```

#### 思维循环防护（think_loop）

| 机制 | 文件 | 说明 |
|------|------|------|
| 工具守卫 | `agent/mind/guardrails.py` | 精确失败重复/同工具连续失败/无进展循环检测，动作 warn/block/halt |
| 错误分类 | `agent/llm/error_classifier.py` | LLM 错误分类（rate_limit/context_overflow/auth 等）驱动重试策略 |
| 自适应重试 | `agent/llm/retry.py` | 指数退避 + 抖动（jittered_backoff） |
| 上下文压缩 | `agent/mind/context_compressor.py` | 溢出检测（真实 usage 优先）→ 保头保尾 + LLM 摘要 → 压缩反馈注入 |
| 结果预算 | `agent/mind/result_budget.py` | 按模型窗口动态截断工具结果（15% 单条 / 30% 整轮） |
| 会话令牌 | `agent/security/session_token.py` | 一次性令牌标记可信历史，泄露即 SECURITY 停止 |
| 威胁扫描 | `agent/security/threat_scanner.py` | 注入模式扫描（工具结果标记 / 记忆写入拦截） |
| 结果脱敏 | `core/sanitizer.py` | API Key/Token/密码自动遮盖（工具结果 + 日志） |

### 前端结构

页面采用壳组件 + 子面板目录拆分模式，通用 TabBar 切换：

```
pages/
├── Chat.tsx             # 对话工作台（首页，三栏：文件树/对话流/功能 Dock）→ chat/
├── Dashboard.tsx        # 总览 → dashboard/
├── Memory.tsx           # 记忆 → memory/
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
| `agent/mind/prefrontal_cortex.py` | 工作记忆、工具召回、上下文组装（分层缓存） |
| `agent/mind/autonomous.py` | 决策类型、态势模型、元决策 prompt |
| `agent/mind/prompt_layers.py` | Prompt 分层缓存（stable/context/volatile + PromptCacheManager） |
| `agent/mind/guardrails.py` | 工具调用守卫（死循环检测 warn/block/halt） |
| `agent/mind/context_compressor.py` | 上下文压缩（溢出检测 + 保头保尾 + LLM 摘要） |
| `agent/mind/result_budget.py` | 工具结果预算截断（按模型窗口动态计算） |
| `agent/mind/tool_activation.py` | 工具沉睡/激活状态机（activate_tool_group） |
| `agent/mind/tools/think_loop.py` | 统一思维循环（多轮 LLM + 工具编排） |
| `agent/llm/error_classifier.py` | LLM 错误分类（驱动重试/压缩/回退策略） |
| `agent/llm/retry.py` | 自适应退避（指数 + 抖动） |
| `agent/security/session_token.py` | 一次性会话令牌（防注入伪造历史） |
| `agent/security/threat_scanner.py` | 威胁模式扫描（prompt 注入检测） |
| `core/sanitizer.py` | 敏感信息脱敏（API Key/Token/密码） |
| `core/tool_gate.py` | 工具门控（check_fn TTL 缓存 + 瞬态宽限） |
| `agent/skills/skill_store.py` | 技能存储（workspace/skills/SKILL.md） |
| `agent/skills/skill_matcher.py` | 技能匹配（关键词 + 语义混合评分） |
| `agent/skills/background_review.py` | 技能后台评审（对话后自动沉淀经验） |
| `agent/skills/curator.py` | 技能策展（自动降级/归档，挂心跳维护） |
| `agent/delegation/sub_agent.py` | 子代理（leaf/orchestrator 角色 + 深度限制） |
| `agent/delegation/delegation_manager.py` | 委托调度（并发上限/预算/聚合/后台模式） |
| `agent/delegation/delegate_tool.py` | delegate_task 工具 |
| `agent/mind/tools/multi_tool.py` | 多工具并行编排（multi_tool_invoke） |
| `agent/mind/tools/decision_executor.py` | 决策执行分发（REPLY/REFLECT/PLAN 等） |
| `agent/mind/tools/media_pipeline.py` | 媒体标签转换 |
| `agent/memory/memory_store.py` | 长期记忆存储（SQLite + FTS5 + Embedding） |
| `agent/memory/tools.py` | 记忆工具（memorize/recall/forget） |
| `agent/memory/notes.py` | 便签文件系统 |
| `agent/task/model.py` | 任务数据模型（TaskDefinition / TaskResult） |
| `agent/task/registry.py` | 任务注册表（config/tasks/*.json 加载/CRUD） |
| `agent/task/executor.py` | 任务执行器（LLM 调用 + 结果存储） |
| `agent/heartbeat/engine.py` | 心跳调度引擎（tick 循环 + 内置维护） |
| `agent/heartbeat/config.py` | 心跳配置（HeartbeatConfig + TaskSchedule） |
| `agent/heartbeat/log.py` | 心跳日志读写 |
| `agent/planning/tools.py` | 规划工具（create_goal/update_goal/delete_goal） |
| `agent/runtime/bootstrap.py` | 启动流程（初始化 → 组装 → 启动 → 健康检查） |
| `entities/_sdk.py` | 工具注册 + LLM 桥接 |
| `agent/channel/manager.py` | 频道管理（register / route） |
| `agent/channel/tool_bridge.py` | 频道工具桥接（@channel_tool 扫描注册 / 通用能力路由 / 敏感门控 / 按频道接口开关 channel_tool_states） |
| `agent/channel/context.py` | 当前会话频道 ContextVar（通用工具默认路由目标） |
| `web/routers/config.py` | 心跳/任务 API + Mind 配置 API |
| `web/routers/workspace.py` | 工作区文件 API（目录树 / 读写 / 搜索，沙箱复用 entities.filesystem） |
| `web/routers/search.py` | 全局搜索聚合 API（记忆 / 日志 / 文件 / 会话） |
| `entities/ui/tools.py` | 界面交互工具组（ui_notify / ui_ask / ui_open_panel / ui_compose / ui_get_state） |
| `web/frontend/src/pages/chat/` | 对话工作凳子面板（Dock / StatusBar / FileEditor / UiCommandHost / render） |
| `web/frontend/src/stores/chat-store.ts` | 对话状态 + 聊天 SSE（含 ui_command 分发） |
| `web/frontend/src/stores/workbench-store.ts` | 工作台状态（Dock / 编辑器 / UI 命令收件箱 / 状态上报） |
| `core/path.py` | PathManager + ConfigPaths 路径常量 |
| `core/lifecycle.py` | 单例生命周期注册表（register / shutdown_all / reset） |

### 工具分组体系

#### group key 规范

工具分组 key 是全局标识符，**必须使用英文**，前端通过 i18n 翻译展示中文/英文名称。

- 后端注册：`group="thinking"` / `entity("web", "...")`
- 前端翻译：`i18n/locales/{zh,en}/tools.json` → `groups.thinking` → "思维工具"
- 前端使用：`t(`groups.${g.group}`, { defaultValue: g.group })`

修改分组名时必须同步更新：
1. 后端 `@tool(group=...)` / `@deferred_tool(group=...)` / `entity(group, ...)` / `activate_group(group, ...)`
2. 前端 `i18n/locales/zh/tools.json` 和 `en/tools.json` 的 `groups` 对象
3. `core/entity.py` 的 `_CATALOG_ORDER`（LLM 工具目录排序）
4. `services/tool.py` 的 `_GROUP_ORDER`（WebUI 工具列表排序）

#### 当前分组索引

| group key | 中文名 | 注册文件 | tags |
|---|---|---|---|
| `output` | 消息输出 | `channel/output_tools.py` | always |
| `memory` | 记忆管理 | `agent/memory/tools.py` | always/core/heartbeat |
| `notes` | 便签记忆 | `agent/memory/notes.py` | core/heartbeat |
| `thinking` | 思维工具 | `agent/mind/mind.py` + `agent/mind/tools/multi_tool.py` + `agent/mind/tool_activation.py` + `agent/mind/context_compressor.py` | always |
| `planning` | 目标规划 | `agent/planning/tools.py` | planning/goal/heartbeat |
| `skills` | 技能 | `agent/skills/tools.py` | always |
| `delegation` | 子代理 | `agent/delegation/delegate_tool.py` | always |
| `ui` | 界面交互 | `entities/ui/tools.py`（经 event_bus `EVENT_UI_COMMAND` → 聊天 SSE 桥接） | always |
| `web` | 网络工具 | `entities/web/tools.py` | web/search/fetch |
| `media` | 多媒体 | `entities/media/tools.py` | media:* |
| `os` | 操作系统 | `entities/filesystem/tools.py` | media:file |
| `environment` | 环境信息 | `entities/system/tools.py` | — |
| `model_control` | 模型控制 | `entities/model_control/tools.py` | core |
| `ollama` | Ollama | `entities/model_control/tools.py` | — |
| `logs` | 日志查询 | `entities/logs/tools.py` | — |
| `channel_ops` | 频道操作 | `agent/channel/tool_bridge.py`（@channel_tool 动态） | capability/channel_id |
| `entity` | 实体管理 | `entities/entity_query/tools.py` | always/core |
| `mcp_manage` | MCP 管理 | `entities/mcp/bridge.py`（动态） | — |
| `mcp:*` | MCP 服务 | 动态注册 | — |

### 开发约定

**import**：`from core.log import log` / `from core.entity import EntityRegistry` / `from core.path import ConfigPaths` / `from agent.memory.memory_store import MemoryStore` / `from agent.heartbeat.engine import HeartbeatEngine` / `from agent.task.registry import TaskRegistry`

**日志**：`log("内容")` / `log("调试", "DEBUG")` / `log(f"错误: {exc}", "ERROR")`

**异常处理**：关键路径（数据库连接、关闭）保持 `except Exception`；工具函数返回 JSON error；其他 `pass` 场景补充 DEBUG 日志

**工具开发**：返回 `str`（JSON）、完整类型注解、Google docstring、内部捕获异常

**频道开发**：继承 BaseChannel、6 个必需接口（channel_id / display_name / capabilities / start / stop / send_text）

**前端**：页面超过 300 行拆为子面板目录、统一用 TabBar、i18n 覆盖所有文本、`Record<string, unknown>` 替换为 `lib/types.ts` 接口

**生命周期**：bootstrap 中创建的有状态单例须调用 `Lifecycle.register(name, instance, cleanup=close_fn)` 注册；关闭时 `Lifecycle.shutdown_all()` 逆序清理

**包管理**：项目依赖由 uv 管理（`pyproject.toml` + `uv.lock`），安装依赖用 `uv add`，临时操作用 `uv pip install`；禁止对 `.venv` 使用 `pip install` / `ensurepip`（uv 创建的 venv 默认不含 pip，属正常状态而非故障，不要"修复"它）

**禁止**：直接 import openai/anthropic SDK（用 litellm）/ entities 直接 import agent（用 _sdk 桥接）

---

## 三、与原 Cursor 规则的差异说明

- 原 `.cursor/rules/*.mdc` 文件使用 Cursor 专属 frontmatter（`globs`、`alwaysApply`），这些字段在 ZCode 中无对应。
- 本 AGENTS.md 默认就是「全量注入」（等价于 `alwaysApply: true` 的语义），不存在按文件路径触发的「globs」机制。
- 若后续需要按需触发的细分规则（例如「只在改前端时调用」），可拆到 `.zcode/skills/<name>/SKILL.md`，通过 frontmatter `description` 中的关键词由模型自由触发。
- `.cursor/rules/*.mdc` 原文件保留，便于 Cursor 客户端继续使用，**两者不冲突**。

---

## 四、项目级 skill 扩展点

未来如需新增项目级 skill，放置位置（按优先级从高到低）：

1. `<repo>/.zcode/skills/<name>/SKILL.md`
2. `<repo>/.agents/skills/<name>/SKILL.md`

仅放置规则「按文件路径条件触发」无法实现——ZCode 没有该机制，需要走 AGENTS.md（全量注入）或 SKILL.md（按 description 关键词触发）两条路径。