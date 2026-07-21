"""
统一的路径管理接口 - 兼容版
"""

import os
import shutil
import platform
from typing import List
from core.async_helper import AsyncHelper, dual_mode
from core.log import log


class PathManager:
    """统一的路径管理器"""

    # ==================== 路径操作 ====================

    @staticmethod
    def expand(path: str) -> str:
        """展开用户路径和环境变量"""
        try:
            expanded = os.path.expanduser(os.path.expandvars(path))
            log(f"🔄 路径展开: {path} -> {expanded}", "DEBUG")
            return expanded
        except Exception as e:
            log(f"❌ 路径展开失败: {path} - {str(e)}", "ERROR")
            return path

    @staticmethod
    def join(*paths: str) -> str:
        """拼接路径"""
        return os.path.join(*paths)

    @staticmethod
    def dirname(path: str) -> str:
        """获取目录名"""
        return os.path.dirname(PathManager.expand(path))

    @staticmethod
    def basename(path: str) -> str:
        """获取基础名"""
        return os.path.basename(PathManager.expand(path))

    @staticmethod
    def normalize(path: str) -> str:
        """标准化路径"""
        return os.path.normpath(PathManager.expand(path))

    @staticmethod
    def abspath(path: str) -> str:
        """获取绝对路径"""
        return os.path.abspath(PathManager.expand(path))

    # ==================== 存在性检查 ====================

    @staticmethod
    @dual_mode
    def exists(path: str) -> bool:
        """检查路径是否存在"""
        try:
            expanded_path = PathManager.expand(path)
            exists = os.path.exists(expanded_path)
            log(f"🔍 检查路径存在性: {path} -> {exists}", "DEBUG")
            return exists
        except Exception as e:
            log(f"❌ 检查路径存在性失败: {path} - {str(e)}", "ERROR")
            return False

    @staticmethod
    @dual_mode
    def is_file(path: str) -> bool:
        """检查是否为文件"""
        try:
            expanded_path = PathManager.expand(path)
            is_file = os.path.isfile(expanded_path)
            log(f"🔍 检查文件类型: {path} -> {is_file}", "DEBUG")
            return is_file
        except Exception as e:
            log(f"❌ 检查文件类型失败: {path} - {str(e)}", "ERROR")
            return False

    @staticmethod
    @dual_mode
    def is_dir(path: str) -> bool:
        """检查是否为目录"""
        try:
            expanded_path = PathManager.expand(path)
            is_dir = os.path.isdir(expanded_path)
            log(f"🔍 检查目录类型: {path} -> {is_dir}", "DEBUG")
            return is_dir
        except Exception as e:
            log(f"❌ 检查目录类型失败: {path} - {str(e)}", "ERROR")
            return False

    # ==================== 文件操作 ====================

    @staticmethod
    @dual_mode
    def read_text(path: str, encoding: str = "utf-8") -> str:
        """读取文本文件内容"""
        try:
            expanded_path = PathManager.expand(path)
            log(f"📖 读取文件: {expanded_path}", "DEBUG")
            with open(expanded_path, "r", encoding=encoding) as f:
                content = f.read()
                log(f"✅ 文件读取成功: {expanded_path} ({len(content)} 字符)", "DEBUG")
                return content
        except FileNotFoundError:
            log(f"❌ 文件不存在: {path}", "WARNING")
            return ""
        except PermissionError:
            log(f"❌ 权限不足，无法读取文件: {path}", "ERROR")
            return ""
        except UnicodeDecodeError as e:
            log(f"❌ 文件编码错误: {path} - {str(e)}", "ERROR")
            return ""
        except Exception as e:
            log(f"❌ 读取文件失败: {path} - {str(e)}", "ERROR")
            return ""

    @staticmethod
    @dual_mode
    def write_text(path: str, content: str, encoding: str = "utf-8", create_dirs: bool = True) -> bool:
        """写入文本文件"""
        try:
            expanded_path = PathManager.expand(path)
            log(f"✍️ 写入文件: {expanded_path} ({len(content)} 字符)", "DEBUG")
            
            if create_dirs:
                parent_dir = os.path.dirname(expanded_path)
                if parent_dir and not os.path.exists(parent_dir):
                    os.makedirs(parent_dir, exist_ok=True)
                    log(f"📁 创建目录: {parent_dir}", "DEBUG")

            with open(expanded_path, "w", encoding=encoding) as f:
                f.write(content)
            log(f"✅ 文件写入成功: {expanded_path}", "DEBUG")
            return True
        except PermissionError:
            log(f"❌ 权限不足，无法写入文件: {path}", "ERROR")
            return False
        except OSError as e:
            log(f"❌ 文件写入失败: {path} - {str(e)}", "ERROR")
            return False
        except Exception as e:
            log(f"❌ 写入文件异常: {path} - {str(e)}", "ERROR")
            return False

    @staticmethod
    @dual_mode
    def append_text(path: str, content: str, encoding: str = "utf-8", create_dirs: bool = True) -> bool:
        """追加文本到文件"""
        try:
            expanded_path = PathManager.expand(path)
            log(f"➕ 追加到文件: {expanded_path} ({len(content)} 字符)", "DEBUG")
            
            if create_dirs:
                parent_dir = os.path.dirname(expanded_path)
                if parent_dir and not os.path.exists(parent_dir):
                    os.makedirs(parent_dir, exist_ok=True)
                    log(f"📁 创建目录: {parent_dir}", "DEBUG")

            with open(expanded_path, "a", encoding=encoding) as f:
                f.write(content)
            log(f"✅ 文件追加成功: {expanded_path}", "DEBUG")
            return True
        except PermissionError:
            log(f"❌ 权限不足，无法追加到文件: {path}", "ERROR")
            return False
        except OSError as e:
            log(f"❌ 文件追加失败: {path} - {str(e)}", "ERROR")
            return False
        except Exception as e:
            log(f"❌ 追加文件异常: {path} - {str(e)}", "ERROR")
            return False

    # ==================== 文件复制和移动 ====================

    @staticmethod
    @dual_mode
    def copy_file(src: str, dst: str, create_dirs: bool = True) -> bool:
        """复制文件"""
        try:
            src_path = PathManager.expand(src)
            dst_path = PathManager.expand(dst)
            log(f"📋 复制文件: {src_path} -> {dst_path}", "DEBUG")

            if not os.path.exists(src_path):
                log(f"❌ 源文件不存在: {src_path}", "ERROR")
                return False

            if create_dirs:
                parent_dir = os.path.dirname(dst_path)
                if parent_dir and not os.path.exists(parent_dir):
                    os.makedirs(parent_dir, exist_ok=True)
                    log(f"📁 创建目标目录: {parent_dir}", "DEBUG")

            shutil.copy2(src_path, dst_path)
            log(f"✅ 文件复制成功: {src_path} -> {dst_path}", "DEBUG")
            return True
        except PermissionError:
            log(f"❌ 权限不足，无法复制文件: {src} -> {dst}", "ERROR")
            return False
        except OSError as e:
            log(f"❌ 文件复制失败: {src} -> {dst} - {str(e)}", "ERROR")
            return False
        except Exception as e:
            log(f"❌ 复制文件异常: {src} -> {dst} - {str(e)}", "ERROR")
            return False

    @staticmethod
    @dual_mode
    def move_file(src: str, dst: str, create_dirs: bool = True) -> bool:
        """移动文件"""
        try:
            src_path = PathManager.expand(src)
            dst_path = PathManager.expand(dst)
            log(f"🚚 移动文件: {src_path} -> {dst_path}", "DEBUG")

            if not os.path.exists(src_path):
                log(f"❌ 源文件不存在: {src_path}", "ERROR")
                return False

            if create_dirs:
                parent_dir = os.path.dirname(dst_path)
                if parent_dir and not os.path.exists(parent_dir):
                    os.makedirs(parent_dir, exist_ok=True)
                    log(f"📁 创建目标目录: {parent_dir}", "DEBUG")

            shutil.move(src_path, dst_path)
            log(f"✅ 文件移动成功: {src_path} -> {dst_path}", "DEBUG")
            return True
        except PermissionError:
            log(f"❌ 权限不足，无法移动文件: {src} -> {dst}", "ERROR")
            return False
        except OSError as e:
            log(f"❌ 文件移动失败: {src} -> {dst} - {str(e)}", "ERROR")
            return False
        except Exception as e:
            log(f"❌ 移动文件异常: {src} -> {dst} - {str(e)}", "ERROR")
            return False

    # ==================== 目录操作 ====================

    @staticmethod
    @dual_mode
    def make_dirs(path: str, exist_ok: bool = True) -> bool:
        """创建目录"""
        try:
            expanded_path = PathManager.expand(path)
            log(f"📁 创建目录: {expanded_path}", "DEBUG")
            os.makedirs(expanded_path, exist_ok=exist_ok)
            log(f"✅ 目录创建成功: {expanded_path}", "DEBUG")
            return True
        except PermissionError:
            log(f"❌ 权限不足，无法创建目录: {path}", "ERROR")
            return False
        except OSError as e:
            log(f"❌ 目录创建失败: {path} - {str(e)}", "ERROR")
            return False
        except Exception as e:
            log(f"❌ 创建目录异常: {path} - {str(e)}", "ERROR")
            return False

    @staticmethod
    @dual_mode
    def remove_file(path: str) -> bool:
        """删除文件"""
        try:
            expanded_path = PathManager.expand(path)
            if os.path.isfile(expanded_path):
                log(f"🗑️ 删除文件: {expanded_path}", "DEBUG")
                os.remove(expanded_path)
                log(f"✅ 文件删除成功: {expanded_path}", "DEBUG")
                return True
            else:
                log(f"⚠️ 文件不存在，无需删除: {expanded_path}", "WARNING")
                return False
        except PermissionError:
            log(f"❌ 权限不足，无法删除文件: {path}", "ERROR")
            return False
        except OSError as e:
            log(f"❌ 文件删除失败: {path} - {str(e)}", "ERROR")
            return False
        except Exception as e:
            log(f"❌ 删除文件异常: {path} - {str(e)}", "ERROR")
            return False

    @staticmethod
    @dual_mode
    def remove_tree(path: str) -> bool:
        """删除目录树"""
        try:
            expanded_path = PathManager.expand(path)
            if os.path.isdir(expanded_path):
                log(f"🗑️ 删除目录树: {expanded_path}", "DEBUG")
                shutil.rmtree(expanded_path)
                log(f"✅ 目录树删除成功: {expanded_path}", "DEBUG")
                return True
            else:
                log(f"⚠️ 目录不存在，无需删除: {expanded_path}", "WARNING")
                return False
        except PermissionError:
            log(f"❌ 权限不足，无法删除目录: {path}", "ERROR")
            return False
        except OSError as e:
            log(f"❌ 目录删除失败: {path} - {str(e)}", "ERROR")
            return False
        except Exception as e:
            log(f"❌ 删除目录异常: {path} - {str(e)}", "ERROR")
            return False

    @staticmethod
    @dual_mode
    def list_dir(path: str, only_dirs: bool = False, only_files: bool = False) -> List[str]:
        """列出目录内容"""
        try:
            expanded_path = PathManager.expand(path)
            log(f"📂 列出目录内容: {expanded_path}", "DEBUG")
            
            if not os.path.isdir(expanded_path):
                log(f"❌ 不是有效目录: {expanded_path}", "WARNING")
                return []

            items = []
            for name in os.listdir(expanded_path):
                full_path = os.path.join(expanded_path, name)
                if only_dirs and not os.path.isdir(full_path):
                    continue
                if only_files and not os.path.isfile(full_path):
                    continue
                items.append(name)

            log(f"✅ 目录内容列出成功: {expanded_path} ({len(items)} 项)", "DEBUG")
            return sorted(items)
        except PermissionError:
            log(f"❌ 权限不足，无法列出目录: {path}", "ERROR")
            return []
        except OSError as e:
            log(f"❌ 列出目录失败: {path} - {str(e)}", "ERROR")
            return []
        except Exception as e:
            log(f"❌ 列出目录异常: {path} - {str(e)}", "ERROR")
            return []

    # ==================== 环境变量和系统信息 ====================

    @staticmethod
    def get_home_dir() -> str:
        """获取用户主目录"""
        return os.path.expanduser("~")

    @staticmethod
    def get_username() -> str:
        """获取当前用户名"""
        return os.getenv("USERNAME") or os.getenv("USER") or "unknown"

    @staticmethod
    def get_env(key: str, default: str = "") -> str:
        """获取环境变量"""
        return os.getenv(key, default)

    @staticmethod
    def get_shell() -> str:
        """获取当前 shell"""
        return os.getenv("SHELL", "unknown")

    @staticmethod
    @dual_mode
    def get_system_info() -> dict:
        """获取系统信息"""
        try:
            log("🔍 获取系统信息", "DEBUG")
            info = {
                "system": platform.system(),
                "platform": platform.platform(),
                "architecture": platform.architecture()[0],
                "python_version": platform.python_version(),
                "user": PathManager.get_username(),
                "home": PathManager.get_home_dir(),
                "shell": PathManager.get_shell(),
            }
            log(f"✅ 系统信息获取成功: {info['system']} {info['architecture']}", "DEBUG")
            return info
        except Exception as e:
            log(f"❌ 获取系统信息失败: {str(e)}", "ERROR")
            return {}

    # ==================== 特殊路径操作 ====================

    @staticmethod
    def create_backup_filename(original_path: str, suffix: str = None) -> str:
        """创建备份文件名"""
        if suffix is None:
            import time
            suffix = f"backup.{int(time.time())}"

        expanded_path = PathManager.expand(original_path)
        return f"{expanded_path}.{suffix}"

    @staticmethod
    @dual_mode
    def ensure_dir_exists(path: str) -> bool:
        """确保目录存在，如果不存在则创建"""
        try:
            expanded_path = PathManager.expand(path)
            if PathManager.is_dir(expanded_path):
                log(f"✅ 目录已存在: {expanded_path}", "DEBUG")
                return True
            
            result = PathManager.make_dirs(expanded_path)
            if result:
                log(f"✅ 目录创建成功: {expanded_path}", "DEBUG")
            else:
                log(f"❌ 目录创建失败: {expanded_path}", "ERROR")
            return result
        except Exception as e:
            log(f"❌ 确保目录存在异常: {path} - {str(e)}", "ERROR")
            return False


# ==================== 便捷函数导出 ====================
# 路径操作便捷函数
expand = PathManager.expand
join = PathManager.join
dirname = PathManager.dirname
basename = PathManager.basename
normalize = PathManager.normalize
abspath = PathManager.abspath

# 存在性检查便捷函数
exists = PathManager.exists
exists_async = PathManager.exists.async_version
is_file = PathManager.is_file
is_file_async = PathManager.is_file.async_version
is_dir = PathManager.is_dir
is_dir_async = PathManager.is_dir.async_version

# 文件操作便捷函数
read_text = PathManager.read_text
read_text_async = PathManager.read_text.async_version
write_text = PathManager.write_text
write_text_async = PathManager.write_text.async_version
append_text = PathManager.append_text
append_text_async = PathManager.append_text.async_version

# 文件复制移动便捷函数
copy_file = PathManager.copy_file
copy_file_async = PathManager.copy_file.async_version
move_file = PathManager.move_file
move_file_async = PathManager.move_file.async_version

# 目录操作便捷函数
make_dirs = PathManager.make_dirs
make_dirs_async = PathManager.make_dirs.async_version
remove_file = PathManager.remove_file
remove_file_async = PathManager.remove_file.async_version
remove_tree = PathManager.remove_tree
remove_tree_async = PathManager.remove_tree.async_version
list_dir = PathManager.list_dir
list_dir_async = PathManager.list_dir.async_version

# 环境信息便捷函数
get_home_dir = PathManager.get_home_dir
get_home_dir_async = lambda: AsyncHelper.run_in_executor(PathManager.get_home_dir)
get_username = PathManager.get_username
get_username_async = lambda: AsyncHelper.run_in_executor(PathManager.get_username)
get_env = PathManager.get_env
get_env_async = lambda key, default="": AsyncHelper.run_in_executor(PathManager.get_env, key, default)
get_shell = PathManager.get_shell
get_system_info = PathManager.get_system_info
get_system_info_async = PathManager.get_system_info.async_version

# 特殊操作便捷函数
create_backup_filename = PathManager.create_backup_filename
ensure_dir_exists = PathManager.ensure_dir_exists
ensure_dir_exists_async = PathManager.ensure_dir_exists.async_version


_PROJECT_ROOT: str = ""
_MARKERS = ("launch.py", "pyproject.toml", ".git")


def project_root() -> str:
    """获取项目根目录绝对路径（首次调用自动探测并缓存）。

    探测策略：从 cwd 和本文件位置向上搜索标志文件（launch.py / pyproject.toml / .git）。
    找不到时回退为 cwd。
    """
    global _PROJECT_ROOT
    if _PROJECT_ROOT:
        return _PROJECT_ROOT

    from pathlib import Path

    for start in (Path.cwd(), Path(__file__).resolve().parent.parent):
        current = start.resolve()
        for _ in range(10):
            if any((current / m).exists() for m in _MARKERS):
                _PROJECT_ROOT = str(current)
                return _PROJECT_ROOT
            parent = current.parent
            if parent == current:
                break
            current = parent

    _PROJECT_ROOT = str(Path.cwd())
    return _PROJECT_ROOT


class ConfigPaths:
    """配置路径常量集中管理。"""
    APP_CONFIG = "config/app_config.json"
    WEBUI_CONFIG = "config/webui.json"
    MIND_CONFIG = "config/mind_config.json"
    LLM_CLIENTS = "config/llm_clients.json"
    MCP_SERVERS = "config/mcp_servers.json"
    HEARTBEAT_CONFIG = "config/heartbeat.json"
    REMINDERS = "config/reminders.json"
    INTROSPECTION_CONFIG = "config/introspection.json"
    INTROSPECTION_DIR = "config/introspection"
    TASKS_DIR = "config/tasks"
    CUSTOM_TAGS = "config/tags.json"
    PERSONAS_DIR = "config/personas"
    PERSONAS_INDEX = "config/personas/index.json"
    MEMORY_DIR = "config/memory"
    COGNEE_CONFIG = "config/cognee.json"
    COGNEE_DATA_DIR = "config/memory/cognee"
    HEARTBEAT_LOG = "config/memory/heartbeat.md"
    SQLITE_DB = "config/memory/data/agent.sqlite3"
    UPLOAD_DIR = "workspace/uploads"
