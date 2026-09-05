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
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

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


def mask_secret(value: str) -> str:
    """敏感配置值掩码：长度 > 8 保留头尾各 4 位，否则全遮蔽。空值原样返回。"""
    if not value:
        return value
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}****{value[-4:]}"


class ConfigStore:
    """外部配置存储后端协议（值落在模块自己的配置文件中，如频道目录的 channel_config.json）。

    经 ``ConfigManager.register_store(prefix, store)`` 按键前缀接入后，
    ConfigManager 的 get/set/has 自动路由到本存储，save() 统一落盘，
    文件级外部变更经 ``ConfigManager.notify_external`` 驱动变更监听热更。
    键名以前缀完整形式传入（如 ``qq_ws_url``），由实现自行拆分。
    """

    def load(self) -> None:
        """从存储介质加载到内存（注册时调用一次）。"""
        raise NotImplementedError

    def get(self, key: str, default: Any = None) -> Any:
        """读取键值（含环境变量等覆盖语义由实现定义）。"""
        raise NotImplementedError

    def set(self, key: str, value: Any) -> None:
        """写入键值（内存态，save 时落盘）。"""
        raise NotImplementedError

    def has(self, key: str) -> bool:
        """键是否存在（含覆盖来源）。"""
        raise NotImplementedError

    def save(self) -> None:
        """持久化到存储介质（须原子写）。"""
        raise NotImplementedError


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
    tag: str = ""  # 条件显示标记（如频道 ws_mode 的 forward/reverse，仅供 UI 分组过滤）

    def __post_init__(self):
        if self.value_type == ConfigValueType.AUTO or self.value_type == "auto":
            self.value_type = self._detect_type(self.default_value)

    @property
    def type_name(self) -> str:
        """声明类型的字符串形式（AUTO 已在构造时解析）。"""
        vt = self.value_type
        return vt.value if isinstance(vt, ConfigValueType) else str(vt)

    @property
    def is_secret(self) -> bool:
        """是否敏感项（PASSWORD 类型，对外展示需掩码）。"""
        return self.type_name == ConfigValueType.PASSWORD.value

    def coerce_value(self, value: Any) -> Any:
        """按声明类型校验并转换外部输入值，非法值抛 ValueError。

        供 Web PUT 与 AI 配置工具共用的统一入口（转换后调用方应再经 clamp 收敛）。
        """
        expected = self.type_name
        try:
            if expected == "boolean":
                if isinstance(value, bool):
                    return value
                if isinstance(value, str):
                    return value.lower() in ("true", "1", "yes")
                return bool(value)
            if expected == "integer":
                return int(value)
            if expected in ("float", "range"):
                number = float(value)
                # range 与默认值保持同型（整型默认值不产生浮点值）
                if expected == "range" and isinstance(self.default_value, int):
                    return int(number)
                return number
            if expected == "enum":
                return str(value)
            if expected == "json":
                return value
            return value if isinstance(value, str) else str(value)
        except (TypeError, ValueError):
            raise ValueError(
                f"配置项 {self.key} 的值类型错误（期望 {expected}）"
            ) from None

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
    # 变更监听器：键前缀 -> 回调列表（set/update 命中前缀时同步通知，供热重载）
    _listeners: Dict[str, List[Callable[[str, Any], None]]] = {}
    # 外部存储后端：键前缀 -> ConfigStore（值落在模块自己的配置文件中，路由读写）
    _stores: Dict[str, "ConfigStore"] = {}

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
        """获取配置值（外部存储后端前缀命中时路由到对应 store）"""
        store = cls._store_for(key)
        if store is not None:
            return store.get(key, default)
        return cls._config.get(key, default)

    @classmethod
    def set(cls, key: str, value: Any) -> None:
        """设置配置值（同时写入生效层与文件层，save 后持久化；通知前缀匹配的监听器）"""
        store = cls._store_for(key)
        if store is not None:
            store.set(key, value)
            cls._notify_listeners(key, value)
            return
        with cls._lock:
            cls._config[key] = value
            cls._file_config[key] = value
        cls._notify_listeners(key, value)

    @classmethod
    def register_store(cls, prefix: str, store: "ConfigStore") -> None:
        """注册外部存储后端：命中前缀的键读写路由到该 store（模块目录自有配置文件）。

        注册即 ``store.load()``；store 管的键不再进入 app_config.json。
        """
        with cls._lock:
            cls._stores[prefix] = store
        store.load()

    @classmethod
    def _store_for(cls, key: str) -> "Optional[ConfigStore]":
        """按键前缀匹配外部存储后端（未命中返回 None）。"""
        for prefix, store in cls._stores.items():
            if key.startswith(prefix):
                return store
        return None

    @classmethod
    def add_listener(cls, prefix: str, callback: Callable[[str, Any], None]) -> None:
        """注册键前缀变更监听（set/update 命中前缀时回调 (key, value)，幂等去重）。

        回调在锁外同步执行；抛错仅记录日志不影响写入方。
        """
        with cls._lock:
            callbacks = cls._listeners.setdefault(prefix, [])
            if callback not in callbacks:
                callbacks.append(callback)

    @classmethod
    def remove_listener(cls, prefix: str, callback: Callable[[str, Any], None]) -> None:
        """移除键前缀监听（不存在时静默跳过）。"""
        with cls._lock:
            callbacks = cls._listeners.get(prefix)
            if callbacks and callback in callbacks:
                callbacks.remove(callback)

    @classmethod
    def _notify_listeners(cls, key: str, value: Any) -> None:
        """通知键前缀匹配的监听器（锁外执行，单个回调异常不影响其余）。"""
        with cls._lock:
            matched = [cb for p, cbs in cls._listeners.items() if key.startswith(p) for cb in cbs]
        for callback in matched:
            try:
                callback(key, value)
            except Exception as exc:
                log(f"配置变更监听回调失败 ({key}): {exc}", "WARNING")

    @classmethod
    def notify_external(cls, key: str, value: Any) -> None:
        """外部存储后端检测到文件级变更时上报（驱动变更监听，如手工编辑频道配置文件）。"""
        cls._notify_listeners(key, value)

    @classmethod
    def has(cls, key: str) -> bool:
        """检查配置键是否存在"""
        store = cls._store_for(key)
        if store is not None:
            return store.has(key)
        return key in cls._config

    @classmethod
    def save(cls) -> bool:
        """保存配置到JSON文件（回写文件原始层，保留 ${ENV_VAR} 引用语法；原子写避免半截文件）。

        外部存储后端的 store 随之一并落盘（各模块自有配置文件）。
        """
        try:
            with cls._lock:
                from core.file_utils import atomic_write_text
                config_content = json.dumps(cls._file_config, indent=2, ensure_ascii=False)
                atomic_write_text(Path(cls._get_config_file()), config_content)
            for store in list(cls._stores.values()):
                try:
                    store.save()
                except Exception as exc:
                    log(f"外部配置存储落盘失败: {exc}", "ERROR")
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
        """清空内存中的配置、监听器与外部存储注册（全量状态复位）。"""
        with cls._lock:
            cls._config.clear()
            cls._file_config.clear()
            cls._listeners.clear()
            cls._stores.clear()

    @classmethod
    def reset(cls) -> None:
        """完全重置到初始状态（测试用）。"""
        with cls._lock:
            cls._config.clear()
            cls._file_config.clear()
            cls._listeners.clear()
            cls._stores.clear()
            cls._initialized = False

    @classmethod
    def get_all(cls) -> Dict[str, Any]:
        """获取所有配置"""
        return cls._config.copy()

    @classmethod
    def update(cls, config_dict: Dict[str, Any]) -> None:
        """批量更新配置"""
        for key, value in config_dict.items():
            store = cls._store_for(key)
            if store is not None:
                store.set(key, value)
                continue
            with cls._lock:
                cls._config[key] = value
                cls._file_config[key] = value
        for key, value in config_dict.items():
            cls._notify_listeners(key, value)

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


# ----------------------------------------------------------------------
# pydantic 模型 → 配置注册（模块以模型为单一声明源，如频道 CONFIG_MODEL）
# ----------------------------------------------------------------------


def _model_field_type(annotation: Any) -> Tuple[ConfigValueType, Optional[List[str]]]:
    """从 pydantic 字段注解推导配置类型；Literal 自动转 ENUM 并提取选项。"""
    import typing

    origin = typing.get_origin(annotation)
    if origin is typing.Literal:
        return ConfigValueType.ENUM, [str(a) for a in typing.get_args(annotation)]
    if origin is Union or str(origin) == "types.UnionType":
        # Optional[X] 解包取第一个非 None 分支
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return _model_field_type(args[0])
    if annotation is bool:
        return ConfigValueType.BOOLEAN, None
    if annotation is int:
        return ConfigValueType.INTEGER, None
    if annotation is float:
        return ConfigValueType.FLOAT, None
    if annotation in (dict, list):
        return ConfigValueType.JSON, None
    return ConfigValueType.STRING, None


def register_model_configs(
    group: str,
    model_cls: Any,
    *,
    key_prefix: str = "",
    only_fields: Optional[set] = None,
) -> List[str]:
    """从 pydantic BaseModel 派生配置项注册到 ConfigRegistry（模型即声明源）。

    字段元数据映射规则：
    - ``Field(description=...)`` → 配置描述
    - 注解类型 → value_type（bool/int/float/dict/list 直映；Literal → ENUM + options）
    - ``Field(json_schema_extra={...})`` 直通覆盖：value_type / options / advanced /
      min / max / step / unit / tag

    Args:
        group: 配置组名（如 ``adapter/qq``）
        model_cls: pydantic BaseModel 子类
        key_prefix: 注册键前缀（如频道 id，字段 ``ws_url`` → 键 ``qq_ws_url``）
        only_fields: 仅注册这些字段名（None = 全部模型字段）

    Returns:
        注册的配置键列表
    """
    registered: List[str] = []
    for name, field in model_cls.model_fields.items():
        if only_fields is not None and name not in only_fields:
            continue
        key = f"{key_prefix}_{name}" if key_prefix else name
        extras = field.json_schema_extra if isinstance(field.json_schema_extra, dict) else {}
        value_type, options = _model_field_type(field.annotation)
        if "value_type" in extras:
            value_type = ConfigValueType(extras["value_type"])
        if "options" in extras:
            options = [str(o) for o in extras["options"]]
        default = field.get_default(call_default_factory=True)

        ConfigRegistry.register(ConfigItem(
            key=key,
            group=group,
            description=field.description or name,
            default_value=default,
            value_type=value_type,
            enum_options=options,
            advanced=bool(extras.get("advanced", False)),
            min_value=extras.get("min"),
            max_value=extras.get("max"),
            step=extras.get("step"),
            unit=str(extras.get("unit", "")),
            tag=str(extras.get("tag", "")),
        ))
        if not ConfigManager.has(key):
            ConfigManager.set(key, default)
        registered.append(key)
    return registered
