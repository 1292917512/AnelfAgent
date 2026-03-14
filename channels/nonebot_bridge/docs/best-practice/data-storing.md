# 数据存储

[`nonebot-plugin-localstore`](https://github.com/nonebot/plugin-localstore) 提供跨平台的本地数据存储路径管理，基于 [platformdirs](https://github.com/platformdirs/platformdirs) 实现。插件可以通过统一 API 获取缓存、数据、配置目录，无需关心不同操作系统的路径差异。

## 安装

```bash
# nb-cli
nb plugin install nonebot-plugin-localstore

# pip
pip install nonebot-plugin-localstore

# poetry
poetry add nonebot-plugin-localstore

# pdm
pdm add nonebot-plugin-localstore
```

## 快速开始

```python
from nonebot import require

require("nonebot_plugin_localstore")

import nonebot_plugin_localstore as store

# 获取插件级别的缓存目录
cache_dir = store.get_plugin_cache_dir()

# 获取插件级别的数据目录
data_dir = store.get_plugin_data_dir()

# 获取插件级别的配置目录
config_dir = store.get_plugin_config_dir()
```

## API 参考

### 目录获取函数

| 函数 | 返回类型 | 说明 |
|------|----------|------|
| `get_cache_dir()` | `Path` | 获取 NoneBot 全局缓存目录 |
| `get_data_dir()` | `Path` | 获取 NoneBot 全局数据目录 |
| `get_config_dir()` | `Path` | 获取 NoneBot 全局配置目录 |
| `get_plugin_cache_dir()` | `Path` | 获取当前插件的缓存目录 |
| `get_plugin_data_dir()` | `Path` | 获取当前插件的数据目录 |
| `get_plugin_config_dir()` | `Path` | 获取当前插件的配置目录 |
| `get_plugin_cache_file(filename)` | `Path` | 获取当前插件缓存目录下的指定文件路径 |
| `get_plugin_data_file(filename)` | `Path` | 获取当前插件数据目录下的指定文件路径 |
| `get_plugin_config_file(filename)` | `Path` | 获取当前插件配置目录下的指定文件路径 |

### 三类目录的用途区分

| 类型 | 用途 | 可否安全删除 | 示例 |
|------|------|-------------|------|
| **cache** | 临时缓存数据，删除后可重建 | 是 | 图片缓存、API 响应缓存 |
| **data** | 持久化业务数据，删除后不可恢复 | 否 | 用户数据、统计记录 |
| **config** | 用户配置文件 | 否 | 插件个性化设置 |

## 使用示例

### 缓存文件读写

```python
from nonebot import require

require("nonebot_plugin_localstore")

import nonebot_plugin_localstore as store

cache_file = store.get_plugin_cache_file("api_cache.json")

# 写入缓存
import json

data = {"last_update": "2025-01-01", "results": [1, 2, 3]}
cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

# 读取缓存
if cache_file.exists():
    cached = json.loads(cache_file.read_text(encoding="utf-8"))
```

### 持久化数据存储

```python
from pathlib import Path

from nonebot import require

require("nonebot_plugin_localstore")

import nonebot_plugin_localstore as store

data_dir = store.get_plugin_data_dir()


def get_user_file(user_id: str) -> Path:
    """获取用户数据文件路径"""
    return data_dir / f"{user_id}.json"


def save_user_data(user_id: str, data: dict) -> None:
    import json

    file = get_user_file(user_id)
    file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_user_data(user_id: str) -> dict:
    import json

    file = get_user_file(user_id)
    if file.exists():
        return json.loads(file.read_text(encoding="utf-8"))
    return {}
```

### 配置文件管理

```python
from nonebot import require

require("nonebot_plugin_localstore")

import nonebot_plugin_localstore as store


def load_plugin_settings() -> dict:
    import json

    config_file = store.get_plugin_config_file("settings.json")
    if config_file.exists():
        return json.loads(config_file.read_text(encoding="utf-8"))
    # 默认配置
    default = {"enabled": True, "max_retry": 3, "timeout": 30}
    config_file.write_text(
        json.dumps(default, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return default
```

### 使用 pathlib.Path 操作

所有函数返回 `pathlib.Path` 对象，可以使用其丰富的 API：

```python
from nonebot import require

require("nonebot_plugin_localstore")

import nonebot_plugin_localstore as store

data_dir = store.get_plugin_data_dir()

# 创建子目录
images_dir = data_dir / "images"
images_dir.mkdir(parents=True, exist_ok=True)

# 遍历目录
for file in data_dir.glob("*.json"):
    print(f"找到数据文件: {file.name}")

# 检查文件是否存在
config_file = store.get_plugin_config_file("config.yaml")
if config_file.exists():
    print(f"配置文件大小: {config_file.stat().st_size} 字节")

# 删除缓存
cache_dir = store.get_plugin_cache_dir()
import shutil

if cache_dir.exists():
    shutil.rmtree(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
```

## 默认存储路径

在不同操作系统下，默认路径如下：

### Windows

| 类型 | 默认路径 |
|------|----------|
| cache | `C:\Users\<user>\AppData\Local\nonebot2\Cache\<plugin_name>` |
| data | `C:\Users\<user>\AppData\Local\nonebot2\<plugin_name>` |
| config | `C:\Users\<user>\AppData\Local\nonebot2\<plugin_name>` |

### macOS

| 类型 | 默认路径 |
|------|----------|
| cache | `~/Library/Caches/nonebot2/<plugin_name>` |
| data | `~/Library/Application Support/nonebot2/<plugin_name>` |
| config | `~/Library/Application Support/nonebot2/<plugin_name>` |

### Linux

| 类型 | 默认路径 |
|------|----------|
| cache | `~/.cache/nonebot2/<plugin_name>` |
| data | `~/.local/share/nonebot2/<plugin_name>` |
| config | `~/.config/nonebot2/<plugin_name>` |

## 配置项

在 `.env` 文件中配置：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `localstore_use_cwd` | `bool` | `False` | 是否使用当前工作目录作为存储根目录，而非系统标准路径 |
| `localstore_cache_dir` | `str\|None` | `None` | 自定义全局缓存目录路径 |
| `localstore_data_dir` | `str\|None` | `None` | 自定义全局数据目录路径 |
| `localstore_config_dir` | `str\|None` | `None` | 自定义全局配置目录路径 |
| `localstore_plugin_cache_dir` | `str\|None` | `None` | 自定义插件缓存目录路径 |
| `localstore_plugin_data_dir` | `str\|None` | `None` | 自定义插件数据目录路径 |
| `localstore_plugin_config_dir` | `str\|None` | `None` | 自定义插件配置目录路径 |

### 配置示例

```dotenv
# 使用当前工作目录存储数据（适合容器化部署）
LOCALSTORE_USE_CWD=true

# 自定义全局存储路径
LOCALSTORE_CACHE_DIR=/data/nonebot/cache
LOCALSTORE_DATA_DIR=/data/nonebot/data
LOCALSTORE_CONFIG_DIR=/data/nonebot/config

# 自定义插件存储路径（会覆盖全局路径 + 插件名的组合）
LOCALSTORE_PLUGIN_DATA_DIR=/data/nonebot/plugins/data
```

当设置 `localstore_use_cwd=True` 时，存储目录将变为：

| 类型 | 路径 |
|------|------|
| cache | `<cwd>/cache/<plugin_name>` |
| data | `<cwd>/data/<plugin_name>` |
| config | `<cwd>/config/<plugin_name>` |

## 结合数据库使用

配合 SQLite 等数据库存储数据的典型模式：

```python
import sqlite3
from contextlib import contextmanager
from typing import Generator

from nonebot import require

require("nonebot_plugin_localstore")

import nonebot_plugin_localstore as store

db_path = store.get_plugin_data_file("database.db")


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_points (
                user_id TEXT PRIMARY KEY,
                points INTEGER DEFAULT 0
            )
            """
        )
```
