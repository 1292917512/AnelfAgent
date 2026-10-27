"""HTTP API 门户的会话规则。

Model Experience: http_api 会话的每次回复看到门户语义（生效地址/认证形态）、完整收发
契约（入站端点、出站投递级联、媒体规则）与同步等待窗口纪律；不注入工具名单与召回覆盖。
Token effect: 每轮数百 token，换取不超时不空等、不误判投递面的纪律。
Cache effect: 经 channel_policy 尾部层注入，不触碰 stable 前缀。
"""

from agent.channel.reply_policy import ReplyPolicy

from .config import HttpApiConfig


def http_api_policy(config: HttpApiConfig) -> ReplyPolicy:
    """按生效配置生成 HTTP API 门户的会话规则。"""
    base = config.base_url
    ws_base = base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    auth = "x-api-token / Bearer 认证" if config.api_token else "回环免认证"
    outbound = (
        f"频道默认回调 {config.callback_url}；" if config.callback_url else ""
    ) + "调用方可在请求里带 callback_url 按会话注册"
    return ReplyPolicy(
        instructions=(
            "[HTTP API 门户契约]\n"
            f"本频道是外部系统与 Agent 的双向 HTTP 门户（生效地址 {base}，{auth}），"
            "对方是程序调用方而非聊天界面：回复文本原样进入 API 响应，直接产出最终内容，"
            "不写寒暄与过程自述。接入帮助用 http_api_portal_help 获取，"
            "默认概览，按需传 topic=chat/sessions/sdk/https。\n"
            f"入站：POST {base}/api/chat（message/user_id/group_id/session_id/images/media/"
            f"async_mode/callback_url）；POST {base}/api/vision/push 推送外部视觉源单帧，"
            f"WS {ws_base}/api/vision/stream 持续推流（每帧一条 JSON，系统内部判变注入）；"
            f"GET {base}/api/chat/stream?user_id=... 订阅 delta/tool_call/reply/turn_end 流式帧。\n"
            f"出站（Agent → 调用方）：同步响应 → SSE 订阅 → webhook 回调三级级联（{outbound}）。"
            f"同步调用（async_mode=false）阻塞等待最终回复最多 {config.reply_timeout} 秒："
            "窗口内能完成的工作直接答复；更长的任务先回复受理结论，并说明后续结果经流或回调送达，"
            "不要让调用方空等超时。\n"
            f"外部应用接入：先用 portal_help 获取真实配置，再读 {base}/api/sdk 与 /openapi.json；"
            "SDK 模型与已启用工具可直接调用；/api/sessions/control 的 stop 复用协作式停止，"
            "resume 向原会话提交新续接消息；/api/sessions/history 支持断线补取历史。"
            "不同用户、群组、子会话必须保持独立标识；集成令牌由可信后端保管。\n"
            "纪律：思考中途经 send_message 发出的进度消息不进同步响应，仅流订阅者或回调可见；"
            "无订阅者且无回调时主动消息返回投递失败；同会话并发同步请求相互取代，"
            "只认最新一条；含媒体的完整消息只经回调投递（结构化段，小文件内联 base64），"
            "不进同步响应体与 SSE 帧。回复文本里不要臆造未配置的地址与凭据。"
        )
    )
