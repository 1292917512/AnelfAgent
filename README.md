# AnelfAgent

**v0.1** · 统一智能体框架 — 自主思考 · 语义记忆 · 工具编排 · 多通道通信

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%20%7C%203.11-blue.svg)](https://www.python.org/)
[![uv](https://img.shields.io/badge/package%20manager-uv-DE5FE9.svg)](https://github.com/astral-sh/uv)

AnelfAgent 是面向个人与团队的开源 AI 智能体运行时：内置自主决策引擎、混合语义记忆、技能自学习、子代理调度、MCP 工具桥接与多平台通道适配，并提供现代化 WebUI 完成配置、对话与运维全生命周期管理。

> 本仓库为 **0.1 稳定基线**：架构与能力已趋于定型，适合自托管部署与二次扩展。

---

## 为什么选 AnelfAgent

| 能力 | 说明 |
|---|---|
| **实体驱动** | 工具 / 模型 / 频道 / MCP / 存储统一注册到 `EntityRegistry`，两级能力发现 |
| **标签路由** | `[key:value]` 贯穿消息元信息与工具注入，AI 始终拿到「刚好够用」的工具集 |
| **双层思维** | 元决策选行动类型 → `think_loop` 多轮工具编排执行 |
| **稳定防护** | 工具守卫 / 错误分类重试 / 上下文压缩 / 结果预算 / 会话令牌 / 威胁扫描 — 程序级兜底 |
| **记忆增强** | SQLite + FTS5 + Embedding 混合召回，可选 Cognee 知识图谱联邦 |
| **持续进化** | 技能自学习闭环 + 心跳任务调度 + 目标规划 |
| **安全可控** | 统一权限引擎（allow / ask / deny）+ 频道化批准 + 敏感信息脱敏 |
| **多端接入** | Telegram / QQ / 飞书 / 微信 / WebUI / HTTP / CLI / NoneBot 桥接 |

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
| 上下文压缩 | 溢出检测 → 保头保尾 + LLM 摘要，长对话可持续 |
| 结果预算 | 按模型窗口动态截断工具结果（单条 / 整轮比例限制） |
| 会话令牌 | 一次性令牌标记可信历史，防注入伪造 |
| 威胁扫描 + 脱敏 | 工具结果 / 记忆写入拦截；API Key、Token、密码自动遮盖 |
| **统一权限引擎** | `工具名(参数glob)` + allow / ask / deny，支持全局与频道 scope；高风险操作经频道或 WebUI 人工批准 |

### 混合语义记忆

Embedding + FTS5 + 标签匹配 + 时间衰减的混合评分；记忆类型覆盖实体画像、知识、事件、永久记忆，并支持 Markdown 便签。

可选启用 **Cognee** 知识图谱投影与联邦召回（`config/cognee.json` / WebUI 记忆配置），与 SQLite 权威存储并存，失败自动降级。详见 [`COGNEE.md`](COGNEE.md)。

### 技能自学习与子代理

- **技能闭环**：对话后后台评审 → `workspace/skills/SKILL.md` 沉淀 → 语义匹配注入 → 心跳策展（降级 / 归档）
- **子代理**：`delegate_task` 支持 leaf / orchestrator 角色、并行 fan-out、后台模式与独立迭代预算

### 心跳与任务

任务内容（`config/tasks/*.json`）与调度（`config/heartbeat.json`）分离：

| 触发模式 | 说明 |
|---|---|
| `heartbeat` | 每 N 次心跳执行 |
| `scheduled` | 每天指定时间 |
| `manual` | 仅手动 / AI 主动触发 |

每次心跳还会跑内置维护：实体画像、记忆健康检查、技能策展、日志合并等。

### 多通道适配

目录自动发现，新增频道只需 `channels/{name}/adapter.py` + `channel_config.json`：

| 平台 | 要点 |
|---|---|
| **Telegram** | 文本 / 媒体 / 内联键盘 / 流式推送等 |
| **QQ** | NoneBot2 + OneBot v11 + NapCat |
| **飞书** | WebSocket 事件驱动 |
| **微信** | iLink Bot API，扫码登录，无需公网 webhook（详见 [`channels/weixin/README.md`](channels/weixin/README.md)） |
| **WebUI** | SSE 推送；三栏对话工作台（文件树 / 对话流 / Dock） |
| **HTTP API** | 同步请求-响应 |
| **CLI** | 终端调试 |
| **NoneBot 桥接** | 扩展更多平台 |

WebUI 对话工作台支持 AI **反向驱动界面**（`ui_notify` / `ui_ask` / `ui_open_panel` 等 → SSE `ui_command`）。

### MCP 桥接

支持 stdio / SSE / Streamable HTTP；后台异步连接，工具自动注册为实体，可热重载。

---

## 技术栈

| 分类 | 技术 |
|---|---|
| 运行时 | Python 3.10–3.11 · [uv](https://github.com/astral-sh/uv) · FastAPI · Uvicorn · Pydantic v2 |
| LLM | litellm（统一 100+ 提供商） |
| 存储 | aiosqlite（WAL）· FTS5 · Embedding · 可选 Cognee |
| 协议 | MCP SDK |
| 前端 | React 18 · TypeScript · Vite 6 · Tailwind CSS 4 · Zustand · TanStack Query |
| i18n | react-i18next（中 / 英） |

---

## 快速开始

### 环境要求

- Python **3.10 ~ 3.11**
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
# Telegram
cp channels/telegram/channel_config.example.json channels/telegram/channel_config.json
# 编辑填入 Bot Token，设置 enabled: true

# 微信：WebUI → 通道管理 → 扫码登录（推荐）
# 或：uv run python scripts/weixin_setup.py
```

环境变量可用 `ANELF_<KEY>` 覆盖对应配置项。

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

禁止: agent → web | core → agent | services → web | entities → agent（经 _sdk 桥接）
```

### 目录职责

| 目录 | 职责 |
|---|---|
| `core/` | EntityRegistry / 配置 / 生命周期 / 标签 / 事件 / 门控 / 脱敏 / 日志 |
| `agent/` | Mind · Memory · Skills · Delegation · Approval · Heartbeat · Task · Planning · LLM · Channel · Runtime |
| `channels/` | 频道适配器（目录自动发现） |
| `entities/` | 工具实体（目录自动发现，经 `_sdk` 注册） |
| `services/` | 业务封装，供 Web API 调用 |
| `web/` | FastAPI + React 前端 |
| `config/` | JSON 配置 · SQLite 数据 · 人设 · 任务定义 |
| `tests/` | pytest 用例 |

### 项目结构（摘要）

```
AnelfAgent/
├── launch.py                 # 启动入口
├── core/                     # 基础框架（零业务依赖）
├── agent/
│   ├── mind/                 # 思维循环 / PFC / Prompt 分层 / 守卫 / 压缩
│   ├── memory/               # 混合语义记忆 + 便签
│   ├── skills/               # 技能存储 / 匹配 / 评审 / 策展
│   ├── delegation/           # 子代理调度
│   ├── approval/             # 统一权限与批准门
│   ├── security/             # 会话令牌 / 威胁扫描
│   ├── heartbeat/ · task/ · planning/
│   ├── llm/ · channel/ · runtime/ · storage/
├── channels/                 # telegram / qq / feishu / weixin / webui / ...
├── entities/                 # filesystem / web / media / ui / mcp / ...
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

约定：返回 `str`（JSON）、完整类型注解 + Google docstring、内部捕获异常。

新增 **group key** 时须同步：后端注册、`i18n/locales/{zh,en}/tools.json`、以及 `core/entity.py` / `services/tool.py` 中的分组排序表。

### 添加心跳任务

在 `config/tasks/` 创建任务 JSON，再于 WebUI 心跳页绑定调度规则。

### 添加频道

在 `channels/{name}/` 提供：

- `adapter.py` — 继承 `BaseChannel`，实现 `channel_id` / `display_name` / `capabilities` / `start` / `stop` / `send_text`
- `channel_config.json`（可用 `.example.json` 作模板）
- `__init__.py` — 导出 `CHANNEL_CLASS`

### 权限规则

新格式：`config/permission_rules.json`（优先）；旧 `approval_policies.json` 可自动转换。规则支持热重载。高风险工具可设为 `ask`，由频道消息或 WebUI 批准页确认。

### 包与测试

```bash
uv sync                          # 安装依赖（含 Cognee 等）
uv run pytest                    # 单元 / 异步测试
uv add <package>                 # 新增依赖（请勿对 uv venv 使用 pip install）
```

更细的架构约定见仓库根目录 [`AGENTS.md`](AGENTS.md)（供编辑器 / Agent 注入的工作区指令，非运行时依赖）。

---

## 敏感信息管理

个人配置与框架代码通过 `.gitignore` 分离：API Key、Token、记忆库、心跳计数、频道密钥等不进仓库，仅保留 `*.example.json` 等模板。

可选将 `.secrets/` 作为独立私密仓库备份：

```bash
./scripts/secrets-backup.sh      # 同步并推送
./scripts/secrets-restore.sh     # 恢复
# Windows: scripts/secrets-backup.bat / secrets-restore.bat
```

备份范围：API 配置 / 心跳与任务 / 记忆数据 / 频道密钥 / 人设等。

---

## 开源与致谢

本项目以 **[MIT License](LICENSE)** 发布，欢迎 Star、Issue 与 PR。

**仓库**：https://github.com/1292917512/AnelfAgent

AnelfAgent 的多平台能力建立在这些优秀开源项目之上：

| 项目 | 用途 | 协议 |
|---|---|---|
| [litellm](https://github.com/BerriAI/litellm) | 统一 LLM API | MIT |
| [NoneBot2](https://github.com/nonebot/nonebot2) | 跨平台机器人框架 / 桥接 | MIT |
| [NapCatQQ](https://github.com/NapNeko/NapCatQQ) | QQ OneBot v11 协议端 | 混合协议 |
| [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) | Telegram Bot API | LGPL-3.0 |
| [lark-oapi](https://github.com/larksuite/oapi-sdk-python) | 飞书 / Lark SDK | MIT |
| [FastAPI](https://github.com/fastapi/fastapi) / [MCP](https://modelcontextprotocol.io/) | Web 与工具协议 | MIT |

特别感谢 [Nekro Agent](https://github.com/KroMiose/nekro-agent) 在多平台智能体架构上的参考与启发。

> **协议说明**：AnelfAgent 通过 OneBot v11 WebSocket 与 NapCatQQ 通信，不包含也不修改 NapCat 源码。NoneBot2 作为依赖引入，遵循其 MIT 协议。微信频道对接腾讯 iLink Bot API，协议实现参考社区适配器实践。

### 参与贡献

1. Fork 本仓库并创建特性分支
2. 保持依赖方向与类型注解约定（见 `AGENTS.md`）
3. 为行为变更补充或更新 `tests/`
4. 提交清晰的 PR 说明动机与验证方式

欢迎在 Issues 中反馈 Bug、讨论设计或提交功能提案。

---

## License

[MIT](LICENSE) © 2025–2026 AnelfAgent Contributors
