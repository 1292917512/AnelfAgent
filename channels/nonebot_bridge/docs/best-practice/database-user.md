# 用户指南

本文档面向使用了 `nonebot-plugin-orm` 插件的 NoneBot 项目用户，介绍如何配置数据库、使用 CLI 命令管理迁移。

## 创建新项目

### 1. 初始化项目

```bash
nb init
```

按照向导创建项目后，安装 ORM 插件及数据库驱动：

```bash
pip install nonebot-plugin-orm[sqlite]
```

### 2. 加载插件

在 `pyproject.toml` 中添加：

```toml
[tool.nonebot]
plugins = ["nonebot_plugin_orm"]
```

### 3. 安装使用 ORM 的插件

```bash
# 安装一个使用了 ORM 的插件
nb plugin install nonebot-plugin-xxx
```

### 4. 执行数据库迁移

首次使用或安装了新的 ORM 插件后，必须执行迁移：

```bash
nb orm upgrade
```

这会自动执行所有已安装插件的数据库迁移脚本，创建或更新所需的数据表。

### 5. 运行 Bot

```bash
nb run
```

## 卸载插件

卸载使用了 ORM 的插件时，建议先回滚该插件的数据库迁移：

```bash
# 回滚指定插件的所有迁移到初始状态
nb orm downgrade <plugin_name>@base

# 例如
nb orm downgrade nonebot_plugin_xxx@base
```

然后再卸载插件：

```bash
nb plugin uninstall nonebot-plugin-xxx
```

> **注意**：`@base` 表示回滚到迁移基线（即撤销该插件创建的所有表）。如果不回滚就直接卸载，数据库中会残留不再使用的表。

## CLI 命令详解

### heads

显示所有迁移分支（即各插件）的最新迁移版本：

```bash
nb orm heads
```

输出示例：

```
nonebot_plugin_xxx@head (head)
nonebot_plugin_yyy@head (head)
```

每个使用 ORM 的插件会有自己独立的迁移分支（branch label），`heads` 命令可以查看所有分支的最新版本。

### upgrade

将数据库升级到最新版本：

```bash
# 升级所有分支到最新
nb orm upgrade

# 仅升级指定插件
nb orm upgrade nonebot_plugin_xxx@head

# 升级到指定的 revision
nb orm upgrade abc123
```

### downgrade

将数据库降级到指定版本：

```bash
# 降级指定插件到基线（删除所有表）
nb orm downgrade nonebot_plugin_xxx@base

# 降级到指定 revision
nb orm downgrade abc123

# 降级一个版本
nb orm downgrade -1
```

### check

检查数据库是否有未执行的迁移脚本：

```bash
nb orm check
```

- 如果数据库已是最新，输出 `No new upgrade operations detected.` 并返回退出码 `0`
- 如果有待执行的迁移，输出详情并返回非零退出码

在 CI/CD 流水线中可以用此命令验证数据库状态：

```bash
nb orm check || echo "Database needs migration!"
```

### revision

生成新的迁移脚本（通常由插件开发者使用）：

```bash
nb orm revision --autogenerate -m "add user table"
```

### history

查看迁移历史：

```bash
nb orm history

# 显示详细信息
nb orm history --verbose
```

### current

显示数据库当前的迁移版本：

```bash
nb orm current
```

## 配置项

所有配置项均在 `.env` 或 `.env.prod` 文件中设置。

### sqlalchemy_database_url

数据库连接 URL，格式遵循 SQLAlchemy 的 [Database URLs](https://docs.sqlalchemy.org/en/20/core/engines.html#database-urls) 规范。

```dotenv
# SQLite（默认，无需配置）
SQLALCHEMY_DATABASE_URL=sqlite+aiosqlite:///data/bot.db

# PostgreSQL
SQLALCHEMY_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/bot

# MySQL
SQLALCHEMY_DATABASE_URL=mysql+aiomysql://user:pass@localhost:3306/bot
```

如果不配置此项，`nonebot-plugin-orm` 会使用默认的 SQLite 数据库，路径由 `nonebot-plugin-localstore` 管理。

### sqlalchemy_binds

多数据库绑定配置。当需要将不同插件的数据存储到不同的数据库时使用。

**简单格式**（值为 URL 字符串）：

```dotenv
SQLALCHEMY_BINDS={"analytics": "postgresql+psycopg://user:pass@localhost/analytics", "cache": "sqlite+aiosqlite:///cache.db"}
```

**完整格式**（值为引擎配置对象）：

```dotenv
SQLALCHEMY_BINDS={"analytics": {"url": "postgresql+psycopg://user:pass@localhost/analytics", "echo": true, "pool_size": 10}}
```

在 `.env` 文件中使用 JSON 格式配置：

```dotenv
SQLALCHEMY_BINDS='
{
  "analytics": "postgresql+psycopg://user:pass@localhost/analytics",
  "cache": "sqlite+aiosqlite:///cache.db"
}
'
```

插件开发者在模型中通过 `__tablename__` 和 `__bind_key__` 指定绑定：

```python
class AnalyticsLog(Model):
    __bind_key__ = "analytics"

    id: Mapped[int] = mapped_column(primary_key=True)
    event: Mapped[str]
```

> **多数据库示例**：将主要数据存储在 PostgreSQL，将缓存数据存储在 SQLite。

```dotenv
# 默认数据库（主要数据）
SQLALCHEMY_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/bot_main

# 绑定数据库（缓存、分析等）
SQLALCHEMY_BINDS={"cache": "sqlite+aiosqlite:///cache.db", "log": "postgresql+psycopg://user:pass@localhost:5432/bot_log"}
```

### sqlalchemy_engine_options

SQLAlchemy 引擎的额外配置选项，以 JSON 格式传入：

```dotenv
SQLALCHEMY_ENGINE_OPTIONS={"pool_size": 5, "max_overflow": 10, "pool_timeout": 30, "pool_recycle": 1800}
```

常用选项：

| 选项 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `pool_size` | `int` | `5` | 连接池大小 |
| `max_overflow` | `int` | `10` | 超出连接池大小后的最大连接数 |
| `pool_timeout` | `float` | `30` | 等待可用连接的超时时间（秒） |
| `pool_recycle` | `int` | `-1` | 连接回收时间（秒），`-1` 表示不回收 |
| `pool_pre_ping` | `bool` | `false` | 每次使用连接前执行 ping 检查 |

> **注意**：SQLite 使用 `StaticPool`，`pool_size` 和 `max_overflow` 对 SQLite 无效。

### sqlalchemy_echo

是否开启 SQL 语句日志输出，用于调试：

```dotenv
# 关闭（默认）
SQLALCHEMY_ECHO=false

# 开启（输出所有 SQL 语句）
SQLALCHEMY_ECHO=true
```

开启后，所有执行的 SQL 语句将通过 NoneBot 的日志系统输出，方便排查数据库相关问题。

> **警告**：生产环境中不建议开启 `SQLALCHEMY_ECHO`，因为会产生大量日志并影响性能。

## 配置示例

### 开发环境

```dotenv
# .env.dev
SQLALCHEMY_ECHO=true
```

使用默认的 SQLite 数据库，开启 SQL 日志，方便开发调试。

### 生产环境（PostgreSQL）

```dotenv
# .env.prod
SQLALCHEMY_DATABASE_URL=postgresql+psycopg://bot:secure_password@db.example.com:5432/nonebot
SQLALCHEMY_ENGINE_OPTIONS={"pool_size": 10, "max_overflow": 20, "pool_pre_ping": true, "pool_recycle": 3600}
SQLALCHEMY_ECHO=false
```

### 多数据库生产环境

```dotenv
# .env.prod
SQLALCHEMY_DATABASE_URL=postgresql+psycopg://bot:pass@localhost:5432/main
SQLALCHEMY_BINDS={"analytics": "postgresql+psycopg://bot:pass@localhost:5432/analytics"}
SQLALCHEMY_ENGINE_OPTIONS={"pool_size": 10, "pool_pre_ping": true}
SQLALCHEMY_ECHO=false
```

## 常见问题

### Q: 首次运行提示表不存在

执行 `nb orm upgrade` 后再启动 Bot。

### Q: 安装新插件后报错

安装使用 ORM 的新插件后，需要再次执行 `nb orm upgrade`。

### Q: 如何备份数据库

- **SQLite**：直接复制 `.db` 文件
- **PostgreSQL**：使用 `pg_dump`
- **MySQL**：使用 `mysqldump`

### Q: 如何从 SQLite 迁移到 PostgreSQL

1. 导出现有数据
2. 修改 `SQLALCHEMY_DATABASE_URL` 为 PostgreSQL URL
3. 执行 `nb orm upgrade` 创建表结构
4. 导入数据
