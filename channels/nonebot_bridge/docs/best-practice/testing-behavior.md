# 测试事件响应与会话操作

在掌握了基础测试方法后，本文档深入介绍如何测试事件匹配规则、权限校验、会话控制流（`finish`/`reject`/`pause`）以及 API 调用。

## 规则测试

### should_pass_rule

断言事件应该通过 matcher 的规则检查：

```python
async with app.test_matcher(matcher) as ctx:
    bot = ctx.create_bot()
    event = make_event("/hello")
    ctx.receive_event(bot, event)
    ctx.should_pass_rule(matcher)
```

### should_not_pass_rule

断言事件不应通过规则检查：

```python
async with app.test_matcher(matcher) as ctx:
    bot = ctx.create_bot()
    event = make_event("not a command")
    ctx.receive_event(bot, event)
    ctx.should_not_pass_rule(matcher)
```

### should_ignore_rule

断言规则检查应被忽略（用于测试优先级等场景）：

```python
async with app.test_matcher(matcher) as ctx:
    bot = ctx.create_bot()
    event = make_event("/hello")
    ctx.receive_event(bot, event)
    ctx.should_ignore_rule(matcher)
```

## 权限测试

### should_pass_permission

断言事件应该通过权限检查：

```python
from nonebot.permission import SUPERUSER

admin_cmd = on_command("admin", permission=SUPERUSER)


async def test_admin_permission(app: App):
    async with app.test_matcher(admin_cmd) as ctx:
        bot = ctx.create_bot()
        event = make_event("/admin", user_id=superuser_id)
        ctx.receive_event(bot, event)
        ctx.should_pass_permission(admin_cmd)
```

### should_not_pass_permission

断言事件不应通过权限检查：

```python
async def test_admin_no_permission(app: App):
    async with app.test_matcher(admin_cmd) as ctx:
        bot = ctx.create_bot()
        event = make_event("/admin", user_id=normal_user_id)
        ctx.receive_event(bot, event)
        ctx.should_not_pass_permission(admin_cmd)
```

### should_ignore_permission

断言权限检查应被忽略：

```python
async with app.test_matcher(matcher) as ctx:
    bot = ctx.create_bot()
    event = make_event("/cmd")
    ctx.receive_event(bot, event)
    ctx.should_ignore_permission(matcher)
```

## 消息发送断言

### should_call_send

断言 matcher 应该向事件来源发送消息：

```python
ctx.should_call_send(
    event,           # 关联事件
    "消息内容",       # 期望的消息（str / Message / MessageSegment）
    result=None,     # send API 的模拟返回值
    bot=bot,         # 可选：指定 Bot 对象
)
```

多次发送：

```python
async with app.test_matcher(matcher) as ctx:
    bot = ctx.create_bot()
    event = make_event("/multi")
    ctx.receive_event(bot, event)
    ctx.should_call_send(event, "第一条消息", result=None)
    ctx.should_call_send(event, "第二条消息", result=None)
    ctx.should_finished(matcher)
```

### should_call_api

断言 matcher 应该调用某个底层 API：

```python
ctx.should_call_api(
    "get_group_member_info",                    # API 名称
    {"group_id": 10000, "user_id": 10001},      # 调用参数
    {"nickname": "test", "card": "测试"},         # 模拟返回值
)
```

完整示例：

```python
async def test_api_call(app: App):
    from my_bot.plugins.info import info_cmd

    async with app.test_matcher(info_cmd) as ctx:
        bot = ctx.create_bot()
        event = make_group_event("/info", user_id=10001, group_id=10000)
        ctx.receive_event(bot, event)

        # 模拟 API 调用返回
        ctx.should_call_api(
            "get_group_member_info",
            {"group_id": 10000, "user_id": 10001, "no_cache": False},
            {"nickname": "Alice", "card": "管理员Alice", "role": "admin"},
        )

        ctx.should_call_send(event, "昵称: Alice\n群名片: 管理员Alice", result=None)
        ctx.should_finished(info_cmd)
```

## 会话控制流断言

### should_finished

断言 matcher 应该结束处理（调用了 `matcher.finish()`）：

```python
ctx.should_finished(matcher)
```

### should_rejected

断言 matcher 应该拒绝当前处理并等待用户重新输入（调用了 `matcher.reject()`）：

```python
# 被测插件
@form.got("name", prompt="请输入你的名字：")
async def handle_name(name: str = ArgPlainText()):
    if len(name) < 2:
        await form.reject("名字太短了，请重新输入：")
    await form.finish(f"你好，{name}！")


# 测试
async def test_reject(app: App):
    from my_bot.plugins.form import form

    async with app.test_matcher(form) as ctx:
        bot = ctx.create_bot()

        # 第一次：触发命令
        event1 = make_event("/form")
        ctx.receive_event(bot, event1)
        ctx.should_call_send(event1, "请输入你的名字：", result=None)
        ctx.should_rejected(form)

        # 第二次：输入过短，被 reject
        event2 = make_event("A")
        ctx.receive_event(bot, event2)
        ctx.should_call_send(event2, "名字太短了，请重新输入：", result=None)
        ctx.should_rejected(form)

        # 第三次：输入合法
        event3 = make_event("Alice")
        ctx.receive_event(bot, event3)
        ctx.should_call_send(event3, "你好，Alice！", result=None)
        ctx.should_finished(form)
```

### should_paused

断言 matcher 应该暂停处理并等待下一条消息（调用了 `matcher.pause()`）：

```python
# 被测插件
@multi_step.handle()
async def step1():
    await multi_step.send("第一步完成，请发送任意内容继续...")
    await multi_step.pause()


@multi_step.handle()
async def step2():
    await multi_step.finish("所有步骤完成！")


# 测试
async def test_pause(app: App):
    from my_bot.plugins.multi import multi_step

    async with app.test_matcher(multi_step) as ctx:
        bot = ctx.create_bot()

        event1 = make_event("/multi")
        ctx.receive_event(bot, event1)
        ctx.should_call_send(event1, "第一步完成，请发送任意内容继续...", result=None)
        ctx.should_paused(multi_step)

        event2 = make_event("继续")
        ctx.receive_event(bot, event2)
        ctx.should_call_send(event2, "所有步骤完成！", result=None)
        ctx.should_finished(multi_step)
```

## 独立测试 vs 集成测试

### 独立测试

只测试单个 matcher 的行为，通过 `app.test_matcher()` 隔离：

```python
async def test_single_matcher(app: App):
    """独立测试：仅测试目标 matcher"""
    from my_bot.plugins.echo import echo

    async with app.test_matcher(echo) as ctx:
        bot = ctx.create_bot()
        event = make_event("/echo hello")
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, "hello", result=None)
        ctx.should_finished(echo)
```

### 集成测试

测试多个 matcher 在同一事件下的交互行为，使用 `app.test_matcher()` 传入多个 matcher：

```python
async def test_multiple_matchers(app: App):
    """集成测试：测试多个 matcher 对同一事件的响应"""
    from my_bot.plugins.echo import echo
    from my_bot.plugins.log import log_handler

    async with app.test_matcher(echo, log_handler) as ctx:
        bot = ctx.create_bot()
        event = make_event("/echo hello")
        ctx.receive_event(bot, event)

        # echo matcher 的行为
        ctx.should_call_send(event, "hello", result=None)
        ctx.should_finished(echo)

        # log_handler 应该也处理了该事件
        ctx.should_pass_rule(log_handler)
```

## 完整示例：密码验证插件测试

### 插件代码 password.py

```python
from nonebot import on_command
from nonebot.adapters import Event
from nonebot.params import ArgPlainText

password_cmd = on_command("setpwd")


@password_cmd.handle()
async def ask_password():
    await password_cmd.send("请输入新密码（6-20位，包含字母和数字）：")


@password_cmd.got("password")
async def validate_password(event: Event, password: str = ArgPlainText()):
    if len(password) < 6:
        await password_cmd.reject("密码太短，至少需要 6 位，请重新输入：")

    if len(password) > 20:
        await password_cmd.reject("密码太长，最多 20 位，请重新输入：")

    has_letter = any(c.isalpha() for c in password)
    has_digit = any(c.isdigit() for c in password)
    if not (has_letter and has_digit):
        await password_cmd.reject("密码必须同时包含字母和数字，请重新输入：")

    user_id = event.get_user_id()
    # save_password(user_id, password)  # 实际业务逻辑
    await password_cmd.finish(f"密码设置成功！")
```

### 测试代码 test_password.py

```python
import pytest
from nonebug import App

from tests.utils import make_private_event


@pytest.fixture
async def app():
    yield App()


async def test_password_success(app: App):
    """测试密码设置成功"""
    from my_bot.plugins.password import password_cmd

    async with app.test_matcher(password_cmd) as ctx:
        bot = ctx.create_bot()

        # 触发命令
        event1 = make_private_event("/setpwd")
        ctx.receive_event(bot, event1)
        ctx.should_call_send(event1, "请输入新密码（6-20位，包含字母和数字）：", result=None)
        ctx.should_rejected(password_cmd)

        # 输入合法密码
        event2 = make_private_event("Hello123")
        ctx.receive_event(bot, event2)
        ctx.should_call_send(event2, "密码设置成功！", result=None)
        ctx.should_finished(password_cmd)


async def test_password_too_short(app: App):
    """测试密码过短"""
    from my_bot.plugins.password import password_cmd

    async with app.test_matcher(password_cmd) as ctx:
        bot = ctx.create_bot()

        event1 = make_private_event("/setpwd")
        ctx.receive_event(bot, event1)
        ctx.should_call_send(event1, "请输入新密码（6-20位，包含字母和数字）：", result=None)
        ctx.should_rejected(password_cmd)

        # 输入过短密码
        event2 = make_private_event("Ab1")
        ctx.receive_event(bot, event2)
        ctx.should_call_send(event2, "密码太短，至少需要 6 位，请重新输入：", result=None)
        ctx.should_rejected(password_cmd)

        # 重新输入合法密码
        event3 = make_private_event("Abc12345")
        ctx.receive_event(bot, event3)
        ctx.should_call_send(event3, "密码设置成功！", result=None)
        ctx.should_finished(password_cmd)


async def test_password_no_digit(app: App):
    """测试密码不含数字"""
    from my_bot.plugins.password import password_cmd

    async with app.test_matcher(password_cmd) as ctx:
        bot = ctx.create_bot()

        event1 = make_private_event("/setpwd")
        ctx.receive_event(bot, event1)
        ctx.should_call_send(event1, "请输入新密码（6-20位，包含字母和数字）：", result=None)
        ctx.should_rejected(password_cmd)

        # 输入无数字密码
        event2 = make_private_event("abcdefgh")
        ctx.receive_event(bot, event2)
        ctx.should_call_send(
            event2, "密码必须同时包含字母和数字，请重新输入：", result=None
        )
        ctx.should_rejected(password_cmd)

        # 输入合法密码
        event3 = make_private_event("abc12345")
        ctx.receive_event(bot, event3)
        ctx.should_call_send(event3, "密码设置成功！", result=None)
        ctx.should_finished(password_cmd)


async def test_password_too_long(app: App):
    """测试密码过长"""
    from my_bot.plugins.password import password_cmd

    async with app.test_matcher(password_cmd) as ctx:
        bot = ctx.create_bot()

        event1 = make_private_event("/setpwd")
        ctx.receive_event(bot, event1)
        ctx.should_call_send(event1, "请输入新密码（6-20位，包含字母和数字）：", result=None)
        ctx.should_rejected(password_cmd)

        # 输入过长密码
        event2 = make_private_event("a1" * 15)
        ctx.receive_event(bot, event2)
        ctx.should_call_send(event2, "密码太长，最多 20 位，请重新输入：", result=None)
        ctx.should_rejected(password_cmd)
```

## 断言方法汇总

| 方法 | 用途 |
|------|------|
| `should_pass_rule(matcher)` | 事件应通过规则 |
| `should_not_pass_rule(matcher)` | 事件不应通过规则 |
| `should_ignore_rule(matcher)` | 规则检查应被忽略 |
| `should_pass_permission(matcher)` | 事件应通过权限 |
| `should_not_pass_permission(matcher)` | 事件不应通过权限 |
| `should_ignore_permission(matcher)` | 权限检查应被忽略 |
| `should_call_send(event, msg, result)` | 应发送消息 |
| `should_call_api(api, data, result)` | 应调用 API |
| `should_finished(matcher)` | matcher 应结束 |
| `should_rejected(matcher)` | matcher 应拒绝并等待重新输入 |
| `should_paused(matcher)` | matcher 应暂停并等待下一条消息 |
