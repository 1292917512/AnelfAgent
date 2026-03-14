# 依赖注入

本文档详细介绍 `nonebot-plugin-orm` 的依赖注入系统，包括 Session 类型选择、`SQLDepends` 用法以及所有类型注解模式。

## Session 类型

### AsyncSession vs async_scoped_session

`nonebot-plugin-orm` 提供两种 Session 类型用于依赖注入：

| 特性 | `AsyncSession` | `async_scoped_session` |
|------|----------------|----------------------|
| 作用域 | 每次注入创建新实例 | 事件处理生命周期内共享 |
| 事务隔离 | 独立事务 | 共享事务 |
| 异常回滚 | 仅影响当前 Session | 影响所有使用该 Session 的处理器 |
| 适用场景 | 需要事务隔离的操作 | 一般数据库操作 |

#### async_scoped_session（推荐）

在同一次事件处理中，所有通过依赖注入获取的 `async_scoped_session` 是同一个实例：

```python
from nonebot import on_command
from nonebot_plugin_orm import async_scoped_session

cmd = on_command("test")

@cmd.handle()
async def step1(session: async_scoped_session):
    # session 实例 A
    user = User(name="test")
    session.add(user)
    await session.commit()

@cmd.got("input")
async def step2(session: async_scoped_session):
    # 与 step1 中的 session 是同一个实例 A
    users = (await session.execute(select(User))).scalars().all()
```

#### AsyncSession

每次注入都会创建新的 Session 实例，适合需要独立事务的场景：

```python
from sqlalchemy.ext.asyncio import AsyncSession

@cmd.handle()
async def handle(session: AsyncSession):
    # 独立的 Session 实例
    # 异常不会影响其他 Session
    try:
        user = User(name="test")
        session.add(user)
        await session.commit()
    except Exception:
        await session.rollback()
```

### 回滚行为

**async_scoped_session 的回滚**：

由于 `async_scoped_session` 在事件处理生命周期内共享，一个处理器中的回滚会影响整个 Session：

```python
@cmd.handle()
async def handle(session: async_scoped_session):
    user = User(name="test")
    session.add(user)
    await session.flush()  # 写入但未提交

    # 如果后续操作失败并回滚...
    await session.rollback()
    # 上面 add 的 user 也会被回滚
```

**AsyncSession 的回滚**：

独立的 Session 实例，回滚只影响当前 Session：

```python
@cmd.handle()
async def handle(session1: AsyncSession):
    # session1 独立运行
    user = User(name="test")
    session1.add(user)
    await session1.rollback()
    # 不影响其他 Session
```

## SQLDepends

`SQLDepends` 是 `nonebot-plugin-orm` 提供的依赖注入辅助函数，用于将 SQLAlchemy 查询语句直接作为依赖注入的结果。

### 基本用法

使用 `select()` 语句：

```python
from nonebot_plugin_orm import SQLDepends
from sqlalchemy import select

from .model import User

@cmd.handle()
async def handle(
    users: list[User] = SQLDepends(select(User))
):
    for user in users:
        print(user.name)
```

### 带条件查询

```python
from nonebot_plugin_orm import SQLDepends
from sqlalchemy import select

@cmd.handle()
async def handle(
    active_users: list[User] = SQLDepends(
        select(User).where(User.is_active == True)
    )
):
    await cmd.finish(f"Active users: {len(active_users)}")
```

### 动态查询（结合 Depends）

使用 NoneBot 的 `Depends` 构建动态查询：

```python
from nonebot.adapters import Event
from nonebot.params import Depends
from nonebot_plugin_orm import SQLDepends
from sqlalchemy import select

from .model import User

def user_query(event: Event):
    return select(User).where(User.user_id == event.get_user_id())

@cmd.handle()
async def handle(
    user: User | None = SQLDepends(Depends(user_query))
):
    if user:
        await cmd.finish(f"Hi, {user.name}!")
```

## Model 类作为依赖

Model 类本身可以直接作为依赖使用，`nonebot-plugin-orm` 会自动根据事件信息查找对应记录：

```python
from .model import User

@cmd.handle()
async def handle(user: User):
    await cmd.finish(f"User: {user.name}, Score: {user.score}")
```

这等价于：

```python
@cmd.handle()
async def handle(session: async_scoped_session, event: Event):
    stmt = select(User).where(User.user_id == event.get_user_id())
    result = await session.execute(stmt)
    user = result.scalar_one()
```

## 类型注解与返回值

`SQLDepends` 根据依赖参数的类型注解决定如何处理查询结果。以下是所有 12 种支持的类型注解模式：

### 1. `AsyncIterator[Model]`

异步迭代器，逐行返回模型实例：

```python
from typing import AsyncIterator

@cmd.handle()
async def handle(
    users: AsyncIterator[User] = SQLDepends(select(User))
):
    async for user in users:
        print(user.name)
```

等价于：

```python
result = await session.stream_scalars(statement)
```

### 2. `Iterator[Model]`

同步迭代器（实际为异步实现的同步包装）：

```python
from typing import Iterator

@cmd.handle()
async def handle(
    users: Iterator[User] = SQLDepends(select(User))
):
    for user in users:
        print(user.name)
```

等价于：

```python
result = (await session.execute(statement)).scalars()
```

### 3. `AsyncResult[Tuple]`

异步结果集，返回原始行元组：

```python
from sqlalchemy import AsyncResult, Tuple

@cmd.handle()
async def handle(
    result: AsyncResult[Tuple[int, str]] = SQLDepends(
        select(User.id, User.name)
    )
):
    async for row in result:
        print(row[0], row[1])
```

等价于：

```python
result = await session.stream(statement)
```

### 4. `ScalarResult[Model]`

标量结果集，返回单列值：

```python
from sqlalchemy import ScalarResult

@cmd.handle()
async def handle(
    result: ScalarResult[User] = SQLDepends(select(User))
):
    users = result.all()
```

等价于：

```python
result = await session.stream_scalars(statement)
```

### 5. `Result[Tuple]`

同步结果集，返回原始行元组：

```python
from sqlalchemy import Result, Tuple

@cmd.handle()
async def handle(
    result: Result[Tuple[int, str]] = SQLDepends(
        select(User.id, User.name)
    )
):
    for row in result:
        print(row[0], row[1])
```

等价于：

```python
result = await session.execute(statement)
```

### 6. `Sequence[Model]`

返回模型实例列表：

```python
from typing import Sequence

@cmd.handle()
async def handle(
    users: Sequence[User] = SQLDepends(select(User))
):
    for user in users:
        print(user.name)
```

等价于：

```python
result = (await session.execute(statement)).scalars().all()
```

### 7. `list[Model]`

与 `Sequence[Model]` 行为相同：

```python
@cmd.handle()
async def handle(
    users: list[User] = SQLDepends(select(User))
):
    print(f"Found {len(users)} users")
```

等价于：

```python
result = (await session.execute(statement)).scalars().all()
```

### 8. `Tuple[column_types...]`

返回单行元组：

```python
from typing import Tuple

@cmd.handle()
async def handle(
    row: Tuple[int, str] = SQLDepends(
        select(User.id, User.name).limit(1)
    )
):
    user_id, name = row
    print(f"User {user_id}: {name}")
```

等价于：

```python
result = (await session.execute(statement)).one()
```

### 9. `Model`（单个模型实例）

返回单个模型实例，如果不存在则抛出异常：

```python
@cmd.handle()
async def handle(
    user: User = SQLDepends(
        select(User).where(User.id == 1)
    )
):
    print(user.name)
```

等价于：

```python
result = (await session.execute(statement)).scalar_one()
```

### 10. `Model | None`（可选模型实例）

返回单个模型实例或 `None`：

```python
@cmd.handle()
async def handle(
    user: User | None = SQLDepends(
        select(User).where(User.id == 1)
    )
):
    if user:
        print(user.name)
    else:
        print("Not found")
```

等价于：

```python
result = (await session.execute(statement)).scalar_one_or_none()
```

### 11. `Optional[Model]`

与 `Model | None` 行为相同：

```python
from typing import Optional

@cmd.handle()
async def handle(
    user: Optional[User] = SQLDepends(
        select(User).where(User.id == 1)
    )
):
    if user:
        print(user.name)
```

等价于：

```python
result = (await session.execute(statement)).scalar_one_or_none()
```

### 12. 标量类型（`int`, `str` 等）

返回单个标量值：

```python
@cmd.handle()
async def handle(
    count: int = SQLDepends(select(func.count()).select_from(User))
):
    print(f"Total users: {count}")
```

等价于：

```python
result = (await session.execute(statement)).scalar_one()
```

## 类型注解速查表

| 类型注解 | 等价 Session 调用 | 返回值 |
|---------|------------------|--------|
| `AsyncIterator[Model]` | `session.stream_scalars(stmt)` | 异步迭代器 |
| `Iterator[Model]` | `(await session.execute(stmt)).scalars()` | 同步迭代器 |
| `AsyncResult[Tuple]` | `session.stream(stmt)` | 异步结果集 |
| `ScalarResult[Model]` | `session.stream_scalars(stmt)` | 标量结果集 |
| `Result[Tuple]` | `await session.execute(stmt)` | 同步结果集 |
| `Sequence[Model]` | `(await session.execute(stmt)).scalars().all()` | 模型列表 |
| `list[Model]` | `(await session.execute(stmt)).scalars().all()` | 模型列表 |
| `Tuple[...]` | `(await session.execute(stmt)).one()` | 单行元组 |
| `Model` | `(await session.execute(stmt)).scalar_one()` | 单个模型（必须存在） |
| `Model \| None` | `(await session.execute(stmt)).scalar_one_or_none()` | 单个模型或 None |
| `Optional[Model]` | `(await session.execute(stmt)).scalar_one_or_none()` | 单个模型或 None |
| `int` / `str` 等 | `(await session.execute(stmt)).scalar_one()` | 标量值 |

## 完整示例

```python
from typing import Optional, Sequence

from nonebot import on_command
from nonebot.adapters import Event
from nonebot.params import Depends
from nonebot_plugin_orm import SQLDepends, async_scoped_session
from sqlalchemy import func, select

from .model import User

cmd = on_command("stats")

def user_query(event: Event):
    return select(User).where(User.user_id == event.get_user_id())

@cmd.handle()
async def handle(
    user: Optional[User] = SQLDepends(Depends(user_query)),
    total: int = SQLDepends(select(func.count()).select_from(User)),
    top_users: Sequence[User] = SQLDepends(
        select(User).order_by(User.score.desc()).limit(5)
    ),
):
    lines = []

    if user:
        lines.append(f"你的积分: {user.score}")
    else:
        lines.append("你还没有记录")

    lines.append(f"总用户数: {total}")
    lines.append("Top 5:")

    for i, u in enumerate(top_users):
        lines.append(f"  {i+1}. {u.name}: {u.score}")

    await cmd.finish("\n".join(lines))
```
