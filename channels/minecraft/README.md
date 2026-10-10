# Minecraft 陪玩频道

Minecraft 频道把 AnelfAgent 的消息、频道配置和开源 Mineflayer MCP 执行器连接起来，让 bot 可以进入 **Minecraft Java Edition 26.1** 世界并执行受限的观察、移动、采集、制作、补给和建造任务。

实现边界、阶段计划和验收设计见 [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md)。本 README 只保留使用、维护和验证所需的信息。

底层执行器来自 [awesome-mineflayer-mcp 1.3.2](https://github.com/G0Osey99/awesome-mineflayer-mcp)，并使用 Mineflayer、pathfinder、collectblock、tool、auto-eat 等组件。频道负责消息接入、任务策略和动作边界，不自行实现 Minecraft 协议。

## 目录与运行入口

```text
channels/minecraft/
├── adapter.py / config.py / protocol.py   频道接入与配置
├── mcp/                                   Node MCP 源码与测试
│   ├── runtime/                           执行器扩展
│   ├── skills/                            陪玩技能
│   └── tests/                             Node 回归与真服验收入口
├── scripts/                               安装、补丁和检查脚本
└── tests/                                 Python 频道测试
```

MCP 插件安装后位于 `workspace/plugins/minecraft-companion/`，入口由插件清单中的 `${PLUGIN_ROOT}` 解析到该负载目录。`channels/minecraft/mcp/` 是源码，安装负载是运行时副本；源码变更后必须重新同步负载并重启 MCP 服务。

## 前置条件

- Minecraft Java Edition 26.1，使用官方客户端或官方服务端。
- Node.js 22+（推荐使用仓库 `.nvmrc` 指定的版本）。Windows 安装脚本可以下载项目内便携 Node，不修改系统 Node；已有可用 Node 时无需下载。
- 项目根目录可用的 `uv` 和 Python 3.11–3.12。
- AnelfAgent 已配置至少一个可用模型。

真实生存验收另需 Java 25 运行时和官方 26.1 `server.jar`。这只是隔离验收环境的要求，不影响日常在自己的游戏世界中使用频道。

## 安装与连接

在游戏中启动服务器，或在单人世界选择“对局域网开放”，记下实际端口。局域网端口通常不是 25565，而且每次开放可能变化。

Windows 首次安装：

```powershell
uv run python -m channels.minecraft.scripts.setup_minecraft --download-node --host 127.0.0.1 --port 25565 --username AnelfBot
```

已有 Node 22+ 时省略 `--download-node`；也可以用 `--node` 指定绝对路径。其他操作系统直接使用已安装的 Node 22+。

正版验证服务器使用 Microsoft 登录：

```powershell
uv run python -m channels.minecraft.scripts.setup_minecraft --download-node --host 127.0.0.1 --port 25565 --auth microsoft --username your-account@example.com
```

首次连接按终端显示的设备码登录。bot 应使用与真人玩家不同的账号，否则服务器可能踢出另一端会话。授权缓存保存在 `workspace/minecraft/state/<世界标识>/`。

可用 `--player PlayerName` 限定可交互玩家，重复参数可允许多人；`--clear-players` 清空限制。地址、端口、账号或玩家名单变化后重新运行安装命令，再重启 AnelfAgent。未提供的参数会沿用已有配置。

启动或重启 AnelfAgent 后，在 WebUI 或 Minecraft 频道中发送：

```text
/minecraft-companion 连接默认世界，找到玩家 PlayerName 并跟着我。
/minecraft-companion 我的世界开在 61234，进来找我并跟着我。
```

也可以直接说“过来玩”，让 AI 先发现局域网世界；存在多个世界时会要求确认。常用游戏内指令：

```text
跟着我
你背包里有什么？
帮我收集 8 个橡木原木
!stop
!pause
!resume
待着
!sethome
回家
!give oak_log 10
```

`!stop`、`!停`、`!stay` 和“待着”会立即请求停止并清除移动；停止结果只在执行器确认收尾后才算完成。任务返回 ID 只表示受理，制作、采集、补给和挖矿的最终结果由进度事件播报。

## 能力与安全边界

常用能力包括：连接和断开、状态/背包/方块观察、跟随、表面采集、有限制作、指定箱子补给、模板化建造和有界矿洞施工。工具按需发现，未启用战斗、创造模式、截图和原始协议工具。

动作共享一个执行锁；冲突任务返回 `BUSY`，不会静默替换正在执行的任务。停止、暂停、受伤、死亡、断线和重连不会自动重放已中断任务。移动和挖掘必须有可验证的路径与服务器方块确认：

- 采集必须先指定方块类型和允许区域，数量按实际入包统计；不会为了接近目标盲挖、挖脚下支撑或搭柱。
- 制作在消耗前检查配方、库存、空间和工作台；缺料、目标不可放置或无法确认收尾时拒绝或报告受阻。
- 补给必须指定可见的箱子/木桶坐标，先模拟整批库存和空间；不是跨玩家事务，不会假装回滚已到账物品。
- 矿洞施工有最低高度、宽度、净高、预算和返程检查；返程受阻时保留位置并报告失败，不承诺强行脱困。
- 真实游戏中的建筑、地形和其他玩家属于外部状态，不能用离线测试替代确认。

## 配置与维护

频道配置在配置中心的 `adapter/minecraft` 管理，文件为 `channels/minecraft/channel_config.json`。MCP 连接参数在 MCP 管理页保存；每个世界使用独立 `server_id`，公共聊天按世界隔离。

检查已安装执行器和工具契约：

```powershell
uv run python -m channels.minecraft.scripts.check_minecraft
```

该命令建立一次 MCP 会话后关闭，不会主动连接游戏世界；检查时应保持其他 bot 连接断开，避免多个 stdio 会话同时占用同一账号。

更新频道源码或 Node 执行器后同步安装负载：

```powershell
uv run python -m channels.minecraft.scripts.setup_minecraft --upgrade
```

必要时加 `--download-node` 或 `--node <绝对路径>`。同步后重启 MCP 服务；频道策略或回复契约变化还需要重载频道或重启 AnelfAgent。不要直接编辑安装负载来代替源码修改。

## 离线验证

离线检查使用真实锁定依赖和内存协议服务端，不连接玩家世界，也不修改游戏存档。依赖已经存在且锁文件未变时不要运行 `npm ci`；下面命令中的第一条只在依赖缺失或确实需要更新 Node 依赖时执行。源码未变时可复用已有安装结果。

```powershell
npm ci --prefix channels/minecraft/mcp --omit=optional --no-audit --no-fund
uv run python -m channels.minecraft.scripts.minecraft_crafting channels/minecraft/mcp
uv run python -m channels.minecraft.scripts.minecraft_placement channels/minecraft/mcp
uv run python -m channels.minecraft.scripts.minecraft_digging channels/minecraft/mcp
uv run python -m channels.minecraft.scripts.minecraft_mining channels/minecraft/mcp
uv run python -m channels.minecraft.scripts.minecraft_actions channels/minecraft/mcp
npm test --prefix channels/minecraft/mcp
```

频道 Python 测试：

```powershell
uv run pytest channels/minecraft/tests
```

修改 `channels/minecraft/reply_policy.py` 后，同步并检查随插件分发的契约：

```powershell
uv run python -m channels.minecraft.scripts.minecraft_contract
uv run python -m channels.minecraft.scripts.check_minecraft
```

## 可选真实验收

真实验收使用独立目录和官方服务端，不要指向自己的生存世界。先完成频道安装或升级，确保 `workspace/plugins/minecraft-companion/` 已存在；再准备一个空目录，把官方 26.1 `server.jar` 放入其中，然后运行：

```powershell
node channels/minecraft/mcp/tests/live-survival.cjs `
  --java '<Java 25 的 java.exe 路径>' `
  --server-dir workspace/minecraft/acceptance-26.1 `
  --payload workspace/plugins/minecraft-companion
```

脚本会创建隔离平坦世界、使用本机随机端口，并在退出时关闭服务端。普通 `npm test` 不会启动真服。制作、补给、采集和矿洞场景可按脚本的 `--suite` 选项单独验收；完整场景和模型链路基准见 [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md)。

## 故障排查顺序

1. 查看 AnelfAgent 日志和 MCP 服务日志，确认服务是否启动、工具是否注册。
2. 运行 `check_minecraft`，确认插件负载和工具契约。
3. 核对世界地址、端口、账号、`server_id` 和玩家白名单；局域网开放端口变化时重新提供端口。
4. 确认 bot 未被另一个 MCP 会话或真人账号占用；停止任务后等待执行器完成收尾。
5. 只有在源码、锁文件或安装负载确实变化时，才重新同步依赖和插件。