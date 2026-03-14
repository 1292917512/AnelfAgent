<!-- source: https://nonebot.dev/docs/tutorial/matcher -->

# 事件响应器

事件响应器（Matcher）是对接收到的事件进行响应的基本单元，所有的事件响应器都继承自 `Matcher` 基类。

在 NoneBot 中，事件响应器可以通过一系列特定的规则筛选出具有某种特征的事件，并按照特定的流程交由预定义的事件处理依赖进行处理。例如，内置插件 `echo` 定义的事件响应器能响应用户发送的 "/echo hello world" 消息，提取 "hello world" 信息并作为回复消息发送。

## 事件响应器辅助函数

NoneBot 提供了一系列"事件响应器辅助函数"来辅助我们用最简的方式创建带有不同规则预设的事件响应器。

辅助函数以 `on()` 或 `on_<type>()` 形式命名，可以从 `nonebot` 模块直接导入。

### 基础示例

```python
from nonebot import on_command

weather = on_command("天气")
```

### 完整参数示例

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
```

## 辅助函数一览

### on()

基础事件响应器，可以响应所有类型的事件。

```python
from nonebot import on

matcher = on(
    type="message",   # 事件类型（可选）
    rule=None,         # 匹配规则（可选）
    priority=1,        # 优先级
    block=False,       # 是否阻断
    temp=False,        # 是否为临时响应器
)
```

### on_message()

消息事件响应器，仅响应消息类型事件。

```python
from nonebot import on_message

matcher = on_message(
    rule=None,
    priority=1,
    block=True,
)

@matcher.handle()
async def handle_msg():
    await matcher.finish("收到消息了！")
```

### on_notice()

通知事件响应器，仅响应通知类型事件。

```python
from nonebot import on_notice

notice_handler = on_notice(priority=1, block=False)

@notice_handler.handle()
async def handle_notice():
    pass
```

### on_request()

请求事件响应器，仅响应请求类型事件（如加群、加好友请求）。

```python
from nonebot import on_request

request_handler = on_request(priority=1, block=False)

@request_handler.handle()
async def handle_request():
    pass
```

### on_command()

命令事件响应器，根据命令名匹配消息。命令前需要有 `COMMAND_START` 中设置的前缀。

```python
from nonebot import on_command

# 基本用法：匹配 /天气
weather = on_command("天气")

# 带别名：匹配 /天气、/weather、/查天气
weather = on_command("天气", aliases={"weather", "查天气"})

# 带子命令分隔符：匹配 /天气.北京（分隔符由 COMMAND_SEP 配置）
weather = on_command(("天气", "北京"))

@weather.handle()
async def handle_weather():
    await weather.finish("今天天气晴朗")
```

**参数说明：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `cmd` | `str \| tuple[str, ...]` | 命令名称 |
| `aliases` | `set[str \| tuple[str, ...]]` | 命令别名 |
| `rule` | `Rule \| T_RuleChecker` | 额外匹配规则 |
| `force_whitespace` | `str \| bool` | 是否强制命令后有空白符 |
| `priority` | `int` | 优先级（数字越小越先） |
| `block` | `bool` | 是否阻断后续响应器 |

### on_startswith()

消息开头匹配响应器，匹配消息纯文本开头。

```python
from nonebot import on_startswith

# 匹配以 "你好" 开头的消息
hello = on_startswith("你好")

# 匹配多个开头
greet = on_startswith(("你好", "hello", "hi"))

# 忽略大小写
greet = on_startswith("hello", ignorecase=True)

@hello.handle()
async def handle_hello():
    await hello.finish("你好呀！")
```

### on_endswith()

消息结尾匹配响应器，匹配消息纯文本结尾。

```python
from nonebot import on_endswith

# 匹配以 "吗" 结尾的消息
question = on_endswith("吗")

# 匹配多个结尾
question = on_endswith(("吗", "？", "?"))

# 忽略大小写
question = on_endswith("right", ignorecase=True)

@question.handle()
async def handle_question():
    await question.finish("是的！")
```

### on_fullmatch()

消息完全匹配响应器，匹配消息纯文本完全相同。

```python
from nonebot import on_fullmatch

# 完全匹配 "菜单"
menu = on_fullmatch("菜单")

# 完全匹配多个关键词
menu = on_fullmatch(("菜单", "menu", "帮助"))

# 忽略大小写
menu = on_fullmatch("MENU", ignorecase=True)

@menu.handle()
async def handle_menu():
    await menu.finish("这是菜单...")
```

### on_keyword()

关键词匹配响应器，匹配消息纯文本中包含的关键词。

```python
from nonebot import on_keyword

# 消息中包含 "天气" 就会触发
weather = on_keyword({"天气"})

# 消息中包含任意一个关键词就会触发
food = on_keyword({"吃饭", "美食", "餐厅"})

@weather.handle()
async def handle_weather():
    await weather.finish("你想查天气？")
```

> **注意**：`on_keyword` 的参数是 `set` 类型，使用花括号 `{}`。

### on_regex()

正则表达式匹配响应器，对消息纯文本进行正则匹配。

```python
from nonebot import on_regex

# 匹配 "天气" 后跟随地名
weather = on_regex(r"^天气\s+(.+)$")

# 忽略大小写
greeting = on_regex(r"^hello\s+\w+$", flags=re.IGNORECASE)

@weather.handle()
async def handle_weather():
    await weather.finish("正在查询天气...")
```

**获取正则匹配结果：**

```python
from nonebot.params import RegexGroup, RegexMatched

@weather.handle()
async def handle_weather(
    matched: str = RegexMatched(),
    groups: tuple = RegexGroup(),
):
    location = groups[0] if groups else "未知"
    await weather.finish(f"正在查询 {location} 的天气...")
```

### on_type()

类型匹配响应器，根据事件类型进行匹配。

```python
from nonebot import on_type
from nonebot.adapters.onebot.v11 import PokeNotifyEvent

poke = on_type(PokeNotifyEvent, priority=10, block=True)

@poke.handle()
async def handle_poke():
    await poke.finish("别戳我！")
```

### on_shell_command()

Shell 风格命令响应器，支持类似 shell 命令的参数解析。

```python
from nonebot import on_shell_command
from nonebot.rule import ArgumentParser

parser = ArgumentParser()
parser.add_argument("name")
parser.add_argument("-t", "--type", default="text")

cmd = on_shell_command("test", parser=parser)
```

## 通用参数说明

所有辅助函数都支持以下通用参数：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `rule` | `Rule \| T_RuleChecker` | `None` | 事件匹配规则 |
| `permission` | `Permission` | 取决于辅助函数 | 权限匹配 |
| `handlers` | `list[Handler]` | `None` | 事件处理函数列表 |
| `temp` | `bool` | `False` | 是否为临时响应器（触发后自动销毁） |
| `expire_time` | `datetime \| timedelta` | `None` | 过期时间 |
| `priority` | `int` | `1` | 优先级（数字越小越优先） |
| `block` | `bool` | 取决于辅助函数 | 是否阻断后续更低优先级的响应器 |
| `state` | `dict` | `None` | 初始会话状态 |

## 优先级与阻断

### 优先级 (priority)

优先级是一个正整数，**数字越小，优先级越高**。当多个事件响应器同时被触发时，优先级高的会先被执行。

```python
# 优先级为 1，最先处理
high = on_command("高优先级", priority=1)

# 优先级为 10，后处理
low = on_command("低优先级", priority=10)
```

### 阻断 (block)

当一个事件响应器设置 `block=True` 时，在该响应器完成处理后，事件将不会继续传播到更低优先级的响应器。

```python
from nonebot import on_command

# 阻断后续响应器
weather = on_command("天气", priority=10, block=True)

# 如果 weather 匹配成功并设置了 block=True，
# 下面这个同优先级或更低优先级的响应器不会被触发
another = on_command("天气查询", priority=20, block=True)
```

> **注意**：`on_command`、`on_startswith`、`on_endswith`、`on_fullmatch`、`on_keyword`、`on_regex` 的 `block` 默认为 `True`。

## 常用匹配规则

### to_me()

判断事件是否与机器人有关（被 @、私聊等）：

```python
from nonebot import on_command
from nonebot.rule import to_me

weather = on_command("天气", rule=to_me())
```

### 自定义规则

```python
from nonebot.rule import Rule

async def my_rule() -> bool:
    return True

matcher = on_command("test", rule=Rule(my_rule))
```

## 完整插件示例

```python
from nonebot import on_command, on_keyword, on_fullmatch
from nonebot.rule import to_me
from nonebot.plugin import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="示例插件",
    description="展示各种事件响应器的用法",
    usage="/天气 <城市> - 查询天气\n/帮助 - 查看帮助",
)

# 命令响应器
weather = on_command(
    "天气",
    rule=to_me(),
    aliases={"weather"},
    priority=10,
    block=True,
)

# 关键词响应器
greet = on_keyword({"你好", "hello"}, priority=20, block=True)

# 完全匹配响应器
help_cmd = on_fullmatch(("帮助", "help", "菜单"), priority=5, block=True)

@weather.handle()
async def handle_weather():
    await weather.finish("请提供城市名称")

@greet.handle()
async def handle_greet():
    await greet.finish("你好呀！有什么可以帮你的？")

@help_cmd.handle()
async def handle_help():
    await help_cmd.finish("这是帮助菜单...")
```
