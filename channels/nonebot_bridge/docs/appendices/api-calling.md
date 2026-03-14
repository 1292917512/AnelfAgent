# 使用平台接口

NoneBot 通过 `Bot` 对象提供统一的平台 API 调用能力。每个适配器实现了自己的 API 调用方式，但上层接口保持一致。

---

## Bot 对象

### 获取 Bot

在处理函数中通过依赖注入获取当前 Bot：

```python
from nonebot import on_command
from nonebot.adapters import Bot

cmd = on_command("test", priority=10, block=True)


@cmd.handle()
async def handle(bot: Bot):
    # bot 是当前处理事件的 Bot 实例
    bot_id = bot.self_id
    await cmd.finish(f"当前 Bot ID: {bot_id}")
```

### 获取所有 Bot

```python
import nonebot

# 获取所有已连接的 Bot 字典 {bot_id: Bot}
bots = nonebot.get_bots()

# 获取指定 Bot
bot = nonebot.get_bot("123456789")

# 获取任意一个 Bot
bot = nonebot.get_bot()
```

---

## 发送消息

### Bot.send()

最常用的消息发送方法，自动回复到当前事件的来源：

```python
from nonebot import on_command
from nonebot.adapters import Bot, Event

cmd = on_command("hello", priority=10, block=True)


@cmd.handle()
async def handle(bot: Bot, event: Event):
    # 方式 1：使用 matcher 的 send/finish（推荐）
    await cmd.send("你好！")

    # 方式 2：使用 bot.send()（效果一样）
    await bot.send(event, "你好！")

    await cmd.finish("结束！")
```

### 发送富文本消息

```python
from nonebot import on_command
from nonebot.adapters import Bot, Event
from nonebot.adapters.onebot.v11 import MessageSegment

cmd = on_command("rich", priority=10, block=True)


@cmd.handle()
async def handle(bot: Bot, event: Event):
    # 发送图片
    await bot.send(event, MessageSegment.image("https://example.com/image.png"))

    # 发送混合消息（文本 + 图片）
    msg = MessageSegment.text("看看这张图：") + MessageSegment.image("file:///path/to/image.png")
    await bot.send(event, msg)
```

---

## call_api()

### 基本用法

`Bot.call_api()` 是调用底层平台 API 的通用方法：

```python
from nonebot import on_command
from nonebot.adapters import Bot

cmd = on_command("api", priority=10, block=True)


@cmd.handle()
async def handle(bot: Bot):
    # 通用调用方式
    result = await bot.call_api("get_login_info")
    await cmd.finish(f"昵称：{result['nickname']}")
```

### 魔法方法调用

NoneBot Bot 对象支持将 API 名称作为方法直接调用（等价于 `call_api`）：

```python
@cmd.handle()
async def handle(bot: Bot):
    # 以下两种写法等价
    result = await bot.call_api("get_login_info")
    result = await bot.get_login_info()
```

---

## OneBot v11 常用 API

以下是 OneBot v11 协议中常用的 API 调用示例。

### 消息相关

#### 发送私聊消息

```python
await bot.send_private_msg(
    user_id=123456789,
    message="你好！这是私聊消息。",
)
```

#### 发送群聊消息

```python
await bot.send_group_msg(
    group_id=987654321,
    message="大家好！这是群聊消息。",
)
```

#### 撤回消息

```python
# 先发送消息获取 message_id
result = await bot.send_group_msg(
    group_id=987654321,
    message="这条消息即将被撤回",
)
# 撤回
await bot.delete_msg(message_id=result["message_id"])
```

#### 获取消息详情

```python
msg = await bot.get_msg(message_id=12345)
# msg: {"message_id": 12345, "real_id": 12345, "sender": {...}, "time": ..., "message": ...}
```

### 好友相关

#### 获取好友列表

```python
friend_list = await bot.get_friend_list()
for friend in friend_list:
    print(f"QQ: {friend['user_id']}, 昵称: {friend['nickname']}")
```

#### 获取陌生人信息

```python
info = await bot.get_stranger_info(user_id=123456789, no_cache=False)
# info: {"user_id": 123456789, "nickname": "xxx", "sex": "male", "age": 20}
```

### 群组相关

#### 获取群列表

```python
group_list = await bot.get_group_list()
for group in group_list:
    print(f"群号: {group['group_id']}, 群名: {group['group_name']}")
```

#### 获取群信息

```python
group_info = await bot.get_group_info(group_id=987654321, no_cache=False)
# group_info: {"group_id": 987654321, "group_name": "xxx", "member_count": 100, ...}
```

#### 获取群成员列表

```python
members = await bot.get_group_member_list(group_id=987654321)
for member in members:
    print(f"QQ: {member['user_id']}, 群昵称: {member['card'] or member['nickname']}")
```

#### 获取群成员信息

```python
member = await bot.get_group_member_info(
    group_id=987654321,
    user_id=123456789,
    no_cache=False,
)
# member: {"group_id": ..., "user_id": ..., "nickname": ..., "card": ..., "role": "owner/admin/member", ...}
```

### 群管理

#### 踢出群成员

```python
await bot.set_group_kick(
    group_id=987654321,
    user_id=123456789,
    reject_add_request=False,  # 是否拒绝再次加群
)
```

#### 禁言群成员

```python
# 禁言 10 分钟
await bot.set_group_ban(
    group_id=987654321,
    user_id=123456789,
    duration=600,  # 秒，0 表示解除禁言
)
```

#### 全体禁言

```python
await bot.set_group_whole_ban(
    group_id=987654321,
    enable=True,  # True 开启，False 关闭
)
```

#### 设置群名片

```python
await bot.set_group_card(
    group_id=987654321,
    user_id=123456789,
    card="新群昵称",
)
```

#### 设置群名

```python
await bot.set_group_name(
    group_id=987654321,
    group_name="新群名",
)
```

#### 设置群管理员

```python
await bot.set_group_admin(
    group_id=987654321,
    user_id=123456789,
    enable=True,  # True 设为管理，False 取消管理
)
```

#### 退出群聊

```python
await bot.set_group_leave(
    group_id=987654321,
    is_dismiss=False,  # 群主解散群（仅群主）
)
```

### 请求处理

#### 处理好友请求

```python
await bot.set_friend_add_request(
    flag="request_flag_xxx",
    approve=True,
    remark="备注名",
)
```

#### 处理加群请求

```python
await bot.set_group_add_request(
    flag="request_flag_xxx",
    sub_type="add",       # "add" 加群 / "invite" 邀请
    approve=True,
    reason="拒绝原因",     # 拒绝时填写
)
```

---

## 完整实用示例

### 群管理插件

```python
from nonebot import on_command
from nonebot.adapters import Bot, Event
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment
from nonebot.params import CommandArg
from nonebot.adapters import Message
from nonebot.permission import SUPERUSER

ban_cmd = on_command("ban", permission=SUPERUSER, priority=1, block=True)


@ban_cmd.handle()
async def handle_ban(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    text = args.extract_plain_text().strip()
    parts = text.split()
    if len(parts) < 1:
        await ban_cmd.finish("用法：/ban @用户 [时长(分钟)]")

    at_segments = [seg for seg in args if seg.type == "at"]
    if not at_segments:
        await ban_cmd.finish("请 @ 要禁言的用户")

    user_id = int(at_segments[0].data["qq"])
    duration = int(parts[-1]) * 60 if parts[-1].isdigit() else 600

    await bot.set_group_ban(
        group_id=event.group_id,
        user_id=user_id,
        duration=duration,
    )
    await ban_cmd.finish(f"已禁言 {duration // 60} 分钟")
```

### 群信息查询

```python
from nonebot import on_command
from nonebot.adapters import Bot
from nonebot.adapters.onebot.v11 import GroupMessageEvent

group_info = on_command("群信息", priority=10, block=True)


@group_info.handle()
async def handle(bot: Bot, event: GroupMessageEvent):
    info = await bot.get_group_info(group_id=event.group_id)
    members = await bot.get_group_member_list(group_id=event.group_id)

    admins = [m for m in members if m["role"] in ("admin", "owner")]
    admin_names = ", ".join(m["card"] or m["nickname"] for m in admins)

    await group_info.finish(
        f"群名：{info['group_name']}\n"
        f"群号：{info['group_id']}\n"
        f"成员数：{info['member_count']}/{info['max_member_count']}\n"
        f"管理员：{admin_names}"
    )
```

---

## API 错误处理

调用 API 可能失败，建议使用 try-except 处理：

```python
from nonebot.adapters import Bot
from nonebot.exception import ActionFailed


@cmd.handle()
async def handle(bot: Bot):
    try:
        await bot.set_group_ban(
            group_id=123456,
            user_id=789012,
            duration=600,
        )
        await cmd.finish("禁言成功！")
    except ActionFailed as e:
        await cmd.finish(f"操作失败：{e}")
    except Exception as e:
        await cmd.finish(f"未知错误：{e}")
```

---

## API 速查表

| API | 说明 |
|-----|------|
| `get_login_info` | 获取登录号信息 |
| `send_private_msg` | 发送私聊消息 |
| `send_group_msg` | 发送群消息 |
| `delete_msg` | 撤回消息 |
| `get_msg` | 获取消息详情 |
| `get_friend_list` | 获取好友列表 |
| `get_stranger_info` | 获取陌生人信息 |
| `get_group_list` | 获取群列表 |
| `get_group_info` | 获取群信息 |
| `get_group_member_list` | 获取群成员列表 |
| `get_group_member_info` | 获取群成员详情 |
| `set_group_kick` | 踢出群成员 |
| `set_group_ban` | 禁言群成员 |
| `set_group_whole_ban` | 全体禁言 |
| `set_group_card` | 设置群名片 |
| `set_group_name` | 设置群名 |
| `set_group_admin` | 设置管理员 |
| `set_group_leave` | 退出/解散群 |
| `set_friend_add_request` | 处理好友请求 |
| `set_group_add_request` | 处理加群请求 |
