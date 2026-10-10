---
description: "AnelfAgent 工作区指令：稳定的开发规范、架构边界与验证方式"
---

# AnelfAgent 项目指令

本文件只描述长期有效的工作指导，不记录日期、提交、测试结果、问题经过或单次任务状态。实现细节以当前源码、配置模型和测试为准；若本文件与源码不一致，先核实再修改文档。

## 开发规范

- 开始修改前先检查相关源码、配置、依赖锁文件和现有测试，不凭名称或历史记录猜测实现。
- 保持最小且完整的修改；优先修复根因，不为局部场景增加平行机制。
- 使用完整类型注解，慎用类型断言；同步处理由修改产生的 lint、类型和导入契约问题。
- 注释只解释当前代码的意图、约束或公共接口，不写版本号、日期和修改过程。
- 复用项目已有的框架、注册表、配置入口和错误类型，不绕过统一管道直接写文件或拼接协议数据。
- 完成修改后检查相关调用链、配置读写、错误路径和测试覆盖，并运行与变更范围匹配的验证。

## 稳定架构边界

### 目录职责

| 目录 | 职责 |
|---|---|
| `core/` | 基础框架、配置、生命周期、流程、实体注册、标签、事件、日志和存储注册 |
| `agent/` | 思维、记忆、模型、频道抽象、任务、心跳、规划、委托、工作流和安全 |
| `channels/` | 频道适配器及其自有配置、协议和技能 |
| `entities/` | 可发现的工具实体；通过 `entities/_sdk.py` 使用运行时能力 |
| `services/` | 面向 Web/API 的业务门面，不反向依赖 `web` |
| `web/routers/` | FastAPI 路由和共享请求/响应模型 |
| `web/frontend/` | React/TypeScript 前端 |
| `config/` | JSON 配置、任务定义和运行数据入口 |

依赖方向保持为 `web/frontend → web/routers → services → agent → core`。`channels` 依赖 `agent.channel` 和 `core`，`entities` 通过 `_sdk` 桥接运行时能力。禁止 `core` 依赖业务层、`agent` 依赖 `web/services`、`channels` 依赖 `web/services`，以及 `entities` 直接依赖 `agent/services/web/channels`。导入契约由 `uv run lint-imports` 守卫。

### 进程宿主

`launch.py` 只负责组合根。一次性初始化放入 `core/flow.py` 的 `FlowMachine` 节点，并用 `depends_on` 声明强依赖；长驻服务统一注册到 `core/lifecycle.py` 的 `Lifecycle`，注册顺序决定启动顺序，关停按逆序执行。不要在调用方重复编排服务清理顺序。

### 注册与配置

- 运行时组件通过 `core/entity.py` 的 `EntityRegistry` 注册和发现；实体类型必须与用途匹配。
- 路径统一使用 `core/path.py` 的 `ConfigPaths`，不要在业务代码中硬编码配置或数据目录。
- 配置项在所属模块通过 `register_configs` 声明；Web 配置、AI 配置工具和文件监听共用同一配置元数据和写入口。
- 频道配置在 `channels/<id>/config.py` 声明 `CONFIG_MODEL`，通过 `set_channel_config` 写入；适配器不要再定义第二份配置模型，也不要直接改配置文件。
- 环境变量引用使用 `${ENV_VAR}`；密钥不写入源码、测试输出或文档示例中的真实值。

### 消息、标签和作用域

- 标签使用 `core.tags` 的构造和解析函数；不要手工拼接标签字符串或用私有正则复制协议。
- 会话作用域使用 `agent.messages.everything` 的 `build_entity_scope`、`parse_entity_scope` 和 `is_conversation_scope`；不要手工拼接 `user_`、`group_` 等字符串。
- `entities` 层只从 `entities._sdk` 获取 LLM、通知、媒体和配置桥接能力；不得直接导入 `agent`。
- 动态状态、运行指标和召回内容放在动态上下文或工具结果中，不把易变事实写入稳定系统提示前缀。

### 频道和插件

频道目录应自持有适配器、配置模型、协议、技能和测试。插件负载、运行时数据和源码目录是不同对象：更新源码后必须通过该频道的安装/升级入口同步负载，修改 Node MCP 后重启对应 MCP 服务。不要把生成的运行时目录当作源码提交，也不要在没有明确替代物和清理需求时删除依赖或已安装负载。

## 实现约定

- 工具函数返回 JSON 字符串，使用完整类型注解和 Google 风格 docstring；错误通过 `core.tool_errors` 统一构造，实体层从 `entities._sdk` 获取对应桥接函数。
- 模型调用统一经过项目的 LLM 抽象，不直接导入 OpenAI、Anthropic 等供应商 SDK；Python 依赖由 `pyproject.toml` 与 `uv.lock` 管理，不在项目虚拟环境中用裸 `pip` 改依赖。
- 只有确有跨层桥接、循环初始化或装饰器注册无法注入构造参数时才使用 `LateBinding`；其他运行时依赖使用构造注入。
- 测试默认使用临时配置和数据目录，不读写真实 `config/`；新增测试优先放在被测模块已有测试目录并复用公共 fixture。
## 验证方式

根据变更范围选择验证，不为“新会话”重复没有变化的整套验证：

```powershell
uv run pytest
uv run ruff check .
uv run lint-imports
uv run mypy core/
```

前端变更：

```powershell
cd web/frontend
npm run lint
npm run typecheck
npm test
npm run build
```

频道或实体变更应优先运行对应目录测试和脚本，再扩大到全仓检查。真实服务、游戏世界、外部账号和网络依赖属于可选验收，不应被离线测试冒充。

### 持续工作与验证规则

- 验证结论以当前提交、相关源码与测试、依赖锁文件和实际运行负载为前提；未发生变化的证据可以复用。
- 执行昂贵测试、重新安装依赖或修改运行负载前，先检查 Git 状态和相关文件是否变化；缺证据时只补缺失的那一项。
- 已部署运行时出现问题时，优先获取在线错误、世界状态和运行日志；除非源码、负载、依赖或测试覆盖变化，不回头重复静态基线验证。
- 没有确认替代物和明确清理需求时，不删除 `node_modules`、运行时目录、缓存或已安装负载。
- 文档应写稳定的使用方式、接口契约和维护规则；临时结果、日期化记录和单次验收放到专门的计划或报告文件。

## 修改文档时

- 根 `README.md` 面向使用者和贡献者；写安装、配置、使用、开发和验证入口，不堆积内部实现流水账。
- 频道 README 面向该频道的使用和维护；保留真实命令、路径、限制和安全边界，详细计划、基准结果和历史证据放到对应文档。
- 新增文档内容前先确认它是否能指导未来工作；只能说明“这次做了什么”的内容不应放入工作指导。

## 相关入口

- 根项目说明：`README.md`
- Minecraft 频道说明：`channels/minecraft/README.md`
- Minecraft 阶段计划与验收设计：`channels/minecraft/DEVELOPMENT_PLAN.md`
- 思维系统现状架构：`docs/mind-architecture.md`
- 详细项目架构参考：`docs/architecture-reference.md`
- 稳定设计决策：`docs/design-decisions.md`
- 上下文缓存排查：`docs/context-cache.md`
- MCP 桥接实现：`docs/mcp-architecture.md`
- 旧版指令归档：`docs/history/AGENTS-legacy.md`（仅查阅，不作为现行规则）
- Python 依赖与工具配置：`pyproject.toml`、`uv.lock`
- 前端依赖与脚本：`web/frontend/package.json`