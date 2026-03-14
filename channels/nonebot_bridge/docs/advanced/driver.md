# 选择驱动器

驱动器（Driver）是 NoneBot 运行的基础引擎，负责处理网络通信。不同的适配器可能需要不同类型的驱动器来支持其功能。

## 驱动器类型

NoneBot 将驱动器分为三种能力类型：

| 类型 | 基类 Mixin | 说明 |
|------|-----------|------|
| 服务端 | `ASGIMixin` | 提供 ASGI 应用，可接收外部请求（如 WebHook） |
| HTTP 客户端 | `HTTPClientMixin` | 提供 HTTP 客户端，可主动发起 HTTP 请求 |
| WebSocket 客户端 | `WebSocketClientMixin` | 提供 WebSocket 客户端，可主动建立 WebSocket 连接 |

## 内置驱动器

| 驱动器 | 模块名 | 类型 | 依赖 |
|--------|--------|------|------|
| FastAPI | `~fastapi` | 服务端 (ASGIMixin) | `nonebot2[fastapi]` |
| Quart | `~quart` | 服务端 (ASGIMixin) | `nonebot2[quart]` |
| httpx | `~httpx` | HTTP 客户端 (HTTPClientMixin) | `nonebot2[httpx]` |
| aiohttp | `~aiohttp` | HTTP + WebSocket 客户端 | `nonebot2[aiohttp]` |
| websockets | `~websockets` | WebSocket 客户端 (WebSocketClientMixin) | `nonebot2[websockets]` |
| None | `~none` | 无网络能力 | 无 |

## 配置驱动器

### 通过环境变量

在 `.env` 或 `.env.*` 文件中配置 `DRIVER` 环境变量：

```dotenv
# 单一驱动器
DRIVER=~fastapi

# 组合驱动器（使用 + 连接）
DRIVER=~fastapi+~httpx
DRIVER=~fastapi+~aiohttp
DRIVER=~fastapi+~httpx+~websockets
```

### 通过代码配置

```python
import nonebot

nonebot.init(driver="~fastapi+~httpx")
```

## 驱动器能力详解

### ASGIMixin — 服务端驱动器

服务端驱动器提供 ASGI 应用接口，适用于接收外部平台推送的事件（WebHook 模式）。

```python
from nonebot.drivers import ASGIMixin

# 获取当前驱动器并检查能力
driver = nonebot.get_driver()
assert isinstance(driver, ASGIMixin)

# 获取 ASGI 应用（如 FastAPI app）
asgi_app = driver.asgi
```

FastAPI 驱动器额外提供：

```python
from nonebot import get_app, get_asgi

# 获取 FastAPI 应用实例
app = get_app()  # FastAPI 实例
asgi = get_asgi()  # 等同于 get_app()
```

### HTTPClientMixin — HTTP 客户端

HTTP 客户端驱动器提供发起 HTTP 请求的能力。

```python
from nonebot.drivers import HTTPClientMixin, Request, Response

driver = nonebot.get_driver()
assert isinstance(driver, HTTPClientMixin)

# 创建请求
request = Request(
    method="GET",
    url="https://api.example.com/data",
    headers={"Authorization": "Bearer token"},
    timeout=30,
)

# 发送请求
response: Response = await driver.request(request)
print(response.status_code)  # 200
print(response.content)      # bytes
print(response.text)         # str（自动解码）
print(response.json())       # dict（自动解析 JSON）
```

POST 请求示例：

```python
request = Request(
    method="POST",
    url="https://api.example.com/submit",
    headers={"Content-Type": "application/json"},
    content='{"key": "value"}',
)
response = await driver.request(request)
```

### WebSocketClientMixin — WebSocket 客户端

WebSocket 客户端驱动器提供主动建立 WebSocket 连接的能力。

```python
from nonebot.drivers import WebSocketClientMixin, Request

driver = nonebot.get_driver()
assert isinstance(driver, WebSocketClientMixin)

request = Request(
    method="GET",
    url="ws://localhost:8080/ws",
    headers={"Authorization": "Bearer token"},
)

async with driver.websocket(request) as ws:
    # 发送消息
    await ws.send("hello")
    await ws.send_bytes(b"\x00\x01")

    # 接收消息
    data = await ws.receive()
    data_bytes = await ws.receive_bytes()

    print(data)
```

## 组合驱动器

NoneBot 支持通过 `+` 号组合多个驱动器，从而同时获得多种能力。组合后的驱动器会继承所有子驱动器的 Mixin 能力。

### 常用组合

```dotenv
# 服务端 + HTTP 客户端（最常用）
DRIVER=~fastapi+~httpx

# 服务端 + 完整客户端（HTTP + WebSocket）
DRIVER=~fastapi+~aiohttp

# 服务端 + HTTP 客户端 + WebSocket 客户端
DRIVER=~fastapi+~httpx+~websockets
```

### 适配器与驱动器的匹配

不同适配器对驱动器有不同要求：

| 适配器 | 连接方式 | 所需驱动器类型 |
|--------|---------|---------------|
| OneBot V11 (正向 WS) | Bot 主动连接 NoneBot | 服务端 (ASGIMixin) |
| OneBot V11 (反向 WS) | NoneBot 主动连接 Bot | WebSocket 客户端 |
| OneBot V11 (HTTP POST) | Bot 推送事件到 NoneBot | 服务端 (ASGIMixin) |
| Telegram | NoneBot 轮询 / WebHook | HTTP 客户端 / 服务端 |
| QQ 官方 | WebSocket 连接 | HTTP + WebSocket 客户端 |
| Discord | WebSocket 连接 | HTTP + WebSocket 客户端 |

### 推荐配置

对于大多数场景，推荐使用：

```dotenv
# 涵盖服务端和 HTTP 客户端能力
DRIVER=~fastapi+~httpx

# 如需 WebSocket 客户端
DRIVER=~fastapi+~httpx+~websockets
```

## 驱动器 Mixin 类型检查

在编写依赖驱动器能力的代码时，应进行类型检查：

```python
import nonebot
from nonebot.drivers import (
    ASGIMixin,
    HTTPClientMixin,
    WebSocketClientMixin,
)

driver = nonebot.get_driver()

# 检查是否支持 HTTP 客户端
if isinstance(driver, HTTPClientMixin):
    response = await driver.request(request)

# 检查是否支持 WebSocket 客户端
if isinstance(driver, WebSocketClientMixin):
    async with driver.websocket(request) as ws:
        ...

# 检查是否支持服务端
if isinstance(driver, ASGIMixin):
    app = driver.asgi
```

## ForwardDriver 与 ReverseDriver

NoneBot 内部通过 `ForwardDriver` 和 `ReverseDriver` 来区分：

```python
from nonebot.drivers import ForwardDriver, ReverseDriver

driver = nonebot.get_driver()

if isinstance(driver, ReverseDriver):
    # 支持服务端功能
    driver.setup_http_server(setup)
    driver.setup_websocket_server(setup)

if isinstance(driver, ForwardDriver):
    # 支持客户端功能
    await driver.request(request)
    async with driver.websocket(request) as ws:
        ...
```

## None 驱动器

`~none` 驱动器不提供任何网络能力，适用于：

- 纯定时任务 Bot
- 仅使用本地功能的 Bot
- 测试场景

```dotenv
DRIVER=~none
```

```python
import nonebot
from nonebot.drivers import Driver

driver = nonebot.get_driver()
# 此时 driver 不具备任何 Mixin 能力

@driver.on_startup
async def startup():
    print("Bot 已启动（无网络能力）")
```
