# 使用适配器

适配器（Adapter）是 NoneBot 与聊天平台之间的桥梁，负责将平台特定的协议转换为 NoneBot 统一的事件和消息格式。

## 注册适配器

适配器需要在 NoneBot 初始化后、启动前注册到驱动器中。

### 基本注册方式

```python
import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

nonebot.init()

driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)
```

### 完整启动流程

```python
import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from nonebot.adapters.telegram import Adapter as TelegramAdapter

# 初始化
nonebot.init()

# 获取驱动器
driver = nonebot.get_driver()

# 注册多个适配器
driver.register_adapter(OneBotV11Adapter)
driver.register_adapter(TelegramAdapter)

# 加载插件
nonebot.load_from_toml("pyproject.toml")

# 启动
nonebot.run()
```

## 可用适配器列表

以下是 NoneBot 社区维护的主要适配器：

| 适配器 | PyPI 包名 | 协议 | 驱动器要求 |
|--------|----------|------|-----------|
| OneBot V11 | `nonebot-adapter-onebot` | OneBot V11 | 服务端 / WebSocket 客户端 |
| OneBot V12 | `nonebot-adapter-onebot` | OneBot V12 | 服务端 / WebSocket 客户端 |
| QQ 官方 | `nonebot-adapter-qq` | QQ 官方 API | HTTP + WebSocket 客户端 |
| Telegram | `nonebot-adapter-telegram` | Telegram Bot API | HTTP 客户端 (+服务端 WebHook) |
| Discord | `nonebot-adapter-discord` | Discord Gateway | HTTP + WebSocket 客户端 |
| 飞书 | `nonebot-adapter-feishu` | 飞书开放平台 | 服务端 + HTTP 客户端 |
| 钉钉 | `nonebot-adapter-ding` | 钉钉机器人 | 服务端 |
| 开黑啦 | `nonebot-adapter-kaiheila` | KOOK API | HTTP + WebSocket 客户端 |
| Console | `nonebot-adapter-console` | 终端控制台 | 无 |
| GitHub | `nonebot-adapter-github` | GitHub WebHook | 服务端 + HTTP 客户端 |

## 安装适配器

```bash
# 使用 nb-cli 安装（推荐）
nb adapter install nonebot-adapter-onebot

# 使用 pip 安装
pip install nonebot-adapter-onebot

# 安装 QQ 官方适配器
pip install nonebot-adapter-qq
```

## 适配器配置

每个适配器可能需要特定的配置项，配置方式与 NoneBot 全局配置一致，在 `.env` 文件中设置。

### OneBot V11 配置

```dotenv
# 正向 WebSocket 连接
ONEBOT_WS_URLS=["ws://127.0.0.1:6700/"]

# 访问令牌（可选）
ONEBOT_ACCESS_TOKEN=your_token_here
```

### QQ 官方适配器配置

```dotenv
QQ_BOTS='
[
  {
    "id": "your_app_id",
    "token": "your_token",
    "secret": "your_secret",
    "intent": {
      "guild_messages": true,
      "at_messages": true
    }
  }
]
'
```

### Telegram 适配器配置

```dotenv
TELEGRAM_BOTS='[{"token": "your_bot_token"}]'

# WebHook 模式（可选）
TELEGRAM_WEBHOOK_URL=https://your-domain.com/telegram
```

## 多适配器共存

NoneBot 支持同时注册多个适配器，不同平台的事件和消息会被各自的适配器处理。

```python
import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from nonebot.adapters.qq import Adapter as QQAdapter
from nonebot.adapters.telegram import Adapter as TelegramAdapter

nonebot.init()
driver = nonebot.get_driver()

driver.register_adapter(OneBotV11Adapter)
driver.register_adapter(QQAdapter)
driver.register_adapter(TelegramAdapter)
```

### 在插件中区分适配器

```python
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot as OneBotBot
from nonebot.adapters.qq import Bot as QQBot

cmd = on_command("hello")

@cmd.handle()
async def handle(bot):
    if isinstance(bot, OneBotBot):
        await cmd.finish("来自 OneBot 的问候！")
    elif isinstance(bot, QQBot):
        await cmd.finish("来自 QQ 官方的问候！")
    else:
        await cmd.finish("Hello!")
```

## 适配器的 Bot 对象

每个适配器提供自己的 Bot 类，通过 Bot 对象可以调用平台特定的 API。

```python
from nonebot.adapters.onebot.v11 import Bot

async def some_handler(bot: Bot):
    # 调用 OneBot V11 API
    await bot.send_private_msg(user_id=12345, message="hello")

    # 获取群列表
    groups = await bot.get_group_list()

    # 获取好友列表
    friends = await bot.get_friend_list()
```

## 获取已连接的 Bot

```python
import nonebot

# 获取所有 Bot
bots = nonebot.get_bots()  # dict[str, Bot]

# 获取指定 Bot
bot = nonebot.get_bot("bot_id")

# 获取任意一个 Bot
bot = nonebot.get_bot()
```

## supported_adapters 声明

插件可以在 `PluginMetadata` 中声明支持的适配器：

```python
from nonebot.plugin import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="我的插件",
    description="仅支持 OneBot V11",
    usage="/hello",
    supported_adapters={"~onebot.v11"},
)
```

适配器名使用 `~` 前缀表示 `nonebot.adapters.` 的简写：

| 简写 | 完整名称 |
|------|---------|
| `~onebot.v11` | `nonebot.adapters.onebot.v11` |
| `~onebot.v12` | `nonebot.adapters.onebot.v12` |
| `~qq` | `nonebot.adapters.qq` |
| `~telegram` | `nonebot.adapters.telegram` |
| `~discord` | `nonebot.adapters.discord` |
