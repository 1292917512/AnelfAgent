# 内置组件

`nonebot_plugin_alconna` 插件提供了一系列内置组件以提升开发者和用户体验。

## 内置插件

类似于 NoneBot 本身提供的内置插件，`nonebot_plugin_alconna` 提供了多个内置插件。

### 加载

你可以用本插件的 `load_builtin_plugin(s)` 来加载它们：

```python
from nonebot_plugin_alconna import load_builtin_plugin, load_builtin_plugins

load_builtin_plugin("echo")
load_builtin_plugins("help", "with")
```

也可以通过[配置项](./alconna-config.md#alconna_builtin_plugins) `alconna_builtin_plugins` 来加载：

```env
ALCONNA_BUILTIN_PLUGINS=["echo", "help", "with", "switch", "lang"]
```

### 使用

#### echo

`echo` 插件能将用户发送的消息原样返回。

**用法示例**：

```text
/echo hello world!
→ hello world!

/echo [图片]
→ [图片]
```

#### help

`help` 插件能列出所有 Alconna 指令。同时还能查询某个指令对应的插件信息。

**用法示例**：

```text
/帮助
→ 当前可用的命令有:
  【0】/echo : echo 指令
  【1】/help : 显示所有命令帮助
  # 输入'命令名 -h|--help' 查看特定命令的语法

/help --plugin-info echo
→ 插件名称: echo
  插件标识: nonebot_plugin_alconna:echo
  插件模块: nonebot-plugin-alconna
  插件版本: 0.57.2
  插件路径: nonebot_plugin_alconna.builtins.plugins.echo
```

**help 插件的帮助信息**：

```text
/help <query: str = -1>
## 注释
  query: 选择某条命令的id或者名称查看具体帮助
显示所有命令帮助
用法:
可以使用 --hide 参数来显示隐藏命令，使用 -P 参数来显示命令所属插件名称
可用的子命令有:
* 是否列出命令所属命名空间
  -N│--namespace│命名空间 [target: str]
  ## 注释
    target: 指定的命名空间
  该子命令内可用的选项有:
  * 列出所有命名空间
    --list
可用的选项有:
* 查看指定页数的命令帮助
  --page <index: int>
* 查看命令所属插件的信息
  -P│插件信息│--plugin-info
* 是否列出隐藏命令
  隐藏│-H│--hide
```

#### lang

`lang` 插件能切换 i18n 的语言设置。

**用法示例**：

```text
/lang list
→ 支持的语言列表:
  * en-US
  * zh-CN

/lang switch en-US
→ Switch to 'en-US' successfully.
```

**lang 插件的帮助信息**：

```text
/lang
i18n配置相关功能
可用的选项有:
* 查看支持的语言列表
  list [name: str]
* 切换语言
  switch [locale: str]
```

其中 `list` 选项可以查找某一插件下的语言支持情况（例如 `/lang list nonebot_plugin_alconna`）。

#### switch

`switch` 插件能用来启用/禁用某个命令，其使用方法与 `help` 类似。

**用法示例**：

```text
/disable
→ 【0】/echo : echo 指令
  【1】/help : 显示所有命令帮助
  【2】/lang : i18n配置相关功能

/disable 0
→ 已禁用 /echo

/echo 1234
→ (无响应)

/enable echo
→ 已启用 /echo

/echo 1234
→ 1234
```

#### with

`with` 插件能在当前会话中设置一个局部命令前缀，以便于有多个子命令的指令使用。

**用法示例**：

```text
/with
→ 当前群组未设置前缀

/with lang
→ 设置前缀成功

list
→ 支持的语言列表:
  * en-US
  * zh-CN
```

**with 插件的帮助信息**：

```text
.with [name: str]
with 指令
用法:
设置局部命令前缀
可用的选项有:
* 设置可能的生效时间
  --expire│expire <time: datetime>
* 取消当前前缀
  unset│--unset
快捷命令:
'[.]局部前缀' = [.]with
```

### 配置

内置插件也有其配置项，并且均以 `NBP_ALC` 开头：

| 配置项 | 说明 | 默认值 |
|---|---|---|
| `nbp_alc_echo_tome` | 是否让 `echo` 插件的消息经过 `to_me` 处理 | - |
| `nbp_alc_help_text` | `help` 指令的指令名 | `"help"` |
| `nbp_alc_help_alias` | `help` 指令的别名 | `"帮助"`, `"命令帮助"` |
| `nbp_alc_help_all_alias` | `help` 指令显示隐藏指令时的别名 | `"所有帮助"`, `"所有命令帮助"` |
| `nbp_alc_page_size` | `help` 与 `switch` 插件每页显示的命令数量 | - |
| `nbp_alc_switch_enable` | `switch` 插件的 `enable` 指令名 | `"enable"` |
| `nbp_alc_switch_enable_alias` | `switch` 插件的 `enable` 指令别名 | `"启用"`, `"启用指令"` |
| `nbp_alc_switch_disable` | `switch` 插件的 `disable` 指令名 | `"disable"` |
| `nbp_alc_switch_disable_alias` | `switch` 插件的 `disable` 指令别名 | `"disable"`, `"禁用"`, `"禁用指令"` |
| `nbp_alc_with_text` | `with` 插件的指令名 | `"with"` |
| `nbp_alc_with_alias` | `with` 插件的别名 | `"局部前缀"` |

## 内置匹配拓展

目前插件提供了 5 个内置的 `Extension`，它们在 `nonebot_plugin_alconna.builtins.extensions` 下。

### ReplyRecordExtension

`ReplyRecordExtension` 可将消息事件中的回复暂存在 extension 中，使得解析用的消息不带回复信息，同时可以在后续的处理中获取回复信息：

```python
from nonebot_plugin_alconna import MsgId, on_alconna
from nonebot_plugin_alconna.builtins.extensions import ReplyRecordExtension

matcher = on_alconna("...", extensions=[ReplyRecordExtension()])

@matcher.handle()
async def handle(msg_id: MsgId, ext: ReplyRecordExtension):
    if reply := ext.get_reply(msg_id):
        ...
    else:
        ...
```

### ReplyMergeExtension

`ReplyMergeExtension` 可将消息事件中的回复指向的原消息合并到当前消息中作为一部分参数：

```python
from nonebot_plugin_alconna import Match, on_alconna
from nonebot_plugin_alconna.builtins.extensions.reply import ReplyMergeExtension

matcher = on_alconna("...", extensions=[ReplyMergeExtension()])

@matcher.handle()
async def handle(content: Match[str]):
    ...
```

构造参数：

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `sep` | `str` | `" "` | 合并时的分隔符 |
| `add_left` | `bool` | `False` | 是否在当前消息的左侧合并回复消息 |

### DiscordSlashExtension

`DiscordSlashExtension` 可自动将 Alconna 对象翻译成 Discord 的 Slash 指令并注册，且将收到的指令交互事件转为指令供命令解析：

```python
from nonebot_plugin_alconna import Match, on_alconna
from nonebot_plugin_alconna.builtins.extensions.discord import DiscordSlashExtension
from arclet.alconna import Alconna, Args, Option, Subcommand, CommandMeta

alc = Alconna(
    ["/"],
    "permission",
    Subcommand("add", Args["plugin", str]["priority?", int]),
    Option("remove", Args["plugin", str]["time?", int]),
    meta=CommandMeta(description="权限管理"),
)

matcher = on_alconna(alc, extensions=[DiscordSlashExtension()])

@matcher.assign("add")
async def add(plugin: Match[str], priority: Match[int], ext: DiscordSlashExtension):
    await ext.send_followup_msg(
        f"added {plugin.result} with {priority.result if priority.available else 0}"
    )

@matcher.assign("remove")
async def remove(plugin: Match[str], time: Match[int]):
    await matcher.finish(
        f"removed {plugin.result} with {time.result if time.available else -1}"
    )
```

### MarkdownOutputExtension

`MarkdownOutputExtension` 可将 Alconna 的自动输出转换为 Markdown 格式。

构造参数：

| 参数 | 类型 | 说明 |
|---|---|---|
| `text_to_image` | `Callable \| None` | 将文本转换为图片的函数，一般用来设置渲染 Markdown 为图片的函数 |
| `escape_dot` | `bool` | 是否转义句中的点号（用来避免被识别为 URL） |

### TelegramSlashExtension

`TelegramSlashExtension` 可将 Alconna 的命令注册在 Telegram 上以获得提示，类似于 `DiscordSlashExtension`：

```python
from nonebot_plugin_alconna import on_alconna
from nonebot.adapters.telegram.model import BotCommandScopeChat
from nonebot_plugin_alconna.builtins.extensions.telegram import TelegramSlashExtension

TelegramSlashExtension.set_scope(BotCommandScopeChat())

matcher = on_alconna("...", extensions=[TelegramSlashExtension()])
```

### 全局加载扩展

```python
from nonebot_plugin_alconna import add_global_extension
from nonebot_plugin_alconna.builtins.extensions.telegram import TelegramSlashExtension

add_global_extension(TelegramSlashExtension)
```

也可以通过[配置项](./alconna-config.md#alconna_global_extensions) `alconna_global_extensions` 来全局加载，对于内置扩展可使用 `@` 缩写路径：

```env
ALCONNA_GLOBAL_EXTENSIONS=["@reply:ReplyMergeExtension"]
```

## 内置自定义消息段

目前插件提供了 3 个内置的 `Segment`，它们在 `nonebot_plugin_alconna.builtins.segments` 下：

### Markdown

可以传入 Markdown 模板的元素：

```python
from nonebot_plugin_alconna.builtins.segments import Markdown

seg = Markdown("# 标题\n\n正文内容")
```

### MarketFace

特指 QQ 的商城表情：

```python
from nonebot_plugin_alconna.builtins.segments import MarketFace

seg = MarketFace(tab_id="...", face_id="...", key="...")
```

### MusicShare

特指 QQ 的音乐分享卡片：

```python
from nonebot_plugin_alconna.builtins.segments import MusicShare

seg = MusicShare(
    kind="...",
    title="...",
    content="...",
    url="...",
    thumbnail="...",
    audio="...",
)
```
