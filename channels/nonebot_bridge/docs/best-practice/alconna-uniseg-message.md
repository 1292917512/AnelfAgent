# 通用消息序列

`uniseg` 提供了一个类似于 `Message` 的 `UniMessage` 类型，其元素为[通用消息段](./alconna-uniseg-segment.md)。

## 获取 UniMessage

你可以用如下方式获取 `UniMessage`：

### 使用 UniMessage.generate

从适配器消息生成通用消息。

### 使用依赖注入

通过提供的 `UniversalMessage` 或基于 Annotated 支持的 `UniMsg` 依赖注入器来获取 `UniMessage`：

```python
from nonebot_plugin_alconna.uniseg import UniMsg, At, Text

matcher = on_xxx(...)

@matcher.handle()
async def _(msg: UniMsg):
    text = msg[Text, 0]
    print(text.text)
    if msg.has(At):
        ats = msg.get(At)
        print(ats)
    ...
```

## 发送消息

你还可以通过 `UniMessage` 的 `export` 与 `send` 方法来跨平台发送消息。

### export

`UniMessage.export` 会通过传入的 `bot: Bot` 参数，或上下文中的 `Bot` 对象读取适配器信息，并使用对应的生成方法把通用消息转为适配器对应的消息序列：

```python
from nonebot import Bot, on_command
from nonebot_plugin_alconna.uniseg import Image, UniMessage

test = on_command("test")

@test.handle()
async def handle_test():
    await test.send(await UniMessage(Image(path="path/to/img")).export())
```

### send

`UniMessage.send` 基于 `UniMessage.export` 并调用各适配器下的发送消息方法，返回一个 `Receipt` 对象：

```python
from nonebot import Bot, on_command
from nonebot_plugin_alconna.uniseg import UniMessage

test = on_command("test")

@test.handle()
async def handle():
    receipt = await UniMessage.text("hello!").send(at_sender=True, reply_to=True)
    await receipt.recall(delay=1)
```

`UniMessage.send` 的定义如下：

```python
async def send(
    self,
    target: Event | Target | None = None,
    bot: Bot | None = None,
    fallback: bool | FallbackStrategy = FallbackStrategy.rollback,
    at_sender: str | bool = False,
    reply_to: str | bool | Reply | None = False,
    **kwargs: Any,
) -> Receipt:
    ...
```

| 参数 | 说明 |
|---|---|
| `target` | 发送目标，可以是 `Event`、`Target` 或 `None`（使用当前上下文） |
| `bot` | 指定 Bot 对象 |
| `fallback` | 回退策略 |
| `at_sender` | 是否 @ 发送者 |
| `reply_to` | 是否回复消息。`Reply` 表示直接使用回复元素；`bool` 表示是否回复当前消息；`str` 表示消息 id |
| `**kwargs` | 各 `Bot.send` 的特定参数 |

在 `AlconnaMatcher` 下，`got`、`send`、`reject` 等方法皆支持使用 `UniMessage`，不需要手动调用 `export`：

```python
from arclet.alconna import Alconna, Args
from nonebot_plugin_alconna import Match, AlconnaMatcher, on_alconna
from nonebot_plugin_alconna.uniseg import At, UniMessage

test_cmd = on_alconna(Alconna("test", Args["target?", At]))

@test_cmd.handle()
async def tt_h(matcher: AlconnaMatcher, target: Match[At]):
    if target.available:
        matcher.set_path_arg("target", target.result)

@test_cmd.got_path("target", prompt="请输入目标")
async def tt(target: At):
    await test_cmd.send(UniMessage([target, "\ndone."]))
```

### 回退策略

`send` 方法的 `fallback` 参数用于指定回退策略（即当前适配器不支持的消息段如何处理）：

| 策略 | 说明 |
|---|---|
| `FallbackStrategy.auto` | 插件自动选择策略 |
| `FallbackStrategy.forbid` | 抛出异常 |
| `FallbackStrategy.rollback` | 从未转换消息段的子元素中提取可能的可发送消息段 |
| `FallbackStrategy.to_text` | 将未转换的消息段转为文本元素 |
| `FallbackStrategy.ignore` | 忽略未转换的消息段 |

另外 `fallback` 传入 `bool` 时，`True` 等价于 `FallbackStrategy.auto`，`False` 等价于 `FallbackStrategy.forbid`。

### 主动发送消息

`UniMessage.send` 也可以用于主动发送消息：

```python
from nonebot_plugin_alconna.uniseg import UniMessage, Target, SupportScope
from nonebot import get_driver

driver = get_driver()

@driver.on_startup
async def on_startup():
    target = Target("xxxx", scope=SupportScope.qq_client)
    await UniMessage("Hello!").send(target=target)
```

> **注意**：在响应器以外的地方，除非启用了 `alconna_apply_fetch_targets` 配置项，否则 `bot` 参数必须手动传入。

### Receipt 对象

`send` 方法返回的 `Receipt` 对象可以用于修改/撤回/表态消息：

```python
async def handle():
    receipt = await UniMessage.text("hello!").send(at_sender=True, reply_to=True)
    await receipt.recall(delay=1)
    receipt1 = await UniMessage.text("hello!").send(at_sender=True, reply_to=True)
    await receipt1.edit("world!")
```

`Receipt` 对象拥有以下方法：

| 方法 | 说明 |
|---|---|
| `reply` | 回复已经发送的消息 |
| `send` / `finish` | 发送消息 |
| `get_reply` | 生成对已发送消息的回复元素 |
| `reaction` | 表态消息 |
| `reactionable` | 表明是否可以表态 |
| `edit` | 修改消息 |
| `editable` | 表明是否可以修改 |
| `recall` | 撤回消息 |
| `recallable` | 表明是否可以撤回 |

## 构造

如同 `Message`，`UniMessage` 可以传入单个字符串/消息段，或可迭代的字符串/消息段：

```python
from nonebot_plugin_alconna.uniseg import UniMessage, At

msg = UniMessage("Hello")
msg1 = UniMessage(At("user", "124"))
msg2 = UniMessage(["Hello", At("user", "124")])
```

`UniMessage` 上同时存在便捷方法，令其可以链式地添加消息段：

```python
from nonebot_plugin_alconna.uniseg import UniMessage, At, Image

msg = UniMessage.text("Hello").at("124").image(path="/path/to/img")
assert msg == UniMessage(
    ["Hello", At("user", "124"), Image(path="/path/to/img")]
)
```

### 使用消息模板

`UniMessage.template` 类似于 `Message.template`，可以用于格式化消息。

#### 拓展控制符

相比 `Message`，UniMessage 对于 `{:XXX}` 做了另一类拓展。其能够识别例如 `At(xxx, yyy)` 或 `Emoji(aaa, bbb)` 的字符串并执行：

```python
from nonebot_plugin_alconna.uniseg import UniMessage

# 直接在格式化字符串中使用 Segment 构造
>>> UniMessage.template("{:At(user, target)}").format(target="123")
UniMessage(At("user", "123"))

>>> UniMessage.template("{:At(type=user, target=id)}").format(id="123")
UniMessage(At("user", "123"))

>>> UniMessage.template("{:At(type=user, target=123)}").format()
UniMessage(At("user", "123"))
```

在 `AlconnaMatcher` 中，`{:XXX}` 更进一步地提供了获取 `event` 和 `bot` 中属性的功能：

```python
from arclet.alconna import Alconna, Args
from nonebot_plugin_alconna import At, Match, UniMessage, AlconnaMatcher, on_alconna

test_cmd = on_alconna(Alconna("test", Args["target?", At]))

@test_cmd.handle()
async def tt_h(matcher: AlconnaMatcher, target: Match[At]):
    if target.available:
        matcher.set_path_arg("target", target.result)

@test_cmd.got_path(
    "target",
    prompt=UniMessage.template("{:At(user, $event.get_user_id())} 请确认目标"),
)
async def tt():
    await test_cmd.send(
        UniMessage.template("{:At(user, $event.get_user_id())} 已确认目标为 {target}")
    )
```

#### 特殊变量

| 变量 | 说明 |
|---|---|
| `$event` | 当前事件对象，可调用其方法如 `$event.get_user_id()` |
| `$message_id` | 当前消息事件 ID |
| `$target` | 当前消息发送对象 |

> **提示**：在 `AlconnaMatcher` 中，`UniMessage.template` 的格式化方法会自动将 `Arparma.all_matched_args`、`state` 中的变量传入到 `format` 方法中，因此你可以直接使用上述变量。

### 拼接消息

`str`、`UniMessage`、`Segment` 对象之间可以直接相加，相加均会返回一个新的 `UniMessage` 对象：

```python
# 消息序列与消息段相加
UniMessage("text") + Text("text")
# 消息序列与字符串相加
UniMessage([Text("text")]) + "text"
# 消息序列与消息序列相加
UniMessage("text") + UniMessage([Text("text")])
# 字符串与消息序列相加
"text" + UniMessage([Text("text")])
# 消息段与消息段相加
Text("text") + Text("text")
# 消息段与字符串相加
Text("text") + "text"
# 消息段与消息序列相加
Text("text") + UniMessage([Text("text")])
# 字符串与消息段相加
"text" + Text("text")
```

如果需要在当前消息序列后直接拼接新的消息段，可以使用 `append`、`extend` 方法，或者使用自加：

```python
msg = UniMessage([Text("text")])
# 自加
msg += "text"
msg += Text("text")
msg += UniMessage([Text("text")])
# 附加
msg.append(Text("text"))
# 扩展
msg.extend([Text("text")])
```

## 操作

### 检查消息段

通过 `in` 运算符或消息序列的 `has` 方法：

```python
# 是否存在消息段
At("user", "1234") in message
# 是否存在指定类型的消息段
At in message
```

使用 `only` 方法检查消息中是否仅包含指定的消息段：

```python
# 是否都为 "test"
message.only("test")
# 是否仅包含指定类型的消息段
message.only(Text)
```

### 获取消息纯文本

类似于 `Message.extract_plain_text()`：

```python
assert UniMessage(
    [At("user", "1234"), "text"]
).extract_plain_text() == "text"
```

### 遍历

通用消息序列继承自 `List[Segment]`，可以使用 `for` 循环遍历：

```python
for segment in message:  # type: Segment
    ...
```

### 过滤、索引与切片

消息序列对列表的索引与切片进行了增强，支持 `type` 过滤索引与切片：

```python
message = UniMessage(
    [
        Reply(...),
        "text1",
        At("user", "1234"),
        "text2",
    ]
)

# 索引
message[0] == Reply(...)
# 切片
message[0:2] == UniMessage([Reply(...), Text("text1")])
# 类型过滤
message[At] == UniMessage([At("user", "1234")])
# 类型索引
message[At, 0] == At("user", "1234")
# 类型切片
message[Text, 0:2] == UniMessage([Text("text1"), Text("text2")])
```

使用 `include`、`exclude` 方法进行类型过滤：

```python
message.include(Text, At)
message.exclude(Reply)
```

使用 `filter` 方法：

```python
message.filter(lambda x: isinstance(x, At) and x.flag == "user")
```

使用增强的 `index`、`count` 方法：

```python
# 指定类型首个消息段索引
message.index(Text) == 1
# 指定类型消息段数量
message.count(Text) == 2
```

使用 `get` 方法获取指定类型指定个数的消息段：

```python
message.get(Text, 1) == UniMessage([Text("test1")])
```

### 嵌套提取

消息序列的 `select` 方法可以递归地从消息中选择指定类型的消息段：

```python
message = UniMessage(
    [
        Text("text1"),
        Image(url="url1")(
            Text("text2"),
        ),
    ]
)

assert message.select(Text) == UniMessage(
    [
        Text("text1"),
        Text("text2"),
    ]
)
```

### 转换

`map` 方法可以将消息段转换为指定类型的数据：

```python
# 转换消息段为另一类型的消息段，此时返回结果仍是 UniMessage
message.map(lambda x: Text(x.target))
# 转换消息段为另一类型的数据，此时返回结果为 list[T]
message.map(lambda x: x.target)
```

`transform` 和 `transform_async` 方法，允许传入转换规则：

```python
rule = {
    "text": True,
    "at": lambda attrs, children: Text(attrs["target"]),
}
message.transform(rule)
```

转换规则的类型一般为 `dict[str, Transformer]`，以消息元素类型的名称为键，定义方式如下：

| 类型 | 说明 |
|---|---|
| `bool` | `True` 表示保留，`False` 表示丢弃 |
| `Fragment` | 直接替换为指定的 Segment 或 Segment 列表 |
| `Render` | 渲染函数 `(attrs, children) -> bool \| Fragment` |

### 字符串操作

类似于 `str`，消息序列支持如下方法操作消息内的文本部分：

- `strip`、`lstrip`、`rstrip`
- `removeprefix`、`removesuffix`
- `startswith`、`endswith`
- `replace`
- `split`

```python
msg = UniMessage.text("foo bar").at("1234").text("baz qux")

# 分割，返回 list[UniMessage]
parts = msg.split(" ")

# 替换，返回 UniMessage。新文本可以用 str 或 Text 来替换
new_msg = msg.replace("ba", "baaa")

# 前缀/后缀检查
msg.startswith("foo")  # True
msg.endswith("qux")    # True

# 去除前缀/后缀
msg1 = msg.removeprefix("foo")
# UniMessage([Text(" bar"), At("user", "1234"), Text("baz qux")])
msg2 = msg.removesuffix("qux")
# UniMessage([Text("foo bar"), At("user", "1234"), Text("baz ")])

# 去除空格
msg1 = msg1.lstrip()
# UniMessage([Text("bar"), At("user", "1234"), Text("baz qux")])
msg2 = msg2.rstrip()
# UniMessage([Text("foo bar"), At("user", "1234"), Text("baz")])
```

## 持久化

`UniMessage` 支持消息持久化，具体为 `dump` 与 `load` 方法：

```python
msg = UniMessage.text("Hello").image(url="url")
data = msg.dump()
# [{"type": "text", "text": "Hello"}, {"type": "image", "url": "url"}]
assert UniMessage.load(data) == msg
```

### dump

```python
def dump(
    self,
    media_save_dir: str | Path | bool | None = None,
    json: bool = False,
) -> str | list[dict[str, Any]]: ...
```

`media_save_dir` 用于指定持久化的媒体文件存储目录：

| 值 | 说明 |
|---|---|
| 不指定 | 尝试使用 `nonebot_plugin_localstore` 提供的路径，否则使用当前工作目录 |
| `True` | 将文件数据转为 base64 编码 |
| `False` | 不保存媒体文件 |
| `str` 或 `Path` | 将媒体文件保存到指定目录下 |

### load

```python
@classmethod
def load(cls, data: str | list[dict[str, Any]]) -> UniMessage: ...
```

其中 `data` 应符合 JSON 格式。
