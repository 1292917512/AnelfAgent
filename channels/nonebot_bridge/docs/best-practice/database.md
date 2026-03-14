# 数据库

[`nonebot-plugin-orm`](https://github.com/nonebot/plugin-orm) 为 NoneBot 提供了基于 [SQLAlchemy](https://www.sqlalchemy.org/) 和 [Alembic](https://alembic.sqlalchemy.org/) 的 ORM 支持，实现了数据库模型定义、自动迁移、依赖注入等功能，让插件开发者可以方便地进行数据库操作。

## 特性

- 基于 SQLAlchemy 2.0 的异步 ORM 支持
- 基于 Alembic 的自动数据库迁移
- 内置依赖注入，简化 Session 管理
- 支持多数据库后端（SQLite、PostgreSQL、MySQL/MariaDB）
- 支持多数据库绑定（multi-bind）
- 与 NoneBot 插件生态深度集成

## 安装

### 使用 nb-cli

```bash
nb plugin install nonebot-plugin-orm

# 安装特定数据库驱动
nb plugin install nonebot-plugin-orm[sqlite]
nb plugin install nonebot-plugin-orm[postgresql]
nb plugin install nonebot-plugin-orm[mysql]
```

### 使用 pip

```bash
pip install nonebot-plugin-orm

# 安装特定数据库驱动
pip install nonebot-plugin-orm[sqlite]
pip install nonebot-plugin-orm[postgresql]
pip install nonebot-plugin-orm[mysql]
```

### 使用 pdm

```bash
pdm add nonebot-plugin-orm

# 安装特定数据库驱动
pdm add nonebot-plugin-orm[sqlite]
pdm add nonebot-plugin-orm[postgresql]
pdm add nonebot-plugin-orm[mysql]
```

## 数据库驱动

### SQLite（默认）

SQLite 是默认的数据库后端，使用 [aiosqlite](https://github.com/omnilib/aiosqlite) 作为异步驱动。

安装驱动：

```bash
pip install nonebot-plugin-orm[sqlite]
# 等价于
pip install aiosqlite
```

默认情况下，数据库文件存储在 NoneBot 数据目录下，路径由 `nonebot-plugin-localstore` 管理。

配置示例：

```dotenv
# .env 或 .env.prod
# 使用默认路径（推荐，无需配置）

# 或指定自定义路径
SQLALCHEMY_DATABASE_URL=sqlite+aiosqlite:///path/to/db.sqlite3

# 使用绝对路径
SQLALCHEMY_DATABASE_URL=sqlite+aiosqlite:////absolute/path/to/db.sqlite3

# Windows 路径
SQLALCHEMY_DATABASE_URL=sqlite+aiosqlite:///C:/path/to/db.sqlite3
```

### PostgreSQL

使用 [psycopg](https://www.psycopg.org/)（psycopg3）作为异步驱动。

安装驱动：

```bash
pip install nonebot-plugin-orm[postgresql]
# 等价于
pip install psycopg[binary]
```

配置示例：

```dotenv
SQLALCHEMY_DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/dbname

# 使用 Unix socket
SQLALCHEMY_DATABASE_URL=postgresql+psycopg://user:password@/dbname?host=/var/run/postgresql
```

### MySQL / MariaDB

使用 [aiomysql](https://github.com/aio-libs/aiomysql) 作为异步驱动。

安装驱动：

```bash
pip install nonebot-plugin-orm[mysql]
# 等价于
pip install aiomysql
```

配置示例：

```dotenv
SQLALCHEMY_DATABASE_URL=mysql+aiomysql://user:password@localhost:3306/dbname

# 指定字符集
SQLALCHEMY_DATABASE_URL=mysql+aiomysql://user:password@localhost:3306/dbname?charset=utf8mb4
```

## 数据库驱动对比

| 数据库 | 驱动 | extras 名称 | 适用场景 |
|--------|------|-------------|----------|
| SQLite | aiosqlite | `sqlite` | 开发环境、小型项目 |
| PostgreSQL | psycopg | `postgresql` | 生产环境、高并发 |
| MySQL/MariaDB | aiomysql | `mysql` | 生产环境 |

## 快速开始

### 1. 安装插件

```bash
pip install nonebot-plugin-orm[sqlite]
```

### 2. 加载插件

在 `pyproject.toml` 中：

```toml
[tool.nonebot]
plugins = ["nonebot_plugin_orm"]
```

或在 `bot.py` 中：

```python
import nonebot

nonebot.init()
nonebot.load_plugin("nonebot_plugin_orm")
```

### 3. 定义模型

在插件中定义数据模型：

```python
from nonebot_plugin_orm import Model
from sqlalchemy.orm import Mapped, mapped_column

class User(Model):
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    score: Mapped[int] = mapped_column(default=0)
```

### 4. 运行迁移

```bash
# 生成迁移脚本
nb orm revision --autogenerate -m "initial"

# 执行迁移
nb orm upgrade
```

### 5. 使用 Session

```python
from nonebot import on_command
from nonebot_plugin_orm import async_scoped_session

matcher = on_command("score")

@matcher.handle()
async def handle(session: async_scoped_session):
    user = await session.get(User, 1)
    if user:
        await matcher.finish(f"Score: {user.score}")
```

## CLI 命令

`nonebot-plugin-orm` 通过 `nb-cli` 提供了一组数据库管理命令：

| 命令 | 说明 |
|------|------|
| `nb orm upgrade` | 将数据库升级到最新版本 |
| `nb orm downgrade <rev>` | 将数据库降级到指定版本 |
| `nb orm revision --autogenerate -m "<msg>"` | 自动生成迁移脚本 |
| `nb orm heads` | 显示所有迁移分支的最新版本 |
| `nb orm check` | 检查数据库是否需要迁移 |
| `nb orm history` | 显示迁移历史 |
| `nb orm current` | 显示当前数据库版本 |

### nb orm upgrade

将数据库升级到最新版本（执行所有未执行的迁移脚本）：

```bash
# 升级到最新版本
nb orm upgrade

# 升级到指定版本
nb orm upgrade <revision>
```

### nb orm check

检查数据库是否有尚未执行的迁移：

```bash
nb orm check
```

如果有未执行的迁移，会输出提示信息并返回非零退出码，适合在 CI/CD 中使用。

## 更多内容

- [用户指南](./database-user.md) - 详细配置与 CLI 使用
- [开发者指南](./database-developer.md) - 模型定义、迁移与 Session 管理
- [测试](./database-developer-test.md) - 多数据库后端测试配置
- [依赖注入](./database-developer-dependency.md) - 高级依赖注入用法
