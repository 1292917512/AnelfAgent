# wcauto 频道（微信个人号 / Windows 远程桥）

把 AnelfAgent 接入**微信个人号**：经 Windows 侧桥服务驱动微信 4.x 桌面客户端，
拿到真·个人号的完整收发能力（私聊/群聊/@/媒体/朋友圈/防撤回），
与 `weixin` 频道（iLink bot 身份，群聊受限）互补共存。

> 桥基于 [wechatauto-replica](https://github.com/fanyuantaier/wechatauto-replica)：
> 收消息走本地 SQLCipher 数据库解密轮询（监听本身不操作界面）；
> 发消息走 UIA 主路径 + OCR/坐标兜底（窗口须可见）。
> **Windows 部署全流程见 [docs/deployment-windows.md](docs/deployment-windows.md)。**

## 架构

```
┌─ Windows 10/11 ────────────────┐   WS(JSON)   ┌─ AnelfAgent ───────────────┐
│ 微信 4.x 客户端（已登录）         │ ◄──────────► │ channels/wcauto/           │
│  ↓ wechatauto-replica           │  事件推送+RPC │  adapter/transport/parser  │
│ bridge/（python -m wcauto_bridge）│              │  send/tools/state          │
└─────────────────────────────────┘              └────────────────────────────┘
```

- **可靠性**：握手带 `last_seq` 断线回补（桥侧 2000 条环形缓冲）；入站
  `username+shard+local_id` 二级去重；RPC 超时/断线显式失败不吞错；
  发送默认回读数据库校验（`send_verify`）
- **风控安全**：桥内置 wechatauto rhythm 拟人节流（写操作 2.5-6s 间隔 +
  突发上限）；频道敏感写工具默认全关（`allow_write=false`）+ 审批门控

## 快速开始

1. **Windows 侧起桥**（详见部署文档）：拷贝 `bridge/` 目录 →
   `uv python install 3.12` → `uv sync --frozen` →
   编辑 `wcauto_bridge.json`（设 token）→ `run_bridge.bat --doctor` → `run_bridge.bat`
2. **频道侧连接**：WebUI 配置中心 `adapter/wcauto` 组（或直接改
   `channel_config.json`）设置 `ws_url` 与 `access_token`，`enabled` 置 true
3. **验证**：频道卡片状态 `running`、日志出现 `wcauto: 已连接桥 account=...` 只说明桥连接成功。
   账号来自数据库缓存；health/doctor 的 `login_state=unknown` 不证明微信已登录。收发验收前先
   检查桥机当前登录画面，再进行真实收发。health/doctor 不会主动弹起窗口。
   即成功；用**另一个微信号**给目标号发消息，验证完整入站链路

## 常用配置项

| 键 | 默认 | 说明 |
|---|---|---|
| `enabled` | `false` | 启用频道 |
| `ws_url` | `ws://127.0.0.1:8655/wcauto/ws` | 桥 WS 地址 |
| `access_token` | `""` | 与桥 token 一致（非回环连接必填） |
| `dm_policy` / `group_policy` | `open` / `open` | 私聊/群聊策略（open/allowlist/disabled） |
| `dm_allow_from` / `group_allow_from` | `""` | 白名单（逗号分隔 wxid 或昵称） |
| `require_mention` | `true` | 群聊需 @我 才激活思考（消息仍入库） |
| `self_names` | `""` | 额外的「我」的称呼（@检测用，桥昵称自动包含） |
| `ignore_chats` | `""` | 完全忽略的会话（逗号分隔 wxid 或昵称） |
| `send_verify` | `true` | 发送后回读数据库校验 |
| `text_chunk_length` | `1500` | 长文本分段长度 |
| `auto_download_image/voice/file/video` | `t/t/f/f` | 私聊/@我 时媒体自动下载到 `workspace/uploads/` |
| `recall_notify` | `true` | 撤回事件（含原文）作为系统消息写入会话 |
| `allow_write` | `false` | 敏感写工具总开关（见下） |

环境变量覆盖：`ANELF_WCAUTO_<字段名大写>`。

## AI 工具面

**读工具**（AUTO_ALLOW）：`wcauto_get_sessions` / `wcauto_get_messages` /
`wcauto_search_contact` / `wcauto_download_media` / `wcauto_list_labels` /
`wcauto_label_members` / `wcauto_get_moments` / `wcauto_get_recalled` /
`wcauto_bridge_doctor`；通用能力 `get_chat_info` / `get_chat_members`。

**写工具**（sensitive + `allow_write` 门控 + 审批）：
`wcauto_send_at`（@群成员）/ `wcauto_recall_last`（撤回自己最近一条）/
`wcauto_forward_msg`（转发，单次≤9 人）/ `wcauto_moment_like` /
`wcauto_moment_comment` / `wcauto_label_send`（标签群发，默认 dry_run 先解析收件人）。

普通发消息/图片/文件走通用 `send_message` 等输出工具。

## 限制（微信桌面客户端形态决定）

- **必须有一台常开 Windows**（物理机/虚拟机）登录微信 4.1.12+，使用 64 位 Python 3.12；
  微信与桥保持同用户、同权限级别，默认不要单独提权
- **发消息/朋友圈互动/at/转发需要窗口可见**：锁屏、远程桌面断开时发送会安全失败；
  数据库监听不操作界面，但锁屏/RDP 断开后新消息能否继续到达取决于微信客户端，不作绝对保证
- **语音为 SILK 原始格式**（.silk），频道不内置转写
- **群聊@我识别**基于正文 `@昵称` 匹配（微信 DB 不存 at 列表）；
  引用消息从正文正则提取，非结构化
- 视频以文件形态发送（微信桌面端无原生视频消息接口）
- 风控是真实存在的：不要高频自动回复/群发，写工具保持审批开启

## 故障排查

| 现象 | 处理 |
|---|---|
| 频道一直 `connecting` | 桥是否在跑；`ws_url` 的 IP/端口；防火墙放行 8655 |
| 日志 `auth_failed` | `access_token` 与桥 `wcauto_bridge.json` 的 token 不一致 |
| 能收不能发 | 先核对实时登录画面及窗口状态（锁屏/RDP 断开等）；`--doctor` 仅检查桥、数据库与缓存 |
| 图片下载失败 | 微信里先点开过该图（缩略图策略）；桥 `doctor` 看图片密钥 |
| 群消息不到 | 检查 `group_policy`/白名单/`ignore_chats` |
| AI 撞拟人节流 | 正常现象（rhythm 冷却 30-75s），降频即可 |

## 目录结构

```
channels/wcauto/
├── adapter.py        # 频道主体（BaseChannel 实现，事件分发/策略/媒体/健康）
├── transport.py      # WS 客户端（握手续传/RPC 配对/断线重连）
├── send.py           # 出站构造（分块/@转换/媒体 base64）
├── parser.py         # 入站事件纯解析（@我/引用/类型映射）
├── tools.py          # wcauto_* 工具面（读 AUTO_ALLOW / 写 sensitive）
├── state.py          # last_seq 水位持久化 + 去重
├── config.py         # CONFIG_MODEL（WebUI 配置元数据）
├── frontend/index.ts # 配置中心分组名注册
├── bridge/           # Windows 桥服务（uv 项目，自包含，整目录拷贝部署）
│   ├── pyproject.toml / uv.lock / .python-version
│   └── wcauto_bridge/  protocol/config/backend/server/__main__
├── docs/deployment-windows.md  # Windows 部署与运维手册
└── tests/            # 频道内单元测试（协议/配置/解析/传输/发送/工具/事件流）
```
