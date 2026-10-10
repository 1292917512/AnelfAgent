# 频道与会话

频道适配器、配置统一接入、会话 scope、统一出站管道与出站防护的现状说明。修改 `channels/`、
`agent/channel/` 或消息投递链路时按需阅读；实时语音的频道能力见[实时语音分册](realtime-voice.md)。

## 频道适配器基础

- 目录自动发现 + 热插拔 `sync_channels`（对账范式，见[架构分册](architecture-reference.md)）
- 继承 BaseChannel，必需接口：channel_id / display_name / capabilities / start / stop / send_text
- 频道经类属性 `display_order`（默认 100）自声明排序；`list_adapters` 按 (order, key) 排序
- 频道工具桥（`agent/channel/tool_bridge.py`）：`@channel_tool` 扫描注册 / 通用能力路由 /
  敏感门控 / 按频道接口开关 channel_tool_states
- 频道目录应自持有适配器、配置模型、协议、技能、前端与测试；插件负载、运行时数据和源码目录是
  不同对象：更新源码后必须通过该频道的安装/升级入口同步负载，不要把生成的运行时目录当作源码提交

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
  `set_channel_config`）经 ConfigManager.set 命中前缀监听器即时热更；手工编辑
  channel_config.json 经 ConfigWatcher mtime 轮询 → store diff → `ConfigManager.notify_external`
  上报同一批监听器（无 diff 不重复触发）
- **频道内部写配置**：一律 `set_channel_config(<id>, field=value)`（登录回填/直播开关等），禁止
  直写文件；频道需要对变更做 diff 应用时覆盖 `_on_config_changed`（参考 acfun/bilibili 委托
  reload_config 的写法）

## 会话 scope（agent/messages/everything.py）

- 格式：`user_{adapter}:{uid}` / `group_{adapter}:{gid}` / `user_{adapter}:{uid}#{chat_id}`——
  entity_scope 含频道 adapter 维度，跨频道同号实体（如 QQ uid 与 WebUI uid）天然隔离
- 构造一律 `build_entity_scope()`，解析一律 `parse_entity_scope()`（返回
  scope_type/adapter/base_id/session_id，兼容无 adapter 旧格式），会话合法性判据
  `is_conversation_scope()`（可路由 = user_/group_ 且含频道前缀），**禁止手工 f-string 拼接**
- 元决策 decide 的 target 一律经 `decision_executor.normalize_target_scope` 规范化后才可用
  （见[思维分册](mind-architecture.md)）
- 记忆标签同构：`user:{adapter}:{uid}`；存量数据由 `agent/storage/scope_migrate.py` 启动自动
  迁移（`legacy_adapter_default` 配置归属频道）；别名实体的跨频道历史合并由 `alias_merge_history`
  配置（默认开）
- 声纹标签 `speaker_scope` → 实体召回的 scope 方言转换单点在 `recollection._speaker_scopes`
  （音频库绑定格式 `user:qq:123` ↔ 思维层 scope `user_qq:123`，见[声纹分册](audio-voiceprint.md)）

## 统一出站管道与出站事实面

所有 AI 出站（手写 output 工具 / `deliver_text` 纯文本投递 / tool_bridge 发送类工具
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

| 机制 | 位置 | 说明 |
|------|------|------|
| 跨周期双答防护 | 回复优先门控 + 出站事实面（见上） | 思维周期消息链在启动时刻冻结（对话历史周期内不重读），并发周期互相看不到对方出站——三通道分工：历史固化/动态注入/执行期边界 |
| 空会话防护与整合 | `outbound_guard.guard_empty_conversation`（出站层）+ `decision_executor`（决策层前置判定）+ `sqlite_backend.sweep_empty_conversations`（存量整合，心跳周期触发） | 空会话 = 从未收到过 user 角色消息的会话（AI 主动搭话历史缺陷与一次性通知写入产生）。三层：①出站层——AI 思维上下文向空会话发送返回结构化错误（guard=empty_conversation，hint 引导等待对方先开口或改记待办）；②决策层——PROACTIVE 与工具操作结果投递在进入回复周期前判定空会话直接放弃（心跳日志留痕）；③存量整合——按 `conversation_empty_sweep_interval_seconds`（默认 86400s，0=关）清除空会话的消息/摘要/回复检查点（1h 宽限窗口内的新会话不动，NOT EXISTS 单语句守卫防与首条用户消息竞态）。判定事实源 `conversation_has_user_message`；查询失败 fail-open（行为护栏而非安全边界）。配置 `channel/outbound` 组 `outbound_guard_empty_enabled` |
| 待回复队列毒丸防护 | `is_conversation_scope`（单点判据）+ `scheduler.enqueue_scope_reply`/`add_reminder`（校验）+ `pop_next_reply_target`（就地清除）+ `work_memory.consume_scope_task`（双队列消费） | 三层防线：①源头——`add_reminder` 拒绝持久化不可路由 scope 的提醒（ValueError，事件降级为不提醒）；②入口——`enqueue_scope_reply` 拒绝非会话 scope 入队（退化为全局短期记忆桶）；③兜底——回复消费点对解析失败或缺频道前缀的队列条目就地清除 + WARNING，任何坏条目最多空转一轮即收敛。投递面守卫（待回复队列/持久化提醒/一次性通知）统一拒绝不可路由 scope |

配置组 `channel/outbound`（enabled / recent_seconds / facts_inject / empty_enabled）。
