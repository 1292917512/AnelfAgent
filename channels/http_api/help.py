"""频道帮助：默认概览，细节按主题获取；与门户运行配置使用同一声明源。

模型看到：portal_help 返回按主题裁剪的接口与示例，参数细节指向 OpenAPI。
Token：概览仅返回接入步骤和地址，需要时再读取指定主题。
缓存：只产生按需工具结果，不修改稳定前缀。
"""

from __future__ import annotations

from typing import Any, Literal

from .config import HttpApiConfig

HelpTopic = Literal["overview", "chat", "sessions", "sdk", "https"]


def build_portal_spec(config: HttpApiConfig) -> dict[str, Any]:
    """机器可读的完整门户契约，不包含认证令牌。"""
    cfg = config
    base = cfg.base_url
    ws_base = base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    return {
        "api_version": "1",
        "channel": "http_api",
        "purpose": "外部系统与 Agent 的 HTTP/HTTPS 门户：消息、媒体、视觉、会话控制、历史、模型 SDK 与系统工具",
        "base_url": base,
        "auth": {
            "required": bool(cfg.api_token),
            "how": "请求头 x-api-token 或 Authorization: Bearer；SSE/WS 可用 ?token=" if cfg.api_token
                   else "未配置令牌（仅回环监听可用）",
        },
        "inbound": {
            "chat": {
                "method": "POST", "path": f"{base}/api/chat",
                "fields": {
                    "message": "消息文本（≤256K 字符，与附件至少其一）",
                    "user_id / group_id / session_id": "身份与会话归属（历史/记忆按它隔离延续）",
                    "kind": "chat（默认）/ event / system → 历史 [kind:xxx] 标签；"
                            "notification → 聚合进通知中心会话（强制异步受理，无同步回复）",
                    "images": "图片 [{url|base64, mime_type}]，落盘后进视觉通道",
                    "media": "非图片媒体 [{kind: audio|video|file, base64, mime_type, file_name}]，≤50MB 落盘为媒体段",
                    "reply_to_id / reply_content": "引用回复（可选）",
                    "async_mode": "false 同步阻塞等回复（≤reply_timeout 秒）；true 立即返回 accepted",
                    "callback_url": "携带即注册本会话出站回调（http/https）",
                    "message_id": "幂等键：重复 id 返回 duplicate 不重复入管道",
                },
                "status": "ok / accepted / timeout / duplicate / error",
            },
            "vision_push": {
                "method": "POST", "path": f"{base}/api/vision/push",
                "fields": {"source": "源名", "image_base64": "png/jpeg/webp ≤20MB", "captured_at": "可选时间戳"},
                "note": "单帧推送，缓冲记 external:<source>，与本地捕获同判变同注入",
            },
            "vision_stream": {
                "method": "WS", "path": f"{ws_base}/api/vision/stream",
                "fields": "每帧一条 JSON 文本（同 vision_push 字段）",
                "note": "持续推流入口：实时画面帧逐帧上行，系统内部完成判变/缓冲/注入；每帧回 ack",
            },
            "sse": {
                "method": "GET", "path": f"{base}/api/chat/stream?user_id=<id>",
                "note": "user_id 或 group_id 选择会话；session_id 非空时精确订阅，否则订阅该身份全部子会话",
            },
            "execution": {
                "method": "POST", "path": f"{base}/api/sessions/control",
                "fields": "{user_id, group_id?, session_id?, action: stop|resume, run_id?, request_id?}",
                "note": "stop 复用 SDK 协作式中断；resume 提交新续接消息，不重放工具；run_id 仅用于精确停止",
            },
            "history": {
                "method": "GET", "path": f"{base}/api/sessions/history",
                "query": "user_id / group_id / session_id；limit=1..200；before_id 或 after_id 翻页",
                "note": "SSE 重连后用 after_id 补取持久化消息；不重放过程 delta",
            },
            "sdk": {
                "discovery": f"{base}/api/sdk",
                "models": f"{base}/api/sdk/models",
                "tools": f"{base}/api/sdk/tools",
                "openapi": f"{base}/openapi.json",
            },
            "health": {"method": "GET", "path": f"{base}/health"},
        },
        "outbound": {
            "cascade": ["同步响应（pending 请求）", "SSE 订阅 reply 帧", "webhook 回调 POST"],
            "callback_url": cfg.callback_url or "未配置默认回调（请求可带 callback_url 按会话注册）",
            "payload": "{event, chat_id, channel_type, scope, message_id, segments, ts, session_id?}；媒体段 ≤20MB 内联 base64，否则仅 path",
            "delivery": "含媒体消息整体经回调；无订阅者或可用回调时返回投递失败",
        },
        "limits": {
            "reply_timeout_s": cfg.reply_timeout,
            "media_max_mb": 50, "vision_frame_max_mb": 20, "callback_inline_max_mb": 20,
            "attachments_max": 9, "message_max_chars": 256_000, "body_max_mb": 80,
            "stream_queue_frames": cfg.stream_queue_size,
            "stream_subscribers": cfg.max_stream_subscribers,
            "connections": cfg.max_connections,
            "sdk_calls": cfg.sdk_concurrency,
        },
        "integration": {
            "discover_first": "GET /api/sdk 获取生效契约；GET /openapi.json 获取参数类型、必填项和默认值；/docs 可交互调试",
            "identity": "外部应用稳定分配 user_id；群聊传 group_id；多个对话传 session_id；user_id/group_id 不得包含 #",
            "chat_example": {"user_id": "my_app:alice", "session_id": "support-1", "message": "你好",
                             "message_id": "my-app-msg-1", "async_mode": True},
            "stream_example": f"{base}/api/chat/stream?user_id=my_app:alice&session_id=support-1",
            "stop_example": {"user_id": "my_app:alice", "session_id": "support-1", "action": "stop"},
            "resume_example": {"user_id": "my_app:alice", "session_id": "support-1", "action": "resume",
                               "request_id": "resume-1"},
            "tools": "GET /api/sdk/tools?group=... 自动发现已启用工具；POST /api/sdk/tools/{name}，正文 {user_id, group_id?, session_id?, arguments, request_id}；复用权限与 hook",
            "models": "POST /api/sdk/models/{operation} 的参数由 ModelAPI 签名生成；二进制字段采用 base64；不自动运行工具或继承对话",
            "trust": "单一集成令牌有系统工具权限；user_id 是调用方身份声明，不是多租户授权凭据。令牌由可信外部后端保管",
        },
        "reliability": {
            "idempotency": "消息和工具 request_id 在进程内去重：300 秒、共享容量 2000 条；重启或容量淘汰后不保证去重，非持久化 exactly-once",
            "stream": "SSE 是实时流，无断线重放；慢客户端队列溢出关闭连接，用 history 校准最终消息",
            "stop": "status=ok 表示停止请求受理；turn_end.reason=interrupted 才代表该轮收束；resume 是新输入，不重放工具",
            "retry": "timeout 不表示未执行；先查历史/执行回执。副作用工具无自动重试；同一 request_id 的 duplicate 不包含旧结果",
            "compatibility": "api_version=1；/api 路径保持兼容，客户端忽略未知字段；HTTP 401/403/404/413/422/502/503 表示传输或接口错误，/api/chat 保留业务 status",
        },
    }


def build_portal_help(config: HttpApiConfig, topic: HelpTopic = "overview") -> dict[str, Any]:
    """默认只给接入步骤与入口；主题帮助保留对应的规则和示例。"""
    spec = build_portal_spec(config)
    common = {key: spec[key] for key in ("api_version", "channel", "base_url", "auth")}
    if topic == "overview":
        return {
            **common,
            "steps": ["外部应用读取 GET /api/sdk 与 /openapi.json；/docs 可调试",
                      "固定分配 user_id；群聊传 group_id，多窗口传 session_id",
                      "长任务先订阅 SSE，再以 async_mode=true 发消息；断线后查询 history"],
            "inbound": {key: {name: value for name, value in item.items() if name in {"method", "path", "discovery"}}
                        for key, item in spec["inbound"].items()},
            "outbound": {key: spec["outbound"][key] for key in ("cascade", "callback_url")},
            "topics": {"chat": "消息、媒体、SSE", "sessions": "停止、继续、历史",
                       "sdk": "模型和系统工具", "https": "证书和外部地址"},
            "trust": spec["integration"]["trust"],
        }
    if topic == "chat":
        return {**common, **{key: spec["inbound"][key] for key in ("chat", "sse", "vision_push", "vision_stream")},
                "example": spec["integration"]["chat_example"], "limits": spec["limits"],
                "outbound": spec["outbound"], "retry": spec["reliability"]["retry"]}
    if topic == "sessions":
        return {**common, "control": spec["inbound"]["execution"], "history": spec["inbound"]["history"],
                "stop": spec["integration"]["stop_example"], "resume": spec["integration"]["resume_example"],
                "semantics": spec["reliability"]["stop"]}
    if topic == "sdk":
        from .sdk_routes import MODEL_OPERATIONS
        return {**common, **spec["inbound"]["sdk"], "model_operations": list(MODEL_OPERATIONS),
                "models": spec["integration"]["models"], "tools": spec["integration"]["tools"],
                "idempotency": spec["reliability"]["idempotency"], "retry": spec["reliability"]["retry"]}
    if topic == "https":
        return {**common, "native": "配置 ssl_certfile、ssl_keyfile；加密私钥另配 ssl_keyfile_password，重启频道生效",
                "proxy": "代理终止 TLS 时填写 public_base_url；代理需支持 SSE 无缓冲和 WebSocket 转发",
                "auth": "公网监听或设置 public_base_url 必须配置 api_token；调用方验证服务端证书",
                "cors": "可信浏览器应用按需配置 cors_origins；系统令牌保管在可信后端"}
    raise ValueError("未知帮助主题；请选择 overview/chat/sessions/sdk/https")
