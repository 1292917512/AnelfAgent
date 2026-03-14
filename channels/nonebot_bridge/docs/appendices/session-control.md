# 会话控制

NoneBot 提供了强大的多轮会话控制功能，允许在一个事件响应器中进行多步交互。通过 `got()`、`receive()`、`pause()`、`reject()` 等方法实现。

---

## 核心概念

### 会话流程

一个事件响应器的完整生命周期：

```
用户发送消息 → 规则匹配 → handle() → [got/receive 暂停等待] → 用户再次发送 → 继续处理 → finish()
```

NoneBot 使用装饰器来定义处理流程中的各个步骤，事件响应器会按装饰器的注册顺序依次执行处理函数。

---

## 流程控制方法

### handle()

注册一个处理函数，按顺序执行：

```python
from nonebot import on_command

cmd = on_command("multi", priority=10, block=True)


@cmd.handle()
async def step1():
    """第一步"""
    await cmd.send("第一步完成！")


@cmd.handle()
async def step2():
    """第二步，紧接着执行"""
    await cmd.finish("第二步完成，结束！")
```

### got()

暂停当前会话，等待用户输入并将输入存储到 `state` 中：

```python
from nonebot import on_command
from nonebot.adapters import Message
from nonebot.params import ArgPlainText

cmd = on_command("ask", priority=10, block=True)


@cmd.got("name", prompt="请输入你的名字：")
async def handle_name(name: str = ArgPlainText()):
    await cmd.finish(f"你好，{name}！")
```

#### got() 参数说明

| 参数 | 类型 | 说明 |
|------|------|------|
| `key` | `str` | 存储到 state 中的键名 |
| `prompt` | `str \| Message \| MessageSegment \| MessageTemplate \| None` | 提示用户的消息 |
| `parameterless` | `Iterable[Any] \| None` | 附加的无参依赖 |

#### 获取 got() 的输入

```python
from nonebot.params import Arg, ArgStr, ArgPlainText

@cmd.got("city", prompt="请输入城市名：")
async def handle(
    city: Message = Arg(),               # 原始 Message 对象
    city_str: str = ArgStr(),             # 消息的字符串表示
    city_text: str = ArgPlainText(),      # 消息的纯文本（去掉非文本段）
):
    pass
```

指定获取某个 key 的参数：

```python
@cmd.got("city", prompt="请输入城市名：")
async def handle(
    city_text: str = ArgPlainText("city"),     # 指定获取 "city" 键
):
    pass
```

### receive()

暂停当前会话，等待用户发送下一条消息（作为完整事件接收）：

```python
from nonebot import on_command
from nonebot.adapters import Event
from nonebot.params import Received

cmd = on_command("confirm", priority=10, block=True)


@cmd.handle()
async def step1():
    await cmd.send("请发送需要确认的内容：")


@cmd.receive("content")
async def step2(event: Event = Received("content")):
    text = event.get_plaintext()
    await cmd.send(f"你发送了：{text}\n确认吗？请回复 是/否")


@cmd.receive("confirm")
async def step3(event: Event = Received("confirm")):
    if event.get_plaintext().strip() == "是":
        await cmd.finish("已确认！")
    else:
        await cmd.finish("已取消。")
```

### pause()

暂停当前会话，等待用户下一条消息后 **继续执行当前处理函数的后续代码**：

```python
from nonebot import on_command

cmd = on_command("step", priority=10, block=True)


@cmd.handle()
async def handle():
    await cmd.send("第一步完成，请发送任意消息继续...")
    await cmd.pause()


@cmd.handle()
async def handle2():
    await cmd.finish("第二步完成！")
```

> **注意**：`pause()` 会抛出异常中断当前函数，下一条消息会触发下一个 `handle()` 装饰的函数。

### reject()

拒绝当前 `got()` 或 `receive()` 接收到的输入，要求用户重新输入：

```python
from nonebot import on_command
from nonebot.params import ArgPlainText

cmd = on_command("age", priority=10, block=True)


@cmd.got("age", prompt="请输入你的年龄：")
async def handle_age(age: str = ArgPlainText()):
    if not age.isdigit() or not (0 < int(age) < 150):
        await cmd.reject("年龄无效，请重新输入一个合理的数字：")
    await cmd.finish(f"你的年龄是 {age} 岁。")
```

#### reject 变体

```python
# reject() - 使用 got/receive 中定义的 prompt 重新提问
await cmd.reject()

# reject("提示文本") - 使用自定义提示重新提问
await cmd.reject("输入不正确，请重新输入：")

# reject_arg("key", "提示") - 拒绝指定 key 的输入
await cmd.reject_arg("name", "名字不能为空，请重新输入：")

# reject_receive("id", "提示") - 拒绝指定 receive 的输入
await cmd.reject_receive("content", "内容不正确，请重新发送：")
```

### send()

发送消息但不结束会话：

```python
await cmd.send("处理中...")
```

### finish()

发送消息并结束当前会话：

```python
await cmd.finish("完成！")
await cmd.finish()  # 不发送消息直接结束
```

### skip()

跳过当前处理函数，执行下一个：

```python
from nonebot import on_command
from nonebot.params import ArgPlainText

cmd = on_command("test", priority=10, block=True)


@cmd.handle()
async def step1():
    await cmd.send("跳过下一步...")


@cmd.handle()
async def step2():
    await cmd.skip()  # 跳过此函数


@cmd.handle()
async def step3():
    await cmd.finish("直接到第三步了！")
```

---

## 完整示例

### 多步表单：用户注册

```python
from nonebot import on_command
from nonebot.params import ArgPlainText
from nonebot.typing import T_State

register = on_command("注册", priority=10, block=True)


@register.handle()
async def start():
    await register.send("欢迎注册！请按提示填写信息。")


@register.got("username", prompt="请输入用户名（3-20个字符）：")
async def get_username(username: str = ArgPlainText()):
    username = username.strip()
    if len(username) < 3 or len(username) > 20:
        await register.reject("用户名长度需要在 3-20 个字符之间，请重新输入：")


@register.got("age", prompt="请输入年龄：")
async def get_age(age: str = ArgPlainText()):
    if not age.strip().isdigit():
        await register.reject("请输入有效的数字：")
    age_num = int(age.strip())
    if age_num < 1 or age_num > 150:
        await register.reject("年龄范围 1-150，请重新输入：")


@register.got("email", prompt="请输入邮箱地址：")
async def get_email(email: str = ArgPlainText()):
    import re
    if not re.match(r"^[\w\.-]+@[\w\.-]+\.\w+$", email.strip()):
        await register.reject("邮箱格式不正确，请重新输入：")


@register.got("confirm", prompt="确认注册吗？（是/否）")
async def confirm(
    state: T_State,
    confirm_text: str = ArgPlainText("confirm"),
):
    if confirm_text.strip() != "是":
        await register.finish("已取消注册。")

    username = state["username"].extract_plain_text()
    age = state["age"].extract_plain_text()
    email = state["email"].extract_plain_text()

    await register.finish(
        f"注册成功！\n"
        f"用户名：{username}\n"
        f"年龄：{age}\n"
        f"邮箱：{email}"
    )
```

### 多轮对话：猜数字游戏

```python
import random
from nonebot import on_command
from nonebot.params import ArgPlainText
from nonebot.typing import T_State

guess = on_command("猜数字", priority=10, block=True)


@guess.handle()
async def start(state: T_State):
    state["answer"] = random.randint(1, 100)
    state["attempts"] = 0
    await guess.send("我想了一个 1-100 的数字，来猜猜看！")


@guess.got("number", prompt="请输入你猜的数字：")
async def handle_guess(state: T_State, number: str = ArgPlainText()):
    if not number.strip().isdigit():
        await guess.reject("请输入一个有效的数字：")

    num = int(number.strip())
    answer = state["answer"]
    state["attempts"] += 1

    if num < answer:
        await guess.reject(f"太小了！已猜 {state['attempts']} 次，再试试：")
    elif num > answer:
        await guess.reject(f"太大了！已猜 {state['attempts']} 次，再试试：")
    else:
        await guess.finish(
            f"恭喜你猜对了！答案就是 {answer}，你一共猜了 {state['attempts']} 次。"
        )
```

### 带超时的会话

```python
from nonebot import on_command
from nonebot.params import ArgPlainText

cmd = on_command("timeout_test", priority=10, block=True)


@cmd.got("input", prompt="请在 30 秒内输入内容：")
async def handle(input_text: str = ArgPlainText()):
    await cmd.finish(f"你输入了：{input_text}")
```

> **注意**：NoneBot 默认的会话超时由 `SESSION_EXPIRE_TIMEOUT` 配置控制（默认 120 秒）。超时后会话自动结束。

### 使用 receive 处理图片

```python
from nonebot import on_command
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.params import Received

img_cmd = on_command("识图", priority=10, block=True)


@img_cmd.handle()
async def ask_image():
    await img_cmd.send("请发送你要识别的图片：")


@img_cmd.receive("image")
async def handle_image(event: Event = Received("image")):
    message = event.get_message()
    images = [seg for seg in message if seg.type == "image"]
    if not images:
        await img_cmd.reject_receive("image", "没有检测到图片，请重新发送：")

    image_url = images[0].data.get("url", "")
    await img_cmd.finish(f"收到图片：{image_url}\n正在识别中...")
```

---

## 流程控制速查

| 方法 | 作用 | 发送消息 | 结束会话 |
|------|------|---------|---------|
| `handle()` | 注册处理函数 | — | — |
| `got(key, prompt)` | 等待用户输入并存储 | 发送 prompt | 否 |
| `receive(id)` | 等待用户下一条消息 | 可选 prompt | 否 |
| `send(msg)` | 发送消息 | 是 | 否 |
| `finish(msg)` | 发送消息并结束 | 是 | 是 |
| `pause(prompt)` | 暂停等待下一条消息 | 可选 prompt | 否 |
| `reject(prompt)` | 拒绝输入，要求重新输入 | 可选 prompt | 否 |
| `reject_arg(key, prompt)` | 拒绝指定 key 的输入 | 可选 prompt | 否 |
| `reject_receive(id, prompt)` | 拒绝指定 receive 的输入 | 可选 prompt | 否 |
| `skip()` | 跳过当前处理函数 | 否 | 否 |
