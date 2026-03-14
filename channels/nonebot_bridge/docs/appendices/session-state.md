# 会话状态

`T_State` 是 NoneBot 中用于在同一会话（事件响应器的一次完整处理流程）内传递和存储数据的字典类型。

---

## 基本概念

### T_State 是什么

`T_State` 本质上是一个 `dict[str, Any]`，在一次事件响应器的处理流程中，所有处理函数共享同一个 state 对象。

```python
from nonebot.typing import T_State
```

### 生命周期

state 在以下范围内有效：

```
事件匹配 → 规则检查（可写入 state）→ handle_1(state) → got/receive → handle_2(state) → finish
                                          ↑                                    ↑
                                      同一个 state 对象，数据共享
```

当事件响应器处理结束（`finish()` 或超时），state 即被销毁。

---

## 基本用法

### 存取数据

```python
from nonebot import on_command
from nonebot.typing import T_State

cmd = on_command("state_demo", priority=10, block=True)


@cmd.handle()
async def step1(state: T_State):
    state["user_data"] = "hello"
    state["counter"] = 0
    await cmd.send("数据已存储！")


@cmd.handle()
async def step2(state: T_State):
    data = state["user_data"]       # "hello"
    state["counter"] += 1           # 1
    await cmd.finish(f"数据：{data}，计数：{state['counter']}")
```

### 在规则中预填 state

规则检查函数可以在匹配成功时向 state 写入数据：

```python
from nonebot import on_message
from nonebot.adapters import Event
from nonebot.rule import Rule
from nonebot.typing import T_State


async def parse_math(event: Event, state: T_State) -> bool:
    import re
    text = event.get_plaintext()
    match = re.match(r"^计算\s+(.+)$", text)
    if match:
        state["expression"] = match.group(1)
        return True
    return False


calc = on_message(rule=Rule(parse_math), priority=10, block=True)


@calc.handle()
async def handle(state: T_State):
    expr = state["expression"]
    await calc.finish(f"表达式：{expr}")
```

---

## got() / receive() 与 state

### got() 自动写入 state

`got(key)` 接收到的用户输入会自动以 `key` 为键名存入 state，值为 `Message` 对象：

```python
from nonebot import on_command
from nonebot.typing import T_State
from nonebot.params import ArgPlainText

cmd = on_command("info", priority=10, block=True)


@cmd.got("name", prompt="请输入名字：")
async def get_name(state: T_State, name: str = ArgPlainText()):
    # state["name"] 自动存储了用户输入的 Message 对象
    # ArgPlainText() 是从 state["name"] 中提取纯文本的快捷方式
    await cmd.send(f"你好，{name}！")


@cmd.got("age", prompt="请输入年龄：")
async def get_age(state: T_State, age: str = ArgPlainText()):
    # 此时 state 中同时有 "name" 和 "age"
    name = state["name"].extract_plain_text()  # 从 Message 提取文本
    await cmd.finish(f"{name}，你 {age} 岁了！")
```

### 在 handle() 中预填 got() 的值

如果在 `got()` 之前的 `handle()` 中预先写入了 state 对应的 key，则 `got()` 会跳过提问直接使用已有值：

```python
from nonebot import on_command
from nonebot.adapters import Message
from nonebot.params import CommandArg, ArgPlainText
from nonebot.typing import T_State

weather = on_command("天气", priority=10, block=True)


@weather.handle()
async def check_args(state: T_State, args: Message = CommandArg()):
    """如果用户直接输入了城市名，就跳过 got() 的提问"""
    city = args.extract_plain_text().strip()
    if city:
        state["city"] = args  # 预填 state，got() 将跳过


@weather.got("city", prompt="请输入城市名：")
async def handle_city(city: str = ArgPlainText()):
    await weather.finish(f"正在查询 {city} 的天气...")
```

这个模式非常常用——当用户输入 `/天气 北京` 时直接查询，输入 `/天气` 时才追问。

---

## state 的特殊键

NoneBot 内部会向 state 中写入一些特殊键：

| 键名 | 说明 |
|------|------|
| `_prefix` | `on_command` 解析出的前缀信息字典 |
| `_suffix` | `on_endswith` 匹配信息 |
| `_matched` | `on_regex` / `on_keyword` 等匹配结果 |
| `_matched_dict` | `on_regex` 命名分组结果 |
| `_matched_groups` | `on_regex` 分组结果 |

通常不需要直接访问这些特殊键，而是使用对应的依赖注入参数。

---

## 实用模式

### 跨步骤数据传递

```python
from nonebot import on_command
from nonebot.params import ArgPlainText
from nonebot.typing import T_State

order = on_command("下单", priority=10, block=True)


@order.handle()
async def start(state: T_State):
    state["items"] = []
    await order.send("开始下单！请输入商品名（输入 '完成' 结束）")


@order.got("item", prompt="请输入商品名：")
async def add_item(state: T_State, item: str = ArgPlainText()):
    item = item.strip()
    if item == "完成":
        items = state["items"]
        if not items:
            await order.finish("未添加任何商品，订单已取消。")
        item_list = "\n".join(f"  {i+1}. {name}" for i, name in enumerate(items))
        await order.finish(f"订单确认：\n{item_list}")

    state["items"].append(item)
    count = len(state["items"])
    await order.reject(f"已添加「{item}」（共 {count} 件），继续输入或发送 '完成'：")
```

### 累积用户输入

```python
from nonebot import on_command
from nonebot.params import ArgPlainText
from nonebot.typing import T_State

note = on_command("记事", priority=10, block=True)


@note.handle()
async def start(state: T_State):
    state["lines"] = []
    await note.send("进入记事模式，每行发送一条内容，发送 'end' 结束。")


@note.got("line")
async def handle_line(state: T_State, line: str = ArgPlainText()):
    if line.strip().lower() == "end":
        content = "\n".join(state["lines"])
        await note.finish(f"记事内容：\n{content}")

    state["lines"].append(line.strip())
    await note.reject(f"已记录第 {len(state['lines'])} 条，继续输入或发送 'end' 结束：")
```

### 条件分支处理

```python
from nonebot import on_command
from nonebot.params import ArgPlainText
from nonebot.typing import T_State

survey = on_command("调查", priority=10, block=True)


@survey.got("hobby", prompt="你喜欢什么？（游戏/音乐/运动）")
async def handle_hobby(state: T_State, hobby: str = ArgPlainText()):
    hobby = hobby.strip()
    if hobby not in ("游戏", "音乐", "运动"):
        await survey.reject("请从 游戏/音乐/运动 中选择：")
    state["hobby"] = hobby


@survey.got("detail", prompt="具体是什么呢？")
async def handle_detail(state: T_State, detail: str = ArgPlainText()):
    hobby = state["hobby"]
    await survey.finish(f"了解了！你喜欢{hobby}，特别是{detail.strip()}。")
```

---

## 注意事项

1. **state 仅在单次会话内有效** — 会话结束后 state 被销毁，不会持久化。
2. **got() 存入的是 Message 对象** — 不是纯文本字符串，使用时注意用 `ArgPlainText()` 或 `.extract_plain_text()` 提取。
3. **不要依赖 state 的键顺序** — 它只是一个普通字典。
4. **特殊键以 `_` 开头** — 避免自定义键名以 `_` 开头，防止与内部键冲突。
5. **state 可以存储任意类型** — 不限于字符串，可以存储列表、字典、对象等。
