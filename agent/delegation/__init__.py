"""子代理调度系统：复杂任务拆分委托与并行执行。

- profile:            子代理档案 schema（模型面 + 执行面，委托侧单一权威）
- sub_agent:          子代理（隔离上下文执行，leaf/orchestrator 角色）
- delegation_manager: 并发调度、预算控制、结果聚合、后台模式
- delegate_tool:      AI 可调用的 delegate_task 工具
- steer:              运行中委托的步骤边界转向（steer/after 双档）
- journal:            委托运行日志（进度/交接/账本，崩溃恢复数据源）

包级导出按需延迟解析：agent.llm 在模块顶层引用 profile（档案存储宿主），
此处急切引入 delegation_manager 会经 agent.mind 构成环——消费方一律
从子模块导入（``from agent.delegation.delegation_manager import ...``）。
"""
