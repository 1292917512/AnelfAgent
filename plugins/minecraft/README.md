# Minecraft 陪玩插件

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
```

玩家在游戏里说话即唤醒 AI（默认无需 @，配置 `require_mention` 可改回 @ 模式），
AI 判断是否在对它说话：闲聊简短接话，指令立即行动，多步任务派给子代理执行。
私聊机器人始终唤醒。
分工：频道会话里的 AI 专注沟通与决策，动作序列经 `delegate_task` 派给 `mc-worker`
子代理执行，结果由沟通者转述。`mc-worker` 档案（快模型池 + 反射式执行守则 +
`mcp:minecraft` 工具限定）由安装脚本自动注册到 `llm_clients.json` 的 `sub_agents`
节；已存在时不覆盖用户自定义。环境里没有池中的快模型时，脚本回退绑定默认聊天
模型（仍可用但变慢），或跳过并在输出中提示手动创建。
`!stop` 或 `!停` 是确定性停止指令：尝试取消当前任务、寻路、控制键和采集，再报告结果，
不等待 LLM。单项失败或超时仍会执行其他停止操作，并明确提示未能确认全部停止。
跟随由执行器常驻任务持续运行，聊天、派活都不打断跟随。
当前不启用战斗、创造模式、复杂建造、截图与原始协议工具；需要时可在 MCP 配置中
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
停用 Minecraft 聊天可关闭频道；停用或卸载插件会通过现有 MCP 生命周期关闭其执行器。

## 验证边界

单元测试覆盖工具名冲突与回滚、聊天路由、私聊隔离、消息去重、执行器重启和停止指令。
真实 MCP 检查覆盖已安装开源执行器的协议握手与工具契约。
实际寻路、跟随与采集还需要在运行的 Java 26.1 世界中进行体验验证。
