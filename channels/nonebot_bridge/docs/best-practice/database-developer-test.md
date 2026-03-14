# 测试

本文档介绍如何为使用了 `nonebot-plugin-orm` 的插件配置多数据库后端的自动化测试，包括 GitHub Actions CI 配置。

## 测试策略

为了保证插件在不同数据库后端下都能正常工作，建议使用测试矩阵覆盖以下场景：

| 数据库 | 操作系统 | 说明 |
|--------|---------|------|
| SQLite | Windows, macOS, Linux | 全平台测试，零配置 |
| PostgreSQL | Linux | 使用 GitHub Actions 服务容器 |
| MySQL | Linux | 使用 GitHub Actions 服务容器 |

> **说明**：PostgreSQL 和 MySQL 仅在 Linux 上测试，因为 GitHub Actions 的服务容器（services）只在 Linux runner 上可用。

## 基本测试配置

### 测试文件结构

```
tests/
├── conftest.py
├── test_plugin.py
└── ...
```

### conftest.py

```python
import pytest
import nonebot
from nonebug import App

@pytest.fixture
async def app():
    nonebot.init()
    nonebot.load_plugin("nonebot_plugin_orm")
    nonebot.load_plugin("your_plugin")
    yield App()
```

### 基本测试

```python
import pytest
from nonebug import App
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent

@pytest.mark.asyncio
async def test_add_score(app: App):
    from your_plugin import add_score

    async with app.test_matcher(add_score) as ctx:
        bot = ctx.create_bot(base=Bot)
        event = make_fake_event(message=Message("/加分"))
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, "当前积分: 1")
```

## 单数据库 CI 配置

如果只需要测试 SQLite（最简单的场景）：

```yaml
# .github/workflows/test.yml
name: Test

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest, macos-latest]
        python-version: ["3.10", "3.11", "3.12"]
      fail-fast: false

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install nonebot2 nonebot-plugin-orm[sqlite] nonebug pytest pytest-asyncio
          pip install -e .

      - name: Run tests
        run: pytest tests/ -v
```

## 多数据库 CI 配置（完整矩阵）

### GitHub Actions 服务容器

使用 GitHub Actions 的 `services` 配置 PostgreSQL 和 MySQL 服务：

```yaml
# .github/workflows/test-matrix.yml
name: Test (Multi-DB)

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

env:
  FORCE_COLOR: "1"

jobs:
  # SQLite 测试 - 全平台
  test-sqlite:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest, macos-latest]
        python-version: ["3.10", "3.11", "3.12"]
      fail-fast: false

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install nonebot2 nonebot-plugin-orm[sqlite] nonebug pytest pytest-asyncio
          pip install -e .

      - name: Run tests
        env:
          SQLALCHEMY_DATABASE_URL: "sqlite+aiosqlite://"
        run: pytest tests/ -v

  # PostgreSQL 测试 - 仅 Linux
  test-postgres:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.10", "3.11", "3.12"]
        postgres-version: ["14", "15", "16"]
      fail-fast: false

    services:
      postgres:
        image: postgres:${{ matrix.postgres-version }}
        env:
          POSTGRES_USER: test
          POSTGRES_PASSWORD: test
          POSTGRES_DB: test_db
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install nonebot2 nonebot-plugin-orm[postgresql] nonebug pytest pytest-asyncio
          pip install -e .

      - name: Run tests
        env:
          SQLALCHEMY_DATABASE_URL: "postgresql+psycopg://test:test@localhost:5432/test_db"
        run: pytest tests/ -v

  # MySQL 测试 - 仅 Linux
  test-mysql:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.10", "3.11", "3.12"]
        mysql-version: ["8.0", "8.2"]
      fail-fast: false

    services:
      mysql:
        image: mysql:${{ matrix.mysql-version }}
        env:
          MYSQL_ROOT_PASSWORD: test
          MYSQL_DATABASE: test_db
          MYSQL_USER: test
          MYSQL_PASSWORD: test
        ports:
          - 3306:3306
        options: >-
          --health-cmd "mysqladmin ping -h 127.0.0.1"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 10
          --health-start-period 30s

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install nonebot2 nonebot-plugin-orm[mysql] nonebug pytest pytest-asyncio
          pip install -e .

      - name: Run tests
        env:
          SQLALCHEMY_DATABASE_URL: "mysql+aiomysql://test:test@127.0.0.1:3306/test_db"
        run: pytest tests/ -v
```

## 合并为单个 Job（可选）

如果希望在单个 Job 中使用 include 矩阵，可以这样配置：

```yaml
name: Test (Unified Matrix)

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        include:
          # SQLite - 全平台
          - os: ubuntu-latest
            python-version: "3.12"
            db: sqlite
            db-url: "sqlite+aiosqlite://"
            extras: "sqlite"
          - os: windows-latest
            python-version: "3.12"
            db: sqlite
            db-url: "sqlite+aiosqlite://"
            extras: "sqlite"
          - os: macos-latest
            python-version: "3.12"
            db: sqlite
            db-url: "sqlite+aiosqlite://"
            extras: "sqlite"

          # PostgreSQL - 仅 Linux
          - os: ubuntu-latest
            python-version: "3.12"
            db: postgres
            db-url: "postgresql+psycopg://test:test@localhost:5432/test_db"
            extras: "postgresql"

          # MySQL - 仅 Linux
          - os: ubuntu-latest
            python-version: "3.12"
            db: mysql
            db-url: "mysql+aiomysql://test:test@127.0.0.1:3306/test_db"
            extras: "mysql"
      fail-fast: false

    services:
      postgres:
        image: ${{ matrix.db == 'postgres' && 'postgres:16' || '' }}
        env:
          POSTGRES_USER: test
          POSTGRES_PASSWORD: test
          POSTGRES_DB: test_db
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5

      mysql:
        image: ${{ matrix.db == 'mysql' && 'mysql:8.0' || '' }}
        env:
          MYSQL_ROOT_PASSWORD: test
          MYSQL_DATABASE: test_db
          MYSQL_USER: test
          MYSQL_PASSWORD: test
        ports:
          - 3306:3306
        options: >-
          --health-cmd "mysqladmin ping -h 127.0.0.1"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 10

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install nonebot2 "nonebot-plugin-orm[${{ matrix.extras }}]" nonebug pytest pytest-asyncio
          pip install -e .

      - name: Run tests
        env:
          SQLALCHEMY_DATABASE_URL: ${{ matrix.db-url }}
        run: pytest tests/ -v
```

## 本地多数据库测试

### 使用 Docker Compose

```yaml
# docker-compose.test.yml
version: "3.8"

services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: test
      POSTGRES_PASSWORD: test
      POSTGRES_DB: test_db
    ports:
      - "5432:5432"

  mysql:
    image: mysql:8.0
    environment:
      MYSQL_ROOT_PASSWORD: test
      MYSQL_DATABASE: test_db
      MYSQL_USER: test
      MYSQL_PASSWORD: test
    ports:
      - "3306:3306"
```

```bash
# 启动服务
docker-compose -f docker-compose.test.yml up -d

# 运行 SQLite 测试
SQLALCHEMY_DATABASE_URL="sqlite+aiosqlite://" pytest tests/ -v

# 运行 PostgreSQL 测试
SQLALCHEMY_DATABASE_URL="postgresql+psycopg://test:test@localhost:5432/test_db" pytest tests/ -v

# 运行 MySQL 测试
SQLALCHEMY_DATABASE_URL="mysql+aiomysql://test:test@localhost:3306/test_db" pytest tests/ -v

# 停止服务
docker-compose -f docker-compose.test.yml down
```

## 测试辅助工具

### 数据库清理 Fixture

```python
import pytest
from sqlalchemy import text
from nonebot_plugin_orm import get_session

@pytest.fixture(autouse=True)
async def clean_db():
    yield
    async with get_session() as session:
        # SQLite 特定清理
        for table in reversed(Model.metadata.sorted_tables):
            await session.execute(text(f"DELETE FROM {table.name}"))
        await session.commit()
```

### 参数化数据库后端

```python
import os
import pytest

DB_URLS = {
    "sqlite": "sqlite+aiosqlite://",
    "postgres": "postgresql+psycopg://test:test@localhost:5432/test_db",
    "mysql": "mysql+aiomysql://test:test@localhost:3306/test_db",
}

@pytest.fixture(params=["sqlite", "postgres", "mysql"])
def db_url(request):
    url = DB_URLS[request.param]
    os.environ["SQLALCHEMY_DATABASE_URL"] = url
    yield url
    del os.environ["SQLALCHEMY_DATABASE_URL"]
```
