# HTTP API 频道

外部系统与 Agent 的**双向 HTTP 门户**：任何能发 HTTP 的程序（传感器、脚本、其他服务、
其他 Agent）都经本频道把消息/图片/媒体/视觉画面传给 AI，AI 的回复与主动消息也经本频道
回投给外部系统。

默认监听 `127.0.0.1:8091`，**默认停用**。配置组 `adapter/http_api`（配置中心可改，
热读取生效以频道重启为准）：

| 配置键 | 默认 | 说明 |
|--------|------|------|
| `http_api_enabled` | `false` | 是否启用 |
| `http_api_host` | `127.0.0.1` | 监听地址；非回环地址必须配置 `api_token`，否则拒绝启动 |
| `http_api_port` | `8091` | 监听端口 |
| `http_api_reply_timeout` | `60` | 同步调用等待最终回复的秒数 |
| `http_api_api_token` | 空 | 入站认证（`x-api-token` 头或 `Authorization: Bearer`）；空 = 仅回环免认证 |
| `http_api_callback_url` | 空 | 默认出站回调（webhook）；AI 的回复/主动消息 POST 到该地址 |
| `http_api_callback_token` | 空 | 出站回调时携带的 `Authorization: Bearer` 令牌 |
| `http_api_callback_timeout` | `10` | 出站回调请求超时（秒） |

## 入站（外部系统 → AI）

### `POST /api/chat` — 消息

```json
{
  "message": "门磁开了",
  "user_id": "sensor_hub",
  "user_name": "传感器中枢",
  "group_id": "",
  "session_id": "",
  "kind": "chat",
  "message_id": "hub-0001",
  "reply_to_id": "",
  "reply_content": "",
  "to_me": true,
  "images": [{"url": "https://…/x.jpg"}],
  "media": [{"kind": "audio", "base64": "…", "mime_type": "audio/wav", "file_name": "a.wav"}],
  "callback_url": "http://192.168.1.10:9000/anelf",
  "async_mode": true,
  "request_id": "req-001"
}
```

- **会话归属**：`group_id` 非空即群会话（scope `group_http_api:<group_id>`），否则私聊
  （scope `user_http_api:<user_id>`）；`session_id` 覆盖会话后缀实现多会话隔离，
  缺省取 `group_id`/`user_id`。同一身份的历史、记忆与上下文在同一 scope 延续。
- **标准信封**：入站经 `dispatch_inbound` 统一归一化——`[time]/[channel]/[name]/[uid]/
  [message_id]` 等标签前缀自动生成，与 QQ/TG 等频道同管道同纪律。
- **`kind`**：`chat`（默认）/ `event` / `system` 留在原会话并以 `[kind:xxx]` 标签区分；
  `notification` 聚合进通知中心会话（`user_http_api:notification`），正文标注来源，
  **强制异步受理**（返回 `accepted`，不产生对调用方的同步回复）。
- **`message_id` 幂等键**：同会话内重复 `message_id` 返回 `status="duplicate"` 不重复入管道
  （客户端重试/网络重发安全）；缺省取 `request_id`。
- **`images`**：图片直注模型（视觉模型原生看图）。`url`（仅 http/https）与 `base64` 二选一，
  base64 落盘 `uploads/image/` 后走标准图片段。
- **`media`**：非图片媒体（`kind`: `audio` / `video` / `file`），仅接受 `base64`，
  单条 ≤ 50MB；落盘 `uploads/<类型>/` 后作为媒体消息段进入统一管道（AI 经媒体工具链处理，
  音频/视频在文本层降级为 `[media_type:…][media_path:…]` 标签，不会静默丢失）。
  附件总数（images + media）≤ 9，单消息 ≤ 256K 字符，请求体 ≤ 80MB（超出 413/422）。
- **`async_mode=false`（默认）同步**：阻塞等待最终回复，最长 `reply_timeout` 秒，
  响应 `{"status": "ok", "reply": "…"}`；超时返回 `status="timeout"`（Agent 仍会处理完，
  结果只经流/回调可见）。**同一会话的并发同步请求相互取代**：旧请求立即失败，只认最新一条。
- **`async_mode=true` 异步**：立即返回 `{"status": "accepted"}`，回复与过程帧经
  `/api/chat/stream` 或 `callback_url` 投递，长任务不受 `reply_timeout` 约束。
- **`callback_url`**：携带即把本会话的出站回调注册为该地址（仅 http/https），
  后续回复与主动消息都推送到它；进程重启后需重新注册（或用频道默认回调配置兜底）。
- 响应 `status`：`ok` / `accepted` / `timeout` / `duplicate` / `error`（`error` 字段带原因）。

### `POST /api/vision/push` — 外部视觉源推帧

```json
{"source": "doorbell", "image_base64": "…", "mime_type": "image/jpeg", "captured_at": 1730000000.0}
```

把一路外部画面（摄像头、其他机器截屏、传感器快照）推入统一视觉缓冲，与本地捕获源
走相同的判变与上下文注入路径，缓冲中记为 `external:<source>`。png/jpeg/webp，单帧 ≤ 20MB。
画面变化时 AI 在后续轮次看到该源最新帧；持续推送保持源活跃。停用中的源拒收
（`vision_disabled_sources` 或视觉页开关）。返回 `{"status": "ok", "changed": …, …}`。
与主 web 服务 `/api/vision/push` 是同一入口的两个门。

### `WS /api/vision/stream` — 持续推流（实时画面）

实时视频源的推流入口：外部系统采帧后逐帧上行，**判变、缓冲与上下文注入由系统内部完成**。
每帧发一条 JSON 文本（字段同 `/api/vision/push`），服务端每帧回 ack：

```
→ {"source": "cam1", "image_base64": "…", "mime_type": "image/jpeg"}
← {"status": "ok", "changed": true, "source": "external:cam1"}
```

坏帧只回 `{"status": "error", "error": …}` 不断连；鉴权同 HTTP（`?token=` 或请求头），
认证失败以 4401 关闭。单帧图片 ≤ 20MB（WS 帧上限 32MB）。推流方自行决定帧率与抽帧
策略（模型侧目前按变化轮次看图，非逐帧实时解析），静态画面持续推送不会被误判离线。

### `GET /api/chat/stream?user_id=<id>` — SSE 订阅

订阅该身份的全部过程帧（`event: message`，data 为 JSON）。**需同一令牌认证**
（不能自定义请求头的客户端用 `?token=`）：

| `type` | 说明 |
|--------|------|
| `delta` | 回复增量（`delta` 文本 + `reasoning` 标记） |
| `tool_call` | 工具调用起止（`status`: running/done/error） |
| `reply` | 一条完整文本（异步回复、进度消息、通知） |
| `turn_end` | 一轮结束（`error` 标记） |

30 秒无帧发 `event: ping` 保活。订阅前缀匹配会话：`user_id` 覆盖其全部 `session_id` 会话。

### `GET /health` — 健康探针

## 出站（AI → 外部系统）

AI 侧无需感知 HTTP 细节：它照常回复或 `send_message`，频道按**三级级联**投递：

1. **同步响应**：有阻塞等待的同步请求时，最终回复直接作为 `/api/chat` 响应体返回；
2. **SSE 订阅**：该会话有活跃 `/api/chat/stream` 订阅时，文本经 `reply` 帧推送；
3. **webhook 回调**：以上都不可达时，POST 到会话注册的 `callback_url`
   （或频道默认 `http_api_callback_url`），带 `callback_token` Bearer 认证。

回调负载：

```json
{
  "event": "message",
  "chat_id": "sensor_hub",
  "message_id": "http-1730000000000",
  "segments": [
    {"type": "text", "text": "已布防"},
    {"type": "image", "caption": "现场", "path": "/…/x.jpg", "base64": "…", "mime_type": "image/jpeg"}
  ],
  "ts": 1730000000.0
}
```

媒体段（image/video/audio/voice/file）只经回调投递：本地文件 ≤ 20MB 时内联 `base64`，
否则仅给服务端 `path`。两个投递面都不可达时消息丢弃并记 WARNING（审批提示等通知不阻塞）。
接收方按 `message_id` 去重。

## 给调用方的建议

- 短问答用同步模式；长任务（视觉分析、委派、工作流）用 `async_mode` + SSE 或回调。
- 持续画面推送走 `/api/vision/stream`（实时流）或 `/api/vision/push`（低频快照）；
  一次性看图问答走 `/api/chat` 的 `images`。
- 外部系统身份用稳定 `user_id`：历史与记忆按它隔离延续。

## AI 侧自感知

- http_api 会话内自动注入门户契约提示（reply_policy）：三级投递级联、同步等待窗口、
  投递面纪律，均按生效配置实时生成。
- 任意会话里 AI 都可调用 `http_api_portal_help` 工具获取完整接入契约（端点/字段/认证/
  收发级联，实时反映当前配置），对外讲解接入方式前先取它，不背默认值。
