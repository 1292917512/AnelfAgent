# 配置

NoneBot 使用 [pydantic](https://docs.pydantic.dev/) 和 [python-dotenv](https://github.com/theskumar/python-dotenv) 来管理配置。所有配置项均可通过 `.env` 文件、环境变量或代码直接指定。

---

## 配置文件体系

### .env 文件层级

NoneBot 的配置文件遵循 dotenv 格式，支持多环境切换：

```
项目根目录/
├── .env            # 通用配置（始终加载），指定当前环境
├── .env.prod       # 生产环境配置
├── .env.dev        # 开发环境配置
└── ...
```

#### .env（主配置文件）

```dotenv
# 指定当前使用的环境
ENVIRONMENT=dev

# 也可以放通用配置
LOG_LEVEL=INFO
```

`ENVIRONMENT` 决定了 NoneBot 会额外加载哪个环境配置文件。例如 `ENVIRONMENT=dev` 时会加载 `.env.dev`。

#### .env.dev（开发环境）

```dotenv
DRIVER=~fastapi
HOST=127.0.0.1
PORT=8080
LOG_LEVEL=DEBUG
COMMAND_START=["/", "!"]
COMMAND_SEP=["."]
SUPERUSERS=["123456789"]
NICKNAME=["bot", "机器人"]
```

#### .env.prod（生产环境）

```dotenv
DRIVER=~fastapi+~httpx+~websockets
HOST=0.0.0.0
PORT=8080
LOG_LEVEL=INFO
COMMAND_START=["/"]
COMMAND_SEP=["."]
SUPERUSERS=["123456789", "987654321"]
NICKNAME=["bot"]
```

> **注意**：环境配置文件中的值会覆盖 `.env` 中的同名配置。

---

## 内置配置项

NoneBot 内置了以下核心配置项（定义在 `nonebot.config.Env` 和 `nonebot.config.Config` 中）：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `ENVIRONMENT` | `str` | `"prod"` | 当前环境名，决定加载哪个 `.env.{name}` |
| `DRIVER` | `str` | `"~fastapi"` | 驱动器，使用 `~` 前缀表示内置驱动器 |
| `HOST` | `IPvAnyAddress` | `127.0.0.1` | 监听地址 |
| `PORT` | `int` | `8080` | 监听端口 |
| `LOG_LEVEL` | `int \| str` | `"INFO"` | 日志级别 |
| `API_TIMEOUT` | `float \| None` | `30.0` | API 调用超时（秒） |
| `SUPERUSERS` | `set[str]` | `set()` | 超级用户 ID 集合 |
| `NICKNAME` | `set[str]` | `set()` | 机器人昵称集合 |
| `COMMAND_START` | `set[str]` | `{"/"}` | 命令起始标识符集合 |
| `COMMAND_SEP` | `set[str]` | `{"."}` | 命令分隔符集合 |

### DRIVER 配置说明

驱动器字符串使用 `+` 连接多个驱动器，`~` 前缀表示 NoneBot 内置驱动器：

```dotenv
# 仅 FastAPI（正向 WebSocket 服务端）
DRIVER=~fastapi

# FastAPI + httpx（支持主动 HTTP 请求）
DRIVER=~fastapi+~httpx

# FastAPI + httpx + websockets（完整功能）
DRIVER=~fastapi+~httpx+~websockets
```

常用驱动器：

| 驱动器 | 说明 | 典型用途 |
|--------|------|---------|
| `~fastapi` | 服务端驱动器，提供 HTTP/WS 服务 | 接收反向 WebSocket 连接 |
| `~httpx` | HTTP 客户端驱动器 | 主动调用 HTTP API |
| `~websockets` | WebSocket 客户端驱动器 | 主动建立正向 WebSocket |
| `~aiohttp` | HTTP + WebSocket 客户端 | httpx + websockets 的替代 |
| `~none` | 空驱动器，无网络能力 | 纯本地使用 |

### COMMAND_START 与 COMMAND_SEP

这两个配置项用于 `on_command` 响应器的命令匹配：

```dotenv
COMMAND_START=["/", "!", "。"]
COMMAND_SEP=["."]
```

当用户发送 `/weather.beijing` 时：

- `COMMAND_START` 匹配到 `/`
- `COMMAND_SEP` 在 `.` 处分割
- 解析为命令 `("weather", "beijing")`

---

## 读取配置

### 读取全局配置

```python
import nonebot

# 获取全局配置对象
global_config = nonebot.get_driver().config

# 读取配置项
superusers = global_config.superusers
command_start = global_config.command_start
host = global_config.host
port = global_config.port
log_level = global_config.log_level
```

### 自定义插件配置类

推荐使用 pydantic `BaseModel` 定义插件配置，配合 `get_plugin_config()` 读取：

```python
from pydantic import BaseModel

class Config(BaseModel):
    """插件配置"""
    weather_api_key: str = ""
    weather_default_city: str = "北京"
    weather_cache_ttl: int = 300
```

在 `.env` 文件中添加对应配置项：

```dotenv
WEATHER_API_KEY=your_api_key_here
WEATHER_DEFAULT_CITY=上海
WEATHER_CACHE_TTL=600
```

> **注意**：pydantic 会自动将 `WEATHER_API_KEY` 映射到 `weather_api_key` 字段（大小写不敏感）。

### get_plugin_config

使用 `nonebot.get_plugin_config()` 将全局配置注入到插件配置类：

```python
import nonebot
from nonebot import get_plugin_config
from pydantic import BaseModel


class Config(BaseModel):
    weather_api_key: str = ""
    weather_default_city: str = "北京"
    weather_cache_ttl: int = 300


plugin_config = get_plugin_config(Config)

# 使用配置
print(plugin_config.weather_api_key)
print(plugin_config.weather_default_city)
```

### 完整插件示例

```python
from nonebot import on_command, get_plugin_config
from nonebot.adapters import Message
from nonebot.params import CommandArg
from pydantic import BaseModel


class Config(BaseModel):
    weather_api_key: str
    weather_default_city: str = "北京"


config = get_plugin_config(Config)

weather = on_command("天气", priority=10, block=True)


@weather.handle()
async def handle_weather(args: Message = CommandArg()):
    city = args.extract_plain_text().strip() or config.weather_default_city
    # 使用 config.weather_api_key 调用 API
    await weather.finish(f"正在查询 {city} 的天气...")
```

---

## 配置类高级用法

### 嵌套配置

```python
from pydantic import BaseModel


class DatabaseConfig(BaseModel):
    host: str = "localhost"
    port: int = 5432
    name: str = "mydb"


class Config(BaseModel):
    db: DatabaseConfig = DatabaseConfig()
    debug: bool = False
```

### 字段验证

```python
from pydantic import BaseModel, field_validator


class Config(BaseModel):
    api_timeout: int = 30

    @field_validator("api_timeout")
    @classmethod
    def validate_timeout(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("超时时间必须大于 0")
        return v
```

### .env 中的复杂类型

dotenv 文件中可以使用 JSON 格式传入列表、字典等复杂类型：

```dotenv
# 列表
COMMAND_START=["/", "!", "。"]

# 集合
SUPERUSERS=["123456789", "987654321"]

# 字典（需要嵌套 JSON）
CUSTOM_MAP={"key1": "value1", "key2": "value2"}
```

---

## 配置优先级

优先级从高到低：

1. **环境变量**（系统环境变量）
2. **环境配置文件**（`.env.dev` / `.env.prod`）
3. **通用配置文件**（`.env`）
4. **代码默认值**（`nonebot.init()` 传参 / pydantic 默认值）

```python
# nonebot.init() 可以直接传入配置覆盖
nonebot.init(
    host="0.0.0.0",
    port=9090,
    log_level="DEBUG",
    custom_config_key="value",
)
```

---

## 与 AnelfTools 桥接的配置

在 AnelfTools 的 NoneBot Bridge 中，NoneBot 配置通过 `channel_config.json` 管理，而非直接使用 `.env` 文件。桥接层会将配置转换为 NoneBot 可识别的格式并传给 `nonebot.init()`。

```json
{
  "driver": "~fastapi",
  "host": "127.0.0.1",
  "port": 8080,
  "command_start": ["/"],
  "command_sep": ["."],
  "superusers": ["123456789"],
  "log_level": "INFO"
}
```

相关实现参见 `channels/nonebot_bridge/config.py`。
