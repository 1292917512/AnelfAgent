"""AnelfAgent — 统一智能体框架。

子系统：
- **runtime**        — AgentApp 运行时、Bootstrap、单例管理
- **llm**            — 统一 LLM 接口（OpenAI 兼容 / Ollama / 多客户端管理）
- **mind**           — 思维系统（自主决策 / 多轮推理 / 工具编排）
- **memory**         — 语义记忆（FTS5 + Embedding 混合检索 / 便签 / 文件索引）
- **introspection**  — 内省系统（反思单元 / 任务单元 / 编排器）
- **planning**       — 自主规划（目标管理 / 执行追踪）
- **channel**        — 频道基础设施（BaseChannel / ChannelManager / schemas）
- **messages**       — 统一消息模型
- **storage**        — SQLite 混合存储与 StorageRouter
- **respond**        — 输入感知 / 输出路由
- **config**         — 集中配置提供器
"""

from .runtime.agent_app import AgentApp, AgentEvent, AgentStatus, AgentStats, get_agent_app

__all__ = [
    "AgentApp",
    "AgentEvent",
    "AgentStatus",
    "AgentStats",
    "get_agent_app",
]
