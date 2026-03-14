# 事件类型与重载

NoneBot 支持通过类型注解来区分不同的事件类型，从而在同一个事件响应器中对不同事件做出不同的处理。这个机制称为"事件重载"。

---

## 基本概念

### 事件类型体系

NoneBot 的事件继承体系（以 OneBot v11 为例）：

```
nonebot.adapters.Event                      # 基类
├── onebot.v11.Event                        # OneBot v11 事件基类
│   ├── MessageEvent                        # 消息事件
│   │   ├── PrivateMessageEvent             # 私聊消息
│   │   └── GroupMessageEvent               # 群聊消息
│   ├── NoticeEvent                         # 通知事件
│   │   ├── GroupUploadNoticeEvent           # 群文件上传
│   │   ├── GroupAdminNoticeEvent            # 群管理变动
│   │   ├── GroupDecreaseNoticeEvent         # 群成员减少
│   │   ├── GroupIncreaseNoticeEvent         # 群成员增加
│   │   ├── GroupBanNoticeEvent              # 群禁言
│   │   ├── FriendAddNoticeEvent             # 好友添加
│   │   ├── GroupRecallNoticeEvent           # 群消息撤回
│   │   ├── FriendRecallNoticeEvent          # 好友消息撤回
│   │   ├── PokeNotifyEvent                 # 戳一戳
│   │   └── ...
│   └── RequestEvent                        # 请求事件
│       ├── FriendRequestEvent              # 好友请求
│       └── GroupRequestEvent               # 加群请求
```

### 依赖注入中的类型注解

NoneBot 的依赖注入系统会根据处理函数的参数类型注解自动筛选事件。如果事件类型不匹配注解，该处理函数会被 **跳过**。

---

## 基础用法

### 区分私聊与群聊

```python
from nonebot import on_command
from nonebot.adapters.onebot.v11 import PrivateMessageEvent, GroupMessageEvent

cmd = on_command("hello", priority=10, block=True)


@cmd.handle()
async def handle_private(event: PrivateMessageEvent):
    """仅在私聊时触发"""
    await cmd.finish(f"你好！这是私聊回复。你的 QQ 是 {event.user_id}")


@cmd.handle()
async def handle_group(event: GroupMessageEvent):
    """仅在群聊时触发"""
    await cmd.finish(f"你好！这是群聊回复。群号 {event.group_id}")
```

执行逻辑：

1. 收到私聊消息 → 尝试 `handle_private` → 类型匹配 `PrivateMessageEvent` → 执行
2. 收到群聊消息 → 尝试 `handle_private` → 类型不匹配 → 跳过 → 尝试 `handle_group` → 类型匹配 → 执行

### 区分不同通知事件

```python
from nonebot import on_notice
from nonebot.adapters.onebot.v11 import (
    GroupIncreaseNoticeEvent,
    GroupDecreaseNoticeEvent,
    FriendAddNoticeEvent,
)

notice = on_notice(priority=10)


@notice.handle()
async def handle_join(event: GroupIncreaseNoticeEvent):
    await notice.finish(f"欢迎 {event.user_id} 加入群聊！")


@notice.handle()
async def handle_leave(event: GroupDecreaseNoticeEvent):
    if event.sub_type == "kick":
        await notice.finish(f"用户 {event.user_id} 被踢出了群聊")
    else:
        await notice.finish(f"用户 {event.user_id} 退出了群聊")


@notice.handle()
async def handle_friend(event: FriendAddNoticeEvent):
    await notice.finish(f"新好友 {event.user_id} 已添加！")
```

---

## 使用 Union 类型

当需要在一个处理函数中处理多种事件类型时，可以使用 `Union`：

```python
from typing import Union
from nonebot import on_command
from nonebot.adapters.onebot.v11 import PrivateMessageEvent, GroupMessageEvent

cmd = on_command("info", priority=10, block=True)


@cmd.handle()
async def handle(event: Union[PrivateMessageEvent, GroupMessageEvent]):
    """私聊和群聊都会触发"""
    user_id = event.user_id
    if isinstance(event, GroupMessageEvent):
        await cmd.finish(f"群聊消息，群号：{event.group_id}，用户：{user_id}")
    else:
        await cmd.finish(f"私聊消息，用户：{user_id}")
```

使用 Python 3.10+ 的联合类型语法：

```python
@cmd.handle()
async def handle(event: PrivateMessageEvent | GroupMessageEvent):
    ...
```

---

## 使用基类接收所有事件

使用基类 `Event` 或 `MessageEvent` 可以接收所有匹配的事件，不做类型过滤：

```python
from nonebot import on_command
from nonebot.adapters import Event

cmd = on_command("any", priority=10, block=True)


@cmd.handle()
async def handle(event: Event):
    """接收所有类型的事件"""
    await cmd.finish(f"收到事件，类型：{event.get_type()}")
```

```python
from nonebot.adapters.onebot.v11 import MessageEvent

cmd = on_command("msg", priority=10, block=True)


@cmd.handle()
async def handle(event: MessageEvent):
    """接收所有消息事件（包括私聊和群聊）"""
    await cmd.finish(f"消息来自：{event.get_session_id()}")
```

---

## 依赖注入与事件类型

### 获取事件特有属性

不同事件类型有不同的属性，通过类型注解可以安全地访问这些属性：

```python
from nonebot import on_command
from nonebot.adapters.onebot.v11 import GroupMessageEvent

cmd = on_command("group_info", priority=10, block=True)


@cmd.handle()
async def handle(event: GroupMessageEvent):
    # GroupMessageEvent 特有属性
    group_id = event.group_id
    user_id = event.user_id
    message_id = event.message_id

    # sender 信息
    nickname = event.sender.nickname
    card = event.sender.card          # 群名片
    role = event.sender.role          # owner / admin / member

    await cmd.finish(
        f"群号：{group_id}\n"
        f"用户：{user_id}\n"
        f"昵称：{nickname}\n"
        f"群名片：{card}\n"
        f"角色：{role}"
    )
```

### Bot 类型注解

类似事件，Bot 也支持类型注解重载：

```python
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot as OneBotBot

cmd = on_command("test", priority=10, block=True)


@cmd.handle()
async def handle(bot: OneBotBot):
    """仅当 Bot 是 OneBot v11 的 Bot 时触发"""
    info = await bot.get_login_info()
    await cmd.finish(f"OneBot Bot: {info['nickname']}")
```

---

## 综合示例

### 消息处理器（完整示例）

```python
from nonebot import on_command
from nonebot.adapters import Bot, Event
from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent,
    PrivateMessageEvent,
    MessageSegment,
)

echo = on_command("echo", priority=10, block=True)


@echo.handle()
async def handle_group(bot: Bot, event: GroupMessageEvent):
    """群聊特殊处理：回复并 @ 用户"""
    text = event.get_plaintext().removeprefix("/echo").strip()
    reply = MessageSegment.reply(event.message_id)
    at = MessageSegment.at(event.user_id)
    await echo.finish(reply + at + f" {text}")


@echo.handle()
async def handle_private(event: PrivateMessageEvent):
    """私聊直接回复"""
    text = event.get_plaintext().removeprefix("/echo").strip()
    await echo.finish(f"Echo: {text}")
```

### 通知事件处理（完整示例）

```python
from nonebot import on_notice
from nonebot.adapters import Bot
from nonebot.adapters.onebot.v11 import (
    GroupIncreaseNoticeEvent,
    GroupDecreaseNoticeEvent,
    GroupBanNoticeEvent,
    GroupRecallNoticeEvent,
    PokeNotifyEvent,
    MessageSegment,
)

notice_handler = on_notice(priority=10)


@notice_handler.handle()
async def on_member_join(bot: Bot, event: GroupIncreaseNoticeEvent):
    """新成员入群"""
    member = await bot.get_group_member_info(
        group_id=event.group_id,
        user_id=event.user_id,
    )
    name = member.get("card") or member.get("nickname", "新成员")
    await notice_handler.finish(
        MessageSegment.at(event.user_id) + f" 欢迎 {name} 加入！"
    )


@notice_handler.handle()
async def on_member_leave(event: GroupDecreaseNoticeEvent):
    """成员退群/被踢"""
    action = "被踢出" if event.sub_type == "kick" else "离开了"
    await notice_handler.finish(f"用户 {event.user_id} {action}群聊")


@notice_handler.handle()
async def on_ban(event: GroupBanNoticeEvent):
    """禁言/解禁"""
    if event.sub_type == "ban":
        minutes = event.duration // 60
        await notice_handler.finish(f"用户 {event.user_id} 被禁言 {minutes} 分钟")
    else:
        await notice_handler.finish(f"用户 {event.user_id} 已被解除禁言")


@notice_handler.handle()
async def on_recall(bot: Bot, event: GroupRecallNoticeEvent):
    """消息撤回"""
    msg = await bot.get_msg(message_id=event.message_id)
    await notice_handler.finish(f"有人撤回了一条消息：{msg['message']}")


@notice_handler.handle()
async def on_poke(event: PokeNotifyEvent):
    """戳一戳"""
    if event.target_id == event.self_id:
        await notice_handler.finish("别戳我！>_<")
```

### 请求事件处理

```python
from nonebot import on_request
from nonebot.adapters import Bot
from nonebot.adapters.onebot.v11 import FriendRequestEvent, GroupRequestEvent

req_handler = on_request(priority=10)


@req_handler.handle()
async def handle_friend_request(bot: Bot, event: FriendRequestEvent):
    """自动同意好友请求"""
    await bot.set_friend_add_request(flag=event.flag, approve=True)
    await req_handler.finish(f"已自动同意 {event.user_id} 的好友请求")


@req_handler.handle()
async def handle_group_request(bot: Bot, event: GroupRequestEvent):
    """处理加群请求"""
    if event.sub_type == "invite":
        await bot.set_group_add_request(
            flag=event.flag,
            sub_type="invite",
            approve=True,
        )
        await req_handler.finish(f"已同意加入群 {event.group_id} 的邀请")
```

---

## 重载注意事项

1. **处理函数按注册顺序尝试** — 第一个类型匹配的函数会被执行
2. **执行后即结束** — 一个处理函数执行了 `finish()` 后，后续的处理函数不会执行
3. **基类兜底** — 如果需要兜底处理，可以在最后注册一个使用基类 `Event` 的处理函数
4. **类型安全** — 使用具体事件类型注解后，IDE 可以正确提示该类型的属性和方法
5. **多适配器环境** — 在多适配器环境下，Bot 和 Event 的类型注解都可用于区分来源

```python
from nonebot import on_command
from nonebot.adapters import Event

cmd = on_command("demo", priority=10, block=True)


@cmd.handle()
async def handle_group(event: GroupMessageEvent):
    await cmd.finish("群聊处理")


@cmd.handle()
async def handle_private(event: PrivateMessageEvent):
    await cmd.finish("私聊处理")


@cmd.handle()
async def handle_fallback(event: Event):
    """兜底处理：以上都不匹配时执行"""
    await cmd.finish("其他环境处理")
```
