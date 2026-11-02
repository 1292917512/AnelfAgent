# QQ 官方机器人

`qqbot` 使用 QQ 开放平台的 AppID 和 AppSecret。可与已有的 OneBot 频道 `qq` 同时启用。两者的账号、配置、消息 ID 和会话历史各自独立。

## 接入

1. 在 QQ 开放平台注册机器人，取得 AppID、AppSecret，并为测试用户、测试群及频道开通需要的场景和 API 权限。[QQ 官方接入指南](https://bot.q.qq.com/wiki/develop/api-v2/)
2. 在 Anelf Web 的「通道 → 通道管理」打开「QQ 官方机器人 → 配置与连接」，填写 AppID 和 AppSecret。也可以通过进程环境变量 `ANELF_QQBOT_APP_ID` 和 `ANELF_QQBOT_APP_SECRET` 提供凭证，页面会标明来源。配置文件示例见 [channel_config.example.json](channel_config.example.json)。
3. **已有公网 HTTPS 域名**时，可以保留默认的 Webhook。将回调路径反向代理到 Anelf Web 服务，保留原始请求体、签名头和 AppID 头，保持服务端时钟同步。在页面输入公网域名，点击「保存并连接」，再将「复制回调地址」得到的完整地址粘贴到开放平台，订阅需要的事件并验证地址。[事件订阅与验证](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/event-emit.html)
4. **本机部署或需要向子频道发消息**时，可选择 WebSocket。勾选机器人有权接收的事件，或直接编辑下方的「WebSocket 订阅掩码」，再点击「保存并连接」。默认接收单聊、群聊及频道 @。主机需能访问 QQ 网关，首次网关登录成功后状态转为「运行中」。[网关协议与 intents](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/event-emit.html)
5. 启动后，卡片上方显示机器人身份、运行状态和最近受理的事件。凭证、接入方式、自动启动、@ 规则和会话白名单可在同一卡片中直接修改。Webhook 回调地址可直接复制，右上角可打开 QQ 开放平台。点击「检查 QQ API」核对出站访问，再用测试账号私聊机器人或在群里 @ 它。事件时间更新后，从同一会话回复并核对 QQ 客户端收到的内容。每次启动频道都会清空这个时间，重复投递的旧消息不会触发更新。

## 会话与能力

使用事件里的平台 ID 或 `list_known_chats` 返回的目标。加入白名单时也使用下表中的完整目标；多个目标用英文逗号分隔，留空允许全部。白名单只控制入站。

| 场景 | Anelf 目标 | 消息能力 |
| --- | --- | --- |
| QQ 单聊 | `c2c.USER_OPENID` | 收发文本、图片、语音、视频、文件，回复及撤回 |
| QQ 群聊 | `group.GROUP_OPENID` | 同上，支持群 @、按权限查询群资料和分页成员，以及设置或解除成员禁言 |
| QQ 频道子频道 | `channel.CHANNEL_ID` | 文本、图片、回复、撤回、发表或移除表情回应、子频道信息 |
| QQ 频道私信 | `dm.DM_GUILD_ID.USER_ID` | 文本、图片、回复、撤回。使用私信事件的虚拟 Guild ID；发送者身份与私信会话分开保存 |

普通群消息和子频道消息在平台下发时进入历史。默认 `require_mention=true`，仅私聊、@ 或指向机器人的互动触发思考。好友变化、群成员进出、频道撤回/表情与消息审核结果作为事件写入历史。精确开通的事件由开放平台权限和订阅决定。

需要向频道子频道发消息时请选择 WebSocket 并确认网关在线，平台对子频道发送有在线状态要求。单聊和群聊可使用 Webhook。[子频道发送条件](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/channel/message/send.html)

使用 `qqbot_open_direct_message` 可从共同频道 ID 和成员 ID 获取私信目标，再通过 Anelf 的发送工具发消息。群成员禁言与消息撤回走 Anelf 敏感操作门控，平台也会检查机器人权限。

`qqbot_send_markdown` 可发送 Markdown 和键盘模板。`qqbot_upload_markdown_material` 将本地图片上传为当前机器人可复用的 `qqbot-media://` 素材；另有分页查询和删除工具。按钮和快捷菜单点击进入对应会话，使用事件外层 ID 回复，使用互动对象 ID 回应客户端。`qqbot_send_card` 在群聊或单聊发送图文卡片。群聊及子频道普通文本支持 Anelf 的 `[at_uid:ID]` 标记。[QQ 素材管理](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/material-management.html)

单聊/群聊媒体接受 HTTP(S) URL、本地路径、本机 file URI、Anelf 上传 URL，以及 `data:…;base64,…` 或 `base64://…` 内联媒体。小文件直接上传，大文件按平台预签名计划逐片 PUT 并确认。仅在同一分片可安全重放时重试 PUT；分片确认遇到平台错误码 `40093001` 时最多尝试三次。文件上限为 200 MiB，内联媒体和子频道本地图片上限为 20 MiB，Markdown 图片素材上限为 10 MiB。入站语音优先使用平台提供的 WAV 地址，ASR 文本标记为转写。[QQ 富媒体说明](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/rich-media.html)

## 官方文档与接入范围

| 官方接口族 | 适配器中的入口或边界 |
| --- | --- |
| [鉴权与 OpenAPI 错误](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/api-call-guide.html) | 自动刷新凭证，识别 `err_code` 与旧版 `code`，保留响应头或正文中的 Trace ID。异步审核和缺失发送回执返回 `outcome=unknown` |
| [消息与事件](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/overview.html) | 单聊、群聊、子频道、频道私信分别路由。在原会话受理引用、附件、卡片及聊天记录，频道生命周期变更进入通知中心。互动仅将按钮和快捷菜单作为用户操作受理 |
| [频道与子频道](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/users_me_guilds.get.html) | `qqbot_list_guilds` 分页查询频道，`qqbot_list_subchannels` 列出子频道。文字子频道支持精华消息、撤回和表情 |
| [群管理](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_join_request_list.get.html) | 群资料、成员分页和资料、机器人在群内的权限、成员禁言、入群事件与申请查询。审批工具要求同时提供群、申请人和申请 ID，走敏感操作门控 |
| [机器人管理](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_menu.get.html) | 可接收已配置菜单的点击。菜单/指令面板发布、群黑名单及自动审批策略配置不在工具面 |
| [流式单聊](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_users_user_openid_stream_messages.post.html)及频道专项接口 | 当前使用普通消息回执；单聊流式、论坛、日程、音频直播、频道和角色管理没有接入 |

账号开通、上架与授权以[开放平台 FAQ](https://bot.q.qq.com/wiki/bot_FAQ/)和[运营规范](https://bot.q.qq.com/wiki/business/)为准。[Agent 接入](https://bot.q.qq.com/wiki/agent-qqbot/)说明 OpenClaw 插件的部署方式，本频道独立实现 OpenAPI 协议。升级时先对照 [API v2 变更记录](https://bot.q.qq.com/wiki/develop/api-v2/changelog.html)，再用与当前机器人同权限的测试账号验证。

## 投递与排障

- Webhook 校验 AppID、Ed25519 签名及时间戳，再将事件提交给 Anelf。提交失败时释放去重键。两种接入方式共用有界去重，同一会话的消息 ID、消息索引或互动 ID 可识别重投。互动确认失败后，可单独补确认而不重复提交消息。同一频道实例停止再启动时保留同账号的记录，换 AppID 时清空。进程重启后历史判重由 Anelf 入站管线承担。
- WebSocket 将心跳和有序入站处理分开。心跳携带最后收到的序号；断线、心跳确认超时或入站积压达到上限时，Resume 携带最后成功受理的序号。无效会话重新 Identify。权限、封禁或分片配置错误进入故障状态，由频道看门狗处理。
- 文本与媒体优先使用显式回复目标，否则按当前思考轮的会话和原消息 ID 选择被动回复。跨会话发送走主动消息；直接调用 SDK 且没有思考轮时可使用本会话最近的有效消息。引用自己的出站消息使用回执的 `ext_info.ref_idx`。概述列出单聊窗口 60 分钟，单聊接口参数表也有 5 分钟的描述；以平台实际回执为准，过期拒绝后不会改成主动消息重发。[QQ 消息规则](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/overview.html) · [单聊接口](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_users_user_openid_messages.post.html)
- 状态页提供机器人 ID、接入方式和最近一次受理事件的时间。HTTP 401 触发一次令牌刷新；部分文档明确标注可重试的查询错误最多补查一次。发送写入后超时、审核中或缺少消息 ID 时，回执为未知并停止后续段。已送达的分段 ID 保留在回执中，先核对再补发。回复序号和可视引用索引保存在当前进程内，进程重启后对旧消息的再次回复由 QQ 校验。
- 保存新的凭证、接入方式或 intents 后，旧连接停止受理，运行中的频道由 Anelf 看门狗重启。停用看门狗时通过频道管理页停止并重新启动。入站白名单和 @ 策略在下一条消息生效。
- 当前实现面向单机器人、单分片，不提供论坛、音频直播、频道角色管理、全量历史同步和 C2C 流式回复。联调时应分别验证所使用的事件、权限、媒体格式和审核行为。

协议实现参考 [NoneBot QQ Adapter](https://github.com/nonebot/adapter-qq)。本频道直接使用项目已有的网络客户端、Ed25519 实现和频道消息管线。
