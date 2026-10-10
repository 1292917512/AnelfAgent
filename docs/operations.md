# 运维与实体设施

进程守护、崩溃恢复、重启交接、凭据中心、本地模型资产、操作实体与用户钩子的现状说明。修改
`entities/devops/`、`entities/operation/`、`entities/system/`、`agent/hooks/` 或守护脚本时按需阅读。

## 进程守护与关停

| 机制 | 位置 | 说明 |
|------|------|------|
| 单实例守卫 | `core/instance_guard.py` + restart.sh | 启动写 `logs/anelf.pid`（项目目录天然按检出副本隔离实例身份），PID 文件指向的活进程经 cmdline 校验（本项目 launch.py）判定为残留实例时 SIGTERM→10s 宽限→SIGKILL 清场接管端口，cmdline 不匹配只警告不误杀（防 PID 复用）；僵尸进程经 psutil status 判定视为已死。restart.sh 优先 PID 文件精准终止，pkill 兜底模式收紧到 `$ROOT/.*launch` |
| 重启看门狗 | `entities/devops/service.py` | restart_app 排定关停后 90s 进程仍存活（优雅关停卡死）→ 无条件 `os._exit(42)` 保底，守护脚本必然接管；等空闲路径在关停请求发出后才布防（防等空闲误触发） |
| 崩溃守护与通报 | `start.sh`/`start.bat` 守护循环 + `core/crash_report.py` + crash_recovery | 致命信号退出（SIGSEGV 等，退出码 128+n；SIGKILL/SIGTERM 不重启）自动退避重启（5×次数秒，上限 60s），崩溃状态落盘 `logs/crash_state.json`，连续 5 次崩溃停止拉起防崩溃循环（稳定运行 ≥600s 后崩溃重置计数）；重启后 crash_recovery 消费崩溃状态并关联 macOS .ips 生成崩溃上下文——有回复检查点随中断元消息注入对应会话，无检查点经 PushHub 写全局通知并唤醒一轮思维；状态标记 reported 只通报一次。AI 详情查询走 devops `get_crash_report` 工具 / 面板 `/crash-info` |
| 重启交接闭环 | `entities/devops/service.py`（交接落盘 + wait_idle 重启）+ RestartHandoffWatcher provider | AI 调 restart_app 等可传 `message` 给重启后的自己留言；交接（owner scope / 回复路由 / 留言）落盘 `<data_dir>/restart_handoff.json`（拒绝不写，已排定重复调用仅在留言非空时更新）；`wait_idle=True` 等思维空闲（120s 上限）让当前回复轮自然收尾。bootstrap 末尾 provider 消费交接（读即删只消费一次；超 1h TTL 仅清理不投递），延迟 5s 推送「重启成功 + 留言」并唤醒一轮思维。Web/API 重启路径不写交接、不等待 |
| 关停总预算 | `core/lifecycle.py::shutdown_all(deadline=)` + `shutdown_budget_seconds`（默认 45s，system/shutdown 组） | 逆序清理按剩余预算裁剪单项上限，耗尽即跳过余下组件记名（二次信号强杀仍是最终兜底）；实例锁释放移到后置钩子，消除双实例窗口 |
| Web 绑定 fail-fast | `web/server.py::WebServerService._serve` | uvicorn 绑定失败不苟活——检测 `server.started` 请求优雅关停，半活实例（Agent 在跑而 WebUI 不可达）不再存活 |
| 实时引擎入 Lifecycle | bootstrap 注册 `realtime_engine`（cleanup=shutdown_all_sessions） | 进程退出不依赖 WS 断连的隐式清理；每会话有界（5s）收尾 |
| 子进程看护 | `entities/filesystem/child_guard.py` + shell_background 登记 | 后台 shell（独立进程组）的 owner-death 守卫：pgid 登记 logs/shell_children.json，启动清扫上次实例孤儿（SIGTERM→5s→SIGKILL 整组），关停终止全部在册子进程；裸 create_task 收编（async_helper.spawn 受管） |

## 组件凭据中心（core/provider_keys.py + config/provider_keys.json）

外部平台 API Key 的统一存取面：组件经 `_sdk.register_provider_key` 登记条目（name/domain/title/
描述/附加字段），get/set 读写，list 脱敏列举（含文件手填但未登记的条目）。三面配置等价：Web
（模型配置页「组件凭据」页签）、配置文件（直接编辑 JSON）、AI（`list_provider_keys`/
`set_provider_key` 工具）——同一存储无第二真源；FunASR 服务地址与本地模型资产摘要并入同一外部
依赖管理面。

**配置归位原则**：大模型类生成继续走 llm_clients 模型配置；实时/特殊协议的语音等模块凭据只走
凭据中心（dashscope 脱离 llm_clients 扫描，环境变量兜底），非凭据参数（模型名/音色/优先级）留
各自配置组。

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
  回报给实体的态势追踪器；实体侧以 `track_fs_op`/`track_ssh_op` 绑定，单点接入
- **文件操作态势**（`entities/filesystem/ops_context.py`，provider `fs_ops`）：按会话追踪当前 Shell
  目录 + 活跃目录 + 最近操作流水；停止操作超 `os_context_ttl_seconds`（默认 600s）注入自动消失。
  **目录说明文档规则**：当前目录优先、最近活跃目录其次，按 `os_context_doc_names`（默认
  AGENTS.md,README.md，仅纯文件名防路径引导）注入，mtime 缓存自动失效，配额受限；可关
- **SSH 操作态势**（`entities/ssh/ops_state.py`，provider `ssh_ops`）：按 (会话, 连接) 追踪——只展示
  本会话近期操作过的主机（与全局花名册 provider `ssh_status` 分工）；远程目录说明文档操作成功后
  经旁路通道后台抓取（30s 最小复用窗口），渲染零远程 I/O
- **SSH 远程目录持久跟踪**（`manager.py`）：ssh_exec 命令经 POSIX pwd 捕获尾块包装（brace 组内 cd
  失败则无标记、退出码语义不变），捕获目录对后续命令生效（与本地 shell cwd 语义对齐）；标记缺失
  且有目标目录 = cd 失败 → 清除持久目录自愈；非 POSIX 远端可关

## 文件扫描与读取防线（entities/filesystem/scan.py）

- **扫描剪枝**：os.walk 按目录名剪枝（默认 .git/node_modules/__pycache__/.venv/dist/build/各类
  缓存，`search_exclude_dirs` 可配置）——不进结果也不再向下遍历；glob 语义（裸 `*.png` 任意深度、
  `**/` 零目录语义）；内容模式跳过二进制扩展名与 >2MB 大文件；结果 path 保持绝对路径
- **二进制嗅探**（`looks_binary`，前 8KB NUL 采样）：read_file 扩展名表之外的内容级防线——文本
  读取走 `errors="replace"` 永不抛解码异常；命中返回 `{"type":"binary"}` JSON 引导媒体工具

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

`config/hooks.json` 声明 tool_pre/tool_post/reply_end 脚本；exit 2 阻塞（stderr 为理由）、串行、
deny 胜过一切；空配置零开销。管理面：设置页「钩子」标签可视化编辑（保存即热生效），样例
`config/hooks.example.json`；校验与运行时同源（`parse_hooks_data`）。

- hook stdout 输出一行前缀 `REPLACE:<json-string>` 即返回替换内容（`HookOutcome.replace`，串行取
  第一个）；非字符串/非 JSON 静默忽略。当前无消费方，解析层保留供未来 tool_post 场景复用
- 与 LLM 钩子面（`agent/hooks_llm/`，异步并行扩员）分层：本面是同步阻塞守门，见
  [思维分册](mind-architecture.md)
