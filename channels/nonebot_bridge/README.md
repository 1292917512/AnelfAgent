# NoneBot 桥接频道 — 完整 NoneBot 客户端

将 NoneBot2 生态（适配器 + 插件）作为**独立子进程客户端**接入 AnelfAgent：
平台消息可被 NoneBot 插件自身处理，同时汇总传递给 AI；AI 也能向各平台发送消息。

> 官方文档参考见 [`docs/`](docs/README.md)（经 `scripts/sync_nonebot_docs.py` 同步）。

---

## 架构（v3：子进程 + 独立 venv）

```
平台（QQ / Telegram / Discord / KOOK / 飞书 / DoDo / Satori / Console …）
   ⇅ 各平台协议（NoneBot 适配器完成协议转换）
NoneBot worker 子进程（独立 venv，uv 管理，自有端口 :8198）
   ├─ 平台适配器（Web 界面安装，含 registry.nonebot.dev 动态适配器）
   ├─ 自由安装的社区插件（处理消息、响应命令）
   └─ 桥接客户端 ──WS(127.0.0.1:8197, token)──┐
                    ⇅ 线协议（事件上行 / 发送下行 / 控制 / 状态 / 日志）
AnelfAgent 主进程（不依赖 nonebot2）
   └─ nonebot_bridge 频道（进程管理 + 粘性路由 + WS 服务端）
       → on_message → ChannelManager → AgentApp → AI
       ← AI 回复 → reply() → 粘性路由到来源 Bot → 平台
```

| 组件 | 位置 | 职责 |
|------|------|------|
| 频道适配器 | `adapter.py` | 桥接 WS 服务端、粘性路由、发送、健康探针、AI 工具 |
| 运行时管理 | `runtime.py` | venv 引导（uv）、worker 进程管理（崩溃自动重启）、包安装、日志环 |
| worker 入口 | `worker/bot.py` | NoneBot 初始化、适配器注册、插件加载、事件钩子、合成命令分发 |
| 事件转换 | `worker/wire_out.py` → `wire_in.py` | NoneBot Event → 中性线协议 JSON → AdapterMessage |
| 线协议 | `worker/protocol.py` | 父/worker 共享消息常量与编解码（纯 stdlib） |
| 配置 | `config.py` | 内置适配器注册表（平台接入元数据）+ 配置 schema |
| 服务层 | `services/nonebot.py` | 商店代理 / 安装 / 配置（Web 与 AI 共用） |

### 关键设计

- **真重启**：worker 是独立进程，Web/AI 可随时重启（适配器、插件、env 变更后热重启）；
- **依赖隔离**：插件与适配器安装在专用 venv（`<数据目录>/nonebot/venv`），
  `uv sync` 不会清除、依赖冲突不影响主应用；
- **消息双路**（默认）：`intercept_all=false` 时插件与 AI 同时收到消息；
  开启后平台事件仅供 AI 处理；
- **粘性路由**：多 Bot 在线时，回复默认发给该会话最近一次消息的来源 Bot；
- **可拓展**：内置 10 个精选适配器之外，registry.nonebot.dev 的适配器（钉钉等）
  可直接安装，通用协议转换兜底。

### 消息质量（OneBot v11 对齐直连频道）

出站（AI → 平台）：

| 能力 | 说明 |
|------|------|
| @ 提及 | `[at_uid:x]` 转**真正的 at 消息段**（含 @全体成员；非 OneBot 平台降级纯文本） |
| 回复引用 | `reply_to` → reply 段 |
| 图片/语音/视频 | 本地文件 **base64 内联**（100MB 上限，大文件线程读取）、URL/file_id 直传 |
| 文件 | 群文件 / 私聊文件上传 API（`upload_group_file` / `upload_private_file`） |
| 多段混排 | 图+文+@ 在同一条消息里发送 |

入站（平台 → AI）：

| 能力 | 说明 |
|------|------|
| 回复内容回捞 | `get_msg` 取被回复消息文本 → `reply_content`（AI 直接看到被回复内容） |
| NapCat 零拷贝 | to_me 消息图片经 `get_image` 解析协议端**本地路径**，免 URL 下载 |
| 群昵称缓存 | 有名片则缓存、无名片回捞历史（200 群容量） |

AI 发送通道：会话内回复（粘性路由）+ 通用输出工具（send_photo/send_voice/send_file
按能力自动可用）+ `nonebot_send`（文本/图片，可指定 bot/平台）。

---

## 快速开始

### 1. 启用桥接

Web → **通道** 页 → 启用 `NoneBot 桥接` 频道（或编辑
`channels/nonebot_bridge/channel_config.json` 的 `enabled`）。首次启用会自动创建
worker venv 并安装基线包（nonebot2 2.5.x + websockets）。

### 2. 安装适配器并对接平台

Web → **NoneBot** 页 → **适配器** Tab：

1. 选择平台卡片（如 OneBot V11）→ 点击**安装**（装包到 worker venv）；
2. 展开卡片，按表单填写平台凭据（写入 worker `.env`）；
3. 打开**启用**开关（worker 自动重启加载）。

### 3. 各平台接入所需内容

| 平台 | 适配器 key | 需要什么 | 难度 |
|------|-----------|---------|------|
| QQ（NapCat/Lagrange） | `onebot_v11` | 反向 WS：把实现端指向 `ws://<主机>:8198/onebot/v11/ws`；或正向 `ONEBOT_WS_URLS` + `ONEBOT_ACCESS_TOKEN` | 易 |
| Telegram | `telegram` | `TELEGRAM_BOTS=[{"token": "..."}]`（@BotFather 获取）；国内需代理 | 易 |
| KOOK / 开黑啦 | `kook` | `KAIHEILA_BOTS=[{"token": "..."}]`，WebSocket 无需公网 | 易 |
| QQ 官方（频道/群） | `qq` | `QQ_BOTS=[{"id": appid, "token": ..., "secret": ..., "intent": {...}}]` | 中 |
| Discord | `discord` | `DISCORD_BOTS=[{"token": "..."}]`；需代理 | 中 |
| 飞书 | `feishu` | `FEISHU_BOTS=[{"app_id", "app_secret", ...}]`，需事件订阅 | 中 |
| DoDo | `dodo` | `DODO_BOTS=[{"client_id", "token"}]` | 中 |
| Satori（协议聚合） | `satori` | `SATORI_CLIENTS=[{"host", "port", "token"}]`，可经 Chronocat 多平台 | 中 |
| OneBot V12 | `onebot_v12` | `ONEBOT_WS_URLS` | 中 |
| 终端（测试用） | `console` | 零配置（worker 日志页交互） | 易 |

> 未列出的平台（钉钉等）：适配器页的"社区"条目直接安装，通用转换兜底接入。

### 4. 安装插件

Web → **NoneBot** 页 → **插件商店** Tab：浏览 / 搜索（数据与
[nonebot.dev/store/plugins](https://nonebot.dev/store/plugins) 同源），一键安装
（装包 + 加入加载列表 + worker 自动重启）。已装插件在**插件** Tab 管理
（支持启停 — 仅调整加载列表、保留安装包 — 与卸载）。

**高级安装（自管理仓库代码）**：商店页"高级安装"支持三类安装源，AI 经
`nonebot_manage_plugin`/`nonebot_manage_adapter` 的 `source` 参数同权使用：

| source 形态 | 说明 |
|-------------|------|
| 空 | 商店/注册表的 PyPI 包名 |
| `git+https://host/you/repo.git@分支` | **git 仓库**（`https://x.git` 与 `user/repo` 简写自动规范化） |
| `/path/to/repo`（可勾选**可编辑**） | 本地路径；可编辑安装后仓库代码改动**即时生效**，适合自维护插件开发 |

**git 源本地检出**：git 源不会只落在 uv 全局缓存（`~/.cache/uv/git-v0/`）——
桥接先把它克隆到 **`<数据目录>/nonebot/repos/<仓库名>`**（随数据目录被 git
忽略），再从本地路径安装。你可以直接在这个目录里浏览、修改源码；
环境 Tab 的「源码仓库」卡列出全部检出项，「拉取并重装」对每个仓库执行
`git pull --ff-only`（快进合并，**不覆盖你的本地修改**）后强制重装。
可编辑安装项改代码即时生效，无需重装。溯源记录原始 git spec，
卸载时按已装清单规范化匹配推导分发名。

**安装源与代理**（配置 Tab / AI `nonebot_config_set`）：

- `pip_index_url`：自定义 PyPI 源（自建 devpi / 镜像），空 = 默认；
- `pip_proxy`：安装代理。空 = 继承系统；**`off` = 强制直连**
  （剥离代理环境变量并覆盖 git 全局配置里的代理，git 源安装同样生效）；
  其余值如 `http://127.0.0.1:7897` = 使用该代理（uv 无 `--proxy` 旗标，
  经子进程环境变量注入，pip 走旗标）。

### 5. 环境管理（uv 集成）

Web → **NoneBot** 页 → **环境** Tab：

- **状态**：uv 版本 / worker venv Python 版本 / venv 路径 / 包数量；
- **初始化环境**：频道未启用时也可直接创建 worker venv（总览页引导卡同样提供）；
- **升级 NoneBot 基线**：一键升级 nonebot2 本体与 websockets（即"NoneBot 更新"）；
- **单包升级**：包列表每行升级按钮，或输入包名升级任意包；
- **重建 venv**：危险操作（确认弹窗），删除整个 venv 重装基线，运行中自动停止并恢复。

未安装 uv 时自动回退 `python -m venv` + venv pip（较慢，界面会提示安装 uv）。

---

## AI 工具（group: `nonebot`，Web 服务启动即注册）

**Web 与 AI 双通道同权**：以下 13 个工具与 Web 界面共用同一服务层
（`services/nonebot.py`），环境引导/装卸/配置在频道未启用时同样可用。
敏感操作（标 🔒）受 `channel_tools_allow_sensitive` 门控。

| 工具 | 用途 |
|------|------|
| `nonebot_status` | 全景状态：环境 / worker 进程 / 在线 Bot / 已启用适配器 / 已加载插件 |
| `nonebot_env_status` | 环境详情：uv / Python 版本、venv 就绪态、基线包 |
| `nonebot_env_packages` | 列出 worker venv 已安装包（名称 + 版本） |
| `nonebot_env_manage` 🔒 | 环境管理：`bootstrap` 初始化 / `upgrade` 升级(缺省基线) / `rebuild` 重建 |
| `nonebot_manage_adapter` 🔒 | 适配器管理：`install` / `uninstall` / `enable` / `disable` |
| `nonebot_manage_plugin` 🔒 | 插件管理：`install`（商店）/ `uninstall` / `enable` / `disable` |
| `nonebot_config_get` | 读取配置（敏感环境变量遮盖） |
| `nonebot_config_set` 🔒 | 原子写配置项：顶层项（`intercept_all`/`worker_port`/`adapters`…）或 `nonebot_env.<ENV_KEY>`（空值删除） |
| `nonebot_lifecycle` 🔒 | worker 生命周期：`start` / `stop` / `restart` |
| `nonebot_logs` | 读取 worker 日志尾部（排障） |
| `nonebot_store_search` | 搜索插件商店，返回 module_name 供安装 |
| `nonebot_run_command` | 以虚拟用户触发任意插件命令并捕获回复（`/help` 等） |
| `nonebot_send` | 指定 bot / 平台 / 目标显式发送消息 |

`nonebot_run_command` 是"插件功能暴露给 AI"的通用通道：worker 构造合成私聊事件走
NoneBot 完整匹配管线，期间截获 `Bot.send` 把插件回复回传给 AI（不真正发到平台）。
当前支持 OneBot V11 事件合成，其余适配器如实报错。

AI 典型自主编排示例：`nonebot_env_manage bootstrap` → `nonebot_manage_adapter install
onebot_v11` → `nonebot_config_set nonebot_env.ONEBOT_ACCESS_TOKEN <token>` →
`nonebot_lifecycle restart` → `nonebot_status` 验证 Bot 在线。

## Model Experience 声明

1. **模型看到**：13 个 `nonebot` 分组工具目录项（Web 服务启动即注入，不依赖频道启用）；
   桥接平台消息按普通频道消息进入对话流。
2. **token 影响**：tools 前缀增长约 13 个目录项（查询类工具常驻；敏感工具受全局开关控制）。
3. **缓存影响**：工具集在运行期版本稳定（模块级注册一次），不触碰 stable/summary/history 前缀层。

---

## 配置参考（`channels/nonebot_bridge/channel_config.json`）

| 键 | 默认 | 说明 |
|----|------|------|
| `enabled` | `false` | 启用频道 |
| `adapters` | `[]` | 加载的适配器 key 列表 |
| `plugins` | `[]` | 加载的插件模块名列表 |
| `nonebot_env` | `{}` | 写入 worker `.env` 的键值对（各平台凭据等） |
| `intercept_all` | `false` | `true`=事件仅供 AI；`false`=插件与 AI 双路 |
| `bridge_ws_port` | `8197` | worker 回连主进程的桥接 WS 端口 |
| `worker_host` / `worker_port` | `127.0.0.1` / `8198` | worker 自身 HTTP/WS 监听（平台反向接入地址） |
| `auto_restart` | `true` | worker 崩溃自动重启（指数退避，稳定 5 分钟后复位） |
| `uv_exec` / `python_exec` | 自动探测 | venv 创建工具覆盖（高级） |

`adapters` / `plugins` / `nonebot_env` / `intercept_all` / `worker_*` 任一变更
都会触发 worker 热重启（配置签名比对，见 `adapter.reload_config`）。

## Web API（`/api/nonebot/*`）

`GET /status` · `POST /restart` · `POST /worker/start|stop` ·
`GET /env` · `POST /env/bootstrap|upgrade|rebuild` · `GET /env/packages` ·
`GET /adapters` · `POST /adapters/install|uninstall|enable` ·
`GET /plugins` · `POST /plugins/install|uninstall|enable` ·
`GET /store/plugins?query=` · `GET /store/adapters` ·
`GET|PUT /config` · `GET /logs?count=` · `POST /command`

Web 界面（`/nonebot` 页七 Tab）与 AI 工具共用同一服务层：总览 / 适配器 /
插件 / 插件商店 / 配置 / 环境 / 日志。

---

## 从 v2（进程内嵌）迁移

**破坏性变更**：

1. NoneBot 不再运行在主进程内，主应用依赖已移除 `nonebot2` / `nonebot-adapter-onebot`
   （原 ASGI 挂载点 `/nonebot` 取消）；
2. **反向 WS 平台（NapCat 等）需改指 worker 端口**：
   旧 `ws://<主机>:<主应用端口>/nonebot/onebot/v11/ws` →
   新 `ws://<主机>:8198/onebot/v11/ws`；
3. worker venv 首次启用时自动创建（`<数据目录>/nonebot/`），原手动 `pip install`
   的适配器需在 Web 界面重装一次；
4. `intercept_all` 默认值由 `true` 改为 `false`（插件与 AI 双路，可在环境配置页切回）；
5. Channels 页的 NoneBot Tab 移除，管理入口迁移到独立 `/nonebot` 页面。

---

## 二次开发指南

新增平台 / 扩展能力只需触碰最小面：

| 要扩展什么 | 改哪里 | 说明 |
|-----------|--------|------|
| 新增内置适配器 | `config.py` `KNOWN_ADAPTERS` | 加一个条目（label/package/import/setup 元数据），Web 表单自动渲染、可安装 |
| 适配器专属发送逻辑 | `worker/bot.py` `_SENDERS` 注册表 | **实现一个 `async def sender(ctx)` + `@_sender("key")` 一行注册**；不注册则走通用兜底。`ctx` 含 bot/target/text/reply_to/media_kind/media_source |
| 入站协议转换 | `worker/wire_out.py`（worker 侧）/ `wire_in.py`（父侧） | 平台事件 → 中性 JSON → AdapterMessage，两侧都是纯函数可单测 |
| 线协议消息类型 | `worker/protocol.py` | 常量 + WIRE_VERSION 递增（两端同仓发布，握手时父进程校验并告警） |
| 新增 AI 工具 | `channels/nonebot_bridge/tools.py` `_TOOL_SPECS` | 一个 async 函数 + 一行注册（名称/描述/敏感标记），handler 内调 `services/nonebot.py` |
| 新增 Web 端点 | `services/nonebot.py`（逻辑）+ `web/routers/nonebot.py`（薄路由） | Web 与 AI 共用同一服务层，一处实现两端受益 |

关键约定：
- **服务层单一来源**：`services/nonebot.py` 是唯一业务实现，Web 路由与 AI 工具都是薄封装；
- **纯函数下沉**：可测试的逻辑放纯函数（`segments.py`/`wire_out.py`/`runtime.py` 的 build_* / parse_*），主环境可直接单测，worker 代码经双路 import 也可测；
- **worker 零 agent 依赖**：`worker/` 只 import nonebot + stdlib，主进程变更不影响 worker venv。

## 故障排查

| 现象 | 排查 |
|------|------|
| 总览显示 venv 未就绪 | 查看 `/nonebot` 页日志 Tab：uv 是否可用、基线包安装是否失败 |
| worker 未回连 | 确认 `bridge_ws_port`（默认 8197）未被占用；日志看 bridge 重连记录 |
| 适配器装了但无 Bot 在线 | 平台凭据是否填对（环境配置 Tab）；反向 WS 地址是否指向 `:8198` |
| 插件命令不响应 | `COMMAND_START` 等前缀配置（环境配置 Tab）；`intercept_all` 是否开启 |
| AI 收不到消息 | 确认频道已启用、适配器已启用、总览在线 Bot ≥ 1 |
| 发送失败 | worker 日志查平台 API 报错；多 Bot 场景核对粘性路由（`nonebot_send` 显式指定） |
