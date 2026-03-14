# 定时任务

[`nonebot-plugin-apscheduler`](https://github.com/nonebot/plugin-apscheduler) 是对 [APScheduler](https://apscheduler.readthedocs.io/en/3.x/) 的 NoneBot 封装，提供基于装饰器和函数调用的定时任务能力。

## 安装

```bash
# nb-cli
nb plugin install nonebot-plugin-apscheduler

# pip
pip install nonebot-plugin-apscheduler

# poetry
poetry add nonebot-plugin-apscheduler

# pdm
pdm add nonebot-plugin-apscheduler
```

## 快速开始

### 声明依赖

在插件中使用前，需要通过 `require` 声明对 `nonebot_plugin_apscheduler` 的依赖：

```python
from nonebot import require

require("nonebot_plugin_apscheduler")

from nonebot_plugin_apscheduler import scheduler
```

> **注意**：`require` 必须在 `import scheduler` 之前调用，否则可能因插件未加载而导入失败。

### 使用装饰器添加任务

```python
from nonebot import require

require("nonebot_plugin_apscheduler")

from nonebot_plugin_apscheduler import scheduler


@scheduler.scheduled_job("cron", hour="*/2", id="my_task")
async def my_task():
    print("每两小时执行一次")


@scheduler.scheduled_job("interval", minutes=30, id="interval_task")
async def interval_task():
    print("每30分钟执行一次")


@scheduler.scheduled_job("date", run_date="2025-12-31 23:59:59", id="once_task")
async def once_task():
    print("在指定时间执行一次")
```

### 使用 add_job 动态添加任务

```python
from nonebot import require

require("nonebot_plugin_apscheduler")

from nonebot_plugin_apscheduler import scheduler


async def my_dynamic_task():
    print("动态添加的任务")


# 在事件处理函数或其他位置动态添加
scheduler.add_job(
    my_dynamic_task,
    "interval",
    seconds=60,
    id="dynamic_task",
    replace_existing=True,
)
```

## 触发器类型

### Cron 触发器

基于 cron 表达式，适合固定时间点的周期性任务。

| 参数 | 类型 | 说明 |
|------|------|------|
| `year` | `int\|str` | 年（4位数字） |
| `month` | `int\|str` | 月（1-12） |
| `day` | `int\|str` | 日（1-31） |
| `week` | `int\|str` | ISO 周数（1-53） |
| `day_of_week` | `int\|str` | 星期几（0-6 或 mon-sun） |
| `hour` | `int\|str` | 时（0-23） |
| `minute` | `int\|str` | 分（0-59） |
| `second` | `int\|str` | 秒（0-59） |
| `start_date` | `datetime\|str` | 最早触发时间 |
| `end_date` | `datetime\|str` | 最晚触发时间 |
| `timezone` | `tzinfo\|str` | 时区 |
| `jitter` | `int` | 随机延迟秒数（防止任务扎堆） |

**Cron 表达式语法**：

| 表达式 | 说明 | 示例 |
|--------|------|------|
| `*` | 匹配所有值 | `hour="*"` 每小时 |
| `*/n` | 每隔 n | `minute="*/5"` 每5分钟 |
| `a-b` | 范围 | `hour="9-17"` 9点到17点 |
| `a,b,c` | 枚举 | `day_of_week="mon,wed,fri"` |
| `last` | 最后一个 | `day="last"` 每月最后一天 |

```python
# 每天早上 8:30 执行
@scheduler.scheduled_job("cron", hour=8, minute=30, id="morning_report")
async def morning_report():
    ...

# 每周一、三、五 18:00 执行
@scheduler.scheduled_job("cron", day_of_week="mon,wed,fri", hour=18, id="weekly_task")
async def weekly_task():
    ...

# 每月 1 号 0:00 执行
@scheduler.scheduled_job("cron", day=1, hour=0, minute=0, id="monthly_task")
async def monthly_task():
    ...
```

### Interval 触发器

固定时间间隔执行。

| 参数 | 类型 | 说明 |
|------|------|------|
| `weeks` | `int` | 间隔周数 |
| `days` | `int` | 间隔天数 |
| `hours` | `int` | 间隔小时数 |
| `minutes` | `int` | 间隔分钟数 |
| `seconds` | `int` | 间隔秒数 |
| `start_date` | `datetime\|str` | 开始时间 |
| `end_date` | `datetime\|str` | 结束时间 |
| `timezone` | `tzinfo\|str` | 时区 |
| `jitter` | `int` | 随机延迟秒数 |

```python
# 每 10 分钟执行一次
@scheduler.scheduled_job("interval", minutes=10, id="check_update")
async def check_update():
    ...

# 每 2 小时 30 分钟执行一次
@scheduler.scheduled_job("interval", hours=2, minutes=30, id="long_interval")
async def long_interval():
    ...
```

### Date 触发器

在指定时间执行一次。

| 参数 | 类型 | 说明 |
|------|------|------|
| `run_date` | `datetime\|str` | 执行时间 |
| `timezone` | `tzinfo\|str` | 时区 |

```python
from datetime import datetime

@scheduler.scheduled_job("date", run_date=datetime(2025, 12, 31, 23, 59, 59), id="new_year")
async def new_year():
    print("新年快乐！")
```

## 任务管理

```python
# 暂停任务
scheduler.pause_job("my_task")

# 恢复任务
scheduler.resume_job("my_task")

# 删除任务
scheduler.remove_job("my_task")

# 修改任务触发器
scheduler.reschedule_job("my_task", trigger="cron", hour=6)

# 获取所有任务
jobs = scheduler.get_jobs()
for job in jobs:
    print(f"任务: {job.id}, 下次执行: {job.next_run_time}")
```

## 在定时任务中发送消息

定时任务中没有事件上下文，需要通过 Bot 对象直接调用 API 发送消息：

```python
import nonebot
from nonebot import require
from nonebot.adapters.onebot.v11 import Bot

require("nonebot_plugin_apscheduler")

from nonebot_plugin_apscheduler import scheduler


@scheduler.scheduled_job("cron", hour=8, id="daily_greeting")
async def daily_greeting():
    bot: Bot = nonebot.get_bot()
    await bot.send_group_msg(group_id=123456, message="早上好！")
```

如果有多个 Bot 实例：

```python
@scheduler.scheduled_job("cron", hour=8, id="daily_greeting_all")
async def daily_greeting_all():
    bots = nonebot.get_bots()
    for bot_id, bot in bots.items():
        try:
            await bot.send_group_msg(group_id=123456, message="早上好！")
        except Exception as e:
            print(f"Bot {bot_id} 发送失败: {e}")
```

## 配置项

在 `.env` 文件或 `nonebot` 配置中设置：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `apscheduler_autostart` | `bool` | `True` | 是否在 NoneBot 启动时自动启动调度器 |
| `apscheduler_log_level` | `int` | `30`（WARNING） | APScheduler 日志级别 |
| `apscheduler_config` | `dict` | `{}` | APScheduler 底层配置字典 |

```dotenv
# .env 示例
APSCHEDULER_AUTOSTART=true
APSCHEDULER_LOG_LEVEL=30
APSCHEDULER_CONFIG={"apscheduler.timezone": "Asia/Shanghai"}
```

### 日志级别参考

| 级别 | 值 |
|------|-----|
| DEBUG | 10 |
| INFO | 20 |
| WARNING | 30 |
| ERROR | 40 |
| CRITICAL | 50 |

### APScheduler 底层配置

`apscheduler_config` 支持传入 APScheduler 原生配置项，例如：

```dotenv
APSCHEDULER_CONFIG={"apscheduler.timezone": "Asia/Shanghai", "apscheduler.job_defaults.misfire_grace_time": 60}
```

常用底层配置：

| 配置键 | 说明 |
|--------|------|
| `apscheduler.timezone` | 调度器时区 |
| `apscheduler.job_defaults.coalesce` | 是否合并错过的执行（默认 `True`） |
| `apscheduler.job_defaults.max_instances` | 同一任务最大并发实例数（默认 `1`） |
| `apscheduler.job_defaults.misfire_grace_time` | 错过执行的容错秒数 |

## 完整示例

```python
from nonebot import get_plugin_config, require
from nonebot.plugin import PluginMetadata
from pydantic import BaseModel

require("nonebot_plugin_apscheduler")

from nonebot_plugin_apscheduler import scheduler


class Config(BaseModel):
    report_group_id: int = 0


__plugin_meta__ = PluginMetadata(
    name="定时报告",
    description="定时发送群报告",
    usage="自动运行，无需命令",
    config=Config,
)

config = get_plugin_config(Config)


@scheduler.scheduled_job("cron", hour=8, minute=0, id="daily_report")
async def daily_report():
    import nonebot

    if not config.report_group_id:
        return

    bot = nonebot.get_bot()
    await bot.send_group_msg(
        group_id=config.report_group_id,
        message="早安！今天也要元气满满哦~",
    )


@scheduler.scheduled_job("interval", minutes=5, id="health_check")
async def health_check():
    import nonebot

    bots = nonebot.get_bots()
    if not bots:
        nonebot.logger.warning("当前没有已连接的 Bot")
```
