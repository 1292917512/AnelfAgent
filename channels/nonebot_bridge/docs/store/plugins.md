# 插件商店

[NoneBot 插件商店](https://nonebot.dev/store/plugins) 是 NoneBot 生态的核心组成部分，收录了社区开发者发布的各类插件，用户可以方便地浏览、搜索和安装所需的插件。

## 商店地址

- **在线商店**：<https://nonebot.dev/store/plugins>
- **注册仓库**：<https://github.com/nonebot/registry>

## 浏览插件

### 搜索与筛选

插件商店提供多种搜索和筛选方式：

- **关键词搜索**：在搜索框输入插件名称或关键词
- **标签筛选**：通过标签过滤特定类型的插件
- **适配器筛选**：查看支持特定适配器的插件
- **排序**：按名称、更新时间等排序

### 插件信息

每个插件在商店中展示以下信息：

| 信息 | 来源 | 说明 |
|------|------|------|
| 名称 | `PluginMetadata.name` | 插件显示名称 |
| 描述 | `PluginMetadata.description` | 插件简短描述 |
| 主页 | `PluginMetadata.homepage` | 项目主页链接 |
| 类型 | `PluginMetadata.type` | `application` 或 `library` |
| 支持的适配器 | `PluginMetadata.supported_adapters` | 支持的适配器列表 |
| PyPI 包名 | 注册信息 | 安装时使用的包名 |
| 作者 | PyPI 信息 | 插件开发者 |
| 版本 | PyPI 信息 | 最新发布版本 |

## 插件类型

### application（应用插件）

提供具体功能的插件，直接面向用户使用：

- 天气查询、翻译、搜索
- 群管理、反垃圾
- 签到、积分、小游戏
- 图片生成、AI 聊天

### library（库插件）

为其他插件提供基础能力的插件：

- `nonebot-plugin-localstore` - 本地数据存储
- `nonebot-plugin-orm` - 数据库 ORM
- `nonebot-plugin-apscheduler` - 定时任务
- `nonebot-plugin-htmlrender` - HTML 渲染

## 安装插件

### 使用 nb-cli（推荐）

```bash
# 安装插件
nb plugin install nonebot-plugin-xxx

# 安装指定版本
nb plugin install nonebot-plugin-xxx==1.0.0

# 卸载插件
nb plugin uninstall nonebot-plugin-xxx
```

`nb-cli` 会自动处理依赖安装和插件配置加载。

### 使用 pip

```bash
pip install nonebot-plugin-xxx
```

使用 pip 安装后，需要手动在配置中加载插件：

```toml
# pyproject.toml
[tool.nonebot]
plugins = ["nonebot_plugin_xxx"]
```

或在代码中加载：

```python
import nonebot
nonebot.load_plugin("nonebot_plugin_xxx")
```

### 使用其他包管理器

```bash
# Poetry
poetry add nonebot-plugin-xxx

# PDM
pdm add nonebot-plugin-xxx

# uv
uv add nonebot-plugin-xxx
```

## 状态指示

商店中的插件可能显示以下状态：

| 状态 | 说明 |
|------|------|
| ✅ 正常 | 插件通过所有自动化检查 |
| ⚠️ 跳过 | 插件跳过了部分检查（如需要特殊环境） |
| ❌ 失败 | 插件未通过自动化检查 |

自动化检查包括：

- 包是否存在于 PyPI
- 插件是否能正确加载
- 是否声明了 `PluginMetadata`
- 配置类是否可用

## 发布插件

### 前提条件

1. 插件已发布到 [PyPI](https://pypi.org/)
2. 插件可以正确加载
3. 声明了 `PluginMetadata`
4. 有完善的 README

### 发布流程

1. 前往 [NoneBot 商店插件页面](https://nonebot.dev/store/plugins)
2. 点击页面上的「发布插件」按钮
3. 填写以下信息：
   - **PyPI 项目名**：如 `nonebot-plugin-weather`
   - **import 包名**：如 `nonebot_plugin_weather`
   - **项目主页**：GitHub 仓库地址
   - **标签**：选择合适的分类标签
4. 提交后会自动在 [nonebot/registry](https://github.com/nonebot/registry) 创建 Pull Request
5. CI 会自动执行检查
6. 检查通过后等待维护者审核合并

### 更新插件

插件商店会自动同步 PyPI 上的最新版本信息，无需手动更新。如果需要修改商店中的元信息（如标签），可以向 registry 仓库提交 PR。

## 常用插件推荐

| 插件 | 说明 |
|------|------|
| `nonebot-plugin-localstore` | 本地文件存储路径管理 |
| `nonebot-plugin-orm` | 数据库 ORM 支持 |
| `nonebot-plugin-apscheduler` | APScheduler 定时任务 |
| `nonebot-plugin-htmlrender` | Playwright HTML 渲染 |
| `nonebot-plugin-alconna` | Alconna 命令解析 |
| `nonebot-plugin-saa` | 跨适配器消息发送 |
| `nonebot-plugin-session` | 会话信息提取 |
| `nonebot-plugin-uninfo` | 统一信息获取 |
