# on_alconna 响应器

`nonebot_plugin_alconna` 插件本体的大部分功能都围绕着 `on_alconna` 响应器展开。

该响应器类似于 `on_command`，基于 `Alconna` 解析器来解析命令。

以下是一个完整的 `on_alconna` 响应器的例子：

```python
from nonebot_plugin_alconna import At, Image, Match, on_alconna
from arclet.alconna import Args, Option, Alconna, MultiVar, Subcommand

alc = Alconna(
    "role-group",
    Subcommand(
        "add|添加",
        Args["name", str],
        Option("member", Args["target", MultiVar(At)]),
        dest="add",
        compact=True,
    ),
    Option("list"),
    Option("icon", Args["icon", Image]),
)

rg = on_alconna(alc, use_command_start=True, aliases={"角色组"})

@rg.assign("list")
async def list_role_group():
    img: bytes = await gen_role_group_list_image()
    await rg.finish(Image(raw=img))

@rg.assign("add")
async def _(name: str, target: Match[tuple[At, ...]]):
    group = await create_role_group(name)
    if target.available:
        ats: tuple[At, ...] = target.result
        group.extend(member.target for member in ats)
    await rg.finish("添加成功")
```

## 声明

`on_alconna` 的参数如下：

```python
def on_alconna(
    command: Alconna | str,
    rule: Rule | T_RuleChecker | None = None,
    skip_for_unmatch: bool = True,
    auto_send_output: bool | None = None,
    aliases: set[str] | tuple[str, ...] | None = None,
    comp_config: CompConfig | None = None,
    extensions: list[type[Extension] | Extension] | None = None,
    exclude_ext: list[type[Extension] | str] | None = None,
    use_origin: bool | None = None,
    use_cmd_start: bool | None = None,
    use_cmd_sep: bool | None = None,
    response_self: bool | None = None,
    **kwargs: Any,
) -> type[AlconnaMatcher]:
    ...
```

| 参数 | 类型 | 说明 |
|---|---|---|
| `command` | `Alconna \| str` | Alconna 命令或字符串（字符串会自动转换） |
| `rule` | `Rule \| None` | 额外的响应规则 |
| `skip_for_unmatch` | `bool` | 是否在命令不匹配时跳过，默认 `True` |
| `auto_send_output` | `bool \| None` | 是否自动发送输出信息；`None` 跟随全局配置，默认 `True` |
| `aliases` | `set[str] \| None` | 命令别名集合 |
| `comp_config` | `CompConfig \| None` | 补全会话配置 |
| `extensions` | `list \| None` | 扩展列表 |
| `exclude_ext` | `list \| None` | 排除的扩展列表 |
| `use_origin` | `bool \| None` | 是否使用原始消息；`None` 跟随全局配置 |
| `use_cmd_start` | `bool \| None` | 是否使用 `COMMAND_START`；`None` 跟随全局配置 |
| `use_cmd_sep` | `bool \| None` | 是否使用 `COMMAND_SEP`；`None` 跟随全局配置 |
| `response_self` | `bool \| None` | 是否响应自身消息；`None` 跟随全局配置，默认 `False` |

`on_alconna` 返回的是 `Matcher` 的子类 `AlconnaMatcher`，其拓展了如下方法：

- `.got`、`send`、`reject` 等：拓展了 prompt 类型，支持使用 `UniMessage` 作为 prompt
- `.got_path(path, prompt, middleware)`：在 `got` 方法基础上，以 path 对应的参数为准，读取传入 message 的最后一个消息段并验证转换
- `.dispatch`：类似 `CommandGroup` 一样返回新的 `AlconnaMatcher`
- `.assign(path, value, or_not)`：用于对包含多个选项/子命令的命令的分派处理

除了标准的创建方式，本插件也提供了 `funcommand` 和 `Command` 两种快捷方式来创建 `AlconnaMatcher`，详见[快捷方式](./alconna-shortcut.md)。

## 依赖注入

`AlconnaMatcher` 的特性之一是拓展了依赖注入的功能。

### 注入模型

插件提供了几种用来处理解析结果的模型：

#### CommandResult

用于快捷访问命令解析结果：

| 属性 | 类型 | 说明 |
|---|---|---|
| `output` | `str \| None` | 命令的输出 |
| `context` | `dict` | 命令的上下文 |
| `matched` | `bool` | 是否匹配 |
| `source` | `Alconna` | 源命令 |
| `result` | `Arparma` | 解析结果 |

#### Match

匹配项，表示参数是否存在于 `Arparma.all_matched_args` 内：

- `Match.available`：判断是否匹配
- `Match.result`：获取匹配的值

> **注意**：`Match` 只能查找到 `Arparma.all_matched_args` 中的参数。对于特定选项/子命令的参数，需要使用 `Query` 来查询。

#### Query

查询项，表示参数是否可由 `Arparma.query` 查询并获得结果：

- `Query.available`：判断是否查询成功
- `Query.result`：获取查询结果
- `Query` 除了查询参数，也可以查询某个选项/子命令是否存在

### 编写示例

```python
async def handle(
    result: CommandResult,
    arp: Arparma,
    dup: Duplication,
    source: Alconna,
    ext: Extension,
    exts: SelectedExtensions,
    abc: str,
    foo: Match[str],
    bar: Query[int] = Query("ttt.bar", 0),
):
    ...
```

`AlconnaMatcher` 的依赖注入拓展支持以下情况：

- `xxx: type`：若 type 为 `Alconna`、`Arparma`、`Duplication`、`CommandResult`、`Extension`、`SelectedExtensions` 等特殊类型，则会注入对应的对象
- `xxx: Match[T]`：注入 `Match` 对象，从 `all_matched_args` 中查找名为 `xxx` 的参数
- `xxx: Query[T] = Query("path", default)`：注入 `Query` 对象，查询指定路径的参数
- `xxx: T`：若 `xxx` 在 `all_matched_args` 中，则直接注入其值

### 完整示例

```python
from nonebot import require

require("nonebot_plugin_alconna")

from nonebot_plugin_alconna import AlconnaQuery, AlcResult, Match, Query, on_alconna
from arclet.alconna import Alconna, Args, Option, Arparma

test = on_alconna(
    Alconna(
        "test",
        Option("foo", Args["bar", int]),
        Option("baz", Args["qux", bool, False]),
    )
)

@test.handle()
async def handle_test1(result: AlcResult):
    await test.send(f"matched: {result.matched}")
    await test.send(f"maybe output: {result.output}")

@test.handle()
async def handle_test2(result: Arparma):
    await test.send(f"head result: {result.header_result}")
    await test.send(f"args: {result.all_matched_args}")

@test.handle()
async def handle_test3(bar: Match[int]):
    if bar.available:
        await test.send(f"foo={bar.result}")

@test.handle()
async def handle_test4(qux: Query[bool] = AlconnaQuery("baz.qux", False)):
    if qux.available:
        await test.send(f"baz.qux={qux.result}")
```

## assign() 分派

`assign` 用于对包含多个选项/子命令的命令进行条件分派处理：

```python
def assign(
    cls,
    path: str,
    value: Any = _seminal,
    or_not: bool = False,
    additional: CHECK | None = None,
    parameterless: Iterable[Any] | None = None,
):
    ...
```

| 参数 | 说明 |
|---|---|
| `path` | 分派路径 |
| `value` | 期望的值（可选） |
| `or_not` | 取反条件 |
| `additional` | 额外的检查函数 |
| `parameterless` | 无参数的依赖注入 |

### 特殊路径

- `$main`：处理没有任何选项/子命令匹配的情况
- `~XX`：在 `dispatch` 后的相对路径

```python
# 处理没有任何选项/子命令匹配的情况
@rg.assign("$main")
async def handle_main(): ...

# 处理 list 选项
@rg.assign("list")
async def handle_list(): ...

# 处理 add 选项，且 name 为 admin
@rg.assign("add.name", "admin")
async def handle_add_admin(): ...
```

## dispatch() 分派

`dispatch` 类似于 `CommandGroup`，返回一个新的 `AlconnaMatcher`：

```python
rg_list_cmd = rg.dispatch("list")

@rg_list_cmd.handle()
async def handle_list(): ...
```

在 `dispatch` 后可以使用 `~` 前缀表示相对路径：

```python
rg_add_cmd = rg.dispatch("add")

# 此时 ~name 表示 add.name
@rg_add_cmd.assign("~name", "admin")
async def handle_add_admin(): ...
```

```python
@rg_add_cmd.assign("~name", "admin")
async def handle_add_admin(target: Query[tuple[At, ...]] = Query("~target")):
    if target.available:
        await rg.send(f"添加成功: {target.result}")
```

## got_path()

`got_path` 在 `got` 方法的基础上，会以 path 对应的参数为准，读取传入 message 的最后一个消息段并验证转换：

```python
from nonebot_plugin_alconna import At, Match, UniMessage, on_alconna

test_cmd = on_alconna(Alconna("test", Args["target?", Union[str, At]]))

@test_cmd.handle()
async def tt_h(target: Match[Union[str, At]]):
    if target.available:
        test_cmd.set_path_arg("target", target.result)

@test_cmd.got_path("target", prompt="请输入目标")
async def tt(target: Union[str, At]):
    await test_cmd.send(UniMessage(["ok\n", target]))
```

## prompt()

`prompt` 方法用于等待用户输入，类似于 Waiter 模式：

```python
from nonebot_plugin_alconna import At, Match, UniMessage, on_alconna

test_cmd = on_alconna(Alconna("test", Args["target?", Union[str, At]]))

@test_cmd.handle()
async def tt_h(target: Match[Union[str, At]]):
    if target.available:
        await test_cmd.finish(UniMessage(["ok\n", target]))
    resp = await test_cmd.prompt("请输入目标", timeout=30)  # 等待 30 秒
    if resp is None:
        await test_cmd.finish("超时")
    await test_cmd.finish(UniMessage(["ok\n", resp[-1]]))
```

## 返回值中间件 (image_fetch)

`image_fetch` 是一个内置的返回值中间件，用于将 `Image` 消息段自动转换为 `bytes`：

```python
from nonebot_plugin_alconna import image_fetch

mask_cmd = on_alconna(Alconna("search", Args["img?", Image]))

@mask_cmd.handle()
async def mask_h(matcher: AlconnaMatcher, img: Match[bytes] = AlconnaMatch("img", image_fetch)):
    result = await search_img(img.result)
    await matcher.send(result.content)
```

## i18n 支持

插件支持 i18n 国际化消息：

```yaml
# 中文语言文件
demo:
  command:
    role-group:
      add: 添加 {name} 成功!
```

```python
@rg.assign("add")
async def handle_add(name: str):
    await rg.i18n("demo", "command.role-group.add", name=name).finish()
```

## Matcher 测试

`AlconnaMatcher` 提供了 `.test()` 方法用于测试命令匹配：

```python
def test(
    cls,
    message: str | UniMessage,
    expected: dict[str, Any] | None = None,
    prefix: bool = True,
): ...
```

## Extension 扩展系统

`Extension` 是一种扩展机制，允许你自定义命令的解析行为。

### 全局扩展

```python
from nonebot_plugin_alconna import add_global_extension
from nonebot_plugin_alconna.builtins.extensions.telegram import TelegramSlashExtension

add_global_extension(TelegramSlashExtension)
```

### 自定义扩展

```python
from nonebot_plugin_alconna import Extension, Alconna, on_alconna, Interface

class LLMExtension(Extension):
    @property
    def priority(self) -> int:
        return 10

    @property
    def id(self) -> str:
        return "LLMExtension"

    def __init__(self, llm):
        self.llm = llm

    def post_init(self, alc: Alconna) -> None:
        self.llm.add_context(alc.command, alc.meta.description)

    async def receive_wrapper(self, bot, event, receive):
        resp = await self.llm.input(str(receive))
        return receive.__class__(resp.content)

    def before_catch(self, name, annotation, default):
        return name == "llm"

    def catch(self, interface: Interface):
        if interface.name == "llm":
            return self.llm

matcher = on_alconna(
    Alconna(...),
    extensions=[LLMExtension(LLM)],
)
```

### Extension 方法一览

#### validate

验证当前 bot 和 event 是否可被该扩展处理：

```python
def validate(self, bot: Bot, event: Event) -> bool: ...
```

#### output_converter

将输出内容转换为 UniMessage：

```python
async def output_converter(self, output_type: OutputType, content: str) -> UniMessage: ...
```

#### message_provider

提供用于解析的消息：

```python
async def message_provider(
    self, event: Event, state: T_State, bot: Bot, use_origin: bool = False
) -> UniMessage | None: ...
```

#### receive_provider

处理接收到的消息：

```python
async def receive_provider(
    self, bot: Bot, event: Event, command: Alconna, receive: UniMessage
) -> UniMessage: ...
```

#### context_provider

提供上下文变量：

```python
async def context_provider(
    self, ctx: dict[str, Any], bot: Bot, event: Event, state: T_State
) -> dict[str, Any]: ...
```

#### permission_check

权限检查：

```python
async def permission_check(self, bot: Bot, event: Event, command: Alconna) -> bool: ...
```

#### parse_wrapper

解析结果后处理：

```python
async def parse_wrapper(self, bot: Bot, state: T_State, event: Event, res: Arparma) -> None: ...
```

#### send_wrapper

发送消息前处理：

```python
async def send_wrapper(self, bot: Bot, event: Event, send: TMessage) -> TMessage: ...
```

#### before_catch

判断是否捕获某个依赖注入参数：

```python
def before_catch(self, name: str, annotation: type, default: Any) -> bool: ...
```

#### catch

捕获并返回依赖注入的值：

```python
async def catch(self, interface: Interface) -> Any: ...
```

#### Interface 对象

```python
class Interface(Generic[TE]):
    event: TE
    state: T_State
    name: str
    annotation: Any
    default: Any
```

## 补全会话 (CompConfig)

补全会话允许在命令输入不完整时引导用户补全参数：

```python
from nonebot_plugin_alconna import Alconna, Args, Field, At, on_alconna

alc = Alconna(
    "添加教师",
    Args["name", str, Field(completion=lambda: "请输入姓名")],
    Args["phone", int, Field(completion=lambda: "请输入手机号")],
    Args["at", [str, At], Field(completion=lambda: "请输入教师号")],
)

cmd = on_alconna(alc, comp_config={"lite": True}, skip_for_unmatch=False)

@cmd.handle()
async def handle(result: Arparma):
    cmd.finish("添加成功")
```

### CompConfig 定义

```python
class CompConfig(TypedDict):
    tab: NotRequired[str]           # 用于切换提示的指令的名称
    enter: NotRequired[str]         # 用于输入提示的指令的名称
    exit: NotRequired[str]          # 用于退出会话的指令的名称
    timeout: NotRequired[int]       # 超时时间
    hide_tabs: NotRequired[bool]    # 是否隐藏所有提示
    hides: NotRequired[Set[Literal["tab", "enter", "exit"]]]      # 隐藏的指令
    disables: NotRequired[Set[Literal["tab", "enter", "exit"]]]   # 禁用的指令
    lite: NotRequired[bool]         # 是否使用简洁版本（同时配置 disables、hides、hide_tabs）
    block: NotRequired[bool]        # 进行补全会话时是否阻塞响应器
```

## 完整示例：角色组管理

```python
from nonebot_plugin_alconna import At, Image, Match, on_alconna
from arclet.alconna import Args, Option, Alconna, MultiVar, Subcommand

alc = Alconna(
    "role-group",
    Subcommand(
        "add|添加",
        Args["name", str],
        Option("member", Args["target", MultiVar(At)]),
        dest="add",
        compact=True,
    ),
    Option("list"),
    Option("icon", Args["icon", Image]),
)

rg = on_alconna(alc, use_command_start=True, aliases={"角色组"})

@rg.assign("list")
async def list_role_group():
    img: bytes = await gen_role_group_list_image()
    await rg.finish(Image(raw=img))

@rg.assign("add")
async def _(name: str, target: Match[tuple[At, ...]]):
    group = await create_role_group(name)
    if target.available:
        ats: tuple[At, ...] = target.result
        group.extend(member.target for member in ats)
    await rg.finish("添加成功")
```
