# Minecraft 陪玩插件

开发评审、问题证据、分阶段任务和验收标准统一记录在
[Minecraft 陪玩功能评审与实施计划](DEVELOPMENT_PLAN.md)。后续改造先阅读该文档，完成一项后同步更新进度与验证结果。

Java **26.1** 是本原型的目标版本。Minecraft 官方最新稳定版为 26.3，
但锁定的 Mineflayer 4.39.0 当前声明支持到 26.1；本插件不自行实现新协议。

游戏执行能力直接复用 MIT 开源项目
[awesome-mineflayer-mcp 1.3.2](https://github.com/G0Osey99/awesome-mineflayer-mcp)，
其底层使用 [Mineflayer](https://github.com/PrismarineJS/mineflayer)、
[mineflayer-pathfinder](https://github.com/PrismarineJS/mineflayer-pathfinder)、
collectblock、tool、auto-eat 和 armor-manager。
AnelfAgent 只增加安装配置、陪玩技能和游戏聊天频道，寻路、物理与采集由现成组件执行。

## 安装

先用官方 Minecraft Launcher 安装 Java 26.1。启动自己的服务器，或打开单人世界的
“对局域网开放”，记录游戏显示的端口。局域网端口通常不是 25565，每次开放也可能变化。

Windows 可以自动下载经过官方 SHA-256 校验的便携 Node 22，所有运行时、npm 缓存和
依赖都留在项目 `workspace/` 下，不修改全局 Node。首次安装需要联网：

```powershell
uv run python scripts/setup_minecraft.py --download-node --host 127.0.0.1 --port 25565 --username AnelfBot
```

把端口换成你的实际端口。已有 Node 22+ 时可省略 `--download-node`；
也可以用 `--node` 指定 Node 的绝对路径。其他操作系统使用已安装的 Node 22+。
命令会通过现有插件管理器安装插件、按锁文件安装 npm 依赖、启用对应 MCP 服务与频道。
执行器不会自动进入游戏，连接动作由用户或 AI 显式发起。

正版验证服务器需要额外的 Microsoft Minecraft 账号：

```powershell
uv run python scripts/setup_minecraft.py --download-node --host 127.0.0.1 --port 25565 --auth microsoft --username your-account@example.com
```

第一次连接按执行器给出的 Microsoft 设备码流程登录，无需在项目中填写密码。
这里的账号应与真人玩家的账号不同，否则服务器会将同账号的另一个会话断开。
安装脚本将授权缓存放在 `workspace/minecraft/state/<世界标识>/`，升级插件时保留。

可用 `--player YourPlayerName` 限定交互玩家；重复此参数可允许多个玩家。
服务地址、端口或账号改变后，重跑安装命令更新配置，再重启 AnelfAgent。
重跑和使用 `--upgrade` 时，未提供的连接参数、世界标识和玩家名单沿用已有配置；
`--clear-players` 可清空玩家限制。应用重启也会保留已保存的 MCP 连接配置。

## 开始陪玩

启动或重启 AnelfAgent。在 WebUI 发送：

```text
/minecraft-companion 连接默认世界，找到玩家 YourPlayerName 并跟着我。
```

刚打开“对局域网开放”的世界端口每次都不同，直接把端口号告诉 AI 即可，不必改配置：

```text
/minecraft-companion 我的世界开在 61234，进来找我并跟着我。
```

AI 会用 `connect_bot` 按你给的地址端口进入世界；`connect_default` 只用于配置里的默认世界。
什么都不用说也行——“过来玩”即可，AI 会先查局域网广播自己找到你的世界（`minecraft_discover_worlds`），
再进入并找你；开了多个世界时它会跟你确认。

进入世界后可以使用：

```text
@AnelfBot 跟着我
@AnelfBot 你背包里有什么？
@AnelfBot 帮我收集 8 个橡木原木
@AnelfBot !stop
@AnelfBot !pause
@AnelfBot !resume
@AnelfBot 待着
@AnelfBot !sethome
@AnelfBot 回家
@AnelfBot !give oak_log 10
```

玩家在游戏里说话即唤醒 AI（默认无需 @，配置 `require_mention` 可改回 @ 模式），
AI 判断是否在对它说话：闲聊简短接话，指令立即行动，多步任务派给子代理执行。
私聊机器人始终唤醒。
分工：频道会话里的 AI 专注沟通与决策，动作序列经 `delegate_task` 派给 `mc-worker`
子代理执行，结果由沟通者转述。成功派工后等待完成通知，不用定时提醒重复派工或恢复旧任务。
`mc-worker` 档案（快模型池 + 反射式执行守则 +
`mcp:minecraft` 工具限定）由安装脚本自动注册到 `llm_clients.json` 的 `sub_agents`
节；已存在时不覆盖用户自定义。环境里没有池中的快模型时，脚本回退绑定默认聊天
模型（仍可用但变慢），或跳过并在输出中提示手动创建。
`!stop`、`!停` 和 `!stay` / “待着”会取消当前工作、清除移动并待命，不等待 LLM。
普通快捷指令在后台排队，停止不等待该队列。派工先标记取消，执行器停止不等待历史收尾；
执行器返回 `stopped=false` 时只表示已请求停止，不能宣称已经停稳，也不会自动续跑。
Node 生存观察直接监听掉血、死亡和呼吸事件，并短周期检查火焰、头部空间、敌对生物和移动进展，
不等待模型或 Python 查询。危险触发有限上浮、避险或跳跃；不能确认安全路线时报告受阻，不盲挖、搭柱或跳坑。
自动重生沿用 Mineflayer 的连接配置，只在真实死亡/出生事件上播报；未知血量不当成死亡。
自动进食、穿装备、天黑插火把和脚边拾取共享动作控制权。停止/暂停后不自动移动，自救后不恢复旧工作。
跟随由执行器持续运行，聊天和只读查询可并行；新动作接管前会先结束跟随并等待收尾。
常用建造（庇护所/围墙/立柱/桥面/矿洞楼梯）有模板分解，见技能文件的
references/building.md；大型异形工程超出模板范围。
当前不启用战斗、创造模式、截图与原始协议工具；需要时可在 MCP 配置中
调整 `MCP_DISABLE_GROUPS`。已有实时语音会话可用于聊天，游戏内消息通过 Minecraft 频道收发。

## 检查与配置

```powershell
uv run python scripts/check_minecraft.py
```

此命令建立真实 MCP 会话，验证所需工具、连接状态和增量事件格式，随后关闭会话。
检查时机器人显示 `disconnected` 是正常的；这个检查不会主动连接 Minecraft 世界。
请在独立检查期间保持游戏机器人未连接，因为每个 stdio 会话各自拥有一个机器人。

频道参数在配置中心 `adapter/minecraft` 管理，值保存到
`channels/minecraft/channel_config.json`；服务连接参数在 MCP 页面管理。
每个世界使用独立 `server_id`，公共聊天按世界分桶，私聊以“世界/玩家”隔离。
频道使用增量事件游标，过滤自身回复，重连后跳过旧消息。

插件安装目录为 `workspace/plugins/minecraft-companion/`。
插件 MCP 清单支持 `${PLUGIN_ROOT}`，安装时解析成负载绝对路径。
手动从插件管理页安装不会自动执行 npm，因此推荐使用安装脚本。

更新本地插件源码后：

```powershell
uv run python scripts/setup_minecraft.py --download-node --upgrade
```

如果 MCP 服务名与现有服务重名，插件管理器会加前缀，安装脚本会将频道绑定到实际服务名。
关闭 Minecraft 频道会取消陪玩任务并让机器人正常下线；停用或卸载插件会通过现有 MCP 生命周期关闭其执行器。

## 验证边界

安装脚本会为锁定版 Mineflayer 应用合成同步补丁：现代协议的合成点击通过整窗同步确认，
配方未凑齐时不再空等结果格变化；2×2 背包合成结束也确认产物到账。
执行器在整批材料不足时提前拒绝，残留光标/合成材料未收尾时阻止继续合成。
合成失败会尝试归还光标和合成材料；背包放不下时保留物品并报告失败，不自动丢弃。
正常下线先等待正在进行的合成，再归还临时物品、确认服务器背包状态，然后断开。
收尾有 4 秒预算，无法完成时明确记日志；强制结束进程或网络断开无法保证执行收尾。
`get_inventory.crafting` 提供光标、合成格和可用空格，供失败后核验及归还材料。
放置工具提前拒绝空气支撑点、无效朝向，以及工作台等整方块与机器人自身重叠的目标，
返回可操作的坐标纠正建议；火把和实体放置仍沿用原有服务器校验。
挖掘计时结束只发送完成请求，必须收到服务器方块移除更新才报告成功；5 秒未确认则失败。
直接挖掘会装备可采收工具并用射线检查可见面，避免空手挖石头或盲挖地下方块。
挖掉和拾取分开核验，采集数量按背包实际增量汇报。
已有进程需重新启动 MCP 服务后才会加载依赖修改。

## 动作控制与运行指标

通过 MCP 发起的移动、挖掘、采集、制作、装备和矿洞任务共享一个动作控制器。
同优先级的进行中任务相互冲突时返回 `BUSY`，不会静默替换；紧急逃离可请求抢占，
跟随可以被普通工作接管。旧动作及异步清理完全结束后，新动作才获得控制权。
未在 5 秒内完成清理时拒绝本次接管，不会在调用失败后偷偷启动新任务。
逃离是有限目标，到达指定安全距离即结束；路径不可达会报告失败并释放控制权。

`cancel_task`、`stop_pathfinding`、`cancel_collect`、`clear_control_states` 共用停止入口。
`action_status` 返回动作 ID、阶段、当前控制权、停止原因与耗时；`stopping` 表示仍在收尾。
合成会在安全点击边界响应取消，并等待材料恢复完成；工具超时不会提前释放仍在操作背包的任务。
矿洞继续用 `mining_status` 查询实际所得、返程和可恢复的检查点，任务 ID 与动作 ID 当前仍分开。

`get_runtime_metrics` 提供各工具和寻路最近 128 个完成样本的 P50/P95，以及进程期间的
Node 事件循环延迟；这些指标不能当作模型延迟、游戏 FPS 或服务器 TPS。
频道日志 `MC_COMMAND` / `MC_RPC` 以事件 `seq` 关联快捷指令及其工具等待，不记录工具参数。

`!pause` / “暂停一下”保留矿洞安全检查点或持续移动目标，`!resume` 重新检查条件后继续。
暂停请求可能先返回“正在完成安全步骤”；合成等已经消耗材料的原子动作只安全取消，不重放。
`!stop` 丢弃当前暂停续跑目标；矿洞检查点仍可经明确的 `resume_mining` / `return_from_mine` 恢复或返程。
死亡、断线和换维度不会自动恢复工作；闲聊和只读查询也不会解除待命。

MCP 工作、自救与空闲行为共用控制器。危险抢占等待合成光标、装备和寻路实际收尾，
同时撤销被中断请求，旧 worker 的迟到指令会被拒绝。每次持续危险最多两次自救尝试；
路线计算候选最多三个、总预算 30 毫秒，移动最多 8 秒、上浮最多 5 秒，不保证所有地形都能脱困。
Python 只从现有 `get_connection_status` 快照同步 `configure_survival` 设置，接收生存事件并异步播报；
`get_survival_status` 可查询生命状态、停止限制及最近结果。配置仍在 `adapter/minecraft`，
`reflexes_enabled` 为总开关，`reflex_interval_seconds` 为本地观察周期（新配置默认 0.25 秒，已有设置保留）。
升级时需同步安装负载并重载 MCP 与频道；本机部署和真实游戏验收记录
见 [开发计划](DEVELOPMENT_PLAN.md#首批真实游戏验收记录)。

Model Experience：新增两个只读诊断工具，停止结果区分请求受理与收尾完成。
Token effect：增加少量 schema，状态和指标按需查询，不要求模型轮询维持任务。
Cache effect：实时进度留在结果和事件，不写入稳定提示前缀。

## 可往返矿洞

`mine_resources` 在 Node 执行器中运行有界后台任务，不逐块调用模型：从当前位置按指定方向
修筑宽 1、高 3 的阶梯，再有限延伸水平通道。每步检查地板、头部空间、液体和重力方块，
使用真实 pathfinder 验证无需挖掘或搭方块的回程路径；挖掘仍须服务器确认，物品按背包增量统计。
完成和可处理的失败会实际沿原路返回；返程受阻会保留位置并报告失败，不承诺强行脱困。
最低脚部 Y 由 `minY` 指定，默认且不可低于服务器维度底界上方 10 格；更高的用户下限优先。
新任务在规划时缩短下降级数，到底后只执行有界的水平段；返回 `minY`、实际 `depth` 和
`requestedDepth`。每次挖掘前再次校验高度，旧检查点也无法越界续挖，但不限制其原路返程。
未获得世界高度时拒绝施工，遇到基岩仍按不可挖障碍停止。

工具为 `mine_resources`、`mining_status`、`resume_mining`、`return_from_mine`。
兼容入口 `dig_staircase` / `dig_tunnel` 改为同一后台任务，宽度限 1，净高固定 3；返回任务 ID 不代表完成。
路径寻路统一禁止挖路、搭柱和多格跳落；矿道地板和机器人脚下支撑受统一挖掘守卫保护。
采集插件仍可挖可见的独立目标，但不会为了接近目标破坏路径。

检查点位于 `AWESOME_MINEFLAYER_MCP_HOME/mines`，原子保存，按地址、端口、账号和维度隔离。
断线后不自动恢复施工；局域网换端口后不匹配旧检查点。当前保留最近任务，已登记地板保护随新任务继承。
施工任务占用现有动作锁，聊天和只读观察照常；频道消费 `mine_progress`，只播报终态事实。
Node 补丁更新后重启 MCP；频道的进度播报和反射协调修改需要热重载频道或重启应用。

Model Experience：增加 4 个稳定工具，输出阶段、背包增量、最低 Y 和返程事实；旧挖掘入口提示后台语义。
Token effect：schema 增加数百 token（含一个可选高度参数），连续施工不再逐块调用模型。
Cache effect：工具 schema 随安装更新一次，进度留在工具结果和事件历史，不写入稳定人设前缀。

离线动作回归使用真实依赖和内存中的协议服务端，不连接游戏、不修改世界。
在仓库根目录、Node 22+ 环境运行：

```powershell
npm ci --prefix plugins/minecraft --omit=optional --no-audit --no-fund
uv run python -m scripts.minecraft_crafting plugins/minecraft
uv run python -m scripts.minecraft_placement plugins/minecraft
uv run python -m scripts.minecraft_digging plugins/minecraft
uv run python -m scripts.minecraft_mining plugins/minecraft
uv run python -m scripts.minecraft_actions plugins/minecraft
npm test --prefix plugins/minecraft
```

单元测试覆盖工具名冲突与回滚、聊天路由、私聊隔离、消息去重、执行器重启和停止指令。
真实 MCP 检查覆盖已安装开源执行器的协议握手与工具契约。
实际寻路、跟随与采集还需要在运行的 Java 26.1 世界中进行体验验证。

陪玩执行契约以 `channels/minecraft/reply_policy.py` 为唯一编辑源。修改后在仓库根目录运行
`uv run python -m scripts.minecraft_contract`，同步随插件分发的技能契约块；其他技能章节保留。
`channels/minecraft/tests/test_reply_contract.py` 校验同步结果，`scripts/check_minecraft.py`
通过真实执行器的工具注册表校验契约引用的游戏工具，工具裁剪时必须同步运行这两项检查。
