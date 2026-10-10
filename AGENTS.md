---
description: "AnelfAgent 工作区指令：稳定的开发规范、架构边界、实现约定与文档索引"
---

# AnelfAgent 项目指令

本文件是供 AI 代码编辑器（ZCode）读取的工作区指令，不属于产品运行时代码。只保留长期有效的
工作指导与关键契约，不记录日期、提交、轮次与单次任务状态——机制细节按种类拆分在 `docs/`
专题分册（见文末索引），修改对应模块时按需阅读；若文档与源码不一致，先核实再改文档。

## 一、开发规范

- 先主动审查所有依赖与相关文件（源码、配置、依赖锁、现有测试），再规划实现方案；禁止假定、
  猜测任何实现
- 除非用户要求否则保持最小化修改；优先修复根因，不为局部场景增加平行机制
- 永远保持项目工程化、整洁性、可维护性，合理拆分功能模块；尽可能使用主流成熟的框架和组件，
  非必要不要自己造轮子
- 执行严格类型注解开发，慎用类型断言；始终处理修改产生的衍生 Linter/类型/导入契约警告，
  非必要禁止忽略
- 修改完成后审查所有依赖逻辑是否正确，并运行与变更范围匹配的验证
- 注释只形容类或函数本身的意图、约束或公共接口，不写版本号、日期和修改过程
- 要求代码简洁高效、优雅不为局部做妥协，解决根本问题，不过度设计
- 对参考信息有困惑时主动提问；以严谨负责的态度处理所有细节，以最高标准要求代码

**持续工作与验证规则**：

- 验证结论以当前提交、相关源码与测试为前提；未发生变化的证据可以复用，不为「新会话」重复
  没有变化的整套验证
- 执行昂贵测试、重装依赖或修改运行负载前，先检查 Git 状态和相关文件是否变化；缺证据时只补
  缺失的那一项
- 已部署运行时出问题时优先获取在线错误与运行日志；没有确认替代物和明确清理需求时，不删除
  `node_modules`、运行时目录、缓存或已安装负载
- 文档写稳定的使用方式、接口契约和维护规则；临时结果、日期化记录和单次验收放专门的计划或
  报告文件，不进工作指导

## 二、稳定架构边界

### 目录职责

| 目录 | 职责 | 关键约定 |
|------|------|---------|
| `core/` | 基础框架（EntityRegistry / ConfigManager / Application+Lifecycle / FlowMachine / ConfigPaths / 标签 / 事件 / 日志 / 存储卷） | 不依赖任何业务模块 |
| `agent/` | 智能体内核（Mind / LLM / Storage / Channel / Runtime / Memory / Task / Heartbeat / Planning） | 不依赖 web |
| `agent/mind/` | 思维核心（自主决策 / 多轮推理 / 跨频道感知） | 工具编排在 `mind/tools/` |
| `agent/memory/` | 语义记忆（FTS5 + Embedding 混合检索 / 便签 / 文件索引） | 不依赖 mind |
| `agent/skills/` | 技能自学习（事实归系统、决策归 AI） | 文件在 `workspace/skills/` |
| `agent/delegation/` | 子代理调度（档案 / 并行 / 续跑 / 双档转向 / 运行日志） | 经 `mind.reflect()` 隔离执行 |
| `agent/workflow/` | 工作流引擎（journal 化 DAG 断点续跑） | ask 步复用 DelegationManager，tool 步走统一审批门 |
| `agent/hooks_llm/` | LLM 钩子面（事件驱动异步 LLM 工作） | 与心跳/任务平行；经 reflect 隔离执行 |
| `agent/security/` | 安全防护（会话令牌 / 威胁扫描） | 脱敏核心在 `core/sanitizer.py` |
| `agent/task/` | 独立任务系统（定义 / 注册表 / 执行器） | 纯内容定义，不含调度逻辑 |
| `agent/heartbeat/` | 心跳调度（引擎 / 配置 / 日志 / 内置维护） | 管理何时执行任务 |
| `agent/planning/` | 自主规划（终态即清，存续决策归 AI） | 依赖 memory |
| `agent/judgment/` | 结构化判断（Choice/Score/Noul 三原语） | 配置走 `judgment/core` 组 |
| `channels/` | 频道适配器（自动发现 + 热插拔） | 继承 BaseChannel，display_order 自声明 |
| `entities/` | 工具实体（自动发现 + 热插拔） | 经 `_sdk.py` 桥接 LLM |
| `services/` | 业务封装层（供 Web API 调用） | 不依赖 web；mcp 为 entities.mcp 薄门面 |
| `web/routers/` | FastAPI 路由 | 共享模型放 `schemas.py` |
| `web/frontend/src/` | React 前端 | 页面壳 + 子面板目录拆分 |
| `config/` | JSON 配置 + SQLite 数据 + 便签 | 路径统一用 `ConfigPaths` |

### 依赖方向

```
web/frontend → web/routers → services → agent → core/
entities → entities._sdk → core.entity
channels/ → agent.channel → core.entity

禁止: agent → entities/channels（仅 agent/runtime 组合根允许装配）/web/services |
core → 业务层 | services → web | channels → web/services |
entities → agent/services/web/channels（_sdk 桥是唯一豁免）|
web/routers → agent/entities/channels（经 services 收口）
```

由 import-linter 机械守卫（pyproject.toml 八条 forbidden 契约，`uv run lint-imports`，CI 门禁）。
注意本地与 CI 的 import-linter 版本差异：实体里写 `from agent.` 前必须想到 _sdk 桥，并以新版
工具验证。

### 关键机制速记

- **进程宿主**：`launch.py` 是薄组合根——一次性初始化进 `FlowMachine`（depends_on 拓扑 + 声明式
  重试超时）；长驻服务统一 `Lifecycle.register(name, instance, on_start, cleanup)`，**注册顺序 =
  启动顺序，逆序 = 关停顺序**（自然获得 drain 语义），调用方不得自行编排清理
- **EntityRegistry**（`core/entity.py`）：工具/模型/适配器/服务/存储/数据库/MCP 的注册与发现中心；
  分组排序权重由归属模块自声明（`register_group_order` / manifest order），core 不内置业务分组名表
- **ConfigPaths**（`core/path.py`）：配置与数据路径唯一入口（`ANELF_CONFIG_DIR` / `ANELF_DATA_DIR` /
  `data_root` 支持整体搬迁）；配置值支持 `${ENV_VAR}` 引用；`ANELF_<KEY>` 仅覆盖生效值不回写
- **配置元数据**：各模块 `register_configs` 声明式注册，驱动配置中心与 AI 配置工具——**新增配置项
  只需在所属模块注册，不要在前端硬编码字段**
- **频道配置**：`channels/<id>/config.py` 暴露 `CONFIG_MODEL`（pydantic 模型即唯一声明源，禁止在
  adapter.py 再定义第二份）；频道内部写配置一律 `set_channel_config`，禁止直写文件
- **标签**：`[key:value]` 由 `core.tags` 构造/解析；出站清洗名单是标签外泄的唯一防线，新增渲染
  标签必须同步名单；工具唤醒只用显式映射，不让用户可控标签泛化激活工具
- **会话 scope**：一律 `build_entity_scope()` / `parse_entity_scope()` / `is_conversation_scope()`，
  **禁止手工 f-string 拼接** `user_`/`group_`/频道前缀
- **entities/_sdk.py**：实体层唯一运行时桥接面（工具注册、LLM、通知、媒体、embedding、配置、统一
  出站发送）；实体不得直接导入 agent
- **系统注入消息**：带 `_source` 来源标记 + `_layer` 层标记，发送前必须经 `normalize_for_send`
  剥离（LLM 不可见，供归因）；这两个字段不进供应商请求与持久化历史
- **晚绑定**：仅限 import 时工具注册拿不到构造参数 / 循环初始化 / 跨层桥三种成因用
  `core.latebind.LateBinding`（`agent/runtime/wiring.py` 统一施绑），其余运行时依赖一律构造注入，
  禁止 `set_xxx` 式模块全局
- **易变内容不进稳定前缀**：动态状态、运行指标、召回内容放动态上下文或工具结果，不写入
  stable/summary/conversation 低变动层（红线清单见 `docs/context-cache.md`）

## 三、实现约定

**import**：`from core.log import log` / `from core.entity import EntityRegistry` /
`from core.path import ConfigPaths` / `from agent.memory.memory_store import MemoryStore` /
`from agent.heartbeat.engine import HeartbeatEngine` / `from agent.task.registry import TaskRegistry`

**日志**：`log("内容")` / `log("调试", "DEBUG")` / `log(f"错误: {exc}", "ERROR")`

**异常处理**：关键路径（数据库连接、关闭）保持 `except Exception`；工具函数返回 JSON error；
其他 `pass` 场景补充 DEBUG 日志

**工具开发**：返回 `str`（JSON）、完整类型注解、Google docstring、内部捕获异常；错误返回统一用
`core.tool_errors`（entities 经 `_sdk` 导出 `tool_error` / `error_from_exception` / `ErrorCause`），
**禁止裸 `{"error": str(e)}`**

**Model Experience 三行声明（新功能必答）**：任何影响模型输入/输出的新功能，须在模块 docstring
登记三件事——① 模型看到什么（注入了什么、走哪个通道）② token 影响（增量还是节省、量级）
③ 缓存影响（是否触碰前缀层；动态区则注明不破前缀）。缓存是本项目一等指标，新功能不声明即视为
未评估

**频道开发**：继承 BaseChannel、6 个必需接口（channel_id / display_name / capabilities / start /
stop / send_text）；频道目录自持有适配器、配置模型、协议、技能、前端与测试

**前端**：页面超过 300 行拆为子面板目录、统一用 TabBar、i18n 覆盖所有文本、类型进 `lib/types.ts`；
核心不直接导入具体实体/频道组件（贡献点体系，见 `docs/frontend.md` 与 `docs/ui-contributions.md`）

**生命周期**：有状态单例与长驻服务一律 `Lifecycle.register`，禁止在入口脚本手工编排服务清理

**包管理**：依赖由 uv 管理（`pyproject.toml` + `uv.lock`），安装依赖用 `uv add`，临时操作用
`uv pip install`；**禁止对 `.venv` 使用 `pip install` / `ensurepip`**（uv 创建的 venv 默认不含
pip，属正常状态而非故障）

**litellm 版本**：pyproject 精确锁定（禁止范围符）；升级前必须验证 `litellm.ModelResponse()` 可
实例化，且全模型缓存健康门全绿（`LLM_CACHE_E2E=1 uv run pytest
tests/integration/test_llm_cache_hit_e2e.py`，见 `docs/context-cache.md`）

**修改文档时**：根 README 面向使用者和贡献者（安装、配置、使用、开发入口）；频道 README 面向该
频道的使用和维护；新增文档内容前先确认它能否指导未来工作——只能说明「这次做了什么」的内容不放
入工作指导

**禁止**：直接 import openai/anthropic SDK（用 litellm）/ entities 直接 import agent（用 _sdk
桥接）/ 在前端硬编码配置字段 / 手工拼接 scope 或标签字符串

## 四、验证方式

根据变更范围选择验证，不为「新会话」重复没有变化的整套验证：

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

频道或实体变更应优先运行对应目录测试（如 `uv run pytest entities/minimax/tests`）和模块自带
脚本，再扩大到全仓检查；本地一键镜像 `scripts/check.sh`。真实服务、游戏世界、外部账号和网络
依赖属于可选验收，不应被离线测试冒充。测试体系与防膨胀规约见 `docs/testing.md`。

## 五、开发文档索引（docs/）

按种类拆分的专题分册，修改对应模块时按需阅读；均以当前源码与测试为准：

| 分册 | 内容 |
|------|------|
| [architecture-reference.md](docs/architecture-reference.md) | 核心框架：目录职责、依赖方向、进程宿主、注册表、路径配置、晚绑定、热插拔、存储卷、工具分组体系、关键文件索引 |
| [mind-architecture.md](docs/mind-architecture.md) | 思维系统：自主循环、回复与工具循环、上下文分层组装、工具装配门控、循环防护、判断能力、LLM 钩子面 |
| [memory-system.md](docs/memory-system.md) | 记忆系统：存储与判重、召回枢纽（检索规划/异步深探/账本）、图谱、cognee 集成与写盘防护、遗忘治理、铁律与受管区块、证据与反思生命周期、mem:ID 索引完整性 |
| [llm-and-models.md](docs/llm-and-models.md) | 模型与 LLM 层：llm_clients 管理、协议路由（chat_protocol）、思考等级契约、重试韧性、用量口径、内部辅助调用 |
| [delegation-and-workflow.md](docs/delegation-and-workflow.md) | 子代理（档案/steer·after/续跑/运行日志/可观测性）与工作流引擎（journal DAG/修订导入/门控重跑） |
| [tasks-heartbeat-planning.md](docs/tasks-heartbeat-planning.md) | 心跳引擎、触发模式（含 idle/事件触发）、任务系统与执行历史、目标规划 |
| [skills.md](docs/skills.md) | 技能系统：目录召回、匹配器、写入决策协议、后台评审、策展重力、向量生命周期 |
| [channels-and-scopes.md](docs/channels-and-scopes.md) | 频道适配器、配置统一接入、会话 scope、统一出站管道、出站事实面与三层防护 |
| [realtime-voice.md](docs/realtime-voice.md) | 实时传输枢纽、语音会话、全双工实时引擎、流式 ASR、TTS 管线、通话频道化 |
| [audio-voiceprint.md](docs/audio-voiceprint.md) | 音频核心库、声纹体系（锚/信道/防投毒/分离度门）、音色预设、语音组件 |
| [vision-and-capabilities.md](docs/vision-and-capabilities.md) | 视觉源框架、视频理解链路、能力路由框架、检索核心域 |
| [security-and-approval.md](docs/security-and-approval.md) | 权限规则与 Guardian、审批审计、风险分层、会话令牌、威胁扫描、脱敏 |
| [operations.md](docs/operations.md) | 进程守护、崩溃恢复、重启交接、组件凭据中心、本地模型资产、操作实体、用户钩子 |
| [frontend.md](docs/frontend.md) | 前端结构、模块插件体系、工作区对话联动与展示层细节 |
| [context-cache.md](docs/context-cache.md) | 上下文缓存与用量排查（三层定位法）、上下文层红线清单、litellm 升级门禁 |
| [mcp-architecture.md](docs/mcp-architecture.md) | MCP 桥接：模块职责、稳定契约、mcp 2.x 适配、OAuth 全链路 |
| [design-decisions.md](docs/design-decisions.md) | 已否决的设计方案与重审条件（防重新发明轮子） |
| [testing.md](docs/testing.md) | 测试体系、防膨胀规约、CI 模块矩阵与教训 |
| [ui-contributions.md](docs/ui-contributions.md) | 模块界面扩展点契约（插槽/清单/贡献点） |
| [feishu-task-binding.md](docs/feishu-task-binding.md) | 飞书任务委派 Agent 执行接入手册 |

## 六、项目级 skill 扩展点

未来如需新增项目级 skill，放置位置（按优先级从高到低）：

1. `<repo>/.zcode/skills/<name>/SKILL.md`
2. `<repo>/.agents/skills/<name>/SKILL.md`

仅放置规则「按文件路径条件触发」无法实现——ZCode 没有该机制，需要走 AGENTS.md（全量注入）或
SKILL.md（按 description 关键词触发）两条路径。
