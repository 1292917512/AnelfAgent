# 插件跨平台支持

NoneBot 支持同时加载多个适配器（OneBot V11、Telegram、QQ 官方、Kaiheila 等），插件如何编写才能兼容多个平台是一个重要课题。

## 方式一：使用基类方法（推荐）

NoneBot 的事件基类 `Event` 提供了一系列平台无关的方法，直接使用基类即可天然跨平台：

```python
from nonebot import on_command
from nonebot.adapters import Event, Bot, Message
from nonebot.params import CommandArg

hello = on_command("hello")


@hello.handle()
async def handle_hello(bot: Bot, event: Event, args: Message = CommandArg()):
    user_id = event.get_user_id()         # 获取用户 ID
    session_id = event.get_session_id()   # 获取会话 ID
    message = event.get_message()         # 获取消息对象
    plaintext = event.get_plaintext()     # 获取纯文本
    event_type = event.get_type()         # 获取事件类型
    event_name = event.get_event_name()   # 获取事件名称
    event_desc = event.get_event_description()  # 获取事件描述
    is_to_me = event.is_tome()            # 是否 @bot

    await bot.send(event, f"你好，{user_id}！")
```

### Event 基类通用方法

| 方法 | 返回类型 | 说明 |
|------|----------|------|
| `get_type()` | `str` | 事件类型（`message`、`notice`、`request`、`meta_event`） |
| `get_event_name()` | `str` | 事件名称 |
| `get_event_description()` | `str` | 事件描述文本 |
| `get_message()` | `Message` | 事件关联的消息对象 |
| `get_plaintext()` | `str` | 事件关联的纯文本 |
| `get_user_id()` | `str` | 触发事件的用户 ID |
| `get_session_id()` | `str` | 会话 ID（用户 + 群组等信息的组合） |
| `is_tome()` | `bool` | 事件是否与 Bot 相关（如 @Bot） |

### Bot 基类通用方法

| 方法 | 说明 |
|------|------|
| `bot.send(event, message)` | 向事件来源发送消息 |
| `bot.call_api(api, **kwargs)` | 调用底层 API |

## 方式二：使用 Overload 处理不同平台

当需要为不同平台编写差异化逻辑时，使用类型注解的 overload 机制：

```python
from nonebot import on_command
from nonebot.adapters import Event, Bot

greet = on_command("greet")


# OneBot V11 专用处理
from nonebot.adapters.onebot.v11 import Bot as OBBot
from nonebot.adapters.onebot.v11 import MessageEvent as OBMessageEvent


@greet.handle()
async def handle_ob(bot: OBBot, event: OBMessageEvent):
    nickname = event.sender.nickname or "未知"
    await bot.send(event, f"你好呀 {nickname}！(来自 QQ)")


# Telegram 专用处理
from nonebot.adapters.telegram import Bot as TGBot
from nonebot.adapters.telegram.event import MessageEvent as TGMessageEvent


@greet.handle()
async def handle_tg(bot: TGBot, event: TGMessageEvent):
    first_name = event.from_.first_name if event.from_ else "Unknown"
    await bot.send(event, f"Hello {first_name}! (from Telegram)")


# 兜底：其他平台
@greet.handle()
async def handle_fallback(bot: Bot, event: Event):
    user_id = event.get_user_id()
    await bot.send(event, f"你好 {user_id}！")
```

NoneBot 会根据事件和 Bot 的实际类型自动匹配最精确的处理函数。匹配优先级：精确类型 > 父类类型 > 基类。

## 方式三：Union 类型处理相似事件

当多个平台的事件逻辑相同时，使用 `Union` 减少重复代码：

```python
from typing import Union

from nonebot import on_command
from nonebot.adapters.onebot.v11 import GroupMessageEvent as OBGroupEvent
from nonebot.adapters.telegram.event import GroupMessageEvent as TGGroupEvent

group_cmd = on_command("info")


@group_cmd.handle()
async def handle_group(event: Union[OBGroupEvent, TGGroupEvent]):
    user_id = event.get_user_id()
    await group_cmd.finish(f"群消息来自用户: {user_id}")
```

## 方式四：依赖注入判断适配器

通过依赖注入获取 Bot 类型，在同一个处理函数中分支处理：

```python
from nonebot import on_command
from nonebot.adapters import Bot, Event

adapter_info = on_command("adapter")


@adapter_info.handle()
async def handle_adapter(bot: Bot, event: Event):
    adapter_name = bot.adapter.get_name()
    await bot.send(event, f"当前适配器: {adapter_name}")
```

也可以用 `isinstance` 进行精确判断：

```python
from nonebot import on_command
from nonebot.adapters import Bot, Event

smart_reply = on_command("reply")


@smart_reply.handle()
async def handle_smart(bot: Bot, event: Event):
    try:
        from nonebot.adapters.onebot.v11 import Bot as OBBot

        if isinstance(bot, OBBot):
            await bot.send_group_forward_msg(
                group_id=event.group_id,
                messages=[...],
            )
            return
    except ImportError:
        pass

    # 通用回退
    await bot.send(event, "这是一条普通回复")
```

## 跨平台插件推荐

以下插件专门为跨平台场景设计，可以大幅简化多适配器兼容工作：

### nonebot-plugin-alconna

基于 [Alconna](https://github.com/ArcletProject/Alconna) 的命令解析框架，提供跨平台的命令定义和消息构建。

```bash
pip install nonebot-plugin-alconna
```

```python
from nonebot import require

require("nonebot_plugin_alconna")

from nonebot_plugin_alconna import Alconna, Args, UniMessage, on_alconna

alc = Alconna("weather", Args["city", str])
weather = on_alconna(alc)


@weather.handle()
async def handle_weather(city: str):
    # UniMessage 自动适配各平台消息格式
    msg = UniMessage()
    msg += f"🌤 {city} 天气：晴\n"
    msg += "温度：25°C"
    await msg.send()
```

**核心能力**：

| 功能 | 说明 |
|------|------|
| `Alconna` | 跨平台命令定义 |
| `UniMessage` | 通用消息构建 |
| `on_alconna` | 创建响应器 |
| `Image` / `At` / `Text` | 通用消息段 |

### nonebot-plugin-send-anything-anywhere (SAA)

提供统一的消息发送接口，屏蔽各适配器差异。

```bash
pip install nonebot-plugin-saa
```

```python
from nonebot import on_command, require

require("nonebot_plugin_saa")

from nonebot_plugin_saa import Image, MessageFactory, Text

hello = on_command("hello")


@hello.handle()
async def handle():
    msg = MessageFactory([Text("你好！"), Image("https://example.com/img.png")])
    await msg.send()
```

### nonebot-plugin-uninfo

统一的用户/群组/频道信息获取接口。

```bash
pip install nonebot-plugin-uninfo
```

```python
from nonebot import on_command, require

require("nonebot_plugin_uninfo")

from nonebot_plugin_uninfo import Uninfo

info = on_command("myinfo")


@info.handle()
async def handle(user_info: Uninfo):
    await info.finish(
        f"用户: {user_info.user.name}\n"
        f"ID: {user_info.user.id}\n"
        f"场景: {user_info.scene.type}"
    )
```

### nonebot-plugin-session

跨平台的会话信息提取。

```bash
pip install nonebot-plugin-session
```

```python
from nonebot import on_command, require

require("nonebot_plugin_session")

from nonebot_plugin_session import EventSession, SessionLevel

session_cmd = on_command("session")


@session_cmd.handle()
async def handle(session: EventSession):
    level = session.level
    msg = f"会话级别: {level.name}\n"
    msg += f"平台: {session.platform}\n"
    msg += f"用户 ID: {session.id1}\n"

    if level == SessionLevel.GROUP:
        msg += f"群组 ID: {session.id2}\n"

    await session_cmd.finish(msg)
```

### nonebot-plugin-userinfo

跨平台的用户信息获取（头像、昵称等）。

```bash
pip install nonebot-plugin-userinfo
```

```python
from nonebot import on_command, require

require("nonebot_plugin_userinfo")

from nonebot_plugin_userinfo import EventUserInfo, UserInfo

user_cmd = on_command("user")


@user_cmd.handle()
async def handle(user_info: UserInfo = EventUserInfo()):
    msg = f"昵称: {user_info.user_name}\n"
    if user_info.user_avatar:
        msg += f"头像: {user_info.user_avatar.url}\n"
    await user_cmd.finish(msg)
```

### nonebot-plugin-all4one

基于 OneBot 12 协议，将其他适配器的 Bot 转换为 OneBot 12 协议 Bot，从而实现一次编写多平台运行。

```bash
pip install nonebot-plugin-all4one
```

```dotenv
# .env
ONEBOT12_ACCESS_TOKEN=your-token
ALL4ONE_OBIMPL={"onebot.v11": {"heartbeat_interval": 5000}}
```

## 跨平台开发最佳实践

1. **优先使用基类方法**：`Event.get_user_id()`、`Bot.send()` 等基类方法覆盖了大多数场景。
2. **仅在必要时使用 overload**：只有需要调用平台特有 API 时才使用特定适配器类型。
3. **使用 UniMessage / SAA**：发送包含图片、@ 等富文本消息时，使用跨平台消息库。
4. **try-except ImportError**：对可选适配器的引用使用 try-except 保护，避免未安装时报错。
5. **添加兜底处理函数**：始终提供一个使用基类参数的处理函数作为兜底。
