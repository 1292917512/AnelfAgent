"""
系统命令执行接口
提供统一的命令执行和工具检测功能
"""

import os
import shutil
import subprocess
import platform
from dataclasses import dataclass
from typing import List, Dict, Optional, Union

from core.async_helper import dual_mode
from core.log import log


@dataclass
class CommandResult:
    """命令执行结果"""
    ok: bool
    stdout: str
    stderr: str


@dual_mode
def run_command(command: Union[str, List[str]], timeout_sec: int = 300, env_vars: Optional[Dict[str, str]] = None,
                shell: Optional[bool] = None, cwd: Optional[str] = None) -> CommandResult:
    """执行系统命令

    Args:
        command: 命令字符串或参数列表
        timeout_sec: 超时时间（秒）
        env_vars: 额外的环境变量
        shell: 是否使用shell模式，None时自动判断
        cwd: 工作目录，None 时继承当前进程目录

    Returns:
        CommandResult: 执行结果
    """
    try:
        # 记录命令执行信息
        cmd_str = command if isinstance(command, str) else ' '.join(command)

        # 准备环境变量
        env = {**os.environ, "RUNZSH": "no", "CHSH": "no", "KEEP_ZSHRC": "yes"}
        if env_vars:
            env.update(env_vars)

        # 自动判断shell模式
        use_shell = isinstance(command, str) if shell is None else shell

        # 准备subprocess参数，在Windows下隐藏命令行窗口
        run_kwargs = {
            'shell': use_shell,
            'capture_output': True,
            'text': True,
            'encoding': 'utf-8',
            'errors': 'replace',
            'timeout': timeout_sec,
            'env': env,
        }
        if cwd:
            run_kwargs['cwd'] = cwd

        # Windows平台下隐藏命令行窗口
        if platform.system() == "Windows":
            run_kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW

        result = subprocess.run(command, **run_kwargs)

        # 记录执行结果
        if result.returncode == 0:
            log(f"✅ 命令执行成功: {cmd_str[:50]}{'...' if len(cmd_str) > 50 else ''}", "DEBUG")
        else:
            log(f"❌ 命令执行失败 (返回码: {result.returncode}): {cmd_str[:50]}{'...' if len(cmd_str) > 50 else ''}",
                "WARNING")
            if result.stderr:
                log(f"错误输出: {result.stderr[:200]}{'...' if len(result.stderr) > 200 else ''}", "DEBUG")

        return CommandResult(
            ok=result.returncode == 0,
            stdout=result.stdout.strip() if result.stdout else "",
            stderr=result.stderr.strip() if result.stderr else ""
        )

    except subprocess.TimeoutExpired:
        log(f"⏰ 命令执行超时 ({timeout_sec}s): {cmd_str[:50]}{'...' if len(cmd_str) > 50 else ''}", "WARNING")
        return CommandResult(False, "", "命令超时")
    except FileNotFoundError:
        log(f"命令未找到: {cmd_str[:50]}{'...' if len(cmd_str) > 50 else ''}", "DEBUG")
        return CommandResult(False, "", f"命令未找到: {cmd_str.split()[0] if cmd_str else ''}")
    except Exception as e:
        log(f"❌ 命令执行异常: {cmd_str[:50]}{'...' if len(cmd_str) > 50 else ''} - {str(e)}", "WARNING")
        return CommandResult(False, "", str(e))


@dual_mode
def which_tool(tool: str) -> Optional[str]:
    """获取工具在系统中的完整路径"""
    try:
        path = shutil.which(tool)
        if path:
            log(f"✅ 找到工具: {tool} -> {path}", "DEBUG")
        else:
            log(f"❌ 未找到工具: {tool}", "DEBUG")
        return path
    except Exception as e:
        log(f"❌ 检查工具路径失败: {tool} - {str(e)}", "ERROR")
        return None


@dual_mode
def get_tool_version(tool: str, version_args: List[str] = None, timeout_sec: int = 3) -> str:
    """获取工具版本信息
    
    Args:
        tool: 工具名称
        version_args: 版本命令参数，默认为["--version"]
        timeout_sec: 超时时间（秒）
        
    Returns:
        版本信息字符串，失败时返回空字符串
    """
    log(f"🔍 获取工具版本: {tool}", "DEBUG")

    if not which_tool(tool):
        log(f"❌ 工具不存在，无法获取版本: {tool}", "WARNING")
        return ""

    version_args = version_args or ["--version"]
    use_shell = platform.system() == "Windows"
    result = run_command([tool] + version_args, timeout_sec, shell=use_shell)

    if result.ok and result.stdout:
        # 取第一行作为版本信息并限制长度
        version = result.stdout.split('\n')[0]
        truncated_version = version[:47] + "..." if len(version) > 50 else version
        log(f"✅ 获取工具版本成功: {tool} -> {truncated_version}", "DEBUG")
        return truncated_version
    else:
        log(f"获取工具版本失败: {tool} - {result.stderr or '无输出'}", "DEBUG")
        return ""
