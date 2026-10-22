# 运维与实体设施

进程守护、崩溃恢复、重启交接、凭据中心、本地模型资产、操作实体与用户钩子的现状说明。修改
`entities/devops/`、`entities/operation/`、`entities/system/`、`agent/hooks/` 或守护脚本时按需阅读。

## 进程守护与关停

| 机制 | 位置 | 说明 |
|------|------|------|
| 单实例守卫 | `core/instance_guard.py` + stop.sh / restart.sh | `logs/anelf.pid.lock` 操作系统文件锁防止同一检出双写；存活旧 PID（含升级前实例）拒绝接管，不发终止信号。POSIX 用 `bash stop.sh` 先核实双方项目归属，再依次向守护与应用发 SIGTERM，不能等守护退出才通知应用；共用默认 600s 等待预算，`ANELF_STOP_TIMEOUT_SECONDS` 可调，每 5s 输出等待 PID，超时失败但不强杀。restart.sh 只在停止成功后启动；锁文件不手工删除 |
| 重启看门狗 | `entities/devops/service.py` | 排定关停后 90s 检查，存在 `Lifecycle.shutdown_blockers()` 时每 30s 告警并继续等；只有全部关键资源关闭后才允许 `os._exit(42)` 处理残留非守护线程。等空闲路径在关停请求发出后才布防 |
| 崩溃守护与通报 | `start.sh`/`start.bat` 守护循环 + `core/crash_report.py` + crash_recovery | 致命信号退出（SIGSEGV 等，退出码 128+n；SIGKILL/SIGTERM 不重启）自动退避重启（5×次数秒，上限 60s），崩溃状态落盘 `logs/crash_state.json`，连续 5 次崩溃停止拉起防崩溃循环（稳定运行 ≥600s 后崩溃重置计数）；重启后 crash_recovery 消费崩溃状态并关联 macOS .ips 生成崩溃上下文——有回复检查点随中断元消息注入对应会话，无检查点经 PushHub 写全局通知并唤醒一轮思维；状态标记 reported 只通报一次。AI 详情查询走 devops `get_crash_report` 工具 / 面板 `/crash-info` |
| 重启交接闭环 | `entities/devops/service.py`（交接落盘 + wait_idle 重启）+ RestartHandoffWatcher provider | AI 调 restart_app 等可传 `message` 给重启后的自己留言；交接（owner scope / 回复路由 / 留言）落盘 `<data_dir>/restart_handoff.json`（拒绝不写，已排定重复调用仅在留言非空时更新）；`wait_idle=True` 等思维空闲（120s 上限）让当前回复轮自然收尾。bootstrap 末尾 provider 消费交接（读即删只消费一次；超 1h TTL 仅清理不投递），延迟 5s 推送「重启成功 + 留言」并唤醒一轮思维。Web/API 重启路径不写交接、不等待 |
| 关停预算与存储保护 | `core/lifecycle.py` + `core/application.py` | 先同步触发 `on_quiesce` 封闭工作入口，再前置钩子和逆序清理；关停不新建记忆模型提取，持久化进度交后续心跳恢复。各组件记录开始与结束耗时。默认 45s 总预算只裁剪普通组件；`critical_cleanup=True` 的存储即使预算耗尽或外层取消仍等待关闭，失败则保留进程、依赖及实例锁并告警。Cognee 候选工作进程树可撤回并有界回收，不等同于强杀宿主；原生库仍须正常关闭。重复 SIGINT/SIGTERM 不升级强杀；后置解锁只在关键存储全部关闭后执行 |
| Web 绑定 fail-fast | `web/server.py::WebServerService._serve` | uvicorn 绑定失败不苟活——检测 `server.started` 请求优雅关停，半活实例（Agent 在跑而 WebUI 不可达）不再存活 |
| 实时引擎入 Lifecycle | bootstrap 注册 `realtime_engine`（cleanup=shutdown_all_sessions） | 进程退出不依赖 WS 断连的隐式清理；每会话有界（5s）收尾 |
| 子进程看护 | `entities/filesystem/child_guard.py` + shell_background 登记 | 后台 shell（独立进程组）的 owner-death 守卫：pgid 登记 logs/shell_children.json，启动清扫上次实例孤儿（SIGTERM→5s→SIGKILL 整组），关停终止全部在册子进程；裸 create_task 收编（async_helper.spawn 受管） |

## 组件凭据中心（core/provider_keys.py + config/provider_keys.json）

模块经 `register_provider_key` 声明凭据字段或专属授权编辑器，归属从实际注册模块解析。
实体从 `entities._sdk`、频道从 `agent.channel` 接入，核心功能直接使用 `core.provider_keys`。
Web 凭据目录按实体、频道、核心分类为等高摘要卡片；点击后才挂载字段或模块授权编辑器。
实体配置页与频道面板复用同一归属筛选视图。关闭编辑器前保护未保存草稿，保存失败保留输入。
字段只由模块声明，不自动添加 API Key。普通字段显示实际值，敏感字段仅返回掩码；保持原值无需
回填，清除需要显式操作后保存。每张卡片经一次请求原子保存变更，缺省字段保持不变。

通用字段仍写入 gitignored 的 `config/provider_keys.json`，Web、配置文件与 AI 凭据工具共用
单一真源。模块卸载保留已配置值，未登记字段全部按敏感信息处理；重载恢复声明。
自定义授权的存储、状态与接口由所属模块持有，目录不复制 token、不自动发起登录或网络探测。
专属授权条目的 `configured=null` 表示状态由模块编辑器查询，并非未配置。
本地模型资产管理保留在设置页面，不混入凭据列表。

**配置归位原则**：大模型类生成继续走 llm_clients 模型配置；实时/特殊协议的语音等模块凭据只走
凭据中心（dashscope 脱离 llm_clients 扫描，环境变量兜底），非凭据参数（模型名/音色/优先级）留
各自配置组。

**MCP server 凭据**：各 MCP server 凭据以字面量存 gitignored 的 `config/mcp_servers.json`
（stdio 走 env、远程走 headers，配置层不做环境变量展开）。百度小度双 MCP（`xiaodu-iot` 设备
控制 / `xiaodu` 智能终端）共用同一 ACCESS_TOKEN：有效期 30 天、隐式授权过期不可刷新，到期于
DuerOS 控制台（dueros.baidu.com/dbp/mcp/console）重新授权并同步更新两条 entry。

## 本地模型资产（agent/model_assets.py + /api/local-models）

- 登记制资产（id/来源/版本/SHA-256 固定，如 silero_vad、smart_turn）；落盘 workspace/models/
  （AI 工作路径内，文件工具可直接查看）；流式下载 + 哈希校验 + 原子替换 + 进度可查，同资产并发
  请求合并单飞，下载中拒绝删除；直连受限网络自动回退镜像源（model_asset_mirror：auto 回退
  hf-mirror / off / 自定义前缀）
- Web 设置页「本地模型」页签与 AI 工具 `list/download/delete_local_model`（voice 组 core）同一
  管理面——语音端点降级时 AI 可自主补装模型恢复满配
- **运行时安装**：`entities/system/python_service.py::install_packages/uninstall_packages` + 工具
  `install/uninstall_python_packages`（environment 组）；uv 管理环境自动走 uv pip、其余走 pip；
  模型缺运行时依赖（onnxruntime）时面板一键安装

## GPU 模型层启停（voicehub :10096 / face :10097）

模型层启停——进程常驻不死、模型显存可释放、下次推理自动重载（跑大模型前一键腾显存）：

- **GPU 状态/释放客户端**（`entities/audiosync/client.py::gpu_status/gpu_unload`）：GET /gpu/status
  聚合四组件加载态；POST /gpu/unload 走 JSON body {"targets":[...]}（缺省全部）——**必须走 body**：
  query 形态传无效 targets 会被静默当作全部卸载
- **声音页 GPU 卡**（`entities/audiosync/router.py` + ServiceStatus.tsx 插槽）：四 worker 行 +
  全部释放（ConfirmDialog）；查询仅在 funasr reachable 时启用
- **face 引擎释放**（`agent/vision/face/engine.py::unload` + POST /face/engine/unload）：face 独立
  服务，单独释放；/health 的 loaded 字段解析（None=老服务未上报）
- 归因纪律：web 层错误映射一致（未配置→503、服务失败→502）

## 操作实体（entities/operation/）

操作 = 可注释/可停用的能力单元：内置桌面动作（desktop.*，九个 pyautogui 动作，不可删）+ MCP
关联操作（mcp.{server}.{工具}）；注释与启停一视同仁（线程锁+原子写）。

- **关联 ≠ 执行通道**：MCP 关联是**语义索引**——把常用工具提升为带注释的一等操作注入操作态势，
  让 AI 在操作语境直接知道「有哪些语义化能力」；**实际执行仍走 mcp:<server> 工具组**
  （activate_tool_group 激活）——不设第二执行通道
- **桌面执行器**（`desktop.py` + pyautogui 声明依赖）：全部动作 to_thread + 30s 超时；FAILSAFE
  开启（鼠标猛移屏幕左上角物理中止）；type 仅 ASCII（中文提示剪贴板+hotkey 粘贴）
- **看屏验证联动**：desktop_act(verify=auto/on/off) → `_sdk.vision_look("screen")` 视觉桥——动作
  成功后自动截屏，结果按多模态契约附最新画面帧（AI 直接「看到」操作后果）；视觉源不可用静默回退
- **按需态势注入**（provider：operation，priority 36）：仅操作活跃窗口内有执行才注入
  （operation_context_window_seconds 默认 600s 滑动续期），静默期零注入；分段预算 + skip-not-stop；
  未关联 server 的工具清单不进注入
- **AI 工具面**：desktop_act / list_operations / register_mcp_operation / remove / update /
  operation_status（operation 组）

## 实体操作态势注入（entities/_ops.py + 文件/SSH 追踪）

- **操作回报装饰器**（`ToolOp` / `track_ops`）：工具执行后把操作事实（scope/工具/目标/成败/耗时）
  回报给实体的态势追踪器；实体侧以 `track_fs_op`/`track_ssh_op` 绑定，单点接入。
  失败判定复用 `core.tool_results`，退出码与 stdout/stderr 证据进入有界摘要，不另设判定规则
- **文件操作态势**（`entities/filesystem/ops_context.py`，provider `fs_ops`）：按会话追踪当前 Shell
  目录 + 活跃目录 + 最近操作流水；停止操作超 `os_context_ttl_seconds`（默认 600s）注入自动消失。
  **目录说明文档规则**：当前目录优先、最近活跃目录其次，按 `os_context_doc_names`（默认
  AGENTS.md,README.md，仅纯文件名防路径引导）注入，mtime 缓存自动失效，配额受限；可关
- **SSH 操作态势**（`entities/ssh/ops_state.py`，provider `ssh_ops`）：按 (会话, 连接) 追踪——只展示
  本会话近期操作过的主机（与全局花名册 provider `ssh_status` 分工）；远程目录说明文档操作成功后
  经旁路通道后台抓取（30s 最小复用窗口），渲染零远程 I/O。操作记录保留失败证据及耗时，
  并明确每次命令是新非交互会话；export、别名、虚拟环境激活不跨命令持久，不根据登录名猜测 sudo 权限
- **SSH 远程目录持久跟踪**（`manager.py`）：ssh_exec 命令经 POSIX pwd 捕获尾块包装（brace 组内 cd
  失败则无标记、退出码语义不变），捕获目录对后续命令生效（与本地 shell cwd 语义对齐）；标记缺失
  且有目标目录时（可能 cd 失败或命令提前 exit）清除持久目录；不把请求目录冒充已确认目录，非 POSIX 远端可关
- **SSH 执行回执**：AI 与 Web 共用退出码、原始输出、登录身份、请求/已确认目录、网络策略与耗时。
  非零码通过统一错误设施附摘要与核查指引，不等同 SSH 断线，也不保证复合命令未生效；
  执行中断线标记结果未知、不可盲目重试，取消时关闭执行通道

## 文件扫描与读取防线（entities/filesystem/scan.py）

- **扫描剪枝**：os.walk 按目录名剪枝（默认 .git/node_modules/__pycache__/.venv/dist/build/各类
  缓存，`search_exclude_dirs` 可配置）——不进结果也不再向下遍历；glob 语义（裸 `*.png` 任意深度、
  `**/` 零目录语义）；内容模式跳过二进制扩展名与 >2MB 大文件；结果 path 保持绝对路径
- **二进制嗅探**（`looks_binary`，前 8KB NUL 采样）：read_file 扩展名表之外的内容级防线——文本
  读取走 `errors="replace"` 永不抛解码异常；命中返回 `{"type":"binary"}` JSON 引导媒体工具

## AI 桌面订阅额度

百炼 Token Plan 额度由 `entities/ai_desktop/modules/subscription/bailian.py` 调用官方
`bl usage token-plan --output json` 只读查询。Bailian CLI 是可选依赖，环境设置显示安装状态与
[安装指南](https://bailian.aliyun.com/cli/install.md)。模型 API Key 不能替代控制台授权。

各平台默认关闭，Web 的 AI 桌面设置和 AI 的 `ai_desktop_modules` 共用配置。AI 可先 `list`
查看配置，再以 `action=set_config, module=subscription, name=provider_bailian_enabled,
value=true` 启用平台（其他平台短名见配置清单）。未启用的平台不查询、不显示、不注入上下文；
关闭立即隐藏，已发出的只读查询允许完成。

百炼在组件凭据卡片扫码登录，额度面板提供跳转入口，授权写入 `ConfigPaths` 配置目录下的 `ai_desktop/bailian/config.json`，
重启后复用，不创建模型 Key，也不改变模型配置。点击登录直接通过阿里云扫码接口获取二维码，
扫码后在弹窗确认控制台授权；已开启额度监控时刷新额度。授权不隐式开启监控。无需浏览器、截图或额外运行包；已有依赖随 `uv sync`
安装。扫码 Cookie 和票据只保留在有界 HTTP 会话内，取消、过期或组件卸载时回收；二维码只传输
一次，后续仅轮询状态。网络请求使用统一网络 SDK。需要额外身份验证的账号提示前往官方控制台
处理，不绕过验证。授权保存成功与额度查询成功分开呈现，额度刷新失败不会要求重新扫码。

也可在运行应用的同一系统用户下执行 `bl auth login --console`；未保存组件专用授权时复用此
CLI 会话。授权失效后暂停查询，更新凭据文件或重新扫码后恢复，不反复向 AI 提醒登录。

供应商通过各自的凭据解析与采集函数返回统一快照；并发查询、单家失败隔离。界面与模型注入
共享缓存，百炼未登录时只显示登录入口。月度用量比例按 0～1 转为剩余百分比，缺失值保持未知。

## 工具面纪律

- **python_exec 输出落盘**：stdout 超 30000 字符经 `shell_state.truncate_or_persist` 落盘
  （`.tool-results/` + persisted 路径，模型 read_file 分段取回）——与 run_shell_command 对称；
  stderr 仍 1000 字符小限截断（多为回溯/警告）
- **只读并发标记**：纯读工具标 `concurrency_safe`——与写工具同轮混发时不再被切进串行批，并行
  机会不流失
- **声明超时对齐**：工具注册的 timeout 必须覆盖 AI 可传参数上限（如 web_download(timeout=300)）；
  未声明落入全局默认 60s 会提前掐断 AI 传的更大值（与 MCP call_timeout 同款错配）

## 代码编排实体（entities/codebox/，沉睡分组 `code`）

一次性子进程跑模型写的 Python（cwd=workspace，子进程自设 rlimit 内存/CPU，墙钟超时父侧强杀）；
stdin/stdout JSON 行协议桥接注册表工具（runner.py 纯 stdlib 按路径直执）；每次子调用过统一审批门
+ `EntityRegistry.execute_tool`；输出/编排/交互类工具脚本内不可用（排除即「脚本不能回复用户」）；
子调用结果头尾截断（脚本可用 offset/limit 自行分页精读）。否决 OS 级沙箱的决策见
[设计决策](design-decisions.md)。

## 用户 hook 事件面（agent/hooks/ + services/hooks.py + web/routers/hooks.py）

`config/hooks.json` 声明 tool_pre/tool_post/reply_end 脚本。条目字段为
matcher（工具名 glob）、command、timeout（1–60 秒）、enabled、mode（sync/async）。
同步脚本按顺序等待；只有同步 tool_pre 的 exit 2 能阻止调用（stderr 为理由，deny 优先）。
异步脚本进入有界队列，不阻塞主流程，也不改写或撤销已投递的消息。
总开关 `hooks_enabled` 在 system/hooks 组，空配置零开销。

脚本 stdin 收到 JSON 事件，同时提供 HOOK_EVENT/HOOK_TOOL 环境变量。
后台最多 4 条并发、64 条在途；输出各保留 16 KiB，超时/取消回收所创建的进程树。
宿主由 Lifecycle 管理，关停取消未完成脚本。后台失败写入执行记录，不误作工具拒绝。

钩子页提供执行流程、自动召回、脚本配置和 LLM 钩子观测。脚本保存时沿用运行时
校验并规范化写盘；测试实际执行已保存脚本，并等待后台脚本结果。测试不受总开关影响，
事件的 enabled=false 条目仍跳过。样例见 `config/hooks.example.json`。

`core/hooks.py` 统一发布运行模式、来源会话、轮次、耗时和结果。工作区按轮次展示，
钩子页在内存保留最近 100 次执行；进程重启后清空。消息正文不经钩子自动投递。
LLM 钩子由独立宿主负责上下文与模型预算，见[思维分册](mind-architecture.md)。
