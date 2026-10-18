# wcauto 桥 Windows 部署与运维手册

本文档是 `channels/wcauto/bridge/` 的完整部署指南：把 AnelfAgent 的
**wcauto 频道**接入一台 Windows 机器上的**微信个人号**。

桥基于开源项目 [wechatauto-replica](https://github.com/fanyuantaier/wechatauto-replica)
（本文以 v1.2.6.1、提交 `62503d7` 为依据）：

- **收消息**：读取微信本地 SQLCipher 数据库，监听本身不操作界面；
- **发消息/at/转发/朋友圈互动**：驱动微信窗口，必须有可用的交互桌面；
- **环境管理**：使用 uv 项目和 `uv.lock`，Python 与依赖可重复安装。

> 先记住一条：**数据库读取能力与 GUI 发送能力是两套独立条件。**
> 自检、验收和排障都必须分别验证，不能用“能收消息”推断“一定能发消息”。

---

## 目录

1. [部署前提与推荐拓扑](#1-部署前提与推荐拓扑)
2. [目录准备](#2-目录准备)
3. [安装 uv、Python 3.12 与锁定依赖](#3-安装-uvpython-312-与锁定依赖)
4. [桥配置](#4-桥配置)
5. [首次自检：上游 doctor 与桥 doctor](#5-首次自检上游-doctor-与桥-doctor)
6. [启动桥并验证网络](#6-启动桥并验证网络)
7. [频道配置与端到端验收](#7-频道配置与端到端验收)
8. [长期稳定运行](#8-长期稳定运行)
9. [升级、回退与备份](#9-升级回退与备份)
10. [故障排查](#10-故障排查)
11. [安全边界](#11-安全边界)
12. [参考资料](#12-参考资料)

---

## 1. 部署前提与推荐拓扑

### 1.1 推荐拓扑

```text
AnelfAgent（macOS/Linux/另一台 Windows）
        │  WebSocket + token
        ▼
Windows 10/11 x64，同一台机器上运行：
  1. 微信 4.x（已登录你的个人号）
  2. wcauto 桥（uv 项目，默认 TCP 8655）
```

桥必须与微信运行在**同一 Windows 用户、同一交互会话、同一权限完整性级别**。
不能把桥放在另一台机器上读取远程微信，也不能把微信和桥拆到两个 Windows 用户里。

### 1.2 环境清单

| 项目 | 要求 | 为什么 |
|---|---|---|
| Windows | 10/11，建议 x64 物理机或虚拟机 | wechatauto 使用 Win32/WinRT/UIA |
| 微信 | 4.1.12+，已登录并保持运行 | 数据库密钥从微信进程内存提取 |
| Python | **3.12，64 位** | 上游已在 3.12 验证；32 位进程不能正确读取 64 位微信进程内存 |
| 包管理 | uv | `pyproject.toml` 声明依赖，`uv.lock` 固定完整依赖树 |
| 网络 | AnelfAgent 能访问桥的 TCP 端口，默认 8655 | 频道是 WS 客户端，桥是服务端 |
| 桌面 | 发送时需要已解锁、可见的交互桌面 | UIA/OCR/剪贴板都依赖 GUI 会话 |
| 权限 | 默认微信与桥都不要单独提权 | 权限不一致会导致进程内存或 UIA 枚举失败 |

### 1.3 明确不支持的部署方式

- **Windows Service / NSSM / Session 0**：没有交互桌面，发送不可用；
- **Docker/WSL 中运行桥**：无法操作宿主机上的微信窗口；
- **微信与桥跨机器**：桥需要读取本机微信进程与本地数据库；
- **把桥端口直接暴露到公网**：桥拥有你的微信完整读写能力；
- **管理员运行微信、普通权限运行桥（或反过来）**：不要制造权限差。

如果只需要只读历史消息，可以在无 GUI 的情况下使用数据库能力，但这不属于
本频道的完整部署目标。

---

## 2. 目录准备

把 AnelfAgent 仓库中的整个目录：

```text
channels/wcauto/bridge/
```

复制到 Windows，例如：

```text
C:\wcauto\bridge\
```

建议路径不要含空格和中文，减少任务计划程序与日志重定向的转义问题。
复制后的关键文件应如下：

```text
C:\wcauto\bridge\
├─ .python-version             # 固定 Python 3.12
├─ pyproject.toml              # 依赖声明
├─ uv.lock                     # 完整依赖锁文件，必须一起复制
├─ run_bridge.bat              # 启动/自检入口
├─ wcauto_bridge.example.json  # 配置样例
└─ wcauto_bridge\              # 桥程序包
   ├─ __main__.py
   ├─ backend.py
   ├─ config.py
   ├─ protocol.py
   └─ server.py
```

以下文件属于**本机生成内容**，不要从别的机器复制来复用：

- `.venv/`：由 `uv sync` 在本机创建；
- `wcauto_bridge.json`：包含本机 token；
- `logs/`、`~/.wcauto_bridge/`：运行日志与媒体缓存；
- 微信数据库密钥缓存：与具体机器、微信账号和进程相关。

---

## 3. 安装 uv、Python 3.12 与锁定依赖

以下命令均在 **PowerShell** 中执行。

### 3.1 安装 uv

优先使用 WinGet：

```powershell
winget install --id=astral-sh.uv -e
```

没有 WinGet 时，使用 uv 官方安装脚本：

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

关闭并重新打开 PowerShell，然后验证：

```powershell
uv --version
```

如果仍提示找不到 `uv`，先检查安装器输出的 `bin` 目录是否已加入 `PATH`，
不要急着重复安装。

### 3.2 安装 64 位 Python 3.12

```powershell
uv python install 3.12
```

桥目录中的 `.python-version` 已指定 `3.12`，后续 uv 命令会优先使用该系列解释器。

### 3.3 按锁文件创建环境

```powershell
cd C:\wcauto\bridge
uv sync --frozen
```

`--frozen` 的含义：直接使用仓库中的 `uv.lock`，**不重新解析、不升级依赖**。
`uv sync` 会在桥目录创建 `.venv`，并按锁文件精确同步环境。

检查解释器、位数和上游库版本：

```powershell
uv run --frozen python -c "import struct,sys,platform,wechatauto; print(sys.version); print(platform.architecture()); print('pointer_bits=',struct.calcsize('P')*8); print('wechatauto=',wechatauto.__version__)"
```

验收标准：

- Python 为 3.12.x；
- `platform.architecture()` 显示 `64bit`；
- `pointer_bits=64`；
- `wechatauto` 能正常导入并打印版本。

如果显示 32 位，**停止后续部署**，先修正 uv 选择的 Python；不要继续尝试
密钥提取，否则容易把位数问题误判成微信故障。

### 3.4 uv 使用纪律

- 日常启动：`uv run --frozen ...`，不更新锁文件；
- 初次/升级后同步：`uv sync --frozen`；
- 不要在生产环境直接 `uv pip install ...` 修改 `.venv`，避免环境漂移；
- 上游升级必须是单独动作，见 [9.1](#91-升级桥代码或-wechatauto-replica)。

---

## 4. 桥配置

### 4.1 创建本机配置

```powershell
cd C:\wcauto\bridge
Copy-Item .\wcauto_bridge.example.json .\wcauto_bridge.json
```

生成一个随机 token（PowerShell 示例）：

```powershell
$bytes = New-Object 'byte[]' 32
[Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
[Convert]::ToBase64String($bytes)
```

把输出填入 `wcauto_bridge.json` 的 `token`。也可以直接设置环境变量
`WCAUTO_BRIDGE_TOKEN`，避免 token 写入文件；此时配置文件中的 `token` 留空，
但启动桥和配置频道时必须注入同一个环境变量值。

> `wcauto_bridge.json` 含有你微信读写能力的凭据，不要提交到 git，不要放进
> 共享目录，也不要通过聊天工具明文传输。

### 4.2 配置项说明

```json
{
  "host": "0.0.0.0",
  "port": 8655,
  "token": "替换为随机长 token",
  "account": "",
  "ignore_chats": [],
  "media_max_mb": 25,
  "event_buffer": 2000,
  "enable_recall_guard": true,
  "recall_mirror_dir": "",
  "enable_moments": true,
  "data_dir": ""
}
```

| 键 | 默认值 | 何时需要修改 |
|---|---|---|
| `host` | `0.0.0.0` | 只允许本机连接时改 `127.0.0.1`；远程频道通常保留 |
| `port` | `8655` | 端口冲突时修改，频道 `ws_url` 同步修改 |
| `token` | 空 | **非回环监听必填**；否则桥拒绝启动 |
| `account` | 空 | 多账号自动选错时填目标账号 wxid |
| `ignore_chats` | `[]` | 不想监听的会话 username 列表 |
| `media_max_mb` | `25` | 单条入站媒体回传上限；大文件不要盲目调大 |
| `event_buffer` | `2000` | 频道断线期间的桥事件缓冲容量 |
| `enable_recall_guard` | `true` | 是否启用防撤回镜像与媒体备份 |
| `recall_mirror_dir` | 空 | 防撤回镜像目录；空则使用上游默认目录 |
| `enable_moments` | `true` | 是否允许读取本地朋友圈数据库 |
| `data_dir` | 空 | 桥下载缓存等；空则为 `~/.wcauto_bridge` |

留空时，上游依次从微信配置文件、注册表和用户目录自动检测。自动检测正常就
不要手填；多账号时先在 doctor 中核对实际选中的 wxid。

所有字段均可通过环境变量覆盖：

```text
WCAUTO_BRIDGE_<字段名大写>
```

例如 `WCAUTO_BRIDGE_PORT=8656`、`WCAUTO_BRIDGE_ENABLE_MOMENTS=false`。

---

## 5. 首次自检：上游 doctor 与桥 doctor

### 5.1 自检前的微信准备

1. 启动微信并完整登录目标个人号；
2. 打开微信主窗口，不要只留在系统托盘；
3. 确认微信和即将运行的 PowerShell 属于同一用户、同一权限级别；
4. 如果需要下载图片，先在微信里点开任意一张图片查看大图；
   图片 AES 密钥未驻留内存时，上游可能暂时扫描不到；
5. 不要在这时同时运行其它微信自动化脚本，避免争用窗口和剪贴板。

首次构造数据库连接会扫描微信进程内存，可能耗时约数秒；这是正常现象。
后续密钥缓存生效后会明显加快。

### 5.2 第一级：运行上游完整 doctor

```powershell
cd C:\wcauto\bridge
uv run --frozen python -m wechatauto doctor
```

依次检查四段输出：

| 检查项 | 通过标准 | 不通过时的影响 |
|---|---|---|
| Python/平台 | 3.12、64 位、Windows | 基础环境不满足，停止 |
| 账号与数据库密钥 | 选中正确 wxid，至少一个库可解密 | 无法读取消息 |
| 图片 AES 密钥 | 已拿到 | 图片下载不可用；不影响纯文本收发 |
| UIA 控件树 | 已物化，或后续真实发送能触发热激活 | 发送/at/转发/朋友圈互动不可用 |

**注意：不要只看 doctor 退出码。** 上游当前实现的退出码主要取决于数据库
密钥，图片密钥或 UIA 树缺失仍可能显示为其它状态；必须阅读完整输出。

数据库密钥失败时，可进一步运行：

```powershell
uv run --frozen python -m wechatauto.diagnose_keys
```

### 5.3 第二级：运行桥 doctor

```powershell
cd C:\wcauto\bridge
.\run_bridge.bat --doctor
```

它验证的是**桥实际使用的配置与初始化路径**，输出 JSON，重点看：

- `account.username`：是否为你的目标 wxid；
- `db_ok=true`；
- `listener.listener_running=true`；
- `image_key=true`（仅图片下载需要）；
- `window_visible=true`（仅发送需要）。

如果上游 doctor 正常、桥 doctor 异常，优先检查两侧是否用了不同的 Python
环境、`account` 配置，以及桥是否被安全软件拦截。

### 5.4 首次发送验证（可选但推荐）

此命令会向你的“文件传输助手”真实发送一条消息：

```powershell
uv run --frozen python -m wechatauto send "wcauto 桥发送自检" --to 文件传输助手 --verify
```

验收：微信中出现一条且仅一条测试消息，命令返回成功。

`--verify` 表示发送后回读本地数据库确认，**不是微信服务端回执**。
若出现“已操作发送，但数据库未确认”，先把结果当作**未知**，检查微信与数据库，
不要立即连续重发，以免重复消息。

---

## 6. 启动桥并验证网络

### 6.1 前台启动

```powershell
cd C:\wcauto\bridge
.\run_bridge.bat
```

等价完整命令：

```powershell
uv run --frozen python -m wcauto_bridge --config wcauto_bridge.json
```

成功时看到类似：

```text
wcauto 桥已启动: ws://0.0.0.0:8655/wcauto/ws
```

首次部署先保持前台运行，完成第 7 节验收后再配置自启动。

### 6.2 本机健康检查

```powershell
$headers = @{ Authorization = "Bearer 你的token" }
Invoke-RestMethod -Headers $headers http://127.0.0.1:8655/wcauto/health
```

重点字段：

- `ok=true`；
- `backend.listener_running=true`；
- `client_connected`：频道连接后应为 true；
- `backend.window_visible`：只表示发送条件，不影响进程存活判断。

### 6.3 远程网络与防火墙

在 AnelfAgent 所在机器执行：

```bash
curl -H "Authorization: Bearer 你的token" http://<Windows-IP>:8655/wcauto/health
```

如果本机可访问、远程不可访问，按顺序检查：

1. `ws_url` 使用的是不是 Windows 的实际内网 IP，而不是 `127.0.0.1`；
2. 桥配置 `host` 是否为 `0.0.0.0`；
3. Windows 防火墙是否放行 8655；
4. 两台机器是否跨 VLAN/访客网络，存在客户端隔离；
5. 云主机/虚拟机的安全组与虚拟网卡规则。

确需新增防火墙规则时，只放给 AnelfAgent 的 IP，不要对全网开放。
以下命令需要管理员 PowerShell：

```powershell
New-NetFirewallRule -DisplayName "wcauto-bridge" -Direction Inbound -Protocol TCP -LocalPort 8655 -Action Allow -RemoteAddress <AnelfAgent-IP>
```

---

## 7. 频道配置与端到端验收

### 7.1 配置频道

重启 AnelfAgent 后，在 WebUI 配置中心的 `adapter/wcauto` 组设置，
或编辑 `channels/wcauto/channel_config.json`：

```json
{
  "enabled": true,
  "ws_url": "ws://<Windows-IP>:8655/wcauto/ws",
  "access_token": "与桥一致的 token",
  "require_mention": true,
  "allow_write": false
}
```

`access_token` 与桥 token 不一致时，桥会拒绝连接；不要把 `ws_url` 写成
`http://` 或漏掉 `/wcauto/ws` 路径。

### 7.2 分层验收清单

建议按顺序执行，每层通过后再做下一层。

| 层级 | 操作 | 通过标准 |
|---|---|---|
| 进程 | 查看桥控制台/健康端点 | 桥存活，`listener_running=true` |
| 连接 | 查看 AnelfAgent 频道卡片与日志 | `running`；出现“已连接桥 account=...” |
| 收私聊 | 用**另一个微信号**给目标号发“wcauto 接收自检” | 频道收到并写入对应私聊会话 |
| 发私聊 | 让 AI 或通用发送工具给测试联系人发一条消息 | 微信只出现一条，无错误回执 |
| 群聊沉默 | 测试群中不 @你的昵称发言 | 消息入库，但默认不触发思考 |
| 群聊@ | 测试群中 `@你的微信昵称 自检` | `is_to_me=true` 并触发回复流程 |
| 图片 | 第二个账号发一张已查看过的图片 | 自动下载到 `workspace/uploads/wcauto/` |
| 断线恢复 | 临时停止再启动桥 | 频道自动重连；断线期间事件按 seq 回补 |
| 敏感写 | 需要时再开 `allow_write` 并审批 | at/撤回/转发等单独逐项验收 |

**“文件传输助手”适合验证出站，不适合验证完整入站链路**：自己账号发出的
消息会被频道识别为 self echo 并丢弃，防止 AI 自循环。入站测试应使用第二个
微信号或另一位真实联系人。

### 7.3 建议的灰度顺序

1. 仅私聊、仅一个测试联系人；
2. 加一个测试群，保持 `require_mention=true`；
3. 开图片/语音入站；
4. 最后按需开启 `allow_write`，逐项开放敏感写工具；
5. 不要在第一天就开放全部群聊自动回复或标签群发。

---

## 8. 长期稳定运行

### 8.1 收与发的桌面条件必须分开理解

| 状态 | 数据库监听 | GUI 发送/互动 |
|---|---|---|
| 微信登录且运行 | 可工作 | 可工作（窗口可用时） |
| 窗口最小化到托盘 | 不依赖界面操作 | 可能找不到主窗口，需恢复窗口 |
| Windows 锁屏 | 数据库读取不依赖界面；**新消息能否继续到达取决于微信客户端，不保证** | 通常失败，应安全报错 |
| RDP 会话断开 | 同上，不作绝对保证 | UIA 通常不可用 |
| 微信被踢下线 | 无法继续正常监听 | 不可用，需重新登录并复检 |
| 微信更新/重启 | 密钥与 UIA 状态可能变化 | 必须重新自检 |

发送失败时频道应收到明确错误，不能把失败当成成功。遇到结果未知时先对账，
不要自动重发。

### 8.2 电源与锁屏

长期运行机器建议：

- 关闭系统自动睡眠；
- 保持微信用户处于已登录交互会话；
- 通过虚拟机控制台或能持续保留 GUI 会话的方式管理机器；
- 每次改变锁屏/RDP 策略后，都重新做一次真实发送验收。

交流电下关闭睡眠的管理员命令示例：

```powershell
powercfg /change standby-timeout-ac 0
```

这只是防止睡眠，**不等于保证锁屏后 GUI 可用**。

### 8.3 RDP 的正确使用方式

直接关闭 RDP 窗口可能让会话进入不可交互状态，导致发送失败。优先级建议：

1. **虚拟机部署**：使用 VMware/Hyper-V/Parallels 控制台保持会话；
2. 物理机部署：保持本机控制台已解锁，或使用不会断开 GUI 会话的远程管理方式；
3. 必须使用 RDP 时，把“断开后的发送能力”作为单独验收项，不要默认可用。

在支持的 Windows/RDS 配置中，可研究用 `tscon` 将 RDP 会话转回控制台：
先用 `query session` 查会话 ID，再以合适权限执行 `tscon <ID> /dest:console`。
但微软文档明确列出权限与会话限制，它不是所有 Windows 10/11 环境的通用保证；
执行后必须实际发送一条测试消息验证，不能只以命令返回成功为准。

### 8.4 任务计划程序自启动

**不要使用 Windows Service/NSSM 方式运行桥。** 使用任务计划程序，并确保
“只在用户登录时运行”，让桥进入微信所在的交互会话。

图形界面配置（`taskschd.msc`）：

1. **常规**：使用登录微信的同一用户；选择“只在用户登录时运行”；
   不勾选“使用最高权限运行”；
2. **触发器**：登录时，建议延迟 30 秒，让微信和网络先就绪；
3. **操作**：启动程序 `C:\wcauto\bridge\run_bridge.bat`；
   “起始于”填 `C:\wcauto\bridge`；
4. **条件**：按机器电源情况取消不合适的空闲/电池限制；
5. **设置**：
   - “如果任务已在运行，不启动新实例”；
   - 关闭“运行超过指定时间后停止任务”；
   - 可配置失败后每 1 分钟重启、最多 3 次。

需要保留控制台日志时，先创建 `C:\wcauto\bridge\logs`，再把“操作”改为：

- 程序：`C:\Windows\System32\cmd.exe`
- 参数：

  ```text
  /c "C:\wcauto\bridge\run_bridge.bat >> C:\wcauto\bridge\logs\bridge.log 2>&1"
  ```

- 起始于：`C:\wcauto\bridge`

配置后手动运行一次任务，检查：

```powershell
Get-Content C:\wcauto\bridge\logs\bridge.log -Tail 50
```

微软 `schtasks /create` 同样支持 `ONLOGON` 与 `/DELAY`，但带空格路径和
重定向时转义容易出错，首次部署建议使用上述图形界面。

### 8.5 开机自动登录的取舍

如果要求断电重启后无人值守恢复，需要 Windows 自动登录、微信自动启动、
桥登录后自启动三者同时成立。自动登录会降低本机凭据安全性，只建议在
隔离的专用虚拟机中评估使用；不要为了省一次登录而在日常主力机上开启。

### 8.6 风控与并发纪律

上游默认 `natural` 档：写动作间隔约 2.5–6 秒、120 秒内最多 6 次、
超限冷却约 30–75 秒。`calm` 更保守，`fast` 更快但不建议长期挂机使用，
`off` 只应用于对照实验。

可通过环境变量调整：

```text
WECHATAUTO_RHYTHM=natural|calm|fast|off
WECHATAUTO_WRITE_GAP=<秒数>
WECHATAUTO_WRITE_BURST=<窗口内次数>
```

运维规则：

- 不要运行多个桥实例操作同一个微信号；
- 不要同时运行其它微信 RPA/抢红包/自动回复软件；
- 不要让 AI 对所有群消息无差别高频回复；
- 标签群发先 `dry_run` 核对收件人；
- 上游记录过真实风控后被要求重新登录的案例，因此出现“操作频繁”或
  强制下线时，应立即停止自动写操作并排查，而不是加速重试。

---

## 9. 升级、回退与备份

### 9.1 升级桥代码或 wechatauto-replica

推荐流程：

1. 在频道侧暂时停止自动写操作；
2. 停止桥进程；
3. 备份当前整个 `C:\wcauto\bridge`（排除 `.venv` 也可以）；
4. 替换桥代码与 `uv.lock`，保留本机 `wcauto_bridge.json`；
5. 执行：

   ```powershell
   cd C:\wcauto\bridge
   uv sync --frozen
   .\run_bridge.bat --doctor
   ```

6. 完成私聊收发和图片验收后再恢复长期运行。

如果目的是升级上游依赖，而不是仅同步已有锁文件，应在测试环境先修改
`pyproject.toml` 中的精确版本约束，再重新生成锁文件：

```powershell
# 先将 wechatauto-replica==1.2.6.1 改为已评审的新版本，再执行：
uv lock
uv sync --frozen
```

审查变更并验收后再把新的 `uv.lock` 带到生产。**不要在生产启动命令中自动
升级依赖，也不要同时升级微信和上游库**，否则故障难以定位。

### 9.2 微信更新后的复检

微信更新、重启或重新登录后：

1. 重启桥；
2. 运行上游 doctor 和桥 doctor；
3. 核对账号与数据库密钥；
4. 做一次带 `--verify` 的文件传输助手发送；
5. 若 OCR 布局明显错位，按上游文档处理布局缓存，不要直接改死坐标。

UIA 热激活状态会随微信重启/更新清零，库会尝试重新激活，但新版本仍可能
出现锚点或布局变化，所以真实发送验收不可省略。

### 9.3 回退

出现异常时：

1. 停止频道自动写操作和桥；
2. 恢复升级前备份的桥目录与 `uv.lock`；
3. `uv sync --frozen`；
4. 重新 doctor 与私聊收发验收；
5. 若怀疑是微信版本问题，保留日志和版本号，不要连续尝试多个上游版本。

### 9.4 需要备份与无需备份的内容

| 内容 | 位置 | 处理 |
|---|---|---|
| 桥配置 | `C:\wcauto\bridge\wcauto_bridge.json` | 加密/离线备份，含 token |
| 桥代码与锁文件 | `pyproject.toml`、`uv.lock` | 纳入版本管理 |
| 稳定数据库密钥副本 | `%LOCALAPPDATA%\wechatauto_keys\` | 高度敏感，按需安全备份 |
| 工作密钥与解密缓存 | `%TEMP%\wechatauto_db\` | 通常无需备份，可重建 |
| 防撤回镜像 | 默认 `~/Documents/wechatauto_recall`，或配置目录 | 需要保留撤回历史时备份 |
| 桥下载缓存 | 默认 `~/.wcauto_bridge` | 通常无需备份 |
| 日志 | `C:\wcauto\bridge\logs\` | 排障需要，注意脱敏 |

---

## 10. 故障排查

### 10.1 先按链路定位

```text
微信登录状态
  → 64 位 Python / 权限一致
  → 数据库密钥
  → 桥 listener
  → WS 鉴权与网络
  → 频道策略（白名单/忽略/是否@我）
  → AI 回复与发送
  → UIA/窗口/剪贴板/rhythm
```

不要从最后一环直接改配置。先确定消息停在哪一环。

### 10.2 常见问题表

| 现象 | 优先检查 | 处理与复验 |
|---|---|---|
| `uv` 找不到 | PATH/新终端 | 重开 PowerShell，确认 `uv --version` |
| Python 不是 64 位 | §3.3 输出 | 改用 64 位 Python 3.12 后重建 `.venv` |
| 数据库密钥 0 个可用 | 微信登录、位数、同用户同权限、账号选择 | 跑 `diagnose_keys`；修正后重跑 doctor |
| 上游 doctor 正常、桥异常 | 不同 Python 环境、`account`、安全软件 | 统一 uv 环境，核对桥 doctor |
| 图片密钥没有 | 是否查看过图片 | 微信点开任意图片看大图后重试 |
| 图片只有缩略图 | 微信本地存储策略 | 不盲目重试；原图需按上游媒体文档单独处理 |
| 语音下载失败 | 是否播放/接收过该语音 | 在微信播放一次后重试 |
| 视频下载失败 | 本地是否已有 mp4 | 确认已落盘；不要当作解密失败 |
| 频道一直连接中 | IP、端口、防火墙、桥是否运行 | 本机/远程分别 curl 健康端点 |
| 启动即 403/`auth_failed` | 两侧 token | 保持一致；检查 Authorization 与 hello token |
| 非回环地址拒绝启动 | 未配置 token | 配置 token，或改回 `127.0.0.1` |
| 能收不能发 | 锁屏、托盘、RDP、UIA、权限 | 恢复可见交互窗口，做一条真实发送 |
| 发送提示剪贴板失败 | 其它程序占用剪贴板 | 停止其它自动化，串行发送后重试 |
| 同名联系人无法发送 | 昵称/备注重名 | 使用 wxid 明确目标，不随便挑一个 |
| 发送结果“数据库未确认” | 本地落库延迟/分片差异 | 视为未知并查库对账，不立即重发 |
| 群消息入库但不思考 | `require_mention`、昵称匹配 | 确认正文是 `@当前微信昵称`，检查 `self_names` |
| 群/私消息完全不到 | policy、白名单、ignore_chats | 先恢复 open 并清空忽略项做对照 |
| 自己发的消息 AI 不接收 | self echo 防循环 | 属预期；用第二个微信号测入站 |
| 撤回没有原文 | 防撤回未开、镜像建立太晚 | 开 `enable_recall_guard`；只能恢复已镜像消息 |
| 点赞/评论失败 | UIA/OCR、模板、窗口布局 | 先在测试账号验证；不要关闭 OCR 兜底 |
| 频繁冷却/操作频繁 | rhythm 或微信风控 | 降频，必要时 `calm`；不要 `off` 硬冲 |

### 10.3 上游缓存位置（排障用）

| 内容 | 默认位置 |
|---|---|
| 工作数据库密钥/解密缓存/监听水位 | `%TEMP%\wechatauto_db\<account>\` |
| 稳定数据库密钥副本 | `%LOCALAPPDATA%\wechatauto_keys\<account>.json` |
| 图片 AES 密钥 | `%TEMP%\wechatauto_db\<account>\image_keys.json` |
| UIA gate 缓存 | `~/.wechatauto/gate_cache.json` |
| OCR 布局 | `~/.wechatauto/layout-*.json` |
| rhythm 状态 | `~/.wechatauto/rhythm.json` |

不要一遇到问题就删除全部缓存。优先保留稳定密钥，只清理需要重建的工作
解密缓存；删除任何密钥文件前，把它当作敏感凭据处理。

---

## 11. 安全边界

- 桥拥有你的微信读取、发送、转发、撤回、朋友圈互动能力；token 必须按密码管理；
- 只监听内网地址，防火墙只允许 AnelfAgent 所在 IP；
- 当前协议是明文 WS，跨不可信网络时应增加 TLS 隧道或受控 VPN；
- `wcauto_bridge.json`、数据库密钥、稳定密钥副本都不能进入 git；
- 生产默认保持 `allow_write=false`，写工具需要审批；
- 对外部消息内容保持“不可信输入”原则：不要让群成员通过消息文本直接诱导
  AI 调用群发、转发等敏感工具；
- 日志与撤回镜像可能包含私人聊天内容，备份和分享前必须脱敏。

---

## 12. 参考资料

### 12.1 本手册直接依据的上游文档

以下链接固定到本文参考的提交，避免上游更新后内容漂移：

- [GUIDE：安装、首次运行与 doctor](https://github.com/fanyuantaier/wechatauto-replica/blob/62503d76a0db069171cb4e42e87090ad285db489/GUIDE.md#L34-L121)
- [GUIDE：数据库监听不依赖前台 UI](https://github.com/fanyuantaier/wechatauto-replica/blob/62503d76a0db069171cb4e42e87090ad285db489/GUIDE.md#L347-L377)
- [GUIDE：发送失败、密钥、UIA 与 RDP 排错](https://github.com/fanyuantaier/wechatauto-replica/blob/62503d76a0db069171cb4e42e87090ad285db489/GUIDE.md#L1035-L1109)
- [GUIDE：防误发与 rhythm](https://github.com/fanyuantaier/wechatauto-replica/blob/62503d76a0db069171cb4e42e87090ad285db489/GUIDE.md#L436-L472)
- [GUIDE：媒体下载](https://github.com/fanyuantaier/wechatauto-replica/blob/62503d76a0db069171cb4e42e87090ad285db489/GUIDE.md#L474-L718)
- [GUIDE：朋友圈读取与互动](https://github.com/fanyuantaier/wechatauto-replica/blob/62503d76a0db069171cb4e42e87090ad285db489/GUIDE.md#L722-L760)
- [README：发送前回读、OCR 开关与剪贴板](https://github.com/fanyuantaier/wechatauto-replica/blob/62503d76a0db069171cb4e42e87090ad285db489/README.zh-CN.md#L178-L196)
- [README：当前已知限制](https://github.com/fanyuantaier/wechatauto-replica/blob/62503d76a0db069171cb4e42e87090ad285db489/README.zh-CN.md#L897-L917)
- [上游缓存位置说明](https://github.com/fanyuantaier/wechatauto-replica/blob/62503d76a0db069171cb4e42e87090ad285db489/AGENTS.md#L18-L22)

上游 `docs/技术文档.md` 标注为较早版本，其中个别 UIA 描述已与当前实现
不一致；部署行为以当前 GUIDE、README 和实际 doctor/发送验收为准。

### 12.2 uv 官方文档

- [安装 uv（WinGet/安装脚本等）](https://docs.astral.sh/uv/getting-started/installation/)
- [uv 项目同步、`uv.lock`、`--frozen` 与 `--locked`](https://docs.astral.sh/uv/concepts/projects/sync/)
- [uv Python 版本管理](https://docs.astral.sh/uv/concepts/python-versions/)

### 12.3 微软官方文档

- [`tscon`：会话连接、权限与限制](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/tscon)
- [`schtasks /create`：ONLOGON、延迟、权限等参数](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/schtasks-create)
