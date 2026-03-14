<!-- source: https://nonebot.dev/docs/tutorial/fundamentals -->

# 机器人的构成

了解机器人的基本构成有助于你更好地使用 NoneBot，本章节将介绍 NoneBot 中的基本组成部分。

## 基本组成部分

使用 NoneBot 框架搭建的机器人具有以下几个基本组成部分：

1. **插件 `Plugin`**：机器人的功能实现，通常负责处理事件并进行一系列的操作
2. **适配器 `Adapter`**：驱动器的上层，负责将平台消息与 NoneBot 事件/操作系统的消息格式相互转换
3. **驱动器 `Driver`**：客户端/服务端的功能实现，负责接收和发送消息（通常为 HTTP 通信）
4. **NoneBot 机器人框架主体**：负责连接各个组成部分，提供基本的机器人功能

除 NoneBot 机器人框架主体外，其他部分均可按需选择、互相搭配，但由于平台的兼容性问题，部分插件可能仅在某些特定平台上可用（这由插件编写者决定）。

## Driver（驱动器）

驱动器是 NoneBot 的底层通信组件，负责处理网络通信。NoneBot 提供了多种驱动器：

| 驱动器 | 包名 | 说明 |
|--------|------|------|
| FastAPI | `nonebot2[fastapi]` | 基于 FastAPI 的服务端驱动器 |
| Quart | `nonebot2[quart]` | 基于 Quart 的服务端驱动器 |
| httpx | `nonebot2[httpx]` | HTTP 客户端驱动器 |
| websockets | `nonebot2[websockets]` | WebSocket 客户端驱动器 |
| aiohttp | `nonebot2[aiohttp]` | HTTP + WebSocket 客户端驱动器 |

驱动器可以组合使用，通过 `+` 号连接：

```bash
# .env 配置文件
DRIVER=~fastapi+~httpx+~websockets
```

### 服务端驱动器 vs 客户端驱动器

- **服务端驱动器**（如 FastAPI）：提供 HTTP/WebSocket 服务端功能，适配器通过反向连接接收消息
- **客户端驱动器**（如 httpx、websockets）：提供 HTTP/WebSocket 客户端功能，适配器主动连接平台服务器

### 配置驱动器

在 `.env` 文件中配置：

```bash
DRIVER=~fastapi
```

在入口文件中无需额外代码，NoneBot 会自动加载配置的驱动器。

## Adapter（适配器）

适配器是 NoneBot 与聊天平台之间的桥梁。每个适配器对应一个特定的聊天平台协议。

### 注册适配器

在入口文件 `bot.py` 中注册适配器：

```python
import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

nonebot.init()

driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

nonebot.run()
```

### 常用适配器

| 适配器 | 包名 | 对应平台 |
|--------|------|---------|
| OneBot V11 | `nonebot-adapter-onebot` | QQ（通过协议实现） |
| Console | `nonebot-adapter-console` | 终端交互 |
| Telegram | `nonebot-adapter-telegram` | Telegram |
| Discord | `nonebot-adapter-discord` | Discord |
| QQ 官方 | `nonebot-adapter-qq` | QQ 官方 Bot API |

## Plugin（插件）

插件是机器人功能的实现载体。NoneBot 的一切功能都通过插件来实现。

### 加载插件

```python
import nonebot

nonebot.init()

# 加载内置插件
nonebot.load_builtin_plugins("echo")

# 加载第三方插件
nonebot.load_plugin("nonebot_plugin_xxx")

# 加载本地插件目录
nonebot.load_plugins("awesome_bot/plugins")

nonebot.run()
```

### 使用 pyproject.toml 配置加载

```toml
[tool.nonebot]
plugin_dirs = ["awesome_bot/plugins"]

[tool.nonebot.plugins]
"nonebot-plugin-xxx" = ["nonebot_plugin_xxx"]
```

```python
nonebot.load_from_toml("pyproject.toml")
```

## Matcher（事件响应器）

Matcher 是 NoneBot 中对接收到的事件进行响应的基本单元。通过定义匹配规则来筛选事件，并交由事件处理函数处理。

### 基本示例

```python
from nonebot import on_command

weather = on_command("天气", priority=10, block=True)

@weather.handle()
async def handle_weather():
    await weather.finish("今天天气晴朗")
```

## 配置文件

NoneBot 使用 dotenv 格式的配置文件，支持多环境配置。

### 配置文件优先级

```
.env < .env.{环境名}
```

### `.env` 文件示例

```bash
ENVIRONMENT=prod
```

### `.env.prod` 文件示例

```bash
HOST=0.0.0.0
PORT=8080
COMMAND_START=["/", "!"]
COMMAND_SEP=["."]
LOG_LEVEL=INFO
NICKNAME=["bot", "机器人"]
SUPERUSERS=["123456789"]
```

### 常用配置项

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `DRIVER` | `str` | `~fastapi` | 驱动器 |
| `HOST` | `str` | `127.0.0.1` | 监听 IP |
| `PORT` | `int` | `8080` | 监听端口 |
| `LOG_LEVEL` | `str/int` | `INFO` | 日志等级 |
| `COMMAND_START` | `set[str]` | `["/"]` | 命令起始字符 |
| `COMMAND_SEP` | `set[str]` | `["."]` | 命令分割字符 |
| `NICKNAME` | `set[str]` | `[]` | 机器人昵称 |
| `SUPERUSERS` | `set[str]` | `[]` | 超级用户 |

## 完整入口文件示例

```python
import nonebot
from nonebot.adapters.console import Adapter as ConsoleAdapter

# 初始化 NoneBot
nonebot.init()

# 获取驱动器
driver = nonebot.get_driver()

# 注册适配器
driver.register_adapter(ConsoleAdapter)

# 加载插件
nonebot.load_builtin_plugins("echo")
nonebot.load_plugins("awesome_bot/plugins")

# 运行
if __name__ == "__main__":
    nonebot.run()
```

## 运行机器人

```bash
# 使用 nb-cli
nb run

# 或直接运行入口文件
python bot.py
```
