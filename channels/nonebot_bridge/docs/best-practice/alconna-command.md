# Alconna 本体

[Alconna](https://github.com/ArcletProject/Alconna) 隶属于 `ArcletProject`，是一个简单、灵活、高效的命令参数解析器，并且不局限于解析命令式字符串。

我们先通过一个例子来讲解 Alconna 的核心 ——`Args`、`Subcommand`、`Option`：

```python
from arclet.alconna import Alconna, Args, Subcommand, Option

alc = Alconna(
    "pip",
    Subcommand(
        "install",
        Args["package", str],
        Option("-r|--requirement", Args["file", str]),
        Option("-i|--index-url", Args["url", str]),
    )
)

res = alc.parse("pip install nonebot2 -i URL")
print(res)
# matched=True, header_match=(origin='pip' result='pip' matched=True groups={}),
# subcommands={'install': (value=Ellipsis args={'package': 'nonebot2'}
# options={'index-url': (value=None args={'url': 'URL'})} subcommands={})},
# other_args={'package': 'nonebot2', 'url': 'URL'}

print(res.all_matched_args)
# {'package': 'nonebot2', 'url': 'URL'}
```

这段代码通过 `Alconna` 创建了一个接受主命令名为 `pip`，子命令为 `install` 且子命令接受一个 Args 参数 `package` 和两个 Option 参数 `-r` 和 `-i` 的命令参数解析器，通过 `parse` 方法返回解析结果 `Arparma` 的实例。

## 命令头

命令头是指命令的前缀 (Prefix) 与命令名 (Command) 的组合，例如 `!help` 中的 `!` 与 `help`。

命令构造时，`Alconna([prefix], command)` 与 `Alconna(command, [prefix])` 是等价的。

| 前缀 | 命令名 | 匹配内容 | 说明 |
|---|---|---|---|
| 不传入 | `"foo"` | `"foo"` | 无前缀的纯文字头 |
| 不传入 | `123` | `123` | 无前缀的元素头 |
| 不传入 | `"re:\d2"` | `"32"` | 无前缀的正则头 |
| 不传入 | `int` | `123` 或 `"456"` | 无前缀的类型头 |
| `[int, bool]` | 不传入 | `True` 或 `123` | 无名的元素类头 |
| `["foo", "bar"]` | 不传入 | `"foo"` 或 `"bar"` | 无名的纯文字头 |
| `["foo", "bar"]` | `"baz"` | `"foobaz"` 或 `"barbaz"` | 纯文字头 |
| `[int, bool]` | `"foo"` | `[123, "foo"]` 或 `[False, "foo"]` | 类型头 |
| `[123, 4567]` | `"foo"` | `[123, "foo"]` 或 `[4567, "foo"]` | 元素头 |
| `[nepattern.NUMBER]` | `"bar"` | `[123, "bar"]` 或 `[123.456, "bar"]` | 表达式头 |
| `[123, "foo"]` | `"bar"` | `[123, "bar"]` 或 `"foobar"` 或 `["foo", "bar"]` | 混合头 |
| `[(int, "foo"), (456, "bar")]` | `"baz"` | `[123, "foobaz"]` 或 `[456, "foobaz"]` 或 `[456, "barbaz"]` | 对头 |

对于无前缀的类型头，此时会将传入的值尝试转为 `BasePattern`，例如 `int` 会转为 `nepattern.INTEGER`。如此该命令头会匹配对应的类型，例如 `int` 会匹配 `123` 或 `"456"`，但不会匹配 `"foo"`。解析后，Alconna 会将命令头匹配到的值转为对应的类型，例如 `int` 会将 `"123"` 转为 `123`。

> **提示**：正则内容只在命令名上生效，前缀中的正则会被转义。

### Bracket Header

除了通过传入 `re:xxx` 来使用正则表达式外，Alconna 还提供了一种更加简洁的方式来使用正则表达式，称为 Bracket Header：

```python
alc = Alconna(".rd{roll:int}")
assert alc.parse(".rd123").header["roll"] == 123
```

Bracket Header 类似 Python 里的 f-string 写法，通过 `"{}"` 声明匹配类型。

`"{}"` 中的内容为 `"name:type or pat"`：

- `"{foo:int}"` ⇔ `"(?P<foo>\d+)"`，其中 `"int"` 部分若能转为 `BasePattern` 则读取里面的表达式
- `"{:\d+}"` ⇔ `"(\d+)"`
- `"{foo}"` ⇔ `"(?P<foo>.+)"`
- `"{}"`、`"{:}"` ⇔ `"(.+)"`，占位符

## 参数声明 (Args)

`Args` 是用于声明命令参数的组件，可以通过以下几种方式构造 Args：

- `Args.key[var, default]`
- `Args[(key, var, default)]`
- `Args[key, var, default][key1, var1, default1][...]`

其中，`key` 一定是字符串，而 `var` 一般为参数的类型，`default` 为具体的值或者 `arclet.alconna.args.Field`。

其与函数签名类似，但是允许含有默认值的参数在前；同时支持 keyword-only 参数不依照构造顺序传入（但是仍需要在非 keyword-only 参数之后）。

### key

`key` 的作用是用以标记解析出来的参数并存放于 `Arparma` 中，以方便用户调用。

其有三种为 Args 注解的标识符：`?`、`/`、`!`，标识符与 key 之间建议以 `;` 分隔：

- `/` 标识符表示该参数的类型注解需要隐藏。
- `?` 标识符表示该参数为可选参数，会在无参数匹配时跳过。
- `!` 标识符表示该处传入的参数应不是规定的类型，或不在指定的值中。

另外，对于参数的注释也可以标记在 `key` 中，其与 key 或者标识符以 `#` 分割：`foo#这是注释;?` 或 `foo?#这是注释`。

> **提示**：`Args` 中的 `key` 在实际命令中并不需要传入（keyword 参数除外）：

```python
from arclet.alconna import Alconna, Args

alc = Alconna("test", Args["foo", str])
alc.parse("test --foo abc")  # 错误
alc.parse("test abc")  # 正确
```

若需要 `test --foo abc`，你应该使用 `Option`：

```python
from arclet.alconna import Alconna, Args, Option

alc = Alconna("test", Option("--foo", Args["foo", str]))
```

### var

`var` 负责命令参数的类型检查与类型转化。

`Args` 的 `var` 表面上看需要传入一个 `type`，但实际上它需要的是一个 `nepattern.BasePattern` 的实例：

```python
from arclet.alconna import Args
from nepattern import BasePattern

# 表示 foo 参数需要匹配一个 @number 样式的字符串
args = Args["foo", BasePattern("@\d+")]
```

`pip` 示例中可以传入 `str` 是因为 `str` 已经注册在了 `nepattern.global_patterns` 中，因此会替换为 `nepattern.global_patterns[str]`。

`nepattern.global_patterns` 默认支持的类型有：

| 类型 | 说明 |
|---|---|
| `str` | 匹配任意字符串 |
| `int` | 匹配整数 |
| `float` | 匹配浮点数 |
| `bool` | 匹配 `True` 与 `False` 以及它们的小写形式 |
| `hex` | 匹配 `0x` 开头的十六进制字符串 |
| `url` | 匹配网址 |
| `email` | 匹配 `xxxx@xxx` 的字符串 |
| `ipv4` | 匹配 `xxx.xxx.xxx.xxx` 的字符串 |
| `list` | 匹配类似 `["foo","bar","baz"]` 的字符串 |
| `dict` | 匹配类似 `{"foo":"bar","baz":"qux"}` 的字符串 |
| `datetime` | 传入一个 `datetime` 支持的格式字符串，或时间戳 |
| `Any` | 匹配任意类型 |
| `AnyString` | 匹配任意类型，转为 `str` |
| `Number` | 匹配 `int` 与 `float`，转为 `int` |

同时可以使用 `typing` 中的类型：

- `Dict[X, Y]`：匹配一个字典，其中的 key 为 `X` 类型，value 为 `Y` 类型
- `List[X]`：匹配一个列表，其中的元素为 `X` 类型
- `Optional[xxx]`：会自动将默认值设为 `None`，并在解析失败时使用默认值
- `Union[X, Y]`：匹配其中的任意一个类型
- `Literal[X]`：匹配其中的任意一个值

#### 特殊传入标记

- `{foo: bar, baz: qux}`：匹配字典中的任意一个键，并返回对应的值（特殊的键 `...` 会匹配任意的值）
- `"rep:xxx"`：匹配一个正则表达式 `xxx`，会返回 `re.Match` 对象
- `"re:xxx"`：匹配一个正则表达式 `xxx`，会返回 `Match[0]`
- `Callable[[X], Y]`：匹配一个参数为 `X` 类型的值，并返回通过该函数调用得到的 `Y` 类型的值
- `[foo, bar, Baz, ...]`：匹配其中的任意一个值或类型
- `"foo|bar|baz"`：匹配 `"foo"` 或 `"bar"` 或 `"baz"`
- `RawStr("foo")`：匹配字符串 `"foo"`（即使有 `BasePattern` 与之关联也不会被替换）
- `"foo"`：匹配字符串 `"foo"`（若没有某个 `BasePattern` 与之关联）

特别的，你可以不传入 `var`，此时会使用 `key` 作为 `var`，匹配 `key` 字符串。

### MultiVar 与 KeyWordVar

`MultiVar` 是一个特殊的标注，用于告知解析器该参数可以接受多个值，类似于函数中的 `*args`，其构造方法形如 `MultiVar(str)`。

同样的还有 `KeyWordVar`，类似于函数中的 `*, name: type`，其构造方法形如 `KeyWordVar(str)`，用于告知解析器该参数为一个 keyword-only 参数。

> **提示**：
> - `MultiVar` 与 `KeyWordVar` 组合时，代表该参数为一个可接受多个 key-value 的参数，类似于函数中的 `**kwargs`，其构造方法形如 `MultiVar(KeyWordVar(str))`
> - `MultiVar` 与 `KeyWordVar` 也可以传入 `default` 参数，用于指定默认值
> - `MultiVar` 不能在 `KeyWordVar` 之后传入

### AllParam

`AllParam` 是一个特殊的标注，用于告知解析器该参数接收命令中在此位置之后的所有参数并结束解析，可以认为是泛匹配参数。

`AllParam` 可直接使用 (`Args["xxx", AllParam]`)，也可以传入指定的接收类型 (`Args["xxx", AllParam(str)]`)。

> **提示**：在 `nonebot_plugin_alconna` 下，`AllParam` 的返回值为 [UniMessage](./alconna-uniseg-message.md)

### default

`default` 传入的是该参数的默认值或者 `Field`，以携带对于该参数的更多信息。

默认情况下（即不声明）`default` 的值为特殊值 `Empty`。这也意味着你可以将默认值设置为 `None` 表示默认值为空值。

`Field` 构造需要的参数说明如下：

| 参数 | 说明 |
|---|---|
| `missing_tips` | 参数单元的缺失提示生成函数 |
| `unmatch_tips` | 参数单元的错误提示生成函数，其接收一个表示匹配失败的元素的参数 |
| `completion` | 参数单元的补全说明生成函数 |
| `alias` | 参数单元默认值的别名 |
| `default` | 参数单元的默认值 |

## 选项与子命令 (Option & Subcommand)

`Option` 和 `Subcommand` 可以传入一组 `alias`，如 `Option("--foo|-F|--FOO|-f")`，`Subcommand("foo", alias=["F"])`。

传入别名后，选项与子命令会选择其中长度最长的作为其名称。若传入为 `"--foo|-f"`，则命令名称为 `"--foo"`。

> **注意**：Option 的名字或别名没有要求必须在前面写上 `-`。

Option 与 Subcommand 的唯一区别在于 **Subcommand 可以传入自己的 Option 与 Subcommand**。

它们拥有如下共同参数：

### requires

一段指定顺序的字符串列表，作为唯一的前置序列与命令嵌套替换。对于命令 `test foo bar baz qux <a:int>`：

```python
Alconna("test", Option("qux", Args["a", int], requires=["foo", "bar", "baz"]))
```

### dest

选项或子命令的目标名称，会在解析结果 `Arparma` 中使用该名称来代替实际名称。

### default

选项或子命令的默认值：

```python
from arclet.alconna import Option, OptionResult

opt1 = Option("--foo", default=False)
opt2 = Option("--foo", default=OptionResult(value=False, args={"bar": 1}))
```

### help_text

选项或子命令的帮助文本。

### Action

`Option` 可以设置 `action` 来指定解析时的行为：

- `store`（默认）：存储解析结果
- `append`：将解析结果追加到列表中
- `count`：计数模式

```python
from arclet.alconna import Alconna, Option, Args, append

alc = Alconna(
    "gcc",
    Option("--flag|-F", Args["content", str], action=append, compact=True),
)
print(alc.parse("gcc -Fabc -Fdef -Fxyz").query[list]("flag.content"))
# ['abc', 'def', 'xyz']
```

```python
from arclet.alconna import Alconna, Option, count

alc = Alconna("pp", Option("--verbose|-v", action=count, default=0))
print(alc.parse("pp -vvv").query[int]("verbose.value"))
# 3
```

## Arparma

`Arparma` 是解析结果的模型，可以通过 `alc.parse(...)` 获得。

主要属性：

- `matched`：是否匹配成功
- `header_match`：命令头匹配结果
- `all_matched_args`：所有匹配到的参数
- `query(path, default)`：路径查询

### 路径查询语法

`Arparma.query` 支持路径查询，如：

- `"foo"`：查询参数 `foo`
- `"install.package"`：查询子命令 `install` 的参数 `package`
- `"flag.content"`：查询选项 `flag` 的参数 `content`
- `"verbose.value"`：查询选项 `verbose` 的值

可以通过泛型来指定返回类型：

```python
res.query[int]("verbose.value")
res.query[str]("install.package")
```

## CommandMeta

`CommandMeta` 用于提供命令的元数据信息：

```python
from arclet.alconna import Alconna, CommandMeta

alc = Alconna(
    ...,
    meta=CommandMeta(
        description="命令描述",
        usage="使用方法",
        example="示例",
        fuzzy_match=False,
        raise_exception=False,
        hide=False,
        compact=False,
        context_style=None,
        extra={}
    )
)
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `description` | `str` | 命令的描述 |
| `usage` | `str \| None` | 命令的使用方法 |
| `example` | `str \| None` | 命令的使用示例 |
| `fuzzy_match` | `bool` | 是否启用模糊匹配 |
| `raise_exception` | `bool` | 是否抛出异常 |
| `hide` | `bool` | 是否隐藏该命令 |
| `compact` | `bool` | 是否紧凑模式（命令名后不需要空格） |
| `context_style` | `str \| None` | 上下文插值风格 |
| `extra` | `dict` | 额外信息 |

## 命名空间 (Namespace)

命名空间配置用于管理一组命令的共同设置：

```python
from arclet.alconna import Alconna, namespace, Namespace, Subcommand, Args, config

ns = Namespace("foo", prefixes=["/"])

alc = Alconna(
    "pip",
    Subcommand("install", Args["package", str]),
    namespace=ns,
)

# 使用上下文管理器方式
with namespace("bar") as np1:
    np1.prefixes = ["!"]
    np1.builtin_option_name["help"] = {"帮助", "-h"}

# 使用 config 管理命名空间
config.namespaces["foo"] = ns
config.default_namespace.prefixes = [...]
```

可通过多种方式修改默认命名空间：

```python
from arclet.alconna import config, namespace, Namespace

config.default_namespace.prefixes = [...]  # 直接修改默认配置

np = Namespace("xxx", prefixes=[...])
config.default_namespace = np  # 更换默认的命名空间

with namespace(config.default_namespace.name) as np:
    np.prefixes = [...]
```

## 快捷指令 (Shortcuts)

快捷指令允许为命令创建别名或简写。其参数类型为 `ShortcutArgs`：

```python
class ShortcutArgs(TypedDict):
    command: NotRequired[str]       # 快捷指令的命令
    args: NotRequired[list[Any]]    # 快捷指令的附带参数
    fuzzy: NotRequired[bool]        # 是否允许命令后随参数
    prefix: NotRequired[bool]       # 是否调用时保留指令前缀
    wrapper: NotRequired[ShortcutRegWrapper]  # 正则匹配结果的额外处理函数
    humanized: NotRequired[str]     # 快捷指令的人类可读描述
```

### 使用 args 参数

通过正则表达式捕获组 `{0}`、`{1}` 等替换参数：

```python
from arclet.alconna import Alconna, Args

alc = Alconna("setu", Args["count", int])
alc.shortcut("涩图(\d+)张", {"args": ["{0}"]})

alc.parse("涩图3张").query("count")
# 3
```

### 使用 command 参数

`{*}` 表示将所有剩余参数传入：

```python
from arclet.alconna import Alconna, Args

alc = Alconna("eval", Args["content", str])
alc.shortcut("echo", {"command": "eval print(\\'{*}\\')"})

alc.parse("echo hello world!")
# hello world!
```

### 管理快捷指令

```python
alc.shortcut("echo", delete=True)  # 删除快捷指令

alc.parse("eval --shortcut list")  # 列出所有快捷指令
# 'echo'
```

### 占位符说明

| 占位符 | 说明 |
|---|---|
| `{X}` | 正则捕获组的第 X 个结果 |
| `{%X}` | 正则命名捕获组 X |
| `{*}` | 匹配剩余所有内容 |

## 紧凑命令 (Compact)

通过 `CommandMeta(compact=True)` 或 `Option(..., compact=True)` 开启紧凑模式，使命令名后不需要空格：

```python
from arclet.alconna import Alconna, Option, CommandMeta, Args

alc = Alconna(
    "test",
    Args["foo", int],
    Option("BAR", Args["baz", str], compact=True),
    meta=CommandMeta(compact=True),
)

assert alc.parse("test123 BARabc").matched
```

## 模糊匹配

通过 `CommandMeta(fuzzy_match=True)` 开启模糊匹配：

```python
from arclet.alconna import Alconna, CommandMeta

alc = Alconna("test_fuzzy", meta=CommandMeta(fuzzy_match=True))
alc.parse("test_fuzy")
# test_fuzy is not matched. Do you mean "test_fuzzy"?
```

## 自动补全

通过 `--comp` 选项触发自动补全：

```python
from arclet.alconna import Alconna, Args, Option

alc = Alconna("test", Args["abc", int]) + Option("foo") + Option("bar")
alc.parse("test --comp")
# 以下是建议的输入：
# * <abc: int>
# * --help
# * -h
# * -sct
# * --shortcut
# * foo
# * bar
```

## Duplication

`Duplication` 用于提供更具类型提示的解析结果封装：

```python
from arclet.alconna import Alconna, Args, Option, OptionResult, Duplication, SubcommandStub, Subcommand, count

class MyDup(Duplication):
    verbose: OptionResult
    install: SubcommandStub

alc = Alconna(
    "pip",
    Subcommand(
        "install",
        Args["package", str],
        Option("-r|--requirement", Args["file", str]),
        Option("-i|--index-url", Args["url", str]),
    ),
    Option("-v|--version"),
    Option("-v|--verbose", action=count),
)

result = alc.parse("pip -v install ...", duplication=MyDup)
print(result.install)
# SubcommandStub(...)
```

简化版的 Duplication，直接声明目标参数：

```python
from typing import Optional
from arclet.alconna import Duplication

class MyDup(Duplication):
    package: str
    file: Optional[str] = None
    url: Optional[str] = None
```

## 上下文插值

通过 `context_style` 配置，支持在命令解析中使用上下文变量：

```python
from arclet.alconna import Alconna, Args, CommandMeta

alc = Alconna(
    "test",
    Args["foo", int],
    meta=CommandMeta(context_style="parentheses"),
)

alc.parse("test $(bar)", {"bar": 123})
# {"foo": 123}
```

支持两种风格：

- `"bracket"`：使用 `{...}` 语法
- `"parentheses"`：使用 `$(...)` 语法
