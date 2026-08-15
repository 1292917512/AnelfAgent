"""
系统命令执行接口
提供统一的命令执行和工具检测功能
"""

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

from core.async_helper import dual_mode
from core.log import log

# 版本信息输出的最大长度（超出截断并追加省略号）
_VERSION_OUTPUT_MAX_LEN = 50


def _is_zsh_related(command: Union[str, List[str]]) -> bool:
    """命令是否与 zsh 相关（决定是否需要注入 oh-my-zsh 安装防护环境变量）。"""
    cmd_str = command if isinstance(command, str) else " ".join(command)
    return "zsh" in cmd_str


@dataclass
class CommandResult:
    """命令执行结果"""
    ok: bool
    stdout: str
    stderr: str
    # 原始退出码（被异常拦截的路径为 None）：hook 类调用方按码路由
    returncode: Optional[int] = None


def _run_with_group_kill(command: Union[str, List[str]], timeout_sec: float,
                         run_kwargs: Dict[str, Any]) -> "subprocess.CompletedProcess[str]":
    """POSIX 下以独立进程组执行，超时时 SIGTERM→SIGKILL 整组终止（防孙进程泄漏）。"""
    import signal
    kwargs = dict(run_kwargs)
    if kwargs.pop("capture_output", False):
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    stdin_data = kwargs.pop("input", None)
    proc = subprocess.Popen(command, stdin=subprocess.PIPE if stdin_data is not None else None, **kwargs)
    try:
        stdout, stderr = proc.communicate(input=stdin_data, timeout=timeout_sec)
        return subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=2)
        except (ProcessLookupError, subprocess.TimeoutExpired, PermissionError):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                log("_run_with_group_kill 异常已忽略", "DEBUG")
        proc.wait()
        raise


@dual_mode
def run_command(command: Union[str, List[str]], timeout_sec: int = 300, env_vars: Optional[Dict[str, str]] = None,
                shell: Optional[bool] = None, cwd: Optional[str] = None,
                stdin_data: Optional[str] = None) -> CommandResult:
    """执行系统命令

    Args:
        command: 命令字符串或参数列表
        timeout_sec: 超时时间（秒）
        env_vars: 额外的环境变量
        shell: 是否使用shell模式，None时自动判断
        cwd: 工作目录，None 时继承当前进程目录
        stdin_data: 可选，写入子进程 stdin 的文本（hook 类调用经此传 JSON payload）

    Returns:
        CommandResult: 执行结果
    """
    try:
        # 记录命令执行信息
        cmd_str = command if isinstance(command, str) else ' '.join(command)

        # 准备环境变量；oh-my-zsh 安装防护变量仅在 zsh 相关命令时注入
        env = dict(os.environ)
        if _is_zsh_related(command):
            env.update({"RUNZSH": "no", "CHSH": "no", "KEEP_ZSHRC": "yes"})
        if env_vars:
            env.update(env_vars)

        # 自动判断shell模式
        use_shell = isinstance(command, str) if shell is None else shell

        # 准备subprocess参数，在Windows下隐藏命令行窗口
        run_kwargs: Dict[str, Any] = {
            'shell': use_shell,
            'capture_output': True,
            'text': True,
            'encoding': 'utf-8',
            'errors': 'replace',
            'env': env,
        }
        if cwd:
            run_kwargs['cwd'] = cwd
        if stdin_data is not None:
            run_kwargs['input'] = stdin_data

        is_windows = platform.system() == "Windows"
        if is_windows:
            run_kwargs['creationflags'] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            # 独立进程组：超时时整组终止，避免 shell 子进程（孙进程）泄漏
            # （对齐 Claude Code tree-kill 语义；subprocess.run 只杀直接子进程）
            run_kwargs['start_new_session'] = True

        if is_windows:
            result = subprocess.run(command, timeout=timeout_sec, **run_kwargs)
        else:
            result = _run_with_group_kill(command, timeout_sec, run_kwargs)

        # 记录执行结果
        if result.returncode == 0:
            log(f"✅ 命令执行成功: {cmd_str[:50]}{'...' if len(cmd_str) > 50 else ''}", "DEBUG")
        elif not result.stderr:
            # 非零码 + 无错误输出：POSIX 语义多为否定结果（grep 无匹配 /
            # 条件测试不成立），不是执行错误，降级为中性记录避免误导
            log(f"命令以返回码 {result.returncode} 结束（无错误输出，搜索/测试类通常为无匹配）: "
                f"{cmd_str[:50]}{'...' if len(cmd_str) > 50 else ''}", "DEBUG")
        else:
            log(f"❌ 命令执行失败 (返回码: {result.returncode}): {cmd_str[:50]}{'...' if len(cmd_str) > 50 else ''}",
                "WARNING")
            log(f"错误输出: {result.stderr[:200]}{'...' if len(result.stderr) > 200 else ''}", "DEBUG")

        return CommandResult(
            ok=result.returncode == 0,
            stdout=result.stdout.strip() if result.stdout else "",
            stderr=result.stderr.strip() if result.stderr else "",
            returncode=result.returncode,
        )

    except subprocess.TimeoutExpired:
        log(f"⏰ 命令执行超时 ({timeout_sec}s): {cmd_str[:50]}{'...' if len(cmd_str) > 50 else ''}", "WARNING")
        return CommandResult(False, "", f"命令超时 ({timeout_sec}s)")
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
def get_tool_version(tool: str, version_args: Optional[List[str]] = None, timeout_sec: int = 3) -> str:
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
        if len(version) > _VERSION_OUTPUT_MAX_LEN:
            truncated_version = version[:_VERSION_OUTPUT_MAX_LEN - 3] + "..."
        else:
            truncated_version = version
        log(f"✅ 获取工具版本成功: {tool} -> {truncated_version}", "DEBUG")
        return truncated_version
    else:
        log(f"获取工具版本失败: {tool} - {result.stderr or '无输出'}", "DEBUG")
        return ""
