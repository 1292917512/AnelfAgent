# 频道与会话

频道适配器、配置统一接入、会话 scope、统一出站管道与出站防护的现状说明。修改 `channels/`、
`agent/channel/` 或消息投递链路时按需阅读；实时语音的频道能力见[实时语音分册](realtime-voice.md)。

## 频道适配器基础

- 目录自动发现 + 热插拔 `sync_channels`（对账范式，见[架构分册](architecture-reference.md)）
- 继承 BaseChannel，声明 `channel_id` / `display_name` / `capabilities` / `metadata` / `_Configs`；
  实现 `start` / `stop` / `forward_message` / `get_self_info` / `get_channel_info` /
  `health_check` 六个抽象方法。`send_text` 是经 `forward_message` 的默认便捷入口，平台可覆盖
- 频道经类属性 `display_order`（默认 100）自声明排序；`list_adapters` 按 (order, key) 排序
- 频道工具桥（`agent/channel/tool_bridge.py`）：`@channel_tool` 扫描注册 / 通用能力路由 /
  敏感门控 / 按频道接口开关 channel_tool_states
- 频道目录应自持有适配器、配置模型、协议、技能、前端与测试；插件负载、运行时数据和源码目录是
  不同对象：更新源码后必须通过该频道的安装/升级入口同步负载，不要把生成的运行时目录当作源码提交
- 非空 `forward_message` 请求仅在请求段已交给真实接收端后返回 `success=true`；不支持的段、缺失媒体路径、
  无可用投递端均须显式失败。部分段已送达时保留其 `message_ids`，不能把整条请求记为成功
- 使用 `_forward_via_segment_map` 时映射的 `send_*` 必须有频道实际实现；只继承基类便捷方法的
  段类型会显式失败，避免便捷方法再次进入 `forward_message`。单段发送异常不会阻断后续段

CLI 频道使用相同的配置模型与发现机制；`python -m channels.cli` 启用终端交互并关闭 WebUI，
其他已启用频道仍按配置启动。在普通服务中启用 CLI 只提供终端文本输出，不自动读取标准输入。

QQ 戳一戳保持平台事件类型：群聊由 `user_id` 标识发起者；NapCat 私聊的 `user_id` 是会话好友，
`sender_id` 才是发起者，`target_id` 是被戳者。事件正文分别标明双方账号和机器人自身，
昵称只作辅助，动作与附加文案不作为身份依据。私聊归属仍为会话好友，不因机器人发起互动而切换。

平台事件的会话信封不等于行为人：私聊系统事实沿用原会话对端，群事件留在原群；实际发起者、
操作目标与来源由频道正文明确表达，缺失身份保持未知。微信桥的撤回与系统消息遵循同一规则；
AcFun 直播事件携带行为人 UID 与目标主播 UID，通知中心保持聚合收件箱语义。
GitHub push 只合并同分支、同一已知发起者的事件，避免将不同人的操作记到同一人名下。

`kind` 必须在连续元数据前缀内，先于引用预览与外部正文；正文中的类标签不改变事件类型。
非聊天事件保留原有入队与唤醒策略，但不计入用户发言次数、群发言者列表或首次用户画像任务。
内部后台通知的待处理信号标为 `system`，保留此前用户请求的时间水位，避免掩盖未处理对话。

http_api 频道是外部系统的双向 HTTP 门户（默认 127.0.0.1:8091，停用）：入站 /api/chat
（文本 + images + media base64 落盘为媒体段）、/api/vision/push 与 WS /api/vision/stream
（视觉单帧/持续推流，与 web 端共用 `push_external_frame`，判变注入由系统内部完成）、
/api/chat/stream（SSE 帧）；出站按 同步响应 → SSE 订阅 → webhook 回调（请求级 callback_url
注册优先，http_api_callback_url 兜底）三级级联，含媒体的消息整体只经回调投递
（≤20MB 内联 base64）；没有实际接收端时出站返回失败。
报文契约见 channels/http_api/README.md；频道内会话规则经 reply_policy 注入，完整接入契约
由 `http_api_portal_help` 频道工具按生效配置实时生成供 AI 自取。

## 频道回复策略（agent/channel/reply_policy.py）

频道通过 `reply_policy` 声明直达回复、初始工具目录、操作规则与 `recall_policy` 默认召回策略。
只有运行中的频道声明生效；内核不引用具体频道。召回优先级是消息显式策略 → SDK
`recall_options` → 频道默认 → 全局配置，生命周期由 `recall_turn` 管理，按会话隔离。
低延迟频道可声明 `RecallPolicy(level="off", background=False)`，保留永久纪律、人设、
会话历史和实时执行上下文，并跳过自动检索；AI 仍能手动调用 `recall` 或调整本轮召回强度。
召回策略不改写稳定前缀，不另设精简开关。

普通会话默认通过 `send_message` 显式投递，不经过 Jev 正文审核，以目标频道的成功回执确认送达。
提示词不把普通正文列为回复方法；自动投递属于系统兜底，不能当作 AI 已回复的依据。
已送达后直接 `end_reply`，不再输出同一份正文或结束话术；失败处理原因，结果未知先核对会话。
`mind/core` 的 `send_interim_text`（默认关闭）控制工具调用前与后台等待中的正文，
`send_final_text`（默认开启）控制独立最终正文；两个开关热生效、互相独立，不影响发送工具。
开启的自动正文共用内核审核和统一发送管道，默认正常投递；Jev 只有一致、高置信度地判定为内部内容、
重复回复或错误目标时才拦截。超时、不可用、输入超预算或结论不确定不阻断回复。
投递开关、无频道/内部事件、结束备注和发送回执去重仍先于审核生效，审核放行不等于频道已送达。
同批已有发送工具时不额外自动发送正文；工具结果、钩子产出或结束备注不变成用户消息。
`handoff_to_events` 只接受本轮真实工具回执，交给频道事件续报时保持静默收束。

## 频道配置统一接入（agent/channel/config.py）

频道配置与全系统同一套注册与读写面，值文件留在频道目录（模块自持有、可插拔）：

- **声明约定**：`channels/<id>/config.py` 暴露标准符号 `CONFIG_MODEL`（ChannelConfig 子类，
  pydantic 模型即唯一声明源；`Field(description=...)` + `json_schema_extra` 直通
  value_type/options/advanced/unit/min/max/step/tag；`Literal` 注解自动转 ENUM）。适配器
  `from .config import XxxConfig`，**禁止在 adapter.py 再定义配置类**（双份真相）
- **键前缀与值存储**：统一配置面的键为 `<id>_<field>`（组 `adapter/<id>`），值存
  `channels/<id>/channel_config.json`（文件内字段名无前缀）——`ChannelConfigStore` 经
  `ConfigManager.register_store(<id>_, store)` 接入，get/set/has/save 自动路由，app_config.json
  不存频道键；env 覆盖 `ANELF_<ID>_<FIELD>` 由 store 读取时生效
- **注册时机**：bootstrap `register_channels` 节点先调 `register_channel_schemas()`——扫描
  channels/ 注册各频道 store + schema（仅子类声明字段，基类通用字段不进配置面），幂等
- **热更双路径**：进程内写入（Web /config/meta、AI update_entity_config、频道内部
  `set_channel_config`）经 ConfigManager.set_persisted 批量校验和持久化后通知前缀监听器；手工编辑
  channel_config.json 经 ConfigWatcher mtime 轮询 → store diff → `ConfigManager.notify_external`
  上报同一批监听器（无 diff 不重复触发）
- **频道内部写配置**：一律 `set_channel_config(<id>, field=value)`（登录回填/直播开关等），禁止
  直写文件；频道需要对变更做 diff 应用时覆盖 `_on_config_changed`（参考 acfun/bilibili 委托
  reload_config 的写法）

集合配置保持 JSON 数组/对象类型。一次提交先校验完整的频道模型（含跨字段约束），通过后
原子写入文件，再发布变更；校验失败不改文件、不发布半份配置。无效的外部文件或运行时热更
保留上一份有效配置并报告错误，首次启动无有效配置则明确失败。重复注册 schema 同时替换文件
监听回调，确保文件变更作用于当前存储。

频道启停由 ChannelManager 按频道串行化，重复启动不创建第二条连接。启动取消会清理已分配
资源并进入 ERROR；主动停止意图先于等待锁登记，看门狗在锁内复核实例与意图，不能把正在
停止或已经移除的频道重新拉起。停止失败不会注销实例，Web 与同步接口如实返回失败。

## 会话 scope（agent/messages/everything.py）

- 格式：`user_{adapter}:{uid}` / `group_{adapter}:{gid}` / `user_{adapter}:{uid}#{chat_id}`——
  entity_scope 含频道 adapter 维度，跨频道同号实体（如 QQ uid 与 WebUI uid）天然隔离
- 构造一律 `build_entity_scope()`，解析一律 `parse_entity_scope()`（返回
  scope_type/adapter/base_id/session_id，兼容无 adapter 旧格式），会话合法性判据
  `is_conversation_scope()`（可路由 = user_/group_ 且含频道前缀），**禁止手工 f-string 拼接**
- 元决策 decide 的 target 一律经 `decisions.normalize_target_scope` 规范化后才可用
  （见[思维分册](mind-architecture.md)）
- 记忆标签同构：`user:{adapter}:{uid}`；存量数据由 `agent/storage/scope_migrate.py` 启动自动
  迁移（`legacy_adapter_default` 配置归属频道）；别名实体的跨频道历史合并由 `alias_merge_history`
  配置（默认开）
- 声纹标签 `speaker_scope` → 实体召回的 scope 方言转换单点在 `recollection._speaker_scopes`
  （音频库绑定格式 `user:qq:123` ↔ 思维层 scope `user_qq:123`，见[声纹分册](audio-voiceprint.md)）

## 统一出站管道与出站事实面

所有 AI 出站（手写 output 工具 / `send_text_to_channel` 自动正文投递 / tool_bridge 发送类工具
（`@channel_tool(outbound=True)` + 发送类 capability）/ 决策执行器 PROACTIVE 与工具操作结果投递）
统一经 `execute_send_action`（校验/目标解析/结果归因；entities 经 `_sdk` 导出）：

| 通道 | 机制 | 说明 |
|------|------|------|
| 历史固化（本会话事实） | `execute_send_action` 的 `record_content` | 发送成功以 assistant 角色写入对话历史，该会话的后续回复周期拉历史即见「已送达」 |
| 动态注入（跨会话事实） | `note_outbound` 登记 + context provider「outbound_facts」（priority 32，全局常驻） | 每轮把**其他会话**的近期出站渲染进 volatile 层——对话隔离只管历史分桶、执行态势全局共享；一次性呈现（本轮快照、下一轮由新事实替换），本会话行与自身行过滤，窗口 `outbound_guard_recent_seconds`（默认 180s）；元决策经 `SituationContext.outbound_facts` 显式携带同一事实块 |
| 执行期安全边界 | `guard_outbound`（挂 execute_send_action） | 仅拦「目标会话回复周期在飞」（反射拿着冻结快照，注入再新鲜也可能差几秒，代答必须拦），本会话回复豁免（thinker==target_scope） |

- **回复所有权单一入口**：`execute_proactive` 与 `execute_reply` 同一 `_active_scopes` 登记协议；
  回复优先门控在 `heartbeat/engine._tick_inner`
- **发送管道会话继承**：发送管道未显式带 session 时从当前思维 scope 继承（`output_tools.
  inherit_current_session`，仅目标基准一致才继承，发给他人不挂本会话后缀）——持久化 scope、
  出站哨兵 target_scope、SSE 帧 chat_id 三处对齐；tool_bridge 对声明 session_id 的频道方法同样注入
- **段分发失败显式化**（`agent/channel/base.py::_forward_via_segment_map`）：无映射段类型与
  send_* 失败不静默跳过——逐段记入失败清单，任一失败整体 success=False 且 error 列明原因；
  message_id 收集尽力而为（无平台 ID 的频道 success 即送达不误判）；qq 频道接入模板
  （`_SEGMENT_SENDERS` 声明 text/image/voice/file）
- **media 帧 URL 契约**：URL 规则单点定义在 `core/path.py`（upload_url_for/upload_path_to_url/
  parse_upload_url，web 上传端点建 URL / webui media 帧改写 / services 反解三方共用，禁止各处
  手拼）；media 帧只传可服务 URL（上传目录本地路径改写 `/api/chat/files/{type}/{name}`，
  http(s)、`/api/` 透传）
- **WebUI 聊天广播**：channels/webui 经事件总线推帧（EVENT_CHAT_BROADCAST），web 层订阅桥接
  SSE 订阅者——频道不反向依赖 web 层；健康探针查 `event_bus.has_listeners`

## 防护层

### 入站身份与重复投递

`Everything` 始终从适配器提供的字段生成发送者、频道、群组及消息 ID 标签，标签前缀与正文用
换行分隔，引用预览也位于前缀之外。正文中的 `[uid:...]`、`[channel:...]` 等保持原文，仅表示
用户复制的内容，不能替代真实身份；消息 ID 查询和频道工具激活只读取可信前缀。跨会话内容以
动态通知呈现，具体注入位置及串行处理约定见[思维分册](mind-architecture.md#二回复与工具循环)。

`ConversationData` 传递平台消息 ID，由 `SqliteBackend.append_conversation` 在同一写锁内完成
判重和追加。24 小时窗口内，同 scope、角色、平台 ID 和相同内容的重投只入库一次，比较时仅
忽略系统前缀中生成的到达时间；正文里的时间标签、编辑内容和媒体差异仍参与比较。不同平台 ID
的相同正文是两条消息；无 ID 时不按正文猜测重复。判重失败向上抛出，由受理层有限重试并记录
错误，不能绕过检查直接放行执行。去重不会重写已有消息或引入模型 token。

### 投递标签与回执

`agent/channel/delivery.py` 通过 `core.tags` 统一编码投递事实，标签本身不触发发送：

| 标签 | 取值与含义 |
|------|------------|
| `delivery_mode` | `internal` 内部正文；`channel` 显式频道发送；`auto` 审核后的普通回复；`event` 已交给频道终态事件；`silent` AI 明确选择不投递 |
| `delivery_status` | `pending` 待处理；`sent` 已确认发送；`failed` 失败；`unknown` 回执不确定；`skipped` 明确跳过 |
| `delivery_scope` | 规范会话 scope，包含频道、用户/群与子会话维度 |

发送工具经 `execute_send_action` 返回 `delivery` 标签串。频道必须返回明确的 `success=true`
或 `ok=true`，且不存在错误或 `outcome=unknown`，才能登记回执并写入已发送历史；空结果、
非结构化结果与仅表示排队的结果均为未确认，不应盲目重发。

默认频道由 `think_session` 按当前 scope 绑定，内部反思绑定为空；嵌套执行退出时恢复外层绑定。
不从全局待处理队列猜测频道，跨会话动态感知也不改变路由。`list_sessions` 的 `space` 区分
`internal` 与 `conversation`，内部空间没有默认收件人。

当前思维轮次只认可发送管道登记的同 scope 回执；其他会话、嵌套反思、已结束执行的迟到发送
不会完成本轮答复。模型正文中的标签不构成回执。工具结果裁剪保留投递字段，出站清洗则移除
这些元数据标签。未发送正文的结束判定见[思维分册](mind-architecture.md#end_reply-与产出语义)。

### 出站约束

| 机制 | 位置 | 说明 |
|------|------|------|
| 跨执行体双答防护 | 回复优先门控 + 出站事实面（见上） | 主思维串行，当前会话历史按水位逐轮并入；独立子代理和钩子的出站事实经动态注入与发送边界共同约束 |
| 空会话防护与整合 | `outbound_guard.guard_empty_conversation`（出站层）+ `decisions`（决策层前置判定）+ `sqlite_backend.sweep_empty_conversations`（存量整合，心跳周期触发） | 空会话 = 从未收到过 user 角色消息的会话（AI 主动搭话历史缺陷与一次性通知写入产生）。三层：①出站层——AI 思维上下文向空会话发送返回结构化错误（guard=empty_conversation，hint 引导等待对方先开口或改记待办）；②决策层——PROACTIVE 与工具操作结果投递在进入回复周期前判定空会话直接放弃（心跳日志留痕）；③存量整合——按 `conversation_empty_sweep_interval_seconds`（默认 86400s，0=关）清除空会话的消息/摘要/回复检查点（1h 宽限窗口内的新会话不动，NOT EXISTS 单语句守卫防与首条用户消息竞态）。判定事实源 `conversation_has_user_message`；查询失败 fail-open（行为护栏而非安全边界）。配置 `channel/outbound` 组 `outbound_guard_empty_enabled` |
| 待回复队列毒丸防护 | `is_conversation_scope`（单点判据）+ `PrefrontalCortex.enqueue_reply`/`add_reminder`（校验）+ `pop_next_reply_target`（就地清除）+ `pfc.consume_scope_task`（双队列消费） | 三层防线：①源头——`add_reminder` 拒绝持久化不可路由 scope 的提醒（ValueError，事件降级为不提醒）；②入口——`enqueue_reply` 拒绝非会话 scope 入队（退化为全局短期记忆桶）；③兜底——回复消费点对解析失败或缺频道前缀的队列条目就地清除 + WARNING，任何坏条目最多空转一轮即收敛。投递面守卫（待回复队列/持久化提醒/一次性通知）统一拒绝不可路由 scope |

配置组 `channel/outbound`（enabled / recent_seconds / facts_inject / empty_enabled）。

## GitHub 频道（channels/github/）

仓库动态监听频道：GitHub 事件规范化后走标准入站消息链推送给 AI，并提供 `github_*`
工具面反查与回写。设计取舍见 `projects/anelf-github-channel-plan.md`。

- **接收双模**：`poll`（默认，零公网依赖——Events API + releases/latest 的 ETag 条件请求
  轮询，304 不占限流配额）与 `webhook`（可选，需公网/隧道——HMAC-SHA256 验签
  **fail-closed**（不配 `webhook_secret` 拒绝启动）+ delivery id 去重环 + 有界队列单
  worker 保序，立即 200 规避 GitHub 10s 超时重投）；`both` 并存。webhook 端点经
  `build_router()` 挂 `/api/channels/github/webhook` 并声明 `@self_authenticated`
- **轮询可靠性**：首次轮询只播种不重放历史（PollCursorStore 语义）；游标/ETag 落数据目录
  （`channel_data_dir("github")`，原子写）重启续传；401/404 暂停该仓并向其会话注入一条
  告警（唤醒 AI 转告主人，不轰炸）；限流读 `Retry-After`/`X-RateLimit-Reset` 全局暂停；
  连续 5 次失败熔断 30 分钟；剩余配额低于 `rate_limit_reserve` 暂停轮询
- **事件管线**（`pipeline.py`，两模汇合点）：订阅匹配（`*`/事件族/精确 action 三档）→
  渲染（`events.py` 渲染器注册表 + 未知事件 generic 降级，不做 codegen）→ 优先级调整
  （`priority_boost` / `watch_mentions_of` @提及升级 / `quiet_hours` 降级）→ 分档路由：
  IMMEDIATE 立即唤醒（先冲刷该仓防抖缓冲保时序）、NORMAL 防抖窗聚合（同窗同分支 push
  合并、达 `aggregate_max_events` 拆条）、DIGEST 只计数进缓冲（不写历史，防噪音）
- **会话形态**：每仓一个 GROUP 会话 `group_github:{owner}/{repo}`（历史/记忆按仓隔离）；
  AI 在该会话的回复经 `reply_relay`（`adapter:target`，默认 `webui:web_user`）转述主人
  频道，未配置则如实失败——GitHub 无会话可回，回写是写工具的职责
- **注入防护**：issue/PR/评论正文是外部不可信内容——注入消息一律包裹「不可信」分隔块、
  截断（`body_digest_chars`）并剥图片语法（防 tracking pixel）；AI 因事件诱发的写操作
  走审批门（可按 `channel_id="github"` 配规则）
- **鉴权三形态**：PAT（主，登记 `provider_keys.json` 的 `github` 条目，不落频道配置文件）/
  GitHub App（`app_auth.py`：JWT RS256 缓存 8min → installation token 缓存至 expires_at-60s，
  pyjwt 既有依赖）/ 匿名（60 req/h，轮询间隔自动抬至 900s）
- **工具面**（`tools.py`，组 `github`）：读组 12 个（事件/issue/PR/commit/diff/CI/release/
  搜索/配额/digest/订阅清单）AUTO_ALLOW；写组 4 个（评论/开 issue/打标签/关闭）`sensitive`
  + `allow_write` 配置门控（默认关）；`react` 表情回应敏感但不受 allow_write 限制；
  订阅自助 `subscribe_repo/unsubscribe_repo`（AI 自行增删监听仓，写配置走
  `set_channel_config` 唯一入口，热更生效）。不提供 merge/push/release/delete 高危写
- **每日巡检**：`github_watch.example.json` 复制到 `config/tasks/`（gitignored）即启用
  定时 digest 任务（AI 读 `github_get_daily_digest` 汇总静默事件，有事通知无事记心跳日志）
- **测试**：`channels/github/tests/`（隔离纪律：状态存储类构造接受 `directory` 参数，
  测试一律落临时目录，绝不写真实数据目录）
