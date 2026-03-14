<!-- source: https://nonebot.dev/docs/tutorial/create-plugin -->

# 插件编写准备

在正式编写插件之前，我们需要先了解一下插件的概念。

## 插件结构

在 NoneBot 中，插件即是 Python 的一个 [模块（module）](https://docs.python.org/zh-cn/3/glossary.html#term-module)。NoneBot 会在导入时对这些模块做一些特殊的处理使得他们成为一个插件。插件间应尽量减少耦合，可以进行有限制的相互调用，NoneBot 能够正确解析插件间的依赖关系。

### 单文件插件

一个普通的 `.py` 文件即可以作为一个插件：

```
📂 plugins
└── 📜 foo.py
```

模块 `foo` 已经可以被称为一个插件了，尽管它还什么都没做。

### 包插件

一个包含 `__init__.py` 的文件夹即是一个常规 Python [包（package）](https://docs.python.org/zh-cn/3/glossary.html#term-regular-package)：

```
📂 plugins
└── 📂 foo
    └── 📜 __init__.py
```

包 `foo` 同样是一个合法的插件，插件内容可以在 `__init__.py` 文件中编写。

### 包插件的常用结构

对于较复杂的插件，推荐使用包插件的形式：

```
📂 plugins
└── 📂 weather
    ├── 📜 __init__.py    # 插件入口
    ├── 📜 config.py      # 插件配置
    ├── 📜 data_source.py # 数据获取
    └── 📜 model.py       # 数据模型
```

## 创建插件

### 使用 nb-cli 创建

通过 `nb-cli` 命令从完整模板创建：

```bash
$ nb plugin create
[?] 插件名称: weather
[?] 使用嵌套插件? (y/N) N
[?] 请输入插件存储位置: awesome_bot/plugins
```

`nb-cli` 会在 `awesome_bot/plugins` 目录下创建一个名为 `weather` 的文件夹：

```
📦 awesome-bot
├── 📂 .venv
├── 📂 awesome_bot
│   └── 📂 plugins
│       └── 📂 weather
│           ├── 📜 __init__.py
│           └── 📜 config.py
├── 📜 .env.prod
├── 📜 pyproject.toml
└── 📜 README.md
```

### 手动创建

也可以手动在插件目录下新建空白文件。

## 插件元数据

每个插件都可以定义元数据，用于描述插件的基本信息：

```python
from nonebot.plugin import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="天气查询",
    description="查询指定城市的天气信息",
    usage="/天气 <城市名>",
)
```

## 项目准备

### 使用 bootstrap 模板

如果在之前的 [快速上手](../quick-start.md) 中已经使用 `bootstrap` 模板创建了项目，需要做如下修改：

1. 在项目目录中创建一个两层文件夹 `awesome_bot/plugins`

```
📦 awesome-bot
├── 📂 .venv
├── 📂 awesome_bot
│   └── 📂 plugins
├── 📜 .env.prod
├── 📜 pyproject.toml
└── 📜 README.md
```

2. 修改 `pyproject.toml` 文件中的 `nonebot` 配置项：

```toml
[tool.nonebot]
plugin_dirs = ["awesome_bot/plugins"]
```

### 使用手动创建的项目

如果手动创建了相关文件，需要修改 `bot.py`：

```python
# 在这里加载插件
nonebot.load_builtin_plugins("echo")  # 内置插件
nonebot.load_plugins("awesome_bot/plugins")  # 本地插件
```

## 加载插件

> **警告**：请勿在插件被加载前 `import` 插件模块，这会导致 NoneBot 无法将其转换为插件而出现意料之外的情况。

加载插件是在机器人入口文件中完成的，需要在框架初始化之后、运行之前进行。

> 加载的插件模块名称（插件文件名或文件夹名）不能相同，且每一个插件只能被加载一次，重复加载将会导致异常。

### load_plugin

通过点分割模块名称或使用 `pathlib` 的 `Path` 对象来加载插件。通常用于加载第三方插件或者项目插件。

```python
from pathlib import Path

nonebot.load_plugin("path.to.your.plugin")  # 加载第三方插件
nonebot.load_plugin(Path("./path/to/your/plugin.py"))  # 加载项目插件
```

> 本地插件的路径应该为相对机器人入口文件（通常为 `bot.py`）可导入的。

### load_plugins

加载传入插件目录中的所有插件，通常用于加载一系列本地编写的项目插件。

```python
nonebot.load_plugins("src/plugins", "path/to/your/plugins")
```

> 插件目录应该为相对机器人入口文件可导入的。

### load_all_plugins

以上两种方式的混合，加载所有传入的插件模块名称，以及所有给定目录下的插件。

```python
nonebot.load_all_plugins(
    ["path.to.your.plugin"],
    ["path/to/your/plugins"]
)
```

### load_from_json

通过 JSON 文件加载插件，是 `load_all_plugins` 的 JSON 变种。

JSON 文件格式：

```json
{
  "plugins": ["path.to.your.plugin"],
  "plugin_dirs": ["path/to/your/plugins"]
}
```

加载方式：

```python
nonebot.load_from_json("plugin_config.json", encoding="utf-8")
```

### load_from_toml

通过 TOML 文件加载插件，是 `load_all_plugins` 的 TOML 变种。

TOML 文件格式：

```toml
[tool.nonebot]
plugin_dirs = ["path/to/your/plugins"]

[tool.nonebot.plugins]
"@local" = ["path.to.your.plugin"]                         # 本地插件
"nonebot-plugin-someplugin" = ["nonebot_plugin_someplugin"] # 商店插件
```

加载方式：

```python
nonebot.load_from_toml("pyproject.toml", encoding="utf-8")
```

### load_builtin_plugin

加载一个内置插件，传入的插件名必须为 NoneBot 内置插件。该方法是 `load_plugin` 的封装。

```python
nonebot.load_builtin_plugin("echo")
```

### load_builtin_plugins

加载传入插件列表中的所有内置插件。

```python
nonebot.load_builtin_plugins("echo", "single_session")
```

## 加载方式速查表

| 方法 | 参数 | 用途 |
|------|------|------|
| `load_plugin` | 模块名或 Path | 加载单个第三方/本地插件 |
| `load_plugins` | 目录路径(可多个) | 加载目录下所有插件 |
| `load_all_plugins` | 模块名列表 + 目录列表 | 混合加载 |
| `load_from_json` | JSON 文件路径 | 从 JSON 配置加载 |
| `load_from_toml` | TOML 文件路径 | 从 TOML 配置加载 |
| `load_builtin_plugin` | 内置插件名 | 加载单个内置插件 |
| `load_builtin_plugins` | 内置插件名(可多个) | 加载多个内置插件 |

## 其他加载方式

有关其他插件加载的方式，可参考：

- [跨插件访问](https://nonebot.dev/docs/advanced/requiring)
- [嵌套插件](https://nonebot.dev/docs/advanced/plugin-nesting)
