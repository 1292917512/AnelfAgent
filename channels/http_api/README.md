# HTTP/HTTPS 频道

为外部应用提供消息、媒体、会话控制、历史、模型 SDK 和系统工具的统一入口。
默认停用，默认地址 `http://127.0.0.1:8091`。

## AI 获取帮助

启用频道后，AI 可直接调用 **`http_api_portal_help()`** 获取简短接入说明。
需要细节时传 `topic="chat"`、`"sessions"`、`"sdk"` 或 `"https"`。
帮助按当前配置生成，不返回认证令牌；外部应用使用 **`GET /api/sdk`** 发现接口，
参数、必填项和默认值以 **`GET /openapi.json`** 为准，**`/docs`** 可交互调试。

推荐顺序：获取帮助 → 分配用户/会话标识 → 先订阅 SSE → 提交异步消息。

## 配置

配置中心的键为 `http_api_<字段>`；频道文件 `channels/http_api/channel_config.json`
使用不带前缀的字段名。通过配置中心或 `set_channel_config` 写入，监听、TLS、并发等配置重启频道生效。

| 字段 | 默认 | 用途 |
|---|---|---|
| `enabled` | `false` | 启用频道 |
| `host` / `port` | `127.0.0.1` / `8091` | 监听地址与端口 |
| `api_token` | 空 | `Authorization: Bearer` 或 `x-api-token`；公网监听/公开地址必须配置 |
| `ssl_certfile` / `ssl_keyfile` | 空 | 原生 HTTPS 的 PEM 证书和私钥，必须成对 |
| `ssl_keyfile_password` | 空 | 加密私钥密码，可用环境变量引用 |
| `public_base_url` | 空 | 外部基址，如 `https://agent.example.com/gateway`，供 AI 与文档使用 |
| `cors_origins` | `[]` | 允许跨域的可信浏览器来源 |
| `reply_timeout` | `60` | 同步答复等待秒数 |
| `callback_url` / `callback_token` | 空 | 默认 webhook 及其 Bearer 令牌 |
| `callback_timeout` | `10` | 回调超时秒数 |
| `stream_queue_size` / `max_stream_subscribers` | `256` / `128` | SSE 缓冲帧数与订阅上限 |
| `max_connections` / `sdk_concurrency` | `256` / `8` | HTTP 连接/任务与 SDK 调用上限；超载返回 503 |

集成令牌具有系统工具权限，应由可信后端保管。`user_id` 是来源声明，会话隔离不构成多租户授权。
面向不可信用户的应用应先在自己的后端认证和授权，再调用门户。

## 接口

| 方法与路径 | 功能 |
|---|---|
| `POST /api/chat` | 文本、图片、音频、视频、文件入站 |
| `GET /api/chat/stream` | SSE：`delta`、`tool_call`、`reply`、`turn_end` |
| `POST /api/sessions/control` | 停止或继续原会话 |
| `GET /api/sessions/history` | 分页/增量读取持久化历史 |
| `POST /api/vision/push` | 外部视觉源单帧 |
| `WS /api/vision/stream` | 持续视觉推流；HTTPS 时用 `wss://` |
| `GET /api/sdk` | 生效接入契约 |
| `GET /api/sdk/models` | 按 `model_type` 列出已启用模型 ID |
| `POST /api/sdk/models/{operation}` | 模型 SDK 直调 |
| `GET /api/sdk/tools` | 已启用工具目录与 schema，可按 `group` 筛选 |
| `POST /api/sdk/tools/{name}` | 经权限与 hook 调用系统工具 |
| `GET /health` | 公开的进程 HTTP 存活探针 |

### 消息与会话

```json
{"user_id":"my_app:alice","session_id":"support-1","message":"你好","message_id":"msg-001","async_mode":true}
```

- 私聊按 `user_id` 分桶；群聊另传 `group_id`，同群不同发送者保留各自 `user_id`。
  多个窗口另传 `session_id`；用户、群组和子会话的收发独立。身份不得包含 `#`，群 ID 不能为保留值 `"0"`。
- `kind` 支持 `chat/event/system/notification`；通知聚合到通知中心，强制异步受理。
  引用回复使用 `reply_to_id/reply_content`，`to_me` 标记是否指向 AI。
- `images` 为 `[{url|base64, mime_type}]`，两种来源二选一；`media` 为
  `[{kind: audio|video|file, base64, mime_type, file_name}]`。媒体进入原有处理链，不等同于模型原生实时音视频。
- `async_mode=false` 等待最终文本，`true` 返回 `accepted`。响应业务状态有
  `ok/accepted/timeout/duplicate/error`。同一会话并发同步请求只保留最新等待者；需要逐请求结果时串行提交。
- 请求携带 `callback_url` 即登记该会话的出站回调。文本最终回复依次尝试同步响应、SSE、回调；
  进度消息只经 SSE/回调。含媒体的整条出站消息仅经回调，无有效接收端则报告失败。

SSE 示例：`GET /api/chat/stream?user_id=my_app:alice&session_id=support-1`。
群聊改传 `group_id`；省略 `session_id` 才订阅该身份的全部子会话。
30 秒无帧发送 `ping`；`turn_end` 包含 `turn_id/reason/error`。流不持久化，断线后重新订阅并用历史校准最终内容。
不能设置请求头的 SSE/WS 客户端可用 `?token=`，一般优先使用认证头。

视觉推送/WS 每帧格式：`{source, image_base64, mime_type, captured_at?}`，图片支持 png/jpeg/webp，
单帧不超过 20MB。系统内部判变、缓冲并在后续轮次注入；WS 坏帧回错误而不断连，未认证关闭码 4401。

### 停止、继续与历史

```json
{"user_id":"my_app:alice","session_id":"support-1","action":"stop"}
```

停止复用 SDK 协作式中断，覆盖该会话回复、子代理和所属后台工作。可附 `run_id` 精确停止，
但必须与会话目标一致。`ok` 表示受理，`idle` 表示没有目标工作；以 `turn_end.reason=interrupted` 核对本轮收束。

```json
{"user_id":"my_app:alice","session_id":"support-1","action":"resume","request_id":"resume-001"}
```

等待上一轮收束后再继续；可用 `message` 自定义续接指令。继续是原会话的新输入，不恢复调用栈、
不自动重放工具；`resume` 不接受 `run_id`，重试复用同一 `request_id`。

历史查询同样传 `user_id/group_id/session_id`，返回 `{status, scope, messages}`，记录含 `id/role/content/ts_ns`。
`limit` 默认 50、上限 200；`before_id` 加载更早消息，`after_id` 按 ID 增量读取，两者互斥。
查询不改变已读水位、不触发思考，也不重放未持久化的 delta。

### SDK 模型与工具

模型 `operation` 支持 `chat`、`describe_images`、`describe_video`、`embed`、`embed_multimodal`、
`transcribe`、`text_to_speech`、`rerank`、`generate_image`、`edit_image`、`generate_video`、`generate_music`、`judge`。
JSON 参数从 `ModelAPI` 签名派生；示例 `POST /api/sdk/models/chat`：

```json
{"messages":[{"role":"user","content":"你好"}],"timeout":60}
```

返回 `{status, result}`，保留模型身份与用量，移除供应商 `raw`；字节输入用 base64，字节输出为 URL-safe base64。
模型直调不继承 Agent 上下文、不自动执行工具；SDK `stream=true` 仍返回聚合结果，过程流使用聊天 SSE。

先查询工具目录，再按 schema 调用 `POST /api/sdk/tools/{name}`：

```json
{"user_id":"my_app:alice","session_id":"support-1","arguments":{},"request_id":"action-001"}
```

返回 `{status, request_id, result, notice}`，保留权限提示；HTTP 200 不代表 `result` 中没有工具错误。
有副作用的调用应提供固定 `request_id`；重发返回 `duplicate`，不重复执行，也不返回旧结果。
工具直调独立于 Agent 思维循环，会话停止不是任意 HTTP 调用的事务撤销。

## HTTPS 与扩展

原生 HTTPS 配置证书/私钥后重启；证书、私钥或端口不可用时启动失败。
也可由代理终止 TLS，内部维持回环 HTTP，填写 `public_base_url` 和 `api_token`；
代理需透传路径和认证头、关闭 SSE 缓冲、支持 WebSocket，并设置足够的长连接超时。
公开基址用于地址公告，不代表已签发证书或配置代理；证书由部署方提供并续期，客户端保持证书验证。

新增内核/实体工具注册后自动进入目录；新增领域资源 API 使用独立 Router，经 `_create_app` 挂载，
沿用统一边界，不反向导入 `web/services`。新增功能同步更新 OpenAPI、频道帮助和契约测试。

## 可靠性约定

- 契约 `api_version=1`，既有 `/api` 路径保持兼容；客户端忽略新增字段，参数以 OpenAPI 为准。
- 认证先于业务解析。401=认证失败、403=权限拒绝、404=不存在、413=体积超限、422=参数错误、
  502=模型上游失败、503=能力未就绪/超载。业务 HTTP 响应带 `X-Request-ID`。
- 单消息不超过 256K 字符，附件最多 9 个，单附件最多 50MB，累计请求体最多 80MB，分块上传同样受限。
- 消息/工具去重共用进程内缓存：300 秒、最多 2000 项；回调登记最多 1000 项，重启后重注册。
  重启或容量淘汰后不保证去重；webhook 当前没有持久化重试队列，不承诺 exactly-once。
- 超时/断线不表示未执行，先查历史、执行诊断或实际状态，避免重复写入。SSE 慢客户端会被断开；
  回调媒体不超过 20MB 时内联 base64，超过时只传服务端路径，文件可达性由接入方安排。
- 回归覆盖本机真实 TLS/SSE、隔离、重试、控制与 SDK 转发；公网证书、代理、压力与恢复指标需在部署环境验收。
