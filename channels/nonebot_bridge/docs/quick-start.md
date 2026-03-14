<!-- source: https://nonebot.dev/docs/quick-start -->

# 快速上手

> **前提条件**
>
> - 请确保你的 Python 版本 >= 3.9
> - 我们强烈建议使用虚拟环境进行开发
> - 如果没有使用虚拟环境，请确保已经卸载可能存在的 NoneBot v1：`pip uninstall nonebot`

在本章节中，我们将介绍如何使用脚手架来创建一个 NoneBot 简易项目。项目将基于 `nb-cli` 脚手架运行，并允许我们从商店安装插件。

## 安装脚手架

确保你已经安装了 Python 3.9 及以上版本，然后在命令行中执行以下命令：

### 1. 安装 pipx

```bash
python -m pip install --user pipx
python -m pipx ensurepath
```

> 如果在此步骤的输出中出现了 "open a new terminal" 或者 "re-login" 字样，那么请关闭当前终端并重新打开一个新的终端。

### 2. 安装脚手架

```bash
pipx install nb-cli
```

安装完成后，你可以在命令行使用 `nb` 命令来使用脚手架。如果出现无法找到命令的情况（例如出现 "Command not found" 字样），请参考 [pipx 文档](https://pypa.github.io/pipx/) 检查你的环境变量。

### 直接使用 pip 安装（替代方案）

如果不使用 `nb-cli`，可以直接通过 pip 安装 NoneBot2 及驱动器：

```bash
pip install "nonebot2[fastapi]"
```

## 创建项目

使用脚手架来创建一个项目：

```bash
nb create
```

这一指令将会执行创建项目的流程，你将会看到一些询问：

### 项目模板

```
[?] 选择一个要使用的模板: bootstrap (初学者或用户)
```

- `bootstrap`：简单的项目模板，能够安装商店插件（适合初学者）
- `simple`：需要自行编写插件的项目模板

### 项目名称

```
[?] 项目名称: awesome-bot
```

### 其他选项

> 请注意，多选项使用**空格**选中或取消，**回车**确认。

```
[?] 要使用哪些适配器? Console (基于终端的交互式适配器)
[?] 要使用哪些驱动器? FastAPI (FastAPI 驱动器)
[?] 要使用什么本地存储策略? 用户全局 (默认，适用于单用户下单实例)
[?] 立即安装依赖? (Y/n) Yes
[?] 创建虚拟环境? (Y/n) Yes
```

### 选择内置插件

```
[?] 要使用哪些内置插件? echo
```

`echo` 是一个简单的复读回显插件，可以用于测试机器人是否正常运行。

### 项目结构

创建完成后，项目结构如下：

```
📦 awesome-bot
├── 📂 .venv
├── 📂 awesome_bot
│   └── 📂 plugins
├── 📜 .env.prod
├── 📜 pyproject.toml
└── 📜 README.md
```

## 运行项目

在项目创建完成后，你可以在**项目目录**中使用以下命令来运行项目：

```bash
nb run
```

生成的项目中默认使用了 `FastAPI` 驱动器和 `Console` 适配器，你之后可以自行修改配置或安装其他适配器。

### 支持自动重载

使用 `--reload` 参数可以在代码更改时自动重新运行：

```bash
nb run --reload
```

## 尝试使用

在项目运行起来后，`Console` 适配器会在你的终端启动交互模式，你可以直接在输入框中输入 `/echo hello world` 来测试机器人是否正常运行。

```
/echo hello world
```

机器人将会回复：

```
hello world
```

## 基本配置

项目中的 `.env` 或 `.env.prod` 文件用于存储配置项，使用 dotenv 格式。常用配置项：

```bash
# 驱动器配置（可选值: ~fastapi, ~httpx, ~websockets, ~aiohttp 等）
DRIVER=~fastapi

# 监听的 IP / 主机名
HOST=0.0.0.0

# 监听的端口
PORT=8080

# 命令起始字符（JSON 数组格式）
COMMAND_START=["/"]

# 命令分割字符（JSON 数组格式）
COMMAND_SEP=["."]
```

> 复杂类型数据需使用 JSON 格式填写。

## 下一步

- 查看 [适配器商店](https://nonebot.dev/store/adapters) 选择平台适配器
- 查看 [插件商店](https://nonebot.dev/store/plugins) 安装更多功能
- 阅读 [机器人的构成](./tutorial/application.md) 了解 NoneBot 架构
