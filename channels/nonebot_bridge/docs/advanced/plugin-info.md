# 插件信息

NoneBot 提供 `PluginMetadata` 类用于声明插件的元数据信息，帮助用户了解插件功能、使用方式以及兼容性。

## PluginMetadata

### 基本用法

在插件模块的顶层定义 `__plugin_meta__` 变量：

```python
from nonebot.plugin import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="天气查询",
    description="查询指定城市的天气信息",
    usage="/weather <城市名>",
)
```

### 完整字段

```python
from nonebot.plugin import PluginMetadata

from .config import WeatherConfig

__plugin_meta__ = PluginMetadata(
    name="天气查询",
    description="查询指定城市的天气信息",
    usage="""\
/weather <城市名> — 查询天气
/weather_sub <城市名> — 订阅天气推送
/weather_unsub — 取消订阅
""",
    type="application",
    homepage="https://github.com/example/nonebot-plugin-weather",
    config=WeatherConfig,
    supported_adapters={"~onebot.v11", "~onebot.v12", "~qq"},
    extra={"author": "example", "version": "1.0.0"},
)
```

## 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | `str` | 是 | 插件名称，用于展示 |
| `description` | `str` | 是 | 插件简短描述 |
| `usage` | `str` | 是 | 插件使用说明 |
| `type` | `str \| None` | 否 | 插件类型 |
| `homepage` | `str \| None` | 否 | 插件主页 URL |
| `config` | `type[BaseModel] \| None` | 否 | 插件配置类（Pydantic BaseModel 子类） |
| `supported_adapters` | `set[str] \| None` | 否 | 支持的适配器集合，`None` 表示支持所有 |
| `extra` | `dict[str, Any]` | 否 | 额外信息（默认 `{}`） |

## type 字段

`type` 字段标识插件的用途类型：

| 值 | 说明 |
|----|------|
| `"application"` | 应用插件，直接为用户提供功能 |
| `"library"` | 库插件，为其他插件提供基础功能 |
| `None` | 未分类 |

```python
# 应用插件
__plugin_meta__ = PluginMetadata(
    name="签到",
    description="每日签到插件",
    usage="/签到",
    type="application",
)

# 库插件
__plugin_meta__ = PluginMetadata(
    name="数据库工具",
    description="为其他插件提供数据库连接管理",
    usage="见文档",
    type="library",
)
```

## config 字段

`config` 字段关联插件的配置模型。当指定后，用户可以通过 NoneBot 的配置体系（`.env` 文件）配置该插件。

### 定义配置类

```python
# config.py
from pydantic import BaseModel


class WeatherConfig(BaseModel):
    weather_api_key: str = ""
    weather_default_city: str = "北京"
    weather_cache_ttl: int = 600
```

### 关联到 PluginMetadata

```python
# __init__.py
from nonebot.plugin import PluginMetadata
from nonebot import get_plugin_config

from .config import WeatherConfig

__plugin_meta__ = PluginMetadata(
    name="天气查询",
    description="查询天气",
    usage="/weather <城市名>",
    config=WeatherConfig,
)

# 获取配置
config = get_plugin_config(WeatherConfig)
print(config.weather_api_key)
```

### 对应的 .env 配置

```dotenv
WEATHER_API_KEY=your_api_key
WEATHER_DEFAULT_CITY=上海
WEATHER_CACHE_TTL=300
```

## supported_adapters 字段

声明插件支持的适配器列表，值为适配器模块名的集合。`None` 表示支持所有适配器。

### 适配器名称简写

使用 `~` 前缀表示 `nonebot.adapters.` 的简写：

```python
supported_adapters={"~onebot.v11", "~qq", "~telegram"}
# 等价于
supported_adapters={
    "nonebot.adapters.onebot.v11",
    "nonebot.adapters.qq",
    "nonebot.adapters.telegram",
}
```

### 示例

```python
# 支持所有适配器
__plugin_meta__ = PluginMetadata(
    name="通用插件",
    description="...",
    usage="...",
    supported_adapters=None,  # 默认值
)

# 仅支持 OneBot V11
__plugin_meta__ = PluginMetadata(
    name="QQ 专属插件",
    description="...",
    usage="...",
    supported_adapters={"~onebot.v11"},
)

# 支持多个适配器
__plugin_meta__ = PluginMetadata(
    name="多平台插件",
    description="...",
    usage="...",
    supported_adapters={"~onebot.v11", "~onebot.v12", "~qq", "~telegram"},
)
```

## inherit_supported_adapters

当插件依赖其他插件时，可以使用 `inherit_supported_adapters()` 函数自动继承依赖插件的适配器支持列表，取交集以确保兼容性。

### 用法

```python
from nonebot.plugin import PluginMetadata, inherit_supported_adapters

__plugin_meta__ = PluginMetadata(
    name="高级天气",
    description="基于天气插件的高级功能",
    usage="/weather_adv",
    supported_adapters=inherit_supported_adapters(
        "nonebot_plugin_weather",
        "nonebot_plugin_database",
    ),
)
```

### 工作原理

`inherit_supported_adapters()` 会：

1. 查找所有指定插件的 `PluginMetadata.supported_adapters`
2. 如果某个插件的 `supported_adapters` 为 `None`（支持全部），则忽略该约束
3. 对所有非 `None` 的集合取**交集**
4. 如果所有插件都为 `None`，返回 `None`

```python
# 插件 A: supported_adapters = {"~onebot.v11", "~qq", "~telegram"}
# 插件 B: supported_adapters = {"~onebot.v11", "~qq"}
# 插件 C: supported_adapters = None

inherit_supported_adapters("plugin_a", "plugin_b", "plugin_c")
# 结果: {"~onebot.v11", "~qq"}
# 解释: A ∩ B = {"~onebot.v11", "~qq"}，C 为 None 不参与交集
```

## 访问插件元数据

### 获取插件对象

```python
import nonebot

# 获取指定插件
plugin = nonebot.get_plugin("plugin_name")
if plugin and plugin.metadata:
    print(plugin.metadata.name)
    print(plugin.metadata.description)
    print(plugin.metadata.usage)

# 获取所有插件
plugins = nonebot.get_loaded_plugins()
for plugin in plugins:
    if plugin.metadata:
        print(f"{plugin.metadata.name}: {plugin.metadata.description}")
```

### Plugin 对象属性

```python
plugin = nonebot.get_plugin("my_plugin")

plugin.name        # 插件模块名
plugin.module      # 插件模块对象
plugin.module_name # 插件模块完整路径
plugin.metadata    # PluginMetadata 或 None
plugin.parent_plugin  # 父插件（嵌套插件场景）
plugin.sub_plugins    # 子插件集合
plugin.matcher        # 插件注册的所有 Matcher
```

## extra 字段

`extra` 字典可用于存放任意额外信息：

```python
__plugin_meta__ = PluginMetadata(
    name="我的插件",
    description="...",
    usage="...",
    extra={
        "author": "Anelf",
        "version": "2.0.0",
        "license": "MIT",
        "tags": ["utility", "fun"],
    },
)
```
