# 添加路由

NoneBot 允许你在驱动器之上添加自定义的 HTTP 路由和 WebSocket 端点，用于实现自定义 API、Web 界面、WebHook 接收等功能。

## 两种添加方式

| 方式 | 说明 | 适用场景 |
|------|------|---------|
| NoneBot 兼容层 | `HTTPServerSetup` / `WebSocketServerSetup` | 跨驱动器兼容 |
| 直接操作底层框架 | `nonebot.get_app()` | 需要完整框架能力 |

## 方式一：NoneBot 兼容层

### HTTPServerSetup — 添加 HTTP 路由

```python
from nonebot import get_driver
from nonebot.drivers import (
    URL,
    Request,
    Response,
    HTTPServerSetup,
    ReverseDriver,
)

driver = get_driver()
assert isinstance(driver, ReverseDriver)

async def handle_hello(request: Request) -> Response:
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json"},
        content='{"message": "Hello from NoneBot!"}',
    )

# 注册 HTTP 路由
driver.setup_http_server(
    HTTPServerSetup(
        path=URL("/api/hello"),
        method="GET",
        name="hello",
        handle_func=handle_hello,
    )
)
```

### Request 对象

`Request` 对象包含 HTTP 请求的所有信息：

| 属性 | 类型 | 说明 |
|------|------|------|
| `method` | `str` | 请求方法（GET, POST 等） |
| `url` | `URL` | 请求 URL |
| `headers` | `dict` | 请求头 |
| `content` | `bytes \| None` | 请求体 |
| `cookies` | `dict` | Cookie |

```python
async def handle_request(request: Request) -> Response:
    # 获取请求信息
    method = request.method
    url = request.url
    headers = request.headers
    body = request.content  # bytes

    # 解析 JSON 请求体
    if body:
        import json
        data = json.loads(body)

    # 获取查询参数
    query = request.url.query  # bytes | None

    return Response(status_code=200, content="OK")
```

### Response 对象

| 属性 | 类型 | 说明 |
|------|------|------|
| `status_code` | `int` | HTTP 状态码 |
| `headers` | `dict` | 响应头 |
| `content` | `str \| bytes \| None` | 响应体 |

```python
# 返回 JSON
response = Response(
    status_code=200,
    headers={"Content-Type": "application/json"},
    content='{"status": "ok"}',
)

# 返回 HTML
response = Response(
    status_code=200,
    headers={"Content-Type": "text/html"},
    content="<h1>Hello</h1>",
)

# 返回错误
response = Response(
    status_code=404,
    content="Not Found",
)
```

### POST 路由示例

```python
import json

async def handle_webhook(request: Request) -> Response:
    if not request.content:
        return Response(status_code=400, content="Empty body")

    try:
        data = json.loads(request.content)
    except json.JSONDecodeError:
        return Response(status_code=400, content="Invalid JSON")

    # 处理 webhook 数据
    await process_webhook(data)

    return Response(
        status_code=200,
        headers={"Content-Type": "application/json"},
        content=json.dumps({"status": "ok"}),
    )

driver.setup_http_server(
    HTTPServerSetup(
        path=URL("/api/webhook"),
        method="POST",
        name="webhook",
        handle_func=handle_webhook,
    )
)
```

### WebSocketServerSetup — 添加 WebSocket 路由

```python
from nonebot.drivers import WebSocketServerSetup, WebSocket

async def handle_ws(ws: WebSocket) -> None:
    await ws.accept()

    try:
        while True:
            data = await ws.receive()
            # 处理接收到的消息
            response = f"Echo: {data}"
            await ws.send(response)
    except Exception:
        pass
    finally:
        await ws.close()

driver.setup_websocket_server(
    WebSocketServerSetup(
        path=URL("/ws/echo"),
        name="echo_ws",
        handle_func=handle_ws,
    )
)
```

### WebSocket 对象

| 方法 | 说明 |
|------|------|
| `await ws.accept()` | 接受 WebSocket 连接 |
| `await ws.close(code?)` | 关闭连接 |
| `await ws.receive()` | 接收文本消息 |
| `await ws.receive_bytes()` | 接收二进制消息 |
| `await ws.send(data)` | 发送文本消息 |
| `await ws.send_bytes(data)` | 发送二进制消息 |

## 方式二：直接操作 FastAPI

当使用 FastAPI 驱动器时，可以直接获取 FastAPI 应用实例来添加路由，享受完整的 FastAPI 功能。

### 获取 FastAPI 实例

```python
import nonebot
from fastapi import FastAPI

app: FastAPI = nonebot.get_app()
```

### 添加 API 路由

```python
import nonebot
from fastapi import FastAPI
from pydantic import BaseModel

app: FastAPI = nonebot.get_app()

class Item(BaseModel):
    name: str
    value: int

@app.get("/api/status")
async def get_status():
    bots = nonebot.get_bots()
    return {
        "status": "running",
        "bots": len(bots),
        "bot_ids": list(bots.keys()),
    }

@app.post("/api/items")
async def create_item(item: Item):
    # 处理创建逻辑
    return {"id": 1, "name": item.name, "value": item.value}

@app.get("/api/items/{item_id}")
async def get_item(item_id: int):
    return {"id": item_id, "name": "example"}
```

### 添加 WebSocket 路由

```python
from fastapi import WebSocket as FastAPIWebSocket

app: FastAPI = nonebot.get_app()

@app.websocket("/ws/chat")
async def websocket_chat(websocket: FastAPIWebSocket):
    await websocket.accept()

    try:
        while True:
            data = await websocket.receive_text()
            # 处理消息
            await websocket.send_text(f"收到: {data}")
    except Exception:
        await websocket.close()
```

### 使用 APIRouter

```python
from fastapi import APIRouter

router = APIRouter(prefix="/api/v1", tags=["v1"])

@router.get("/users")
async def list_users():
    return [{"id": 1, "name": "Alice"}]

@router.get("/users/{user_id}")
async def get_user(user_id: int):
    return {"id": user_id, "name": "Alice"}

# 将 router 添加到 FastAPI 应用
app: FastAPI = nonebot.get_app()
app.include_router(router)
```

### 静态文件

```python
from fastapi.staticfiles import StaticFiles

app: FastAPI = nonebot.get_app()
app.mount("/static", StaticFiles(directory="static"), name="static")
```

### 中间件

```python
from fastapi import Request
from fastapi.responses import JSONResponse

app: FastAPI = nonebot.get_app()

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    token = request.headers.get("Authorization")
    if not token and request.url.path.startswith("/api/admin"):
        return JSONResponse(
            status_code=401,
            content={"detail": "Unauthorized"},
        )
    response = await call_next(request)
    return response
```

## 在启动钩子中注册路由

推荐在 `on_startup` 钩子中注册路由，确保 NoneBot 已完成初始化：

```python
import nonebot
from nonebot import get_driver

driver = get_driver()

@driver.on_startup
async def register_routes():
    app = nonebot.get_app()

    @app.get("/api/health")
    async def health_check():
        return {"status": "healthy"}
```

## 完整示例：Bot 管理 API

```python
import nonebot
from nonebot import get_driver
from nonebot.drivers import URL, Request, Response, HTTPServerSetup, ReverseDriver
import json

driver = get_driver()

async def handle_bot_list(request: Request) -> Response:
    bots = nonebot.get_bots()
    bot_list = [
        {"id": bot_id, "adapter": bot.adapter.get_name()}
        for bot_id, bot in bots.items()
    ]
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json"},
        content=json.dumps({"bots": bot_list}),
    )

async def handle_send_message(request: Request) -> Response:
    if not request.content:
        return Response(status_code=400, content="Missing body")

    data = json.loads(request.content)
    bot_id = data.get("bot_id")
    target = data.get("target")
    message = data.get("message")

    bot = nonebot.get_bot(bot_id)
    await bot.send_msg(
        message_type="private",
        user_id=int(target),
        message=message,
    )

    return Response(
        status_code=200,
        headers={"Content-Type": "application/json"},
        content='{"status": "ok"}',
    )

assert isinstance(driver, ReverseDriver)

driver.setup_http_server(
    HTTPServerSetup(
        path=URL("/api/bots"),
        method="GET",
        name="bot_list",
        handle_func=handle_bot_list,
    )
)

driver.setup_http_server(
    HTTPServerSetup(
        path=URL("/api/send"),
        method="POST",
        name="send_message",
        handle_func=handle_send_message,
    )
)
```
