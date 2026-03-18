# AnelfAgent

**统一智能体框架** — 自主思考 · 语义记忆 · 工具编排 · 多通道通信

AnelfAgent 是一个开箱即用的 AI 智能体框架，内置自主决策引擎、混合语义记忆、MCP 工具桥接和多平台通道适配，配合现代化 WebUI 实现智能体的全生命周期管理。

---

## 核心特性

### 🏗️ 实体注册系统（EntityRegistry）

所有能力统一以「实体」注册到中央枢纽，通过分组和标签进行组织、发现和调用。AI 可通过**两级能力发现**自主探索可用工具：

```
Level 1: 查看实体目录 → 分组名 + 描述 + 工具数
Level 2: 展开具体分组 → 方法签名 + 参数 + 文档
```

| 实体类型 | 用途 |
|---|---|
| TOOL | 工具函数（文件/网络/媒体/系统等） |
| MODEL | LLM 客户端 |
| ADAPTER | 频道适配器 |
| MCP_SERVER | MCP 服务 |
| STORAGE | 数据存储 |
| SERVICE | 内部服务 |

实体通过 `@tool` 装饰器或 `entity()` 函数注册，目录自动发现，零配置接入：

```python
from entities._sdk import tool, entity

entity("weather", "天气查询 — 获取实时天气信息")

@tool(name="get_weather", group="weather")
async def get_weather(city: str) -> str:
    """查询指定城市的实时天气。

    Args:
        city: 城市名称
    """
    return json.dumps({"city": city, "weather": "晴", "temp": 25})
```

### 🏷️ 标签系统（Tag System）

`[key:value]` 格式的统一数据编码，贯穿消息、工具路由和上下文组装的全链路：

**消息上下文标签** — 注入对话元信息，LLM 可感知：

```
[time:2025年3月14日10时30分] [uid:12345] [name:Alice] [channel:telegram]
[media_file:image:/path/to/photo.jpg] [reply_to:msg_789]
```

**工具路由标签** — PFC（前额叶皮层）内部调度，自动匹配工具集：

| 标签 | 作用 |
|---|---|
| `always` | 永驻工具，始终加载 |
| `core` | 核心工具，高优先级召回 |
| `media:image` | 图片相关工具（收到图片自动激活） |
| `send_photo` | 频道图片发送能力 |
| `web` | 网络搜索与页面抓取 |
| `planning` | 目标规划与任务管理 |

标签驱动的工具注入使 AI 始终拥有恰当的能力集 — 不多不少。

### 🧠 自主思维系统（Mind）

两层决策架构：**元决策**（LLM 选行动类型）→ **执行决策**（多轮工具调用循环）。

```
消息到达 → PFC 收集态势 → 元决策 → 执行决策 → 工具调用循环 → 输出
                                ↕
                          MemoryStore（语义召回）
```

| 决策类型 | 说明 |
|---|---|
| REPLY | 回复消息 |
| REFLECT | 自我反思（定期内省） |
| REMEMBER | 主动记忆 |
| PROACTIVE | 主动发消息 |
| TOOL_ACTION | 自主工具操作 |
| PLAN | 目标规划 |

**PFC 七路工具合并** — 每次思考自动组装最优工具集：

| 来源 | 说明 |
|---|---|
| always | 永驻工具 |
| mcp:* | MCP 服务工具 |
| channel | 频道能力（如发图片/语音） |
| tag_match | 标签激活（如收到图片 → 图片工具） |
| hot_recall | 热门工具 Top-N |
| discovered | 动态发现 |
| task_tools | 任务专属工具 |

### 💾 混合语义记忆

Embedding 向量 + FTS5 全文检索 + 标签匹配 + 时间衰减的混合评分管线：

```
语义评分 (0.7)                衰减评分 (0.3)
├─ Vector 相似度 (0.6)        ├─ Recency 新鲜度 (0.5)
├─ FTS 全文匹配 (0.25)        ├─ Frequency 频率 (0.3)
└─ Tag 标签匹配 (0.15)        └─ Importance 重要度 (0.2)
```

记忆类型涵盖实体画像、知识、反思、心跳日志，支持 Markdown 便签文件系统。

### 🔌 多通道适配

目录自动发现，新增频道只需 `channels/{name}/adapter.py` + `channel_config.json`：

| 平台 | 能力 |
|---|---|
| **Telegram** | 文本/图片/视频/语音/文件/位置/编辑/删除/转发/置顶/内联键盘/流式推送 |
| **QQ** | NoneBot2 + OneBot v11 + NapCat，文本/图片/语音/文件/撤回/转发/群管理 |
| **飞书** | WebSocket 事件驱动，文本/图片/富文本 |
| **HTTP API** | 同步请求-响应 |
| **WebUI** | SSE 推送，多媒体完整支持 |
| **CLI** | 终端调试 |
| **NoneBot 桥接** | 通过 NoneBot 适配器扩展更多平台 |

### 🌐 MCP 桥接

原生支持 Model Context Protocol，stdio / SSE / Streamable HTTP 三种传输方式，后台异步连接，工具自动注册为实体。

---

## 技术栈

| 分类 | 技术 |
|---|---|
| 后端 | Python 3.10+ / uv / FastAPI / Uvicorn / Pydantic v2 |
| LLM | litellm（统一 100+ LLM API） |
| 存储 | aiosqlite（SQLite WAL） / Embedding 向量 / FTS5 全文检索 |
| MCP | MCP SDK（Model Context Protocol） |
| 前端 | React 18 + TypeScript + Vite 6 + Tailwind CSS 4 + Zustand + TanStack Query |
| 国际化 | react-i18next（中/英双语） |

---

## 快速开始

### 环境要求

- Python 3.10 ~ 3.11
- Node.js 18+（构建前端）
- [uv](https://github.com/astral-sh/uv)（推荐）或 pip

### 安装与启动

```bash
git clone https://github.com/1292917512/AnelfAgent.git
cd AnelfAgent

# 配置（从模板复制，填入你的 API Key）
cp config/llm_clients.example.json config/llm_clients.json
cp config/app_config.example.json config/app_config.json
cp config/mcp_servers.example.json config/mcp_servers.json

# 安装依赖
uv sync

# 构建前端（可选，不构建也能运行 API）
cd web/frontend && npm install && npm run build && cd ../..

# 启动
./start.sh              # macOS / Linux
start.bat               # Windows
python launch.py        # 直接运行
python launch.py --no-webui   # 仅 Agent，不启动 WebUI
```

启动后访问 `http://127.0.0.1:8092/webui/` 打开管理界面。

### 频道配置

需要接入 Telegram / QQ / 飞书等平台时，从模板创建配置：

```bash
cp channels/telegram/channel_config.example.json channels/telegram/channel_config.json
# 编辑填入 Bot Token，设置 enabled: true
```

---

## 项目结构

```
AnelfAgent/
├── launch.py                 # 启动入口
├── core/                     # 基础框架层（零业务依赖）
│   ├── entity.py             #   EntityRegistry 中央注册枢纽
│   ├── tags.py               #   标签系统 [key:value]
│   ├── config.py             #   ConfigManager
│   ├── path.py               #   PathManager + ConfigPaths
│   ├── event_bus.py          #   异步事件总线
│   ├── lifecycle.py          #   单例生命周期管理
│   ├── flow.py               #   FlowMachine 流程状态机
│   └── log.py                #   统一日志（loguru）
├── agent/core/               # 智能体内核
│   ├── mind/                 #   思维系统
│   │   ├── mind.py           #     自主循环 + 多轮推理 + 工具编排
│   │   ├── prefrontal_cortex.py  # 工作记忆 + 七路工具合并 + 上下文组装
│   │   ├── autonomous.py     #     元决策模型
│   │   ├── memory/           #     语义记忆（Embedding + FTS5 + 便签）
│   │   ├── planning/         #     目标规划
│   │   └── introspection/    #     内省系统（模块化反思单元）
│   ├── llm/                  #   LLM 统一接口（litellm）
│   ├── channel/              #   频道基础设施
│   ├── runtime/              #   运行时（Bootstrap + AgentApp）
│   ├── storage/              #   混合存储（SQLite + StorageRouter）
│   └── config.py             #   BotConfigProvider
├── channels/                 # 频道适配器（目录自动发现）
│   ├── telegram/             #   Telegram
│   ├── qq/                   #   QQ（OneBot v11）
│   ├── feishu/               #   飞书
│   ├── http_api/             #   HTTP 接口
│   ├── webui/                #   WebUI（SSE）
│   └── cli/                  #   命令行
├── entities/                 # 工具实体（目录自动发现）
│   ├── _sdk.py               #   工具注册 SDK + LLM 桥接
│   ├── filesystem/           #   文件操作
│   ├── web/                  #   网页搜索 / 内容提取
│   ├── media/                #   多模态（图片/语音/视频）
│   ├── mcp/                  #   MCP 桥接
│   └── system/               #   系统信息 / Shell / Python
├── services/                 # 服务层（封装业务，供 API 调用）
├── web/                      # Web 层
│   ├── server.py             #   FastAPI 应用
│   ├── routers/              #   API 路由（13 个模块）
│   └── frontend/src/         #   React 前端
│       ├── pages/            #     页面（壳组件 + 子面板）
│       ├── components/       #     通用组件
│       ├── stores/           #     Zustand 状态管理
│       └── i18n/             #     国际化（zh/en）
├── config/                   # 运行时配置
│   ├── *.example.json        #   配置模板
│   ├── personas/             #   人设
│   └── memory/               #   记忆数据（.gitignore）
└── scripts/                  # 工具脚本
    └── secrets-backup.sh    #   备份个人配置到 .secrets/ 私密仓库
```

## 架构

```
┌─────────────┐     ┌──────────────┐     ┌──────────┐     ┌─────────────┐     ┌────────────┐
│  Frontend   │────▶│  Web API     │────▶│ Services │────▶│ Agent Core  │────▶│   core/    │
│  (React)    │     │  (FastAPI)   │     │          │     │  (Mind/LLM) │     │ (Registry) │
└─────────────┘     └──────────────┘     └──────────┘     └──────┬──────┘     └────────────┘
                                                                 │
                                              ┌──────────────────┼──────────────────┐
                                              ▼                  ▼                  ▼
                                        ┌──────────┐     ┌────────────┐     ┌────────────┐
                                        │ Channels │     │  Entities  │     │    MCP     │
                                        │ (适配器)  │     │  (工具)    │     │  (桥接)    │
                                        └──────────┘     └────────────┘     └────────────┘
```

**依赖方向（严格单向）：**

```
web/frontend → web/routers → services → agent → core/
entities → entities._sdk → core.entity
channels/ → agent.channel

禁止: agent → web | core → agent | services → web | entities → agent（通过 _sdk 桥接）
```

---

## 开发指南

### 添加 AI 工具

在 `entities/` 下新建目录，创建 `tools.py`，框架自动发现并注册：

```python
# entities/weather/tools.py
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

工具开发规范：
- 返回值为 `str`（JSON 格式）
- 完整类型注解 + Google docstring
- 内部捕获异常，返回 JSON error
- 复杂逻辑拆分到 service 层

### 添加频道

在 `channels/{name}/` 下创建：

- `adapter.py` — 继承 `BaseChannel`，实现 6 个必需接口
- `channel_config.json` — 频道配置
- `__init__.py` — 导出 `CHANNEL_CLASS`

必需接口：`channel_id` / `display_name` / `capabilities` / `start` / `stop` / `send_text`

### 配置管理

所有配置文件在 `config/` 目录，JSON 格式。频道独立配置在各频道目录的 `channel_config.json`。

环境变量覆盖：`ANELF_<KEY>` 格式的环境变量会覆盖对应配置项。

---

## 敏感信息管理

个人配置与框架代码通过 `.gitignore` 完全分离，敏感文件（API Key、Token、记忆数据等）不会进入仓库，仅保留 `*.example.json` 模板供参考。

可选：将 `.secrets/` 初始化为独立 git 仓库，用于远程备份个人数据：

```bash
./scripts/secrets-backup.sh          # 一键同步并推送到私密仓库
```

---

## 致谢

AnelfAgent 的多平台通信能力依赖于以下优秀的开源项目：

| 项目 | 用途 | 协议 |
|---|---|---|
| [NoneBot2](https://github.com/nonebot/nonebot2) | 跨平台异步机器人框架，提供 NoneBot 桥接适配器 | MIT |
| [NapCatQQ](https://github.com/NapNeko/NapCatQQ) | 基于 NTQQ 的现代 Bot 协议端，提供 QQ OneBot v11 接口 | 混合协议 |
| [litellm](https://github.com/BerriAI/litellm) | 统一 100+ LLM 提供商的调用接口 | MIT |
| [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) | Telegram Bot API 封装 | LGPL-3.0 |
| [lark-oapi](https://github.com/larksuite/oapi-sdk-python) | 飞书 / Lark 开放平台 SDK | MIT |

特别感谢 [Nekro Agent](https://github.com/KroMiose/nekro-agent) 项目在多平台智能体架构设计上提供的参考与启发。

> **协议说明**：AnelfAgent 通过 OneBot v11 WebSocket 协议与 NapCatQQ 通信，不包含也不修改 NapCat 源码。NoneBot2 作为 pip 依赖引入，遵循其 MIT 协议。

---

## License

[MIT](LICENSE) © AnelfAgent Contributors
