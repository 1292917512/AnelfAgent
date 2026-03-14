<!-- source: https://nonebot.dev/docs/tutorial/message -->

# 处理消息

在不同平台中，一条消息可能会有各种不同的表现形式，它可能是一段纯文本、一张图片、一段语音、一篇富文本文章，也有可能是多种类型的组合等等。

在 NoneBot 中，为确保消息的正常处理与跨平台兼容性，采用了扁平化的消息序列形式，即 `Message` 对象。消息序列是 NoneBot 中的消息载体，无论是接收还是发送的消息，都采用消息序列的形式进行处理。

## 认识消息类型

### 消息序列 Message

`Message` 的主要作用是用于表达"一串消息"。由于消息序列继承自 `List[MessageSegment]`，所以 `Message` 的本质是由若干消息段所组成的序列。因此，消息序列的使用方法与 `List` 有很多相似之处，例如切片、索引、拼接等。

### 消息段 MessageSegment

`MessageSegment` 是一段消息，是构成消息序列的最小单位。消息序列类似于一个自然段，而消息段则是组成自然段的一句话。

> **注意**：消息段的类型是由协议适配器提供的，因此你需要参考协议适配器的文档并导入对应的消息段后才能使用其特殊的消息类型。

## 使用消息序列

> 以下示例使用 `Console` 协议适配器演示。在实际使用中，需要确保使用的消息序列类型与目标平台类型一致。

### 基本操作

```python
from nonebot.adapters.console import Message, MessageSegment

message = Message([
    MessageSegment(type="text", data={"text": "hello"}),
    MessageSegment(type="markdown", data={"markup": "**world**"}),
])

for segment in message:
    print(segment.type, segment.data)
# text {'text': 'hello'}
# markdown {'markup': '**world**'}

len(message)
# 2
```

### 构造消息序列

#### 直接构造

`Message` 类可以直接实例化，支持 `str`、`MessageSegment`、`Iterable[MessageSegment]` 或适配器自定义类型的参数。

```python
from nonebot.adapters.console import Message, MessageSegment

# 从字符串构造
Message("Hello, world!")

# 从消息段构造
Message(MessageSegment.text("Hello, world!"))

# 从消息段列表构造
Message([MessageSegment.text("Hello, world!")])
```

#### 运算构造

`Message` 对象可以通过 `str`、`MessageSegment` 相加构造：

```python
# 消息序列 + 消息段
Message([MessageSegment.text("text")]) + MessageSegment.text("text")

# 消息序列 + 字符串
Message([MessageSegment.text("text")]) + "text"

# 消息序列 + 消息序列
Message([MessageSegment.text("text")]) + Message([MessageSegment.text("text")])

# 字符串 + 消息序列
"text" + Message([MessageSegment.text("text")])

# 消息段 + 消息段
MessageSegment.text("text") + MessageSegment.text("text")

# 消息段 + 字符串
MessageSegment.text("text") + "text"

# 消息段 + 消息序列
MessageSegment.text("text") + Message([MessageSegment.text("text")])

# 字符串 + 消息段
"text" + MessageSegment.text("text")
```

#### 从字典数组构造

使用 Pydantic 的 `TypeAdapter` 方法进行构造：

```python
from pydantic import TypeAdapter
from nonebot.adapters.console import Message, MessageSegment

# 由字典构造消息段
TypeAdapter(MessageSegment).validate_python(
    {"type": "text", "data": {"text": "text"}}
) == MessageSegment.text("text")

# 由字典数组构造消息序列
TypeAdapter(Message).validate_python(
    [
        MessageSegment.text("text"),
        {"type": "text", "data": {"text": "text"}},
    ],
) == Message([MessageSegment.text("text"), MessageSegment.text("text")])
```

### 获取消息纯文本

`str(message)` 通常不能得到消息的纯文本，而是消息序列的字符串表示。

```python
from nonebot.adapters.console import Message, MessageSegment

# 判断消息段是否为纯文本
MessageSegment.text("text").is_text() == True

# 提取消息纯文本字符串
Message(
    [MessageSegment.text("text"), MessageSegment.markdown("**markup**")]
).extract_plain_text() == "text"
```

### 遍历

消息序列继承自 `List[MessageSegment]`，可以使用 `for` 循环遍历：

```python
for segment in message:
    if segment.is_text():
        print("文本:", segment.data["text"])
    else:
        print("其他类型:", segment.type)
```

### 比较

消息和消息段都可以使用 `==` 或 `!=` 运算符比较：

```python
MessageSegment.text("text") != MessageSegment.text("foo")
some_message == Message([MessageSegment.text("text")])
```

### 检查消息段

使用 `in` 运算符或消息序列的 `has` 方法：

```python
# 是否存在消息段
MessageSegment.text("text") in message

# 是否存在指定类型的消息段
"text" in message
```

使用 `only` 方法检查消息中是否仅包含指定的消息段：

```python
# 是否都为指定消息段
message.only(MessageSegment.text("test"))

# 是否仅包含指定类型的消息段
message.only("text")
```

### 过滤、索引与切片

消息序列对列表的索引与切片进行了增强，支持 `type` 过滤索引与切片：

```python
from nonebot.adapters.console import Message, MessageSegment

message = Message([
    MessageSegment.text("test"),
    MessageSegment.markdown("test2"),
    MessageSegment.markdown("test3"),
    MessageSegment.text("test4"),
])

# 普通索引
message[0] == MessageSegment.text("test")

# 普通切片
message[0:2] == Message(
    [MessageSegment.text("test"), MessageSegment.markdown("test2")]
)

# 类型过滤 - 获取所有指定类型的消息段
message["markdown"] == Message(
    [MessageSegment.markdown("test2"), MessageSegment.markdown("test3")]
)

# 类型索引 - 获取指定类型的第 N 个消息段
message["markdown", 0] == MessageSegment.markdown("test2")

# 类型切片
message["markdown", 0:2] == Message(
    [MessageSegment.markdown("test2"), MessageSegment.markdown("test3")]
)
```

使用 `include`、`exclude` 方法进行类型过滤：

```python
# 仅保留指定类型
message.include("text", "markdown")

# 排除指定类型
message.exclude("text")
```

增强的 `index`、`count` 方法：

```python
# 指定类型首个消息段索引
message.index("markdown") == 1

# 指定类型消息段数量
message.count("markdown") == 2
```

`get` 方法获取指定类型指定个数的消息段：

```python
message.get("markdown", 1) == Message([MessageSegment.markdown("test2")])
```

### 拼接消息

使用自加和方法拼接：

```python
msg = Message([MessageSegment.text("text")])

# 自加
msg += "text"
msg += MessageSegment.text("text")
msg += Message([MessageSegment.text("text")])

# 附加
msg.append("text")
msg.append(MessageSegment.text("text"))

# 扩展
msg.extend([MessageSegment.text("text")])
```

使用 `join` 方法拼接一串消息：

```python
seg = MessageSegment.text("text")
msg = seg.join([
    MessageSegment.text("first"),
    Message([
        MessageSegment.text("second"),
        MessageSegment.text("third"),
    ]),
])

msg == Message([
    MessageSegment.text("first"),
    MessageSegment.text("text"),
    MessageSegment.text("second"),
    MessageSegment.text("third"),
])
```

## 使用消息模板

消息模板功能用于构建消息序列，在以下场景中特别有用：

- 客制化（由 Bot 最终用户提供消息模板时）
- 多行富文本编排（包含图片、文字以及表情等）

### 纯文本模板

默认采用 `str` 纯文本形式的格式化：

```python
from nonebot.adapters import MessageTemplate

MessageTemplate("{} {}").format("hello", "world")
# 'hello world'
```

### 消息序列模板

使用 `Message.template` 构建的消息模板采用消息序列形式的格式化：

> **注意**：应使用平台适配器提供的 `Message` 类型，不能使用 `nonebot.adapters.Message` 基类。

```python
from nonebot.adapters.console import Message, MessageSegment

Message.template("{} {}").format("hello", "world")
# Message(
#     MessageSegment.text("hello"),
#     MessageSegment.text(" "),
#     MessageSegment.text("world"),
# )
```

### 使用消息段格式化

```python
from nonebot.adapters.console import Message, MessageSegment

Message.template("{}{}").format(
    MessageSegment.markdown("**markup**"),
    "world",
)
# Message(
#     MessageSegment(type='markdown', data={'markup': '**markup**'}),
#     MessageSegment(type='text', data={'text': 'world'}),
# )
```

### 使用消息序列作为模板

```python
from nonebot.adapters.console import Message, MessageSegment

Message.template(
    MessageSegment.text("{user_id}")
    + MessageSegment.emoji("tada")
    + MessageSegment.text("{message}")
).format_map({
    "user_id": 123456,
    "message": "hello world",
})
# Message(
#     MessageSegment(type='text', data={'text': '123456'}),
#     MessageSegment(type='emoji', data={'emoji': 'tada'}),
#     MessageSegment(type='text', data={'text': 'hello world'}),
# )
```

> **注意**：只有消息序列中的文本类型消息段才能被格式化，其他类型的消息段将会原样添加。

### 扩展控制符

消息模板支持使用拓展控制符来控制消息段类型：

```python
from nonebot.adapters.console import Message, MessageSegment

Message.template("{name:emoji}").format(name='tada')
# Message(MessageSegment(type='emoji', data={'name': 'tada'}))
```

## 常用平台消息段类型

不同适配器提供的 `MessageSegment` 工厂方法不同，以下是一些常见的类型：

### OneBot V11

```python
from nonebot.adapters.onebot.v11 import MessageSegment

# 纯文本
MessageSegment.text("hello")

# 图片
MessageSegment.image("file:///path/to/image.png")
MessageSegment.image("https://example.com/image.png")
MessageSegment.image(b"binary_data")

# @某人
MessageSegment.at(user_id=123456)

# @全体成员
MessageSegment.at(user_id="all")

# 表情
MessageSegment.face(id_=123)

# 回复
MessageSegment.reply(id_=message_id)

# 语音
MessageSegment.record(file="file:///path/to/audio.mp3")

# JSON 消息
MessageSegment.json(data=json_string)
```

### Console

```python
from nonebot.adapters.console import MessageSegment

# 纯文本
MessageSegment.text("hello")

# Markdown
MessageSegment.markdown("**bold**")

# 表情
MessageSegment.emoji("tada")
```

## 在事件处理中使用消息

### 发送消息

```python
from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageSegment

cmd = on_command("test", priority=10, block=True)

@cmd.handle()
async def handle():
    # 发送纯文本
    await cmd.send("Hello!")

    # 发送消息段
    await cmd.send(MessageSegment.image("https://example.com/img.png"))

    # 发送组合消息
    msg = MessageSegment.text("看这张图：") + MessageSegment.image("https://example.com/img.png")
    await cmd.finish(msg)
```

### 处理接收到的消息

```python
from nonebot import on_message
from nonebot.adapters import Message
from nonebot.params import EventMessage

handler = on_message(priority=10, block=False)

@handler.handle()
async def handle(msg: Message = EventMessage()):
    # 获取纯文本
    text = msg.extract_plain_text()

    # 遍历消息段
    for seg in msg:
        if seg.type == "image":
            url = seg.data.get("url", "")
            # 处理图片...
        elif seg.type == "text":
            content = seg.data.get("text", "")
            # 处理文本...

    # 检查是否包含图片
    if "image" in msg:
        await handler.send("检测到图片！")

    # 获取所有图片
    images = msg["image"]
    if images:
        await handler.send(f"共 {len(images)} 张图片")
```

## 消息序列方法速查表

| 方法 | 说明 | 返回类型 |
|------|------|---------|
| `extract_plain_text()` | 提取纯文本 | `str` |
| `append(seg)` | 添加消息段 | `None` |
| `extend(segs)` | 扩展消息段列表 | `None` |
| `include(*types)` | 保留指定类型 | `Message` |
| `exclude(*types)` | 排除指定类型 | `Message` |
| `index(type)` | 查找首个指定类型的索引 | `int` |
| `count(type)` | 统计指定类型数量 | `int` |
| `get(type, count)` | 获取指定类型指定数量 | `Message` |
| `only(seg_or_type)` | 是否仅包含指定段/类型 | `bool` |
| `has(seg_or_type)` | 是否包含指定段/类型 | `bool` |
| `join(iterable)` | 连接多个消息 | `Message` |

## MessageSegment 方法速查表

| 方法 | 说明 | 返回类型 |
|------|------|---------|
| `is_text()` | 是否为纯文本段 | `bool` |
| `.type` | 消息段类型 | `str` |
| `.data` | 消息段数据 | `dict` |
