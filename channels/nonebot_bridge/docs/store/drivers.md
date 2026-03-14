# 驱动器商店

[NoneBot 驱动器商店](https://nonebot.dev/store/drivers) 收录了 NoneBot 可用的驱动器（Driver），驱动器决定了 NoneBot 如何处理网络通信。

## 商店地址

- **在线商店**：<https://nonebot.dev/store/drivers>

## 什么是驱动器

驱动器是 NoneBot 的底层网络通信组件，负责：

- **服务端**（Reverse Driver）：提供 HTTP/WebSocket 服务端，接收平台推送
- **客户端**（Forward Driver）：主动发起 HTTP/WebSocket 连接到平台

根据适配器的连接方式不同，需要选择合适的驱动器或驱动器组合。

## 驱动器类型

| 类型 | 说明 | 能力 |
|------|------|------|
| 服务端（Reverse） | 提供 ASGI 服务，监听端口 | 接收 HTTP 回调、WebSocket 连接 |
| 客户端（Forward） | 发起 HTTP 请求、WebSocket 连接 | 主动连接平台服务器 |

## 可用驱动器

### FastAPI（服务端）

| 属性 | 值 |
|------|-----|
| 包名 | `nonebot2[fastapi]` |
| 驱动器名 | `~fastapi` |
| 类型 | 服务端（Reverse） |
| 依赖 | [FastAPI](https://fastapi.tiangolo.com/) + [Uvicorn](https://www.uvicorn.org/) |

NoneBot 默认的驱动器，提供高性能的 ASGI 服务端，支持 HTTP 和 WebSocket。

```bash
pip install nonebot2[fastapi]
```

```dotenv
DRIVER=~fastapi
```

特性：

- 基于 FastAPI 的高性能 HTTP 服务
- 内置 WebSocket 支持
- 自动生成 API 文档（`/docs`）
- 支持自定义路由和中间件

### httpx（客户端 HTTP）

| 属性 | 值 |
|------|-----|
| 包名 | `nonebot2[httpx]` |
| 驱动器名 | `~httpx` |
| 类型 | 客户端（Forward） - 仅 HTTP |
| 依赖 | [httpx](https://www.python-httpx.org/) |

提供异步 HTTP 客户端功能，用于主动发起 HTTP 请求。

```bash
pip install nonebot2[httpx]
```

```dotenv
DRIVER=~httpx
```

### aiohttp（客户端 HTTP + WebSocket）

| 属性 | 值 |
|------|-----|
| 包名 | `nonebot2[aiohttp]` |
| 驱动器名 | `~aiohttp` |
| 类型 | 客户端（Forward） - HTTP + WebSocket |
| 依赖 | [aiohttp](https://docs.aiohttp.org/) |

提供异步 HTTP 客户端和 WebSocket 客户端功能。

```bash
pip install nonebot2[aiohttp]
```

```dotenv
DRIVER=~aiohttp
```

### websockets（客户端 WebSocket）

| 属性 | 值 |
|------|-----|
| 包名 | `nonebot2[websockets]` |
| 驱动器名 | `~websockets` |
| 类型 | 客户端（Forward） - 仅 WebSocket |
| 依赖 | [websockets](https://websockets.readthedocs.io/) |

提供异步 WebSocket 客户端功能。

```bash
pip install nonebot2[websockets]
```

```dotenv
DRIVER=~websockets
```

### none（无驱动器）

| 属性 | 值 |
|------|-----|
| 包名 | 内置 |
| 驱动器名 | `~none` |
| 类型 | 无网络功能 |

不提供任何网络功能，适用于纯定时任务或测试场景。

```dotenv
DRIVER=~none
```

## 驱动器组合

NoneBot 支持将多个驱动器组合使用，以同时获得服务端和客户端能力。使用 `+` 号连接多个驱动器：

### 常用组合

| 组合 | DRIVER 配置 | 能力 |
|------|------------|------|
| FastAPI + httpx + websockets | `~fastapi+~httpx+~websockets` | 服务端 + HTTP 客户端 + WebSocket 客户端 |
| FastAPI + aiohttp | `~fastapi+~aiohttp` | 服务端 + HTTP/WS 客户端 |
| FastAPI + httpx | `~fastapi+~httpx` | 服务端 + HTTP 客户端 |
| httpx + websockets | `~httpx+~websockets` | HTTP 客户端 + WebSocket 客户端 |
| aiohttp | `~aiohttp` | HTTP/WS 客户端 |

### 安装组合驱动器

```bash
# 全功能安装
pip install nonebot2[fastapi,httpx,websockets]

# 简化安装
pip install nonebot2[fastapi,aiohttp]
```

### 配置驱动器组合

```dotenv
# .env
DRIVER=~fastapi+~httpx+~websockets
```

## 如何选择驱动器

### 根据适配器需求选择

不同适配器对驱动器有不同要求：

| 适配器连接方式 | 需要的驱动器类型 | 推荐配置 |
|---------------|----------------|---------|
| 反向 WebSocket | 服务端 | `~fastapi` |
| 正向 WebSocket | WebSocket 客户端 | `~fastapi+~websockets` |
| HTTP 回调 | 服务端 | `~fastapi` |
| HTTP 轮询 | HTTP 客户端 | `~fastapi+~httpx` |
| WebHook | 服务端 + HTTP 客户端 | `~fastapi+~httpx` |

### 常见场景

**OneBot V11（反向 WebSocket）**：

```dotenv
DRIVER=~fastapi
```

**OneBot V11（正向 WebSocket）**：

```dotenv
DRIVER=~fastapi+~websockets
```

**QQ 官方机器人（WebSocket + HTTP）**：

```dotenv
DRIVER=~fastapi+~httpx+~websockets
```

**Telegram（HTTP 长轮询）**：

```dotenv
DRIVER=~fastapi+~httpx
```

**多适配器混合使用**：

```dotenv
# 推荐全功能安装
DRIVER=~fastapi+~httpx+~websockets
```

## 配置示例

### pyproject.toml

```toml
[tool.nonebot]
driver = "~fastapi+~httpx+~websockets"
```

### .env

```dotenv
DRIVER=~fastapi+~httpx+~websockets
HOST=0.0.0.0
PORT=8080
```

### 代码中设置

```python
import nonebot

nonebot.init(driver="~fastapi+~httpx+~websockets")
```

## 驱动器对比总结

| 驱动器 | 服务端 | HTTP 客户端 | WebSocket 客户端 | 推荐场景 |
|--------|--------|------------|-----------------|---------|
| FastAPI | ✅ | ❌ | ❌ | 反向连接 |
| httpx | ❌ | ✅ | ❌ | HTTP API 调用 |
| aiohttp | ❌ | ✅ | ✅ | 正向连接（简化） |
| websockets | ❌ | ❌ | ✅ | 正向 WebSocket |
| none | ❌ | ❌ | ❌ | 定时任务/测试 |
