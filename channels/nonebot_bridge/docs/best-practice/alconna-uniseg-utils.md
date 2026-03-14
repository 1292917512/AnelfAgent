# 辅助功能

`uniseg` 模块同时提供了多种方法以通用消息操作。

> **注意**：这些方法中与 `event`、`bot` 相关的参数都会尝试从上下文中获取对象。

## 消息事件 ID

消息事件 ID 是用来标识当前消息事件的唯一 ID，通常用于回复/撤回/编辑/表态当前消息。

### 使用获取函数

```python
from nonebot_plugin_alconna.uniseg import get_message_id

msg_id = get_message_id(event)
```

### 使用依赖注入

通过提供的 `MessageId` 或 `MsgId` 依赖注入器来获取消息事件 ID：

```python
from nonebot_plugin_alconna.uniseg import MsgId

matcher = on_xxx(...)

@matcher.handle()
async def _(msg_id: MsgId):
    ...
```

> **注意**：该方法获取的消息事件 ID 不推荐直接用于各适配器的 API 调用中，可能会操作失败。

## 发送对象

消息发送对象是用来描述当前消息事件的可发送对象或者主动发送消息时的目标对象。

### Target 模型

```python
class Target:
    id: str
    """目标id；若为群聊则为 group_id 或者 channel_id，若为私聊则为 user_id"""
    parent_id: str
    """父级id；若为频道则为 guild_id，其他情况下可能为空字符串（例如 Feishu 下可作为部门 id）"""
    channel: bool
    """是否为频道，仅当目标平台符合频道概念时"""
    private: bool
    """是否为私聊"""
    source: str
    """可能的事件id"""
    self_id: str | None
    """机器人id，若为 None 则 Bot 对象会随机选择"""
    selector: Callable[[Bot], Awaitable[bool]] | None
    """选择器，用于在多个 Bot 对象中选择特定 Bot"""
    extra: dict[str, Any]
    """额外信息，用于适配器扩展"""
```

| 属性 | 类型 | 说明 |
|---|---|---|
| `id` | `str` | 目标 ID；群聊为 group_id/channel_id，私聊为 user_id |
| `parent_id` | `str` | 父级 ID；频道为 guild_id，其他情况可能为空 |
| `channel` | `bool` | 是否为频道 |
| `private` | `bool` | 是否为私聊 |
| `source` | `str` | 可能的事件 ID |
| `self_id` | `str \| None` | 机器人 ID，`None` 则随机选择 |
| `selector` | `Callable \| None` | Bot 选择器 |
| `extra` | `dict` | 额外信息 |

### 使用依赖注入获取 Target

通过提供的 `MessageTarget` 或 `MsgTarget` 依赖注入器来获取消息发送对象：

```python
from nonebot_plugin_alconna.uniseg import MsgTarget

matcher = on_xxx(...)

@matcher.handle()
async def _(target: MsgTarget):
    ...
```

### 构造 Target

主动构造一个发送对象时，可传入以下参数：

| 参数 | 类型 | 说明 |
|---|---|---|
| `id` | `str` | 目标 ID |
| `parent_id` | `str` | 父级 ID |
| `channel` | `bool` | 是否为频道 |
| `private` | `bool` | 是否为私聊 |
| `source` | `str` | 可能的事件 ID |
| `self_id` | `str \| None` | 机器人 ID |
| `selector` | `Callable \| None` | Bot 选择器 |
| `extra` | `dict` | 额外信息 |
| `platform` | `str \| None` | 平台名称，仅当目标适配器存在多个平台时使用 |
| `adapter` | `str \| None` | 适配器名称，若为 `None` 则需要明确指定 Bot 对象 |
| `scope` | `SupportScope \| None` | 平台范围，表示当前发送对象的平台类别 |

### 通过 Target 发送消息

通过 `Target` 对象，可以在 `UniMessage.send` 中指定发送对象：

```python
from nonebot_plugin_alconna.uniseg import UniMessage, MsgTarget, Target, SupportScope

matcher = on_xxx(...)

@matcher.handle()
async def _(target: MsgTarget):
    # 将消息发送给当前事件的发送者
    await UniMessage("Hello!").send(target=target)
    # 主动发送消息给群号为 12345 的 QQ 群聊
    target1 = Target("12345", scope=SupportScope.qq_client)
    await UniMessage("Hello!").send(target=target1)
```

### 选择器 (Target.select)

一般来说，主动发送消息时，`UniMessage.send` 或 `Target.self_id` 应指定一个 Bot 对象。但是这样会加重开发者的负担。

因此，构造 `Target` 对象时，`self_id`、`scope`、`adapter` 和 `platform` 都会参与到 `selector` 的构造中。

你可以使用 `Target` 来帮你筛选 Bot 对象：

```python
async def _():
    target = Target("12345", scope=SupportScope.qq_client)
    bot = await target.select()
```

若配置了 [alconna_apply_fetch_targets](./alconna-config.md#alconna_apply_fetch_targets) 选项，则在启动时会主动拉取一次发送对象列表。即对于某一主动构造的 `Target` 对象，插件将其与拉取下来的众多发送对象进行匹配，并选择第一个符合条件的发送对象，以选择对应的 Bot 对象。

## 撤回消息

通过 `message_recall` 方法来撤回消息事件：

```python
from nonebot_plugin_alconna.uniseg import MsgId, message_recall

matcher = on_xxx(...)

@matcher.handle()
async def _(msg_id: MsgId):
    await message_recall(msg_id)
```

`message_recall` 方法的参数如下：

```python
async def message_recall(
    message_id: str | None = None,
    event: Event | None = None,
    bot: Bot | None = None,
    adapter: str | None = None,
): ...
```

当 `message_id` 为 `None` 时，插件会尝试从 `event` 中获取消息事件 ID。

## 编辑消息

通过 `message_edit` 方法来编辑消息事件：

```python
from nonebot_plugin_alconna.uniseg import UniMessage, message_edit

matcher = on_xxx(...)

@matcher.handle()
async def _():
    await message_edit(UniMessage.text("1234"))
```

`message_edit` 方法的参数如下：

```python
async def message_edit(
    msg: UniMessage,
    message_id: str | None = None,
    event: Event | None = None,
    bot: Bot | None = None,
    adapter: str | None = None,
): ...
```

当 `message_id` 为 `None` 时，插件会尝试从 `event` 中获取消息事件 ID。

## 表态消息

> **注意**：该方法属于实验性功能。其接口可能会在未来的版本中发生变化。

通过 `message_reaction` 方法来表态消息事件：

```python
from nonebot_plugin_alconna.uniseg import message_reaction

matcher = on_xxx(...)

@matcher.handle()
async def _():
    await message_reaction("👍")
```

`message_reaction` 方法的参数如下：

```python
async def message_reaction(
    reaction: str | Emoji,
    message_id: str | None = None,
    event: Event | None = None,
    bot: Bot | None = None,
    adapter: str | None = None,
    delete: bool = False,
): ...
```

| 参数 | 说明 |
|---|---|
| `reaction` | 表态内容，可以是字符串或 `Emoji` 对象 |
| `message_id` | 消息 ID，为 `None` 时从 `event` 中获取 |
| `event` | 事件对象 |
| `bot` | Bot 对象 |
| `adapter` | 适配器名称 |
| `delete` | 是否删除自己的表态消息，默认 `False` |

## 响应规则

`uniseg` 模块提供了两个响应规则：

### at_me

是否在消息中 @ 了机器人。相较于 NoneBot 内置的 `to_me` 规则，`at_me` 规则只会在消息中 @ 机器人时触发：

```python
from nonebot_plugin_alconna.uniseg import at_me

matcher = on_xxx(..., rule=at_me())
```

### at_in

是否在消息中 @ 了指定的用户：

```python
from nonebot_plugin_alconna.uniseg import at_in

matcher = on_xxx(..., rule=at_in("user_id"))
```
