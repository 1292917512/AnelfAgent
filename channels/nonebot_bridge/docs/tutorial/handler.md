<!-- source: https://nonebot.dev/docs/tutorial/handler -->
<!-- extended: https://nonebot.dev/docs/appendices/session-control -->

# 事件处理

在我们收到事件，并被某个事件响应器正确响应后，便正式开启了对于这个事件的处理流程。

## 认识事件处理流程

处理一个事件需要一套流程。在事件响应器对一个事件进行响应之后，会依次执行一系列的事件处理依赖（通常是函数）。在这个流程中，我们需要了解两个概念：

1. **事件处理函数**：函数形式的"事件处理依赖"
2. **事件响应器操作**：用于控制流程和交互的方法

## 事件处理函数

事件处理流程可以由一个或多个"事件处理函数"组成，这些事件处理函数将会**按照顺序依次**对事件进行处理，直到全部执行完成或被中断。

### 使用 handle 装饰器

```python
from nonebot import on_command
from nonebot.rule import to_me

weather = on_command(
    "天气",
    rule=to_me(),
    aliases={"weather", "查天气"},
    priority=10,
    block=True,
)

@weather.handle()
async def handle_function():
    pass  # do something here
```

`handle_function` 函数会被添加到 `weather` 的事件处理流程中。在 `weather` 响应器被触发之后，将会依次调用其事件处理函数。

### 多个处理函数

一个响应器可以有多个处理函数，它们会按定义顺序依次执行：

```python
@weather.handle()
async def step_one():
    # 第一步处理
    pass

@weather.handle()
async def step_two():
    # 第二步处理
    pass

@weather.handle()
async def step_three():
    # 第三步处理
    pass
```

### handle 嵌套

`handle` 装饰器支持嵌套操作，同一个函数可以被添加多次：

```python
@matcher.handle()
@matcher.handle()
async def handle_func():
    # 这个函数会被执行两次
    ...
```

## 事件响应器操作

事件响应器操作通常作为事件响应器 `Matcher` 的类方法存在，调用形式为 `Matcher.func()`。

### send - 发送消息

向用户回复一条消息，但**不结束**事件处理流程。

```python
@weather.handle()
async def handle_function():
    await weather.send("正在查询天气...")
    # 后续代码仍会执行
    await weather.send("查询完成！")
```

等同于 `bot.send(event, message, **kwargs)`，但不需要自行传入 `event`。

### finish - 发送消息并结束

向用户回复一条消息（可选），并**立即结束**整个处理流程。

```python
@weather.handle()
async def handle_function():
    await weather.finish("天气是...")
    # 下面的代码不会被执行
    print("这行永远不会执行")
```

> **警告**：`finish` 是通过抛出 `FinishedException` 异常来结束事件的。因此异常可能会被未加限制的 `try-except` 捕获。请务必在异常捕获中排除 `MatcherException` 类型：

```python
from nonebot.exception import MatcherException

try:
    await weather.finish("天气是...")
except MatcherException:
    raise
except Exception as e:
    pass  # do something here
```

### pause - 暂停等待

向用户回复一条消息（可选），立即结束当前事件处理函数，**等待接收一个新的事件**后进入下一个事件处理函数。

```python
@matcher.handle()
async def step1():
    if need_confirm:
        await matcher.pause("请在两分钟内确认执行")

@matcher.handle()
async def step2():
    # 用户回复后执行此函数
    ...
```

### reject - 拒绝并重试

向用户回复一条消息（可选），立即结束当前事件处理函数，等待接收一个新的事件后**再次执行当前**事件处理函数。

通常用于用户回复不符合格式或标准时要求重新输入。

```python
from nonebot.params import ArgPlainText

@matcher.got("arg")
async def handle_arg(arg: str = ArgPlainText()):
    if not is_valid(arg):
        await matcher.reject("输入无效，请重新输入！")
    await matcher.finish(f"收到：{arg}")
```

### reject_arg - 拒绝指定参数

向用户回复一条消息（可选），拒绝指定 `got` 接收的参数。通常在嵌套装饰器时使用。

```python
@matcher.got("a")
@matcher.got("b")
async def handle(a: str = ArgPlainText(), b: str = ArgPlainText()):
    if a not in b:
        await matcher.reject_arg("a", "参数 a 无效，请重新输入！")
```

### reject_receive - 拒绝指定事件

向用户回复一条消息（可选），拒绝指定 `receive` 接收的事件。

```python
@matcher.receive("a")
@matcher.receive("b")
async def handle(a: Event = Received("a"), b: Event = Received("b")):
    if a.get_user_id() != b.get_user_id():
        await matcher.reject_receive("a")
```

### skip - 跳过当前处理

立即结束当前事件处理函数，进入下一个事件处理函数。通常在依赖注入中使用。

```python
from nonebot.params import Depends

async def dependency():
    matcher.skip()

@matcher.handle()
async def handle(check=Depends(dependency)):
    # 这个函数不会被执行
    ...
```

### stop_propagation - 阻止事件传播

阻止事件向更低优先级的事件响应器传播。

```python
from nonebot.matcher import Matcher

@foo.handle()
async def handle(matcher: Matcher):
    matcher.stop_propagation()
```

> **注意**：`stop_propagation` 是实例方法，需要先通过依赖注入获取 `Matcher` 实例。

## 会话控制 - got 装饰器

`got` 装饰器用于实现多轮对话，可以向用户发送询问消息并等待用户回复。

### 基本用法

```python
from nonebot import on_command
from nonebot.adapters import Message
from nonebot.params import CommandArg, ArgPlainText

weather = on_command("天气", priority=10, block=True)

@weather.handle()
async def handle_function(args: Message = CommandArg()):
    if location := args.extract_plain_text():
        await weather.finish(f"今天{location}的天气是...")

@weather.got("location", prompt="请输入地名")
async def got_location(location: str = ArgPlainText()):
    await weather.finish(f"今天{location}的天气是...")
```

对话流程：

```
用户: /天气
Bot:  请输入地名
用户: 北京
Bot:  今天北京的天气是...
```

如果用户直接提供了参数：

```
用户: /天气 上海
Bot:  今天上海的天气是...
```

### 跳过询问 - set_arg

使用 `set_arg` 主动设置参数，如果参数已设置，`got` 不会再次询问：

```python
from nonebot.matcher import Matcher

@weather.handle()
async def handle_function(matcher: Matcher, args: Message = CommandArg()):
    if args.extract_plain_text():
        matcher.set_arg("location", args)

@weather.got("location", prompt="请输入地名")
async def got_location(location: str = ArgPlainText()):
    await weather.finish(f"今天{location}的天气是...")
```

### 参数验证与重试 - reject

```python
@weather.handle()
async def handle_function(matcher: Matcher, args: Message = CommandArg()):
    if args.extract_plain_text():
        matcher.set_arg("location", args)

@weather.got("location", prompt="请输入地名")
async def got_location(location: str = ArgPlainText()):
    if location not in ["北京", "上海", "广州", "深圳"]:
        await weather.reject(f"你想查询的城市 {location} 暂不支持，请重新输入！")
    await weather.finish(f"今天{location}的天气是...")
```

对话流程：

```
用户: /天气
Bot:  请输入地名
用户: 南京
Bot:  你想查询的城市 南京 暂不支持，请重新输入！
用户: 北京
Bot:  今天北京的天气是...
```

### got 嵌套

`got` 装饰器支持与 `got` 和 `receive` 装饰器嵌套，一个事件处理函数可以在接收多个消息后执行：

```python
from nonebot.params import Arg
from nonebot.adapters import Message

@matcher.got("key1", prompt="请输入 key1...")
@matcher.got("key2", prompt="请输入 key2...")
async def got_func(
    key1: Message = Arg(),
    key2: Message = Arg(),
):
    ...
```

## receive 装饰器

`receive` 与 `got` 类似，但它等待一个新的事件（而非消息参数），且不发送询问消息。

```python
from nonebot.params import Received
from nonebot.adapters import Event

@matcher.receive("id")
async def receive_func(event: Event = Received("id")):
    ...
```

### receive 与 got 嵌套

```python
@matcher.receive("key1")
@matcher.got("key2", prompt="请输入 key2...")
@matcher.got("key3", prompt="请输入 key3...")
async def receive_func(
    key1: Event = Received("key1"),
    key2: Message = Arg(),
    key3: Message = Arg(),
):
    ...
```

## 状态管理

### get_arg - 获取参数

获取一个 `got` 接收的参数。

```python
from nonebot.matcher import Matcher

@matcher.handle()
async def handle(matcher: Matcher):
    key = matcher.get_arg("key", default=None)
```

### set_arg - 设置参数

设置 / 覆盖一个 `got` 接收的参数。参数值必须是 `Message` 对象。

```python
from nonebot.matcher import Matcher
from nonebot.adapters import Message

@matcher.handle()
async def handle(matcher: Matcher):
    matcher.set_arg("key", Message("value"))
```

### get_receive - 获取接收的事件

```python
from nonebot.matcher import Matcher

@matcher.handle()
async def handle(matcher: Matcher):
    event = matcher.get_receive("id", default=None)
```

### get_last_receive - 获取最近的事件

```python
from nonebot.matcher import Matcher

@matcher.handle()
async def handle(matcher: Matcher):
    event = matcher.get_last_receive(default=None)
```

### set_receive - 设置接收的事件

```python
from nonebot.matcher import Matcher
from nonebot.adapters import Event

@matcher.handle()
async def handle(matcher: Matcher):
    matcher.set_receive("key", Event())
```

## 事件响应器操作速查表

| 操作 | 类型 | 发送消息 | 结束当前函数 | 结束流程 | 等待事件 |
|------|------|---------|------------|---------|---------|
| `send` | 交互 | ✅ | ❌ | ❌ | ❌ |
| `finish` | 流程控制 | ✅（可选） | ✅ | ✅ | ❌ |
| `pause` | 流程控制 | ✅（可选） | ✅ | ❌ | ✅（进入下一个函数） |
| `reject` | 流程控制 | ✅（可选） | ✅ | ❌ | ✅（重新执行当前函数） |
| `reject_arg` | 流程控制 | ✅（可选） | ✅ | ❌ | ✅（重新执行当前函数） |
| `reject_receive` | 流程控制 | ✅（可选） | ✅ | ❌ | ✅（重新执行当前函数） |
| `skip` | 流程控制 | ❌ | ✅ | ❌ | ❌ |
| `stop_propagation` | 流程控制 | ❌ | ❌ | ❌ | ❌ |

## 完整示例：多轮天气查询

```python
from nonebot import on_command
from nonebot.rule import to_me
from nonebot.matcher import Matcher
from nonebot.adapters import Message
from nonebot.params import CommandArg, ArgPlainText
from nonebot.plugin import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="天气查询",
    description="查询指定城市的天气信息",
    usage="/天气 <城市名>",
)

SUPPORTED_CITIES = {"北京", "上海", "广州", "深圳", "杭州", "成都"}

weather = on_command(
    "天气",
    rule=to_me(),
    aliases={"weather", "查天气"},
    priority=10,
    block=True,
)

@weather.handle()
async def handle_first(matcher: Matcher, args: Message = CommandArg()):
    if args.extract_plain_text():
        matcher.set_arg("location", args)

@weather.got("location", prompt="请输入你要查询天气的城市名称：")
async def handle_location(location: str = ArgPlainText()):
    if location not in SUPPORTED_CITIES:
        await weather.reject(
            f"城市 {location} 暂不支持，"
            f"目前支持：{'、'.join(SUPPORTED_CITIES)}。\n"
            "请重新输入："
        )
    await weather.finish(f"今天{location}的天气是：晴，温度 25°C")
```
