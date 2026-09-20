"""模型控制实体 — AI 自主管理模型选择、参数与优先级。"""

from core.entity import EntityRegistry

EntityRegistry.register_group_order("model_control", 50)
EntityRegistry.register_group_order("ollama", 51)
