# 编写适配器

本文档介绍如何为 NoneBot 编写自定义适配器，将 NoneBot 连接到不同的聊天平台。

## 概述

NoneBot 适配器负责：

1. 与平台建立连接（WebSocket、HTTP 等）
2. 接收并转换平台事件为 NoneBot 事件
3. 将 NoneBot 的 API 调用转换为平台 API 请求
4. 定义消息段类型和消息构造方式

## 适配器组织结构

NoneBot 适配器使用 [命名空间包](https://peps.python.org/pep-0420/) 组织，统一放在 `nonebot.adapters` 命名空间下：

```
nonebot-adapter-xxx/
├── nonebot/
│   └── adapters/
│       └── xxx/
│           ├── __init__.py      # 导出公共 API
│           ├── adapter.py       # 适配器主类
│           ├── bot.py           # Bot 类
│           ├── config.py        # 配置类
│           ├── event.py         # 事件类
│           ├── message.py       # 消息类
│           └── utils.py         # 工具函数
├── pyproject.toml
├── README.md
└── LICENSE
```

> **重要**：`nonebot/` 和 `nonebot/adapters/` 目录下 **不能** 有 `__init__.py` 文件，否则会破坏命名空间包机制。

### 使用 nb-cli 创建

```bash
nb adapter create
```

按照向导填写适配器名称等信息后，会自动生成项目骨架。

## 适配器组件

### Log（日志）

使用 `nonebot.utils.logger_wrapper` 创建适配器专用日志记录器：

```python
from nonebot.utils import logger_wrapper

log = logger_wrapper("XXX")

# 使用
log("DEBUG", "Received event")
log("INFO", "Connected to server")
log("ERROR", f"Connection failed: {e}")
```

`logger_wrapper` 会自动添加适配器名称前缀，便于在日志中区分不同适配器的输出。

### Config（配置）

使用 Pydantic `BaseModel` 定义适配器配置：

```python
from pydantic import BaseModel, Field

class Config(BaseModel):
    xxx_api_url: str = "https://api.example.com"
    xxx_access_token: str = ""
    xxx_secret: str = ""
    xxx_reconnect_interval: int = Field(default=3, ge=1)
    xxx_max_retry: int = Field(default=5, ge=0)
```

在适配器中加载配置：

```python
from nonebot import get_driver

config = Config.model_validate(get_driver().config.model_dump())
```

### Adapter（适配器主类）

适配器主类继承 `nonebot.adapters.Adapter`（即 `BaseAdapter`），负责管理与平台的连接。

#### 基本结构

```python
from nonebot.adapters import Adapter as BaseAdapter
from nonebot.drivers import Driver

from .config import Config
from .utils import log

class Adapter(BaseAdapter):
    @classmethod
    def get_name(cls) -> str:
        return "XXX"

    def __init__(self, driver: Driver, **kwargs):
        super().__init__(driver, **kwargs)
        self.config = Config.model_validate(driver.config.model_dump())
        self.setup()

    def setup(self):
        # 注册启动和关闭钩子
        self.driver.on_startup(self._start)
        self.driver.on_shutdown(self._stop)
```

#### 客户端 WebSocket 连接

适配器作为客户端主动连接到平台的 WebSocket 服务器：

```python
from nonebot.drivers import (
    ForwardDriver,
    URL,
    WebSocket,
    WebSocketClientMixin,
)

class Adapter(BaseAdapter):
    def __init__(self, driver: Driver, **kwargs):
        super().__init__(driver, **kwargs)
        self.config = Config.model_validate(driver.config.model_dump())
        self.task: Optional[asyncio.Task] = None
        self.setup()

    def setup(self):
        if not isinstance(self.driver, WebSocketClientMixin):
            raise RuntimeError(
                f"Current driver {self.config.driver} does not support WebSocket client."
                " Please use a driver that supports WebSocket client, such as 'httpx+websockets'."
            )
        self.driver.on_startup(self._start)
        self.driver.on_shutdown(self._stop)

    async def _start(self):
        self.task = asyncio.create_task(self._forward_ws())

    async def _stop(self):
        if self.task:
            self.task.cancel()

    async def _forward_ws(self):
        while True:
            try:
                url = URL(self.config.xxx_ws_url)
                async with self.websocket(url) as ws:
                    log("INFO", f"Connected to {url}")

                    # 认证
                    bot = await self._authenticate(ws)
                    self.bot_connect(bot)

                    try:
                        while True:
                            data = await ws.receive()
                            event = self._parse_event(data)
                            if event:
                                asyncio.create_task(bot.handle_event(event))
                    finally:
                        self.bot_disconnect(bot)
            except Exception as e:
                log("ERROR", f"WebSocket error: {e}")
                await asyncio.sleep(self.config.xxx_reconnect_interval)

    async def _authenticate(self, ws: WebSocket) -> "Bot":
        # 发送认证信息
        await ws.send(json.dumps({"token": self.config.xxx_access_token}))
        resp = json.loads(await ws.receive())
        bot_id = resp["bot_id"]
        return Bot(self, bot_id)
```

#### 服务端 WebSocket/HTTP 连接

适配器作为服务端，接收平台的 WebSocket 连接或 HTTP 回调：

```python
from nonebot.drivers import (
    ReverseDriver,
    ASGIMixin,
    Request,
    Response,
    WebSocket as BaseWebSocket,
)

class Adapter(BaseAdapter):
    def __init__(self, driver: Driver, **kwargs):
        super().__init__(driver, **kwargs)
        self.config = Config.model_validate(driver.config.model_dump())
        self.setup()

    def setup(self):
        if not isinstance(self.driver, ASGIMixin):
            raise RuntimeError(
                "Current driver does not support ASGI."
                " Please use a reverse driver, such as 'fastapi'."
            )
        # 注册 HTTP 回调路由
        self.driver.on_startup(self._register_routes)

    async def _register_routes(self):
        # HTTP POST 回调
        @self.driver.server_app.post("/xxx/callback")
        async def handle_http(request: Request):
            data = request.content
            if not self._verify_signature(request, data):
                return Response(403, content="Invalid signature")

            event = self._parse_event(json.loads(data))
            bot = self.bots.get(event.self_id)
            if bot:
                asyncio.create_task(bot.handle_event(event))
            return Response(200, content="ok")

        # WebSocket 连接
        @self.driver.server_app.websocket("/xxx/ws")
        async def handle_ws(ws: BaseWebSocket):
            await ws.accept()
            bot_id = await self._ws_authenticate(ws)
            bot = Bot(self, bot_id)
            self.bot_connect(bot)

            try:
                while True:
                    data = await ws.receive()
                    event = self._parse_event(data)
                    if event:
                        asyncio.create_task(bot.handle_event(event))
            except Exception:
                pass
            finally:
                self.bot_disconnect(bot)

    def _verify_signature(self, request: Request, data: bytes) -> bool:
        signature = request.headers.get("X-Signature")
        expected = hmac.new(
            self.config.xxx_secret.encode(),
            data,
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(signature or "", expected)
```

#### bot_connect / bot_disconnect

当 Bot 上线或下线时，必须调用这两个方法通知 NoneBot 框架：

```python
# Bot 上线
bot = Bot(self, bot_id)
self.bot_connect(bot)
log("INFO", f"Bot {bot_id} connected")

# Bot 下线
self.bot_disconnect(bot)
log("INFO", f"Bot {bot_id} disconnected")
```

#### _call_api

实现平台 API 调用。根据连接方式不同，有 HTTP 和 WebSocket 两种实现：

**HTTP 方式**：

```python
class Adapter(BaseAdapter):
    async def _call_api(self, bot: "Bot", api: str, **data) -> Any:
        url = f"{self.config.xxx_api_url}/{api}"
        headers = {
            "Authorization": f"Bearer {self.config.xxx_access_token}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=data, headers=headers)
            result = resp.json()

            if result.get("retcode") != 0:
                raise ActionFailed(
                    retcode=result["retcode"],
                    message=result.get("message", ""),
                )
            return result.get("data")
```

**WebSocket 方式**：

```python
class Adapter(BaseAdapter):
    def __init__(self, driver: Driver, **kwargs):
        super().__init__(driver, **kwargs)
        self._ws: Optional[WebSocket] = None
        self._api_response: dict[str, asyncio.Future] = {}

    async def _call_api(self, bot: "Bot", api: str, **data) -> Any:
        if not self._ws:
            raise RuntimeError("WebSocket not connected")

        echo = str(uuid.uuid4())
        payload = {"action": api, "params": data, "echo": echo}

        future = asyncio.get_event_loop().create_future()
        self._api_response[echo] = future

        try:
            await self._ws.send(json.dumps(payload))
            result = await asyncio.wait_for(future, timeout=30)
        finally:
            self._api_response.pop(echo, None)

        if result.get("retcode") != 0:
            raise ActionFailed(
                retcode=result["retcode"],
                message=result.get("message", ""),
            )
        return result.get("data")
```

### Bot（机器人类）

Bot 类继承 `nonebot.adapters.Bot`（即 `BaseBot`），代表一个机器人实例：

```python
from nonebot.adapters import Bot as BaseBot
from nonebot.adapters import Event, Message, MessageSegment

class Bot(BaseBot):
    async def send(
        self,
        event: Event,
        message: str | Message | MessageSegment,
        **kwargs,
    ) -> Any:
        if isinstance(message, str):
            message = Message(message)
        elif isinstance(message, MessageSegment):
            message = Message([message])

        # 根据事件类型选择发送目标
        if hasattr(event, "group_id"):
            return await self.call_api(
                "send_group_message",
                group_id=event.group_id,
                message=message.to_dict(),
            )
        else:
            return await self.call_api(
                "send_private_message",
                user_id=event.get_user_id(),
                message=message.to_dict(),
            )

    async def handle_event(self, event: Event) -> None:
        await handle_event(self, event)
```

`send` 方法是最核心的方法，用于向用户或群组发送消息。NoneBot 的 `matcher.send()` / `matcher.finish()` 最终都会调用此方法。

### Event（事件类）

事件类继承 `nonebot.adapters.Event`（即 `BaseEvent`），需要实现以下抽象方法：

```python
from nonebot.adapters import Event as BaseEvent
from nonebot.utils import escape_tag

class Event(BaseEvent):
    # 通用字段
    post_type: str
    timestamp: int

    def get_type(self) -> str:
        """获取事件类型，如 'message', 'notice', 'request', 'meta_event'"""
        return self.post_type

    def get_event_name(self) -> str:
        """获取事件名称，用于日志显示"""
        return self.post_type

    def get_event_description(self) -> str:
        """获取事件描述，用于日志显示"""
        return escape_tag(str(self.model_dump()))

    def get_message(self) -> "Message":
        """获取事件消息（仅消息事件需要实现）"""
        raise ValueError("Event has no message")

    def get_user_id(self) -> str:
        """获取触发事件的用户 ID"""
        raise ValueError("Event has no user_id")

    def get_session_id(self) -> str:
        """获取会话 ID，用于标识唯一会话"""
        raise ValueError("Event has no session_id")

    def is_tome(self) -> bool:
        """判断事件是否与 Bot 相关（如 @机器人）"""
        return False
```

#### 事件类型示例

**心跳事件**：

```python
class HeartbeatEvent(Event):
    post_type: str = "meta_event"
    meta_event_type: str = "heartbeat"
    interval: int

    def get_type(self) -> str:
        return "meta_event"

    def get_event_name(self) -> str:
        return "meta_event.heartbeat"

    def get_event_description(self) -> str:
        return f"Heartbeat (interval={self.interval}ms)"

    def get_user_id(self) -> str:
        raise ValueError("HeartbeatEvent has no user_id")

    def get_session_id(self) -> str:
        raise ValueError("HeartbeatEvent has no session_id")
```

**消息事件**：

```python
from typing import Optional

class MessageEvent(Event):
    post_type: str = "message"
    message_type: str
    message_id: str
    user_id: str
    message: Message
    raw_message: str

    def get_type(self) -> str:
        return "message"

    def get_event_name(self) -> str:
        return f"message.{self.message_type}"

    def get_event_description(self) -> str:
        return (
            f"Message {self.message_id} from {self.user_id}: "
            f"{escape_tag(self.raw_message[:50])}"
        )

    def get_message(self) -> Message:
        return self.message

    def get_user_id(self) -> str:
        return self.user_id

    def get_session_id(self) -> str:
        return self.user_id

    def is_tome(self) -> bool:
        return self._is_tome

class PrivateMessageEvent(MessageEvent):
    message_type: str = "private"

    def get_event_name(self) -> str:
        return "message.private"

    def get_session_id(self) -> str:
        return f"private_{self.user_id}"

class GroupMessageEvent(MessageEvent):
    message_type: str = "group"
    group_id: str

    def get_event_name(self) -> str:
        return "message.group"

    def get_session_id(self) -> str:
        return f"group_{self.group_id}_{self.user_id}"
```

**加群事件**：

```python
class JoinRoomEvent(Event):
    post_type: str = "notice"
    notice_type: str = "group_increase"
    group_id: str
    user_id: str
    operator_id: Optional[str] = None

    def get_type(self) -> str:
        return "notice"

    def get_event_name(self) -> str:
        return "notice.group_increase"

    def get_event_description(self) -> str:
        return f"User {self.user_id} joined group {self.group_id}"

    def get_user_id(self) -> str:
        return self.user_id

    def get_session_id(self) -> str:
        return f"group_{self.group_id}_{self.user_id}"
```

**加好友请求事件**：

```python
class ApplyAddFriendEvent(Event):
    post_type: str = "request"
    request_type: str = "friend"
    user_id: str
    comment: str = ""
    flag: str

    def get_type(self) -> str:
        return "request"

    def get_event_name(self) -> str:
        return "request.friend"

    def get_event_description(self) -> str:
        return f"Friend request from {self.user_id}: {self.comment}"

    def get_user_id(self) -> str:
        return self.user_id

    def get_session_id(self) -> str:
        return f"request_{self.user_id}"
```

#### 事件转换

在适配器中将平台原始数据转换为事件对象：

```python
class Adapter(BaseAdapter):
    def _parse_event(self, data: str | bytes) -> Optional[Event]:
        payload = json.loads(data) if isinstance(data, (str, bytes)) else data
        post_type = payload.get("post_type")

        try:
            if post_type == "meta_event":
                return HeartbeatEvent.model_validate(payload)
            elif post_type == "message":
                msg_type = payload.get("message_type")
                if msg_type == "private":
                    return PrivateMessageEvent.model_validate(payload)
                elif msg_type == "group":
                    return GroupMessageEvent.model_validate(payload)
            elif post_type == "notice":
                return JoinRoomEvent.model_validate(payload)
            elif post_type == "request":
                return ApplyAddFriendEvent.model_validate(payload)
        except Exception as e:
            log("WARNING", f"Failed to parse event: {e}")
            return None

        log("DEBUG", f"Unknown event type: {post_type}")
        return None
```

### Message（消息类）

#### MessageSegment（消息段）

消息段是消息的基本组成单元，继承 `nonebot.adapters.MessageSegment`：

```python
from nonebot.adapters import MessageSegment as BaseMessageSegment

class MessageSegment(BaseMessageSegment["Message"]):
    @classmethod
    def get_message_class(cls) -> type["Message"]:
        return Message

    def __str__(self) -> str:
        if self.is_text():
            return self.data.get("text", "")
        return f"[{self.type}]"

    def is_text(self) -> bool:
        return self.type == "text"

    # 构造方法
    @staticmethod
    def text(text: str) -> "MessageSegment":
        return MessageSegment(type="text", data={"text": text})

    @staticmethod
    def image(url: str) -> "MessageSegment":
        return MessageSegment(type="image", data={"url": url})

    @staticmethod
    def image_file(file: bytes) -> "MessageSegment":
        import base64
        b64 = base64.b64encode(file).decode()
        return MessageSegment(type="image", data={"file": f"base64://{b64}"})

    @staticmethod
    def at(user_id: str) -> "MessageSegment":
        return MessageSegment(type="at", data={"user_id": user_id})

    @staticmethod
    def reply(message_id: str) -> "MessageSegment":
        return MessageSegment(type="reply", data={"message_id": message_id})

    @staticmethod
    def face(face_id: int) -> "MessageSegment":
        return MessageSegment(type="face", data={"id": str(face_id)})

    @staticmethod
    def record(url: str) -> "MessageSegment":
        return MessageSegment(type="record", data={"url": url})

    @staticmethod
    def video(url: str) -> "MessageSegment":
        return MessageSegment(type="video", data={"url": url})
```

#### Message（消息）

消息类是消息段的有序容器，继承 `nonebot.adapters.Message`：

```python
from typing import Iterable, Union
from nonebot.adapters import Message as BaseMessage

class Message(BaseMessage[MessageSegment]):
    @classmethod
    def get_segment_class(cls) -> type[MessageSegment]:
        return MessageSegment

    @staticmethod
    def _construct(msg: str) -> Iterable[MessageSegment]:
        """将字符串解析为消息段列表"""
        import re

        # 匹配 CQ 码格式 [CQ:type,key=value,...]
        pattern = r"\[CQ:(\w+)(?:,([^\]]*))?\]"

        last_end = 0
        for match in re.finditer(pattern, msg):
            # 前面的文本部分
            if match.start() > last_end:
                yield MessageSegment.text(msg[last_end:match.start()])

            # 解析 CQ 码
            seg_type = match.group(1)
            params = {}
            if match.group(2):
                for param in match.group(2).split(","):
                    key, _, value = param.partition("=")
                    params[key] = value

            yield MessageSegment(type=seg_type, data=params)
            last_end = match.end()

        # 剩余的文本
        if last_end < len(msg):
            yield MessageSegment.text(msg[last_end:])

    def to_dict(self) -> list[dict]:
        """序列化为可发送的格式"""
        return [
            {"type": seg.type, "data": seg.data}
            for seg in self
        ]
```

#### 使用示例

```python
# 构造消息
msg = Message()
msg += MessageSegment.at("12345")
msg += MessageSegment.text(" 你好！")
msg += MessageSegment.image("https://example.com/img.png")

# 从字符串构造
msg = Message("Hello [CQ:at,qq=12345] World")

# 提取纯文本
text = msg.extract_plain_text()

# 过滤特定类型
images = msg["image"]  # 获取所有图片消息段
```

## 测试适配器

使用 `nonebug` 测试适配器功能：

```python
import pytest
from nonebug import App
from nonebot.adapters.xxx import Adapter, Bot, Message, MessageEvent

@pytest.fixture
async def app():
    nonebot.init()
    driver = nonebot.get_driver()
    driver.register_adapter(Adapter)
    yield App()

@pytest.mark.asyncio
async def test_send_message(app: App):
    async with app.test_api() as ctx:
        bot = ctx.create_bot(base=Bot, self_id="test_bot")
        ctx.should_call_api(
            "send_private_message",
            {"user_id": "12345", "message": [{"type": "text", "data": {"text": "hello"}}]},
            {"message_id": "1"},
        )
        await bot.send_msg(user_id="12345", message=Message("hello"))
```

## 发布适配器

适配器的发布流程与插件类似，但提交到 [适配器商店](https://nonebot.dev/store/adapters)：

1. 发布到 PyPI（包名格式：`nonebot-adapter-xxx`）
2. 前往 NoneBot 商店提交适配器信息
3. 等待审核合并

```toml
# pyproject.toml 示例
[project]
name = "nonebot-adapter-xxx"
version = "0.1.0"
description = "NoneBot XXX Adapter"
requires-python = ">=3.10"
dependencies = [
    "nonebot2>=2.3.0",
]
```
