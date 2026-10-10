# MCP 工具桥接实现说明

本文只在修改 `entities/mcp/`、MCP 服务热同步、工具 schema 或连接重试时阅读。频道和实体通过统一注册表使用这套桥接，不应在业务模块复制一套 MCP 生命周期。

## 模块职责

| 文件 | 责任 |
|---|---|
| `bridge.py` | MCP 会话生命周期、连接状态、调用分发和存活探测 |
| `config.py` | 服务配置、启用状态、存储和沉睡策略 |
| `manage_tools.py` | 服务管理与工具管理入口 |
| `transport.py` | stdio/HTTP 传输创建、环境变量白名单和超时参数 |
| `schema.py` | 参数 schema 解析、默认值和工具名规范化 |
| `render.py` | 文本、结构化内容和多模态结果渲染 |
| `retry.py` | 连接重试和稳定窗口内的重试预算 |

## 稳定契约

- MCP 返回的 text 直接合并；图片先落盘到 `uploads/mcp/`，通过内部 `_multimodal` 结构交给视觉模型，base64 原文不能进入上下文。audio、resource link 和 embedded resource 使用短占位。
- 工具列表变更通过 SDK 的消息处理器接入，使用短防抖后增量注册和注销；描述没有变化时不要重建整组 tools，避免无谓破坏前缀缓存。
- 服务声明的 `call_timeout` 必须透传到工具执行超时；连接生命周期超时和单次调用超时是两件事。
- schema 解析必须保留 `anyOf/oneOf` 的可选分支、`default`、`items`、`minimum` 等约束；不能把未知结构统一降成 string。
- 工具名在规范化后再做冲突检测；非法字符替换、长度限制和哈希后缀必须保持确定性，否则端点可能拒绝整组工具。
- 调用错误使用统一语义原因：未找到、超时和断线分别提供结构化错误及可重试标记。
- 连接稳定超过重试窗口后应重置重试预算；退避不能因为长期运行中的一次偶发抖动永久耗尽。
- 存活探测失败必须进入既有断线/重连分支；禁用或删除服务后不应被后台重连重新拉起。
- 服务启停以配置中的 `enabled` 为准，而不是以当前连接状态推断；启用但连接失败仍保留启用状态并报告 `last_error`。
- 工具注册表、沉睡激活状态和实体版本任一变化，都要让进行中的回复在下一轮重建工具集合；工具顺序仍采用追加式冻结。

## 修改后检查

```powershell
uv run pytest entities/mcp/tests
uv run python -m channels.minecraft.scripts.check_minecraft
uv run lint-imports
```

如果修改了 MCP SDK 版本，测试构造应使用真实 `mcp.types` 模型，不用 `SimpleNamespace` 伪造字段；同时核对 snake_case 模型字段、streamable HTTP 超时映射和 OAuth 适配。