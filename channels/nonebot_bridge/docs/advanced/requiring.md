# 跨插件访问

NoneBot 提供 `require()` 函数用于在插件间建立依赖关系，实现跨插件的模块导入。

## 为什么需要 require()

NoneBot 使用 Import Hook 机制来追踪插件的加载状态。直接使用 `import` 语句导入其他插件的模块可能导致：

1. 目标插件尚未加载，导入失败
2. 插件依赖关系无法被 NoneBot 正确追踪
3. 加载顺序不可预测

因此，**必须先调用 `require()` 再进行 `import`**。

## 基本用法

### require() 函数

```python
from nonebot import require

# 声明依赖并确保插件已加载
require("nonebot_plugin_apscheduler")

# 然后才能安全导入
from nonebot_plugin_apscheduler import scheduler
```

### 完整示例

```python
# my_plugin/__init__.py
from nonebot import require, on_command
from nonebot.plugin import PluginMetadata

# 声明依赖
require("nonebot_plugin_apscheduler")
require("nonebot_plugin_datastore")

# 安全导入依赖插件的内容
from nonebot_plugin_apscheduler import scheduler
from nonebot_plugin_datastore import get_data_file

__plugin_meta__ = PluginMetadata(
    name="定时提醒",
    description="定时提醒功能",
    usage="/remind <时间> <内容>",
)

# 使用依赖插件提供的功能
@scheduler.scheduled_job("interval", minutes=30)
async def check_reminders():
    data_file = get_data_file("reminders.json")
    # ...处理提醒逻辑
```

## require() 的工作流程

1. 检查目标插件是否已加载
2. 如果未加载，尝试加载该插件
3. 如果加载失败，抛出异常
4. 返回目标插件的 `Plugin` 对象

```python
from nonebot import require

# require() 返回 Plugin 对象
plugin = require("nonebot_plugin_apscheduler")
print(plugin.name)       # 插件名
print(plugin.module)     # 插件模块
print(plugin.metadata)   # 插件元数据
```

## 常见使用模式

### 使用第三方插件的调度器

```python
from nonebot import require

require("nonebot_plugin_apscheduler")

from nonebot_plugin_apscheduler import scheduler

@scheduler.scheduled_job("cron", hour=8, minute=0)
async def morning_greeting():
    # 每天早上8点执行
    ...
```

### 使用数据存储插件

```python
from nonebot import require

require("nonebot_plugin_localstore")

from nonebot_plugin_localstore import get_cache_dir, get_data_dir, get_config_dir

cache_dir = get_cache_dir("my_plugin")
data_dir = get_data_dir("my_plugin")
```

### 使用通用消息插件

```python
from nonebot import require

require("nonebot_plugin_alconna")

from nonebot_plugin_alconna import UniMessage, on_alconna
from arclet.alconna import Alconna, Args
```

## 错误处理

### 插件不存在

```python
from nonebot import require

try:
    require("nonexistent_plugin")
except RuntimeError as e:
    print(f"插件加载失败: {e}")
```

### 常见错误

| 错误 | 原因 | 解决方法 |
|------|------|---------|
| `RuntimeError` | 插件未安装或找不到 | 确认插件已安装 (`pip install`) |
| `ImportError` | 在 `require()` 之前 `import` | 将 `import` 移到 `require()` 之后 |
| 循环依赖 | 插件 A 依赖 B，B 又依赖 A | 重构代码，消除循环 |

## 重要规则

### 必须先 require() 再 import

```python
# ❌ 错误：直接导入可能在插件未加载时失败
from nonebot_plugin_apscheduler import scheduler

# ✅ 正确：先 require 确保插件已加载
from nonebot import require
require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler
```

### require() 应在模块顶层调用

```python
# ✅ 正确：在模块顶层调用
from nonebot import require
require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

# ❌ 错误：在函数内部延迟调用（可能遗漏）
async def handler():
    require("nonebot_plugin_apscheduler")
    from nonebot_plugin_apscheduler import scheduler
    ...
```

### 每个依赖只需 require 一次

```python
# ✅ 正确
require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

# ⚠️ 多次 require 不会报错，但没有必要
require("nonebot_plugin_apscheduler")
require("nonebot_plugin_apscheduler")  # 冗余
```

## Import Hook 机制

NoneBot 使用 Python 的 Import Hook（`sys.meta_path`）来拦截插件模块的导入行为：

1. 当加载插件时，NoneBot 注册一个自定义 Finder
2. 该 Finder 拦截所有对插件模块的 `import` 操作
3. 记录导入关系，建立插件依赖图
4. 确保插件的加载生命周期被正确管理

这就是为什么需要使用 `require()` 而非直接 `import` 的根本原因 — 它确保 NoneBot 能正确追踪和管理插件间的依赖关系。

## 与 inherit_supported_adapters 配合

声明依赖的同时，可以使用 `inherit_supported_adapters()` 继承依赖插件的适配器支持：

```python
from nonebot import require
from nonebot.plugin import PluginMetadata, inherit_supported_adapters

require("nonebot_plugin_alconna")
require("nonebot_plugin_apscheduler")

from nonebot_plugin_alconna import UniMessage
from nonebot_plugin_apscheduler import scheduler

__plugin_meta__ = PluginMetadata(
    name="我的插件",
    description="...",
    usage="...",
    supported_adapters=inherit_supported_adapters(
        "nonebot_plugin_alconna",
        "nonebot_plugin_apscheduler",
    ),
)
```
