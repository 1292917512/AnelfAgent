"""
独立配置管理器 - 完全独立的配置系统
提供基于全局内存的简化配置管理功能
"""
import json
import os
import re
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from core.log import log
from core.path import ConfigPaths, PathManager

_ENV_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

# 简单邮箱形态校验（用于配置值类型自动探测）
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# 字符串长度超过该阈值时自动探测为长文本类型
_TEXT_LENGTH_THRESHOLD = 100


def parse_env_value(value: str) -> Any:
    """将环境变量字符串解析为合适的 Python 类型（bool/int/float/str）。"""
    low = value.lower()
    if low in ("true", "1", "yes"):
        return True
    if low in ("false", "0", "no"):
        return False
    try:
        return int(value)
    except ValueError:
        log("parse_env_value 异常已忽略", "DEBUG")
    try:
        return float(value)
    except ValueError:
        log("parse_env_value 异常已忽略", "DEBUG")
    return value


def expand_env_refs(value: Any) -> Any:
    """递归展开字符串值中的 ``${ENV_VAR}`` 引用（用于密钥外置到环境变量）。

    未设置的变量替换为空字符串并记录 WARNING。dict/list 递归处理，其他类型原样返回。
    """
    if isinstance(value, str):
        def _sub(match: "re.Match[str]") -> str:
            var = match.group(1)
            if var not in os.environ:
                log(f"配置引用了未设置的环境变量: {var}", "WARNING")
            return os.environ.get(var, "")
        return _ENV_REF_RE.sub(_sub, value)
    if isinstance(value, dict):
        return {k: expand_env_refs(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env_refs(v) for v in value]
    return value


def load_json_config(path: Union[str, Path], default: Any = None) -> Any:
    """读取 JSON 配置文件并展开 ${ENV_VAR} 引用；文件缺失或解析失败返回 default。"""
    p = Path(path)
    if not p.exists():
        return default
    try:
        return expand_env_refs(json.loads(p.read_text("utf-8")))
    except Exception as e:
        log(f"JSON 配置加载失败 ({p}): {e}", "WARNING")
        return default


class ConfigValueType(Enum):
    """配置值类型枚举"""
    AUTO = "auto"  # 自动检测
    STRING = "string"  # 字符串
    INTEGER = "integer"  # 整数
    FLOAT = "float"  # 浮点数
    BOOLEAN = "boolean"  # 布尔值
    PATH = "path"  # 文件路径
    URL = "url"  # URL地址
    EMAIL = "email"  # 邮箱地址
    PASSWORD = "password"  # 密码（隐藏显示）
    TEXT = "text"  # 长文本
    JSON = "json"  # JSON对象
    ENUM = "enum"  # 枚举选择
    COLOR = "color"  # 颜色值
    RANGE = "range"  # 数值范围


@dataclass
class ConfigItem:
    """简化的配置项描述"""
    key: str
    group: str
    description: str
    default_value: Any
    value_type: Union[ConfigValueType, str] = ConfigValueType.AUTO
    editable: bool = True
    # 基本约束
    enum_options: Optional[List[str]] = None  # 枚举选项
    required: bool = False  # 是否必填
    # 展示与交互元数据
    advanced: bool = False  # 高级项（UI 折叠到高级区，基础项直接展示）
    min_value: Optional[float] = None  # RANGE 类型下界
    max_value: Optional[float] = None  # RANGE 类型上界
    step: Optional[float] = None  # RANGE 类型步进
    unit: str = ""  # 单位展示（秒/%/条/分钟…）

    def __post_init__(self):
        if self.value_type == ConfigValueType.AUTO or self.value_type == "auto":
            self.value_type = self._detect_type(self.default_value)

    def clamp(self, value: Any) -> Any:
        """按声明的 min/max 边界收敛数值（未声明边界时原样返回）。"""
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return value
        if self.min_value is not None and value < self.min_value:
            return self.min_value
        if self.max_value is not None and value > self.max_value:
            return self.max_value
        return value

    def _detect_type(self, value: object) -> ConfigValueType:
        """自动检测值类型"""
        if isinstance(value, bool):
            return ConfigValueType.BOOLEAN
        elif isinstance(value, int):
            return ConfigValueType.INTEGER
        elif isinstance(value, float):
            return ConfigValueType.FLOAT
        elif isinstance(value, (dict, list)):
            return ConfigValueType.JSON
        elif isinstance(value, str):
            if value.startswith(('http://', 'https://', 'ftp://')):
                return ConfigValueType.URL
            elif _EMAIL_RE.match(value):
                return ConfigValueType.EMAIL
            elif value.startswith(('/', '~', '\\')) or os.sep in value:
                return ConfigValueType.PATH
            elif len(value) > _TEXT_LENGTH_THRESHOLD:
                return ConfigValueType.TEXT
            else:
                return ConfigValueType.STRING
        else:
            return ConfigValueType.STRING


class ConfigRegistry:
    """配置注册表"""

    _registry: Dict[str, ConfigItem] = {}
    _groups: Dict[str, List[str]] = {}
    _lock = threading.RLock()

    @classmethod
    def register(cls, item: ConfigItem):
        """注册配置项（重复注册更新定义，分组索引不累积重复 key）"""
        with cls._lock:
            cls._registry[item.key] = item
            keys = cls._groups.setdefault(item.group, [])
            if item.key not in keys:
                keys.append(item.key)

    @classmethod
    def get_item(cls, key: str) -> Optional[ConfigItem]:
        """获取配置项"""
        return cls._registry.get(key)

    @classmethod
    def get_group_items(cls, group: str) -> List[ConfigItem]:
        """获取分组下的所有配置项"""
        keys = cls._groups.get(group, [])
        return [cls._registry[key] for key in keys if key in cls._registry]

    @classmethod
    def get_all_groups(cls) -> List[str]:
        """获取所有分组名"""
        return list(cls._groups.keys())

    @classmethod
    def get_all_items(cls) -> List[ConfigItem]:
        """获取所有配置项"""
        return list(cls._registry.values())

    @classmethod
    def get_grouped_items(cls) -> Dict[str, List[ConfigItem]]:
        """获取按分组组织的配置项"""
        grouped = {}
        for group_name in cls._groups:
            grouped[group_name] = cls.get_group_items(group_name)
        return grouped


class ConfigManager:
    """全局配置管理器

    双层级存储：
    - ``_file_config``：文件原始值（保留 ``${ENV_VAR}`` 引用语法），save 时回写
    - ``_config``：生效值（引用已展开、环境变量已覆盖），get 时读取

    加载优先级（后者覆盖前者）：文件值 < ``ANELF_<KEY>`` 环境变量。
    """

    # 全局内存配置存储
    _config: Dict[str, Any] = {}
    _file_config: Dict[str, Any] = {}
    # 显式指定的配置文件路径；None 时每次访问动态解析 ConfigPaths.APP_CONFIG
    _config_file: Optional[str] = None
    _lock = threading.RLock()
    _initialized = False

    @classmethod
    def _get_config_file(cls) -> str:
        """当前生效的配置文件路径（未显式指定时动态解析，避免 import 时冻结）。"""
        return cls._config_file if cls._config_file is not None else ConfigPaths.APP_CONFIG

    @classmethod
    def initialize(cls, config_file: Optional[str] = None) -> bool:
        """初始化配置管理器"""
        if cls._initialized:
            return True

        try:
            with cls._lock:
                if config_file:
                    cls._config_file = config_file

                # 确保配置目录存在
                config_dir = PathManager.dirname(cls._get_config_file())
                if config_dir and not PathManager.exists(config_dir):
                    PathManager.ensure_dir_exists(config_dir)

                # 加载配置文件
                cls._load_config()
                cls._initialized = True
                return True

        except Exception as e:
            log(f"❌ 配置管理器初始化失败: {str(e)}", "ERROR")
            return False

    @classmethod
    def get(cls, key: str, default: Any = None) -> Any:
        """获取配置值"""
        return cls._config.get(key, default)

    @classmethod
    def set(cls, key: str, value: Any) -> None:
        """设置配置值（同时写入生效层与文件层，save 后持久化）"""
        with cls._lock:
            cls._config[key] = value
            cls._file_config[key] = value

    @classmethod
    def has(cls, key: str) -> bool:
        """检查配置键是否存在"""
        return key in cls._config

    @classmethod
    def save(cls) -> bool:
        """保存配置到JSON文件（回写文件原始层，保留 ${ENV_VAR} 引用语法；原子写避免半截文件）"""
        try:
            with cls._lock:
                from core.file_utils import atomic_write_text
                config_content = json.dumps(cls._file_config, indent=2, ensure_ascii=False)
                atomic_write_text(Path(cls._get_config_file()), config_content)
                return True
        except Exception as e:
            log(f"❌ 保存配置异常: {str(e)}", "ERROR")
            return False

    @classmethod
    def reload(cls) -> bool:
        """重新加载配置文件"""
        try:
            with cls._lock:
                cls._load_config()
                return True
        except Exception as e:
            log(f"❌ 重新加载配置失败: {str(e)}", "ERROR")
            return False

    @classmethod
    def clear(cls) -> None:
        """清空内存中的配置"""
        with cls._lock:
            cls._config.clear()
            cls._file_config.clear()

    @classmethod
    def reset(cls) -> None:
        """完全重置到初始状态（测试用）。"""
        with cls._lock:
            cls._config.clear()
            cls._file_config.clear()
            cls._initialized = False

    @classmethod
    def get_all(cls) -> Dict[str, Any]:
        """获取所有配置"""
        return cls._config.copy()

    @classmethod
    def update(cls, config_dict: Dict[str, Any]) -> None:
        """批量更新配置"""
        with cls._lock:
            cls._config.update(config_dict)
            cls._file_config.update(config_dict)

    @classmethod
    def _load_config(cls) -> None:
        """从文件加载配置：展开 ${ENV_VAR} 引用后，再应用 ANELF_<KEY> 环境变量覆盖。"""
        try:
            config_file = cls._get_config_file()
            content = PathManager.read_text(config_file) if PathManager.exists(config_file) else ""
            raw = json.loads(content) if content.strip() else {}
            if not isinstance(raw, dict):
                raw = {}
            cls._file_config = raw
            cls._config = expand_env_refs(raw)
            cls._apply_env_overrides()
        except Exception as e:
            # 加载失败（如文件损坏）时保留内存现有配置：
            # 清空 _file_config 会导致后续 save() 把空配置覆盖落盘，造成全量配置丢失
            log(f"配置文件加载失败（保留当前内存配置）: {e}", "ERROR")

    @classmethod
    def _apply_env_overrides(cls) -> None:
        """ANELF_<KEY> 环境变量覆盖文件已存在的同名配置项（仅作用于生效层，不回写文件）。"""
        overridden: List[str] = []
        for key in list(cls._config):
            env_key = f"ANELF_{key.upper()}"
            env_val = os.environ.get(env_key)
            if env_val is None:
                continue
            cls._config[key] = parse_env_value(env_val)
            overridden.append(env_key)
        if overridden:
            log(f"环境变量覆盖 {len(overridden)} 项配置: {', '.join(overridden)}")


def register_configs(configs: Dict[str, Dict[str, Any]]) -> None:
    """批量注册配置"""
    for group_name, group_configs in configs.items():
        for config_key, config_info in group_configs.items():
            config_item = ConfigItem(
                key=config_key,
                group=group_name,
                description=config_info.get("description", config_key),
                default_value=config_info.get("default", ""),
                value_type=config_info.get("value_type", ConfigValueType.AUTO),
                enum_options=config_info.get("options"),
                required=config_info.get("required", False),
                advanced=config_info.get("advanced", False),
                min_value=config_info.get("min"),
                max_value=config_info.get("max"),
                step=config_info.get("step"),
                unit=config_info.get("unit", ""),
            )

            # 注册配置项并初始化默认值
            ConfigRegistry.register(config_item)
            if not ConfigManager.has(config_key):
                ConfigManager.set(config_key, config_info.get("default", ""))


# ----------------------------------------------------------------------
# 安全访问辅助（供各模块在 ConfigManager 可能未初始化时使用）
# ----------------------------------------------------------------------


def get_config(key: str, default: Any = None) -> Any:
    """安全读取配置值：ConfigManager 不可用/未初始化时返回 default。

    各模块统一使用本函数替代散落的 try/except ConfigManager.get 样板代码。
    """
    try:
        return ConfigManager.get(key, default)
    except Exception as exc:
        log(f"读取配置失败 {key}: {exc}", "DEBUG")
        return default


def get_config_bool(key: str, default: bool = False) -> bool:
    """安全读取布尔配置（字符串 "false"/"0"/"no" 正确解析为 False）。"""
    value = get_config(key, default)
    if isinstance(value, str):
        parsed = parse_env_value(value)
        return bool(parsed) if isinstance(parsed, bool) else bool(value)
    return bool(value)


def get_config_int(key: str, default: int = 0) -> int:
    """安全读取整数配置。"""
    try:
        return int(get_config(key, default))
    except (TypeError, ValueError):
        return default


def get_config_float(key: str, default: float = 0.0) -> float:
    """安全读取浮点配置。"""
    try:
        return float(get_config(key, default))
    except (TypeError, ValueError):
        return default


def register_configs_safe(configs: Dict[str, Dict[str, Any]]) -> None:
    """安全注册配置：ConfigManager 不可用时不中断模块导入。"""
    try:
        register_configs(configs)
    except Exception as e:
        log(f"配置注册失败（已忽略，不中断导入）: {e}", "WARNING")
