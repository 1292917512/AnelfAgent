# 依赖注入

NoneBot 拥有一套完善的依赖注入（Dependency Injection, DI）系统，可以让事件处理函数自动获取所需的上下文信息。

## 基本概念

依赖注入通过函数参数的**类型注解**或**默认值**声明需要的依赖。NoneBot 在调用处理函数时会自动解析并注入这些依赖。

```python
from nonebot import on_command
from nonebot.adapters import Bot, Event

cmd = on_command("hello")

@cmd.handle()
async def handle(bot: Bot, event: Event):
    # bot 和 event 由 NoneBot 自动注入
    await bot.send(event, "Hello!")
```

## 内置依赖注入

### Bot — 机器人实例

```python
from nonebot.adapters import Bot

@cmd.handle()
async def handle(bot: Bot):
    await bot.send(event, "Hello!")
```

也可以使用具体适配器的 Bot 类型进行过滤：

```python
from nonebot.adapters.onebot.v11 import Bot as V11Bot

@cmd.handle()
async def handle(bot: V11Bot):
    # 仅当 Bot 是 OneBot V11 类型时才触发
    await bot.send_group_msg(group_id=123456, message="Hello!")
```

### Event — 事件对象

```python
from nonebot.adapters import Event

@cmd.handle()
async def handle(event: Event):
    user_id = event.get_user_id()
    session_id = event.get_session_id()
    msg = event.get_message()
```

使用具体事件类型：

```python
from nonebot.adapters.onebot.v11 import GroupMessageEvent

@cmd.handle()
async def handle(event: GroupMessageEvent):
    # 仅当事件是群消息时触发
    group_id = event.group_id
    user_id = event.user_id
```

### State — 状态字典

```python
from nonebot.typing import T_State

@cmd.handle()
async def handle(state: T_State):
    state["count"] = state.get("count", 0) + 1
```

### Matcher — 当前响应器

```python
from nonebot.matcher import Matcher

@cmd.handle()
async def handle(matcher: Matcher):
    await matcher.send("处理中...")
    await matcher.finish("完成！")
```

### Exception — 异常注入

用于 `run_postprocessor` 中获取处理过程中的异常：

```python
from nonebot.message import run_postprocessor

@run_postprocessor
async def post(exception: Exception | None):
    if exception:
        print(f"处理异常: {exception}")
```

## Depends — 子依赖

`Depends()` 允许你定义可复用的依赖函数。

### 基本用法

```python
from nonebot.params import Depends

async def get_user_info(event: Event) -> dict:
    return {
        "user_id": event.get_user_id(),
        "session_id": event.get_session_id(),
    }

@cmd.handle()
async def handle(user_info: dict = Depends(get_user_info)):
    print(user_info["user_id"])
```

### 嵌套子依赖

```python
from nonebot.params import Depends
from nonebot.adapters import Event

async def get_user_id(event: Event) -> str:
    return event.get_user_id()

async def get_user_name(user_id: str = Depends(get_user_id)) -> str:
    # 子依赖也可以有自己的依赖
    return f"User_{user_id}"

@cmd.handle()
async def handle(name: str = Depends(get_user_name)):
    await cmd.finish(f"你好, {name}")
```

### use_cache — 依赖缓存

默认情况下 `Depends` 会缓存同一事件处理中的依赖结果（`use_cache=True`）。

```python
async def expensive_operation() -> str:
    # 耗时操作，同一事件处理中只执行一次
    return await fetch_data()

@cmd.handle()
async def handle(
    data1: str = Depends(expensive_operation),
    data2: str = Depends(expensive_operation),
):
    # data1 和 data2 是同一个结果（缓存命中）
    assert data1 is data2
```

禁用缓存：

```python
@cmd.handle()
async def handle(
    data: str = Depends(expensive_operation, use_cache=False),
):
    # 每次都会重新执行
    ...
```

## 类型验证

NoneBot 会对注入的依赖进行类型验证，不匹配时跳过该处理函数。

```python
from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent

@cmd.handle()
async def group_handler(event: GroupMessageEvent):
    # 只处理群消息
    await cmd.finish("这是群消息")

@cmd.handle()
async def private_handler(event: PrivateMessageEvent):
    # 只处理私聊消息
    await cmd.finish("这是私聊消息")
```

## 类依赖

类也可以作为依赖使用，NoneBot 会将其 `__init__` 的参数作为子依赖解析。

```python
from nonebot.params import Depends
from nonebot.adapters import Bot, Event

class UserContext:
    def __init__(self, bot: Bot, event: Event):
        self.bot = bot
        self.user_id = event.get_user_id()

    async def get_nickname(self) -> str:
        info = await self.bot.call_api("get_stranger_info", user_id=int(self.user_id))
        return info.get("nickname", "未知")

@cmd.handle()
async def handle(ctx: UserContext = Depends(UserContext)):
    nickname = await ctx.get_nickname()
    await cmd.finish(f"你好, {nickname}")
```

## 生成器依赖

生成器函数可以用作依赖，`yield` 之前的代码在处理前执行，`yield` 之后的代码在处理后执行（类似 contextmanager）。

```python
from nonebot.params import Depends

async def database_session():
    session = await create_session()
    try:
        yield session
    finally:
        await session.close()

@cmd.handle()
async def handle(session = Depends(database_session)):
    await session.execute("SELECT ...")
    # session 会在处理结束后自动关闭
```

## 可调用对象依赖

任何实现了 `__call__` 的对象都可以作为依赖：

```python
from nonebot.params import Depends
from nonebot.adapters import Event

class PermissionChecker:
    def __init__(self, required_level: int):
        self.required_level = required_level

    async def __call__(self, event: Event) -> bool:
        user_id = event.get_user_id()
        user_level = await get_user_level(user_id)
        return user_level >= self.required_level

@cmd.handle()
async def handle(is_admin: bool = Depends(PermissionChecker(5))):
    if not is_admin:
        await cmd.finish("权限不足")
    await cmd.finish("管理员操作成功")
```

## 内置辅助注入器

NoneBot 提供了大量内置注入器，位于 `nonebot.params` 模块。

### 事件信息注入器

| 注入器 | 类型 | 说明 |
|--------|------|------|
| `EventType()` | `str` | 事件类型字符串 |
| `EventMessage()` | `Message` | 事件消息对象 |
| `EventPlainText()` | `str` | 事件消息纯文本 |
| `EventToMe()` | `bool` | 是否 @机器人 |

```python
from nonebot.params import EventType, EventMessage, EventPlainText, EventToMe

@cmd.handle()
async def handle(
    event_type: str = EventType(),
    message: Message = EventMessage(),
    plain_text: str = EventPlainText(),
    to_me: bool = EventToMe(),
):
    print(f"类型: {event_type}")
    print(f"消息: {message}")
    print(f"纯文本: {plain_text}")
    print(f"@我: {to_me}")
```

### 命令注入器

| 注入器 | 类型 | 说明 |
|--------|------|------|
| `Command()` | `tuple[str, ...]` | 命令元组（如 `("help",)`） |
| `RawCommand()` | `str` | 原始命令文本（如 `"/help"`） |
| `CommandArg()` | `Message` | 命令参数消息 |
| `CommandStart()` | `str` | 命令前缀（如 `"/"`） |
| `CommandWhitespace()` | `str` | 命令与参数间的空白字符 |

```python
from nonebot.params import Command, RawCommand, CommandArg, CommandStart, CommandWhitespace
from nonebot.adapters import Message

@cmd.handle()
async def handle(
    command: tuple[str, ...] = Command(),
    raw_command: str = RawCommand(),
    args: Message = CommandArg(),
    start: str = CommandStart(),
    whitespace: str = CommandWhitespace(),
):
    print(f"命令: {command}")        # ("help",)
    print(f"原始: {raw_command}")     # "/help"
    print(f"参数: {args}")           # "topic"
    print(f"前缀: {start}")          # "/"
    print(f"空白: {whitespace!r}")   # " "
```

### Shell 命令注入器

| 注入器 | 类型 | 说明 |
|--------|------|------|
| `ShellCommandArgv()` | `list[str]` | Shell 命令参数列表 |
| `ShellCommandArgs()` | `Namespace` | 解析后的参数对象 |

```python
from nonebot.params import ShellCommandArgv, ShellCommandArgs

@shell_cmd.handle()
async def handle(
    argv: list[str] = ShellCommandArgv(),
    args: Namespace = ShellCommandArgs(),
):
    print(f"参数列表: {argv}")   # ["name", "-t", "3"]
    print(f"解析结果: {args}")   # Namespace(name="name", times=3)
```

### 正则注入器

| 注入器 | 类型 | 说明 |
|--------|------|------|
| `RegexMatched()` | `re.Match` | 正则匹配对象 |
| `RegexStr()` | `str` | 完整匹配字符串 |
| `RegexGroup()` | `tuple[Any, ...]` | 匹配的分组元组 |
| `RegexDict()` | `dict[str, Any]` | 命名分组字典 |

```python
from nonebot import on_regex
from nonebot.params import RegexMatched, RegexStr, RegexGroup, RegexDict
import re

matcher = on_regex(r"^(?P<cmd>\w+)\s+(?P<arg>.+)$")

@matcher.handle()
async def handle(
    matched: re.Match = RegexMatched(),
    full_match: str = RegexStr(),
    groups: tuple = RegexGroup(),
    group_dict: dict = RegexDict(),
):
    print(f"匹配对象: {matched}")
    print(f"完整匹配: {full_match}")
    print(f"分组: {groups}")           # ("hello", "world")
    print(f"命名分组: {group_dict}")    # {"cmd": "hello", "arg": "world"}
```

### 文本匹配注入器

| 注入器 | 类型 | 说明 |
|--------|------|------|
| `Startswith()` | `str` | startswith 匹配到的前缀 |
| `Endswith()` | `str` | endswith 匹配到的后缀 |
| `Fullmatch()` | `str` | fullmatch 匹配到的完整文本 |
| `Keyword()` | `str` | keyword 匹配到的关键词 |

```python
from nonebot import on_startswith, on_endswith, on_fullmatch, on_keyword
from nonebot.params import Startswith, Endswith, Fullmatch, Keyword

sw = on_startswith("你好")

@sw.handle()
async def handle_sw(prefix: str = Startswith()):
    print(f"匹配前缀: {prefix}")  # "你好"

ew = on_endswith("吗")

@ew.handle()
async def handle_ew(suffix: str = Endswith()):
    print(f"匹配后缀: {suffix}")  # "吗"

fm = on_fullmatch("签到")

@fm.handle()
async def handle_fm(text: str = Fullmatch()):
    print(f"完整匹配: {text}")  # "签到"

kw = on_keyword({"天气"})

@kw.handle()
async def handle_kw(word: str = Keyword()):
    print(f"关键词: {word}")  # "天气"
```

### 会话交互注入器

| 注入器 | 类型 | 说明 |
|--------|------|------|
| `Received()` | `Event` | `receive()` 接收到的事件 |
| `LastReceived()` | `Event` | 最近一次 `receive()` 的事件 |
| `Arg()` | `Message` | `got()` 获取到的参数消息 |
| `ArgStr()` | `str` | `got()` 获取到的参数纯文本 |
| `ArgPlainText()` | `str` | `got()` 获取到的参数纯文本（等同 ArgStr） |

```python
from nonebot.params import Arg, ArgStr, ArgPlainText

cmd = on_command("order")

@cmd.got("food", prompt="你想吃什么？")
async def handle_food(
    food: Message = Arg(),
    food_str: str = ArgStr(),
    food_text: str = ArgPlainText(),
):
    print(f"消息对象: {food}")
    print(f"字符串: {food_str}")
    print(f"纯文本: {food_text}")
    await cmd.finish(f"好的，{food_text}！")
```

指定键名：

```python
@cmd.got("name", prompt="你叫什么？")
@cmd.got("age", prompt="你几岁？")
async def handle(
    name: str = ArgStr("name"),
    age: str = ArgStr("age"),
):
    await cmd.finish(f"{name}，{age}岁")
```

`Received` 示例：

```python
from nonebot.params import Received, LastReceived
from nonebot.adapters import Event

@cmd.receive("confirmation")
async def handle(
    event: Event = Received("confirmation"),
    last: Event = LastReceived(),
):
    # event 是 receive("confirmation") 收到的事件
    # last 是最后一次 receive 收到的事件
    ...
```

## Annotated 语法

NoneBot 支持使用 `typing.Annotated`（Python 3.9+）来声明依赖，使代码更简洁。

### 基本 Annotated 用法

```python
from typing import Annotated
from nonebot.adapters import Event, Message
from nonebot.params import EventPlainText, CommandArg, Depends

@cmd.handle()
async def handle(
    plain_text: Annotated[str, EventPlainText()],
    args: Annotated[Message, CommandArg()],
):
    ...
```

### Annotated 与 Depends

```python
from typing import Annotated
from nonebot.params import Depends

async def get_user_id(event: Event) -> str:
    return event.get_user_id()

UserId = Annotated[str, Depends(get_user_id)]

@cmd.handle()
async def handle(user_id: UserId):
    await cmd.finish(f"用户: {user_id}")
```

### 预定义 Annotated 类型

NoneBot 预定义了一些常用的 Annotated 类型：

```python
from nonebot.params import (
    EventType,
    EventMessage,
    EventPlainText,
    EventToMe,
    Command,
    RawCommand,
    CommandArg,
    CommandStart,
    CommandWhitespace,
    ShellCommandArgv,
    ShellCommandArgs,
    RegexMatched,
    RegexStr,
    RegexGroup,
    RegexDict,
    Startswith,
    Endswith,
    Fullmatch,
    Keyword,
    Received,
    LastReceived,
    Arg,
    ArgStr,
    ArgPlainText,
)
```

## 综合示例

### 完整的命令处理

```python
from typing import Annotated
from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.params import CommandArg, Depends, ArgStr
from nonebot.typing import T_State

async def parse_target(args: Message = CommandArg()) -> str | None:
    text = args.extract_plain_text().strip()
    return text if text else None

cmd = on_command("info")

@cmd.handle()
async def first_receive(
    target: Annotated[str | None, Depends(parse_target)],
    state: T_State,
):
    if target:
        state["target"] = target
    # 如果没有参数，会继续到 got

@cmd.got("target", prompt="请输入查询目标：")
async def handle(
    bot: Bot,
    event: Event,
    target: str = ArgStr("target"),
):
    result = await fetch_info(target)
    await cmd.finish(f"查询结果: {result}")
```

### 可复用的权限依赖

```python
from typing import Annotated
from nonebot.params import Depends
from nonebot.adapters import Event

async def require_admin(event: Event) -> bool:
    user_id = event.get_user_id()
    if user_id not in ADMIN_LIST:
        raise PermissionError("权限不足")
    return True

IsAdmin = Annotated[bool, Depends(require_admin)]

@cmd.handle()
async def admin_only(is_admin: IsAdmin):
    await cmd.finish("管理员操作成功")
```
