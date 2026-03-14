<!-- source: https://nonebot.dev/docs/tutorial/application -->

# 手动创建项目

在 [快速上手](../quick-start.md) 中，我们已经介绍了如何安装和使用 `nb-cli` 创建一个项目。在本章节中，我们将介绍如何在**不使用 `nb-cli`** 的方式创建一个机器人项目的最小实例并启动。

> **警告**：我们十分不推荐直接创建机器人项目，请优先考虑使用 nb-cli 进行项目创建。

## 机器人的基本组成

一个机器人项目的最小实例中至少需要包含以下内容：

| 组成部分 | 说明 |
|---------|------|
| **插件 Plugin** | 为机器人提供具体的功能 |
| **配置文件** | 存储机器人启动所需的配置 |
| **入口文件** | 初始化并运行机器人的 Python 文件 |

## 安装依赖

在创建项目前，首先需要将项目所需依赖安装至环境中。

### （可选）创建虚拟环境

以 `venv` 为例：

**Linux / macOS：**

```bash
python -m venv .venv --prompt nonebot2
source .venv/bin/activate
```

**Windows：**

```bash
python -m venv .venv --prompt nonebot2
.venv\Scripts\activate
```

### 安装 NoneBot2 以及驱动器

以 FastAPI 驱动器为例：

```bash
pip install "nonebot2[fastapi]"
```

> 驱动器包名可以在 [驱动器商店](https://nonebot.dev/store/drivers) 中找到，请替换方括号中的内容。

### 安装适配器

以 Console 适配器为例：

```bash
pip install nonebot-adapter-console
```

> 适配器包名可以在 [适配器商店](https://nonebot.dev/store/adapters) 中找到。

## 创建配置文件

配置文件用于存放 NoneBot 运行所需要的配置项，使用 [pydantic](https://docs.pydantic.dev/) 以及 [python-dotenv](https://saurabh-kumar.com/python-dotenv/) 来读取配置。

在项目文件夹中创建一个名为 `.env` 的文件，并写入以下内容：

```bash
HOST=0.0.0.0       # 配置 NoneBot 监听的 IP / 主机名
PORT=8080           # 配置 NoneBot 监听的端口
COMMAND_START=["/"] # 配置命令起始字符
COMMAND_SEP=["."]   # 配置命令分割字符
```

> 配置项需符合 dotenv 格式，复杂类型数据需使用 JSON 格式填写。

## 创建入口文件

入口文件（Entrypoint）用来初始化并运行机器人。需要完成框架的初始化、注册适配器、加载插件等工作。

> 如果你使用 `nb-cli` 创建项目，入口文件不会被创建，该文件功能会被 `nb run` 命令代替。

在项目文件夹中创建一个 `bot.py` 文件，并写入以下内容：

```python
import nonebot
from nonebot.adapters.console import Adapter as ConsoleAdapter

# 初始化 NoneBot
nonebot.init()

# 注册适配器
driver = nonebot.get_driver()
driver.register_adapter(ConsoleAdapter)

# 在这里加载插件
nonebot.load_builtin_plugins("echo")  # 内置插件
# nonebot.load_plugin("thirdparty_plugin")  # 第三方插件
# nonebot.load_plugins("awesome_bot/plugins")  # 本地插件

if __name__ == "__main__":
    nonebot.run()
```

### 各部分说明

| 代码 | 说明 |
|------|------|
| `nonebot.init()` | 初始化 NoneBot 框架，读取配置文件 |
| `nonebot.get_driver()` | 获取驱动器实例 |
| `driver.register_adapter(Adapter)` | 注册协议适配器 |
| `nonebot.load_builtin_plugins()` | 加载内置插件 |
| `nonebot.load_plugin()` | 加载第三方插件 |
| `nonebot.load_plugins()` | 加载目录下的本地插件 |
| `nonebot.run()` | 启动 NoneBot |

## 运行机器人

在项目文件夹中，使用配置好环境的 Python 解释器运行入口文件：

**Windows：**

```bash
# 激活虚拟环境（未使用虚拟环境时跳过此行）
.venv\Scripts\activate
# 运行机器人
python bot.py
```

**Linux / macOS：**

```bash
source .venv/bin/activate
python bot.py
```

如果你后续使用了 `nb-cli`，你仍可以使用 `nb run` 命令来运行机器人，`nb-cli` 会自动检测入口文件 `bot.py` 是否存在并运行。

## NoneBot 架构概览

使用 NoneBot 框架搭建的机器人具有以下基本组成部分：

```
┌─────────────────────────────────────┐
│         NoneBot 机器人框架主体        │
│  负责连接各个组成部分，提供基本功能     │
│                                     │
│  ┌──────────┐  ┌──────────────────┐ │
│  │  Driver   │  │    Adapter       │ │
│  │  驱动器    │──│    适配器         │ │
│  │  HTTP通信  │  │  消息格式转换     │ │
│  └──────────┘  └──────────────────┘ │
│                                     │
│  ┌──────────────────────────────┐   │
│  │         Plugin 插件           │   │
│  │  机器人的功能实现              │   │
│  │  负责处理事件并进行操作        │   │
│  └──────────────────────────────┘   │
└─────────────────────────────────────┘
```

### 组件详解

| 组件 | 说明 |
|------|------|
| **Driver（驱动器）** | 客户端/服务端的功能实现，负责接收和发送消息（通常为 HTTP 通信） |
| **Adapter（适配器）** | 驱动器的上层，负责将平台消息与 NoneBot 事件/操作系统的消息格式相互转换 |
| **Plugin（插件）** | 机器人的功能实现，通常负责处理事件并进行一系列的操作 |
| **NoneBot 框架主体** | 负责连接各个组成部分，提供基本的机器人功能 |

除 NoneBot 机器人框架主体外，其他部分均可按需选择、互相搭配，但由于平台的兼容性问题，部分插件可能仅在某些特定平台上可用（这由插件编写者决定）。

### 事件处理流程

```
平台消息
  ↓
Driver（驱动器）接收消息
  ↓
Adapter（适配器）转换为 NoneBot Event
  ↓
NoneBot 主体分发事件
  ↓
Plugin（插件）中的 Matcher 匹配事件
  ↓
执行事件处理函数（Handler）
  ↓
通过 Adapter 和 Driver 发送回复
  ↓
平台接收回复
```
