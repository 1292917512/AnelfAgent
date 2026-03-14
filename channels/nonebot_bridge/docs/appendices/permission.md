# 权限控制

权限（Permission）用于控制 **谁** 可以触发事件响应器。与规则（Rule）不同，权限只在首次响应时检查，会话期间的后续消息不再检查权限。

---

## 基本概念

### Permission 与 Rule 的区别

| 特性 | Rule（规则） | Permission（权限） |
|------|-------------|-------------------|
| 作用 | 检查 **事件内容** 是否匹配 | 检查 **用户身份** 是否有权限 |
| 检查时机 | 每次事件都检查 | 仅首次触发时检查 |
| 会话期间 | 每条消息都检查 | 会话中后续消息不再检查 |
| 组合方式 | `&`（与）、`\|`（或） | `\|`（或）、不支持 `&` |

### Permission 类

```python
from nonebot.permission import Permission
```

`Permission` 包含一组权限检查函数（`PermissionChecker`），**任一** 检查函数返回 `True` 即视为有权限（或关系）。

---

## 内置权限

### SUPERUSER

超级用户权限，匹配 `SUPERUSERS` 配置中的用户 ID：

```python
from nonebot import on_command
from nonebot.permission import SUPERUSER

admin_cmd = on_command("admin", permission=SUPERUSER, priority=1, block=True)


@admin_cmd.handle()
async def handle():
    await admin_cmd.finish("你是超级管理员！")
```

对应的 `.env` 配置：

```dotenv
SUPERUSERS=["123456789", "987654321"]
```

### 适配器特定权限

OneBot v11 提供了以下内置权限：

```python
from nonebot.adapters.onebot.v11.permission import (
    GROUP,              # 群消息
    GROUP_ADMIN,        # 群管理员
    GROUP_MEMBER,       # 群成员
    GROUP_OWNER,        # 群主
    PRIVATE,            # 私聊
    PRIVATE_FRIEND,     # 好友私聊
    PRIVATE_GROUP,      # 临时会话
    PRIVATE_OTHER,      # 其他私聊
)
```

使用示例：

```python
from nonebot import on_command
from nonebot.adapters.onebot.v11.permission import GROUP_ADMIN, GROUP_OWNER

# 仅群管理员和群主可触发
admin_cmd = on_command("管理", permission=GROUP_ADMIN | GROUP_OWNER, priority=5, block=True)


@admin_cmd.handle()
async def handle():
    await admin_cmd.finish("管理员命令已执行！")
```

---

## 自定义权限

### PermissionChecker

权限检查函数是一个异步函数，接收 `Bot` 和 `Event`，返回 `bool`：

```python
from nonebot.adapters import Bot, Event
from nonebot.permission import Permission


async def check_vip(bot: Bot, event: Event) -> bool:
    """检查用户是否为 VIP"""
    vip_list = {"111111", "222222", "333333"}
    return event.get_user_id() in vip_list


# 创建权限对象
VIP = Permission(check_vip)
```

### 在事件响应器中使用

```python
from nonebot import on_command
from nonebot.adapters import Bot, Event
from nonebot.permission import Permission


async def is_vip(bot: Bot, event: Event) -> bool:
    vip_users = {"123456", "789012"}
    return event.get_user_id() in vip_users

VIP = Permission(is_vip)

vip_cmd = on_command("vip", permission=VIP, priority=5, block=True)


@vip_cmd.handle()
async def handle():
    await vip_cmd.finish("欢迎 VIP 用户！")
```

### 基于外部数据的权限

```python
from nonebot.adapters import Bot, Event
from nonebot.permission import Permission


async def is_allowed_user(bot: Bot, event: Event) -> bool:
    """从数据库或文件中读取白名单"""
    # 实际中可以从数据库读取
    whitelist = load_whitelist()
    return event.get_user_id() in whitelist


ALLOWED = Permission(is_allowed_user)
```

### 基于群角色的权限

```python
from nonebot.adapters import Bot, Event
from nonebot.permission import Permission


async def is_group_admin_or_owner(bot: Bot, event: Event) -> bool:
    """检查是否为群管理员或群主"""
    try:
        session = event.get_session_id()
        if "group" not in session:
            return False

        user_id = event.get_user_id()
        group_id = session.split("_")[1]
        member_info = await bot.call_api(
            "get_group_member_info",
            group_id=int(group_id),
            user_id=int(user_id),
        )
        return member_info.get("role") in ("admin", "owner")
    except Exception:
        return False


GROUP_ADMIN_CUSTOM = Permission(is_group_admin_or_owner)
```

---

## 组合权限

### 使用 `|`（或）

```python
from nonebot import on_command
from nonebot.permission import SUPERUSER, Permission


async def is_vip(bot, event) -> bool:
    return event.get_user_id() in {"111", "222"}

VIP = Permission(is_vip)

# 超级用户 或 VIP 都可以触发
special_cmd = on_command("special", permission=SUPERUSER | VIP, priority=5, block=True)


@special_cmd.handle()
async def handle():
    await special_cmd.finish("特殊用户命令！")
```

### 组合多个权限

```python
from nonebot.adapters.onebot.v11.permission import GROUP_ADMIN, GROUP_OWNER, PRIVATE_FRIEND

# 群管理员 或 群主 或 好友私聊
mixed = GROUP_ADMIN | GROUP_OWNER | PRIVATE_FRIEND

cmd = on_command("cmd", permission=mixed, priority=5, block=True)
```

> **注意**：Permission 不支持 `&`（与）运算符。如果需要同时满足多个条件，应在一个 PermissionChecker 函数内部实现。

```python
async def admin_and_vip(bot: Bot, event: Event) -> bool:
    """同时检查多个条件"""
    is_admin = await check_admin(bot, event)
    is_vip = event.get_user_id() in vip_list
    return is_admin and is_vip

ADMIN_VIP = Permission(admin_and_vip)
```

---

## 权限与会话

### 首次触发 vs 后续消息

权限只在事件首次匹配响应器时检查。一旦会话开始（进入 `got()` / `receive()`），后续消息不再检查权限：

```python
from nonebot import on_command
from nonebot.permission import SUPERUSER
from nonebot.params import ArgPlainText

admin = on_command("admin_op", permission=SUPERUSER, priority=1, block=True)


@admin.got("action", prompt="请输入操作：")
async def handle(action: str = ArgPlainText()):
    # 这里不会再次检查 SUPERUSER 权限
    # 但 NoneBot 会确保后续消息来自同一个用户
    await admin.finish(f"执行操作：{action}")
```

### 指定会话中的权限

可以通过 `permission` 参数控制 `got()` / `receive()` 在等待时谁可以继续会话：

```python
from nonebot import on_command
from nonebot.permission import SUPERUSER

cmd = on_command("test", priority=10, block=True)


@cmd.handle()
async def step1():
    await cmd.send("请输入内容：")


@cmd.receive("data")
async def step2():
    await cmd.finish("收到！")
```

---

## 实用示例

### 多级权限管理

```python
from nonebot import on_command
from nonebot.adapters import Bot, Event
from nonebot.permission import SUPERUSER, Permission


async def is_admin(bot: Bot, event: Event) -> bool:
    admin_list = {"100001", "100002"}
    return event.get_user_id() in admin_list


async def is_moderator(bot: Bot, event: Event) -> bool:
    mod_list = {"200001", "200002", "200003"}
    return event.get_user_id() in mod_list


ADMIN = Permission(is_admin)
MODERATOR = Permission(is_moderator)

# 超管命令：仅超级用户
su_cmd = on_command("su", permission=SUPERUSER, priority=1, block=True)

# 管理命令：超级用户 或 管理员
admin_cmd = on_command("manage", permission=SUPERUSER | ADMIN, priority=2, block=True)

# 版主命令：超级用户 或 管理员 或 版主
mod_cmd = on_command("mod", permission=SUPERUSER | ADMIN | MODERATOR, priority=3, block=True)


@su_cmd.handle()
async def handle_su():
    await su_cmd.finish("超管命令已执行")

@admin_cmd.handle()
async def handle_admin():
    await admin_cmd.finish("管理命令已执行")

@mod_cmd.handle()
async def handle_mod():
    await mod_cmd.finish("版主命令已执行")
```

### 动态权限

```python
from nonebot import on_command
from nonebot.adapters import Bot, Event
from nonebot.permission import Permission

enabled_users: set[str] = set()


async def is_enabled(bot: Bot, event: Event) -> bool:
    return event.get_user_id() in enabled_users


ENABLED = Permission(is_enabled)

# 管理命令：添加/移除授权用户
from nonebot.permission import SUPERUSER
from nonebot.params import CommandArg
from nonebot.adapters import Message

auth = on_command("authorize", permission=SUPERUSER, priority=1, block=True)


@auth.handle()
async def handle_auth(args: Message = CommandArg()):
    parts = args.extract_plain_text().strip().split()
    if len(parts) != 2 or parts[0] not in ("add", "remove"):
        await auth.finish("用法：/authorize add|remove <user_id>")

    action, user_id = parts
    if action == "add":
        enabled_users.add(user_id)
        await auth.finish(f"已授权用户 {user_id}")
    else:
        enabled_users.discard(user_id)
        await auth.finish(f"已取消用户 {user_id} 的授权")


# 需要授权才能使用的命令
special = on_command("special", permission=ENABLED | SUPERUSER, priority=5, block=True)


@special.handle()
async def handle_special():
    await special.finish("授权命令已执行！")
```
