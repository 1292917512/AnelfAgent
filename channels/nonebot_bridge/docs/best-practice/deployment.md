# 部署

本文档介绍 NoneBot 项目从依赖管理、Docker 打包到 CI/CD 的完整部署流程。

## 项目依赖管理

### Poetry

```bash
# 安装 Poetry
pip install poetry

# 初始化项目
poetry init

# 添加依赖
poetry add nonebot2
poetry add nonebot-adapter-onebot

# 添加开发依赖
poetry add --group dev pytest nonebug

# 安装所有依赖
poetry install

# 导出 requirements.txt（用于 Docker）
poetry export -f requirements.txt -o requirements.txt --without-hashes
```

`pyproject.toml` 示例（Poetry）：

```toml
[tool.poetry]
name = "my-bot"
version = "0.1.0"
description = "My NoneBot Bot"
authors = ["Author <author@example.com>"]

[tool.poetry.dependencies]
python = "^3.10"
nonebot2 = "^2.4.0"
nonebot-adapter-onebot = "^2.4.0"
nonebot-plugin-apscheduler = "^0.5.0"

[tool.poetry.group.dev.dependencies]
pytest = "^8.0"
nonebug = "^0.4.0"

[build-system]
requires = ["poetry-core"]
build-backend = "poetry.core.masonry.api"
```

### PDM

```bash
# 安装 PDM
pip install pdm

# 初始化项目
pdm init

# 添加依赖
pdm add nonebot2
pdm add nonebot-adapter-onebot

# 添加开发依赖
pdm add -dG dev pytest nonebug

# 安装
pdm install

# 导出 requirements.txt
pdm export -f requirements -o requirements.txt --no-hashes
```

### pip

```bash
# 使用 requirements.txt
pip install -r requirements.txt

# 生成 requirements.txt
pip freeze > requirements.txt
```

`requirements.txt` 示例：

```
nonebot2>=2.4.0
nonebot-adapter-onebot>=2.4.0
nonebot-plugin-apscheduler>=0.5.0
httpx>=0.27.0
```

## Docker 部署

### 安装 Docker

```bash
# Ubuntu / Debian
curl -fsSL https://get.docker.com | sh
sudo systemctl enable docker
sudo systemctl start docker

# 验证安装
docker --version
docker compose version
```

### 使用 nb-cli-plugin-docker

`nb-cli` 提供了 Docker 插件来简化容器化部署：

```bash
# 安装 docker 插件
nb plugin install nb-cli-plugin-docker

# 生成 Dockerfile 和 docker-compose.yml
nb docker generate

# 构建并启动（前台）
nb docker up

# 后台运行
nb docker up -d

# 查看日志
nb docker logs
nb docker logs -f  # 持续查看

# 停止
nb docker down

# 重新构建
nb docker build
nb docker up -d --build
```

### 自定义 Dockerfile

如果需要更细粒度的控制，可以手写 Dockerfile：

```dockerfile
FROM python:3.10-slim

WORKDIR /app

# 系统依赖（根据需要调整）
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目文件
COPY . .

# 暴露端口（根据实际配置）
EXPOSE 8080

# 启动命令
CMD ["python", "bot.py"]
```

多阶段构建（减小镜像体积）：

```dockerfile
# 构建阶段
FROM python:3.10-slim AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# 运行阶段
FROM python:3.10-slim

WORKDIR /app

COPY --from=builder /install /usr/local
COPY . .

EXPOSE 8080

CMD ["python", "bot.py"]
```

### docker-compose.yml

```yaml
version: "3.8"

services:
  nonebot:
    build: .
    container_name: nonebot
    restart: always
    ports:
      - "8080:8080"
    volumes:
      - ./data:/app/data         # 持久化数据
      - ./.env.prod:/app/.env    # 生产环境配置
    environment:
      - TZ=Asia/Shanghai
    networks:
      - bot-network

  # 如需 Redis
  redis:
    image: redis:7-alpine
    container_name: bot-redis
    restart: always
    volumes:
      - redis-data:/data
    networks:
      - bot-network

networks:
  bot-network:
    driver: bridge

volumes:
  redis-data:
```

### 常用 Docker 命令

```bash
# 构建镜像
docker compose build

# 启动服务
docker compose up -d

# 查看运行状态
docker compose ps

# 查看日志
docker compose logs -f nonebot

# 重启服务
docker compose restart nonebot

# 停止并移除
docker compose down

# 进入容器调试
docker compose exec nonebot bash
```

## CI/CD

### GitHub Actions - 自动测试

`.github/workflows/test.yml`：

```yaml
name: Test

on:
  push:
    branches: [main, dev]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.10", "3.11", "3.12"]

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt
          pip install pytest nonebug pytest-asyncio

      - name: Run tests
        run: pytest tests/ -v
```

### GitHub Actions - 构建并推送 Docker Hub

`.github/workflows/docker-publish.yml`：

```yaml
name: Docker Publish

on:
  push:
    tags: ["v*"]

jobs:
  publish:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Login to Docker Hub
        uses: docker/login-action@v3
        with:
          username: ${{ secrets.DOCKER_USERNAME }}
          password: ${{ secrets.DOCKER_PASSWORD }}

      - name: Extract metadata
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: myuser/my-bot
          tags: |
            type=semver,pattern={{version}}
            type=semver,pattern={{major}}.{{minor}}
            type=sha

      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: .
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

### GitHub Actions - 持续部署（SSH）

`.github/workflows/deploy.yml`：

```yaml
name: Deploy

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - name: Deploy to server via SSH
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.SSH_HOST }}
          username: ${{ secrets.SSH_USERNAME }}
          key: ${{ secrets.SSH_KEY }}
          port: ${{ secrets.SSH_PORT }}
          script: |
            cd /opt/my-bot
            git pull origin main
            docker compose build
            docker compose up -d
            docker compose logs --tail 20
```

需要在 GitHub 仓库的 Settings > Secrets 中配置：

| Secret 名 | 说明 |
|-----------|------|
| `SSH_HOST` | 服务器 IP 或域名 |
| `SSH_USERNAME` | SSH 用户名 |
| `SSH_KEY` | SSH 私钥 |
| `SSH_PORT` | SSH 端口（默认 22） |
| `DOCKER_USERNAME` | Docker Hub 用户名 |
| `DOCKER_PASSWORD` | Docker Hub 密码或 Access Token |

### 完整 CI/CD 流程

```yaml
name: CI/CD

on:
  push:
    branches: [main]
    tags: ["v*"]
  pull_request:
    branches: [main]

jobs:
  # 1. 测试
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.10"
      - run: pip install -r requirements.txt && pip install pytest nonebug pytest-asyncio
      - run: pytest tests/ -v

  # 2. 构建推送镜像（仅 tag 触发）
  build:
    needs: test
    if: startsWith(github.ref, 'refs/tags/v')
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          username: ${{ secrets.DOCKER_USERNAME }}
          password: ${{ secrets.DOCKER_PASSWORD }}
      - uses: docker/build-push-action@v5
        with:
          context: .
          push: true
          tags: myuser/my-bot:${{ github.ref_name }},myuser/my-bot:latest

  # 3. 部署到服务器（仅 main 分支）
  deploy:
    needs: test
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    steps:
      - uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.SSH_HOST }}
          username: ${{ secrets.SSH_USERNAME }}
          key: ${{ secrets.SSH_KEY }}
          script: |
            cd /opt/my-bot
            git pull origin main
            docker compose up -d --build
```

## 生产环境建议

### .env 配置分离

```
.env          # 公共配置
.env.dev      # 开发环境
.env.prod     # 生产环境
```

```dotenv
# .env.prod
DRIVER=~fastapi
HOST=0.0.0.0
PORT=8080
LOG_LEVEL=WARNING
ENVIRONMENT=production

# 适配器配置
ONEBOT_ACCESS_TOKEN=your-secret-token
```

### 日志持久化

```yaml
services:
  nonebot:
    # ...
    volumes:
      - ./logs:/app/logs
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

### 健康检查

```yaml
services:
  nonebot:
    # ...
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 10s
```

### 资源限制

```yaml
services:
  nonebot:
    # ...
    deploy:
      resources:
        limits:
          cpus: "1.0"
          memory: 512M
        reservations:
          cpus: "0.25"
          memory: 128M
```
