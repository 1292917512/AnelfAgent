# 钩子函数

NoneBot 提供了丰富的钩子（Hook）函数，允许你在 Bot 生命周期的各个关键节点插入自定义逻辑。

## 钩子类型概览

| 类别 | 钩子 | 触发时机 |
|------|------|---------|
| 全局 | `on_startup` | NoneBot 启动时 |
| 全局 | `on_shutdown` | NoneBot 关闭时 |
| 全局 | `on_bot_connect` | Bot 连接时 |
| 全局 | `on_bot_disconnect` | Bot 断开连接时 |
| 事件 | `event_preprocessor` | 事件处理前 |
| 事件 | `event_postprocessor` | 事件处理后 |
| 运行 | `run_preprocessor` | Matcher 运行前 |
| 运行 | `run_postprocessor` | Matcher 运行后 |
| API | `on_calling_api` | 调用 API 前 |
| API | `on_called_api` | 调用 API 后 |

## 全局钩子

### on_startup — 启动钩子

在 NoneBot 启动时（所有适配器注册完成后、开始监听前）触发。

```python
from nonebot import get_driver

driver = get_driver()

@driver.on_startup
async def startup():
    print("NoneBot 启动中...")
    # 初始化数据库连接
    await init_database()
    # 加载缓存
    await load_cache()
```

### on_shutdown — 关闭钩子

在 NoneBot 关闭时触发。

```python
@driver.on_shutdown
async def shutdown():
    print("NoneBot 关闭中...")
    # 关闭数据库连接
    await close_database()
    # 保存缓存
    await save_cache()
    # 清理临时文件
    cleanup_temp_files()
```

### on_bot_connect — Bot 连接钩子

当 Bot 成功连接到 NoneBot 时触发。

```python
from nonebot.adapters import Bot

@driver.on_bot_connect
async def bot_connect(bot: Bot):
    print(f"Bot {bot.self_id} 已连接")
    # 发送上线通知
    await bot.send_private_msg(
        user_id=ADMIN_ID,
        message=f"Bot {bot.self_id} 已上线！"
    )
```

### on_bot_disconnect — Bot 断开钩子

当 Bot 断开与 NoneBot 的连接时触发。

```python
@driver.on_bot_disconnect
async def bot_disconnect(bot: Bot):
    print(f"Bot {bot.self_id} 已断开")
    # 记录断开时间
    await log_disconnect(bot.self_id)
```

## 事件钩子

### event_preprocessor — 事件预处理

在所有事件处理之前执行，可用于事件过滤、日志记录等。

```python
from nonebot.message import event_preprocessor
from nonebot.adapters import Bot, Event
from nonebot.exception import IgnoredException

@event_preprocessor
async def preprocess(bot: Bot, event: Event):
    user_id = event.get_user_id()

    # 黑名单检查
    if user_id in BLACKLIST:
        raise IgnoredException(f"用户 {user_id} 在黑名单中")

    # 日志记录
    print(f"收到事件: {event.get_event_name()} from {user_id}")
```

#### IgnoredException

在 `event_preprocessor` 中抛出 `IgnoredException` 可以忽略当前事件，阻止所有后续处理。

```python
from nonebot.exception import IgnoredException

@event_preprocessor
async def check_cooldown(event: Event):
    user_id = event.get_user_id()
    if is_in_cooldown(user_id):
        raise IgnoredException("冷却中")
```

### event_postprocessor — 事件后处理

在所有事件处理完成后执行。

```python
from nonebot.message import event_postprocessor

@event_postprocessor
async def postprocess(bot: Bot, event: Event):
    # 统计处理
    await update_statistics(event)

    # 记录处理完成
    print(f"事件处理完成: {event.get_event_name()}")
```

### run_preprocessor — 运行预处理

在每个 Matcher 的处理函数运行前执行。

```python
from nonebot.message import run_preprocessor
from nonebot.matcher import Matcher
from nonebot.adapters import Bot, Event
from nonebot.exception import IgnoredException

@run_preprocessor
async def pre_run(
    bot: Bot,
    event: Event,
    matcher: Matcher,
):
    plugin = matcher.plugin
    if plugin:
        print(f"即将运行插件: {plugin.name}")

    # 可以阻止特定 Matcher 运行
    if matcher.plugin_name == "disabled_plugin":
        raise IgnoredException("插件已禁用")
```

### run_postprocessor — 运行后处理

在每个 Matcher 的处理函数运行后执行，可以获取异常信息。

```python
from nonebot.message import run_postprocessor
from nonebot.matcher import Matcher
from nonebot.adapters import Bot, Event

@run_postprocessor
async def post_run(
    bot: Bot,
    event: Event,
    matcher: Matcher,
    exception: Exception | None,
):
    if exception:
        # 记录异常
        print(f"Matcher 运行异常: {exception}")
        await bot.send(
            event,
            f"处理出错: {type(exception).__name__}",
        )
    else:
        print(f"Matcher 运行成功: {matcher.plugin_name}")
```

## API 钩子

### on_calling_api — API 调用前

在 Bot 调用平台 API 之前触发，可以修改 API 参数或使用 `MockApiException` 模拟返回值。

```python
from nonebot.adapters import Bot
from nonebot.message import on_calling_api

@on_calling_api
async def calling_api(
    bot: Bot,
    api: str,
    data: dict,
):
    print(f"调用 API: {api}")
    print(f"参数: {data}")

    # 修改 API 参数
    if api == "send_msg":
        data["message"] = data.get("message", "") + "\n— Powered by Bot"
```

#### MockApiException

在 `on_calling_api` 中抛出 `MockApiException` 可以模拟 API 返回值，不会真正调用 API。

```python
from nonebot.exception import MockApiException

@on_calling_api
async def mock_api(bot: Bot, api: str, data: dict):
    # 模拟获取群列表
    if api == "get_group_list":
        raise MockApiException(
            result=[
                {"group_id": 123456, "group_name": "测试群"},
                {"group_id": 789012, "group_name": "开发群"},
            ]
        )
```

### on_called_api — API 调用后

在 Bot 调用平台 API 之后触发，可以获取返回值和异常。

```python
from nonebot.message import on_called_api

@on_called_api
async def called_api(
    bot: Bot,
    exception: Exception | None,
    api: str,
    data: dict,
    result: any,
):
    if exception:
        print(f"API 调用失败: {api}, 错误: {exception}")
    else:
        print(f"API 调用成功: {api}, 结果: {result}")
```

## 钩子函数的依赖注入

所有钩子函数都支持 NoneBot 的依赖注入系统：

```python
from nonebot.message import event_preprocessor
from nonebot.adapters import Bot, Event
from nonebot.params import Depends

async def get_user_level(event: Event) -> int:
    user_id = event.get_user_id()
    return await fetch_level(user_id)

@event_preprocessor
async def check_level(
    bot: Bot,
    event: Event,
    level: int = Depends(get_user_level),
):
    if level < 1:
        raise IgnoredException("用户等级不足")
```

## 完整示例

### 全局日志系统

```python
import time
from nonebot import get_driver
from nonebot.message import (
    event_preprocessor,
    event_postprocessor,
    run_preprocessor,
    run_postprocessor,
    on_calling_api,
    on_called_api,
)
from nonebot.adapters import Bot, Event
from nonebot.matcher import Matcher

driver = get_driver()
event_timers: dict[str, float] = {}

@driver.on_startup
async def startup():
    print("[LOG] NoneBot 启动")

@driver.on_shutdown
async def shutdown():
    print("[LOG] NoneBot 关闭")

@driver.on_bot_connect
async def bot_connect(bot: Bot):
    print(f"[LOG] Bot 上线: {bot.self_id}")

@driver.on_bot_disconnect
async def bot_disconnect(bot: Bot):
    print(f"[LOG] Bot 离线: {bot.self_id}")

@event_preprocessor
async def log_event_start(event: Event):
    event_id = id(event)
    event_timers[str(event_id)] = time.time()
    print(f"[LOG] 事件开始: {event.get_event_name()}")

@event_postprocessor
async def log_event_end(event: Event):
    event_id = str(id(event))
    elapsed = time.time() - event_timers.pop(event_id, time.time())
    print(f"[LOG] 事件结束: {event.get_event_name()} ({elapsed:.3f}s)")

@run_preprocessor
async def log_matcher_start(matcher: Matcher):
    print(f"[LOG] Matcher 开始: {matcher.plugin_name}")

@run_postprocessor
async def log_matcher_end(matcher: Matcher, exception: Exception | None):
    status = "失败" if exception else "成功"
    print(f"[LOG] Matcher {status}: {matcher.plugin_name}")

@on_calling_api
async def log_api_call(bot: Bot, api: str, data: dict):
    print(f"[LOG] API 调用: {api}")

@on_called_api
async def log_api_result(bot: Bot, api: str, exception: Exception | None, result: any):
    status = "失败" if exception else "成功"
    print(f"[LOG] API {status}: {api}")
```

### 全局黑名单与频率限制

```python
import time
from collections import defaultdict
from nonebot.message import event_preprocessor
from nonebot.adapters import Event
from nonebot.exception import IgnoredException

BLACKLIST: set[str] = {"bad_user_001"}
RATE_LIMIT = 5  # 每分钟最多5条
user_messages: dict[str, list[float]] = defaultdict(list)

@event_preprocessor
async def global_filter(event: Event):
    user_id = event.get_user_id()

    # 黑名单检查
    if user_id in BLACKLIST:
        raise IgnoredException(f"黑名单用户: {user_id}")

    # 频率限制
    now = time.time()
    timestamps = user_messages[user_id]
    # 清理一分钟前的记录
    timestamps[:] = [t for t in timestamps if now - t < 60]

    if len(timestamps) >= RATE_LIMIT:
        raise IgnoredException(f"用户 {user_id} 触发频率限制")

    timestamps.append(now)
```

### API 请求日志与重试

```python
import asyncio
from nonebot.message import on_calling_api, on_called_api
from nonebot.adapters import Bot

api_retry_count: dict[str, int] = {}

@on_called_api
async def retry_failed_api(
    bot: Bot,
    exception: Exception | None,
    api: str,
    data: dict,
    result: any,
):
    if exception and api.startswith("send_"):
        key = f"{api}:{id(data)}"
        count = api_retry_count.get(key, 0)
        if count < 3:
            api_retry_count[key] = count + 1
            await asyncio.sleep(1)
            print(f"[重试] {api} 第 {count + 1} 次")
            await getattr(bot, api)(**data)
        else:
            api_retry_count.pop(key, None)
            print(f"[放弃] {api} 已重试 3 次")
```
