"""Git 环境管理：配置读写、代理管理、连通性测试。"""

import time
from typing import Any, Dict, Tuple

from core.command import run_command
from core.async_helper import AsyncHelper
from core.log import log

dual_mode = AsyncHelper.dual_mode


def _run_git(*args: str, timeout: int = 20) -> Any:
    """执行 git 命令，返回 run_command 结果。"""
    return run_command(["git", *args], timeout_sec=timeout)


# ==================== 配置读写 ====================

@dual_mode
def git_config_get_all() -> Dict[str, str]:
    """一次性获取所有 Git 全局配置，返回 {key: value} 字典。"""
    result = _run_git("config", "--global", "--list")
    if not result.ok or not result.stdout:
        return {}
    config: Dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            config[k] = v
    return config


@dual_mode
def git_config_get(key: str) -> str:
    """获取单个 Git 全局配置值，不存在返回空字符串。"""
    return git_config_get_all().get(key, "")


@dual_mode
def git_config_set(key: str, value: str) -> Tuple[bool, str]:
    """设置 Git 全局配置项。"""
    result = _run_git("config", "--global", key, value)
    if result.ok:
        log(f"Git 配置已设置: {key} = {value}")
        return True, "设置成功"
    return False, result.stderr or "设置失败"


@dual_mode
def git_config_unset(key: str) -> Tuple[bool, str]:
    """删除 Git 全局配置项（不存在视为成功）。"""
    result = _run_git("config", "--global", "--unset", key)
    if result.ok:
        return True, "已删除"
    stderr = result.stderr.lower()
    if "not" in stderr or result.returncode == 5:
        return True, "配置项不存在"
    return False, result.stderr or "删除失败"


@dual_mode
def git_config_list() -> Tuple[bool, str]:
    """列出所有 Git 全局配置（原始文本）。"""
    result = _run_git("config", "--global", "--list")
    return (True, result.stdout or "无配置项") if result.ok else (False, result.stderr or "获取失败")


# ==================== 聚合查询 ====================

@dual_mode
def get_user_config() -> Dict[str, str]:
    """获取 Git 用户配置（单次子进程调用）。"""
    all_cfg = git_config_get_all()
    return {
        "user_name": all_cfg.get("user.name", ""),
        "user_email": all_cfg.get("user.email", ""),
        "autocrlf": all_cfg.get("core.autocrlf", ""),
        "default_branch": all_cfg.get("init.defaultbranch", ""),
        "http_proxy": all_cfg.get("http.proxy", ""),
        "https_proxy": all_cfg.get("https.proxy", ""),
    }


# ==================== 代理管理 ====================

@dual_mode
def set_proxy(http_proxy: str = "", https_proxy: str = "") -> Tuple[bool, str]:
    """设置 Git 代理。"""
    if not http_proxy and not https_proxy:
        return False, "未提供代理地址"

    errors: list[str] = []
    if http_proxy:
        ok, msg = git_config_set("http.proxy", http_proxy)
        if not ok:
            errors.append(f"HTTP: {msg}")
    if https_proxy:
        ok, msg = git_config_set("https.proxy", https_proxy)
        if not ok:
            errors.append(f"HTTPS: {msg}")

    if errors:
        return False, "; ".join(errors)
    log(f"Git 代理已设置: http={http_proxy}, https={https_proxy}")
    return True, "代理设置成功"


@dual_mode
def unset_proxy() -> Tuple[bool, str]:
    """清除 Git 代理配置。"""
    ok1, _ = git_config_unset("http.proxy")
    ok2, _ = git_config_unset("https.proxy")
    if ok1 and ok2:
        log("Git 代理已清除")
        return True, "代理已清除"
    return False, "部分清除失败"


# ==================== 连通性测试 ====================

@dual_mode
def test_github_connectivity() -> Dict[str, Any]:
    """测试 GitHub 连通性，返回结果字典。"""
    all_cfg = git_config_get_all()
    http_proxy = all_cfg.get("http.proxy", "")
    https_proxy = all_cfg.get("https.proxy", "")

    start = time.time()
    result = _run_git("ls-remote", "https://github.com/octocat/Hello-World.git", timeout=30)
    elapsed_ms = round((time.time() - start) * 1000, 2)

    base = {"response_time": elapsed_ms, "http_proxy": http_proxy, "https_proxy": https_proxy}

    if result.ok:
        log(f"GitHub 连通性测试成功 ({elapsed_ms}ms)")
        return {**base, "success": True, "message": "连接成功",
                "detail": result.stdout[:200]}
    else:
        error = result.stderr or result.stdout or "未知错误"
        log(f"GitHub 连通性测试失败: {error[:100]}", "WARNING")
        return {**base, "success": False, "message": "连接失败", "error": error}
