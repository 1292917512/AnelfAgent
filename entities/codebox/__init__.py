"""代码编排实体（codebox）：run_python 脚本工具（沉睡分组 code）。

模型写 Python 脚本，在一次性子进程内运行；脚本经 JSON 行协议同步调用注册表
其他工具（每次调用过统一审批门），只有打印输出回到模型上下文——把「批量
文件处理、多步流水线、反复试探」从十几轮工具往返压缩成一次调用。

模式语义（长在既有机制上，不另造 mode 对象）：
- 分组 code 默认沉睡，模型经 activate_tool_group(group="code") 按需激活，
  用完经 deactivate_tool_group 关闭（双向阀）；
- 当前激活状态经 exec_context [已激活工具分组] 每轮动态呈现；
- store 类跨调用状态不提供——持久化走便签/文件等既有通道（避免冗余机制）。

框架对齐点：
- entity()/entity_manifest()：分组描述与清单
- tools.py @tool(allow_sleep=True)：沉睡分组注册
- register_configs_safe：entity/codebox 配置节
- runner.py：纯 stdlib 子进程引导（按文件路径直接执行，无仓库导入）
"""

from core.config import register_configs_safe
from entities._sdk import entity, entity_manifest

entity("code", "代码编排 - Python 脚本内循环/条件调用其他工具，适合批量多步任务")
entity_manifest(display_name="代码编排", icon="code", order=27, group="code")

register_configs_safe({
    "entity/codebox": {
        "codebox_enabled": {
            "description": "代码编排总开关（关闭后 run_python 不可用）",
            "default": True,
        },
        "codebox_default_timeout": {
            "description": "脚本默认超时秒数",
            "default": 120,
        },
        "codebox_max_timeout": {
            "description": "脚本超时上限秒数",
            "default": 600,
        },
        "codebox_memory_limit_mb": {
            "description": "子进程地址空间上限 MB（POSIX rlimit，Windows 不生效）",
            "default": 1024,
        },
        "codebox_max_tool_calls": {
            "description": "单次运行的工具调用次数上限",
            "default": 100,
        },
        "codebox_max_result_chars": {
            "description": "单次工具结果传给脚本的数据上限；超限报错，保持数据完整性",
            "default": 2000000,
            "min": 1024,
            "max": 32000000,
            "advanced": True,
        },
        "codebox_output_chars": {
            "description": "脚本打印输出回传字符上限",
            "default": 20000,
        },
    }
})

from . import tools  # noqa: E402,F401  触发 @tool 注册
