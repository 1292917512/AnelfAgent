# 响应规则

响应规则（Rule）用于控制事件响应器是否对某个事件进行响应。NoneBot 提供了丰富的内置规则，也支持自定义规则。

---

## Rule 基础

### 什么是 Rule

`Rule` 是一组检查函数（`RuleChecker`）的集合。只有当 **所有** 检查函数都返回 `True` 时，规则才匹配成功。

```python
from nonebot.rule import Rule
```

### RuleChecker

`RuleChecker` 是一个异步函数，接受依赖注入参数，返回 `bool`：

```python
from nonebot.adapters import Bot, Event


async def my_checker(bot: Bot, event: Event) -> bool:
    return event.get_user_id() == "123456789"
```

---

## 自定义规则

### 基本用法

```python
from nonebot import on_message
from nonebot.adapters import Bot, Event
from nonebot.rule import Rule


async def is_test_user(event: Event) -> bool:
    return event.get_user_id() in {"123456789", "987654321"}


test_matcher = on_message(rule=Rule(is_test_user))


@test_matcher.handle()
async def handle():
    await test_matcher.finish("你是测试用户！")
```

### 使用依赖注入

规则检查函数支持 NoneBot 的依赖注入系统：

```python
from nonebot.adapters import Bot, Event
from nonebot.rule import Rule


async def is_group_admin(bot: Bot, event: Event) -> bool:
    """检查用户是否为群管理员"""
    try:
        user_id = event.get_user_id()
        session = event.get_session_id()
        if "group" not in session:
            return False
        group_id = session.split("_")[1]
        info = await bot.call_api("get_group_member_info",
                                   group_id=int(group_id),
                                   user_id=int(user_id))
        return info.get("role") in ("admin", "owner")
    except Exception:
        return False


admin_matcher = on_message(rule=Rule(is_group_admin))
```

### 带状态的规则

```python
from nonebot.adapters import Event
from nonebot.rule import Rule
from nonebot.typing import T_State


async def check_and_store(event: Event, state: T_State) -> bool:
    """规则匹配成功时可以向 state 中写入数据，供后续处理函数使用"""
    text = event.get_plaintext()
    if text.startswith("翻译"):
        state["content"] = text[2:].strip()
        return True
    return False


translate = on_message(rule=Rule(check_and_store))


@translate.handle()
async def handle(state: T_State):
    content = state["content"]
    await translate.finish(f"翻译内容：{content}")
```

---

## 组合规则

### 使用 `&`（与）

`&` 运算符将两个规则合并，**两个规则都通过** 才匹配：

```python
from nonebot import on_message
from nonebot.rule import Rule


async def is_long_text(event) -> bool:
    return len(event.get_plaintext()) > 10


async def contains_hello(event) -> bool:
    return "hello" in event.get_plaintext().lower()


# 同时满足：文本长度 > 10 且包含 "hello"
matcher = on_message(rule=Rule(is_long_text) & Rule(contains_hello))
```

### 使用 `|`（或）

`|` 运算符将两个规则合并，**任一规则通过** 即匹配：

```python
from nonebot import on_message
from nonebot.rule import Rule


async def starts_with_hi(event) -> bool:
    return event.get_plaintext().startswith("hi")


async def starts_with_hello(event) -> bool:
    return event.get_plaintext().startswith("hello")


# 任一满足：以 "hi" 或 "hello" 开头
matcher = on_message(rule=Rule(starts_with_hi) | Rule(starts_with_hello))
```

### 混合组合

```python
rule = (Rule(check_a) & Rule(check_b)) | Rule(check_c)
matcher = on_message(rule=rule)
```

---

## 内置规则

NoneBot 提供了大量预定义规则辅助函数，可直接使用或与 `on_*` 系列快捷函数配合。

### startswith

匹配消息纯文本以指定前缀开头：

```python
from nonebot import on_startswith

# 匹配以 "查询" 开头的消息
query = on_startswith("查询", priority=10, block=True)


@query.handle()
async def handle(event):
    text = event.get_plaintext()
    keyword = text[len("查询"):].strip()
    await query.finish(f"正在查询：{keyword}")
```

也可以传入元组匹配多个前缀：

```python
# 匹配以 "查询" 或 "搜索" 开头
query = on_startswith(("查询", "搜索"), priority=10, block=True)
```

参数说明：

| 参数 | 类型 | 说明 |
|------|------|------|
| `msg` | `str \| tuple[str, ...]` | 匹配的前缀 |
| `ignorecase` | `bool` | 是否忽略大小写，默认 `False` |

### endswith

匹配消息纯文本以指定后缀结尾：

```python
from nonebot import on_endswith

# 匹配以 "是什么" 结尾的消息
what_is = on_endswith("是什么", priority=10, block=True)


@what_is.handle()
async def handle(event):
    text = event.get_plaintext()
    topic = text[:text.rfind("是什么")].strip()
    await what_is.finish(f"让我来解释「{topic}」...")
```

多后缀匹配：

```python
what_is = on_endswith(("是什么", "是啥", "是什么意思"), priority=10, block=True)
```

参数说明：

| 参数 | 类型 | 说明 |
|------|------|------|
| `msg` | `str \| tuple[str, ...]` | 匹配的后缀 |
| `ignorecase` | `bool` | 是否忽略大小写，默认 `False` |

### fullmatch

匹配消息纯文本完全等于指定内容：

```python
from nonebot import on_fullmatch

# 精确匹配 "菜单"
menu = on_fullmatch("菜单", priority=10, block=True)


@menu.handle()
async def handle():
    await menu.finish("功能列表：\n1. 天气\n2. 翻译\n3. 搜索")
```

多文本匹配：

```python
menu = on_fullmatch(("菜单", "帮助", "help"), ignorecase=True, priority=10, block=True)
```

参数说明：

| 参数 | 类型 | 说明 |
|------|------|------|
| `msg` | `str \| tuple[str, ...]` | 匹配的完整文本 |
| `ignorecase` | `bool` | 是否忽略大小写，默认 `False` |

### keyword

匹配消息纯文本中包含指定关键词：

```python
from nonebot import on_keyword

# 消息中包含 "天气" 就触发
weather = on_keyword({"天气"}, priority=10, block=True)


@weather.handle()
async def handle(event):
    await weather.finish("你想查什么地方的天气呢？")
```

多关键词匹配（任一命中即可）：

```python
greeting = on_keyword({"你好", "hello", "hi", "嗨"}, priority=10, block=True)


@greeting.handle()
async def handle():
    await greeting.finish("你好呀！")
```

参数说明：

| 参数 | 类型 | 说明 |
|------|------|------|
| `keywords` | `set[str]` | 关键词集合，任一匹配即触发 |

### command

匹配命令格式的消息。命令前缀由 `COMMAND_START` 配置，命令分隔符由 `COMMAND_SEP` 配置。

```python
from nonebot import on_command
from nonebot.adapters import Message
from nonebot.params import CommandArg

# 匹配 /天气 或 !天气（取决于 COMMAND_START 配置）
weather = on_command("天气", priority=10, block=True)


@weather.handle()
async def handle(args: Message = CommandArg()):
    city = args.extract_plain_text().strip()
    if not city:
        await weather.finish("请输入城市名，例如：/天气 北京")
    await weather.finish(f"正在查询 {city} 的天气...")
```

命令别名：

```python
# /天气 或 /weather 或 /tq 都可以触发
weather = on_command("天气", aliases={"weather", "tq"}, priority=10, block=True)
```

多级命令：

```python
# 匹配 /group.list（COMMAND_SEP=["."] 时）
group_list = on_command(("group", "list"), priority=10, block=True)
```

参数说明：

| 参数 | 类型 | 说明 |
|------|------|------|
| `cmd` | `str \| tuple[str, ...]` | 命令名或多级命令元组 |
| `aliases` | `set[str \| tuple[str, ...]]` | 命令别名集合 |
| `force_whitespace` | `bool \| str` | 是否强制命令后需要空白字符 |

获取命令信息：

```python
from nonebot.params import Command, CommandArg, CommandStart, CommandWhitespace

@weather.handle()
async def handle(
    cmd: tuple[str, ...] = Command(),          # 命令元组，如 ("天气",)
    args: Message = CommandArg(),               # 命令参数
    start: str = CommandStart(),                # 命令前缀，如 "/"
    whitespace: str = CommandWhitespace(),      # 命令与参数之间的空白
):
    pass
```

### shell_command

类似 shell 命令的解析，支持使用 `ArgumentParser`：

```python
from nonebot import on_shell_command
from nonebot.params import ShellCommandArgs, ShellCommandArgv
from nonebot.rule import ArgumentParser

parser = ArgumentParser("ban")
parser.add_argument("user_id", type=str, help="用户 ID")
parser.add_argument("-t", "--time", type=int, default=60, help="封禁时长（分钟）")
parser.add_argument("-r", "--reason", type=str, default="违规", help="封禁原因")

ban = on_shell_command("ban", parser=parser, priority=10, block=True)


@ban.handle()
async def handle(args=ShellCommandArgs()):
    user_id = args.user_id
    time = args.time
    reason = args.reason
    await ban.finish(f"已封禁用户 {user_id}，时长 {time} 分钟，原因：{reason}")
```

不使用 parser 时可以获取原始参数列表：

```python
cmd = on_shell_command("cmd", priority=10, block=True)


@cmd.handle()
async def handle(argv: list[str] = ShellCommandArgv()):
    await cmd.finish(f"参数列表：{argv}")
```

### regex

使用正则表达式匹配消息纯文本：

```python
from nonebot import on_regex
from nonebot.params import RegexGroup, RegexMatched

# 匹配形如 "查询 xxx 天气" 的消息
weather = on_regex(r"^查询\s*(.+?)\s*天气$", priority=10, block=True)


@weather.handle()
async def handle(
    matched: str = RegexMatched(),            # 完整匹配文本
    groups: tuple = RegexGroup(),             # 正则分组
):
    city = groups[0]
    await weather.finish(f"查询到 {city} 的天气...")
```

使用 flags：

```python
import re

# 忽略大小写匹配
hello = on_regex(r"^hello\s+world$", flags=re.IGNORECASE, priority=10, block=True)
```

参数说明：

| 参数 | 类型 | 说明 |
|------|------|------|
| `pattern` | `str` | 正则表达式 |
| `flags` | `int \| re.RegexFlag` | 正则标志，默认 `0` |

获取匹配结果：

| 依赖 | 类型 | 说明 |
|------|------|------|
| `RegexMatched()` | `str` | 完整匹配文本 |
| `RegexGroup()` | `tuple[Any, ...]` | 分组匹配结果 |
| `RegexDict()` | `dict[str, Any]` | 命名分组匹配结果 |

命名分组示例：

```python
from nonebot.params import RegexDict

calc = on_regex(
    r"^(?P<num1>\d+)\s*(?P<op>[+\-*/])\s*(?P<num2>\d+)$",
    priority=10,
    block=True,
)


@calc.handle()
async def handle(groups: dict[str, str] = RegexDict()):
    num1 = int(groups["num1"])
    num2 = int(groups["num2"])
    op = groups["op"]
    result = eval(f"{num1}{op}{num2}")
    await calc.finish(f"{num1} {op} {num2} = {result}")
```

### to_me

检查事件是否与机器人直接相关（@机器人、私聊、或以昵称开头）：

```python
from nonebot import on_command
from nonebot.rule import to_me

# 只有 @机器人 或私聊时才触发
helper = on_command("help", rule=to_me(), priority=10, block=True)


@helper.handle()
async def handle():
    await helper.finish("这是帮助信息...")
```

`to_me()` 返回 `True` 的情况：

- 私聊消息
- 消息中 @了机器人
- 消息以机器人昵称（`NICKNAME` 配置）开头

### is_type

检查事件是否属于指定的事件类型：

```python
from nonebot import on_message
from nonebot.rule import is_type
from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent

# 仅群聊消息触发
group_only = on_message(rule=is_type(GroupMessageEvent), priority=10, block=True)


@group_only.handle()
async def handle():
    await group_only.finish("这是群聊消息！")


# 仅私聊消息触发
private_only = on_message(rule=is_type(PrivateMessageEvent), priority=10, block=True)


@private_only.handle()
async def handle_private():
    await private_only.finish("这是私聊消息！")
```

匹配多种事件类型：

```python
# 群聊或私聊
msg_only = on_message(
    rule=is_type(GroupMessageEvent, PrivateMessageEvent),
    priority=10,
    block=True,
)
```

---

## 在事件响应器中使用规则

### on_message 配合自定义规则

```python
from nonebot import on_message
from nonebot.rule import Rule


async def check_vip(event) -> bool:
    vip_list = {"111", "222", "333"}
    return event.get_user_id() in vip_list


vip_matcher = on_message(rule=Rule(check_vip), priority=5, block=True)


@vip_matcher.handle()
async def handle():
    await vip_matcher.finish("VIP 用户你好！")
```

### 组合内置规则与自定义规则

```python
from nonebot import on_message
from nonebot.rule import Rule, to_me


async def check_admin(event) -> bool:
    return event.get_user_id() in {"123456789"}


# 必须 @机器人 且发送者是管理员
admin_cmd = on_message(rule=to_me() & Rule(check_admin), priority=1, block=True)
```

---

## 规则速查表

| 快捷函数 | 对应规则 | 说明 |
|----------|---------|------|
| `on_startswith(msg)` | `startswith(msg)` | 消息前缀匹配 |
| `on_endswith(msg)` | `endswith(msg)` | 消息后缀匹配 |
| `on_fullmatch(msg)` | `fullmatch(msg)` | 消息完全匹配 |
| `on_keyword(kws)` | `keyword(kws)` | 关键词匹配 |
| `on_command(cmd)` | `command(cmd)` | 命令匹配 |
| `on_shell_command(cmd)` | `shell_command(cmd)` | Shell 风格命令 |
| `on_regex(pattern)` | `regex(pattern)` | 正则匹配 |
| — | `to_me()` | @机器人 / 私聊 / 昵称触发 |
| — | `is_type(EventType)` | 事件类型匹配 |

所有 `on_*` 快捷函数都接受 `rule` 参数用于附加额外规则：

```python
from nonebot import on_command
from nonebot.rule import to_me

# 命令规则 + to_me 规则
cmd = on_command("test", rule=to_me(), priority=10, block=True)
```
