# 模拟网络通信

NoneBot 的适配器通常通过 HTTP 或 WebSocket 与外部通信。NoneBug 提供了模拟网络通信的能力，用于测试驱动器的 HTTP/WebSocket 服务端行为。

## 基础设置

```python
import pytest
from nonebug import App


@pytest.fixture
async def app():
    yield App()
```

## HTTP 服务端测试

### app.test_server()

`app.test_server()` 创建一个测试上下文，可以获取 HTTP 客户端并向 NoneBot 的内置服务器发送请求。

```python
from nonebug import App


async def test_http_post(app: App):
    async with app.test_server() as ctx:
        client = ctx.get_client()

        # 发送 POST 请求
        resp = await client.post(
            "/onebot/v11/",
            json={
                "post_type": "message",
                "message_type": "private",
                "sub_type": "friend",
                "user_id": 10001,
                "message": [{"type": "text", "data": {"text": "hello"}}],
                "raw_message": "hello",
                "self_id": 1,
                "time": 1000000,
                "message_id": 1,
                "font": 0,
                "sender": {"user_id": 10001, "nickname": "test"},
            },
            headers={"X-Self-ID": "1"},
        )

        assert resp.status_code == 204
```

### 带鉴权的 HTTP 请求

```python
async def test_http_with_token(app: App):
    async with app.test_server() as ctx:
        client = ctx.get_client()

        # 携带 Access Token
        resp = await client.post(
            "/onebot/v11/",
            json={...},
            headers={
                "X-Self-ID": "1",
                "Authorization": "Bearer your-access-token",
            },
        )

        assert resp.status_code == 204
```

### 测试自定义路由

如果在 NoneBot 中注册了自定义 HTTP 路由，也可以通过 test_server 测试：

```python
async def test_custom_route(app: App):
    async with app.test_server() as ctx:
        client = ctx.get_client()

        # GET 请求
        resp = await client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data

        # POST 请求
        resp = await client.post(
            "/api/config",
            json={"key": "value"},
        )
        assert resp.status_code == 200
```

### 测试 GET 请求

```python
async def test_http_get(app: App):
    async with app.test_server() as ctx:
        client = ctx.get_client()

        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
```

## WebSocket 服务端测试

### websocket_connect

通过 `ctx.websocket_connect()` 创建一个 WebSocket 连接，模拟适配器的 WebSocket 交互。

```python
async def test_ws_connection(app: App):
    async with app.test_server() as ctx:
        async with ctx.websocket_connect(
            "/onebot/v11/ws",
            headers={"X-Self-ID": "1"},
        ) as ws:
            # 发送事件数据
            await ws.send_json(
                {
                    "post_type": "meta_event",
                    "meta_event_type": "lifecycle",
                    "sub_type": "connect",
                    "self_id": 1,
                    "time": 1000000,
                }
            )

            # 发送消息事件
            await ws.send_json(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "sub_type": "friend",
                    "user_id": 10001,
                    "message": [{"type": "text", "data": {"text": "/hello"}}],
                    "raw_message": "/hello",
                    "self_id": 1,
                    "time": 1000001,
                    "message_id": 1,
                    "font": 0,
                    "sender": {"user_id": 10001, "nickname": "test"},
                }
            )

            # 接收 Bot 的 API 调用响应
            data = await ws.receive_json()
            assert data["action"] == "send_msg"
```

### WebSocket 多次交互

```python
async def test_ws_multi_message(app: App):
    async with app.test_server() as ctx:
        async with ctx.websocket_connect(
            "/onebot/v11/ws",
            headers={"X-Self-ID": "1"},
        ) as ws:
            # 发送生命周期事件
            await ws.send_json(
                {
                    "post_type": "meta_event",
                    "meta_event_type": "lifecycle",
                    "sub_type": "connect",
                    "self_id": 1,
                    "time": 1000000,
                }
            )

            # 连续发送多条消息
            for i in range(3):
                await ws.send_json(
                    {
                        "post_type": "message",
                        "message_type": "private",
                        "sub_type": "friend",
                        "user_id": 10001,
                        "message": [
                            {"type": "text", "data": {"text": f"/echo 消息{i}"}}
                        ],
                        "raw_message": f"/echo 消息{i}",
                        "self_id": 1,
                        "time": 1000001 + i,
                        "message_id": i + 1,
                        "font": 0,
                        "sender": {"user_id": 10001, "nickname": "test"},
                    }
                )
```

### 带鉴权的 WebSocket 连接

```python
async def test_ws_with_token(app: App):
    async with app.test_server() as ctx:
        async with ctx.websocket_connect(
            "/onebot/v11/ws",
            headers={
                "X-Self-ID": "1",
                "Authorization": "Bearer your-access-token",
            },
        ) as ws:
            await ws.send_json({...})
```

## HTTP / WebSocket 客户端测试

> **注意**：NoneBug 目前暂不支持 HTTP / WebSocket 客户端（即 NoneBot 主动向外发起请求）的模拟测试。这部分功能在未来版本中可能会添加。

对于需要测试外部 HTTP 请求的场景，推荐使用以下替代方案：

### 使用 respx 模拟 HTTP 请求

```bash
pip install respx
```

```python
import httpx
import respx


@respx.mock
async def test_external_api():
    # 模拟外部 API 响应
    respx.get("https://api.example.com/weather").mock(
        return_value=httpx.Response(
            200,
            json={"city": "北京", "temp": 25, "weather": "晴"},
        )
    )

    # 在这里运行你的代码，httpx 请求会被拦截
    async with httpx.AsyncClient() as client:
        resp = await client.get("https://api.example.com/weather")
        assert resp.json()["city"] == "北京"
```

### 使用 pytest-httpx

```bash
pip install pytest-httpx
```

```python
import httpx
from pytest_httpx import HTTPXMock


async def test_with_httpx_mock(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://api.example.com/data",
        json={"result": "success"},
    )

    async with httpx.AsyncClient() as client:
        resp = await client.get("https://api.example.com/data")
        assert resp.json()["result"] == "success"
```

## 完整测试示例

```python
import pytest
from nonebug import App


@pytest.fixture
async def app():
    yield App()


async def test_onebot_http_event(app: App):
    """测试 OneBot V11 HTTP 上报"""
    async with app.test_server() as ctx:
        client = ctx.get_client()

        resp = await client.post(
            "/onebot/v11/",
            json={
                "post_type": "message",
                "message_type": "private",
                "sub_type": "friend",
                "user_id": 10001,
                "message": [{"type": "text", "data": {"text": "/ping"}}],
                "raw_message": "/ping",
                "self_id": 1,
                "time": 1000000,
                "message_id": 1,
                "font": 0,
                "sender": {"user_id": 10001, "nickname": "test"},
            },
            headers={"X-Self-ID": "1"},
        )
        assert resp.status_code in (200, 204)


async def test_onebot_ws_event(app: App):
    """测试 OneBot V11 WebSocket 连接"""
    async with app.test_server() as ctx:
        async with ctx.websocket_connect(
            "/onebot/v11/ws",
            headers={"X-Self-ID": "1"},
        ) as ws:
            await ws.send_json(
                {
                    "post_type": "meta_event",
                    "meta_event_type": "lifecycle",
                    "sub_type": "connect",
                    "self_id": 1,
                    "time": 1000000,
                }
            )

            await ws.send_json(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "sub_type": "friend",
                    "user_id": 10001,
                    "message": [{"type": "text", "data": {"text": "/ping"}}],
                    "raw_message": "/ping",
                    "self_id": 1,
                    "time": 1000001,
                    "message_id": 1,
                    "font": 0,
                    "sender": {"user_id": 10001, "nickname": "test"},
                }
            )
```

## API 汇总

| 方法 | 说明 |
|------|------|
| `app.test_server()` | 创建服务端测试上下文 |
| `ctx.get_client()` | 获取 HTTP 测试客户端 |
| `client.get(url, **kwargs)` | 发送 GET 请求 |
| `client.post(url, **kwargs)` | 发送 POST 请求 |
| `ctx.websocket_connect(url, **kwargs)` | 创建 WebSocket 连接 |
| `ws.send_json(data)` | 发送 JSON 数据 |
| `ws.send_text(data)` | 发送文本数据 |
| `ws.send_bytes(data)` | 发送二进制数据 |
| `ws.receive_json()` | 接收 JSON 数据 |
| `ws.receive_text()` | 接收文本数据 |
| `ws.receive_bytes()` | 接收二进制数据 |
