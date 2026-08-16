"""NoneBot 桥接线协议 — 父进程与 worker 子进程共享的消息常量与编解码。

本文件必须保持零第三方依赖（纯 stdlib）：
- 父进程经 ``channels.nonebot_bridge.worker.protocol`` 导入（主应用环境）；
- worker 以脚本方式运行（``python worker/bot.py``），经同目录裸导入
  ``from protocol import ...``（worker venv 环境，仅有 nonebot/websockets）。

消息流转：
- worker → 父进程：``event``（平台消息）/ ``status``（bot 与插件快照）/
  ``log``（日志行）/ ``send_result`` / ``cmd_result`` / ``hello``
- 父进程 → worker：``send``（发送请求）/ ``cmd``（控制命令）/ ``ping``
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

# ------------------------------------------------------------------
# 消息类型
# ------------------------------------------------------------------

MSG_HELLO = "hello"            # worker → 父进程：握手 + 初始状态快照
MSG_EVENT = "event"            # worker → 父进程：平台消息（已转换）
MSG_STATUS = "status"          # worker → 父进程：bot/插件状态快照
MSG_LOG = "log"                # worker → 父进程：日志行
MSG_SEND = "send"              # 父进程 → worker：发送请求（seq 关联）
MSG_SEND_RESULT = "send_result"  # worker → 父进程：发送结果（seq 关联）
MSG_CMD = "cmd"                # 父进程 → worker：控制命令（seq 关联）
MSG_CMD_RESULT = "cmd_result"  # worker → 父进程：命令结果（seq 关联）
MSG_PING = "ping"              # 父进程 → worker：心跳
MSG_PONG = "pong"              # worker → 父进程：心跳应答

# 控制命令 action 取值
CMD_GET_STATUS = "get_status"      # 拉取状态快照
CMD_RUN_COMMAND = "run_command"    # 合成事件触发插件命令并捕获回复
CMD_GET_PLUGINS = "get_plugins"    # 拉取已加载插件详情

# worker 环境变量（父进程 spawn 时注入）
ENV_BRIDGE_WS_URL = "ANELF_BRIDGE_WS_URL"
ENV_BRIDGE_TOKEN = "ANELF_BRIDGE_TOKEN"

# 线协议版本（不兼容变更时递增，两端校验）
WIRE_VERSION = 3


def encode(payload: Dict[str, Any]) -> str:
    """将消息编码为线格式字符串。"""
    return json.dumps(payload, ensure_ascii=False, default=str)


def decode(raw: str) -> Optional[Dict[str, Any]]:
    """解码线格式字符串，失败返回 None。"""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None
