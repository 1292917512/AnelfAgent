# 机器人商店

[NoneBot 机器人商店](https://nonebot.dev/store/bots) 展示了社区使用 NoneBot 构建的机器人项目，为开发者提供参考和灵感。

## 商店地址

- **在线商店**：<https://nonebot.dev/store/bots>
- **注册仓库**：<https://github.com/nonebot/registry>

## 什么是机器人项目

与插件和适配器不同，机器人项目是完整的、可直接部署运行的 NoneBot 应用。它们通常：

- 组合了多个插件实现丰富功能
- 包含完整的配置和部署方案
- 针对特定使用场景进行了优化
- 可能包含自定义插件

## 浏览机器人

在 [机器人商店](https://nonebot.dev/store/bots) 中，你可以浏览社区发布的机器人项目。每个项目展示以下信息：

| 信息 | 说明 |
|------|------|
| 名称 | 机器人项目名称 |
| 描述 | 项目简短描述 |
| 主页 | 项目仓库地址（通常为 GitHub） |
| 作者 | 项目开发者 |
| 标签 | 分类标签 |

## 社区机器人

社区中有许多优秀的 NoneBot 机器人项目，涵盖各种使用场景：

### 常见类型

- **综合管理机器人**：群管理、信息查询、娱乐功能集合
- **游戏辅助机器人**：游戏数据查询、战绩查询、抽卡模拟
- **工具类机器人**：翻译、天气、搜索、提醒
- **AI 对话机器人**：接入 LLM 的智能对话机器人
- **内容推送机器人**：RSS 订阅、社交媒体动态推送

## 使用机器人项目

### 从项目部署

1. **克隆项目**

```bash
git clone https://github.com/username/my-bot.git
cd my-bot
```

2. **安装依赖**

```bash
# 根据项目使用的包管理器
pip install -r requirements.txt
# 或
poetry install
# 或
pdm install
```

3. **配置环境**

```bash
# 复制示例配置
cp .env.example .env

# 编辑配置
# 填写必要的 Token、数据库连接等信息
```

4. **运行**

```bash
nb run
# 或
python bot.py
```

### 使用 Docker 部署

许多机器人项目提供了 Docker 支持：

```bash
# 使用 docker-compose
docker-compose up -d

# 或直接构建运行
docker build -t my-bot .
docker run -d --name my-bot --env-file .env my-bot
```

## 发布机器人

### 前提条件

1. 项目代码托管在公开的 Git 仓库（如 GitHub）
2. 有完善的 README，包括：
   - 项目介绍和功能列表
   - 安装和部署指南
   - 配置说明
   - 使用说明
3. 项目可以正常运行

### 发布流程

1. 前往 [NoneBot 机器人商店](https://nonebot.dev/store/bots)
2. 点击「发布机器人」
3. 填写以下信息：
   - **名称**：机器人项目名称
   - **描述**：简短描述机器人功能
   - **项目主页**：GitHub 仓库地址
   - **标签**：为项目添加合适的分类标签
4. 提交后会自动在 [nonebot/registry](https://github.com/nonebot/registry) 创建 Pull Request
5. 等待维护者审核合并

### 发布建议

- 提供清晰的安装和配置文档
- 列出所有使用的插件和适配器
- 提供 Docker 部署方案（推荐）
- 添加功能截图或演示
- 声明开源许可证
- 保持项目活跃更新

## 创建自己的机器人

如果你想从零开始创建机器人项目：

### 使用 nb-cli

```bash
# 创建新项目
nb init

# 安装适配器
nb adapter install nonebot-adapter-onebot

# 安装插件
nb plugin install nonebot-plugin-apscheduler
nb plugin install nonebot-plugin-localstore

# 运行
nb run
```

### 项目结构

```
my-bot/
├── src/
│   └── plugins/          # 自定义插件目录
│       ├── __init__.py
│       └── my_plugin/
├── .env                  # 环境配置
├── .env.prod             # 生产环境配置
├── bot.py                # 入口文件
├── pyproject.toml        # 项目配置
├── Dockerfile            # Docker 构建文件
├── docker-compose.yml    # Docker Compose 配置
└── README.md
```

### bot.py 示例

```python
import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

nonebot.init()

driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

nonebot.load_from_toml("pyproject.toml")
nonebot.load_plugins("src/plugins")

if __name__ == "__main__":
    nonebot.run()
```
