"""
独立配置管理器 - 完全独立的配置系统
提供基于全局内存的简化配置管理功能
"""
import json
import threading
from typing import Dict, Any, Optional, Union, List
from dataclasses import dataclass
from enum import Enum
from core.log import log
from core.path import ConfigPaths, PathManager


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
    tag: str = ""  # 条件显示标签（如 "forward"/"reverse"）

    def __post_init__(self):
        if self.value_type == ConfigValueType.AUTO or self.value_type == "auto":
            self.value_type = self._detect_type(self.default_value)

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
            elif '@' in value and '.' in value:
                return ConfigValueType.EMAIL
            elif '/' in value or '\\' in value:
                return ConfigValueType.PATH
            elif len(value) > 100:
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
        """注册配置项"""
        with cls._lock:
            cls._registry[item.key] = item
            cls._groups.setdefault(item.group, []).append(item.key)

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

    @classmethod
    def get_hierarchical_items(cls) -> Dict[str, Any]:
        """获取按层次结构组织的配置项 - 简化版本，直接返回嵌套字典"""
        hierarchical = {}
        grouped_items = cls.get_grouped_items()

        for group_name, items in grouped_items.items():
            # 处理多级分组路径
            if '/' in group_name:
                parts = group_name.split('/')
                current = hierarchical

                # 构建嵌套结构 - 简化，不使用_children层
                for i, part in enumerate(parts):
                    if i == len(parts) - 1:
                        # 最后一级，直接设置配置项
                        current[part] = items
                    else:
                        # 中间层级，确保存在字典
                        if part not in current:
                            current[part] = {}
                        current = current[part]
            else:
                # 单级分组，直接设置
                hierarchical[group_name] = items

        return hierarchical


class ConfigManager:
    """全局配置管理器"""

    # 全局内存配置存储
    _config: Dict[str, Any] = {}
    _config_file: str = ConfigPaths.APP_CONFIG
    _lock = threading.RLock()
    _initialized = False

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
                config_dir = PathManager.dirname(cls._config_file)
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
        """设置配置值"""
        with cls._lock:
            cls._config[key] = value

    @classmethod
    def has(cls, key: str) -> bool:
        """检查配置键是否存在"""
        return key in cls._config

    @classmethod
    def save(cls) -> bool:
        """保存配置到JSON文件"""
        try:
            with cls._lock:
                config_content = json.dumps(cls._config, indent=2, ensure_ascii=False)
                success = PathManager.write_text(cls._config_file, config_content)
                return success
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

    @classmethod
    def reset(cls) -> None:
        """完全重置到初始状态（测试用）。"""
        with cls._lock:
            cls._config.clear()
            cls._initialized = False

    @classmethod
    def get_statistics(cls) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            'total_configs': len(cls._config),
            'total_groups': len(ConfigRegistry._groups),
            'total_items': len(ConfigRegistry._registry)
        }

    @classmethod
    def get_all(cls) -> Dict[str, Any]:
        """获取所有配置"""
        return cls._config.copy()

    @classmethod
    def update(cls, config_dict: Dict[str, Any]) -> None:
        """批量更新配置"""
        with cls._lock:
            cls._config.update(config_dict)

    @classmethod
    def get_grouped_configs(cls) -> Dict[str, Dict[str, Any]]:
        """获取按分组组织的配置"""
        grouped_items = ConfigRegistry.get_grouped_items()

        # 使用字典推导式简化
        grouped = {
            group_name: {item.key: cls.get(item.key, item.default_value) for item in items}
            for group_name, items in grouped_items.items()
        }

        # 添加未注册的配置
        registered_keys = {item.key for item in ConfigRegistry.get_all_items()}
        unregistered = {k: v for k, v in cls._config.items() if k not in registered_keys}
        if unregistered:
            grouped["其他"] = unregistered

        return grouped

    @classmethod
    def _load_config(cls) -> None:
        """从文件加载配置"""
        try:
            # 简化文件加载逻辑
            content = PathManager.read_text(cls._config_file) if PathManager.exists(cls._config_file) else ""
            cls._config = json.loads(content) if content.strip() else {}
        except Exception as e:
            log(f"配置文件加载失败: {e}", "WARNING")
            cls._config = {}


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
                tag=config_info.get("tag", ""),
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
    except Exception:
        return default


def get_config_bool(key: str, default: bool = False) -> bool:
    """安全读取布尔配置。"""
    return bool(get_config(key, default))


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
    except Exception:
        pass
