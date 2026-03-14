# 发布插件

本文档介绍如何开发、打包并发布一个 NoneBot 插件到 PyPI 和 NoneBot 插件商店。

## 命名规范

NoneBot 插件遵循以下命名约定：

| 格式 | 用途 | 示例 |
|------|------|------|
| `nonebot-plugin-xxx` | PyPI 包名（连字符） | `nonebot-plugin-weather` |
| `nonebot_plugin_xxx` | Python 包名（下划线） | `nonebot_plugin_weather` |

> **注意**：PyPI 包名和 Python 包名必须保持一致（仅连字符与下划线的区别）。

## 项目结构

标准的 NoneBot 插件项目结构：

```
nonebot-plugin-xxx/
├── nonebot_plugin_xxx/
│   ├── __init__.py          # 插件入口
│   ├── config.py            # 插件配置
│   ├── model.py             # 数据模型（可选）
│   └── ...
├── tests/
│   ├── conftest.py
│   └── test_xxx.py
├── pyproject.toml           # 项目配置
├── README.md                # 项目说明
└── LICENSE                  # 开源许可证
```

### `__init__.py` 示例

```python
from nonebot import get_plugin_config, require
from nonebot.plugin import PluginMetadata

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="示例插件",
    description="这是一个示例插件",
    usage="使用 /hello 命令",
    type="application",
    homepage="https://github.com/username/nonebot-plugin-xxx",
    config=Config,
    supported_adapters=None,  # None 表示支持所有适配器
)

plugin_config = get_plugin_config(Config)
```

## 项目模板

社区提供了多种项目模板，可以快速初始化插件项目：

### RF-Tar-Railt/nonebot-plugin-template（PDM）

```bash
# 使用 PDM 管理依赖
pdm init --copier gh:RF-Tar-Railt/nonebot-plugin-template
```

### fllesser/nonebot-plugin-template（uv）

```bash
# 使用 uv 管理依赖
uvx copier copy gh:fllesser/nonebot-plugin-template ./my-plugin
```

### A-kirami/nonebot-plugin-template（Poetry）

```bash
# 使用 Poetry 管理依赖
# 通过 GitHub 仓库模板创建
# 访问 https://github.com/A-kirami/nonebot-plugin-template
# 点击 "Use this template"
```

## 从零开始创建

### 1. 创建项目

```bash
# 使用 PDM
mkdir nonebot-plugin-xxx && cd nonebot-plugin-xxx
pdm init
mkdir nonebot_plugin_xxx
touch nonebot_plugin_xxx/__init__.py

# 使用 Poetry
poetry new nonebot-plugin-xxx
mv nonebot_plugin_xxx/ nonebot-plugin-xxx/nonebot_plugin_xxx/

# 使用 uv
uv init nonebot-plugin-xxx
cd nonebot-plugin-xxx
mkdir nonebot_plugin_xxx
touch nonebot_plugin_xxx/__init__.py
```

### 2. 配置 pyproject.toml

```toml
[project]
name = "nonebot-plugin-xxx"
version = "0.1.0"
description = "NoneBot 示例插件"
readme = "README.md"
license = {text = "MIT"}
requires-python = ">=3.10"
authors = [
    {name = "Your Name", email = "your@email.com"},
]
dependencies = [
    "nonebot2>=2.3.0",
]

[project.urls]
Homepage = "https://github.com/username/nonebot-plugin-xxx"
Repository = "https://github.com/username/nonebot-plugin-xxx"

[build-system]
requires = ["pdm-backend"]
build-backend = "pdm.backend"
```

### 3. 配置 GitHub Actions 权限

在仓库的 `Settings > Actions > General > Workflow permissions` 中：

- 选择 **Read and write permissions**
- 勾选 **Allow GitHub Actions to create and approve pull requests**

### 4. 全局替换

在模板项目中执行全局替换：

| 占位符 | 替换为 | 示例 |
|--------|--------|------|
| `plugin-xxx` | 你的插件名（连字符） | `plugin-weather` |
| `plugin_xxx` | 你的插件名（下划线） | `plugin_weather` |
| `username` | 你的 GitHub 用户名 | `myname` |
| `your@email.com` | 你的邮箱 | `me@example.com` |

### 5. 安装依赖

```bash
# PDM
pdm install

# Poetry
poetry install

# uv
uv sync
```

### 6. 版本管理

#### 使用 bump-my-version

```bash
pip install bump-my-version

# pyproject.toml 配置
```

```toml
[tool.bumpversion]
current_version = "0.1.0"
commit = true
tag = true

[[tool.bumpversion.files]]
filename = "pyproject.toml"
search = 'version = "{current_version}"'
replace = 'version = "{new_version}"'

[[tool.bumpversion.files]]
filename = "nonebot_plugin_xxx/__init__.py"
search = '__version__ = "{current_version}"'
replace = '__version__ = "{new_version}"'
```

```bash
# 升级补丁版本 0.1.0 -> 0.1.1
bump-my-version bump patch

# 升级次版本 0.1.1 -> 0.2.0
bump-my-version bump minor

# 升级主版本 0.2.0 -> 1.0.0
bump-my-version bump major
```

#### 使用 pdm-bump

```bash
pdm plugin add pdm-bump

# 升级版本
pdm bump patch  # 0.1.0 -> 0.1.1
pdm bump minor  # 0.1.1 -> 0.2.0
pdm bump major  # 0.2.0 -> 1.0.0
```

#### 手动管理

直接修改 `pyproject.toml` 中的 `version` 字段。

### 7. 发布到 PyPI

```bash
# PDM
pdm publish

# Poetry
poetry publish --build

# 使用 twine
pip install build twine
python -m build
twine upload dist/*
```

#### GitHub Actions 自动发布

```yaml
# .github/workflows/publish.yml
name: Publish to PyPI

on:
  release:
    types: [published]

jobs:
  publish:
    runs-on: ubuntu-latest
    permissions:
      id-token: write
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install build tools
        run: pip install build
      - name: Build
        run: python -m build
      - name: Publish
        uses: pypa/gh-action-pypi-publish@release/v1
```

## 基本要求

### 正确加载

插件必须能够通过以下方式正确加载：

```python
nonebot.load_plugin("nonebot_plugin_xxx")
```

### 使用 require() 声明依赖

如果插件依赖其他 NoneBot 插件，必须使用 `require()` 显式声明：

```python
from nonebot import require

# 在导入依赖插件之前调用 require()
require("nonebot_plugin_localstore")
require("nonebot_plugin_orm")

import nonebot_plugin_localstore as store
from nonebot_plugin_orm import Model
```

### 零配置加载

插件应该在不配置任何选项的情况下也能正常加载（即使功能受限）。可以在配置中提供合理的默认值：

```python
from pydantic import BaseModel

class Config(BaseModel):
    weather_api_key: str = ""  # 空字符串作为默认值
    weather_default_city: str = "北京"
```

### PluginMetadata

每个插件必须在 `__init__.py` 中声明 `PluginMetadata`：

```python
from nonebot.plugin import PluginMetadata

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="天气查询",
    description="查询指定城市的天气信息",
    usage="发送 /天气 <城市名> 查询天气",
    type="application",
    homepage="https://github.com/username/nonebot-plugin-weather",
    config=Config,
    supported_adapters=None,
)
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | `str` | 是 | 插件显示名称 |
| `description` | `str` | 是 | 插件简短描述 |
| `usage` | `str` | 是 | 使用说明 |
| `type` | `str` | 否 | 插件类型：`application`（功能插件）或 `library`（库插件） |
| `homepage` | `str` | 否 | 插件主页 URL |
| `config` | `type[BaseModel]` | 否 | 配置类 |
| `supported_adapters` | `set[str] \| None` | 否 | 支持的适配器集合，`None` 表示全部 |
| `extra` | `dict` | 否 | 额外信息 |

### inherit_supported_adapters()

如果插件依赖了限定适配器的插件，使用 `inherit_supported_adapters()` 自动继承适配器支持范围：

```python
from nonebot.plugin import PluginMetadata, inherit_supported_adapters

__plugin_meta__ = PluginMetadata(
    name="示例插件",
    description="依赖 OneBot 适配器的插件",
    usage="/hello",
    type="application",
    homepage="https://github.com/username/nonebot-plugin-xxx",
    config=Config,
    supported_adapters=inherit_supported_adapters(
        "nonebot.adapters.onebot.v11",
        "nonebot.adapters.onebot.v12",
    ),
)
```

### README 要求

README.md 应包含以下内容：

- 插件名称和描述
- 安装方法
- 配置说明（列出所有配置项及默认值）
- 使用方法和命令列表
- 示例截图（可选）
- 许可证

## 质量要求

### 依赖管理

- 在 `pyproject.toml` 中正确声明所有运行时依赖
- 使用版本范围约束（如 `nonebot2>=2.3.0`），避免锁定特定版本
- 不要依赖未发布或私有包

### 禁止同步阻塞操作

插件中的所有 I/O 操作必须使用异步方式：

```python
# 错误：同步 HTTP 请求
import requests
resp = requests.get("https://api.example.com")

# 正确：异步 HTTP 请求
import httpx
async with httpx.AsyncClient() as client:
    resp = await client.get("https://api.example.com")
```

```python
# 错误：同步文件操作（大文件）
with open("large_file.txt") as f:
    data = f.read()

# 正确：使用 anyio 异步文件操作
import anyio
data = await anyio.Path("large_file.txt").read_text()
```

### 使用 localstore 管理文件存储

插件的持久化文件应使用 `nonebot-plugin-localstore` 管理路径：

```python
from nonebot import require
require("nonebot_plugin_localstore")

import nonebot_plugin_localstore as store

# 获取插件数据目录
data_dir = store.get_plugin_data_dir()
cache_dir = store.get_plugin_cache_dir()

# 在数据目录中读写文件
data_file = data_dir / "data.json"
```

## 提交到插件商店

### 前提条件

1. 插件已发布到 PyPI
2. 满足上述所有基本要求和质量要求
3. 有完善的 README

### 提交流程

1. 前往 [NoneBot 商店](https://nonebot.dev/store/plugins)
2. 点击「发布插件」
3. 填写插件信息：
   - **PyPI 包名**：如 `nonebot-plugin-weather`
   - **import 包名**：如 `nonebot_plugin_weather`
   - **项目主页**：GitHub 仓库地址
   - **标签**：为插件添加合适的分类标签
4. 提交后会自动创建 Pull Request 到 [nonebot/registry](https://github.com/nonebot/registry)
5. 自动化检查通过后，等待维护者审核合并

### 自动化检查项

商店会对提交的插件执行以下自动化检查：

- 包是否能在 PyPI 上找到
- 插件是否能正确加载
- 是否声明了 `PluginMetadata`
- 配置类是否可用
- 支持的适配器是否正确

如果检查未通过，请根据错误提示修复问题后重新提交。
