"""GitHub 频道 — 监听仓库动态并推送给 AI。

接收双模:REST 轮询(默认,零公网依赖)+ webhook(可选,需公网/隧道);
事件经规范化管线分级(立即/聚合/静默)后走标准入站消息链推送给 AI,
AI 经 ``github_*`` 工具面反查与回写。设计见 ``projects/anelf-github-channel-plan.md``。
"""
