"""ACP（Agent Client Protocol）服务面 — 把 AnelfAgent 暴露为 ACP 智能体。

独立进程入口（非守护进程内组件，刻意零内部导入，仅依赖标准库与 httpx）：
外部 ACP 客户端（如 acpx / feishu-task-agent 官方桥）以
``anelf-acp acp serve`` 拉起本包，经 ndjson JSON-RPC 驱动守护进程的
http_api 频道完成任务执行，与守护进程保持进程级解耦。
"""
