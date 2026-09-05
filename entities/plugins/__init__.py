"""插件管理实体 — 插件包的安装、升级、移除与市场订阅。

- ``activation.py``：运行时激活编排（技能入库 / MCP 合并 / 工具注册 / 事件广播）
- ``tools.py``：AI 自主管理工具（plugins 组）
- ``router.py``：Web 管理端点（自动挂载 /api/entity/plugins）

核心引擎在 ``core/plugins``（清单解析 / 注册表 / 负载获取 / 编排门面），
本实体经钩子把激活动作接入 Anelf 运行时。
"""
