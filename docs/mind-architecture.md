# 思维系统架构

本文是面向维护者的现状说明，按当前源码组织，不记录实现轮次或任务过程。代码和测试是最终依据；修改思维链路时，应先确认本文涉及的边界是否仍然成立。

## 一、从消息到自主周期

```text
频道/服务消息
  → PFC（待处理消息与一般任务队列）
  → Mind.execute_mind()
  → cycle：廉价检查 / 态势收集 / 元决策
  → decision_executor：REPLY、REFLECT、PLAN、TASK、TOOL_ACTION 等
  → reply 或后台任务
```

`Mind` 是组合宿主，不把所有逻辑都写在 `mind.py`：

| 组件 | 责任 | 入口 |
|---|---|---|
| `agent/mind/mind.py` | 组装运行时组件，提供周期、回复和反思的宿主接口 | `Mind.execute_mind()`、`Mind.reply()` |
| `agent/mind/cycle.py` | 自主周期的锁、fast-path、态势收集、元决策和收尾 | `_cycle_body()` |
| `agent/mind/prefrontal_cortex.py` | 对外提供待办、工具、上下文和会话状态门面 | `PrefrontalCortex` |
| `agent/mind/work_memory.py` | 待办消息、短期状态和动态任务数据 | `WorkMemory` |
| `agent/mind/tools/decision_executor.py` | 把结构化决策分发到回复、反思、计划和任务执行器 | 决策执行函数 |
| `agent/mind/tools/think_loop.py` | 单次回复的多轮模型调用和工具调用循环 | `think_loop()` |

Mind 实例的自主周期由 `_cycle_lock` 串行化。没有待处理消息、任务或分析项时直接结束；普通消息在没有复杂任务、目标或画像工作时走 fast-path，直接创建 `REPLY` 决策。复杂场景才收集完整态势并调用元决策模型。心跳周期会把长决策转为后台执行，避免阻塞新消息。

## 二、回复与工具循环

`Mind.reply()` 进入 `agent/mind/tools/think_loop.py`。一次回复通常重复以下步骤，直到模型给出正文、调用 `end_reply` 或达到停止条件：

1. 从当前 scope 取得上下文和可用工具。
2. 调用统一 LLM 入口，保留必要的工具调用和 reasoning 字段。
3. 执行工具并把结构化结果加入当前轮的工具链。
4. 经过结果预算、错误分类、重试和循环守卫后决定继续、重试或停止。
5. 完成回复，写入消息历史并释放本轮动态工具状态。

`guardrails.py` 负责检测精确重复、连续失败和无进展循环；`result_budget.py` 控制单个工具结果和整轮结果的上下文占用；`context_compressor.py` 在真实用量超过窗口时压缩历史并重新建立上下文。工具调用异常应通过统一错误结构返回给模型，不能靠解析展示文本判断。

## 三、上下文组装与缓存

`PrefrontalCortex` 把 `WorkMemory`、`ToolAssembly` 和 `ContextAssembly` 接在一起。`ContextAssembly.build_llm_context()` 使用 `ContextPipeline` 按变动频率从静到动组装消息，并为每个块附加内部 `_layer` 标记：

```text
stable       人设、工具规则和稳定工具目录
summary      对话摘要
conversation 对话历史（追加）
context      便签、文件索引等尾部上下文
session      画像、召回、短期记忆和技能
message      本轮状态或安全提示
tool_chain   本轮工具调用（think_loop 管理）
provider     上下文提供者实时注入
exec_context 本轮执行状态
```

层的注册和排序由 `agent/mind/context_pipeline.py` 统一维护；新增上下文块应声明其层和变动率，不要在调用点手工插入消息。`PromptCacheManager` 对 stable/context 等构建结果做内容寻址缓存；发送前由 `agent/mind/message_schema.normalize_for_send()` 清理内部层标记和来源字段，供应商不可见。

保持缓存稳定的基本规则：稳定内容放前面，历史只追加，易变状态放动态尾部；系统注入消息带 `_source` 供追踪但在发送边界剥离；折叠或压缩后重新建立合法缓存基线，不把错误文案注入上下文。

## 四、工具集合如何形成

`agent/mind/tool_assembly.py` 维护当前 scope 的工具集合。候选来源包括：

- 永驻工具（`always`）
- MCP 服务和频道能力
- 消息标签或实际媒体触发的工具
- 最近使用的热工具
- `list_entity_methods` 发现的工具
- 已激活的沉睡分组

候选工具先经过 `core.tool_gate` 的 `check_fn` 门控，再由 `ToolActivationManager` 处理沉睡/激活状态，最后合并成确定顺序的 schema。工具集合版本变化会通知 think loop 重建请求；不应在单个业务工具里私自维护另一套工具目录。

工具激活状态按会话 scope 隔离。沉睡工具只展示简短说明，模型需要时调用 `activate_tool_group`；频道、标签和动态发现的工具在会话结束时按既定生命周期清理。

## 五、心跳、记忆与后台工作

`HeartbeatEngine` 由 `Mind` 持有，按 `mind.heartbeat_interval` 驱动 `tick()`。一次 tick 负责维护任务和记忆状态、选择到期调度、调用 `TaskExecutor`，并写入心跳状态。idle、scheduled、heartbeat 和 manual 是调度层的触发模式；调度不应绕过统一任务执行器直接创建长期后台循环。

记忆由 `agent/memory/` 提供存储、召回、摘要、画像和治理能力。思维系统只通过记忆门面读取或写入，不在 `mind` 中复制数据库逻辑。规划、委托和技能评审同样以任务或反思方式运行，结果通过统一后台任务和消息路由回到所属 scope。

后台任务必须保存所属 scope 和任务状态；完成通知走统一投递管道。取消或停止表示请求已受理，只有执行器确认收尾后才能报告完成。

## 六、修改思维系统前的检查顺序

1. 明确修改属于周期、回复循环、上下文、工具装配、心跳还是记忆边界。
2. 先读对应组件及其测试，确认数据从哪里进入、在哪个层级变换、最终由谁持久化或投递。
3. 检查 `_layer`、`_source`、scope 和工具版本是否会泄露到供应商或跨会话串线。
4. 为新增行为补充最近组件的单元测试；涉及真实 LLM、频道或外部服务时再运行相应集成测试。
5. 修改完成后检查上下文顺序、并发锁、取消收尾、错误结构和导入方向。

不要把本页重新扩展成逐轮变更记录；需要记录一次验收结果时使用专门的报告或计划文件。