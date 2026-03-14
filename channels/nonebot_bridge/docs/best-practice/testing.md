# 配置与测试事件响应器

[NoneBug](https://github.com/nonebot/nonebug) 是 NoneBot 官方测试框架，基于 [pytest](https://docs.pytest.org/) 和 [anyio](https://anyio.readthedocs.io/)，提供对事件响应器、消息收发、API 调用等的模拟测试能力。

## 安装

```bash
# pip
pip install nonebug pytest-asyncio

# poetry
poetry add --group dev nonebug pytest-asyncio

# pdm
pdm add -dG dev nonebug pytest-asyncio
```

## 配置 pytest

### pytest.ini / pyproject.toml

```toml
# pyproject.toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

或 `pytest.ini`：

```ini
[pytest]
asyncio_mode = auto
```

`asyncio_mode = "auto"` 会让所有 `async def test_*` 函数自动以异步方式运行，无需手动加 `@pytest.mark.asyncio`。

## conftest.py 配置

在项目根目录的 `tests/` 文件夹下创建 `conftest.py`：

```python
from pathlib import Path

import pytest
from nonebug import App


# 加载 NoneBot 配置和插件
@pytest.fixture
async def app(tmp_path: Path):
    from nonebot import require

    yield App()
```

如果需要自定义配置：

```python
from pathlib import Path

import pytest
import nonebot
from nonebug import App


@pytest.fixture(scope="session", autouse=True)
def _load_bot():
    """在测试会话开始时初始化 NoneBot"""
    nonebot.init(
        driver="~none",
        command_start={"/"},
        command_sep={"."},
    )
    # 加载要测试的插件
    nonebot.load_plugins("my_bot/plugins")


@pytest.fixture
async def app():
    yield App()
```

## 基础测试

### test_matcher 上下文

`app.test_matcher()` 创建一个测试上下文，在该上下文中可以模拟 Bot、事件、消息发送等：

```python
from nonebug import App


async def test_hello(app: App):
    from my_bot.plugins.hello import hello_handler  # 导入你的 matcher

    async with app.test_matcher(hello_handler) as ctx:
        bot = ctx.create_bot()
        event = make_event("/hello")  # 构造事件
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, "你好！", result=None)
        ctx.should_finished(hello_handler)
```

### 创建 Bot

```python
async with app.test_matcher(matcher) as ctx:
    # 创建默认 Bot
    bot = ctx.create_bot()

    # 创建指定适配器的 Bot
    from nonebot.adapters.onebot.v11 import Adapter, Bot

    bot = ctx.create_bot(base=Bot, adapter=Adapter, self_id="12345")
```

### 构造事件

不同适配器有不同的事件类，以 OneBot V11 为例：

```python
from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent,
    Message,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.adapters.onebot.v11.event import Sender


def make_private_event(text: str, user_id: int = 10001) -> PrivateMessageEvent:
    return PrivateMessageEvent(
        time=1000000,
        self_id=1,
        post_type="message",
        sub_type="friend",
        user_id=user_id,
        message_type="private",
        message_id=1,
        message=Message(text),
        original_message=Message(text),
        raw_message=text,
        font=0,
        sender=Sender(user_id=user_id, nickname="test"),
    )


def make_group_event(
    text: str, user_id: int = 10001, group_id: int = 10000
) -> GroupMessageEvent:
    return GroupMessageEvent(
        time=1000000,
        self_id=1,
        post_type="message",
        sub_type="normal",
        user_id=user_id,
        message_type="group",
        message_id=1,
        message=Message(text),
        original_message=Message(text),
        raw_message=text,
        font=0,
        sender=Sender(user_id=user_id, nickname="test"),
        group_id=group_id,
    )
```

### receive_event

将构造好的事件发送给 matcher：

```python
async with app.test_matcher(matcher) as ctx:
    bot = ctx.create_bot()
    event = make_private_event("/hello")
    ctx.receive_event(bot, event)
```

### should_call_send

断言 matcher 应该发送指定消息：

```python
ctx.should_call_send(
    event,           # 关联的事件
    "你好！",         # 期望发送的消息（str 或 Message）
    result=None,     # send 的返回值
    bot=bot,         # 可选：指定 Bot
)
```

消息匹配支持多种方式：

```python
# 精确匹配字符串
ctx.should_call_send(event, "精确内容", result=None)

# 匹配 Message 对象
from nonebot.adapters.onebot.v11 import Message

ctx.should_call_send(event, Message("消息内容"), result=None)

# 匹配包含 MessageSegment 的消息
from nonebot.adapters.onebot.v11 import MessageSegment

msg = Message([MessageSegment.text("你好"), MessageSegment.face(1)])
ctx.should_call_send(event, msg, result=None)
```

### should_finished

断言 matcher 应该结束（调用了 `matcher.finish()`）：

```python
ctx.should_finished(matcher)
```

## 天气插件测试完整示例

### 被测试的插件 weather.py

```python
from nonebot import on_command
from nonebot.adapters import Message
from nonebot.params import CommandArg

weather = on_command("天气", aliases={"weather"})


@weather.handle()
async def handle_weather(args: Message = CommandArg()):
    city = args.extract_plain_text().strip()
    if not city:
        await weather.finish("请输入城市名，如：/天气 北京")

    # 模拟天气查询
    weather_info = f"{city}：晴，25°C，湿度 40%"
    await weather.finish(weather_info)
```

### 测试文件 test_weather.py

```python
import pytest
from nonebug import App


@pytest.fixture
async def app():
    yield App()


async def test_weather_with_city(app: App):
    """测试正常天气查询"""
    from nonebot.adapters.onebot.v11 import Adapter, Bot

    from my_bot.plugins.weather import weather

    async with app.test_matcher(weather) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=Adapter, self_id="1")
        event = make_private_event("/天气 北京")
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, "北京：晴，25°C，湿度 40%", result=None)
        ctx.should_finished(weather)


async def test_weather_without_city(app: App):
    """测试未提供城市名"""
    from nonebot.adapters.onebot.v11 import Adapter, Bot

    from my_bot.plugins.weather import weather

    async with app.test_matcher(weather) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=Adapter, self_id="1")
        event = make_private_event("/天气")
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, "请输入城市名，如：/天气 北京", result=None)
        ctx.should_finished(weather)
```

## 测试目录结构

```
my_bot/
├── my_bot/
│   └── plugins/
│       └── weather.py
├── tests/
│   ├── conftest.py
│   ├── test_weather.py
│   └── utils.py          # 事件构造辅助函数
├── pyproject.toml
└── bot.py
```

## 运行测试

```bash
# 运行所有测试
pytest tests/ -v

# 运行单个测试文件
pytest tests/test_weather.py -v

# 运行指定测试函数
pytest tests/test_weather.py::test_weather_with_city -v

# 显示详细输出
pytest tests/ -v -s
```
