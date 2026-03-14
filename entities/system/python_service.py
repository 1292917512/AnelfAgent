"""
Python环境管理工具
提供Python、Conda、uv等工具的检测、安装、配置和包管理功能
"""

import os
import platform
import re
import sys
import json
from typing import Dict, List, Tuple, Optional, Any
from pathlib import Path
from core.command import run_command, CommandResult, which_tool
from core.async_helper import AsyncHelper
from core.log import log

dual_mode = AsyncHelper.dual_mode

# pip可用性缓存
_pip_availability_cache: Dict[str, bool] = {}

# ==================== pip 工具检测 ====================

@dual_mode
def is_pip_available(python_path: str = None) -> bool:
    """检查指定Python环境中pip是否可用（带缓存）"""
    python_exe = python_path or sys.executable
    
    # 检查缓存
    if python_exe in _pip_availability_cache:
        return _pip_availability_cache[python_exe]
    
    log(f"🔍 检查pip可用性: {python_exe}", "DEBUG")
    
    try:
        # 检查Python可执行文件
        if not os.path.exists(python_exe):
            log(f"❌ Python可执行文件不存在: {python_exe}", "WARNING")
            _pip_availability_cache[python_exe] = False
            return False
        
        # 快速检查pip模块
        result = run_command([python_exe, "-m", "pip", "--version"], timeout_sec=5)
        available = result.ok
        
        _pip_availability_cache[python_exe] = available
        
        if available:
            log(f"✅ pip可用: {python_exe}", "DEBUG")
        else:
            log(f"❌ pip不可用: {python_exe} - {result.stderr}", "WARNING")
        
        return available
    except Exception as e:
        log(f"❌ pip可用性检查异常: {python_exe} - {str(e)}", "ERROR")
        _pip_availability_cache[python_exe] = False
        return False


def clear_pip_cache():
    """清空pip缓存"""
    global _pip_availability_cache
    _pip_availability_cache.clear()
    log("🧹 pip缓存已清空", "DEBUG")


# ==================== Python 环境检测 ====================

@dual_mode
def get_python_info() -> Dict[str, Any]:
    """获取当前Python环境的详细信息"""
    log("🐍 获取当前Python环境信息", "DEBUG")
    
    try:
        info = {
            'python_path': sys.executable,
            'python_version': f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            'python_version_info': sys.version,
            'pip_version': None,
            'pip_available': False,
            'venv_active': False,
            'venv_path': None,
            'site_packages': None
        }
        
        log(f"🔍 Python可执行文件: {info['python_path']}", "DEBUG")
        log(f"📊 Python版本: {info['python_version']}", "DEBUG")
        
        # 检测虚拟环境
        if hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix):
            info['venv_active'] = True
            info['venv_path'] = sys.prefix
            log(f"🌿 检测到虚拟环境: {info['venv_path']}", "DEBUG")
        else:
            log("🏠 使用系统Python环境", "DEBUG")
        
        # 检查pip可用性
        log("🔍 检查pip可用性", "DEBUG")
        info['pip_available'] = is_pip_available(sys.executable)
        
        if info['pip_available']:
            # 获取 pip 版本
            pip_result = run_command([sys.executable, "-m", "pip", "--version"], timeout_sec=5)
            if pip_result.ok:
                pip_match = re.search(r'pip (\d+\.\d+\.\d+)', pip_result.stdout)
                if pip_match:
                    info['pip_version'] = pip_match.group(1)
                    log(f"📦 pip版本: {info['pip_version']}", "DEBUG")
                else:
                    log("⚠️ 无法解析pip版本信息", "WARNING")
            
            # 获取 site-packages 路径
            log("📂 获取site-packages路径", "DEBUG")
            site_result = run_command([sys.executable, "-c", "import site; print(site.getsitepackages()[0])"], timeout_sec=3)
            if site_result.ok:
                info['site_packages'] = site_result.stdout.strip()
                log(f"📚 site-packages: {info['site_packages']}", "DEBUG")
            else:
                log("⚠️ 无法获取site-packages路径", "WARNING")
        else:
            log("⚠️ pip不可用，跳过相关信息获取", "WARNING")
            
        log("✅ Python环境信息获取完成", "DEBUG")
        return info
    except Exception as e:
        log(f"❌ 获取Python环境信息失败: {str(e)}", "ERROR")
        return {'error': str(e)}


@dual_mode
def find_python_installations() -> List[Dict[str, Any]]:
    """查找系统中所有的Python安装"""
    log("🔍 扫描系统中的Python安装", "DEBUG")
    
    installations = []
    
    # 检查 PATH 中的 Python
    python_commands = ['python', 'python3', 'python3.8', 'python3.9', 'python3.10', 'python3.11', 'python3.12']
    log(f"🔎 检查Python命令: {', '.join(python_commands)}", "DEBUG")
    
    found_count = 0
    for cmd in python_commands:
        python_path = which_tool(cmd)
        if python_path:
            version_info = get_python_version_info(python_path)
            if version_info:
                installations.append({
                    'command': cmd,
                    'path': python_path,
                    'version': version_info['version'],
                    'version_info': version_info['full_version']
                })
                found_count += 1
                log(f"✅ 找到Python: {cmd} -> {python_path} (v{version_info['version']})", "DEBUG")
    
    # 去重（基于路径）
    seen_paths = set()
    unique_installations = []
    for install in installations:
        if install['path'] not in seen_paths:
            seen_paths.add(install['path'])
            unique_installations.append(install)
    
    dedupe_removed = len(installations) - len(unique_installations)
    if dedupe_removed > 0:
        log(f"🔄 去重处理，移除 {dedupe_removed} 个重复项", "DEBUG")
    
    log(f"✅ Python安装扫描完成，找到 {len(unique_installations)} 个唯一安装", "DEBUG")
    return unique_installations


@dual_mode
def get_python_version_info(python_path: str) -> Optional[Dict[str, str]]:
    """获取指定Python可执行文件的版本信息"""
    log(f"🔍 获取Python版本信息: {python_path}", "DEBUG")
    
    try:
        result = run_command([python_path, "--version"])
        if result.ok:
            version_line = result.stdout.strip() or result.stderr.strip()
            version_match = re.search(r'Python (\d+\.\d+\.\d+)', version_line)
            if version_match:
                version_info = {
                    'version': version_match.group(1),
                    'full_version': version_line
                }
                log(f"✅ 版本信息获取成功: {version_info['version']}", "DEBUG")
                return version_info
            else:
                log(f"❌ 无法解析版本信息: {version_line}", "WARNING")
        else:
            log(f"❌ Python版本命令失败: {result.stderr}", "WARNING")
        return None
    except Exception as e:
        log(f"❌ 获取Python版本异常: {python_path} - {str(e)}", "ERROR")
        return None


# ==================== Conda 环境检测 ====================

@dual_mode
def is_conda_installed() -> Tuple[bool, Optional[str], Optional[str]]:
    """检查系统是否安装了Conda"""
    log("🔍 检查Conda安装状态", "DEBUG")
    
    conda_path = which_tool("conda")
    if not conda_path:
        log("❌ Conda未安装或不在PATH中", "DEBUG")
        return False, None, None
    
    log(f"✅ 找到Conda: {conda_path}", "DEBUG")
    
    version_result = run_command("conda --version")
    version = version_result.stdout.strip() if version_result.ok else "未知版本"
    
    if version_result.ok:
        log(f"📊 Conda版本: {version}", "DEBUG")
    else:
        log("⚠️ 无法获取Conda版本", "WARNING")
    
    return True, conda_path, version


@dual_mode
def list_conda_environments() -> List[Dict[str, Any]]:
    """列出所有Conda环境"""
    log("🔍 列出Conda环境", "DEBUG")
    
    try:
        # 使用 conda info --envs 获取环境列表
        log("📋 使用 'conda info --envs' 获取环境列表", "DEBUG")
        result = run_command("conda info --envs")
        if not result.ok:
            log(f"❌ conda info --envs 命令失败: {result.stderr}", "WARNING")
            return []
        
        environments = []
        current_prefix = os.environ.get('CONDA_PREFIX', '')
        log(f"🏠 当前Conda前缀: {current_prefix}", "DEBUG")
        
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            # 解析环境行：name    *    /path/to/env
            parts = line.split()
            if len(parts) >= 2:
                env_name = parts[0]
                env_path = parts[-1]  # 最后一个是路径
                is_active = '*' in parts  # 当前激活的环境有 * 标记
                
                # 跳过无效路径
                if not os.path.exists(env_path):
                    log(f"⚠️ 环境路径不存在，跳过: {env_path}", "WARNING")
                    continue
                
                # 获取环境中的 Python 版本
                python_path = Path(env_path) / ('python.exe' if platform.system() == 'Windows' else 'bin/python')
                python_version = None
                
                if python_path.exists():
                    version_info = get_python_version_info(str(python_path))
                    python_version = version_info['version'] if version_info else None
                
                environments.append({
                    'name': env_name,
                    'path': env_path,
                    'python_version': python_version,
                    'is_active': is_active or env_path == current_prefix
                })
                
                log(f"✅ 找到Conda环境: {env_name} ({'激活' if is_active else '未激活'})", "DEBUG")
        
        log(f"✅ Conda环境列表获取完成，共 {len(environments)} 个环境", "DEBUG")
        return environments
        
    except Exception as e:
        log(f"❌ 列出Conda环境失败，尝试JSON方法: {str(e)}", "WARNING")
        # 回退方法：使用JSON格式
        try:
            log("🔄 使用 'conda env list --json' 作为备选方法", "DEBUG")
            result = run_command("conda env list --json")
            if not result.ok:
                log(f"❌ conda env list --json 也失败: {result.stderr}", "ERROR")
                return []
            
            data = json.loads(result.stdout)
            environments = []
            current_prefix = os.environ.get('CONDA_PREFIX', '')
            
            env_paths = data.get('envs', [])
            log(f"📂 从JSON获取到 {len(env_paths)} 个环境路径", "DEBUG")
            
            for env_path in env_paths:
                if not os.path.exists(env_path):
                    log(f"⚠️ 环境路径不存在，跳过: {env_path}", "WARNING")
                    continue
                
                # 从路径中提取环境名
                env_name = Path(env_path).name
                
                # 特殊处理 base 环境
                if 'miniconda3' in env_path or 'anaconda3' in env_path:
                    if env_name in ['miniconda3', 'anaconda3']:
                        env_name = 'base'
                
                # 获取环境中的 Python 版本
                python_path = Path(env_path) / ('python.exe' if platform.system() == 'Windows' else 'bin/python')
                python_version = None
                
                if python_path.exists():
                    version_info = get_python_version_info(str(python_path))
                    python_version = version_info['version'] if version_info else None
                
                environments.append({
                    'name': env_name,
                    'path': env_path,
                    'python_version': python_version,
                    'is_active': env_path == current_prefix
                })
                
                log(f"✅ 找到Conda环境: {env_name} ({'激活' if env_path == current_prefix else '未激活'})", "DEBUG")
            
            log(f"✅ Conda环境列表获取完成（JSON方法），共 {len(environments)} 个环境", "DEBUG")
            return environments
            
        except Exception as json_error:
            log(f"❌ JSON方法也失败: {str(json_error)}", "ERROR")
            return []


# ==================== 包管理 ====================

@dual_mode
def get_installed_packages(python_path: str = None) -> List[Dict[str, str]]:
    """获取指定Python环境中已安装的包列表"""
    python_exe = python_path or sys.executable
    log(f"📦 获取已安装包列表: {python_exe}", "DEBUG")
    
    try:
        # 确保路径存在且可执行
        if not os.path.exists(python_exe):
            log(f"❌ Python可执行文件不存在: {python_exe}", "ERROR")
            return []
        
        # 检查pip可用性
        if not is_pip_available(python_exe):
            log(f"❌ pip不可用，无法获取包列表: {python_exe}", "WARNING")
            return []
        
        # 对于 conda 环境，使用特殊处理方法
        if 'conda' in python_exe or 'miniconda' in python_exe or 'anaconda' in python_exe:
            log("🐍 检测到Conda环境，使用Conda包管理", "DEBUG")
            return _get_conda_packages(python_exe)
        
        # 标准 Python 环境处理
        log("🐍 使用标准Python环境包管理", "DEBUG")
        return _get_standard_packages(python_exe)
        
    except Exception as e:
        log(f"❌ 获取包列表失败: {python_exe} - {str(e)}", "ERROR")
        return []


def _get_conda_packages(python_exe: str) -> List[Dict[str, str]]:
    """获取Conda环境的包列表"""
    log(f"🐍 获取Conda环境包列表: {python_exe}", "DEBUG")
    packages = []
    
    # 使用 conda list 获取包列表
    env_path = str(Path(python_exe).parent.parent)
    env_name = Path(env_path).name
    
    log(f"🏠 Conda环境: {env_name} (路径: {env_path})", "DEBUG")
    
    # 如果是 base 环境，直接用 conda list
    if env_name in ['miniconda3', 'anaconda3', 'conda']:
        log("🔧 使用 'conda list --json' 获取base环境包", "DEBUG")
        result = run_command("conda list --json")
    else:
        log(f"🔧 使用 'conda list -n {env_name} --json' 获取环境包", "DEBUG")
        result = run_command(f"conda list -n {env_name} --json")
    
    if result.ok and result.stdout.strip():
        try:
            conda_packages = json.loads(result.stdout)
            for pkg in conda_packages:
                if isinstance(pkg, dict):
                    packages.append({
                        'name': pkg.get('name', ''),
                        'version': pkg.get('version', '')
                    })
            log(f"✅ Conda包列表获取成功，共 {len(packages)} 个包", "DEBUG")
            return packages
        except json.JSONDecodeError as e:
            log(f"❌ 解析Conda包JSON失败: {str(e)}", "WARNING")
    else:
        log(f"❌ conda list 命令失败: {result.stderr}", "WARNING")
    
    # 回退到 pip 方式
    log("🔄 回退到pip方式获取包列表", "DEBUG")
    return _get_standard_packages(python_exe)


def _get_standard_packages(python_exe: str) -> List[Dict[str, str]]:
    """获取标准Python环境的包列表"""
    log(f"📦 获取标准Python环境包列表: {python_exe}", "DEBUG")
    packages = []
    
    try:
        # 检查 Python 可执行文件
        if not os.path.exists(python_exe):
            log(f"❌ Python可执行文件不存在: {python_exe}", "ERROR")
            return []
        
        # 检查是否可以执行
        test_result = run_command([python_exe, "--version"])
        if not test_result.ok:
            log(f"❌ Python可执行文件无法运行: {python_exe}", "ERROR")
            return []
        
        # 使用 pip list --format=json 获取包列表
        log("🔧 使用 'pip list --format=json' 获取包列表", "DEBUG")
        result = run_command([python_exe, "-m", "pip", "list", "--format=json"], timeout_sec=10)
        
        if result.ok and result.stdout.strip():
            try:
                packages_data = json.loads(result.stdout)
                for pkg in packages_data:
                    packages.append({
                        'name': pkg.get('name', ''),
                        'version': pkg.get('version', '')
                    })
                log(f"✅ pip包列表获取成功，共 {len(packages)} 个包", "DEBUG")
                return packages
            except json.JSONDecodeError as e:
                log(f"❌ 解析pip包JSON失败: {str(e)}", "WARNING")
        
        # 回退方法：使用标准格式
        log("🔄 使用标准格式作为备选方法", "DEBUG")
        result = run_command([python_exe, "-m", "pip", "list"], timeout_sec=10)
        if result.ok:
            lines = result.stdout.splitlines()
            for line in lines[2:]:  # 跳过头部
                parts = line.strip().split()
                if len(parts) >= 2:
                    packages.append({
                        'name': parts[0],
                        'version': parts[1]
                    })
            log(f"✅ pip包列表获取成功（标准格式），共 {len(packages)} 个包", "DEBUG")
        else:
            log(f"❌ pip list 命令失败: {result.stderr}", "ERROR")
        
        return packages
    except Exception as e:
        log(f"❌ 获取标准包列表失败: {python_exe} - {str(e)}", "ERROR")
        return []


# ==================== uv 工具检测和安装 ====================

@dual_mode
def get_comprehensive_uv_info(python_path: str = None, packages: List[Dict[str, str]] = None) -> Dict[str, Any]:
    """获取全面的 uv 安装信息 - 检测 pip 和 pipx 两种安装方式"""
    log("🔍 获取uv工具安装信息", "DEBUG")
    
    info = {
        'installed': False,
        'version': None,
        'installed_via': None,
        'pip_installed': False,
        'pipx_installed': False,
        'global_available': False
    }
    
    # 1. 检查当前 Python 环境中是否安装了 uv（pip 方式）
    if packages:
        log("📦 检查当前环境中的uv包", "DEBUG")
        for pkg in packages:
            pkg_name = pkg.get('name', '').lower()
            if pkg_name == 'uv':
                info['pip_installed'] = True
                info['installed'] = True
                info['version'] = pkg.get('version', '未知')
                info['installed_via'] = 'pip'
                log(f"✅ 通过pip找到uv: v{info['version']}", "DEBUG")
                break
    
    # 2. 检查全局是否有 uv 命令可用（pipx 方式或其他全局安装）
    log("🌍 检查全局uv命令", "DEBUG")
    uv_global_path = which_tool("uv")
    if uv_global_path:
        info['global_available'] = True
        log(f"✅ 找到全局uv命令: {uv_global_path}", "DEBUG")
        
        # 获取全局 uv 版本
        global_version_result = run_command("uv --version")
        if global_version_result.ok:
            # 解析版本信息，格式通常为 "uv 0.4.18" 或 "uv 0.4.18 (xxxx)"
            version_match = re.search(r'uv (\d+\.\d+\.\d+)', global_version_result.stdout)
            if version_match:
                global_version = version_match.group(1)
                log(f"📊 全局uv版本: {global_version}", "DEBUG")
                
                # 如果当前环境没有 uv，则使用全局版本
                if not info['pip_installed']:
                    info['installed'] = True
                    info['version'] = global_version
                    info['installed_via'] = _detect_uv_install_method(uv_global_path)
                    log(f"🔧 使用全局uv，安装方式: {info['installed_via']}", "DEBUG")
    
    # 3. 具体检查 pipx 安装的 uv
    log("🔍 检查pipx是否安装了uv", "DEBUG")
    pipx_installed, pipx_cmd = is_pipx_installed()
    if pipx_installed:
        log(f"✅ pipx可用: {pipx_cmd}", "DEBUG")
        # 检查 pipx 是否安装了 uv
        try:
            if pipx_cmd and "python" in pipx_cmd:
                # 使用 python -m pipx
                pipx_list_result = run_command([sys.executable, "-m", "pipx", "list", "--short"])
            else:
                # 使用 pipx 命令
                pipx_list_result = run_command(["pipx", "list", "--short"])
            
            if pipx_list_result.ok and 'uv' in pipx_list_result.stdout:
                info['pipx_installed'] = True
                log("✅ pipx中安装了uv", "DEBUG")
                
                # 如果当前环境没有 pip 安装的 uv，标记为 pipx 安装
                if not info['pip_installed']:
                    info['installed'] = True
                    info['installed_via'] = 'pipx'
        except Exception as e:
            log(f"❌ 检查pipx uv安装时出错: {str(e)}", "WARNING")
    else:
        log("❌ pipx未安装或不可用", "DEBUG")
    
    status_summary = f"安装状态: {'已安装' if info['installed'] else '未安装'}"
    if info['installed']:
        status_summary += f", 版本: {info['version']}, 方式: {info['installed_via']}"
    log(f"📊 uv工具状态总结: {status_summary}", "INFO")
    
    return info


def _detect_uv_install_method(uv_path: str) -> str:
    """检测 uv 的安装方式"""
    log(f"🔍 检测uv安装方式: {uv_path}", "DEBUG")
    
    try:
        uv_path_lower = uv_path.lower()
        
        # pipx 安装通常在特定路径
        if '.local/bin' in uv_path_lower or 'pipx' in uv_path_lower:
            log("🔧 检测到pipx安装方式（Unix路径）", "DEBUG")
            return 'pipx'
        
        # Windows pipx 路径
        if 'appdata' in uv_path_lower and 'roaming' in uv_path_lower:
            log("🔧 检测到pipx安装方式（Windows路径）", "DEBUG")
            return 'pipx'
        
        # 检查是否在 Python 环境的 Scripts 或 bin 目录中
        python_scripts_paths = [
            sys.prefix + '/Scripts',  # Windows Python 环境
            sys.prefix + '/bin',      # Unix Python 环境  
            sys.base_prefix + '/Scripts',
            sys.base_prefix + '/bin'
        ]
        
        for scripts_path in python_scripts_paths:
            scripts_normalized = scripts_path.lower().replace('\\', '/')
            uv_normalized = uv_path_lower.replace('\\', '/')
            if scripts_normalized in uv_normalized:
                log("🔧 检测到pip安装方式", "DEBUG")
                return 'pip'
        
        # 默认推测为 pipx（如果在全局路径中）
        log("🔧 默认推测为pipx安装方式", "DEBUG")
        return 'pipx'
        
    except Exception as e:
        log(f"❌ 检测uv安装方式失败: {str(e)}", "WARNING")
        return '未知'


# 更新原有函数以保持兼容性
def get_uv_info_from_packages(packages: List[Dict[str, str]]) -> Dict[str, Any]:
    """从包列表中获取 uv 信息 - 兼容性函数，现在调用新的全面检测"""
    return get_comprehensive_uv_info(packages=packages)


@dual_mode
def is_pipx_installed() -> Tuple[bool, Optional[str]]:
    """检查 pipx 是否安装"""
    log("🔍 检查pipx安装状态", "DEBUG")
    
    pipx_path = which_tool("pipx")
    if pipx_path:
        log(f"✅ 找到pipx命令: {pipx_path}", "DEBUG")
        return True, pipx_path
    
    # 检查是否通过 python -m pipx 可用
    result = run_command([sys.executable, "-m", "pipx", "--version"])
    if result.ok:
        log(f"✅ pipx通过python模块可用", "DEBUG")
        return True, f"{sys.executable} -m pipx"
    
    log("❌ pipx未安装或不可用", "DEBUG")
    return False, None


@dual_mode
def install_pipx() -> CommandResult:
    """安装 pipx"""
    log("📥 安装pipx", "INFO")
    
    # 先尝试用 pip 安装 pipx
    result = run_command([sys.executable, "-m", "pip", "install", "pipx"])
    
    if result.ok:
        log("✅ pipx安装成功", "INFO")
    else:
        log(f"❌ pipx安装失败: {result.stderr}", "ERROR")
    
    return result


@dual_mode
def install_uv_with_pip(python_path: str = None) -> CommandResult:
    """使用 pip 安装 uv"""
    python_exe = python_path or sys.executable
    log(f"📥 使用pip安装uv: {python_exe}", "INFO")
    
    # 检查pip可用性
    if not is_pip_available(python_exe):
        log(f"❌ pip不可用，无法安装uv: {python_exe}", "ERROR")
        return CommandResult(
            ok=False,
            stdout="",
            stderr="pip 不可用，无法安装 uv"
        )
    
    result = run_command([python_exe, "-m", "pip", "install", "uv"], timeout_sec=60)
    
    if result.ok:
        log("✅ uv通过pip安装成功", "INFO")
    else:
        log(f"❌ uv通过pip安装失败: {result.stderr}", "ERROR")
    
    return result


@dual_mode
def install_uv_with_pipx() -> CommandResult:
    """使用 pipx 安装 uv"""
    log("📥 使用pipx安装uv", "INFO")
    
    pipx_installed, pipx_cmd = is_pipx_installed()
    
    if not pipx_installed:
        # 尝试安装 pipx
        log("🔧 pipx未安装，尝试自动安装...", "INFO")
        install_result = install_pipx()
        if not install_result.ok:
            log(f"❌ 无法安装pipx: {install_result.stderr}", "ERROR")
            return CommandResult(
                ok=False,
                stdout="",
                stderr=f"无法安装 pipx: {install_result.stderr}"
            )
        
        # 重新检查 pipx
        pipx_installed, pipx_cmd = is_pipx_installed()
        if not pipx_installed:
            log("❌ pipx安装后仍然不可用", "ERROR")
            return CommandResult(
                ok=False,
                stdout="",
                stderr="pipx 安装后仍然不可用"
            )
    
    # 使用 pipx 安装 uv
    if pipx_cmd.endswith("-m pipx"):
        log("🔧 使用python -m pipx安装uv", "DEBUG")
        result = run_command([sys.executable, "-m", "pipx", "install", "uv"])
    else:
        log("🔧 使用pipx命令安装uv", "DEBUG")
        result = run_command(["pipx", "install", "uv"])
    
    if result.ok:
        log("✅ uv通过pipx安装成功", "INFO")
    else:
        log(f"❌ uv通过pipx安装失败: {result.stderr}", "ERROR")
    
    return result


@dual_mode
def uninstall_uv_package(python_path: str = None) -> CommandResult:
    """卸载 uv（智能检测安装方式）"""
    python_exe = python_path or sys.executable
    log(f"🗑️ 卸载uv: {python_exe}", "INFO")
    
    # 先尝试用 pip 卸载（从指定环境）
    if is_pip_available(python_exe):
        log("🔧 尝试用pip卸载uv", "DEBUG")
        pip_result = run_command([python_exe, "-m", "pip", "uninstall", "uv", "-y"], timeout_sec=30)
        if pip_result.ok:
            log("✅ uv通过pip卸载成功", "INFO")
            return pip_result
        log("⚠️ pip卸载失败，尝试pipx卸载", "WARNING")
    else:
        log("⚠️ pip不可用，直接尝试pipx卸载", "WARNING")
        pip_result = CommandResult(False, "", "pip 不可用")
    
    # 如果 pip 失败，尝试用 pipx 卸载（全局）
    pipx_installed, pipx_cmd = is_pipx_installed()
    if pipx_installed:
        if pipx_cmd.endswith("-m pipx"):
            log("🔧 使用python -m pipx卸载uv", "DEBUG")
            result = run_command([sys.executable, "-m", "pipx", "uninstall", "uv"], timeout_sec=30)
        else:
            log("🔧 使用pipx命令卸载uv", "DEBUG")
            result = run_command(["pipx", "uninstall", "uv"], timeout_sec=30)
        
        if result.ok:
            log("✅ uv通过pipx卸载成功", "INFO")
        else:
            log(f"❌ uv通过pipx卸载失败: {result.stderr}", "ERROR")
        
        return result
    
    log("❌ 所有卸载方式都失败", "ERROR")
    return pip_result  # 返回原始的 pip 错误


# ==================== pip 镜像源管理 ====================

# 常用的 pip 镜像源
PIP_MIRRORS = {
    "官方源": "https://pypi.org/simple/",
    "清华大学": "https://pypi.tuna.tsinghua.edu.cn/simple/",
    "阿里云": "https://mirrors.aliyun.com/pypi/simple/",
    "豆瓣": "https://pypi.douban.com/simple/",
    "中科大": "https://pypi.mirrors.ustc.edu.cn/simple/",
    "华为云": "https://mirrors.huaweicloud.com/repository/pypi/simple/",
    "腾讯云": "https://mirrors.cloud.tencent.com/pypi/simple/",
    "网易": "https://mirrors.163.com/pypi/simple/"
}


@dual_mode
def get_pip_config(python_path: str = None) -> Dict[str, Any]:
    """获取 pip 配置信息"""
    python_exe = python_path or sys.executable
    log(f"🔧 获取pip配置信息: {python_exe}", "DEBUG")
    
    config_info = {
        'index_url': None,
        'trusted_hosts': [],
        'config_file': None,
        'current_mirror': '官方源'
    }
    
    # 检查pip可用性
    if not is_pip_available(python_exe):
        log(f"❌ pip不可用，返回默认配置: {python_exe}", "WARNING")
        return config_info
    
    try:
        # 获取 pip 配置
        log("📋 读取pip配置列表", "DEBUG")
        result = run_command([python_exe, "-m", "pip", "config", "list"], timeout_sec=5)
        if result.ok:
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.startswith('global.index-url='):
                    config_info['index_url'] = line.split('=', 1)[1]
                    log(f"🔗 找到index-url: {config_info['index_url']}", "DEBUG")
                elif line.startswith('global.trusted-host='):
                    trusted_host = line.split('=', 1)[1]
                    config_info['trusted_hosts'].append(trusted_host)
                    log(f"🔒 找到trusted-host: {trusted_host}", "DEBUG")
        else:
            log(f"⚠️ 获取pip配置列表失败: {result.stderr}", "WARNING")
        
        # 判断当前使用的镜像源
        if config_info['index_url']:
            # 清理URL格式，移除引号和空格，统一末尾斜杠
            current_url = config_info['index_url'].strip().strip("'\"").rstrip('/').lower()
            log(f"🔍 分析当前镜像源URL: {current_url}", "DEBUG")
            
            found_mirror = False
            for name, url in PIP_MIRRORS.items():
                # 清理预定义URL格式
                clean_url = url.strip().rstrip('/').lower()
                
                # 支持 http 和 https 的灵活匹配
                if current_url == clean_url or \
                   current_url.replace('https://', 'http://') == clean_url.replace('https://', 'http://') or \
                   current_url.replace('http://', 'https://') == clean_url.replace('http://', 'https://'):
                    config_info['current_mirror'] = name
                    found_mirror = True
                    log(f"✅ 匹配到镜像源: {name}", "DEBUG")
                    break
            
            if not found_mirror:
                config_info['current_mirror'] = '自定义源'
                log(f"⚠️ 未匹配到已知镜像源，当前URL: {current_url}", "DEBUG")
        else:
            log("ℹ️ 未设置自定义index-url，使用官方源", "DEBUG")
        
        log(f"✅ pip配置信息获取完成，当前镜像源: {config_info['current_mirror']}", "DEBUG")
        return config_info
    
    except Exception as e:
        log(f"❌ 获取pip配置失败: {str(e)}", "ERROR")
        return config_info


@dual_mode
def set_pip_mirror(mirror_name: str, python_path: str = None) -> CommandResult:
    """设置 pip 镜像源"""
    python_exe = python_path or sys.executable
    log(f"🔧 设置pip镜像源: {mirror_name} (Python: {python_exe})", "INFO")
    
    # 检查pip可用性
    if not is_pip_available(python_exe):
        log(f"❌ pip不可用，无法设置镜像源: {python_exe}", "ERROR")
        return CommandResult(
            ok=False,
            stdout="",
            stderr="pip 不可用，无法设置镜像源"
        )
    
    if mirror_name not in PIP_MIRRORS:
        log(f"❌ 不支持的镜像源: {mirror_name}", "ERROR")
        return CommandResult(
            ok=False,
            stdout="",
            stderr=f"不支持的镜像源: {mirror_name}"
        )
    
    mirror_url = PIP_MIRRORS[mirror_name]
    log(f"🔗 镜像源URL: {mirror_url}", "DEBUG")
    
    try:
        # 设置 index-url
        log("🔧 设置index-url配置", "DEBUG")
        result = run_command([python_exe, "-m", "pip", "config", "set", "global.index-url", mirror_url], timeout_sec=10)
        if not result.ok:
            log(f"❌ 设置index-url失败: {result.stderr}", "ERROR")
            return result
        
        # 如果是国内源，添加可信任主机
        if mirror_name != "官方源":
            log("🔒 为国内源添加trusted-host", "DEBUG")
            from urllib.parse import urlparse
            parsed = urlparse(mirror_url)
            hostname = parsed.hostname
            
            if hostname:
                log(f"🔒 添加trusted-host: {hostname}", "DEBUG")
                trust_result = run_command([
                    python_exe, "-m", "pip", "config", "set",
                    "global.trusted-host", hostname
                ], timeout_sec=5)
                # 信任主机失败不影响主要功能
                if not trust_result.ok:
                    log(f"⚠️ 设置信任主机失败: {trust_result.stderr}", "WARNING")
            else:
                log("⚠️ 无法解析镜像源主机名", "WARNING")
        
        log(f"✅ pip镜像源设置完成: {mirror_name}", "INFO")
        return CommandResult(
            ok=True,
            stdout=f"已设置 pip 镜像源为: {mirror_name} ({mirror_url})",
            stderr=""
        )
    
    except Exception as e:
        log(f"❌ 设置镜像源异常: {str(e)}", "ERROR")
        return CommandResult(
            ok=False,
            stdout="",
            stderr=f"设置镜像源失败: {str(e)}"
        )


@dual_mode
def reset_pip_mirror(python_path: str = None) -> CommandResult:
    """重置 pip 镜像源到官方源"""
    python_exe = python_path or sys.executable
    log(f"🔄 重置pip镜像源到官方源: {python_exe}", "INFO")
    
    # 检查pip可用性
    if not is_pip_available(python_exe):
        log(f"❌ pip不可用，无法重置镜像源: {python_exe}", "ERROR")
        return CommandResult(
            ok=False,
            stdout="",
            stderr="pip 不可用，无法重置镜像源"
        )
    
    try:
        # 移除 index-url 配置
        log("🗑️ 移除index-url配置", "DEBUG")
        result1 = run_command([python_exe, "-m", "pip", "config", "unset", "global.index-url"], timeout_sec=5)
        
        # 移除 trusted-host 配置
        log("🗑️ 移除trusted-host配置", "DEBUG")
        result2 = run_command([python_exe, "-m", "pip", "config", "unset", "global.trusted-host"], timeout_sec=5)
        
        # 即使部分命令失败也认为重置成功
        log("✅ pip镜像源重置完成", "INFO")
        return CommandResult(
            ok=True,
            stdout="已重置 pip 镜像源为官方源",
            stderr=""
        )
    
    except Exception as e:
        log(f"❌ 重置镜像源异常: {str(e)}", "ERROR")
        return CommandResult(
            ok=False,
            stdout="",
            stderr=f"重置镜像源失败: {str(e)}"
        )


# ==================== 包管理 ====================

@dual_mode
def export_requirements(output_file: str = "requirements.txt", python_path: str = None) -> CommandResult:
    """导出 requirements.txt"""
    python_exe = python_path or sys.executable
    log(f"📦 导出requirements.txt: {output_file} (Python: {python_exe})", "INFO")
    
    # 检查pip可用性
    if not is_pip_available(python_exe):
        log(f"❌ pip不可用，无法导出requirements.txt: {python_exe}", "ERROR")
        return CommandResult(
            ok=False,
            stdout="",
            stderr="pip 不可用，无法导出 requirements.txt"
        )
    
    cmd = [python_exe, "-m", "pip", "freeze"]
    
    result = run_command(cmd, timeout_sec=15)
    if result.ok:
        try:
            Path(output_file).write_text(result.stdout)
            log(f"✅ requirements.txt导出成功: {output_file}", "INFO")
            return CommandResult(ok=True, stdout=f"已导出到 {output_file}", stderr="")
        except Exception as e:
            log(f"❌ 写入requirements.txt失败: {str(e)}", "ERROR")
            return CommandResult(ok=False, stdout="", stderr=str(e))
    else:
        log(f"❌ pip freeze命令失败: {result.stderr}", "ERROR")
    
    return result


# ==================== 综合状态检查 ====================

@dual_mode
def get_python_status() -> Dict[str, Any]:
    """获取完整的 Python 环境状态"""
    log("📊 获取完整Python环境状态", "INFO")
    
    status = {
        'system_info': {
            'system': platform.system(),
            'architecture': platform.architecture()[0], 
            'python_version': platform.python_version()
        },
        'current_python': get_python_info(),
        'python_installations': find_python_installations(),
        'conda_info': {
            'installed': False,
            'version': None,
            'environments': []
        }
    }
    
    log(f"🖥️ 系统信息: {status['system_info']['system']} {status['system_info']['architecture']}", "DEBUG")
    
    # Conda 信息
    log("🐍 获取Conda信息", "DEBUG")
    conda_installed, conda_path, conda_version = is_conda_installed()
    status['conda_info'] = {
        'installed': conda_installed,
        'path': conda_path,
        'version': conda_version,
        'environments': list_conda_environments() if conda_installed else []
    }
    
    if conda_installed:
        env_count = len(status['conda_info']['environments'])
        log(f"✅ Conda信息获取完成，共 {env_count} 个环境", "DEBUG")
    else:
        log("ℹ️ 系统未安装Conda", "DEBUG")
    
    log("✅ Python环境状态获取完成", "INFO")
    return status
