# 快捷方式声明

针对 `Alconna` 编写对于入门开发者来说较为复杂的问题，本插件提供了一些快捷方式来简化开发者的工作。

## 装饰器构造器 (funcommand)

本插件提供了一个 `funcommand` 装饰器，其用于将一个接受任意参数、返回 `str` 或 `Message` 或 `MessageSegment` 的函数转换为命令响应器：

```python
from nonebot_plugin_alconna import funcommand

@funcommand()
async def echo(msg: str):
    return msg
```

其等同于：

```python
from arclet.alconna import Alconna, Args
from nonebot_plugin_alconna import on_alconna, AlconnaMatch, Match

echo = on_alconna(Alconna("echo", Args["msg", str]))

@echo.handle()
async def echo_exit(msg: Match[str] = AlconnaMatch("msg")):
    await echo.finish(msg.result)
```

### funcommand 额外参数

相比于 `on_alconna`，`funcommand` 增加了三个参数：

| 参数 | 说明 |
|---|---|
| `name` | 命令名称，默认为函数名 |
| `prefixes` | 命令前缀列表 |
| `description` | 命令描述 |

## 类 Koishi 构造器 (Command)

本插件提供了一个 `Command` 构造器，其基于 `arclet.alconna.tools` 中的 `AlconnaString`，以类似 [Koishi](https://koishi.chat/zh-CN/guide/basic/command.html) 中注册命令的方式来构建一个 `AlconnaMatcher`：

```python
from nonebot_plugin_alconna import Command, Arparma

book = (
    Command("book", "测试")
    .option("writer", "-w <id:int>")
    .option("writer", "--anonymous", {"id": 0})
    .usage("book [-w <id:int> | --anonymous]")
    .shortcut("测试", {"args": ["--anonymous"]})
    .build()
)

@book.handle()
async def _(arp: Arparma):
    await book.send(str(arp.options))
```

甚至，你可以设置 `action` 来设定响应行为：

```python
book = (
    Command("book", "测试")
    .option("writer", "-w <id:int>")
    .option("writer", "--anonymous", {"id": 0})
    .usage("book [-w <id:int> | --anonymous]")
    .shortcut("测试", {"args": ["--anonymous"]})
    .action(lambda options: str(options))  # 会自动通过 bot.send 发送
    .build()
)
```

### 参数类型

`Command` 的参数类型也如 Koishi 一样，**必选参数**用尖括号包裹，**可选参数**用方括号包裹：

| 语法 | 说明 |
|---|---|
| `<foo:int>` | 必选参数 `foo`，类型为 `int` |
| `[foo:int]` | 可选参数 `foo`，类型为 `int` |
| `<foo:int=1>` | 必选参数 `foo`，类型为 `int`，默认值为 `1` |
| `<foo>` | 必选参数 `foo`，类型为 `Any` |
| `<foo:str+>` / `<foo:str*>` | [变长参数](./alconna-command.md#multivar-与-keywordvar) `foo`，类型为 `str` |
| `<foo:+str>` / `<foo:text>` | 参数 `foo`，类型为 `str`，将变长参数结果用空格合并 |
| `<...foo>` | [泛匹配参数](./alconna-command.md#allparam) |

### 针对通用消息段的拓展类型

| 语法 | 说明 |
|---|---|
| `<foo:At>` | 类型为 `At` [通用消息段](./alconna-uniseg-segment.md) |
| `<foo:Image>` | 类型为 `Image` 通用消息段 |
| `<foo:select(Image).first>` | 获取子元素类型，选取第一个 `Image` |
| `<foo:Dot(Image, 'url')>` | 类型为 `Image`，并且只获取 `url` 属性 |

## 从文件加载

`Command` 支持读取 `json` 或 `yaml` 文件来加载命令。

### YAML 示例

`book.yml`：

```yaml
command: book
help: 测试
options:
  - name: writer
    opt: "-w <id:int>"
  - name: writer
    opt: "--anonymous"
    default:
      id: 1
usage: book [-w <id:int> | --anonymous]
shortcuts:
  - key: 测试
    args: ["--anonymous"]
actions:
  - params: ["options"]
    code: |
      return str(options)
```

加载：

```python
from nonebot_plugin_alconna import command_from_yaml

book = command_from_yaml("book.yml")
```

### JSON 示例

`book.json`：

```json
{
  "command": "book",
  "help": "测试",
  "options": [
    {
      "name": "writer",
      "opt": "-w <id:int>"
    },
    {
      "name": "writer",
      "opt": "--anonymous",
      "default": {
        "id": 1
      }
    }
  ],
  "usage": "book [-w <id:int> | --anonymous]",
  "shortcuts": [
    {
      "key": "测试",
      "args": ["--anonymous"]
    }
  ],
  "actions": [
    {
      "params": ["options"],
      "code": "return str(options)"
    }
  ]
}
```

加载：

```python
from nonebot_plugin_alconna import command_from_json

book = command_from_json("book.json")
```
