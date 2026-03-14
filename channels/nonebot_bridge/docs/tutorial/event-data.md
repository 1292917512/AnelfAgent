<!-- source: https://nonebot.dev/docs/tutorial/event-data -->

# 获取事件信息

在 NoneBot 事件处理流程中，获取事件信息并做出对应的操作是非常常见的场景。本章节将介绍如何通过**依赖注入**获取事件信息。

## 认识依赖注入

在事件处理流程中，事件响应器具有自己独立的上下文，例如：当前响应的事件、收到事件的机器人或者其他处理流程中新增的信息等。这些数据可以根据需求，通过依赖注入的方式，在执行事件处理流程中注入到事件处理函数中。

相对于传统的信息获取方法，通过依赖注入获取信息的最大特色在于**按需获取**：

- 如果事件处理函数不需要任何额外信息，可以不进行依赖注入
- 如果需要额外数据，可以通过依赖注入灵活标注出需要的依赖

## 使用依赖注入

使用依赖注入获取上下文信息非常简单，只需要在函数的参数中声明所需的依赖即可。

### 基本示例

```python
from nonebot import on_command
from nonebot.rule import to_me
from nonebot.adapters import Message
from nonebot.params import CommandArg

weather = on_command(
    "天气",
    rule=to_me(),
    aliases={"weather", "查天气"},
    priority=10,
    block=True,
)

@weather.handle()
async def handle_function(args: Message = CommandArg()):
    if location := args.extract_plain_text():
        await weather.finish(f"今天{location}的天气是...")
    else:
        await weather.finish("请输入地名")
```

> `:=` 是 Python 3.8 引入的 [海象表达式（Assignment Expressions）](https://docs.python.org/zh-cn/3/reference/expressions.html#assignment-expressions)，可以在表达式中直接赋值。

## 常用依赖注入类型

### Bot - 获取机器人实例

获取当前事件对应的 Bot 实例。

```python
from nonebot.adapters import Bot

@matcher.handle()
async def handle(bot: Bot):
    # bot 是当前收到事件的机器人实例
    await bot.send(event, "Hello!")
```

使用特定适配器的 Bot 类型：

```python
from nonebot.adapters.onebot.v11 import Bot

@matcher.handle()
async def handle(bot: Bot):
    # 仅当事件来自 OneBot V11 适配器时才会触发
    await bot.send_group_msg(group_id=123456, message="Hello!")
```

### Event - 获取事件

获取当前接收到的事件对象。

```python
from nonebot.adapters import Event

@matcher.handle()
async def handle(event: Event):
    user_id = event.get_user_id()
    msg = event.get_message()
    plain_text = event.get_plaintext()
    is_to_me = event.is_tome()
```

使用特定适配器的 Event 类型：

```python
from nonebot.adapters.onebot.v11 import GroupMessageEvent

@matcher.handle()
async def handle(event: GroupMessageEvent):
    # 仅当事件是群消息时才会触发
    group_id = event.group_id
    user_id = event.user_id
```

### Message - 获取消息

通过类型注解获取事件消息对象。

```python
from nonebot.adapters import Message

@matcher.handle()
async def handle(msg: Message):
    # msg 是当前事件的消息对象
    text = msg.extract_plain_text()
```

### CommandArg - 获取命令参数

获取 `on_command` 命令后跟随的内容（去除头部空白符）。

```python
from nonebot.adapters import Message
from nonebot.params import CommandArg

@weather.handle()
async def handle(args: Message = CommandArg()):
    location = args.extract_plain_text()
    # /天气 上海 -> location = "上海"
    # /天气上海  -> location = "上海"
```

> 命令与参数之间可以不需要空格，`CommandArg()` 获取的信息为命令后跟随的内容并去除了头部空白符。

### Command - 获取命令名

获取当前触发的命令名称。

```python
from nonebot.params import Command

@matcher.handle()
async def handle(cmd: tuple[str, ...] = Command()):
    # 如果用户发送 /天气.北京
    # cmd = ("天气", "北京")
    print(cmd)
```

### CommandStart - 获取命令前缀

获取当前触发命令的前缀。

```python
from nonebot.params import CommandStart

@matcher.handle()
async def handle(start: str = CommandStart()):
    # 如果用户发送 /天气
    # start = "/"
    print(start)
```

### CommandWhitespace - 获取命令空白符

获取命令与参数之间的空白符。

```python
from nonebot.params import CommandWhitespace

@matcher.handle()
async def handle(ws: str = CommandWhitespace()):
    print(ws)
```

### EventMessage - 获取事件消息

获取事件消息（等同于 `event.get_message()`）。

```python
from nonebot.params import EventMessage
from nonebot.adapters import Message

@matcher.handle()
async def handle(msg: Message = EventMessage()):
    text = msg.extract_plain_text()
```

### EventPlainText - 获取纯文本

获取事件消息的纯文本内容（等同于 `event.get_plaintext()`）。

```python
from nonebot.params import EventPlainText

@matcher.handle()
async def handle(text: str = EventPlainText()):
    await matcher.finish(f"你发送了：{text}")
```

### EventToMe - 是否与我有关

判断事件是否与机器人有关（等同于 `event.is_tome()`）。

```python
from nonebot.params import EventToMe

@matcher.handle()
async def handle(to_me: bool = EventToMe()):
    if to_me:
        await matcher.finish("你在叫我吗？")
```

### ArgPlainText - 获取 got 参数纯文本

获取 `got` 装饰器接收的参数的纯文本。

```python
from nonebot.params import ArgPlainText

@matcher.got("name", prompt="请输入你的名字")
async def handle(name: str = ArgPlainText()):
    await matcher.finish(f"你好，{name}！")
```

### Arg - 获取 got 参数消息

获取 `got` 装饰器接收的参数的消息对象。

```python
from nonebot.params import Arg
from nonebot.adapters import Message

@matcher.got("content", prompt="请输入内容")
async def handle(content: Message = Arg()):
    text = content.extract_plain_text()
    await matcher.finish(f"收到：{text}")
```

### ArgStr - 获取 got 参数字符串

获取 `got` 装饰器接收的参数的字符串表示。

```python
from nonebot.params import ArgStr

@matcher.got("name", prompt="请输入名字")
async def handle(name: str = ArgStr()):
    await matcher.finish(f"你好，{name}")
```

### RegexMatched - 正则匹配结果

获取 `on_regex` 的匹配结果字符串。

```python
from nonebot.params import RegexMatched

@matcher.handle()
async def handle(matched: str = RegexMatched()):
    await matcher.finish(f"匹配到：{matched}")
```

### RegexGroup - 正则分组

获取 `on_regex` 的匹配分组。

```python
from nonebot.params import RegexGroup

@matcher.handle()
async def handle(groups: tuple = RegexGroup()):
    if groups:
        await matcher.finish(f"第一个分组：{groups[0]}")
```

### Matcher - 获取响应器实例

获取当前事件响应器实例。

```python
from nonebot.matcher import Matcher

@matcher.handle()
async def handle(m: Matcher):
    m.set_arg("key", Message("value"))
    m.stop_propagation()
```

### State - 获取会话状态

获取当前会话状态字典。

```python
from nonebot.typing import T_State

@matcher.handle()
async def handle(state: T_State):
    state["key"] = "value"
```

### Received - 获取 receive 事件

获取 `receive` 装饰器接收的事件。

```python
from nonebot.params import Received
from nonebot.adapters import Event

@matcher.receive("id")
async def handle(event: Event = Received("id")):
    user_id = event.get_user_id()
```

### LastReceived - 获取最近的 receive 事件

```python
from nonebot.params import LastReceived
from nonebot.adapters import Event

@matcher.receive("id")
async def handle(event: Event = LastReceived()):
    ...
```

## 依赖注入速查表

| 注入类型 | 导入路径 | 获取内容 |
|---------|---------|---------|
| `Bot` | `nonebot.adapters` | 机器人实例 |
| `Event` | `nonebot.adapters` | 事件对象 |
| `Message` | `nonebot.adapters` | 消息对象（类型注解） |
| `CommandArg()` | `nonebot.params` | 命令参数消息 |
| `Command()` | `nonebot.params` | 命令名称元组 |
| `CommandStart()` | `nonebot.params` | 命令前缀字符串 |
| `CommandWhitespace()` | `nonebot.params` | 命令后空白符 |
| `EventMessage()` | `nonebot.params` | 事件消息对象 |
| `EventPlainText()` | `nonebot.params` | 事件纯文本 |
| `EventToMe()` | `nonebot.params` | 是否与我有关 |
| `Arg()` | `nonebot.params` | got 参数消息对象 |
| `ArgStr()` | `nonebot.params` | got 参数字符串 |
| `ArgPlainText()` | `nonebot.params` | got 参数纯文本 |
| `RegexMatched()` | `nonebot.params` | 正则匹配字符串 |
| `RegexGroup()` | `nonebot.params` | 正则分组元组 |
| `Matcher` | `nonebot.matcher` | 响应器实例 |
| `T_State` | `nonebot.typing` | 会话状态字典 |
| `Received()` | `nonebot.params` | receive 事件 |
| `LastReceived()` | `nonebot.params` | 最近 receive 事件 |

## 自定义依赖注入

使用 `Depends` 创建自定义依赖：

```python
from nonebot.params import Depends
from nonebot.adapters import Event

async def get_user_info(event: Event) -> dict:
    user_id = event.get_user_id()
    return {"user_id": user_id, "is_admin": user_id in ADMIN_LIST}

@matcher.handle()
async def handle(user_info: dict = Depends(get_user_info)):
    if user_info["is_admin"]:
        await matcher.finish("管理员你好！")
    else:
        await matcher.finish("普通用户你好！")
```

更多依赖注入内容可参考 [依赖注入文档](https://nonebot.dev/docs/advanced/dependency)。
