# 适配器商店

[NoneBot 适配器商店](https://nonebot.dev/store/adapters) 收录了社区开发的各平台适配器，让 NoneBot 可以连接到不同的聊天平台。

## 商店地址

- **在线商店**：<https://nonebot.dev/store/adapters>
- **注册仓库**：<https://github.com/nonebot/registry>

## 可用适配器

### 官方适配器

| 适配器 | 包名 | 说明 |
|--------|------|------|
| OneBot V11 | `nonebot-adapter-onebot` | 支持 OneBot v11 协议，对接 QQ 等平台 |
| OneBot V12 | `nonebot-adapter-onebot` | 支持 OneBot v12 协议 |
| Console | `nonebot-adapter-console` | 控制台适配器，用于开发调试 |

### 社区适配器

| 适配器 | 包名 | 说明 |
|--------|------|------|
| QQ 官方 | `nonebot-adapter-qq` | QQ 官方机器人 API |
| Telegram | `nonebot-adapter-telegram` | Telegram Bot API |
| Discord | `nonebot-adapter-discord` | Discord Bot API |
| 飞书 | `nonebot-adapter-feishu` | 飞书开放平台 |
| 钉钉 | `nonebot-adapter-ding` | 钉钉机器人 |
| 开黑啦/KOOK | `nonebot-adapter-kaiheila` | KOOK 聊天平台 |
| Mirai | `nonebot-adapter-mirai` | mirai-api-http |
| GitHub | `nonebot-adapter-github` | GitHub Webhooks & API |
| Minecraft | `nonebot-adapter-minecraft` | Minecraft 服务器 |
| Satori | `nonebot-adapter-satori` | Satori 协议 |
| Dodo | `nonebot-adapter-dodo` | DoDo 聊天平台 |
| Villa | `nonebot-adapter-villa` | 米游社大别野 |
| Ntchat | `nonebot-adapter-ntchat` | 微信（ntchat） |
| Red | `nonebot-adapter-red` | Red 协议（Chronocat） |
| Kritor | `nonebot-adapter-kritor` | Kritor 协议 |
| Tailchat | `nonebot-adapter-tailchat` | Tailchat 平台 |

> **注意**：以上列表可能不完整，最新适配器列表请查看 [在线商店](https://nonebot.dev/store/adapters)。

## 安装适配器

### 使用 nb-cli（推荐）

```bash
# 安装适配器
nb adapter install nonebot-adapter-onebot

# 查看已安装的适配器
nb adapter list
```

### 使用 pip

```bash
pip install nonebot-adapter-onebot
```

手动注册适配器：

```python
import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)
```

### 使用其他包管理器

```bash
# Poetry
poetry add nonebot-adapter-onebot

# PDM
pdm add nonebot-adapter-onebot

# uv
uv add nonebot-adapter-onebot
```

## 注册适配器

安装适配器后，需要在项目中注册：

### 通过 pyproject.toml

```toml
[tool.nonebot]
adapters = [
    {name = "OneBot V11", module_name = "nonebot.adapters.onebot.v11"},
    {name = "QQ", module_name = "nonebot.adapters.qq"},
]
```

### 通过代码

```python
import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from nonebot.adapters.qq import Adapter as QQAdapter

driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)
driver.register_adapter(QQAdapter)
```

## 多适配器支持

NoneBot 支持同时使用多个适配器，可以让同一个 Bot 项目连接到多个平台：

```python
import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from nonebot.adapters.telegram import Adapter as TelegramAdapter

driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)
driver.register_adapter(TelegramAdapter)
```

编写跨适配器插件时，可以使用 `nonebot-plugin-saa`（Send Anything Anywhere）或 `nonebot-plugin-alconna` 实现统一的消息发送。

## 发布适配器

### 前提条件

1. 适配器已发布到 [PyPI](https://pypi.org/)
2. 使用命名空间包 `nonebot.adapters.xxx`
3. 正确继承并实现 `Adapter`、`Bot`、`Event`、`Message` 等基类
4. 有完善的文档和 README

### 发布流程

1. 前往 [NoneBot 适配器商店](https://nonebot.dev/store/adapters)
2. 点击「发布适配器」
3. 填写适配器信息：
   - **PyPI 项目名**：如 `nonebot-adapter-xxx`
   - **适配器模块名**：如 `nonebot.adapters.xxx`
   - **项目主页**：GitHub 仓库地址
4. 提交后会自动创建 PR 到 [nonebot/registry](https://github.com/nonebot/registry)
5. 等待审核合并

### 适配器开发指南

详细的适配器开发教程请参阅 [编写适配器](../developer/adapter-writing.md)。
