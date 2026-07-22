# 微信频道（Weixin / iLink Bot API）

通过腾讯 **iLink Bot API** 把 AnelfAgent 接入个人微信。长轮询收发消息，
**无需公网端点或 webhook**。协议实现与 hermes-agent 的 weixin 适配器完全一致。

> 企业微信请使用 WeCom 渠道（不在本频道范围内）。

## 接入步骤

### 1. 安装依赖

```bash
uv sync   # cryptography + qrcode 已声明在 pyproject.toml
```

### 2. 扫码登录（WebUI，推荐）

打开 WebUI（默认 `http://127.0.0.1:8092/webui/`）→ **通道管理** → 找到「微信」卡片
→ 点击 **扫码登录** → 微信扫码并在手机上确认。

确认后凭据自动写入 `channels/weixin/channel_config.json`（`enabled` 自动置 true），
**频道自动启动**，无需重启、无需手改任何文件。二维码过期会自动刷新（最多 3 次）。

### 2'. 扫码登录（终端，替代方式）

```bash
uv run python scripts/weixin_setup.py
```

效果相同：终端显示二维码，扫码确认后写入配置并提示启用。

### 3. 验证

频道卡片状态变为 `running`、日志出现 `微信: 已连接 account=...` 即成功。
给你的 iLink bot 发条私聊消息即可对话。

## 配置项（channel_config.json）

| 字段 | 默认 | 说明 |
|---|---|---|
| `enabled` | `false` | 启用频道 |
| `account_id` | — | iLink Bot 账号 ID（扫码获得，形如 `...@im.bot`） |
| `token` | — | Bot Token（留空时自动从 `workspace/weixin/accounts/` 恢复） |
| `base_url` | `https://ilinkai.weixin.qq.com` | iLink API 地址 |
| `cdn_base_url` | `https://novac2c.cdn.weixin.qq.com/c2c` | 媒体 CDN |
| `dm_policy` | `open` | 私聊策略：`open` / `allowlist` / `disabled` |
| `allow_from` | `""` | 私聊白名单（逗号分隔用户 ID） |
| `group_policy` | `disabled` | 群聊策略（见下方限制） |
| `group_allow_from` | `""` | 群聊白名单（逗号分隔**群 ID**） |
| `split_multiline_messages` | `false` | 多行消息逐行拆分发送 |
| `typing_indicator` | `true` | 处理中显示「正在输入」 |
| `send_chunk_delay_seconds` | `1.5` | 文本分块发送间隔 |
| `text_batch_delay_seconds` | `3.0` | 连发消息合批静默期 |

所有字段也支持环境变量覆盖：`ANELF_WEIXIN_<字段名大写>`，
如 `ANELF_WEIXIN_TOKEN`、`ANELF_WEIXIN_DM_POLICY`。

## 功能

- 文本收发：markdown 块感知分块（单条 ≤2000 字符）、复制友好软换行、
  短闲聊多行自动拆气泡；连发消息 3s 静默期合批，避免多次触发思考
- 媒体收发：图片 / 视频 / 文件 / 语音，全部走 AES-128-ECB 加密 CDN
  （入站自动解密到 `workspace/uploads/`；语音优先使用转写文本）
- 会话连续性：`context_token` 落盘，重启后回复不中断；
  会话过期（-14）自动去掉 token 降级重发一次
- 「正在输入」提示（typing ticket 600s TTL，自动刷新）
- 消息去重：消息 ID + 内容指纹二级去重（5 分钟窗口）
- 健壮性：长轮询 35s 超时视为空响应、2s/30s 阶梯退避、
  会话过期暂停 10 分钟、限频熔断（30s）、游标即时落盘
- 访问策略：私聊/群聊独立 open/allowlist/disabled

## 限制（iLink bot 身份）

扫码登录后连接的是 **iLink bot 身份**（如 `...@im.bot`），不是普通个人号：

- 大多数情况下**只有发给 iLink bot 的私聊能可靠工作**；
- bot 通常无法被拉入普通微信群，iLink 一般也不推送群事件，
  @个人号 ≠ @bot。群策略设为非 `disabled` 时启动会记录 WARNING；
  若群消息不到达，**限制在 iLink 侧而非本频道**；
- 同一个 token 只允许一个网关在跑（多开请先停掉另一个实例）；
- 会话过期后需重新运行 `scripts/weixin_setup.py` 扫码。

## 故障排查

| 现象 | 处理 |
|---|---|
| 启动报缺 aiohttp/cryptography | `uv sync` |
| 启动报缺 account_id/token | 重跑 `scripts/weixin_setup.py` |
| 日志反复「会话已过期」 | 重新扫码登录 |
| 消息没反应 | 检查 `dm_policy` / 白名单；确认消息是发给 iLink bot 的私聊 |
| 图片显示灰图 | 不会发生在本实现（aes_key 已按 base64(hex) 发送）；若出现请提 issue |

## 目录结构

```
channels/weixin/
├── adapter.py        # 频道主体（BaseChannel 实现）
├── ilink_client.py   # iLink 协议层（API/AES/CDN/文本分块，纯函数）
├── state.py          # 凭据/游标/context_token/typing/去重 持久化
├── qr_login.py       # 扫码登录流程
├── config.py         # WebUI 配置元数据
└── channel_config.json
scripts/weixin_setup.py   # 配置向导（扫码登录）
```
