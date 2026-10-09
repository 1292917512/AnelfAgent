# AnelfAgent

**v0.3** · 统一智能体框架 — 自主思考 · 语义记忆 · 工具编排 · 多模态生成 · 实时语音 · 多通道通信

**简体中文** | [English](README_EN.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%20%7C%203.12-blue.svg)](https://www.python.org/)
[![uv](https://img.shields.io/badge/package%20manager-uv-DE5FE9.svg)](https://github.com/astral-sh/uv)

AnelfAgent 是一个开源自托管的 AI 智能体运行时：给它一个 LLM API Key，它就能成为一个**有自己的记忆、会自主学习技能、能主动干活、住在多个聊天平台里**的 AI 助手。它内置自主决策引擎、混合语义记忆、技能自学习、子代理调度、可恢复工作流、MCP 工具桥接与多平台通道适配，覆盖文本、图像、语音、视频、音乐多模态能力，并附带一个现代化 WebUI 完成配置、对话与运维的全部操作。

> 本仓库为 **0.3 稳定基线**：架构与能力已趋于定型，适合自托管部署与二次扩展。

---

## 快速开始

> 目标：**5 分钟内让 Agent 跑起来**。只需要 Python、Node.js 和一个 LLM API Key。

### 1. 环境要求

| 依赖 | 版本 | 说明 |
|---|---|---|
| Python | **3.11 ~ 3.12** | 主运行时 |
| Node.js | **20**（推荐，见 `.nvmrc`） | 构建 WebUI 前端；不构建也能跑（仅无网页界面） |
| [uv](https://github.com/astral-sh/uv) | 最新 | Python 包管理器（强烈推荐，启动脚本会自动用它装依赖） |

### 2. 克隆与配置

```bash
git clone https://github.com/1292917512/AnelfAgent.git
cd AnelfAgent

# 从模板创建三份核心配置
cp config/llm_clients.example.json config/llm_clients.json
cp config/app_config.example.json config/app_config.json
cp config/mcp_servers.example.json config/mcp_servers.json
```

然后打开 `config/llm_clients.json`，在任意一个 provider 的 `api_key` 里填入你的 Key（OpenAI / Anthropic / DeepSeek / 智谱等 100+ 提供商均可，经 litellm 统一接入）。**只填一个能用的聊天模型 + 一个 embedding 模型即可起步**，其余全部可以在 WebUI 里边用边配。

> 也可以完全不手编 JSON：先空着启动，登录 WebUI 后在「模型」页点鼠标完成添加（热生效，无需重启）。

### 3. 构建前端并启动

```bash
# 构建 WebUI（一次性；之后代码更新才需要重新构建）
cd web/frontend && npm install && npm run build && cd ../..

# 启动（启动脚本会自动同步 Python 依赖）
./start.sh                 # macOS / Linux
start.bat                  # Windows
```

### 4. 打开 WebUI

浏览器访问：**http://127.0.0.1:8092/webui/**

在 WebUI 里你可以：和 AI 对话、添加/切换模型、开启频道、配置心跳任务、查看记忆与技能、管理审批——**几乎所有配置都是网页点选、保存即热生效**。

### 日常运维

```bash
./restart.sh               # 一键后台重启（改配置/更新代码后用）
Ctrl + C                   # 停止（start.sh 前台运行时）
uv run python launch.py --no-webui   # 只跑 Agent 内核，不开网页
```

- `start.sh` 自带**崩溃守护**：进程异常崩溃自动拉起，连续崩溃 5 次才停手
- 单实例守卫：重复启动会自动清场占用端口的老进程，不会双开
- 想开机自启/挂后台：直接用 `restart.sh`（nohup 后台拉起），或自行包一层 systemd / pm2

### 接入第一个聊天频道

WebUI →「频道」页选择平台按提示操作即可。最快上手的是**微信**（扫码登录，无需公网）和 **QQ**（配合 NapCat）。各频道接入要点见下文「多平台接入」。

---

## 它能做什么？

一句话：**一个 7×24 在线、记得住事、学得会技能、自己会干活的 AI**。

| 场景 | 说明 |
|---|---|
| 💬 聊天助手 | 住在 QQ / 微信 / 飞书 / Telegram / B站 / AcFun / WebUI 里，多平台同一颗大脑 |
| 🧠 记得住 | 语义记忆 + 知识图谱：你说过的事、你的喜好、你们的关系，长期记住且越用越准 |
| 🌱 会成长 | 聊天中自动沉淀「技能」（排障经验、任务方法），下次遇到同类问题直接复用 |
| 🛠 干活 | 读写文件、跑命令、联网搜索、操作桌面、控制智能家居、管理服务器——经审批安全执行 |
| ⏰ 主动性 | 心跳调度：定时提醒、定时任务、空闲时自我反思整理，不等指令也能推进工作 |
| 🎨 多模态 | 画图、语音合成、视频生成、音乐生成、表情包检索、看图说话、语音转写 |
| 📞 实时语音 | 全双工实时通话引擎：边说边听、随时打断，支持声纹识别「听出是谁」 |
| 👀 视觉 | 截屏/盯屏（持续观察屏幕变化）、人脸识别、桌面操控（看屏点鼠标） |
| 🤝 分身协作 | 一个任务拆给多个子代理并行干，完成后汇总；长任务可续跑、可中途追加指令 |
| 🔄 工作流 | 声明式 DAG 编排多步任务，崩溃断点续跑，已完成的步骤不重跑不重复付费 |
| 🔌 开放 | MCP 工具桥接、OpenAI 兼容 Responses API、插件市场、Webhook/HTTP 频道 |

---

## 核心能力

### 自主思维（Mind）

```
消息入队 → 收集态势（消息/任务/记忆/目标） → 元决策选行动 → think_loop 多轮工具编排 → 完成
```

| 决策类型 | 用途 |
|---|---|
| `REPLY` | 回复消息 |
| `REFLECT` | 心跳 / 反思任务 |
| `REMEMBER` | 主动记忆 |
| `PROACTIVE` | 主动触达 |
| `TOOL_ACTION` | 自主工具操作 |
| `PLAN` | 目标规划 |

系统提示按变更频率分层（stable → summary → 历史 → context → volatile → provider），字节级稳定命中 Anthropic / OpenAI **前缀缓存**，长对话成本显著降低。

### 实体注册与工具门控

所有能力（工具 / 模型 / 频道 / MCP / 存储）统一注册到 `EntityRegistry`，AI 按「目录 → 分组」两级发现工具，永远只拿到「刚好够用」的工具集：

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

### 混合语义记忆

Embedding + FTS5 + 标签匹配 + 时间衰减的混合评分召回；可选启用 **Cognee** 知识图谱投影与联邦召回，失败自动降级、SQLite 永远是权威存储。

- **LLM 检索规划**：首轮召回由轻量模型规划多路互补查询，异步深探在 AI 思考期间增量补入，三键账本保证同一事实一次回复只出现一次
- **记忆铁律文档**：写入路由 / 标签纪律 / 查询路由收敛为单一系统级提示词文档，Web 记忆页可视化编辑
- **遗忘治理**：重要性松弛 + 检索练习效应 + 归档/墓碑兜底召回（可恢复）；图谱边强度衰减 + AI 策展议程（事实归系统、决策归 AI）
- **写入判重**：结构化判断引擎先判「新事实 / 已覆盖 / 演进 / 碎片」，绝大多数写入热路径零额外 LLM 调用

### 技能自学习

对话后由 LLM 钩子面后台评审 → 沉淀为 `workspace/skills/SKILL.md` → 目录 + 语义双层召回注入 → 心跳策展（降级 / 归档 / 合并）。技能越攒越多，Agent 越用越顺手。输入 `/技能名` 可确定性点名调用。

### 子代理与工作流

- **子代理**：`delegate_task` 并行 fan-out、后台模式、独立迭代预算，按档案选模（内置 easy/medium/hard 难度档）；`follow_up_agent` 以完整 transcript 无损续跑；进度流 / 用量归集 / Web 面板全链路可观测；`send_to_agent` 支持 steer（步骤边界改方向）/ after（收束边界追加）两档中途指令
- **工作流**：JSON 声明 DAG（ask 子代理步 / tool 工具步 + 依赖），journal 断点恢复（崩溃续跑、已完步骤指纹复用不重付费）、修订导入父运行成果、门控重跑（结果不合期望先修复再重跑）

### 心跳与任务

任务内容（`config/tasks/*.json`）与调度分离，五种触发正交：

| 触发模式 | 说明 |
|---|---|
| `heartbeat` | 每 N 次心跳执行 |
| `scheduled` | 每天指定时间（多槽位独立去重） |
| `idle` | 连续空闲后触发（全局唯一一条，如自我反思） |
| `manual` | 仅手动 / AI 主动触发 |
| `trigger_event` | 事件触发（经 LLM 钩子面） |

每次心跳还跑内置维护：记忆健康检查、技能策展、日志合并、空闲会话折叠、图谱治理议程等。

### 实时语音与视觉

- **实时通话引擎**（`agent/realtime`）：全双工会话、智能端点检测（smart_turn）、随时打断、回声抑制；转写优先走 qwen 实时 ASR、FunASR 兜底
- **语音全链路**（`agent/audio` · `voice` · `tts`）：ASR 转写、TTS 流式合成（MiniMax / CosyVoice / Qwen-TTS / edge-tts）、**声纹识别**——听出说话人是谁，跨频道音源管理
- **视觉框架**（`agent/vision` + `screen` 实体）：截屏 / 盯屏（持续观察屏幕区域变化）、人脸识别（可选 [`deploy/face_server`](deploy/face_server) 边车）、桌面操控 `desktop_act`（看屏点鼠标键盘）
- **多模态生成**：图像 / 语音 / 视频 / 音乐统一适配器（OpenAI · MiniMax · 硅基流动 · DashScope 等）

### 模型管理（LLMManager）

- **两级结构**：Provider → Model，按能力类型（chat / tools / vision / embedding …）组织；禁用模型自动从所有路径剔除
- **候选链回退**：`chat_with_fallback` 在候选模型间推进，上下文超限快速失败换大窗口候选
- **思考契约配置驱动**：每个模型在配置里声明思考参数映射，代码对模型名零特判
- **双协议**：Chat Completions 与 Responses（`auto` 模式 404 自动回退），统一经 litellm 转发
- **热重载**：手改 `llm_clients.json` 三路径热生效（文件监听 / Web / AI 工具），不变模型零触碰
- **结构化判断**（`agent/judgment`）：Choice / Score / Noul 三原语统一评判通道，可选接入 TypeSafe Jev，未配置时降级普通模型

### 安全与权限

| 机制 | 作用 |
|---|---|
| 统一权限引擎 | `工具名(参数glob)` + allow / ask / deny，全局与频道两级 scope；高风险操作经频道消息或 WebUI 人工批准 |
| CRITICAL 风险兜底 | `@tool(risk="CRITICAL")` 声明自动升级为审批，guardian 先行评审 |
| 审批审计 | 非默认放行决策全部落账本，信任计数重启不丢 |
| 工具守卫 | 精确失败重复 / 连续失败 / 无进展循环 → warn / block / halt |
| 会话令牌 + 威胁扫描 | 一次性令牌标记可信历史；注入模式扫描拦截工具结果与记忆写入 |
| 自动脱敏 | API Key / Token / 密码在工具结果与日志中自动遮盖 |
| WebUI 认证 | `config/webui.json` 的 `auth.password`（空 = 免登录）；`auth.strict=true` 且密码为空时自动生成 32 位管理密码；API 访问支持 Bearer Key 管理 |

### LLM 钩子面

「在 LLM 思考边界派生带上下文的异步 LLM 工作」的统一注册原语：同一事件（`after_reply` / `delegation_resolved` / `llm_end` …）可挂多个钩子并行拉起，各自独立治理（并发 / 冷却 / 防抖 / 防递归）。技能后台评审、任务事件触发、实体钩子均经此落地。

### MCP 桥接

支持 stdio / SSE / Streamable HTTP；后台异步连接，工具自动注册为实体，可热重载；工具列表变更通知热同步，图像结果落盘注入视觉模型（截图类 MCP 直接「看到」画面）；连接存活探测 + 重连预算 + OAuth。

### 插件与热插拔

- **Minecraft 陪玩**：复用开源 Mineflayer MCP，支持 Java 26.1 游戏操作与游戏内聊天。
  安装与使用见 [`plugins/minecraft/README.md`](plugins/minecraft/README.md)。
- **插件系统**（`entities/plugins`）：插件 = 清单 + skills/ + .mcp.json + tools.py 的目录包，支持市场订阅（本地目录或 git 仓库），AI 可自主安装 / 升级 / 移除
- **模块热插拔**：实体 / 频道目录增删经目录监听自动对账（新增即时注册、删除完整拆除），Web 端亦可手动热同步

### 自运维与数据

| 能力 | 说明 |
|---|---|
| SSH 远程管理 | 连接管理 / 命令执行 / 文件传输，AI 可自主管理自己的部署 |
| DevOps | 项目代码更新 / 前端构建 / 应用重启（支持重启交接留言） |
| 存储卷管理 | 全部持久化数据登记为 8 个存储卷，在线热备份 / 恢复 / 迁移 / SQL 导出导入，Web 数据管理页可视化 |
| 外部 SQL 数据源 | PostgreSQL / MySQL 连接注册表，WebUI 浏览查询 |
| 文件分享 | 工作区文件生成对外下载链接并管理生命周期 |

---

## 多平台接入

频道目录自动发现 + 热插拔，新增频道只需 `channels/{name}/adapter.py` + 配置：

| 平台 | 要点 |
|---|---|
| **QQ** | OneBot v11 + NapCat（直连） |
| **微信** | iLink Bot API，扫码登录，无需公网 webhook（详见 [`channels/weixin/README.md`](channels/weixin/README.md)） |
| **飞书** | WebSocket 事件驱动；另有独立的飞书任务委派 ACP 接入（[`acp/`](acp) + `scripts/anelf-acp`，见 [`docs/feishu-task-binding.md`](docs/feishu-task-binding.md)） |
| **Telegram** | Bot API 长轮询 / Webhook |
| **Bilibili / AcFun** | 弹幕与私信接入 |
| **WebUI** | 内置三栏对话工作台（文件树 / 对话流 / Dock），SSE 推送，AI 可反向驱动界面 |
| **HTTP API** | 同步请求-响应 |
| **CLI** | 终端调试 |
| **Responses API** | 把 AnelfAgent 当作 OpenAI 兼容模型服务（`/v1/responses`）对外提供 |

---

## 内置工具（entities/）

| 实体 | 说明 |
|---|---|
| `filesystem` | 操作系统 — 文件读写 / 目录管理 / Shell 命令 / Python 执行（沙箱） |
| `codebox` | 代码编排 — Python 脚本内循环/条件调用其他工具，批量多步任务 |
| `operation` | 桌面操控（`desktop_act`）与 MCP 操作注册/语义化执行 |
| `screen` | 屏幕源 — 截屏 / 盯屏的视觉源组件 |
| `smart_home` | 智能家居 — Home Assistant 接入，灯光/空调/窗帘等设备域可插拔 |
| `minimax` | MiniMax — 图片理解/生成、语音合成/音色管理、联网检索、流式 TTS |
| `dashscope` | 阿里百炼 — 流式/非流式 ASR、CosyVoice/Qwen-TTS、音色复刻 |
| `audiosync` | 音源同步 — 外部音源（目录/推送）接入核心音频库 |
| `sticker` | 表情包与图片感知 — 收藏/语义检索/发送，文搜图、图搜图 |
| `vault` | 密码本 — 加密凭据库 / TOTP 验证器 / 模糊检索 / 泄露体检 |
| `ssh` | SSH 远程管理 — 连接 / 命令 / 文件传输 |
| `devops` | 运维管理 — 应用重启 / 前端构建 / 项目更新 |
| `share` | 分享推送 — 文件下载 / 媒体渲染 / 网址推送，生成对外链接 |
| `ui` | 界面交互 — 通知 / 弹窗提问 / 切换面板 / 注入草稿 |
| `ai_desktop` | AI 桌面 — 时间 / 节日 / 天气 / 日程 / 订阅额度等环境信息注入 |
| `dify` | 连接既有 Dify 实例：应用与工作流 DSL 管理、运行调用、MCP 桥接 |
| `sillytavern` | SillyTavern 酒馆纳管：进程生命周期 / git 更新 / 角色卡管理 |
| `plugins` | 插件安装 / 升级 / 移除与市场订阅 |
| `mcp` | MCP 服务桥接（动态注册） |
| `model_control` | 模型切换 / 参数调整 / Ollama 本地模型管理 |
| `entity_query` | 实体目录两级发现 |
| `system` | 环境信息 — 系统信息 / Git / 日志查询 |

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

**依赖方向（严格单向，import-linter 机械守卫）：**

```
web/frontend → web/routers → services → agent → core/
entities → entities._sdk → core.entity        channels/ → agent.channel → core.entity
禁止: agent → web | core → 业务层 | services → web | entities → agent（经 _sdk 桥接）
```

### 目录职责

| 目录 | 职责 |
|---|---|
| `core/` | EntityRegistry / 配置（`ConfigPaths` 动态路径）/ 生命周期 / 标签 / 事件 / 门控 / 脱敏 / 日志 / 存储卷注册表 |
| `agent/mind/` | 思维循环 / PFC / Prompt 分层 / 守卫 / 压缩 / 思维会话 |
| `agent/llm/` | LLM 客户端与管理器 / 错误分类重试 / 弹性回退 / 能力探测 / 多模态适配器 / Responses 协议 |
| `agent/memory/` | 混合语义记忆 + 便签 + 可选 Cognee 知识图谱 |
| `agent/skills/` · `delegation/` · `workflow/` | 技能自学习 / 子代理调度 / journal 化工作流引擎 |
| `agent/hooks_llm/` · `hooks/` | LLM 钩子面（异步并行）/ 用户 shell 钩子（同步守门） |
| `agent/heartbeat/` · `task/` · `planning/` | 心跳调度 / 任务定义 / 目标规划 |
| `agent/realtime/` · `voice/` · `tts/` · `audio/` | 实时通话引擎 / 语音会话 / TTS 流水线 / 音频能力注册表（ASR · 声纹） |
| `agent/vision/` | 视觉框架（截屏 / 盯屏 / 人脸 / 桌面操控视觉源） |
| `agent/judgment/` · `retrieval/` | 结构化判断（Jev）/ 联网检索供应商 |
| `agent/approval/` · `security/` | 统一权限与批准门 / 会话令牌 / 威胁扫描 |
| `channels/` | 频道适配器（目录自动发现 + 热插拔） |
| `entities/` | 工具实体（目录自动发现 + 热插拔，经 `_sdk` 注册） |
| `services/` · `web/` | 业务封装层 / FastAPI 路由 + React 前端 |
| `acp/` | 飞书任务委派 ACP 接入（独立顶层包） |
| `deploy/` | 参考边车服务（`face_server` 人脸识别 / `voicehub` 语音枢纽） |
| `config/` | JSON 配置 · SQLite 数据 · 人设 · 任务定义 |
| `tests/` | pytest 分层套件（实体/频道单测在各模块内 `<模块>/tests/`，随模块拔出） |

### 项目结构（摘要）

```
AnelfAgent/
├── launch.py                 # 启动入口（薄组合根）
├── start.sh / start.bat      # 启动脚本（含崩溃守护）
├── restart.sh                # 一键后台重启
├── core/                     # 基础框架（零业务依赖）
├── agent/                    # 智能体内核（不依赖 web）
├── channels/                 # qq / weixin / feishu / telegram / bilibili / acfun / webui / http_api / cli
├── entities/                 # filesystem / codebox / smart_home / minimax / vault / ssh / mcp / ...
├── services/ · web/          # 业务封装 · Web API + React 前端
├── acp/ · deploy/ · scripts/ # 飞书 ACP · 边车参考服务 · 运维脚本
├── config/                   # 配置与数据（模板为 *.example.json）
└── workspace/                # 运行时工作区（技能 / 上传等，本地生成）
```

---

## 配置说明

| 文件 | 内容 | 热生效 |
|---|---|---|
| `config/llm_clients.json` | 模型提供商与模型清单 | ✅（文件监听 / Web / AI 工具三路径） |
| `config/app_config.json` | 主配置（记忆 / 心跳 / 网络等数百项） | 大部分经 WebUI 配置中心保存即热更 |
| `config/mcp_servers.json` | MCP 服务清单 | ✅ |
| `config/webui.json` | WebUI 端口 / 认证 / 品牌 | 端口需重启 |
| `channels/<id>/channel_config.json` | 各频道配置 | ✅ |
| `config/tasks/*.json` + `config/heartbeat.json` | 任务定义 + 调度 | ✅ |

- **环境变量覆盖**：`ANELF_<KEY>` 覆盖 `app_config.json` 同名项；密钥可用 `${ENV_VAR}` 引用语法外置
- **整体搬迁**：`ANELF_CONFIG_DIR` / `ANELF_DATA_DIR` 环境变量可把配置与数据目录迁出项目目录
- **配置中心**：WebUI `/config` 页纯数据驱动，新配置项在后端注册即自动出现，保存即热更

---

## 开发指南

### 添加工具

在 `entities/` 下新建目录并实现 `tools.py`，框架自动发现：

```python
# entities/weather/tools.py
from entities._sdk import tool, entity
import json

entity("weather", "天气查询 — 获取实时天气信息")

@tool(name="get_weather", group="weather", tags=["web"])
async def get_weather(city: str) -> str:
    """查询指定城市的实时天气。

    Args:
        city: 城市名称
    """
    return json.dumps({"city": city, "weather": "晴", "temp": 25})
```

约定：返回 `str`（JSON）、完整类型注解 + Google docstring、错误统一走 `core.tool_errors`。

### 添加频道

在 `channels/{name}/` 提供：`adapter.py`（继承 `BaseChannel`）+ `config.py`（暴露 `CONFIG_MODEL` pydantic 模型）+ `channel_config.json` + `__init__.py`（导出 `CHANNEL_CLASS`）。

### 添加心跳任务

在 `config/tasks/` 创建任务 JSON，于 WebUI 心跳页绑定调度；亦可配置 `trigger_event` 让任务由事件触发。

### 包与测试

```bash
uv sync                          # 安装依赖
uv run pytest                    # 全量测试（unit + 无需凭证的 integration）
uv run pytest tests/unit         # 分层单元测试
uv run ruff check .              # Lint
uv run lint-imports              # 依赖方向契约检查
uv run mypy core/                # 类型检查（core 严格层）
scripts/check.sh                 # 本地 CI 镜像门禁（push 前跑）
```

CI（GitHub Actions）：lint 全仓静态门禁 + tests 按模块动态矩阵分腿 + frontend 构建；文档类提交全跳过。

更细的架构约定见 [`AGENTS.md`](AGENTS.md)（供编辑器 / Agent 注入的工作区指令，非运行时依赖）。

---

## 技术栈

| 分类 | 技术 |
|---|---|
| 运行时 | Python 3.11–3.12 · [uv](https://github.com/astral-sh/uv) · FastAPI · Uvicorn · Pydantic v2 |
| LLM | litellm（统一 100+ 提供商）· Chat Completions / Responses 双协议 |
| 存储 | aiosqlite（WAL）· FTS5 · sqlite-vec · 可选 Cognee 知识图谱 |
| 语音 | FunASR / qwen 实时 ASR · MiniMax / CosyVoice / Qwen-TTS / edge-tts · silero VAD / smart_turn |
| 外部数据 | asyncpg（PostgreSQL）· aiomysql（MySQL）· asyncssh（SSH）· Home Assistant |
| 协议 | MCP SDK · OneBot v11 · iLink · lark-oapi |
| 前端 | React 18 · TypeScript · Vite 6 · Tailwind CSS 4 · Zustand · TanStack Query · react-i18next（中/英） |

---

## 敏感信息管理

个人配置与框架代码通过 `.gitignore` 分离：API Key、Token、记忆库、频道密钥等不进仓库，仅保留 `*.example.json` 模板。配置值支持 `${ENV_VAR}` 引用语法把密钥外置到环境变量。

个人数据（API 配置 / 记忆数据 / 频道密钥 / 人设等）的备份由用户自行负责（如 NAS 定期备份，或经存储卷面板导出）。

---

## 开源与致谢

本项目以 **[MIT License](LICENSE)** 发布，欢迎 Star、Issue 与 PR。

**仓库**：https://github.com/1292917512/AnelfAgent

| 项目 | 用途 | 协议 |
|---|---|---|
| [litellm](https://github.com/BerriAI/litellm) | 统一 LLM API | MIT |
| [NapCatQQ](https://github.com/NapNeko/NapCatQQ) | QQ OneBot v11 协议端 | 混合协议 |
| [lark-oapi](https://github.com/larksuite/oapi-sdk-python) | 飞书 / Lark SDK | MIT |
| [FastAPI](https://github.com/fastapi/fastapi) / [MCP](https://modelcontextprotocol.io/) | Web 与工具协议 | MIT |

特别感谢 [Nekro Agent](https://github.com/KroMiose/nekro-agent) 与 [N.E.K.O](https://github.com/Project-N-E-K-O/N.E.K.O) 两个项目的参考与启发。

> **协议说明**：AnelfAgent 通过 OneBot v11 WebSocket 与 NapCatQQ 通信，不包含也不修改 NapCat 源码。微信频道对接腾讯 iLink Bot API，协议实现参考社区适配器实践。

### 参与贡献

1. Fork 本仓库并创建特性分支
2. 保持依赖方向与类型注解约定（见 `AGENTS.md`）
3. 为行为变更补充或更新 `tests/`
4. 提交清晰的 PR 说明动机与验证方式

---

## License

[MIT](LICENSE) © 2025–2026 AnelfAgent Contributors
