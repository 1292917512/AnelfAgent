# 安全与审批

权限规则、审批审计、风险分层与内容安全的现状说明。修改 `agent/approval/`、`agent/security/`、
`core/sanitizer.py` 或权限配置时按需阅读。

## 权限与 Guardian 评审

- 权限配置只有 `config/permission_rules.json`（参考 `config/permission_rules.example.json`）：
  `allow` 直接执行，`deny` 拦截；辅助评审开启时，`ask` 由 Guardian AI 裁决，**CRITICAL 工具
  在无显式规则覆盖时自动评审**。辅助评审默认关闭，ask/CRITICAL 直接执行，不调用模型、不读取
  评审历史、不追加关闭提醒或高频审计；凭据使用提醒由工具结果提供。规则拒绝始终优先于放行；
  用户 ID 必须限定频道，群聊 ID 不是操作者身份
- Guardian 不可用时按自主策略继续执行，原因进入审计和执行任务的 AI 上下文，不创建人工会话或发送
  频道通知；权限规则本身损坏或检查故障则拦截本次调用，不能借此绕过禁止规则
- 配置中心 `approval/guardian` 的 `approval_guardian_backend` 可选 `sdk`（默认，系统 Jev）
  或 `llm`（普通聊天模型）。SDK 模式仅使用已配置的原生 Jev，不回退普通聊天模型；未配置、
  停用、超时或无效答案默认通过。只有有效危险分类的选中概率达到 0.8 才拒绝，不确定时放行并提醒
- `approval_guardian_model` 与思考档位只作用于 `llm` 通道；SDK 通道复用系统 Jev 配置。
  思考档位使用系统统一七级下拉；开启后的总时限默认 60 秒，覆盖历史读取与模型调用，
  调整评审配置会重置熔断
- 旧的人工批准 API、频道批准命令、会话放行、累计人工信任和 `approval_policies.json` 已移除；
  存量审计记录保留可读

## 审批审计持久化（agent/approval/audit.py + approval_audit 表）

规则拒绝、Guardian 放行或拒绝、启用评审后的不可用放行及权限求值错误写入账本；
常态 `rule_allow`（含主动关闭辅助评审后的直接执行）不记高频审计。历史人工审批记录保留可读，
不再累计人工信任或产生人工会话。`/approvals/history` 按 offset 与 tool_name 分页，stats 按
outcome 聚合；写失败仅记日志，不阻断已获得权限的调用。

## 工具元数据风险层（rules.py::tool_meta_risk_rule，求值管线第 6 层）

`@tool(risk="CRITICAL")` 声明落地为审批兜底：声明式/会话级规则未命中且默认放行时，CRITICAL 工具
合成 ask 规则（由 Guardian 裁决）。仅 CRITICAL 升级（MEDIUM/HIGH 只作 guardian
评审与审计的风险标注，write_file 等高频工具不受影响）；显式 allow 规则天然优先（求值顺序保证）；
审计 matched_rule=meta:risk 可归因。默认策略集与示例模板使用真实工具名（不留永不命中的死模式）。

## 内容安全

| 机制 | 位置 | 说明 |
|------|------|------|
| 会话令牌 | `agent/security/session_token.py` | 一次性令牌标记可信历史，泄露即 SECURITY 停止。注意：`security_session_token_enabled` 开启后历史消息逐条包裹每轮随机的令牌，conversation 层字节全变、历史锚点恒失效——排查缓存命中率时先确认该开关（见[上下文缓存分册](context-cache.md)） |
| 威胁扫描 | `agent/security/threat_scanner.py` | 注入模式扫描（工具结果标记 / 记忆写入拦截） |
| 结果脱敏 | `core/sanitizer.py` | 普通工具结果与日志中的 API Key/Token/密码自动遮盖；临时凭据结果遵循下述工具声明契约 |
| SSRF 防护 | `agent/retrieval/fetcher.py` | 检索抓取的直连防护（见[能力路由分册](vision-and-capabilities.md)） |
| 文件写入基线 | entities.filesystem | 保持 read-before-write 约束：已有文件先经读取或建立可信基线才能写入（append 语义独立）；不因缓存缺失自动放宽（见[设计决策](design-decisions.md)） |

## AI 临时取用凭据

密码本 `vault_reveal`、`vault_totp`、`vault_generate` 与 `vault_add` 通过
`@tool(sensitive_result=True)` 声明敏感结果。调用仍先走统一权限流程；规则允许或 Guardian
放行后，成功结果在当前任务工具链保留明文，不再被通用脱敏遮盖。权限拒绝、错误诊断与其他
工具的脱敏行为不受此声明影响，工具输出中的同名字段不能自行申请豁免。

可见性属于系统执行契约：执行宿主从注册声明读取属性并标记内部工具结果，结果管线只消费该
属性，不查询实体实现或维护工具名名单。`_sensitive_result` 在发送模型请求及持久化快照前剥离；
实体卸载后，已有结果仍按携带的属性隐藏。密码本字段、解密和使用提醒均由实体自身负责，
删除密码本不影响 core/mind 的导入与通用工具执行。

工具结果提醒模型仅为当前任务临时使用，可用于授权的登录或认证，不在回复中复述、不向无关
站点或第三方外发、不写入记忆、便签或普通文件。这是模型使用纪律，不是新增的出站拦截机制；
凭据进入所选任务模型的请求是本功能预期行为。

框架的日志、执行事件、上下文调试快照、崩溃恢复账本、执行摘要、回复后钩子快照与委托续跑记录使用隐藏敏感
结果的副本。敏感结果即使超长也不自动写入工具输出文件；续跑需要重新取用凭据。声明不改变
Guardian 不可用时的自主执行策略，也不关闭全局脱敏。


## Web 管理与分享访问边界

WebUI 默认允许局域网访问，监听地址与认证策略独立配置。`auth.strict=true` 时修改密码不得保存空值；
非严格模式仍支持取消密码保护。工作区删除入口对规范化后的 workspace/project 根目录拒绝删除，
包含空路径、点路径和折返路径；该保护不影响根目录读取和子目录删除。

分享页、媒体字节和下载使用同一次访问凭据。页面导航原子领取一次额度，签发仅适用于该分享链接的
一小时凭据；预览内的媒体与下载 URL 携带凭据，直接访问 raw 也领取额度，并用限定路径的 HttpOnly
cookie 支持后续 Range 请求。资源响应不缓存；链接撤销、到期或凭据到期后拒绝请求。凭据随进程重启
失效。额度耗尽阻止新的访问，不撤销已经领取的有效凭据；管理列表与统计仍把耗尽条目归入失效状态。
访问计数与审计在分享存储层统一写入，路由层只承担 HTTP 和页面渲染，不另外维护计数。
