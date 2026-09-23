"""工作流 — journal 化的可恢复 DAG 编排（agent 层子系统）。

- ``spec``：声明式规格模型与校验（DAG/门控/续聊约束）
- ``journal``：SQLite 断点恢复事实源（准入即落 running / 终态一笔写 / 事件单调序）
- ``engine``：编排引擎（拓扑调度 / 缓存结算 / 修订导入 / 门控重跑）
- ``recovery``：启动收敛（崩溃残留 running run → stopped(interrupted)）
- ``tools``：AI 工具面（启动/查询/停止/续跑，经晚绑定端口消费引擎实例）
"""

from agent.workflow.engine import WorkflowEngine
from agent.workflow.spec import WorkflowSpec, WorkflowSpecError, parse_spec

__all__ = ["WorkflowEngine", "WorkflowSpec", "WorkflowSpecError", "parse_spec"]
