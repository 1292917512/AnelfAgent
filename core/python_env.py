"""宿主 Python 环境事实，供运行时上下文与环境管理工具共用。"""
import importlib.util
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from core.log import log

# 环境管理器检测缓存
_env_manager_cache: Dict[str, Dict[str, Any]] = {}

# ==================== 环境管理器检测 ====================

def detect_env_manager(python_path: Optional[str] = None) -> Dict[str, Any]:
    """检测 Python 环境的管理工具（uv / conda / pip）。

    判定依据：venv 的 pyvenv.cfg 中 `uv = x.y.z` 标记（uv 创建 venv 时写入），
    其次按路径特征识别 conda，默认视为 pip 管理。
    uv 管理的 venv 默认不含 pip，属正常状态而非故障。
    """
    python_exe = python_path or sys.executable

    if python_exe in _env_manager_cache:
        return _env_manager_cache[python_exe]

    info: Dict[str, Any] = {'manager': 'pip', 'uv_managed': False, 'uv_version': None}

    cfg_path = Path(python_exe).parent.parent / "pyvenv.cfg"
    if cfg_path.exists():
        try:
            for line in cfg_path.read_text().splitlines():
                key, _, value = line.partition('=')
                if key.strip() == 'uv':
                    info['uv_managed'] = True
                    info['manager'] = 'uv'
                    info['uv_version'] = value.strip() or None
                    break
        except Exception as e:
            log(f"⚠️ 读取pyvenv.cfg失败: {cfg_path} - {str(e)}", "DEBUG")

    if not info['uv_managed'] and any(k in python_exe for k in ('conda', 'miniconda', 'anaconda')):
        info['manager'] = 'conda'

    _env_manager_cache[python_exe] = info
    return info


# 运行环境摘要缓存：解释器/包管理器布局是进程级不变量，
# 人设层（[运行环境] 块）指纹依赖其字节稳定，首次调用后不再重算
_runtime_env_summary_cache: Optional[str] = None


def get_runtime_env_summary() -> str:
    """构建宿主解释器与包管理器摘要，进程内冻结以保持提示词前缀稳定。"""
    global _runtime_env_summary_cache
    if _runtime_env_summary_cache is not None:
        return _runtime_env_summary_cache

    exe = sys.executable
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    shell_python = shutil.which("python3") or ""
    if shell_python and os.path.realpath(shell_python) != os.path.realpath(exe):
        line = (
            f"宿主环境: 项目环境为宿主大环境，python_exec 为其解释器 {exe} ({version})；"
            f"shell 的 python3 为 {shell_python}（两者不同）"
        )
    else:
        line = (
            f"宿主环境: 项目环境为宿主大环境，shell 的 python3 与 python_exec "
            f"均为其解释器 {exe} ({version})"
        )
        if detect_env_manager(exe)["manager"] == "uv":
            line += "，venv 由 uv 创建、不含 pip"
        elif importlib.util.find_spec("pip") is None:
            line += "，不含 pip"

    _runtime_env_summary_cache = line
    return line


def clear_env_cache() -> None:
    """清除环境管理器检测缓存；会话前缀摘要保持冻结。"""
    _env_manager_cache.clear()
