# 日志

NoneBot 使用 [loguru](https://github.com/Delgan/loguru) 作为日志库，提供了简洁强大的日志功能。

---

## 基本使用

### 导入 logger

```python
from nonebot.log import logger
```

NoneBot 的 `logger` 是 loguru 的 logger 实例，可以直接使用 loguru 的所有功能。

### 日志级别

```python
from nonebot.log import logger

logger.trace("最详细的追踪日志")
logger.debug("调试信息")
logger.info("一般信息")
logger.success("成功信息")
logger.warning("警告信息")
logger.error("错误信息")
logger.critical("严重错误")
```

日志级别从低到高：

| 级别 | 数值 | 说明 |
|------|------|------|
| `TRACE` | 5 | 最详细的追踪信息 |
| `DEBUG` | 10 | 调试信息 |
| `INFO` | 20 | 一般信息 |
| `SUCCESS` | 25 | 成功信息 |
| `WARNING` | 30 | 警告信息 |
| `ERROR` | 40 | 错误信息 |
| `CRITICAL` | 50 | 严重错误 |

---

## 配置日志级别

### 通过 .env 配置

```dotenv
LOG_LEVEL=DEBUG
```

### 通过 nonebot.init() 配置

```python
import nonebot

nonebot.init(log_level="DEBUG")
```

### 运行时修改

```python
from nonebot.log import logger

logger.remove()
logger.add(
    "sys.stderr",
    level="DEBUG",
)
```

---

## 在插件中使用日志

### 基础日志记录

```python
from nonebot import on_command
from nonebot.log import logger

weather = on_command("天气", priority=10, block=True)


@weather.handle()
async def handle(event):
    user_id = event.get_user_id()
    logger.info(f"用户 {user_id} 请求了天气查询")

    try:
        result = await fetch_weather("北京")
        logger.debug(f"天气查询结果: {result}")
        await weather.finish(f"天气：{result}")
    except Exception as e:
        logger.error(f"天气查询失败: {e}")
        await weather.finish("查询失败，请稍后重试")
```

### 结构化日志

loguru 支持使用 `{}` 占位符进行结构化日志（推荐，性能更好）：

```python
from nonebot.log import logger

logger.info("用户 {} 在群 {} 发送了消息", user_id, group_id)
logger.debug("API 响应: status={}, data={}", status_code, data)
logger.warning("配置项 {} 未设置，使用默认值 {}", key, default)
```

### 记录异常

```python
from nonebot.log import logger


@cmd.handle()
async def handle():
    try:
        result = 1 / 0
    except Exception:
        logger.exception("处理过程中发生异常")
        # exception() 会自动包含完整的 traceback
        await cmd.finish("发生错误")
```

也可以使用 `opt(exception=True)`：

```python
try:
    risky_operation()
except Exception as e:
    logger.opt(exception=True).error("操作失败")
```

---

## 自定义日志格式

### 默认格式

NoneBot 默认的日志格式包含时间、级别、模块名和消息内容。

### 自定义格式

```python
from nonebot.log import logger
import sys

# 先移除默认的 handler
logger.remove()

# 添加自定义格式的 handler
logger.add(
    sys.stderr,
    level="INFO",
    format=(
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    ),
    colorize=True,
)
```

### 输出到文件

```python
from nonebot.log import logger

logger.add(
    "logs/bot_{time:YYYY-MM-DD}.log",
    level="DEBUG",
    rotation="00:00",       # 每天午夜轮转
    retention="30 days",    # 保留 30 天
    compression="zip",      # 压缩旧日志
    encoding="utf-8",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
)
```

### 按级别分文件

```python
from nonebot.log import logger

# 错误日志单独存放
logger.add(
    "logs/error_{time:YYYY-MM-DD}.log",
    level="ERROR",
    rotation="00:00",
    retention="90 days",
    encoding="utf-8",
)

# 全量日志
logger.add(
    "logs/all_{time:YYYY-MM-DD}.log",
    level="DEBUG",
    rotation="00:00",
    retention="7 days",
    encoding="utf-8",
)
```

---

## loguru 高级功能

### 过滤特定模块

```python
from nonebot.log import logger

# 只记录特定模块的日志
logger.add(
    "logs/my_plugin.log",
    level="DEBUG",
    filter=lambda record: "my_plugin" in record["name"],
)
```

### 自定义 filter 函数

```python
from nonebot.log import logger


def my_filter(record):
    """只记录包含关键词的日志"""
    return "重要" in record["message"] or record["level"].no >= 30


logger.add("logs/important.log", filter=my_filter)
```

### 上下文绑定

```python
from nonebot.log import logger

# 绑定上下文信息
context_logger = logger.bind(user_id="123456", group_id="789012")
context_logger.info("用户操作记录")
# 输出: ... | user_id=123456 group_id=789012 | 用户操作记录
```

### 延迟求值

```python
from nonebot.log import logger

# 使用 opt(lazy=True) 避免不必要的字符串格式化
logger.opt(lazy=True).debug("耗时计算结果: {}", lambda: expensive_computation())
```

### 带颜色标记

```python
from nonebot.log import logger

logger.opt(colors=True).info(
    "处理 <yellow>{}</yellow> 的请求，结果: <green>成功</green>",
    "用户A",
)
```

---

## NoneBot 内部日志

NoneBot 框架内部会输出以下类型的日志：

| 日志来源 | 级别 | 内容 |
|---------|------|------|
| `nonebot` | INFO | 框架启动、插件加载 |
| `nonebot.plugin` | DEBUG | 插件加载详情 |
| `nonebot.matcher` | DEBUG | 事件匹配过程 |
| `nonebot.adapters.*` | DEBUG/INFO | 适配器连接、消息收发 |
| `uvicorn` | INFO | HTTP 服务器日志 |

可以通过调整 `LOG_LEVEL` 来控制这些日志的输出。开发时建议设为 `DEBUG`，生产环境设为 `INFO` 或 `WARNING`。

---

## 最佳实践

1. **插件中使用 `nonebot.log.logger`** — 保持日志输出统一
2. **使用结构化占位符** — `logger.info("user={}", uid)` 优于 `logger.info(f"user={uid}")`
3. **记录关键操作** — API 调用、用户操作、错误处理都应有日志
4. **生产环境用 INFO** — 避免 DEBUG 日志影响性能
5. **记录异常用 `exception()`** — 自动包含 traceback
6. **日志文件定期轮转** — 使用 `rotation` 和 `retention` 防止磁盘爆满
