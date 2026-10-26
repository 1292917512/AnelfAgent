# AnelfAgent

**v0.4.0** · 个人开源、自托管的自主 AI 运行时

**连续记忆 · 跨频道在场 · 主动行动 · 技能生长 · AI 自维护 · 模块热更新**

**简体中文** | [English](README_EN.md)

[![CI](https://github.com/1292917512/AnelfAgent/actions/workflows/ci.yml/badge.svg)](https://github.com/1292917512/AnelfAgent/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%20%7C%203.12-blue.svg)](https://www.python.org/)
[![uv](https://img.shields.io/badge/package%20manager-uv-DE5FE9.svg)](https://github.com/astral-sh/uv)

AnelfAgent 希望让你在自己的设备上养成一个长期在场的 AI 个体：**有连续的记忆与人设，能在多个频道理解不同的人，能主动推进任务，也能在授权范围内学习技能、修改扩展并验证更新。**

QQ、微信、飞书、Telegram、游戏和 Web 工作区是它的不同入口，共用同一套思维、记忆、技能与工具。对话、心跳、后台事件、子代理和工作流共同构成持续工作的运行时；你可以通过 WebUI 查看它正在做什么、依据什么，以及操作是否真正完成。

这是一个**个人维护、持续迭代的 MIT 开源项目**。代码、部署、模型选择、权限和数据由使用者掌握；日常配置与运维既能在网页完成，也有供 AI 调用的工具接口。“自主成长”落实为可追溯的记忆、可复用的技能和可验证的模块更新流程。

## 导航

- [快速开始](#快速开始)
- [自主性与 AI 自维护](#自主性与-ai-自维护)
- [记忆与跨频道连续性](#记忆与跨频道连续性)
- [能力与工作区](#能力与工作区)
- [频道与工具生态](#频道与工具生态)
- [架构与开发](#架构与开发)
- [个人部署与开源协作](#个人部署与开源协作)

## 快速开始

### 环境与安装

| 依赖 | 版本 / 用途 |
|---|---|
| Python | **3.11–3.12**，主运行时 |
| Node.js | **24**（见 `.nvmrc`；最低 22.12），构建和开发 WebUI |
| [uv](https://github.com/astral-sh/uv) | 管理 Python 环境、依赖与锁文件 |
| 模型 | 可用的聊天模型；语义检索另配 Embedding 模型，语音和生成能力按需添加 |

```bash
git clone https://github.com/1292917512/AnelfAgent.git
cd AnelfAgent

cp config/llm_clients.example.json config/llm_clients.json
cp config/app_config.example.json config/app_config.json
cp config/mcp_servers.example.json config/mcp_servers.json

uv sync --locked
cd web/frontend
npm ci
npm run build
cd ../..
```

编辑 `config/llm_clients.json` 的模型与凭据，或先使用模板启动，在 WebUI「模型」页配置。调用经 LiteLLM 统一接入，可选择云端提供商或兼容的本地模型服务。

### 启动与首次配置

```bash
./start.sh                 # macOS / Linux
```

Windows 在项目根目录执行：

```powershell
.\start.bat
```

打开 **[http://127.0.0.1:8092/webui/](http://127.0.0.1:8092/webui/)**。配置模型与人设，开始对话，再到「频道」页接入平台。也可单独运行内核或终端模式：

```bash
uv run python launch.py --no-webui
uv run python -m channels.cli
```

Cognee 知识图谱依赖随项目安装，集成默认启用；配置相应聊天与嵌入模型后使用。图谱增强不可用时保留原生记忆检索。联网检索支持内置源、商业提供者及独立部署的 SearXNG / Crawl4AI；有 Docker 环境时可后台启动本地服务，见[检索部署说明](deploy/retrieval/README.md)。外部服务和语音、视觉负载按需配置。

### 日常运行与更新

- `start.sh` / `start.bat` 提供崩溃守护；连续致命崩溃达到上限会停止重启，避免无限循环。
- 实例锁拒绝同一检出的重复接管，保护正在写入的数据。先正常停止旧实例，再启动新实例。
- 前台运行用 `Ctrl+C` 正常停止；macOS / Linux 可用 `./stop.sh`、`./restart.sh` 有序停止与后台重启。
- AI 重启工具要求进程由启动脚本守护拉起；直接运行 `launch.py` 的实例需手动重启。重启留言可在恢复后交还 AI 接续。
- 拉取版本后用 `uv sync --locked` 同步依赖；前端资源变更后执行 `npm ci`、`npm run build`。模块更新与核心升级采用不同路径，见下方热更新表。

## 自主性与 AI 自维护

### 从响应消息到持续工作

```text
对话 / 心跳 / 平台事件
          ↓
收集消息、记忆、任务和目标 → 决策 → 工具执行 / 委托 / 工作流
          ↑                                  ↓
     下一次行动 ← 真实回执、进展、反思与技能沉淀
```

主 Mind 通过单一调度 worker 串行处理顶层工作，统一理解多频道态势；子代理、LLM 钩子与工作流中的隔离执行体可以并行。用户消息、定时工作和后台完成事件沿同一运行时协调，忙碌时保留待办，维护工作在阶段边界让路。

| 机制 | 自主工作的方式 |
|---|---|
| Mind 决策 | 回复、反思、记忆、主动触达、工具操作与目标规划；普通对话可走直接回复路径 |
| 心跳与任务 | 每 N 次心跳、每日时刻、空闲、手动与事件触发；任务内容与调度分离，执行历史用于去重与核验 |
| 目标规划 | 持续保存目标、步骤与进展；完成事实驱动收口，终态清理 |
| 技能生长 | 后台评审实际操作，提炼可复用的 `SKILL.md`；关键词与语义匹配推荐，AI 自主读取、使用、合并或恢复 |
| 子代理 | 按档案选模型、并行委托、续跑与 steer / after 追加指令，保留进度和用量归属 |
| 工作流 | 可保存的 DAG 定义、ask / tool 步、journal 断点恢复、修订成果复用及有界修复重跑 |
| LLM 钩子 | 在回复、委托完成等事件边界派生后台工作，统一控制并发、冷却、防抖和递归 |

**事实由系统记录，取舍由 AI 决策，结果以实际回执为准。** 技能匹配不代表已执行，工具受理不代表已完成，生成了回复也不等于消息已送达。

### AI 可以维护自己的扩展

在已授权的源码路径与工具权限内，AI 可以完成以下闭环：

1. 查阅现有接口、记忆与技能，读取代码并修改工具实体或频道。
2. 运行对应验证，通过单模块工具加载新实现，或批量同步目录。
3. 从回执读取当前工具名与分组，调用 `list_entity_methods` 获取最新参数，再实际调用核验行为。
4. 加载失败时检查回滚结果，修复源码后重试；依赖、核心或状态模块变更走安装与正常重启路径。
5. 将可复用方法沉淀为技能，保留必要的记忆与交接，供后续工作复用。

AI、Web 管理页和目录监听复用现有注册表、生命周期、权限与热同步器。

| AI 工具 | 作用 |
|---|---|
| `reload_entity(entity_name)` | 按 `entities/` 实际目录名重载单个实体入口 |
| `reload_channel(channel_id)` | 重载单个频道，运行中的连接会短暂断开并重连 |
| `sync_modules(target="all", reload_existing=False)` | 对账 `entities` / `channels` / `all` 的增删；显式传 `True` 才主动刷新正常存续模块 |
| `update_project` / `update_and_restart` | 拉取代码，或更新、构建并排定重启；冲突、脏工作区或构建失败如实返回 |
| `restart_app(message)` | 有序重启并为恢复后的自己留下接续信息 |

三个模块同步工具均标记为 **CRITICAL**，经过统一权限入口；频道另受敏感操作开关约束。规则可按目标匹配，如 `reload_entity(filesystem)`、`reload_channel(qq)`、`sync_modules(entities)`。源码写入仍需原有文件沙箱与权限许可，重载不扩大写入范围。

### 热更新覆盖与边界

**工具实体和频道模块统一接入热发现、热同步与定向重载，组件界面通过模块贡献点接入前端。** 不同内容的生效方式如下：

| 内容 | 生效方式与边界 |
|---|---|
| 实体 / 频道目录增删 | 目录监听自动对账（可关闭），也可由 Web 或 AI 手动同步 |
| 单个实体代码 | 刷新 `tools.py` / `router.py`；保留状态资源身份；已缓存的辅助模块、连接管理器与进程级桥接代码需重启 |
| 单个频道代码 | 刷新适配器、配置模型和私有 Python 依赖；保留配置、工具开关与已学习路由；停用频道不自动启动 |
| 模型、MCP、任务与大部分配置 | 经各自配置监听、Web 或 AI 管理入口更新；连接型参数可能需要组件重连 |
| 实体 / 频道 / 核心前端 | Vite 开发模式使用 Fast Refresh；清单与语言资源变动重新发现并刷新；正式页面需构建资源后刷新浏览器 |
| 核心框架、共享依赖、独立负载 | 正常重启或使用所属模块的安装 / 升级入口；Python 依赖由 uv 管理，热重载不代替安装 |

加载过程有并发保护和模块级回滚；失败、忙碌和部分完成不会报告成全部成功。**批量同步是逐模块事务**，已成功的模块不会因另一个模块失败而整体撤回。频道回收失败时保留资源所有权并报告，避免同时创建第二份连接。回滚覆盖受管注册与运行资源，不撤销任意文件、网络或外部系统副作用。

## 记忆与跨频道连续性

### 一颗大脑，多个现场

频道共享 AI 的记忆、技能与人设，各自保留会话、用户、群组和子会话标识。身份由**频道 + ID** 联合识别，不凭昵称或相同数字 ID 猜测同一个人；已有别名关联可用于跨频道历史识别，语音身份另有声纹绑定。

跨频道感知让它能把相关经历与未完成的工作联系起来；发送始终明确目标频道和会话，统一记录真实投递回执。切换会话不隐式改变当前调用的收件人。

记忆属于同一个 AI 个体，相关内容可以跨频道召回，同时携带涉及对象、出处、日期和隐私标记。私密内容的披露由记忆纪律约束，人物反思另有 scope 可见性限制；**共享记忆不等于不可信多租户之间的数据库隔离**。个人部署应按实际需求配置可访问者、权限与外部发送范围。

### 记忆各司其职

| 内容 | 权威入口 |
|---|---|
| 事实、事件、反思、永久规则 | 长期记忆，`memorize` / `recall` / `get_memory` |
| 人物偏好与互动风格 | 实体画像，一人一份持续更新 |
| 谁与谁有什么关系 | 原生关系图谱，带证据与来源记忆指针 |
| 可复用的方法与经验 | 技能库，`workspace/skills/` 下的技能目录与 `SKILL.md` |
| 工作笔记与导航 | Markdown 便签与 `mem:ID` 指针，系统受管区块受保护 |
| 临时提醒与目标进度 | 短期记忆 / 任务 / planning，按各自生命周期收口 |
| 图谱检索增强 | Cognee 派生投影；原生存储仍是权威来源 |

一条信息按职责落入对应系统，其他位置使用指针引用，减少多份副本之间的矛盾。

### 检索、纠错与长期治理

- **混合召回**：SQLite + FTS5 + Embedding，结合标签、时间与关系。sqlite-vec 使用精确 KNN，扩展不可用时有 NumPy 评分回退；数据规模增长仍需考虑扫描成本。
- **分级与预算**：`off / tags / fast / deep`，按频道和轮次选择；查询规划、多路检索与图谱增强受准备预算约束，超时保留已取得的证据，AI 可手动深入检索。
- **材料可追溯**：区分涉及人物与实际来源，区分事件日期、记录时间和材料窗口；文件命中显示真实路径和片段行号，图谱综合明确区别于原话。
- **持续捕获**：按会话消费消息并保存检查点，失败可恢复；写入判重区分新事实、已有覆盖和事实演进，版本复核避免旧判断覆盖新修订。
- **纠错与遗忘**：更新原条目、证据反馈、重要性衰减、可恢复归档与墓碑梗概；合并保留 ID 重定向及谱系。语义相似只是候选，不直接当作同一事实。
- **人格生长**：用户设定的人设与证据驱动的自画像分层；反思先待验证，经过确认与晋升，不把 AI 自述当作用户确认。
- **图谱保护**：Cognee 增强失败保留原生结果，构建与维护经过受管锁、检查点和发布恢复；更换嵌入模型需核对向量身份。

Web 记忆页提供长期记忆、便签、关系、召回测试、标签治理和索引维护。详见[记忆系统](docs/memory-system.md)与[技能系统](docs/skills.md)。

## 能力与工作区

### 模型、多模态与外部世界

| 能力 | 实现与用途 |
|---|---|
| 模型管理 | Provider → Model，按聊天、工具、视觉、嵌入等能力选模；候选链回退、Chat Completions / Responses 协议、配置驱动的思考参数 |
| 结构化判断 | Choice / Score / Noul，原生 Jev 与普通模型通道，供去重、可选工作预判等使用 |
| 实时语音 | 全双工、端点检测、打断、ASR 与流式 TTS；声纹、音源库和频道身份联动 |
| 视觉与桌面 | 图片 / 视频理解、截屏、持续盯屏、人脸识别与 `desktop_act`；可按动作回看画面核验 |
| 多模态生成 | 图像、语音、视频、音乐统一能力路由，按所配模型与组件选用提供者 |
| 联网检索 | 搜索 / 网页读取提供者矩阵：内置源、BigModel、DeepSeek、SearXNG / Crawl4AI 等 |
| MCP | stdio / SSE / Streamable HTTP、OAuth、连接恢复与工具列表增量同步；图像结果可交视觉模型 |
| 开放接口 | HTTP 双向门户、OpenAI 兼容 `/v1/responses`、飞书任务委派 ACP 接入 |

上下文按变动频率分层：稳定指令与历史保留前缀，召回、状态和执行反馈放在动态尾部；工具按目录、分组与能力门控按需发现。缓存观测区分客户端字节、供应商缓存和统计口径，成本与可用性仍取决于所选模型和服务。

### WebUI 工作区

- **对话与文件协作**：文件树、编辑器、对话和功能 Dock；文件 / 选区可作为上下文引用，草稿与服务端快照分离。
- **过程与完成状态**：主轮次、子代理、模型用量和工具回执关联展示；子代理默认紧凑，详情按需展开；完成状态与执行中状态明确区分。
- **本轮文件改动**：侧栏汇总已有活动记录中的文件操作并查看差异。展示受观测操作，不是完整 Git 差异；外部命令的任意修改不保证被捕获，也不提供无快照保证的撤回。
- **长时间运行**：历史分批读取、向前翻页、最新窗口裁剪与有界会话缓存；实时流重连和快照补同步，避免把静默误判成执行结束。
- **统一交互**：来源区块、组合检索、横向标签、共享弹窗与二级详情、统一按钮对齐和动效，适配深浅主题、移动端及 reduced-motion。
- **AI 界面交互**：通知、提问、草稿与面板跳转；页面状态区分活跃、后台、空闲和离线，可见性信号不等于已阅读或已授权。
- **运维入口**：模型、频道、工具来源、任务、记忆、权限审计、组件凭据、数据卷与服务状态；模块专属界面由贡献点装配。

## 频道与工具生态

### 多平台频道

| 平台 / 模块 | 接入特点 |
|---|---|
| **QQ** | OneBot v11 + NapCat |
| **微信 `weixin`** | iLink Bot API，扫码登录、无需公网 webhook；见[说明](channels/weixin/README.md) |
| **个人微信 `wcauto`** | Windows 桌面桥，私聊 / 群聊 / 媒体 / 朋友圈；独立桥负载与写操作开关，见[说明](channels/wcauto/README.md) |
| **飞书** | WebSocket 事件；另有[飞书任务委派 ACP](docs/feishu-task-binding.md) |
| **Telegram** | Bot API 长轮询 / Webhook |
| **GitHub** | 仓库事件轮询或 Webhook，优先级唤醒与聚合，AI 可反查及回写；见[说明](channels/github/README.md) |
| **AcFun** | 弹幕与私信 |
| **Minecraft** | Mineflayer MCP 游戏陪玩与任务操作；源码和已安装负载分别管理，见[说明](channels/minecraft/README.md) |
| **WebUI** | 工作区对话、SSE / WebSocket 与界面交互 |
| **HTTP API** | 双向消息 / 媒体 / 视觉门户，同步、SSE 与回调；默认停用，见[协议](channels/http_api/README.md) |
| **CLI** | 终端交互与调试，见[说明](channels/cli/README.md) |
| **Responses API** | 作为 OpenAI 兼容的智能体服务对外提供能力 |

各平台仍需要相应账号、凭据和运行环境；频道已连接与真正完成收发是不同的验证结论。

### 工具实体

| 实体 | 职责 |
|---|---|
| `filesystem` / `codebox` | 文件、目录、Shell、Python 与工具编排，遵守沙箱和权限 |
| `operation` / `screen` | 桌面动作、MCP 语义操作与屏幕视觉源 |
| `smart_home` | Home Assistant 与可插拔设备域，见[说明](entities/smart_home/README.md) |
| `minimax` / `dashscope` | 多模态、语音、音色与提供者专属能力 |
| `audiosync` / `sticker` | 音源接入、图片 / 表情收藏、感知与语义检索 |
| `vault` | 加密凭据库、TOTP、生成与检索 |
| `ssh` / `devops` | 远程管理、项目更新、构建、重启、模块热同步与崩溃报告 |
| `share` / `ui` | 文件与媒体分享、链接生命周期、AI 界面交互 |
| `ai_desktop` | 时间、节日、天气、日程与额度等环境信息 |
| `dify` / `sillytavern` | Dify 应用 / 工作流集成与 SillyTavern 进程 / 角色卡管理 |
| `plugins` / `mcp` | 插件市场订阅、安装 / 升级 / 移除，以及 MCP 连接与工具 |
| `model_control` / `entity_query` / `system` | 模型与 Ollama 管理、两级能力发现、环境 / Git / 日志工具 |

工具页区分**核心、实体、频道和外部接入**。来源来自真实注册归属；核心工具不会被当作可单实体重载的扩展。插件可携带清单、技能、MCP 配置和 Python 工具，AI 可在授权范围内管理安装与升级。

## 架构与开发

### 分层与模块所有权

```text
React WebUI → FastAPI 路由 → services → agent → core
                                         ↑
实体 → entities._sdk → 注册表 / 运行时桥     频道 → agent.channel
```

`core` 不依赖业务层，`agent` 不依赖 Web；具体实体与频道仅在运行时组合根装配。依赖方向由 import-linter 守卫。启动步骤用 FlowMachine 声明依赖，长驻组件由 Lifecycle 正序启动、逆序清理；关键存储未关闭前不释放实例锁。

| 目录 | 职责 |
|---|---|
| `core/` | 注册表、配置与路径、生命周期、事件、日志、网络、存储卷与通用契约 |
| `agent/mind/` · `context/` · `llm/` | 思维调度与执行、上下文分层、统一模型 SDK 与缓存观测 |
| `agent/memory/` · `skills/` | 记忆、关系、图谱投影、技能学习与治理 |
| `agent/task/` · `heartbeat/` · `planning/` | 任务定义、何时执行与目标进展 |
| `agent/delegation/` · `workflow/` · `hooks_llm/` | 隔离委托、可恢复工作流与异步 LLM 工作 |
| `agent/audio/` · `voice/` · `tts/` · `realtime/` · `vision/` | 音视频与实时能力 |
| `agent/approval/` · `security/` · `judgment/` · `retrieval/` | 权限、安全、结构化判断与检索 |
| `channels/` · `entities/` | 自持后端、配置声明、前端、翻译与测试的扩展模块 |
| `services/` · `web/` | 管理业务门面、API 与 React 前端 |
| `acp/` · `deploy/` · `scripts/` | ACP、独立服务部署与运维入口 |
| `config/` · `workspace/` · `tests/` | 本地配置、运行工作区与分层测试 |

### 配置与数据

| 位置 | 内容与更新方式 |
|---|---|
| `config/llm_clients.json` | 提供商与模型；文件监听、Web、AI 同源更新 |
| `config/app_config.json` | 主配置；配置中心从后端声明生成，大部分热生效 |
| `config/mcp_servers.json` | MCP 服务；由连接管理入口同步 |
| `config/webui.json` | 端口、认证与品牌；端口需重启，品牌可自定义 |
| `channels/<id>/channel_config.json` | 模块自持配置；`CONFIG_MODEL` 是字段声明唯一来源 |
| `config/tasks/` · `config/heartbeat.json` | 任务内容与调度 |
| `config/provider_keys.json` | 组件凭据；专属授权由所属模块管理 |
| `config/permission_rules.json` | 工具规则、目标参数与会话权限 |

路径统一经 `ConfigPaths`；`ANELF_CONFIG_DIR` / `ANELF_DATA_DIR` 可迁出配置与数据，`ANELF_<KEY>` 覆盖主配置生效值。支持环境引用的配置字段可用 `${ENV_VAR}`；MCP 的 `env` / `headers` 按其服务配置契约处理。

SQLite、Cognee 和便签通过存储卷管理：在线备份、校验发布、恢复 / 迁移后重启生效；外部 PostgreSQL / MySQL 可作查询和表数据传输。配置与存储备份应一并纳入个人部署的备份方案。

### 扩展与验证

- 工具实体：在 `entities/<name>/tools.py` 声明工具，经 `entities._sdk` 使用框架能力；返回 JSON 字符串，完整类型注解与 Google docstring，错误用 SDK 的 `tool_error` / `error_from_exception`。
- 频道：提供 `channels/<id>/adapter.py`、`config.py`（`CONFIG_MODEL`）与 `__init__.py`；继承 `BaseChannel` 并实现接口。可导出 `CHANNEL_CLASS`，运行值文件由配置层持有。
- 模块 UI：实体 `panel.tsx` / `panels/`、频道 `frontend/` 自持实现，贡献点挂载工作区、总览、凭据编辑器、路由和工具视图；核心页面不直接导入具体模块。
- 运行资源注册到 Lifecycle；导入阶段只做声明。涉及模型 I/O 时说明可见内容、token 与缓存影响。实体 / 频道测试放在各自 `tests/` 中。

```bash
uv sync --locked
uv run ruff check .
uv run lint-imports
uv run mypy core/
uv run pytest
scripts/check.sh                 # macOS / Linux：本地 CI 镜像
```

前端开发保留后端运行，在另一个终端执行：

```bash
cd web/frontend
npm ci
npm run dev                     # http://127.0.0.1:3000/webui/
# /api（含 WebSocket）默认代理到 127.0.0.1:8092
npm run lint
npm run typecheck
npm run test:modules
npm test
npx playwright install chromium
npm run test:e2e
npm run build
```

可用 `ANELF_WEBUI_API_TARGET` 覆盖开发代理。CI 按变更范围运行静态门禁、模块 / 全量测试和前端验证，包含 Windows 与相关 macOS 检查；文档体量门禁始终保留。浏览器测试使用隔离数据，真实账号、游戏或外部服务需另做实际验收。

### 深入阅读

| 主题 | 文档 |
|---|---|
| 架构与工作约定 | [架构参考](docs/architecture-reference.md) · [工作区指令](AGENTS.md) · [设计取舍](docs/design-decisions.md) |
| 自主执行 | [思维](docs/mind-architecture.md) · [任务 / 心跳 / 规划](docs/tasks-heartbeat-planning.md) · [委托与工作流](docs/delegation-and-workflow.md) |
| 记忆与模型 | [记忆](docs/memory-system.md) · [技能](docs/skills.md) · [模型 SDK](docs/llm-and-models.md) · [缓存与成本](docs/context-cache.md) |
| 连接与多模态 | [频道与 scope](docs/channels-and-scopes.md) · [实时语音](docs/realtime-voice.md) · [声纹](docs/audio-voiceprint.md) · [视觉 / 能力路由](docs/vision-and-capabilities.md) |
| 安全与运维 | [权限与审批](docs/security-and-approval.md) · [运维](docs/operations.md) · [网络](docs/network.md) · [MCP](docs/mcp-architecture.md) |
| 前端与质量 | [前端](docs/frontend.md) · [模块贡献点](docs/ui-contributions.md) · [测试体系](docs/testing.md) |
| 独立集成 | [飞书 ACP](docs/feishu-task-binding.md) · [检索服务](deploy/retrieval/README.md) · [VoiceHub](deploy/voicehub/README.md) · [人脸服务](deploy/face_server/README.md) |

## 个人部署与开源协作

### 权限、隐私与维护边界

项目面向个人自托管。你决定启用哪些频道、模型、工具和自动任务，也负责账号授权、运行权限、备份和访问控制。使用外部模型时，任务所需上下文会发送到所选服务；自托管数据目录并不代表模型推理全部在本地。

`permission_rules.json` 使用 `工具名(参数glob)` 与 `allow / ask / deny`，支持全局和频道范围，拒绝优先。Guardian 辅助评审**默认关闭**；开启后评审 `ask` 和无显式规则的 CRITICAL 调用，不提供人工审批会话。Guardian 不可用时按自主策略放行并记录原因；权限配置损坏或检查异常会拦截。需要硬限制的操作应使用明确的 `deny` 与部署权限。旧版 `approval_policies.json` 应迁移到当前规则文件。

框架还提供文件写入基线、工具循环防护、威胁扫描和普通结果 / 日志脱敏。密码本的临时凭据工具经授权可把明文交给当前任务模型，观测与持久化副本隐藏原文；这类使用仍应受目标与权限约束。WebUI 支持管理密码与 Bearer API Key，对外开放前先配置认证。

### 个人维护，开放参与

项目以个人实际使用中的长期 AI 为方向，持续维护核心契约，让扩展跟随模块自持。欢迎提出具体问题、分享可复现场景、贡献频道、工具、技能和文档；支持与验证范围以仓库实现及对应测试为准。

1. Fork 仓库并创建特性分支。
2. 遵守 [AGENTS.md](AGENTS.md) 的依赖方向、类型与模块归属约定。
3. 为行为变更补充匹配范围的验证，说明真实验证与隔离测试的区别。
4. PR 写清问题、最终行为与验证方式；提交前清理临时实体、频道、数据和凭据。

**技术栈**：Python · uv · FastAPI / Pydantic · LiteLLM · SQLite / FTS5 / sqlite-vec · Cognee · React 18 / TypeScript / Vite 6 · Tailwind CSS 4 · Zustand / TanStack Query · react-i18next。

感谢 [LiteLLM](https://github.com/BerriAI/litellm)、[FastAPI](https://github.com/fastapi/fastapi)、[MCP](https://modelcontextprotocol.io/)、[lark-oapi](https://github.com/larksuite/oapi-sdk-python)、[NapCatQQ](https://github.com/NapNeko/NapCatQQ) 等开源项目，以及 [Nekro Agent](https://github.com/KroMiose/nekro-agent)、[N.E.K.O](https://github.com/Project-N-E-K-O/N.E.K.O) 的参考与启发；感谢 [Xerj](https://github.com/xerj-org/xerj) 对检索片段、来源定位与索引覆盖设计的启发。Minecraft 集成复用 [awesome-mineflayer-mcp](https://github.com/G0Osey99/awesome-mineflayer-mcp) 生态。

AnelfAgent 经 OneBot 与 NapCat 通信，不包含或修改其源码；各外部组件与服务遵循各自的许可和使用条款。

[MIT License](LICENSE) © 2025–2026 AnelfAgent Contributors · [GitHub](https://github.com/1292917512/AnelfAgent)
