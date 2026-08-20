"""统一的路径管理接口"""

import os
import platform
import shutil
import time
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

from core.async_helper import dual_mode
from core.log import log

F = TypeVar("F", bound=Callable[..., Any])

# 项目根目录探测时向上查找的最大层数
_PROJECT_ROOT_PROBE_DEPTH = 10


def _format_paths(args: Tuple[Any, ...]) -> str:
    """将位置参数格式化为路径描述（用于错误日志）。"""
    return " -> ".join(str(a) for a in args)


def _path_op(action: str, default: Any) -> Callable[[F], F]:
    """路径操作统一错误处理装饰器。

    按异常类型记录日志并返回默认值：
    - FileNotFoundError → WARNING（目标路径不存在）
    - PermissionError → ERROR（权限不足）
    - OSError / 其他异常 → ERROR
    """
    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except FileNotFoundError:
                log(f"❌ {action}失败，路径不存在: {_format_paths(args)}", "WARNING")
                return default
            except PermissionError:
                log(f"❌ 权限不足，{action}失败: {_format_paths(args)}", "ERROR")
                return default
            except OSError as e:
                log(f"❌ {action}失败: {_format_paths(args)} - {e}", "ERROR")
                return default
            except Exception as e:
                log(f"❌ {action}异常: {_format_paths(args)} - {e}", "ERROR")
                return default
        return wrapper  # type: ignore[return-value]
    return decorator


class PathManager:
    """统一的路径管理器"""

    # ==================== 路径操作 ====================

    @staticmethod
    def _expand(path: str) -> str:
        """展开用户路径和环境变量（内部实现，不记日志）。"""
        return os.path.expanduser(os.path.expandvars(path))

    @staticmethod
    def expand(path: str) -> str:
        """展开用户路径和环境变量（公开入口，记一条 DEBUG 日志）"""
        try:
            expanded = PathManager._expand(path)
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
        return os.path.dirname(PathManager._expand(path))

    @staticmethod
    def basename(path: str) -> str:
        """获取基础名"""
        return os.path.basename(PathManager._expand(path))

    @staticmethod
    def normalize(path: str) -> str:
        """标准化路径"""
        return os.path.normpath(PathManager._expand(path))

    @staticmethod
    def abspath(path: str) -> str:
        """获取绝对路径"""
        return os.path.abspath(PathManager._expand(path))

    # ==================== 存在性检查 ====================

    @staticmethod
    @dual_mode
    @_path_op("检查路径存在性", False)
    def exists(path: str) -> bool:
        """检查路径是否存在"""
        expanded_path = PathManager._expand(path)
        exists = os.path.exists(expanded_path)
        log(f"🔍 检查路径存在性: {path} -> {exists}", "DEBUG")
        return exists

    @staticmethod
    @dual_mode
    @_path_op("检查文件类型", False)
    def is_file(path: str) -> bool:
        """检查是否为文件"""
        expanded_path = PathManager._expand(path)
        is_file = os.path.isfile(expanded_path)
        log(f"🔍 检查文件类型: {path} -> {is_file}", "DEBUG")
        return is_file

    @staticmethod
    @dual_mode
    @_path_op("检查目录类型", False)
    def is_dir(path: str) -> bool:
        """检查是否为目录"""
        expanded_path = PathManager._expand(path)
        is_dir = os.path.isdir(expanded_path)
        log(f"🔍 检查目录类型: {path} -> {is_dir}", "DEBUG")
        return is_dir

    # ==================== 文件操作 ====================

    @staticmethod
    @dual_mode
    @_path_op("读取文件", "")
    def read_text(path: str, encoding: str = "utf-8") -> str:
        """读取文本文件内容"""
        expanded_path = PathManager._expand(path)
        log(f"📖 读取文件: {expanded_path}", "DEBUG")
        with open(expanded_path, "r", encoding=encoding) as f:
            content = f.read()
            log(f"✅ 文件读取成功: {expanded_path} ({len(content)} 字符)", "DEBUG")
            return content

    @staticmethod
    @dual_mode
    @_path_op("写入文件", False)
    def write_text(path: str, content: str, encoding: str = "utf-8", create_dirs: bool = True) -> bool:
        """写入文本文件"""
        expanded_path = PathManager._expand(path)
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

    @staticmethod
    @dual_mode
    @_path_op("追加文件", False)
    def append_text(path: str, content: str, encoding: str = "utf-8", create_dirs: bool = True) -> bool:
        """追加文本到文件"""
        expanded_path = PathManager._expand(path)
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

    # ==================== 文件复制和移动 ====================

    @staticmethod
    @dual_mode
    @_path_op("复制文件", False)
    def copy_file(src: str, dst: str, create_dirs: bool = True) -> bool:
        """复制文件"""
        src_path = PathManager._expand(src)
        dst_path = PathManager._expand(dst)
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

    @staticmethod
    @dual_mode
    @_path_op("移动文件", False)
    def move_file(src: str, dst: str, create_dirs: bool = True) -> bool:
        """移动文件"""
        src_path = PathManager._expand(src)
        dst_path = PathManager._expand(dst)
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

    # ==================== 目录操作 ====================

    @staticmethod
    @dual_mode
    @_path_op("创建目录", False)
    def make_dirs(path: str, exist_ok: bool = True) -> bool:
        """创建目录"""
        expanded_path = PathManager._expand(path)
        os.makedirs(expanded_path, exist_ok=exist_ok)
        log(f"✅ 目录创建成功: {expanded_path}", "DEBUG")
        return True

    @staticmethod
    @dual_mode
    @_path_op("删除文件", False)
    def remove_file(path: str) -> bool:
        """删除文件"""
        expanded_path = PathManager._expand(path)
        if os.path.isfile(expanded_path):
            os.remove(expanded_path)
            log(f"✅ 文件删除成功: {expanded_path}", "DEBUG")
            return True
        log(f"⚠️ 文件不存在，无需删除: {expanded_path}", "WARNING")
        return False

    @staticmethod
    @dual_mode
    @_path_op("删除目录树", False)
    def remove_tree(path: str) -> bool:
        """删除目录树"""
        expanded_path = PathManager._expand(path)
        if os.path.isdir(expanded_path):
            shutil.rmtree(expanded_path)
            log(f"✅ 目录树删除成功: {expanded_path}", "DEBUG")
            return True
        log(f"⚠️ 目录不存在，无需删除: {expanded_path}", "WARNING")
        return False

    @staticmethod
    @dual_mode
    @_path_op("列出目录", [])
    def list_dir(path: str, only_dirs: bool = False, only_files: bool = False) -> List[str]:
        """列出目录内容"""
        expanded_path = PathManager._expand(path)
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

    # ==================== 环境变量和系统信息 ====================

    @staticmethod
    @dual_mode
    def get_home_dir() -> str:
        """获取用户主目录"""
        return os.path.expanduser("~")

    @staticmethod
    @dual_mode
    def get_username() -> str:
        """获取当前用户名"""
        return os.getenv("USERNAME") or os.getenv("USER") or "unknown"

    @staticmethod
    @dual_mode
    def get_env(key: str, default: str = "") -> str:
        """获取环境变量"""
        return os.getenv(key, default)

    @staticmethod
    def get_shell() -> str:
        """获取当前 shell"""
        return os.getenv("SHELL", "unknown")

    @staticmethod
    @dual_mode
    @_path_op("获取系统信息", {})
    def get_system_info() -> Dict[str, str]:
        """获取系统信息"""
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

    # ==================== 特殊路径操作 ====================

    @staticmethod
    def create_backup_filename(original_path: str, suffix: Optional[str] = None) -> str:
        """创建备份文件名"""
        if suffix is None:
            suffix = f"backup.{int(time.time())}"

        expanded_path = PathManager._expand(original_path)
        return f"{expanded_path}.{suffix}"

    @staticmethod
    @dual_mode
    @_path_op("确保目录存在", False)
    def ensure_dir_exists(path: str) -> bool:
        """确保目录存在，如果不存在则创建（单次调用只记一条日志）"""
        expanded_path = PathManager._expand(path)
        if os.path.isdir(expanded_path):
            log(f"✅ 目录已存在: {expanded_path}", "DEBUG")
            return True
        os.makedirs(expanded_path, exist_ok=True)
        log(f"✅ 目录创建成功: {expanded_path}", "DEBUG")
        return True


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
get_home_dir_async = PathManager.get_home_dir.async_version
get_username = PathManager.get_username
get_username_async = PathManager.get_username.async_version
get_env = PathManager.get_env
get_env_async = PathManager.get_env.async_version
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

    for start in (Path.cwd(), Path(__file__).resolve().parent.parent):
        current = start.resolve()
        for _ in range(_PROJECT_ROOT_PROBE_DEPTH):
            if any((current / m).exists() for m in _MARKERS):
                _PROJECT_ROOT = str(current)
                return _PROJECT_ROOT
            parent = current.parent
            if parent == current:
                break
            current = parent

    _PROJECT_ROOT = str(Path.cwd())
    return _PROJECT_ROOT


def workspace_root() -> str:
    """获取工作区根目录绝对路径。

    与 entities/filesystem 工具的路径解析基准保持一致：
    读取 workspace_root 配置（默认 "workspace"），相对路径基于进程 cwd 解析。
    """
    from core.config import ConfigManager
    return os.path.abspath(ConfigManager.get("workspace_root", "workspace"))


def config_dir() -> str:
    """获取配置目录（纯 JSON 配置）。

    解析优先级：``ANELF_CONFIG_DIR`` 环境变量 > 默认 "config"。
    """
    return os.environ.get("ANELF_CONFIG_DIR", "config")


def data_dir() -> str:
    """获取数据目录（SQLite / 记忆便签 / cognee 等运行数据）。

    解析优先级：``ANELF_DATA_DIR`` 环境变量 > app_config.json 的 ``data_root``
    > 默认 ``<config_dir>/memory``（与历史布局一致）。
    """
    env = os.environ.get("ANELF_DATA_DIR", "").strip()
    if env:
        return env
    try:
        from core.config import ConfigManager
        root = str(ConfigManager.get("data_root", "") or "").strip()
        if root:
            return root
    except Exception as e:
        log(f"data_root 配置读取失败: {e}", "DEBUG")
    return os.path.join(config_dir(), "memory")


# ConfigPaths 路径规格：name -> (scope, relpath)
# scope: config=配置目录 data=数据目录 literal=字面量（不随目录配置变化）
_PATH_SPECS: Dict[str, Tuple[str, str]] = {
    "APP_CONFIG": ("config", "app_config.json"),
    "WEBUI_CONFIG": ("config", "webui.json"),
    "MIND_CONFIG": ("config", "mind_config.json"),
    "LLM_CLIENTS": ("config", "llm_clients.json"),
    "MCP_SERVERS": ("config", "mcp_servers.json"),
    "HEARTBEAT_CONFIG": ("config", "heartbeat.json"),
    "REMINDERS": ("config", "reminders.json"),
    "INTROSPECTION_CONFIG": ("config", "introspection.json"),
    "INTROSPECTION_DIR": ("config", "introspection"),
    "TASKS_DIR": ("config", "tasks"),
    "CUSTOM_TAGS": ("config", "tags.json"),
    "PERSONAS_DIR": ("config", "personas"),
    "PERSONAS_INDEX": ("config", "personas/index.json"),
    "PERMISSION_RULES": ("config", "permission_rules.json"),
    "HOOKS": ("config", "hooks.json"),
    "APPROVAL_POLICIES": ("config", "approval_policies.json"),
    "DB_CONNECTIONS": ("config", "db_connections.json"),
    "STORAGE_VOLUMES": ("config", "storage_volumes.json"),
    "COGNEE_CONFIG": ("config", "cognee.json"),
    "MEMORY_DIR": ("data", ""),
    "COGNEE_DATA_DIR": ("data", "cognee"),
    "HEARTBEAT_LOG": ("data", "heartbeat.md"),
    "SQLITE_DB": ("data", "data/agent.sqlite3"),
    "NONEBOT_DIR": ("data", "nonebot"),
    "UPLOAD_DIR": ("literal", "workspace/uploads"),
}

# 测试/脚本注入的路径覆盖（monkeypatch.setattr 或直接赋值）
_PATH_OVERRIDES: Dict[str, str] = {}


class _ConfigPathsMeta(type):
    """ConfigPaths 元类：按 _PATH_SPECS 动态解析路径常量。

    读取：优先 _PATH_OVERRIDES，否则基于 config_dir()/data_dir() 动态拼接。
    赋值/删除：写入/清除 _PATH_OVERRIDES（兼容测试 monkeypatch 与脚本覆盖）。
    """

    def __getattr__(cls, name: str) -> str:
        if name in _PATH_SPECS:
            override = _PATH_OVERRIDES.get(name)
            if override is not None:
                return override
            scope, rel = _PATH_SPECS[name]
            if scope == "config":
                return os.path.join(config_dir(), rel)
            if scope == "data":
                base = data_dir()
                return base if not rel else os.path.join(base, rel)
            return rel
        raise AttributeError(f"type object 'ConfigPaths' has no attribute {name!r}")

    def __setattr__(cls, name: str, value: object) -> None:
        if name in _PATH_SPECS:
            _PATH_OVERRIDES[name] = str(value)
            return
        super().__setattr__(name, value)

    def __delattr__(cls, name: str) -> None:
        if name in _PATH_SPECS:
            _PATH_OVERRIDES.pop(name, None)
            return
        super().__delattr__(name)


class ConfigPaths(metaclass=_ConfigPathsMeta):
    """配置路径常量集中管理（动态解析）。

    默认布局与历史一致（config/ + config/memory/），整体搬迁方式：
    - ``ANELF_CONFIG_DIR``：配置目录（纯 JSON 配置）
    - ``ANELF_DATA_DIR``：数据目录（SQLite / 便签 / cognee，优先级最高）
    - app_config.json 的 ``data_root``：数据目录（优先级低于环境变量）
    """
