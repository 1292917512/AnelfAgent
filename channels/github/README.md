# GitHub 频道

通过轮询或 webhook 接收 GitHub 事件，按仓库或议题送入 AI 会话。查询、回复、配置和恢复操作均通过频道工具提供，普通回复按 `reply_relay` 转述。

## 配置步骤

1. 在组件凭据中心保存 PAT，或保存 GitHub App 私钥。频道配置只填写凭据引用名。
2. 在频道页“接收方式”选择轮询、Webhook 或两者。
3. 在“仓库与事件”添加 owner/repo，勾选默认事件或仓库专属事件。仓库事件空选表示沿用默认；全局事件空选表示不订阅仓库事件。
4. 在“推送内容”勾选时间、触发者、正文、链接、对象上下文和本地代码路径。仓库内容可沿用默认；关闭沿用后空选表示只推送标题。
5. 保存并连接。运行与恢复页显示收件箱、失败原因和轮询暂停状态。

数组和嵌套对象均由 `config.py` 的 Pydantic schema 驱动，频道表单不要求输入逗号分隔字符串。新增字段须在配置模型中声明；标量选择用 Literal，数组选择用 `json_schema_extra.options`。

## 接收与可靠性

- 轮询首次只播种，不重放历史；后续按仓库游标分页获取，遵守服务器轮询间隔。
- 匿名轮询间隔至少 900 秒。配额预留最多占账户总配额的 10%；到重置时间后允许探测恢复。
- Events API 只提供部分事件且有历史窗口限制。完整事件目录需要 webhook；状态页会提示分页窗口可能的缺口。
- Webhook 默认地址为 `/api/channels/github/webhook`。命名账户使用 `/api/channels/github/webhook/<account>` 和对应的 `webhook_secret_ref`。
- 验签强制使用 HMAC-SHA256。接收成功意味着原始事件已持久化到数据目录的 SQLite 收件箱；容量不足返回 503，保留已有记录。
- GitHub 不自动重投失败的 webhook。被拒收的事件需在 GitHub 的投递记录中手动重投；已接受事件由本地恢复任务重试。
- 本地处理失败指数退避，连续失败 8 次后保留记录等待手动恢复。已完成事件和原始载荷保留 7 天。
- 同一投递 ID 幂等；业务标识充分时，轮询、Webhook 和 release 轮询跨来源去重。成功确认前中断可能重新投递，属于至少一次投递，不承诺跨进程崩溃的绝对恰好一次。
- 聚合按仓库或议题隔离；提交数未知的现代 PushEvent 仍保留 before/head。评论上下文保留对象 ID、路径、行和安装信息。
- 本频道写出的评论按对象 ID 过滤回调，其他机器人的事件仍可接收。
- `delivery` 可指定立即、聚合、仅汇总或智能分级；`digest=false` 让仓库退出摘要缓冲。选中的低优先级事件会进入汇总，不被静默丢弃。

## 认证与账户

默认字段兼容单账户配置：`auth_mode`、`token_ref`、`app_id`、`app_private_key_ref`、`installation_id`。

多个账户在 `accounts` 中声明，仓库通过 `account` 引用。GitHub App 优先使用显式安装 ID，否则按仓库定位安装；无仓库且存在多个安装时要求明确选择，不取第一个安装。

OAuth 账户需设置 `auth_mode=oauth`、client ID、client secret 引用和独立的 token 引用。保存并启动频道后，在认证页生成授权链接；GitHub OAuth 应用的回调地址配置为：

```text
https://<你的服务地址>/api/channels/github/oauth/callback
```

授权使用一次性 state 和 PKCE；令牌与刷新令牌写入凭据中心。App 安装认证、用户认证和 OAuth App 认证通过请求局部上下文隔离。API 工具的 `auth` 对象支持：

```json
{
  "account": "work",
  "installation_id": 123,
  "repositories": ["example"],
  "permissions": {"contents": "read", "issues": "write"}
}
```

`user_token_ref` 可临时使用用户令牌；`as_app` 和 `as_oauth_app` 分别切换 App 与 OAuth App 身份。权限缩减由 GitHubKit 验证并交给 GitHub 实施。

## AI 工具

| 用途 | 工具 |
|---|---|
| 常用查询 | `get_issue`、`list_issues`、`list_prs`、`list_commits`、`get_release`、`search_issues` |
| 审查与 CI | `get_pr_diff`、`get_pr_context`、`get_workflow_runs`、`get_workflow_jobs` |
| 原始事件与恢复 | `list_events`、`get_event`、`delivery_status`、`retry_delivery`、`get_daily_digest` |
| 配置 | `describe_config`、`configure`、`list_subscriptions`、`subscribe_repo`、`unsubscribe_repo` |
| 回复与写入 | `reply`、`create_comment`、`create_issue`、`add_label`、`close_issue`、`react` |
| 完整 API | `api_read`、`api_write`、`graphql_read`、`graphql_write`、`describe_api`、`call_rest` |

实际工具名均带 `github_` 前缀。最新评论从尾页读取；大结果明确标记截断，分页结果提供后续页码。GraphQL read 解析操作类型并拒绝 mutation；完整 SDK 操作由 `call_rest` 提供，包括上传端点。

所有写工具（含 reaction 和完整 SDK 调用）要求 `allow_write=true` 并经过统一审批。网络超时或服务端故障导致结果不确定时，不自动重复提交；先查询 GitHub 状态再决定是否重试。

订阅更新中，未传字段保留，`[]`、`false`、`0` 是有效值。需要恢复仓库内容的默认继承时，通过 `configure` 将该订阅的 `message_fields` 设置为 `null`。模式和 API 根地址变更需要重新连接；其余策略在处理新事件时生效。

## 模块职责

| 模块 | 职责 |
|---|---|
| `adapter.py` / `router.py` | 生命周期、标准消息适配、HTTP 接线 |
| `config.py` / `catalog.py` | 配置声明、事件与内容选项 |
| `auth.py` / `app_auth.py` / `oauth.py` / `credentials.py` | 请求局部认证、安装定位、用户授权和凭据声明 |
| `client.py` | HTTP、配额跟踪和安全重试 |
| `event_models.py` / `events.py` | 事件结构、规范化、订阅和上下文 |
| `renderers.py` / `formatting.py` | 事件摘要和选定内容的消息格式 |
| `journal.py` / `pipeline.py` | 持久接收、原子领取、聚合、确认与恢复 |
| `poller.py` / `webhook.py` | 事件来源 |
| `tool_*.py` / `policy.py` | 查询、写入、配置、完整 API 与回复规则 |
| `frontend/` | schema 表单、运行恢复和 OAuth 操作 |

事件目录与 GitHubKit 的 webhook schema 一致，由测试守卫；更新 SDK 后同步 `event_catalog.json`。未知事件仍可通过规范名称订阅并保留原始载荷。现有 NoneBot 的事件处理、Markdown 内容、GitHub 回复、完整 SDK、GraphQL、多 App/安装和用户认证能力均有对应频道接口；NoneBot 的驱动和 matcher 生命周期由 AnelfAgent 频道体系承担。

每日巡检可使用 `github_watch.example.json` 配置任务。管理 HTTP 路由继承 Web UI 认证；OAuth callback 用一次性状态验证，Webhook 用签名验证。

## 验证

```powershell
uv run pytest channels/github/tests
uv run ruff check channels/github
uv run lint-imports
```

前端运行仓库的 lint、typecheck、test、build 和浏览器测试。离线模拟测试覆盖错误和恢复路径；真实账户权限、OAuth 回调可达性、Webhook 投递与企业版环境需在对应环境验收。
