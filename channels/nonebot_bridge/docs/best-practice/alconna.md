# Alconna 插件

[nonebot-plugin-alconna](https://github.com/nonebot/plugin-alconna) 是一类极大地提升了 NoneBot 开发体验的插件。

该插件可分为三个部分：

**命令解析**：基于 [Alconna](https://github.com/ArcletProject/Alconna) 的命令解析器，支持复杂的命令参数解析。

**通用消息组件**：实现了跨平台接收、发送、撤回、编辑、表态消息的功能。

- `UniMsg`、`MsgId`、`MsgTarget`、`at_in`、`at_me` 等提供给 NoneBot 使用的依赖注入和 `Rule`。
- `Target` 通用消息目标模型，并通过该模型进行主动消息发送。
- `message_recall`、`message_edit`、`message_reaction` 等功能函数。
- `Text`、`Image`、`At` 等通用消息段模型，既与 `UniMessage` 配合使用，又能用于 `Alconna` 的命令解析。
- `UniMessage` 通用消息模型，支持各适配器下的消息转换和导出、发送。

**内置功能插件**：基于上述部分实现的内置功能插件。

- `with`：针对具有多个子命令的指令，通过 `with` 在当前会话中载入命令头以节省输入。
- `switch`：禁用/启用某个指令。
- `lang`：切换 `Alconna` 使用的语言。
- `help`：列出所有 `on_alconna` 事件响应器的帮助信息或其对应的插件信息。
- `echo`：通过 `on_alconna` 实现的 echo 插件，支持回显回复消息。

## 适配器支持

以最新版本为例 (v0.59)，本插件已支持 NoneBot 生态中几乎所有的适配器，包括：

| 协议名称 | 路径 |
|---|---|
| [OneBot 协议](https://onebot.dev/) | `adapters.onebot11`, `adapters.onebot12` |
| [Telegram](https://core.telegram.org/bots/api) | `adapters.telegram` |
| [飞书](https://open.feishu.cn/document/home/index) | `adapters.feishu` |
| [GitHub](https://docs.github.com/en/developers/apps) | `adapters.github` |
| [QQ bot](https://github.com/nonebot/adapter-qq) | `adapters.qq` |
| [钉钉](https://open.dingtalk.com/document/) | `adapters.ding` |
| [Console](https://github.com/nonebot/adapter-console) | `adapters.console` |
| [开黑啦](https://developer.kookapp.cn/) | `adapters.kook` |
| [Mirai](https://docs.mirai.mamoe.net/mirai-api-http/) | `adapters.mirai` |
| [Ntchat](https://github.com/JustUndertaker/adapter-ntchat) | `adapters.ntchat` |
| [MineCraft](https://github.com/17TheWord/nonebot-adapter-minecraft) | `adapters.minecraft` |
| [Walle-Q](https://github.com/onebot-walle/nonebot_adapter_walleq) | `adapters.onebot12` |
| [Discord](https://github.com/nonebot/adapter-discord) | `adapters.discord` |
| [Red 协议](https://github.com/nonebot/adapter-red) | `adapters.red` |
| [Satori](https://github.com/nonebot/adapter-satori) | `adapters.satori` |
| [Dodo IM](https://github.com/nonebot/adapter-dodo) | `adapters.dodo` |
| [Kritor](https://github.com/nonebot/adapter-kritor) | `adapters.kritor` |
| [Tailchat](https://github.com/eya46/nonebot-adapter-tailchat) | `adapters.tailchat` |
| [Mail](https://github.com/mobyw/nonebot-adapter-mail) | `adapters.mail` |
| [微信公众号](https://github.com/YangRucheng/nonebot-adapter-wxmp) | `adapters.wxmp` |
| [黑盒语音](https://github.com/lclbm/adapter-heybox) | `adapters.heybox` |
| [Milky](https://github.com/nonebot/adapter-milky) | `adapters.milky` |
| [EFChat](https://github.com/molanp/nonebot_adapter_efchat) | `adapters.efchat` |

## 安装插件

在使用前请先安装 `nonebot-plugin-alconna` 插件至项目环境中，可参考[获取商店插件](https://nonebot.dev/docs/tutorial/store#安装插件)来了解并选择安装插件的方式。如：

使用 nb-cli：

```shell
nb plugin install nonebot-plugin-alconna
```

使用 pip：

```shell
pip install nonebot-plugin-alconna
```

使用 pdm：

```shell
pdm add nonebot-plugin-alconna
```

## 导入插件

由于 `nonebot-plugin-alconna` 作为插件，因此需要在使用前对其进行加载。使用 `require` 方法可轻松完成这一过程，可参考[跨插件访问](https://nonebot.dev/docs/advanced/requiring)一节进行了解。

```python
from nonebot import require

require("nonebot_plugin_alconna")

from nonebot_plugin_alconna import ...
```

## 使用插件

在前面的[深入指南](https://nonebot.dev/docs/appendices/session-control)中，我们已经得到了一个天气插件。现在我们将使用 `Alconna` 来改写这个插件。

### 使用 on_command 的写法

```python
from nonebot import on_command
from nonebot.rule import to_me
from nonebot.matcher import Matcher
from nonebot.adapters import Message
from nonebot.params import CommandArg, ArgPlainText

weather = on_command("天气", rule=to_me(), aliases={"weather", "天气预报"})

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

### 使用 on_alconna 的写法

```python
from nonebot.rule import to_me
from arclet.alconna import Alconna, Args
from nonebot_plugin_alconna import Match, on_alconna

weather = on_alconna(
    Alconna("天气", Args["location?", str]),
    aliases={"weather", "天气预报"},
    rule=to_me(),
)

@weather.handle()
async def handle_function(location: Match[str]):
    if location.available:
        weather.set_path_arg("location", location.result)

@weather.got_path("location", prompt="请输入地名")
async def got_location(location: str):
    if location not in ["北京", "上海", "广州", "深圳"]:
        await weather.reject(f"你想查询的城市 {location} 暂不支持，请重新输入！")
    await weather.finish(f"今天{location}的天气是...")
```

在上面的代码中，我们使用 `Alconna` 来解析命令，`on_alconna` 用来创建响应器，使用 `Match` 来获取解析结果。

## 更多内容

- [Alconna 本体](./alconna-command.md) - 命令解析器的完整指南
- [on_alconna 响应器](./alconna-matcher.md) - 响应器的使用方法
- [配置项](./alconna-config.md) - 所有配置项说明
- [通用消息组件](./alconna-uniseg.md) - 跨平台消息功能
- [通用消息段](./alconna-uniseg-segment.md) - 消息段模型定义
- [通用消息序列](./alconna-uniseg-message.md) - UniMessage 使用指南
- [辅助功能](./alconna-uniseg-utils.md) - Target、MsgId 等辅助工具
- [快捷方式声明](./alconna-shortcut.md) - funcommand 与 Command 构造器
- [内置组件](./alconna-builtins.md) - 内置插件与扩展

## 交流与反馈

- QQ 交流群: [链接](https://jq.qq.com/?_wv=1027&k=PUPOnCSH)
- 友链: [Alconna 文档](https://graiax.cn/guide/message_parser/alconna.html)
