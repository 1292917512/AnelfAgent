"""屏幕源实体的工具注册入口（实体发现机制的加载锚点）。

本实体不暴露 AI 工具：屏幕画面的查看/监视/注入统一由核心视觉能力
（agent.vision 的 vision_look / vision_watch / vision_sources 工具组）提供，
避免同一能力出现两套工具面。实体包导入（__init__.py）即完成
ScreenSource 注册与实体配置面登记。
"""

from __future__ import annotations
