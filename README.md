# AnelfAgent

**v0.3** · 统一智能体框架 — 自主思考 · 语义记忆 · 工具编排 · 多模态生成 · 多通道通信

**简体中文** | [English](README_EN.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%20%7C%203.12-blue.svg)](https://www.python.org/)
[![uv](https://img.shields.io/badge/package%20manager-uv-DE5FE9.svg)](https://github.com/astral-sh/uv)

AnelfAgent 是面向个人与团队的开源 AI 智能体运行时：内置自主决策引擎、混合语义记忆、技能自学习、子代理调度、MCP 工具桥接与多平台通道适配，覆盖文本、图像、语音、视频、音乐多模态生成，并提供现代化 WebUI 完成配置、对话与运维全生命周期管理。

> 本仓库为 **0.3 稳定基线**：架构与能力已趋于定型，适合自托管部署与二次扩展。

---

## 为什么选 AnelfAgent

| 能力 | 说明 |
|---|---|
| **实体驱动** | 工具 / 模型 / 频道 / MCP / 存储统一注册到 `EntityRegistry`，两级能力发现 |
| **标签路由** | `[key:value]` 贯穿消息元信息与工具注入，AI 始终拿到「刚好够用」的工具集 |
| **双层思维** | 元决策选行动类型 → `think_loop` 多轮工具编排执行 |
| **稳定防护** | 工具守卫 / 错误分类重试 / 候选链回退 / 上下文压缩 / 结果预算 / 会话令牌 / 威胁扫描 — 程序级兜底 |
| **记忆增强** | SQLite + FTS5 + Embedding 混合召回，可选 Cognee 知识图谱联邦 |
| **多模态生成** | 图像 / 语音 / 视频 / 音乐统一适配器（OpenAI · MiniMax · 硅基流动 · DashScope 等） |
| **持续进化** | 技能自学习闭环 + 心跳任务调度 + 目标规划 |
| **安全可控** | 统一权限引擎（allow / ask / deny）+ 频道化批准 + WebUI 认证 + 敏感信息脱敏 |
| **自运维** | SSH 远程管理 / 记忆备份 / 项目更新 / 文件分享 — AI 可自主管理自身部署 |
| **多端接入** | QQ / 飞书 / 微信 / Telegram / Bilibili / Acfun / WebUI / HTTP / CLI + OpenAI 兼容 Responses API |

---

## 核心能力

### 实体注册与工具门控

所有能力以实体注册，经分组与标签组织；AI 可按目录 → 分组两级发现可用工具。

```python
from entities._sdk import tool, entity

entity("weather", "天气查询 — 获取实时天气信息")

@tool(name="get_weather", group="weather", tags=["web"])
async def get_weather(city: str) -> str:
    """查询指定城市的实时天气。

    Args:
        city: 城市名称
    """
    return json.dumps({"city": city, "weather": "晴", "temp": 25})
```

**PFC 多路合并**后经两道门控过滤，schema 保持精简：

| 来源 | 说明 |
|---|---|
| `always` | 永驻工具（`end_reply` / `send_message` 等） |
| `mcp:*` | MCP 服务工具 |
| `channel` | 当前频道能力匹配 |
| `tag_match` | 消息标签激活（如 `media:image`） |
| `hot_recall` | 热门工具 Top-N |
| `discovered` / `activated` | 动态发现与沉睡组唤醒 |

- **check_fn 门控**：环境前置检查（TTL 缓存 + 瞬态故障宽限），不满足则不进 schema
- **沉睡 / 激活**：`allow_sleep` 工具默认只展示简介，AI 调用 `activate_tool_group` 按需唤醒

### 自主思维（Mind）

```
消息入队 → PFC 收集态势 → 元决策 → 执行决策
  → 记忆召回 / 技能注入 → think_loop（多轮 LLM + 工具）
  → end_reply → 完成
```

| 决策类型 | 用途 |
|---|---|
| `REPLY` | 回复消息 |
| `REFLECT` | 心跳 / 反思任务 |
| `REMEMBER` | 主动记忆 |
| `PROACTIVE` | 主动触达 |
| `TOOL_ACTION` | 自主工具操作 |
| `PLAN` | 目标规划 |

系统提示按变更频率分层，命中 Anthropic / OpenAI **前缀缓存**：

```
stable（人设 + 工具提示，对话内冻结）
  → context（便签，低频）
  → volatile（召回 + 技能 + 安全标记，每轮）
```

### 稳定与安全

| 机制 | 作用 |
|---|---|
| 工具守卫 | 精确失败重复 / 连续失败 / 无进展循环 → warn / block / halt |
| 错误分类 + 自适应重试 | 限流、超时、上下文溢出等分类驱动退避与模型回退 |
| 候选链回退（resilience） | `chat_with_fallback` 在候选模型链间推进；上下文超限快速失败，窗口更大的候选直接尝试，避免重复压缩 |
| 模型能力探测 | 实测 tools / vision 支持度，自动修正模型能力画像 |
| 上下文压缩 | 溢出检测 → 保头保尾 + LLM 摘要，长对话可持续 |
| 结果预算 | 按模型窗口动态截断工具结果（单条 / 整轮比例限制） |
| 会话令牌 | 一次性令牌标记可信历史，防注入伪造 |
| 威胁扫描 + 脱敏 | 工具结果 / 记忆写入拦截；API Key、Token、密码自动遮盖 |
| **统一权限引擎** | `工具名(参数glob)` + allow / ask / deny，支持全局与频道 scope；高风险操作经频道或 WebUI 人工批准 |

### 模型管理（LLMManager）

- **两级结构**：Provider（提供商）→ Model（模型），按能力类型（chat / tools / vision / embedding …）组织
- **启用开关**：禁用模型自动从选择 / 回退 / 执行路径中剔除，状态持久化
- **子代理档案**（`sub_agents`）：名称 → 有序模型候选池 + 执行面（专职守则 / 工具选择器 / 结构化产出契约），内置难度档 1–3 为纯模型池语法糖，全不可用时自动降挡
- **思考契约配置驱动**：每个模型在配置中声明思考参数映射（`thinking` 字段），代码对模型名零特判
- **代理支持**：`HTTP(S)_PROXY` 环境变量租约 + 可深拷贝代理客户端
- **协议适配**：Chat Completions 与 Responses 双协议（`agent/llm/responses`），统一经 litellm 转发

### 多模态生成

统一适配器屏蔽各平台差异，AI 通过 `media` / `minimax` 工具直接产出多媒体内容：

| 模态 | 适配器 | 平台 |
|---|---|---|
| 图像 | `ImageGenAdapter` | OpenAI · DashScope · 硅基流动 · MiniMax |
| 语音 | `SpeechAdapter` | OpenAI · MiniMax（含音色克隆） |
| 视频 | `VideoGenAdapter` | OpenAI · MiniMax（V1 / V2，异步任务轮询） |
| 音乐 | `MusicAdapter` | MiniMax |

配套的 **表情包实体**（`sticker`）支持收藏 / 检索 / 发送表情包、文搜图、图搜图，检索候选以多模态约定注入上下文，让视觉模型「亲眼看到」再选用。

### 混合语义记忆

Embedding + FTS5 + 标签匹配 + 时间衰减的混合评分；记忆类型覆盖实体画像、知识、事件、永久记忆，并支持 Markdown 便签。存储层拆分为 `memory/store/`（连接 / 检索 / 文件索引 / 队列）与上层领域逻辑解耦。

- **记忆铁律文档**：写入路由 / 标签纪律 / 查询路由收敛为单一系统级提示词文档（`config/memory_rules.md`），Web 记忆页可视化编辑
- **主标签记忆**（`main:hub`）：每回复周期置顶注入的永久记忆，AI 整段维护、心跳自愈
- **遗忘治理**：重要性松弛 + 检索练习效应 + 归档 / 墓碑兜底召回（`restore_memory` 可恢复）
- **图谱治理**：边强度衰减 / 弱边遗忘 + AI 策展议程（事实归系统、决策归 AI）

可选启用 **Cognee** 知识图谱投影与联邦召回（`config/cognee.json` / WebUI 记忆配置），与 SQLite 权威存储并存，失败自动降级。

### 技能自学习与子代理

- **技能闭环**：对话后经 LLM 钩子面后台评审 → `workspace/skills/SKILL.md` 沉淀 → 语义匹配注入 → 心跳策展（降级 / 归档）
- **子代理**：`delegate_task` 支持并行 fan-out、后台模式与独立迭代预算，按档案选模；`follow_up_agent` 以完整 transcript 无损续跑；进度流 / 用量归集 / Web 面板全链路可观测

### LLM 钩子面

「在 LLM 思考边界派生带上下文的异步 LLM 工作」的统一注册原语，与心跳 / 任务系统平行：同一事件（`after_reply` / `delegation_resolved` / `llm_end` …）可挂多个钩子并行拉起，各自独立治理（并发 / 冷却 / 防抖）。技能后台评审、任务事件触发、实体钩子均经此落地。

### 心跳与任务

任务内容（`config/tasks/*.json`）与调度（`config/heartbeat.json`）分离：

| 触发模式 | 说明 |
|---|---|
| `heartbeat` | 每 N 次心跳执行 |
| `scheduled` | 每天指定时间（多槽位独立去重） |
| `idle` | 连续 N 拍无思考活动后触发（全局唯一一条） |
| `manual` | 仅手动 / AI 主动触发 |
| `trigger_event` | 事件触发（经 LLM 钩子面，与调度正交） |

每次心跳还会跑内置维护：实体画像、记忆健康检查、技能策展、日志合并、空闲会话折叠等。

### 插件与热插拔

- **插件系统**（`entities/plugins`）：插件 = 清单 + skills/ + .mcp.json + tools.py 的目录包，支持市场订阅（本地目录或 git 仓库），AI 可自主安装 / 升级 / 移除
- **模块热插拔**：实体 / 频道目录增删经目录监听自动对账（新增即时注册、删除完整拆除），Web 端亦可手动热同步

### 自运维与外部数据

| 实体 / 服务 | 能力 |
|---|---|
| **SSH 远程管理** | 连接管理 / 命令执行 / 文件传输，远程目录持久跟踪，支持默认连接与 WebUI 面板 |
| **DevOps** | 记忆同步到私有 GitHub 仓库 / 拉取项目更新 / 重启应用（支持重启交接留言） |
| **文件分享** | 为工作区文件生成对外可下载链接并管理其生命周期 |
| **外部 SQL 数据源** | PostgreSQL / MySQL 只读连接注册表（`config/db_connections.json`），供 WebUI 数据管理页浏览与查询 |
| **存储卷管理** | 全部持久化数据登记为存储卷（8 卷），支持在线热备份 / 恢复 / 迁移 / SQL 导出导入，Web 数据管理页可视化操作 |
| **数据目录迁移** | 在线热备份拷贝 + 校验 + `data_root` 切换 |

### 多通道适配

目录自动发现，新增频道只需 `channels/{name}/adapter.py` + `channel_config.json`：

| 平台 | 要点 |
|---|---|
| **QQ** | OneBot v11 + NapCat（直连） |
| **飞书** | WebSocket 事件驱动 |
| **微信** | iLink Bot API，扫码登录，无需公网 webhook（详见 [`channels/weixin/README.md`](channels/weixin/README.md)） |
| **Telegram** | Bot API 长轮询 |
| **Bilibili / Acfun** | 弹幕与私信接入 |
| **WebUI** | SSE 推送；三栏对话工作台（文件树 / 对话流 / Dock） |
| **HTTP API** | 同步请求-响应 |
| **CLI** | 终端调试 |
| **Responses API** | OpenAI 兼容网关（`/v1/responses`），可把 AnelfAgent 当作模型服务对外提供 |

WebUI 对话工作台支持 AI **反向驱动界面**（`ui_notify` / `ui_ask` / `ui_open_panel` 等 → SSE `ui_command`）。

### MCP 桥接

支持 stdio / SSE / Streamable HTTP；后台异步连接，工具自动注册为实体，可热重载；工具列表变更通知热同步，图像结果落盘注入视觉模型。

---

## 技术栈

| 分类 | 技术 |
|---|---|
| 运行时 | Python 3.11–3.12 · [uv](https://github.com/astral-sh/uv) · FastAPI · Uvicorn · Pydantic v2 |
| LLM | litellm（统一 100+ 提供商）· Chat Completions / Responses 双协议 |
| 存储 | aiosqlite（WAL）· FTS5 · Embedding（sqlite-vec）· 可选 Cognee |
| 外部数据 | asyncpg（PostgreSQL）· aiomysql（MySQL）· asyncssh（SSH） |
| 文档解析 | pypdf · python-docx · tiktoken |
| 协议 | MCP SDK |
| 前端 | React 18 · TypeScript · Vite 6 · Tailwind CSS 4 · Zustand · TanStack Query |
| i18n | react-i18next（中 / 英） |

---

## 快速开始

### 环境要求

- Python **3.11 ~ 3.12**
- Node.js **18+**（构建前端）
- [uv](https://github.com/astral-sh/uv)（推荐）

### 安装与启动

```bash
git clone https://github.com/1292917512/AnelfAgent.git
cd AnelfAgent

# 从模板创建配置并填入 API Key
cp config/llm_clients.example.json config/llm_clients.json
cp config/app_config.example.json config/app_config.json
cp config/mcp_servers.example.json config/mcp_servers.json

# 安装依赖
uv sync

# 构建前端（可选；不构建亦可只跑 API）
cd web/frontend && npm install && npm run build && cd ../..

# 启动
./start.sh                 # macOS / Linux
start.bat                  # Windows
uv run python launch.py    # 直接运行
uv run python launch.py --no-webui   # 仅 Agent，不启动 WebUI
```

启动后打开：**http://127.0.0.1:8092/webui/**

### 接入频道（示例）

```bash
# 微信：WebUI → 通道管理 → 扫码登录（推荐）
# 或：uv run python scripts/weixin_setup.py
```

环境变量可用 `ANELF_<KEY>` 覆盖对应配置项；密钥可用 `${ENV_VAR}` 引用语法外置。

---

## 架构

```
┌─────────────┐     ┌──────────────┐     ┌──────────┐     ┌─────────────┐     ┌────────────┐
│  Frontend   │────▶│  Web API     │────▶│ Services │────▶│   Agent     │────▶│   core/    │
│  (React)    │     │  (FastAPI)   │     │          │     │ Mind / LLM  │     │ Registry   │
└─────────────┘     └──────────────┘     └──────────┘     └──────┬──────┘     └────────────┘
                                                                 │
                                              ┌──────────────────┼──────────────────┐
                                              ▼                  ▼                  ▼
                                        ┌──────────┐     ┌────────────┐     ┌────────────┐
                                        │ Channels │     │  Entities  │     │    MCP     │
                                        │  适配器   │     │   工具     │     │   桥接     │
                                        └──────────┘     └────────────┘     └────────────┘
```

**依赖方向（严格单向）：**

```
web/frontend → web/routers → services → agent → core/
entities → entities._sdk → core.entity
channels/ → agent.channel → core.entity

agent.mind → agent.memory / heartbeat / task / planning
agent.heartbeat → agent.task + memory + mind（调度执行）
agent.planning → agent.memory

禁止: agent → web | core → agent | services → web | entities → agent（经 _sdk 桥接）
```

以上方向由 import-linter 机械守卫（`uv run lint-imports`，CI 红绿门禁）。

### 目录职责

| 目录 | 职责 |
|---|---|
| `core/` | EntityRegistry / 配置（`ConfigPaths` 动态路径）/ 生命周期 / 标签 / 事件 / 门控 / 脱敏 / 日志 / 存储卷注册表 |
| `agent/mind/` | 思维循环 / PFC / Prompt 分层 / 守卫 / 压缩 / 思维会话 |
| `agent/llm/` | LLM 客户端与管理器 / 错误分类重试 / 弹性回退 / 能力探测 / 多模态适配器 / Responses 协议 |
| `agent/memory/` | 混合语义记忆（`store/` 存储层）+ 便签 + 可选 Cognee |
| `agent/skills/` | 技能存储 / 匹配 / 后台评审 / 策展 |
| `agent/delegation/` | 子代理调度（档案 / 并行 fan-out / 续跑 / 运行日志） |
| `agent/hooks_llm/` | LLM 钩子面（事件驱动的异步 LLM 工作注册原语；评审/任务事件/实体钩子并行拉起） |
| `agent/approval/` | 统一权限与批准门 |
| `agent/security/` | 会话令牌 / 威胁扫描 |
| `agent/heartbeat/` · `task/` · `planning/` | 心跳调度 / 任务定义 / 目标规划 |
| `agent/messages/` | 会话 scope 构建与解析 / 人设预设 |
| `agent/channel/` · `runtime/` · `storage/` | 频道管理 / 启动装配 / 存储路由与迁移 |
| `channels/` | 频道适配器（目录自动发现 + 热插拔） |
| `entities/` | 工具实体（目录自动发现 + 热插拔，经 `_sdk` 注册） |
| `services/` | 业务封装，供 Web API 调用 |
| `web/` | FastAPI 路由 + React 前端 |
| `config/` | JSON 配置 · SQLite 数据 · 人设 · 任务定义 |
| `tests/` | pytest 分层套件（`unit/` 单元 + `integration/` 集成；实体/频道单测在各模块内 `<模块>/tests/`，随模块拔出） |

### 内置实体（entities/）

| 实体 | 说明 |
|---|---|
| `filesystem` | 文件读写 / 目录树 / 搜索（沙箱） |
| `web` | 搜索 / 抓取 / 网页内容提取 |
| `media` | 图像识别 / 语音转写与合成等多媒体处理 |
| `minimax` | MiniMax 语音 / 图片 / 音色克隆 |
| `sticker` | 表情包收藏 / 检索 / 发送，文搜图 / 图搜图 |
| `ui` | 界面交互（`ui_notify` / `ui_ask` / `ui_open_panel` 等） |
| `ssh` | SSH 连接管理 / 命令执行 / 文件传输 |
| `devops` | 记忆备份 / 项目更新 / 应用重启 |
| `share` | 文件分享链接管理 |
| `vault` | 密码本 — 加密凭据库 / TOTP 验证器 / 模糊检索 / 泄露体检 |
| `voiceprint` | 音源库 — 说话人声纹识别与档案管理、语音转写语义检索 |
| `ai_desktop` | AI 桌面 — 时间 / 节日 / 天气 / 日程 / 订阅额度等环境信息可插拔注入 |
| `dify` | 连接既有 Dify 实例：应用与工作流 DSL 管理、运行调用、MCP 桥接 |
| `sillytavern` | 本机 SillyTavern 纳管：进程生命周期 / git 更新 / 角色卡管理 |
| `plugins` | 插件安装 / 升级 / 移除与市场订阅 |
| `mcp` | MCP 服务桥接（动态注册） |
| `entity_query` | 实体目录两级发现 |
| `model_control` | 模型切换 / 参数调整 / Ollama 管理 |
| `system` | 系统信息 / Python 环境 / Git / 日志查询 |

### 项目结构（摘要）

```
AnelfAgent/
├── launch.py                 # 启动入口
├── core/                     # 基础框架（零业务依赖）
├── agent/
│   ├── mind/                 # 思维循环 / PFC / Prompt 分层 / 守卫 / 压缩 / 思维会话
│   ├── llm/                  # LLM 管理 / 弹性回退 / 探测 / 多模态适配器 / Responses
│   ├── memory/               # 混合语义记忆（store/）+ 便签 + Cognee
│   ├── skills/ · delegation/ · hooks_llm/ · approval/ · security/
│   ├── heartbeat/ · task/ · planning/ · messages/
│   ├── channel/ · runtime/ · storage/
├── channels/                 # qq / feishu / weixin / telegram / bilibili / acfun / webui / http_api / cli
├── entities/                 # filesystem / web / media / vault / voiceprint / ai_desktop / dify / plugins / mcp / ...
├── services/ · web/ · config/ · scripts/ · tests/
└── workspace/                # 运行时工作区（上传 / 技能等，本地生成）
```

---

## 开发指南

### 添加工具

在 `entities/` 下新建目录并实现 `tools.py`，框架自动发现：

```python
# entities/weather/tools.py
from entities._sdk import tool, entity
import json

entity("weather", "天气查询 — 获取实时天气信息")

@tool(
    name="get_weather",
    group="weather",
    tags=["web"],
    # 可选：门控与沉睡
    # check_fn=lambda: True,
    # allow_sleep=True, sleep_brief="天气查询",
)
async def get_weather(city: str) -> str:
    """查询指定城市的实时天气。

    Args:
        city: 城市名称
    """
    return json.dumps({"city": city, "weather": "晴", "temp": 25})
```

约定：返回 `str`（JSON）、完整类型注解 + Google docstring、内部捕获异常，错误统一走 `core.tool_errors`（entities 经 `_sdk` 导入）。

新增 **group key** 时须同步：后端注册、`i18n/locales/{zh,en}/tools.json`、以及 `core/entity.py` 中的分组排序（实体可经 `entity_manifest(order=)` 自声明覆盖）。

### 添加心跳任务

在 `config/tasks/` 创建任务 JSON，再于 WebUI 心跳页绑定调度规则；亦可配置 `trigger_event` 让任务由事件触发。

### 添加频道

在 `channels/{name}/` 提供：

- `adapter.py` — 继承 `BaseChannel`，实现 `channel_id` / `display_name` / `capabilities` / `start` / `stop` / `send_text`
- `config.py` — 暴露 `CONFIG_MODEL`（pydantic 模型即配置唯一声明源）
- `channel_config.json`（可用 `.example.json` 作模板）
- `__init__.py` — 导出 `CHANNEL_CLASS`

### 权限规则

新格式：`config/permission_rules.json`（优先）；旧 `approval_policies.json` 自动转换加载。规则支持热重载。高风险工具可设为 `ask`，由频道消息或 WebUI 批准页确认。

### 包与测试

```bash
uv sync                          # 安装依赖（含 Cognee 等）
uv run pytest                    # 全量测试（unit + 无需凭证的 integration）
uv run pytest tests/unit         # 分层单元测试（core/agent/services/web，快速）
uv run pytest entities/minimax/tests # 单模块测试（实体/频道单测在各模块内 tests/）
uv run pytest -m integration     # 仅集成测试（需凭证的用例自动跳过）
uv run ruff check .              # Lint
uv run lint-imports              # 依赖方向契约检查
uv run mypy core/                # 类型检查（core 严格层）
uv add <package>                 # 新增依赖（请勿对 uv venv 使用 pip install）

scripts/check.sh                 # 本地 CI 镜像门禁：与 CI 同口径一键验证（push 前跑）
scripts/check.sh --fast          # 仅静态门禁（ruff + lint-imports + mypy 三平台）
```

CI（GitHub Actions，`.github/workflows/ci.yml`）：push/PR 时先按改动路径归类——`lint` job 全仓静态门禁（ruff → import-linter → mypy core 三平台），`tests` job 按模块动态矩阵分腿（实体/频道改动只跑对应模块 `tests/` 套件，主干改动触发全量腿），`frontend` job 跑 `npm run lint` + `npm run build`；文档类提交全跳过。

更细的架构约定见仓库根目录 [`AGENTS.md`](AGENTS.md)（供编辑器 / Agent 注入的工作区指令，非运行时依赖）。

---

## 敏感信息管理

个人配置与框架代码通过 `.gitignore` 分离：API Key、Token、记忆库、心跳计数、频道密钥等不进仓库，仅保留 `*.example.json` 等模板。配置值支持 `${ENV_VAR}` 引用语法，把密钥外置到环境变量。

个人数据（API 配置 / 心跳与任务 / 记忆数据 / 频道密钥 / 人设等）的备份由用户自行负责（如 NAS 定期备份，或经存储卷面板导出）。

---

## 开源与致谢

本项目以 **[MIT License](LICENSE)** 发布，欢迎 Star、Issue 与 PR。

**仓库**：https://github.com/1292917512/AnelfAgent

AnelfAgent 的多平台能力建立在这些优秀开源项目之上：

| 项目 | 用途 | 协议 |
|---|---|---|
| [litellm](https://github.com/BerriAI/litellm) | 统一 LLM API | MIT |
| [NapCatQQ](https://github.com/NapNeko/NapCatQQ) | QQ OneBot v11 协议端 | 混合协议 |
| [lark-oapi](https://github.com/larksuite/oapi-sdk-python) | 飞书 / Lark SDK | MIT |
| [FastAPI](https://github.com/fastapi/fastapi) / [MCP](https://modelcontextprotocol.io/) | Web 与工具协议 | MIT |

特别感谢以下两个项目（相互独立、各有侧重）在架构与体验上的参考与启发：

- [Nekro Agent](https://github.com/KroMiose/nekro-agent)（开源智能体框架）：多平台智能体架构、多模态候选注入体验与实时语音链路（含麦克风上行音频帧协议）。
- [N.E.K.O / Project-N-E-K-O](https://github.com/Project-N-E-K-O/N.E.K.O)（桌面陪伴智能体）：实时语音内核质量方向——语义端点检测与输入音频预处理的思路、本地推理模型的管理形态，以及"内核能力 + 桌面陪伴"的产品形态探索。

> **协议说明**：AnelfAgent 通过 OneBot v11 WebSocket 与 NapCatQQ 通信，不包含也不修改 NapCat 源码。微信频道对接腾讯 iLink Bot API，协议实现参考社区适配器实践。

### 参与贡献

1. Fork 本仓库并创建特性分支
2. 保持依赖方向与类型注解约定（见 `AGENTS.md`）
3. 为行为变更补充或更新 `tests/`
4. 提交清晰的 PR 说明动机与验证方式

欢迎在 Issues 中反馈 Bug、讨论设计或提交功能提案。

---

## License

[MIT](LICENSE) © 2025–2026 AnelfAgent Contributors
