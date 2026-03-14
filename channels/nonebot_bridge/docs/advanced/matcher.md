# 事件响应器进阶

事件响应器（Matcher）是 NoneBot 事件处理的核心组件。本文详细介绍 Matcher 的高级用法，包括组成要素、内置规则和分组。

## Matcher 的组成要素

创建 Matcher 时可以指定以下参数：

| 参数 | 类型 | 说明 |
|------|------|------|
| `type` | `str` | 响应的事件类型 |
| `rule` | `Rule \| T_RuleChecker` | 事件响应规则 |
| `permission` | `Permission \| T_PermissionChecker` | 事件触发权限 |
| `priority` | `int` | 优先级（数字越小越优先，默认 `1`） |
| `block` | `bool` | 是否阻止更低优先级的 Matcher |
| `temp` | `bool` | 是否为临时 Matcher（响应一次后销毁） |
| `expire_time` | `datetime \| timedelta \| None` | 过期时间 |
| `default_state` | `T_State \| None` | 默认状态字典 |

### type — 事件类型

```python
from nonebot import on

# 仅响应消息事件
matcher = on("message")

# 仅响应通知事件
matcher = on("notice")

# 仅响应请求事件
matcher = on("request")

# 响应所有事件（空字符串）
matcher = on("")
```

常用的快捷方式已预定义了 `type`：

| 快捷函数 | 等价 type |
|---------|----------|
| `on_message()` | `"message"` |
| `on_notice()` | `"notice"` |
| `on_request()` | `"request"` |
| `on_metaevent()` | `"meta_event"` |

### priority — 优先级

```python
from nonebot import on_message

# 高优先级（先执行）
high = on_message(priority=1)

# 低优先级（后执行）
low = on_message(priority=10)
```

### block — 阻断传播

```python
from nonebot import on_command

# block=True：响应后阻止低优先级 Matcher
cmd = on_command("help", priority=5, block=True)

# block=False：响应后继续传播到更低优先级
cmd2 = on_command("hello", priority=5, block=False)
```

> `on_command()` 等快捷函数默认 `block=True`。

### temp — 临时 Matcher

```python
from nonebot import on_message

# 响应一次后自动销毁
temp_matcher = on_message(temp=True)
```

### expire_time — 过期时间

```python
from datetime import datetime, timedelta
from nonebot import on_message

# 10 分钟后过期
matcher = on_message(
    temp=True,
    expire_time=timedelta(minutes=10),
)

# 指定具体过期时间
matcher = on_message(
    temp=True,
    expire_time=datetime(2025, 12, 31, 23, 59, 59),
)
```

### default_state — 默认状态

```python
from nonebot import on_command

cmd = on_command("greet", default_state={"greeting": "你好"})

@cmd.handle()
async def handle(state: dict):
    greeting = state.get("greeting", "Hi")
    await cmd.finish(greeting)
```

## 内置响应规则

NoneBot 提供了丰富的内置响应规则，可单独使用或组合使用。

### startswith — 消息前缀匹配

```python
from nonebot import on_startswith

# 匹配以 "你好" 开头的消息
matcher = on_startswith("你好")

# 匹配多个前缀
matcher = on_startswith(("你好", "hello", "hi"))

# 忽略大小写
matcher = on_startswith("hello", ignorecase=True)
```

### endswith — 消息后缀匹配

```python
from nonebot import on_endswith

# 匹配以 "吗" 结尾的消息
matcher = on_endswith("吗")

# 匹配多个后缀
matcher = on_endswith(("吗", "呢", "啊"))
```

### fullmatch — 完整匹配

```python
from nonebot import on_fullmatch

# 完整匹配 "签到"
matcher = on_fullmatch("签到")

# 匹配多个
matcher = on_fullmatch(("签到", "打卡"))

# 忽略大小写
matcher = on_fullmatch("Hello", ignorecase=True)
```

### keyword — 关键词匹配

```python
from nonebot import on_keyword

# 消息中包含任一关键词即触发
matcher = on_keyword({"天气", "气温", "下雨"})
```

### command — 命令匹配

```python
from nonebot import on_command

# 匹配 /help 命令
matcher = on_command("help")

# 命令别名
matcher = on_command("help", aliases={"帮助", "usage"})

# 多级命令
matcher = on_command(("admin", "ban"))  # 匹配 /admin.ban
```

命令前缀由 `COMMAND_START` 配置决定：

```dotenv
COMMAND_START=["/", "!", ""]
COMMAND_SEP=["."]
```

### shell_command — Shell 风格命令

```python
from nonebot import on_shell_command
from nonebot.rule import ArgumentParser

parser = ArgumentParser()
parser.add_argument("name", type=str)
parser.add_argument("-t", "--times", type=int, default=1)
parser.add_argument("-f", "--flag", action="store_true")

matcher = on_shell_command("greet", parser=parser)
```

### regex — 正则匹配

```python
from nonebot import on_regex

# 正则匹配
matcher = on_regex(r"^(\d+)\s*\+\s*(\d+)$")

# 忽略大小写
matcher = on_regex(r"^hello\s+(\w+)$", flags=re.IGNORECASE)
```

### to_me — @机器人

```python
from nonebot import on_message
from nonebot.rule import to_me

# 仅当 @机器人 时触发
matcher = on_message(rule=to_me())
```

### is_type — 事件类型匹配

```python
from nonebot import on
from nonebot.rule import is_type
from nonebot.adapters.onebot.v11 import GroupMessageEvent

# 仅匹配群消息事件
matcher = on(rule=is_type(GroupMessageEvent))
```

## 规则组合

规则之间可以使用 `&`（与）、`|`（或）、`~`（非）组合：

```python
from nonebot import on_message
from nonebot.rule import to_me, keyword, startswith

# 同时满足 @机器人 和 包含关键词
matcher = on_message(rule=to_me() & keyword("你好"))

# 满足任一条件
matcher = on_message(rule=startswith("!") | startswith("/"))

# 取反
matcher = on_message(rule=~to_me())  # 非 @机器人
```

## CommandGroup — 命令组

`CommandGroup` 用于创建一组共享前缀的命令。

```python
from nonebot import CommandGroup

# 创建命令组，共享配置
weather = CommandGroup(
    "weather",
    priority=5,
    block=True,
)

# 创建子命令：/weather.query
query = weather.command("query", aliases={"查询"})

# 创建子命令：/weather.sub
subscribe = weather.command("sub", aliases={"订阅"})

# 创建子命令：/weather.unsub
unsubscribe = weather.command("unsub", aliases={"取消订阅"})

@query.handle()
async def handle_query():
    await query.finish("天气查询功能")

@subscribe.handle()
async def handle_sub():
    await subscribe.finish("已订阅天气")

@unsubscribe.handle()
async def handle_unsub():
    await unsubscribe.finish("已取消订阅")
```

### CommandGroup 参数

```python
group = CommandGroup(
    "admin",
    prefix_aliases=True,  # 是否共享前缀别名
    # 以下参数会被所有子命令继承
    rule=to_me(),
    permission=SUPERUSER,
    priority=1,
    block=True,
)

# 子命令可以覆盖继承的参数
ban = group.command("ban", priority=2)
kick = group.command("kick", permission=None)
```

## MatcherGroup — 响应器组

`MatcherGroup` 用于创建一组共享配置的事件响应器。

```python
from nonebot import MatcherGroup

# 创建响应器组
group = MatcherGroup(
    priority=10,
    block=True,
)

# 使用不同的快捷方式创建 Matcher
cmd = group.on_command("hello")
msg = group.on_message()
kw = group.on_keyword({"test"})

# 所有 Matcher 共享 priority=10 和 block=True
```

### MatcherGroup 支持的方法

```python
group = MatcherGroup(priority=5)

group.on()                    # 通用 Matcher
group.on_message()            # 消息事件
group.on_notice()             # 通知事件
group.on_request()            # 请求事件
group.on_metaevent()          # 元事件
group.on_startswith("hi")     # 前缀匹配
group.on_endswith("bye")      # 后缀匹配
group.on_fullmatch("ok")     # 完整匹配
group.on_keyword({"test"})   # 关键词匹配
group.on_command("cmd")       # 命令匹配
group.on_shell_command("sh")  # Shell 命令
group.on_regex(r"pattern")    # 正则匹配
```

## Alconna 第三方规则

[nonebot-plugin-alconna](https://github.com/nonebot/plugin-alconna) 提供了更强大的命令解析能力：

```python
from nonebot import require

require("nonebot_plugin_alconna")

from nonebot_plugin_alconna import on_alconna, UniMessage
from arclet.alconna import Alconna, Args, Option, Subcommand

# 定义命令
alc = Alconna(
    "weather",
    Args["city", str],
    Option("-d|--days", Args["days", int, 3]),
)

matcher = on_alconna(alc)

@matcher.handle()
async def handle(city: str, days: int = 3):
    await matcher.finish(f"查询 {city} 未来 {days} 天的天气")
```

### Alconna 的优势

| 特性 | 内置 command | Alconna |
|------|-------------|---------|
| 参数类型检查 | ❌ | ✅ |
| 子命令 | 需手动实现 | ✅ 原生支持 |
| 选项/标志 | 需 shell_command | ✅ 更灵活 |
| 模糊匹配 | ❌ | ✅ |
| 自动补全 | ❌ | ✅ |
| 跨适配器消息 | ❌ | ✅ UniMessage |
| 帮助信息生成 | 需手动 | ✅ 自动 |

### Alconna 复杂命令示例

```python
from arclet.alconna import Alconna, Args, Option, Subcommand

cmd = Alconna(
    "admin",
    Subcommand(
        "user",
        Subcommand(
            "ban",
            Args["target", str]["duration?", int],
            Option("--reason|-r", Args["reason", str]),
        ),
        Subcommand(
            "unban",
            Args["target", str],
        ),
        Subcommand(
            "info",
            Args["target", str],
        ),
    ),
    Subcommand(
        "config",
        Option("--set|-s", Args["key", str]["value", str]),
        Option("--get|-g", Args["key", str]),
    ),
)

# 可匹配：
# /admin user ban @user 3600 --reason 违规
# /admin user unban @user
# /admin config --set welcome_msg "欢迎！"
```
