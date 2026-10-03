"""run_python 工具注册（沉睡分组 code）。"""

from core.config import get_config_bool
from core.tool_errors import ErrorCause, tool_error
from entities._sdk import tool

_SLEEP_BRIEF = (
    "Python 脚本内用 tools.<工具名>(参数=值) 同步调用其他工具，支持循环/条件/异常处理，"
    "只有打印输出回到上下文"
)


@tool(
    name="run_python",
    group="code",
    allow_sleep=True,
    sleep_brief=_SLEEP_BRIEF,
    timeout=660,  # 工具自身超时 = 脚本超时上限 600s + 进程启动/收尾余量
    description="运行 Python 脚本编排其他工具：脚本内用 tools.<工具名>(参数名=值) 同步调用"
    "（循环/条件/try-except 均可），每次调用仍过审批门，结果只有 print 文本回到上下文。"
    "适合 3 步以上的批量文件处理、多步流水线、反复试探的任务；"
    "不调用工具的纯计算用 python_exec 更直接，单次调用直接调对应工具。",
)
async def run_python(code: str, timeout: int = 0) -> str:
    """运行 Python 脚本编排其他工具。

    Args:
        code: 脚本源码。全局对象 tools 是工具代理：result = tools.read_file(path="...")
              同步返回工具结果（字符串），失败抛 ToolError（可 try/except 捕获继续）；
              tools.list() 返回可调用工具目录。print() 的文本即本次运行的输出。
              脚本不能回复用户（send_message 等输出/编排/交互工具在脚本内不可用）。
        timeout: 超时秒数（0=默认 120，上限 600）
    """
    if not get_config_bool("codebox_enabled", True):
        return tool_error(
            "代码编排已禁用（codebox_enabled=false）",
            cause=ErrorCause.CONFIG,
            hint="在配置 entity/codebox 中开启后重试",
        )
    from .sandbox import run_script
    return await run_script(code, timeout=timeout)
