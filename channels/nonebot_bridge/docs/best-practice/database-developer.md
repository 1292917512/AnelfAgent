# 开发者指南

本文档面向 NoneBot 插件开发者，介绍如何使用 `nonebot-plugin-orm` 定义数据模型、生成迁移脚本、管理 Session。

## 模型定义

### 基本模型

使用 `nonebot_plugin_orm` 提供的 `Model` 基类定义模型，它基于 SQLAlchemy 2.0 的声明式映射：

```python
from nonebot_plugin_orm import Model
from sqlalchemy.orm import Mapped, mapped_column

class User(Model):
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    score: Mapped[int] = mapped_column(default=0)
```

### 自动表名

`Model` 基类会根据类名自动生成表名，规则为将驼峰命名转换为蛇形命名，并加上插件名前缀：

| 类名 | 生成的表名 |
|------|-----------|
| `User` | `nonebot_plugin_xxx_user` |
| `GameScore` | `nonebot_plugin_xxx_game_score` |
| `HTTPLog` | `nonebot_plugin_xxx_http_log` |

也可以手动指定表名：

```python
class User(Model):
    __tablename__ = "my_custom_table"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
```

### 字段类型

SQLAlchemy 常用类型映射：

| Python 类型 | SQLAlchemy 类型 | 数据库类型（SQLite） |
|-------------|----------------|---------------------|
| `int` | `Integer` | `INTEGER` |
| `str` | `String` | `VARCHAR` |
| `float` | `Float` | `FLOAT` |
| `bool` | `Boolean` | `BOOLEAN` |
| `datetime` | `DateTime` | `DATETIME` |
| `bytes` | `LargeBinary` | `BLOB` |
| `Decimal` | `Numeric` | `NUMERIC` |

### 可选字段

使用 `Optional` 表示可为 `NULL` 的字段：

```python
from typing import Optional
from datetime import datetime

class User(Model):
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    email: Mapped[Optional[str]] = mapped_column(default=None)
    created_at: Mapped[Optional[datetime]] = mapped_column(default=None)
```

### 完整模型示例

```python
from datetime import datetime
from typing import Optional

from nonebot_plugin_orm import Model
from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

class User(Model):
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), unique=True)
    nickname: Mapped[str] = mapped_column(String(128))
    bio: Mapped[Optional[str]] = mapped_column(Text, default=None)
    score: Mapped[int] = mapped_column(default=0)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)
    updated_at: Mapped[Optional[datetime]] = mapped_column(default=None, onupdate=datetime.now)
```

### 关联关系

```python
from nonebot_plugin_orm import Model
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

class User(Model):
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    posts: Mapped[list["Post"]] = relationship(back_populates="author")

class Post(Model):
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]
    author_id: Mapped[int] = mapped_column(ForeignKey("nonebot_plugin_xxx_user.id"))
    author: Mapped["User"] = relationship(back_populates="posts")
```

## 数据库迁移

### 首次迁移

定义好模型后，生成首次迁移脚本：

```bash
nb orm revision --autogenerate -m "initial migration"
```

该命令会在插件目录下生成迁移脚本，通常位于 `<plugin>/migrations/versions/` 目录中。

### 迁移脚本结构

生成的迁移脚本结构如下：

```python
"""initial migration

Revision ID: abc123def456
Revises:
Create Date: 2024-01-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "abc123def456"
down_revision = None
branch_labels = ("nonebot_plugin_xxx",)
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nonebot_plugin_xxx_user",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("nonebot_plugin_xxx_user")
```

关键要素：

- `revision`：迁移版本的唯一标识
- `down_revision`：上一个迁移版本（`None` 表示首次迁移）
- `branch_labels`：迁移分支标签，通常为插件包名
- `upgrade()`：升级操作（创建表、添加列等）
- `downgrade()`：降级操作（删除表、移除列等，用于回滚）

### 后续迁移

修改模型后，生成新的迁移脚本：

```bash
# 修改了 User 模型，添加了 email 字段
nb orm revision --autogenerate -m "add email to user"
```

生成的迁移脚本：

```python
def upgrade() -> None:
    op.add_column(
        "nonebot_plugin_xxx_user",
        sa.Column("email", sa.String(), nullable=True),
    )

def downgrade() -> None:
    op.drop_column("nonebot_plugin_xxx_user", "email")
```

### 开发环境跳过启动检查

在开发阶段，如果频繁修改模型但不想每次都生成迁移脚本，可以设置环境变量跳过启动时的迁移检查：

```dotenv
ALEMBIC_STARTUP_CHECK=false
```

> **警告**：仅在开发环境中使用此配置！生产环境务必保持启动检查开启，以确保数据库 schema 与模型一致。

## Session 管理

### 通过依赖注入获取 Session

`nonebot-plugin-orm` 提供了 `async_scoped_session` 类型，可以通过 NoneBot 的依赖注入系统自动获取：

```python
from nonebot import on_command
from nonebot_plugin_orm import async_scoped_session

from .model import User

cmd = on_command("get_user")

@cmd.handle()
async def handle(session: async_scoped_session):
    user = await session.get(User, 1)
    if user:
        await cmd.finish(f"User: {user.name}, Score: {user.score}")
    else:
        await cmd.finish("User not found")
```

### 基本 CRUD 操作

#### 查询

```python
from sqlalchemy import select

@cmd.handle()
async def handle(session: async_scoped_session):
    # 按主键查询
    user = await session.get(User, 1)

    # 条件查询
    stmt = select(User).where(User.name == "Alice")
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()

    # 查询多条记录
    stmt = select(User).where(User.score > 100)
    result = await session.execute(stmt)
    users = result.scalars().all()

    # 排序与分页
    stmt = select(User).order_by(User.score.desc()).limit(10).offset(0)
    result = await session.execute(stmt)
    top_users = result.scalars().all()
```

#### 新增

```python
@cmd.handle()
async def handle(session: async_scoped_session):
    user = User(name="Alice", score=100)
    session.add(user)
    await session.commit()
    await cmd.finish(f"Created user: {user.name}")
```

#### 更新

```python
@cmd.handle()
async def handle(session: async_scoped_session):
    user = await session.get(User, 1)
    if user:
        user.score += 10
        await session.commit()
        await cmd.finish(f"Updated score: {user.score}")
```

#### 删除

```python
@cmd.handle()
async def handle(session: async_scoped_session):
    user = await session.get(User, 1)
    if user:
        await session.delete(user)
        await session.commit()
        await cmd.finish(f"Deleted user: {user.name}")
```

### Session 生命周期

> **重要**：`nonebot-plugin-orm` 的 Session 作用域与事件处理的生命周期绑定。在同一次事件处理中，通过依赖注入获取的 Session 是同一个实例，事件处理结束后 Session 会自动关闭。

```python
from nonebot import on_command
from nonebot_plugin_orm import async_scoped_session

cmd = on_command("test")

@cmd.handle()
async def step1(session: async_scoped_session):
    user = User(name="Test")
    session.add(user)
    await session.commit()

@cmd.got("confirm")
async def step2(session: async_scoped_session):
    # 此处的 session 与 step1 中的是同一个实例
    users = (await session.execute(select(User))).scalars().all()
```

> **警告**：ORM 的 Session 与 NoneBot 的 Session（会话状态管理）是完全不同的概念，不要混淆。ORM Session 管理数据库连接和事务，NoneBot Session 管理用户交互状态。

### 异常处理

```python
from sqlalchemy.exc import IntegrityError

@cmd.handle()
async def handle(session: async_scoped_session):
    try:
        user = User(user_id="12345", name="Alice")
        session.add(user)
        await session.commit()
    except IntegrityError:
        await session.rollback()
        await cmd.finish("User already exists")
```

## 依赖注入

`nonebot-plugin-orm` 提供了 `SQLDepends` 辅助函数，简化常见的查询操作。

### 基本用法

```python
from nonebot.params import Depends
from nonebot_plugin_orm import SQLDepends
from sqlalchemy import select

from .model import User

async def get_user(
    user: User = SQLDepends(select(User).where(User.user_id == "12345"))
):
    return user
```

### 结合事件参数

```python
from nonebot.adapters import Event
from nonebot.params import Depends
from nonebot_plugin_orm import SQLDepends
from sqlalchemy import select

from .model import User

def get_user_query(event: Event):
    return select(User).where(User.user_id == event.get_user_id())

@cmd.handle()
async def handle(
    user: User | None = SQLDepends(get_user_query)
):
    if user:
        await cmd.finish(f"Welcome back, {user.name}!")
    else:
        await cmd.finish("New user detected!")
```

### 类依赖

```python
from nonebot.adapters import Event
from nonebot.params import Depends
from nonebot_plugin_orm import async_scoped_session
from sqlalchemy import select

from .model import User

class UserDep:
    def __init__(self, event: Event, session: async_scoped_session):
        self.event = event
        self.session = session

    async def __call__(self) -> User | None:
        stmt = select(User).where(User.user_id == self.event.get_user_id())
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
```

更多依赖注入的高级用法，请参阅 [依赖注入](./database-developer-dependency.md)。

## 完整插件示例

```python
from nonebot import require, on_command
from nonebot.adapters import Event

require("nonebot_plugin_orm")

from nonebot_plugin_orm import Model, async_scoped_session
from sqlalchemy import String, select
from sqlalchemy.orm import Mapped, mapped_column

class Score(Model):
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), unique=True)
    value: Mapped[int] = mapped_column(default=0)

add_score = on_command("加分")
show_score = on_command("积分")
rank = on_command("排行榜")

@add_score.handle()
async def handle_add(event: Event, session: async_scoped_session):
    user_id = event.get_user_id()
    stmt = select(Score).where(Score.user_id == user_id)
    result = await session.execute(stmt)
    score = result.scalar_one_or_none()

    if score is None:
        score = Score(user_id=user_id, value=1)
        session.add(score)
    else:
        score.value += 1

    await session.commit()
    await add_score.finish(f"当前积分: {score.value}")

@show_score.handle()
async def handle_show(event: Event, session: async_scoped_session):
    user_id = event.get_user_id()
    score = (await session.execute(
        select(Score).where(Score.user_id == user_id)
    )).scalar_one_or_none()

    if score:
        await show_score.finish(f"你的积分: {score.value}")
    else:
        await show_score.finish("你还没有积分")

@rank.handle()
async def handle_rank(session: async_scoped_session):
    stmt = select(Score).order_by(Score.value.desc()).limit(10)
    result = await session.execute(stmt)
    scores = result.scalars().all()

    if not scores:
        await rank.finish("暂无排行数据")

    lines = [f"{i+1}. {s.user_id}: {s.value}" for i, s in enumerate(scores)]
    await rank.finish("积分排行榜:\n" + "\n".join(lines))
```
