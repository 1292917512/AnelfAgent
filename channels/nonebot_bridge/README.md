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
（装包 + 加入加载列表 + worker 自动重启）。已装插件在**插件** Tab 管理。

---

## AI 工具（group: `nonebot`）

启用桥接后自动注册，AI 在任意会话可用：

| 工具 | 用途 |
|------|------|
| `nonebot_status` | worker 进程 / 在线 Bot / 适配器 / 插件全景 |
| `nonebot_restart` | 重启 worker（敏感，受 channel_tools_allow_sensitive 门控） |
| `nonebot_list_plugins` | 已加载插件及用法说明 |
| `nonebot_store_search` | 搜索插件商店 |
| `nonebot_install_plugin` | 安装商店插件并重启（敏感） |
| `nonebot_run_command` | 以虚拟用户触发任意插件命令并捕获回复（`/help` 等） |
| `nonebot_send` | 指定 bot / 平台 / 目标显式发送消息 |

`nonebot_run_command` 是"插件功能暴露给 AI"的通用通道：worker 构造合成私聊事件走
NoneBot 完整匹配管线，期间截获 `Bot.send` 把插件回复回传给 AI（不真正发到平台）。
当前支持 OneBot V11 事件合成，其余适配器如实报错。

## Model Experience 声明

1. **模型看到**：7 个 `nonebot` 分组工具目录项；桥接平台消息按普通频道消息进入对话流。
2. **token 影响**：tools 前缀小幅增长（管理工具随频道启用注入）。
3. **缓存影响**：工具集在运行期版本稳定，不触碰 stable/summary/history 前缀层。

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

`GET /status` · `POST /restart` · `GET /adapters` · `POST /adapters/install|uninstall` ·
`GET /plugins` · `POST /plugins/install|uninstall` ·
`GET /store/plugins?query=` · `GET /store/adapters` ·
`GET|PUT /config` · `GET /logs?count=` · `POST /command`

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

## 故障排查

| 现象 | 排查 |
|------|------|
| 总览显示 venv 未就绪 | 查看 `/nonebot` 页日志 Tab：uv 是否可用、基线包安装是否失败 |
| worker 未回连 | 确认 `bridge_ws_port`（默认 8197）未被占用；日志看 bridge 重连记录 |
| 适配器装了但无 Bot 在线 | 平台凭据是否填对（环境配置 Tab）；反向 WS 地址是否指向 `:8198` |
| 插件命令不响应 | `COMMAND_START` 等前缀配置（环境配置 Tab）；`intercept_all` 是否开启 |
| AI 收不到消息 | 确认频道已启用、适配器已启用、总览在线 Bot ≥ 1 |
| 发送失败 | worker 日志查平台 API 报错；多 Bot 场景核对粘性路由（`nonebot_send` 显式指定） |
