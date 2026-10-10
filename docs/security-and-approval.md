# 安全与审批

权限规则、审批审计、风险分层与内容安全的现状说明。修改 `agent/approval/`、`agent/security/`、
`core/sanitizer.py` 或权限配置时按需阅读。

## 权限与 Guardian 评审

- 权限配置只有 `config/permission_rules.json`（参考 `config/permission_rules.example.json`）：
  `allow` 直接执行，`deny` 拦截，`ask` 由 Guardian AI 裁决。**CRITICAL 工具在无显式规则覆盖时
  自动评审**；规则拒绝优先于放行；用户 ID 必须限定频道，群聊 ID 不是操作者身份
- Guardian 不可用时按自主策略继续执行，原因进入审计和执行任务的 AI 上下文，不创建人工会话或发送
  频道通知；权限规则本身损坏或检查故障则拦截本次调用，不能借此绕过禁止规则
- 配置中心 `approval/guardian` 可选择评审模型和总时限；总时限覆盖历史读取与模型调用，调整配置会
  重置熔断
- 旧的人工批准 API、频道批准命令、会话放行、累计人工信任和 `approval_policies.json` 已移除；
  存量审计记录保留可读

## 审批审计持久化（agent/approval/audit.py + approval_audit 表）

所有**非默认放行**的审批决策落账本（人工 approve/deny/cancel/expire、规则拒绝、信任放行、超时
放行；常态 rule_allow 高频无信息量不记）。`trust_after_n_approvals` 计数从账本统计
（outcome=approved 累计）——重启不再从零重数；trusted 不计入 approved（防自动放行自我强化）。
`/approvals/history` 读表分页（offset+tool_name 过滤），stats 走 outcome 聚合；写失败 fail-open
仅记日志。

## 工具元数据风险层（rules.py::tool_meta_risk_rule，求值管线第 6 层）

`@tool(risk="CRITICAL")` 声明落地为审批兜底：声明式/会话级规则未命中且默认放行时，CRITICAL 工具
合成 ask 规则（guardian 先行评审，危险才升级人工）。仅 CRITICAL 升级（MEDIUM/HIGH 只作 guardian
评审与审计的风险标注，write_file 等高频工具不受影响）；显式 allow 规则天然优先（求值顺序保证）；
审计 matched_rule=meta:risk 可归因。默认策略集与示例模板使用真实工具名（不留永不命中的死模式）。

## 内容安全

| 机制 | 位置 | 说明 |
|------|------|------|
| 会话令牌 | `agent/security/session_token.py` | 一次性令牌标记可信历史，泄露即 SECURITY 停止。注意：`security_session_token_enabled` 开启后历史消息逐条包裹每轮随机的令牌，conversation 层字节全变、历史锚点恒失效——排查缓存命中率时先确认该开关（见[上下文缓存分册](context-cache.md)） |
| 威胁扫描 | `agent/security/threat_scanner.py` | 注入模式扫描（工具结果标记 / 记忆写入拦截） |
| 结果脱敏 | `core/sanitizer.py` | API Key/Token/密码自动遮盖（工具结果 + 日志） |
| SSRF 防护 | `agent/retrieval/fetcher.py` | 检索抓取的直连防护（见[能力路由分册](vision-and-capabilities.md)） |
| 文件写入基线 | entities.filesystem | 保持 read-before-write 约束：已有文件先经读取或建立可信基线才能写入（append 语义独立）；不因缓存缺失自动放宽（见[设计决策](design-decisions.md)） |
