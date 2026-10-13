# wcauto 桥 Windows 部署与运维手册

本文档面向**把 AnelfAgent 接入微信个人号**的部署者：在 Windows 机器上安装并
长期稳定运行 wcauto 桥服务（`bridge/` 目录），与 AnelfAgent 的 wcauto 频道对接。

> 一句话：桥 = 贴在微信 4.x Windows 客户端旁边的 WebSocket 服务。
> 读消息走本地数据库解密（不碰界面），发消息走 UI 自动化（碰界面）。
> 理解了这一条，下面的所有注意事项都是它的推论。

---

## 1. 环境要求

| 项目 | 要求 | 原因 |
|---|---|---|
| 系统 | Windows 10 / 11（物理机或虚拟机均可） | wechatauto 全栈依赖 Win32/WinRT |
| Python | 3.9+（建议 3.12） | wechatauto 要求 |
| 微信 | 4.1.12+，**已登录**，保持运行 | 数据库密钥在进程内存里 |
| 运行权限 | **普通用户权限**（不要「以管理员身份运行」微信或桥） | 进程内存读取要求权限一致 |
| 网络 | 桥端口（默认 8655）对 AnelfAgent 所在机器可达 | 频道经 WS 连接 |

虚拟机提示：VMware/Parallels/Hyper-V 里的 Windows 均可，但要**保持有交互会话
登录且不锁屏**（见 §5）。无头（headless）场景只能收消息不能发消息。

## 2. 安装

```powershell
# 1) 拷贝 bridge 目录（AnelfAgent 仓库 channels/wcauto/bridge/）到任意位置，例如：
#    C:\wcauto\bridge\

# 2) 安装依赖（建议在venv中）
cd C:\wcauto\bridge
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 3) 生成配置（首次运行 run_bridge.bat 会自动从样例复制）
copy wcauto_bridge.example.json wcauto_bridge.json
notepad wcauto_bridge.json   # 必改 token；其余见 §3
```

## 3. 桥配置（wcauto_bridge.json）

| 键 | 默认 | 说明 |
|---|---|---|
| `host` | `0.0.0.0` | 监听地址；只本机连就改 `127.0.0.1` |
| `port` | `8655` | 监听端口 |
| `token` | `""` | **访问令牌，非回环监听必填**；填一段长随机串 |
| `account` | `""` | 多账号机器上指定 wxid；留空自动检测最近活跃账号 |
| `ignore_chats` | `[]` | 不监听的会话（username 列表），如公众号会话 |
| `media_max_mb` | `25` | 单条入站媒体回传上限 |
| `event_buffer` | `2000` | 事件环形缓冲（频道断线回补容量） |
| `enable_recall_guard` | `true` | 防撤回镜像（含媒体备份） |
| `recall_mirror_dir` | `""` | 镜像库目录，留空为 wechatauto 默认（~/Documents/wechatauto_recall） |
| `enable_moments` | `true` | 朋友圈读取 |
| `data_dir` | `""` | 桥数据目录（下载缓存等），留空为 `~/.wcauto_bridge` |

所有键都可用环境变量覆盖：`WCAUTO_BRIDGE_<键名大写>`（如 `WCAUTO_BRIDGE_TOKEN`）。

## 4. 启动与自检

```powershell
# 方式一：双击/执行批处理（推荐，自动 UTF-8、首次自动生成配置）
run_bridge.bat

# 方式二：手动
python -m wcauto_bridge --config wcauto_bridge.json

# 自检（不启动服务，检查 账号/数据库密钥/图片密钥/窗口 四项）
python -m wcauto_bridge --config wcauto_bridge.json --doctor
```

启动成功标志：控制台打印 `wcauto 桥已启动: ws://0.0.0.0:8655/wcauto/ws`。

健康检查（频道侧卡片也用这个）：

```powershell
curl -H "Authorization: Bearer <token>" http://127.0.0.1:8655/wcauto/health
```

## 5. 稳定运行的关键（务必读完）

### 5.1 窗口活性 = 发送能力

- **收消息**：不依赖窗口，锁屏、最小化、RDP 断开都能收。
- **发消息**（含 @、转发、朋友圈点赞评论）：必须微信窗口**可见**。
  锁屏 / 远程桌面断开 / 微信退到托盘且界面未物化时，发送会**安全失败**
  （返回明确错误，不会乱发）。

### 5.2 虚拟机/远程桌面防锁屏

- Windows 电源设置：关闭自动锁屏与睡眠（`powercfg /change standby-timeout-ac 0`）。
- 用 RDP 管理时，**断开前用 `tscon` 把会话切回控制台**而不是直接关窗口：
  `tscon %SESSIONNAME% /dest:console`（管理员 cmd）。直接关 RDP 会让 GUI 会话锁屏。
- Hyper-V 增强会话 / VMware 控制台保持登录即可。

### 5.3 微信自身

- 微信更新后**必须重启桥**（UIA 热激活与密钥锚点随版本漂移，重启桥会自动重做）。
- 微信被踢下线（手机端退出/风控）后：重新登录微信 → 重启桥。
- 首次启动桥会扫描微信进程内存提取数据库密钥（约 6 秒），之后本地缓存秒开；
  安全软件若拦截进程读取，把桥目录加白名单。

### 5.4 风控纪律（关系到你微信号的安全）

- 桥内置 wechatauto `rhythm` 拟人节流：写操作最小间隔 2.5-6s、
  120s 内最多 6 次、超限冷却 30-75s（`WECHATAUTO_RHYTHM` 环境变量可调档：
  `natural` 默认 / `calm` 更慢 / `fast` 更快 / `off` 关闭——**不建议 off**）。
- 频道侧纪律：敏感写工具默认全关（`allow_write=false`），AI 写操作走审批；
  不要让 AI 高频自动回复群聊；标签群发首用必须 `dry_run`。
- 风控信号（微信弹「操作频繁」/被强制下线）：立即把频道 `group_policy` 收紧、
  桥重启、观察 24h。

### 5.5 开机自启（任务计划程序）

```powershell
# 注册开机任务（以当前用户登录后运行；不要用 SYSTEM/服务方式——必须有交互会话）
schtasks /create /tn "wcauto-bridge" /tr "C:\wcauto\bridge\.venv\Scripts\python.exe -m wcauto_bridge --config C:\wcauto\bridge\wcauto_bridge.json" ^
  /sc onlogon /rl limited /f
# 工作目录需在桥目录：把上面包一层 run_bridge.bat 作为 /tr 目标更稳：
schtasks /create /tn "wcauto-bridge" /tr "C:\wcauto\bridge\run_bridge.bat" /sc onlogon /rl limited /f
```

配合 Windows「自动登录」（netplwiz）可实现断电重启后全链路自愈。
注意：微信本身也要开机自启（微信设置里开「开机自动启动」）。

## 6. 频道侧对接

WebUI → 配置中心 → `adapter/wcauto` 组（或 `channel_config.json`）：

```json
{
  "enabled": true,
  "ws_url": "ws://<Windows机器IP>:8655/wcauto/ws",
  "access_token": "<与桥一致的 token>"
}
```

连接成功日志：`wcauto: 已连接桥 account=wxid_xxx nickname=...`。
断线自动重连（默认 5s 起、指数退避到 60s），重连后自动回补断线期间的事件。

## 7. 故障排查

| 现象 | 排查 |
|---|---|
| 桥启动报错「wechatauto 不可用」 | 依赖没装全（`pip install -r requirements.txt`）；不在 Windows |
| `--doctor` 显示数据库密钥 0 张可用 | 微信没登录；微信版本过旧；安全软件拦截；微信刚更新需重启桥 |
| 频道连接被拒/超时 | Windows 防火墙放行 8655；`host` 是否为 `0.0.0.0`；IP 是否正确 |
| `auth_failed` | 两侧 token 不一致 |
| 能收不能发 | §5.1/5.2：窗口不可见；`--doctor` 看 `window_visible` |
| 图片下载返回不可用 | 微信里先点开该图（未点开的群图只有缩略图，微信存储策略） |
| 撤回检测不到 | `enable_recall_guard` 须为 true；监听建立前的消息救不回 |

## 8. 安全说明

- 桥持有你微信的**完整读写能力**，token 等同密码：只监听内网地址、
  务必设 token、不要把桥端口暴露到公网。
- 桥与频道之间是明文 WS（内网场景）；跨网段/公网请套一层 TLS 隧道
  （如 stunnel/frp 的 stcp）。
- `wcauto_bridge.json` 含 token，不要提交进任何 git 仓库
  （AnelfAgent 侧 `channel_config.json` 同理）。
