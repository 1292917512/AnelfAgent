# 会话更新

在 NoneBot 中，事件响应器（Matcher）在等待用户后续输入时，需要确定：

1. 应该响应什么**类型**的事件（type_updater）
2. **谁**可以继续这个会话（permission_updater）

NoneBot 提供了 `type_updater` 和 `permission_updater` 装饰器来自定义这些行为。

## 默认行为

默认情况下：

- **type_updater**：后续等待的事件类型与触发事件类型相同（通常是 `"message"`）
- **permission_updater**：只允许**同一用户**（`USER` 权限）继续响应

```python
from nonebot import on_command

cmd = on_command("ask")

@cmd.handle()
async def first(event: Event):
    await cmd.send("请回答问题：")

@cmd.got("answer")
async def second(answer: str = ArgStr()):
    # 默认只有触发 /ask 的用户能继续
    await cmd.finish(f"你的答案是: {answer}")
```

## type_updater — 更新事件类型

`type_updater` 用于自定义 Matcher 在等待后续事件时应响应的事件类型。

### 基本用法

```python
from nonebot import on_command
from nonebot.adapters import Bot, Event
from nonebot.matcher import Matcher
from nonebot.typing import T_State

cmd = on_command("wait_notice")

@cmd.type_updater
async def update_type(
    bot: Bot,
    event: Event,
    state: T_State,
    matcher: Matcher,
) -> str:
    # 返回新的事件类型
    return "notice"

@cmd.handle()
async def handle():
    await cmd.send("等待通知事件...")

@cmd.receive("notice_event")
async def on_notice(event: Event):
    await cmd.finish(f"收到通知: {event}")
```

### 参数说明

`type_updater` 装饰的函数支持依赖注入，可注入：

| 参数 | 类型 | 说明 |
|------|------|------|
| `bot` | `Bot` | 当前 Bot 实例 |
| `event` | `Event` | 当前事件 |
| `state` | `T_State` | 会话状态 |
| `matcher` | `Matcher` | 当前 Matcher 实例 |

返回值为 `str`，表示新的事件类型。

### 动态切换事件类型

```python
cmd = on_command("dynamic")

@cmd.type_updater
async def update_type(state: T_State) -> str:
    step = state.get("step", 0)
    if step == 0:
        return "message"  # 第一步等待消息
    elif step == 1:
        return "notice"   # 第二步等待通知
    return "message"

@cmd.handle()
async def step1(state: T_State):
    state["step"] = 0
    await cmd.send("请发送一条消息")

@cmd.got("msg")
async def step2(state: T_State):
    state["step"] = 1
    await cmd.send("现在等待一个通知事件...")

@cmd.receive("notice")
async def step3():
    await cmd.finish("流程完成！")
```

## permission_updater — 更新会话权限

`permission_updater` 用于自定义**谁**可以在后续步骤中继续响应此 Matcher。

### USER 权限 vs User 权限

NoneBot 区分两种用户权限：

| 权限 | 说明 | 匹配方式 |
|------|------|---------|
| `USER` | 会话用户权限 | 匹配同一用户 + 同一会话（如同一群） |
| `User` | 用户权限构造器 | 可配置是否检查会话 |

```python
from nonebot.permission import USER, User

# USER: 同一用户 + 同一会话（默认行为）
# 如果用户在群 A 触发，只有群 A 的同一用户能继续

# User: 可自定义
User(users=("user_123",), perm=None)  # 指定用户
```

### 基本用法

```python
from nonebot import on_command
from nonebot.adapters import Bot, Event
from nonebot.permission import Permission, USER

cmd = on_command("open_session")

@cmd.permission_updater
async def update_permission(
    bot: Bot,
    event: Event,
    matcher: Matcher,
) -> Permission:
    # 返回新的 Permission
    return USER  # 保持默认（仅发起者可继续）
```

### 允许任何人继续

```python
from nonebot.permission import Permission, MESSAGE

cmd = on_command("public")

@cmd.permission_updater
async def open_to_all(
    bot: Bot,
    event: Event,
    matcher: Matcher,
) -> Permission:
    # 允许任何发送消息的人继续响应
    return MESSAGE
```

### 多用户会话

实现多个用户可以参与同一个会话：

```python
from nonebot import on_command
from nonebot.adapters import Bot, Event
from nonebot.matcher import Matcher
from nonebot.permission import Permission, User, USER

participants: set[str] = set()

cmd = on_command("team")

@cmd.permission_updater
async def update_perm(
    bot: Bot,
    event: Event,
    matcher: Matcher,
) -> Permission:
    # 将当前用户加入参与者列表
    participants.add(event.get_user_id())
    # 允许所有参与者继续
    return User(users=tuple(participants), perm=None)

@cmd.handle()
async def start(event: Event):
    participants.clear()
    participants.add(event.get_user_id())
    await cmd.send("团队会话已开启，其他人也可以参与！回复 '结束' 结束会话。")

@cmd.got("input")
async def handle_input(event: Event, input_text: str = ArgStr("input")):
    if input_text.strip() == "结束":
        await cmd.finish(f"会话结束，参与者: {participants}")
    participants.add(event.get_user_id())
    await cmd.reject(f"[{event.get_user_id()}]: {input_text}")
```

### 限定特定用户

```python
from nonebot.permission import User

ADMINS = ("admin_001", "admin_002")

cmd = on_command("admin_session")

@cmd.permission_updater
async def admin_only(
    bot: Bot,
    event: Event,
    matcher: Matcher,
) -> Permission:
    return User(users=ADMINS, perm=None)

@cmd.handle()
async def start():
    await cmd.send("管理员会话已开启")

@cmd.got("action")
async def handle(action: str = ArgStr()):
    await cmd.finish(f"执行管理操作: {action}")
```

## 综合示例：多人投票

```python
from nonebot import on_command
from nonebot.adapters import Bot, Event
from nonebot.matcher import Matcher
from nonebot.permission import Permission, MESSAGE
from nonebot.params import ArgStr
from nonebot.typing import T_State

vote_cmd = on_command("vote")

@vote_cmd.permission_updater
async def allow_all(
    bot: Bot,
    event: Event,
    matcher: Matcher,
) -> Permission:
    return MESSAGE

@vote_cmd.handle()
async def start_vote(state: T_State):
    state["votes"] = {"赞成": 0, "反对": 0}
    state["voters"] = set()
    await vote_cmd.send("投票开始！请输入 '赞成' 或 '反对'，输入 '结束投票' 结束。")

@vote_cmd.got("choice")
async def handle_vote(
    event: Event,
    state: T_State,
    choice: str = ArgStr("choice"),
):
    choice = choice.strip()
    user_id = event.get_user_id()

    if choice == "结束投票":
        votes = state["votes"]
        await vote_cmd.finish(
            f"投票结束！\n赞成: {votes['赞成']}\n反对: {votes['反对']}"
        )

    if user_id in state["voters"]:
        await vote_cmd.reject("你已经投过票了！")

    if choice in ("赞成", "反对"):
        state["votes"][choice] += 1
        state["voters"].add(user_id)
        total = sum(state["votes"].values())
        await vote_cmd.reject(f"{user_id} 投了{choice}！当前共 {total} 票。")
    else:
        await vote_cmd.reject("请输入 '赞成' 或 '反对'")
```
