# AnelfAgent

Electron 本地 APP、浏览器管理页和命令行共用 `app/` 的管理后端。APP 不包含另一套 Anelf
运行时；安装项目后通过 `start.bat` / `start.sh` 启动，退出码 42、崩溃守护及 AI 重启
交接继续由原入口负责。无需使用 APP 时，直接执行 `start` 入口即可。

## 面向使用者

| 平台 | 发布包 | 用法 |
|---|---|---|
| Windows x64 | NSIS `.exe` 安装器 | 按用户安装，不要求管理员权限 |
| macOS Intel / Apple silicon | 各自架构 `.dmg` | 将 APP 拖到 Applications |
| Ubuntu x64 | `.AppImage` / `.deb` | AppImage 独立运行，deb 由系统包管理器安装 |
| 其他 Linux | 源码命令行 | 直接使用原 `start.sh` 或管理 CLI |

安装 APP 后按三个步骤完成部署：

首次进入先选择是否已有 AnelfAgent Git 项目：选「否」按以下流程全新安装；选「是」选择旧项目
根目录，并确认「迁移到安装目录」（默认勾选）及目标路径。取消勾选则直接接入原目录，不移动文件、
不停止原服务。自动读取原项目的 Git origin，默认沿用；接入后检查原环境，完整可用的项目直接进入管理界面。

迁移前检查服务状态；正在运行时询问是否安全关闭，选「否，返回上一步」不执行停服或移动。
确认后等待宿主及管理服务退出，无法安全关停的旧实例需要通过原入口停止。目标必须为空且与原目录
互不包含。Windows、macOS、Linux 均优先使用同卷目录重命名；跨卷才复制至临时目录，逐文件 SHA-256
校验后切换并清除原目录。进度卡显示方式、阶段、已处理字节和总大小，详情按需展开。跨卷复制或校验
可取消，原目录保持可用；短暂的目录提交阶段完成后再返回。中断记录在下次打开时恢复，未校验完成不删除原文件。

项目 Git 历史、未提交更改、配置与数据一起迁移。Python 虚拟环境包含绝对路径，不能直接当作可移动
环境使用（参见 [Python venv 文档](https://docs.python.org/3/library/venv.html)）；旧 `.venv` 保存在
新项目 `logs/manager/migration-backups/` 下，再由 uv 按锁文件准备新路径的环境并完整构建前端。
APP 不自行修改源码或任意配置里的绝对路径；自定义的外部数据位置仍保留在原处。
子模块使用相对路径且 Git 元数据完整位于迁移范围内时可随项目移动。Git linked worktree、外部对象共享、
绝对路径的 Git 关联及无法安全重定位的目录联接应取消迁移勾选，直接使用原目录；检查失败会指出具体路径。
Windows 目录联接（包括 pnpm 的 `node_modules`）保留其联接类型，内部目标随项目重定位，外部目标保留原位置。
跨盘无法表达的相对链接使用绝对目标；复制文件前先检查链接创建能力。目录切换与链接恢复均记入迁移日志，
中断后可恢复；清理临时目录和旧目录时不遍历或删除外部链接目标。
接入的项目始终属于用户，迁移到 APP 数据目录也不获得卸载删除所有权；卸载保留这些项目。

1. **位置与来源**：确认完整项目路径，默认按系统时区推荐 GitHub/Gitee 与国内/国际环境源。
   源码源、环境源和 uv 包镜像放在默认折叠的「调整安装选项」中，展开后可手动覆盖。
   初始页不依赖 Python；点击「开始安装」即隐藏配置，自动依次准备管理环境、项目依赖与前端，
   无需再次确认安装。接入已完成安装的工程直接进入管理页；取消后由用户决定何时继续。
   项目所在磁盘至少预留 **5 GiB** 可用空间，建议 **10 GiB**；这是包含源码、环境、缓存、前端
   构建临时文件的安装余量，非最终占用承诺，模型和用户数据另计。APP 显示实际可用容量，管理
   工具目录另外检查 512 MiB 余量（可能位于另一块磁盘）。下载前重新检测，不足或无法读取时
   暂停安装并提示换目录；网页和 CLI 同样在下载、停服前检查。已有服务的启动和停止不受安装门槛影响。
2. **准备环境**：兼容工具直接复用，缺失项才安装，检测结果可展开查看 uv、Git、Node、Python 的路径与版本。
   默认显示当前阶段、简要进度与耗时；展开后查看真实下载字节数（下载端提供长度时）和命令实时输出。uv 同步会显示
   解析、准备、下载、构建、安装状态与已报告的包数。APP 通过 Windows ConPTY / POSIX PTY
   读取 uv 的真实终端回执，显示并行下载包的名称、字节数和进度条，原始诊断可展开日志查看。没有总大小的
   操作只显示当前阶段，不估算虚假百分比。可取消安装或更新，结束本次安装子进程树后保留已完成
   文件以供重试。服务关闭仍使用安全关停，不强制终止数据库。
3. **完成安装**：Python 依赖、前端依赖、前端构建和校验全部成功后自动进入运行管理页，按需启动、重启、停止或更新。未运行时不显示打开工作区
   的入口；安装不自动打开工作区。配置通过顶部按钮进入，日志在当前页折叠展开。

安装与设置窗口默认宽 640，运行面板宽 570；进入页面时按内容确定紧凑高度，不为安装卡片预留空白。
窗口高度最多 760 像素且不超过屏幕工作区，超过时只由整个页面纵向滚动，不在下载列表内嵌第二层
滚动区域。界面统一采用石墨深色与青蓝色强调；安装默认只显示阶段、简要进度和耗时，直接点击进度卡片
展开或收起包列表、镜像地址与步骤记录，也支持键盘 Enter / 空格操作，不另设「安装详情」文字入口。
安装中的取消按钮居中，详细日志保持独立折叠入口。详情内的并行下载默认展示前三项，其余可继续展开。
未展开时进度刷新不触发窗口伸缩，只有页面切换或
主动展开详情、日志、环境明细时调整高度，收起时缩回。长错误信息同样先显示单行摘要，展开才显示全文。
无侧栏，运行面板使用同一个启动/停止按钮，运行后显示更新并重启。设置通过顶部按钮进入，地址与
端口并排，自动启动与访问密码并排；来源、停止超时、环境修复与清理统一放在默认收起的「高级设置」中。
展开箭头靠右、与标题垂直居中；配置页顶部和底部均可返回管理页。安装与配置页的底部操作按钮统一成组居中。
语言通过中/英小按钮切换。启动中显示最近三行日志。动画遵守系统减少动态效果偏好。
启动就绪后显示「打开 Web 控制台」，点击后使用系统默认浏览器；停止、启动中或连接中断时不可打开。

访问范围可选择「允许局域网访问」（`0.0.0.0`）或「仅本机访问」（`127.0.0.1`）。新安装默认允许
局域网，已有监听配置保持原值；保存需先停止服务，下次启动生效。运行页可展开本机网卡的局域网地址，
本机打开控制台仍使用回环地址。局域网设备需在同一网络，且系统防火墙允许所选端口。
新项目默认无密码，密码框留空；已有密码以固定掩码和「已设置」提示，不读取或回显原密码。
停止服务后可填写新密码，或点击输入框内的移除按钮，统一通过「保存配置」提交；移除在保存前可撤销。
未编辑的密码框保持原密码。常规配置与密码分别写入各自配置源，密码保存失败时明确提示并保留输入供重试。
密码通过目标项目解释器按 `ConfigPaths.WEBUI_CONFIG` 原子更新原有 `auth.password`，保留其他
Web 配置及 API Key；与 Web 登录共用同一配置，不在管理器设置中保存副本。密码经子进程标准输入传递，
不进入命令行、状态响应或操作日志；显式移除密码同时关闭 `auth.strict`，避免重启重新生成密码。
Windows 后台进程使用无控制台和隐藏窗口参数，macOS/Linux 使用原生会话隔离，交互式命令行不受影响。
首次连接显示准备状态；连续连接失败后提示重连，日志读取失败不影响管理连接状态。
来源选择页会检测当前 PyPI、Python、Node、npm 入口，显示地址、耗时与错误；更换来源会重新检测。
检测沿用系统代理和证书，不自动切源。入口可达不代表全部具体资源可达，离线缓存仍可能支持安装。
连接中断会禁用操作；恢复连接后同步真实状态。异常中断后的安装可换源重试。
APP 不会把一次网络失败当成仍在安装，也不会自动开始第二次安装。

默认源码来源为自动：系统 IANA 时区属于中国（如 `Asia/Shanghai` / `Asia/Urumqi`）时使用
`https://gitee.com/eflyz/AnelfAgent.git`，其他时区使用
`https://github.com/1292917512/AnelfAgent.git`。Windows 命令行同时识别中国标准时间名称。
设置可手动指定 GitHub 或 Gitee，显式选择优先。时区只用于默认选源，不进行 IP 定位。

新安装记录所选仓库为 Git origin；已有项目的「自动」沿用当前 origin，保留 SSH / HTTPS 地址。
明确选择 GitHub / Gitee 时，官方仓库切换为对应镜像地址，保留当前分支和未提交文件；后续更新使用该地址。
自定义 fork 不自动映射到官方仓库，需自行通过 Git 配置对应镜像。环境和 uv 包镜像独立设置，不改写源码源。
更新只接受干净工作区与 `git pull --ff-only`，不 stash/reset/覆盖用户文件。依赖同步失败时保留
日志并暂停启动，不假装新版本已就绪。

国内环境源使用 npmmirror 的 Python、Node、MinGit、npm，清华 PyPI/conda-forge，以及中科大 uv
镜像；国际环境源使用各工具官方分发。APP 优先使用本机 uv，没有时用随包内置、经校验的 uv。
`uv / Python 包镜像` 可独立选择清华、阿里云、腾讯云、华为云或 PyPI 官方源，默认跟随环境来源；
它只改变 Python 包索引，不改变 Python 解释器、Git 或 Node 的下载来源。首次准备管理环境前就可选择，
安装页和配置页使用同一份 `downloads.json` 镜像清单，并显示实际地址；网络检查检测所选索引。
取消后在「继续安装」旁直接更换包镜像即可。选择记录在管理状态 `logs/manager/settings.json`，
通过 uv 命令参数传递，不写入下载项目的 `pyproject.toml`、`uv.lock`、`uv.toml`、Git 配置或用户全局配置。
管理环境和指定镜像的包安装使用 `--no-config`，并在子进程中排除会覆盖所选索引的环境变量；
系统代理、证书和外部终端环境保持原样。网络不可达时显示错误，由用户选择其他镜像，不悄悄切源。

镜像地址依据提供方说明：[清华](https://mirror.tuna.tsinghua.edu.cn/help/pypi/)、
[阿里云](https://developer.aliyun.com/mirror/pypi)、
[腾讯云](https://tencent.github.io/CodeAnalysis/zh/quickStarted/FAQ.html)、
[华为云](https://www.huaweicloud.com/special/pypi-jingxiang.html)，均使用 HTTPS 公网入口。
工具发行版本及上游 SHA-256 固定在 `tool-releases.json`；下载完成后校验再解包。Python 下载由 uv
验证，npm 沿用锁文件完整性校验。镜像可能有同步延迟，失败时可取消并切换来源，不跳过校验。

自动选择国际源时使用 `uv sync --locked --inexact --no-dev`。uv 锁文件包含绝对下载 URL，因此国内
模式或手动指定包索引时先以 `uv export --locked --no-dev --no-emit-project` 导出固定版本、平台标记及哈希，再使用
`uv pip install --no-config --require-hashes --no-deps` 从镜像安装到项目 `.venv`。覆盖规则已包含在
锁文件中，此安装步骤不再发现项目或用户配置，防止覆盖规则替换导出的固定版本及哈希。不修改 `uv.lock`，保留
额外安装的包，不向虚拟环境安装 pip。不是简单修改索引环境变量却仍从锁内官方 URL 下载。

中断或失败后，保留项目、`.venv` 和 uv 缓存。重新打开 APP 会识别未完成的已有工程并提供
“继续安装”；uv 检查并补齐锁定依赖，匹配版本的已安装包不重复安装，可用缓存继续复用。
依赖阶段显示现有虚拟环境检测到的已安装包数，再由 uv 核对版本并安装缺失项。换镜像不重建 `.venv`，
不删除项目缓存或用户额外安装的包；尚未下载完整或只有旧镜像索引缓存的资源，换源后可能仍需重新下载。
下载/构建成功不等于已安装：uv 可能在准备完所需包后才执行安装，因此界面只在完整构建校验后宣布完成。

项目虚拟环境固定在项目 `.venv`，不会继承其他工程的 `VIRTUAL_ENV` 或 `UV_PROJECT_ENVIRONMENT`。
uv 是独立工具，不要求预装 Python。APP 优先检测 PATH、用户 `.local/bin` / `.cargo/bin` 中的 uv；
找不到可用 uv 才使用内置版本。准备管理环境前，通过 uv 查找当前用户受管 Python、PATH 和
Windows 注册表中的兼容 Python；不会因改用 APP 自有目录而漏掉用户的全局 uv 解释器。
项目依次检查已有 `.venv`、可共享的管理解释器、项目受管目录和用户/系统安装，只下载缺失的版本。
APP 自建项目可以共享管理器解释器；接入的已有工程不得依赖卸载时会移除的管理器解释器。
管理后端通过 `uv run --no-project --isolated` 准备独立依赖环境；这不等于再次下载 Python。
Git 与 Node/npm 分别管理源码和前端。全局 Python 包不混入项目，依赖仍由 uv 根据锁文件同步到 `.venv`。
终端适配依赖固定在 `app/runtime-requirements.txt`，也由 uv 通过所选 PyPI 源安装到管理环境，
不会写入项目依赖。源码 CLI 未安装这些可选适配器时仍使用普通流式日志与包阶段回执。
APP 验证安装成功后，以 `ANELF_ENV_READY=1` 调用原 `start` 入口，使用已同步的 `.venv`，避免启动时
重新同步并绕过所选镜像。直接命令行启动仍保留原有的环境同步与守护流程。

### 旧检出的启动兼容

发行包带有同一份 `start` 脚本与 `core.host_control` / `core.host_protocol`。如果所选检出的
启动脚本不支持 `ANELF_LAUNCH_ENTRY`，APP 使用随包脚本，将 `ANELF_PROJECT_ROOT` 指向工程，
仍由该脚本处理退出码 42 和崩溃恢复，不创建另一套守护循环。
`app/runtime_entry.py` 调用项目原有 `launch.main()`；仅当项目未注册 HostControl 时，才在单实例
锁和初始化完成后补注册到同一 Lifecycle，并使监听地址和端口使用 APP 配置。已有控制服务不重复注册，
不会改写 Git 工程文件；后续更新仍可使用 `git pull --ff-only`。启动成功以宿主生命周期就绪为准，
不以进程创建或 PID 文件存在为准。

升级前已经运行、且没有控制接口的进程无法被新版本热补接口；APP 明确显示该实例并阻止重复启动，
需要先通过原入口安全退出一次。新启动使用兼容入口后，停止和重启都走宿主原有的安全清理流程。

macOS/Linux 缺失 Git 时通过 micromamba 安装到项目工具目录；国内使用清华 conda-forge。
不写全局 PATH、Python 注册表或全局 npm 配置。工具缓存和管理器 Python 均在 APP 自有目录内。
安装需要联网，镜像可达不代表所有项目依赖都能下载；完整联网安装需要在目标系统验收。

### 安装目录、升级与卸载

Windows 默认结构如下；安装器可选择 APP 安装目录：

```text
<安装目录>/
  AnelfAgent.exe
  data/
    project/          # Git 下载的 AnelfAgent 工程、.venv、配置、运行日志
    tools/            # 管理器 Python 与下载缓存
    runtime/          # 管理后端源码缓存
    profile/          # 桌面设置与浏览器缓存
    projects.json     # APP 创建 / 用户接入的项目所有权记录
```

源码开发时同类临时内容在 `app/data/`。APP 升级只替换程序文件，保留 `data/` 与项目；明确卸载时，
Windows 安装器先请求取消安装、等待安全关停、确认管理进程退出，再清理 APP 自建工程、环境、缓存
和程序文件。无法安全停止则中止卸载并提示，不在进程仍运行时宣称清理成功。工程目录和所有权标记
必须匹配后才允许递归删除；接入的已有工程不取得删除所有权。

macOS 和 deb 应用目录受系统/签名保护，运行数据使用系统用户应用数据目录；AppImage 使用镜像文件
所在目录下的 `data/`。macOS/Linux 删除 APP 前，在配置页执行「清理 APP 安装的数据」，确认清理
列出的 APP 自建项目并退出，再删除 APP/卸载 deb。系统卸载器不能代表所有用户执行用户数据清理。
这些平台的实际安装/卸载需通过对应设备验证。所有平台均保留用户主动接入的已有工程。

点击窗口关闭按钮时隐藏到系统托盘，保留窗口会话、安装进度和运行服务，不弹退出确认。
点击托盘图标恢复窗口；再次启动 APP 同样唤回原窗口，不创建新实例。
托盘右键菜单跟随当前项目状态和界面语言：未安装完成时显示「打开安装界面」，完成后显示「打开控制面板」
和「打开 Web 控制台」。Web 入口只在服务就绪、管理连接有效时可用，点击时重新核对本地服务状态，
已停止或断连时回到管理页，不打开失效地址。「打开设置」直达配置页；首次准备管理环境前则展开安装选项，
准备中暂时禁用。菜单复用现有状态刷新，不启动另一个服务或另设轮询。
Windows/Linux 使用通知区域，macOS 使用菜单栏模板图标；Linux 桌面需要支持系统托盘，
托盘不可见时仍可通过再次启动 APP 恢复窗口。窗口最小化按钮保留系统原有行为。

通过托盘菜单「退出」或操作系统的应用退出命令退出时，先检测项目实际服务：已停止、仅有空闲管理后端时直接退出，不显示确认框。
实际服务仍在运行或正在启停时才显示简短确认框，「同时安全停止服务」默认不勾选。不勾选则只退出 APP，后台服务
和安装任务继续运行；下次打开会验证并连接该项目已有的管理服务，复用原实例，不重复启动。
勾选后先取消安装、等待当前操作结束，再通过公共控制接口停止本次打开的项目服务与管理后端，
确认退出后才关闭 APP。安全停止失败时保留窗口并显示错误，不将后台仍在运行误报为已退出。
APP 的退出选择不删除工程或环境文件；独立命令行入口仍可使用。
WebUI / AI 发起重启时，APP 自动跟随同一份运行状态，无需手工刷新或再次启动。

## 不使用 APP

```powershell
# Windows：直接启动应用，含原有守护循环
.\start.bat
.\start.bat stop             # 安全停止
.\start.bat restart          # 请求现有实例重启
.\start.bat update           # 更新、同步环境，原来运行中则恢复运行
.\start.bat status           # 查看状态
# 只打开浏览器管理页，不自动启动应用
.\start.bat --manager
```

```bash
# macOS / Linux
./start.sh
./start.sh stop
./start.sh restart
./start.sh update
./start.sh status
./start.sh --manager
```

日常只需这一个 `start` 入口，不需要额外的 Windows stop/restart 脚本。`stop`、`restart`、`status`
直接使用现有运行时控制面，不要求 APP 或管理服务已打开。没有运行实例时停止为无操作，重启会
明确提示未就绪；不支持控制协议的旧实例需要在原终端安全停止，不会报告假成功。

旧的 `stop.sh` / `restart.sh` 保留为 POSIX 兼容入口。跨目录安装等维护操作仍可用
`python -m app --help`，通过 `--root` 指定目标检出；普通使用无需记忆这些内部命令。

CLI 管理器只需要 Python 标准库；可以使用 `uv run --no-project --python 3.12 python -m app`。
`serve --port 8090` 前台运行管理服务，`--port 0` 由系统分配端口。管理页始终只绑定回环地址。

## 运行契约

- `start` 脚本是应用守护的唯一实现。管理器启动这两个脚本，不维护第二套崩溃/重启循环。
- `core/host_control.py` 经组合根注入重启函数、经 Lifecycle 注册。在取得实例锁后发布
  `logs/manager/runtime.json`，直接 `start` 和 APP 启动均可被发现。
- APP 重启通过带进程令牌/PID/请求 ID 的信箱进入既有 devops `request_restart`，与 Web 重启走
  同一路径；AI 的等待空闲、留言交接语义不变。直接 `python launch.py` 无守护时仍拒绝重启。
- Lifecycle quiesce/cleanup 发布 restarting/stopping/stopped 状态。APP 读取这份状态，避免把
  Web 触发的重启误判为停止并再次拉起。停止等待应用与入口进程退出，超时不强杀数据库。
- 安装、更新和 AI 运维共用 `logs/project-operation.lock`。重启请求将锁交还既有 devops 服务。
- 管理器不接管没有控制协议的旧实例；通过旧入口安全关闭后再切换。管理服务关闭只停止自己
  拉起的入口，独立 `start` 启动的应用继续运行。
- WebUI 监听设置通过 `ANELF_WEBUI_HOST` / `ANELF_WEBUI_PORT` 仅覆盖 APP 启动的子进程，
  不隐式改变认证设置；访问密码只有用户明确设置或移除时才更新。端口冲突时拒绝启动/保存，不终止占用者。
- 管理 API 校验 Bearer 令牌、Host 与 Origin。前端只访问固定端点；Electron 渲染器启用
  contextIsolation/sandbox、关闭 Node 集成。preload 仅暴露固定的安装、目录、窗口和托盘操作，
  不提供任意命令执行接口；主进程校验 IPC 来源，托盘访问 Web 前重新核实服务状态和本机地址。
- `uv sync --locked --inexact --no-dev` 保留已装额外 Python 包，不重写锁文件。用户配置、
  数据、凭据、模型及工作目录不随 APP 发布；卸载仅清理已记录所有权的 APP 自建项目。

运行状态、便携工具和安装日志均位于目标项目 `logs/manager/`；该目录被 Git 忽略。Electron
在上述 APP 数据目录保存项目选择与按内容哈希隔离的管理后端缓存，不移动用户项目。`auto_start` 为管理服务启动时运行
Anelf，不代表操作系统开机自启。

## 构建与发布

APP 源码在 `app/desktop/`，依赖使用 `pnpm-lock.yaml`。仅在开发/发布 APP 时需要 Node 24
与 pnpm 11.25；最终用户不需要这些构建工具。
发布者元数据统一为 `anelf.cn`，官网地址为 `https://anelf.cn`；在 `desktop/package.json` 中维护，
用于应用文件信息、安装器的发布者与官网链接，以及 Linux 软件包的维护者信息。

```bash
cd app/desktop
pnpm install --frozen-lockfile
pnpm typecheck
pnpm test
pnpm stage
pnpm start                  # 开发运行 APP
pnpm dist                   # 在当前系统生成原生发布包
```

启动器的文档、测试、源码和构建内容集中在 `app/`：本文件为使用与维护入口，`desktop/`
为 Electron 外壳，`ui/` 为共享界面，`tests/` 为后端测试。发布包统一输出到 **`app/dist/`**；Windows 该目录仅保留一个可分发 EXE。
解包程序、blockmap 和打包日志均在 `app/build/package/`，其他临时打包资源在 `app/build/`。这些目录与 `app/data/`、`app/project/` 均在 `app/.gitignore` 中，不提交 EXE、APP、DMG、
deb、AppImage 或解包目录。stage 只收集管理后端、终端适配依赖清单、
共享网页、宿主协议与经 SHA-256 校验的 uv，不递归复制项目或用户目录。

`.github/workflows/launcher-release.yml` 在原生系统上分别构建 Windows x64、macOS x64/arm64、
Ubuntu x64。手动运行 workflow 默认仅生成 Actions artifacts。版本以根 `pyproject.toml` 的 `[project].version`
为准；开发启动、stage、打包和发布检查通过 `desktop/version.cjs` 同步到 APP 的 `package.json`，
界面显示、安装包元数据与文件名保持一致，不单独递增 APP 版本。发布版本使用 `主.次.修订` 格式。
发布时先等待源码提交的 CI 通过，再推送与项目版本一致的 `v<版本>` 标签（也兼容 `launcher-v<版本>`）。所有平台构建成功后，
发布任务再次核实该提交的 CI，检查五个安装包并生成 `SHA256SUMS.txt`；附件上传至草稿后才公开 GitHub Release。
发布说明保存在 `app/RELEASE_NOTES.md`，发版时同步维护。主 CI 同时检查 APP 类型、桌面逻辑和无界面浏览器交互。
同版本修复可在该提交 CI 通过后手动运行并勾选 `replace_existing`：仅替换已有公开 Release 的附件和校验清单，
不新建 Release、不移动或新建标签；目标发布不存在时失败退出。发布说明会记录实际构建提交，便于追溯对应源码。
Linux x64 的文件名遵循打包格式：AppImage 使用 `x86_64`，deb 使用 `amd64`，均对应同一 64 位架构。
发布前将对应项目源码同步到 GitHub 与 Gitee，确保自动选源的用户获得相同的控制协议。
Gitee 源码使用者仍可下载相同安装包；工作流不自动发布到 Gitee Releases。

未配置签名证书时生成未签名的公开测试包，不代表已通过 Windows 签名或 Apple 公证；正式面向
用户分发前应在发行环境配置平台签名。Linux 不含桌面环境时使用 CLI。其他平台的产物和实际安装
必须由对应 CI/设备验证，不以 Windows 本机的静态检查替代。

针对性验证：

```bash
uv run pytest app/tests tests/unit/core/test_host_control.py entities/devops/tests tests/unit/core/test_launch_contracts.py
uv run ruff check .
uv run lint-imports
uv run mypy core app
```

真实联网下载、用户系统安装、模型和频道运行属于人工验收。测试中的临时进程不代表这些条件已验证。

界面隔离验收（需要项目 Web 前端已有 Playwright 依赖和浏览器）：

```bash
node app/desktop/tests/ui.smoke.mjs
node app/desktop/tests/bootstrap.smoke.mjs
```

该检查使用临时 HTTP 数据和无界面浏览器，不操作已安装 APP，不启动真实 Anelf 服务。
应用图标以 `desktop/icon.svg` 为唯一矢量源，A 形轮廓、中心光点与环绕轨迹用于表达自主智能和持续运行。
修改后执行 `pnpm --dir app/desktop icon`，使用同一套无界面浏览器生成安装器所需的 1024 像素
`icon.png` 与 macOS 的 1×/2× 托盘模板图标；这些图标是随源码维护的设计资源。
初始安装页和管理页共用 `desktop/theme.css` 与矢量图标，打包时只复制这两个公共资源到管理后端。
下载源契约参考：[uv Python 镜像](https://docs.astral.sh/uv/configuration/environment/#uv_python_install_mirror)、
[清华 PyPI](https://mirror.tuna.tsinghua.edu.cn/help/pypi/)、
[中科大 GitHub Release 镜像](https://mirrors.ustc.edu.cn/help/github-release.html)。
