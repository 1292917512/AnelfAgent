# GitHub 频道

监听 GitHub 仓库动态(push / issue / PR / release / CI / star…),规范化后推送给 AI;
AI 可经 `github_*` 工具面反查与回写。设计方案:`projects/anelf-github-channel-plan.md`。

## 快速开始(轮询模式,零公网依赖)

1. 生成 fine-grained PAT:GitHub → Settings → Developer settings → Fine-grained tokens,
   勾选要监听的仓库,权限最小集:`Contents: Read`、`Issues: Read`(要回写就给 R&W)、
   `Pull requests: Read`(回写同理)、`Metadata: Read`(强制)。
2. 登记凭据:Web 面板「组件凭据」的 GitHub 卡片,或直接写 `config/provider_keys.json`
   的 `github.api_key`(该文件 gitignored)。
3. 配置订阅:编辑本目录 `channel_config.json`(或 Web 配置页 `adapter/github` 组):

   ```json
   {
     "enabled": true,
     "mode": "poll",
     "repos": [
       {"owner": "KroMiose", "repo": "nekro-agent",
        "local_path": "/Users/wangchenglong/projects/nekro-agent", "priority_boost": true}
     ]
   }
   ```

   也可以在对话里让 AI 自助:`帮我订阅 KroMiose/nekro-agent`(github_subscribe_repo 工具)。
4. 启用频道:Web 频道页打开开关,或让 AI `start_channel("github")`。

首次轮询只播种不重放历史;之后新事件按优先级推送:IMMEDIATE 立即唤醒 AI、
NORMAL 按 `aggregate_window_sec` 聚合后唤醒、DIGEST(star/fork 等)只计数,
由每日巡检任务统一汇总(见下)。

## 每日巡检(可选)

```bash
cp channels/github/github_watch.example.json config/tasks/github_watch.json
```

config/tasks/ 是 gitignored 的个人任务目录;落盘后 AI 每天 09:30 自主巡检并汇报。

## webhook 模式(可选,实时性最好;需公网可达)

1. 本机/webui 端口需公网可达(frp、cloudflared、公网服务器反代均可);
2. 配置 `mode: "webhook"`(或 `"both"`)+ `webhook_secret`(强随机串);
   **未配 secret 频道拒绝启动**(fail-closed,不验签的公网端点是 AI 注入面);
3. GitHub 仓库/组织 → Settings → Webhooks:Payload URL 填
   `https://<你的域名>/api/channels/github/webhook`,Content type `application/json`,
   Secret 同上,按需勾选事件;
4. 验证:GitHub 会先发 ping(返回 pong);重投/超时重发由 delivery 去重环幂等。

## 常用配置项

| 键 | 默认 | 说明 |
|---|---|---|
| `mode` | `poll` | `poll` / `webhook` / `both` |
| `default_poll_interval_sec` | 300 | 每仓轮询间隔(下限 60;匿名形态强制 ≥900) |
| `aggregate_window_sec` | 300 | NORMAL 事件聚合窗 |
| `quiet_hours` | 空 | 如 `23:00-08:00`:期间 IMMEDIATE→NORMAL、NORMAL→DIGEST |
| `watch_mentions_of` | 空 | 填 GitHub 登录名后,评论/帖子 @你 会立即唤醒 AI |
| `reply_relay` | `webui:web_user` | AI 在 GitHub 会话中回复的转述目标(`adapter:target`;空=不转述) |
| `allow_write` | false | 写工具总开关(评论/开 issue/打标签/关 issue;reaction 不受限) |
| `rate_limit_reserve` | 500 | 配额低于此值暂停轮询 |
| `api_base_url` | `https://api.github.com` | GHES 企业版改此 |

## AI 工具面(组 github)

读:`list_events / get_issue / list_issues / list_prs / list_commits / get_pr_diff /
get_workflow_runs / search_issues / get_release / get_rate_limit / get_daily_digest /
list_subscriptions`
订阅自助:`subscribe_repo / unsubscribe_repo`
写(`allow_write=true` 才放行,均经审批):`create_comment / create_issue / add_label /
close_issue`;轻量:`react`(表情回应)。

## 安全注意

- issue/PR/评论正文是**外部不可信内容**:注入 AI 时已截断并包裹「不可信」分隔块,
  AI 因事件诱发的写操作走审批门(可在 `config/permission_rules.json` 按
  `channel_id="github"` 收紧);
- 管理端点(`/status` `/events` `/subscriptions` `/test-inject`)由 webui 密码保护;
  webhook 端点只靠 HMAC 验签,请妥善保管 secret;
- token/私钥只存 `provider_keys.json`,不写进 `channel_config.json`。

## 故障排查

- `GET /api/channels/github/status`(webui 密码):接收器状态/每仓游标/配额/丢弃计数;
- 首次订阅看不到事件 = 正常(播种纪律);想立即看效果用
  `POST /api/channels/github/test-inject` 贴一段 payload 演练;
- 轮询暂停(401/404/熔断)会在对应仓会话收到一条「GitHub 频道告警」并唤醒 AI 转告。
