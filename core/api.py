"""
简化的独立API注册系统 - 去除钩子函数和锁机制
提供统一的API装饰器系统，支持分组管理
"""
import inspect
import time
from typing import Dict, Any, Callable, Optional, List, Set
from dataclasses import dataclass, field
from enum import Enum
from core.log import log
from core.exceptions import catch_exceptions


class ApiType(Enum):
    """API类型枚举"""
    FUNCTION = "function"
    METHOD = "method"
    ASYNC_FUNCTION = "async_function"
    BUILTIN = "builtin"
    CUSTOM = "custom"


class ApiScope(Enum):
    """API作用域枚举"""
    PUBLIC = "public"
    PRIVATE = "private"
    SYSTEM = "system"
    ENTITY = "entity"
    DEBUG = "debug"


@dataclass
class ParameterInfo:
    """参数信息"""
    name: str
    param_type: str = "Any"
    default_value: Any = inspect.Parameter.empty
    required: bool = True

    def __post_init__(self):
        self.required = self.default_value == inspect.Parameter.empty


@dataclass
class ApiMetadata:
    """API元数据"""
    name: str
    func: Callable
    api_type: ApiType
    scope: ApiScope = ApiScope.PUBLIC
    group: str = "default"
    description: str = ""
    tags: List[str] = field(default_factory=list)
    parameters: List[ParameterInfo] = field(default_factory=list)
    return_type: str = "Any"
    enabled: bool = True

    def __post_init__(self):
        """自动分析函数信息"""
        if self.func and callable(self.func):
            self._analyze_function()

    @catch_exceptions()
    def _analyze_function(self):
        """分析函数信息"""
        sig = inspect.signature(self.func)

        # 分析参数
        self.parameters = []
        for param_name, param in sig.parameters.items():
            param_info = ParameterInfo(
                name=param_name,
                param_type=str(param.annotation) if param.annotation != inspect.Parameter.empty else "Any",
                default_value=param.default,
                required=param.default == inspect.Parameter.empty
            )
            self.parameters.append(param_info)

        # 分析返回类型
        if sig.return_annotation != inspect.Signature.empty:
            self.return_type = str(sig.return_annotation)

        # 检查是否为异步函数
        if inspect.iscoroutinefunction(self.func) and self.api_type == ApiType.FUNCTION:
            self.api_type = ApiType.ASYNC_FUNCTION

        # 获取文档字符串
        if not self.description and self.func.__doc__:
            self.description = self.func.__doc__.strip()


class APIRegistry:
    """简化的API注册表"""

    _apis: Dict[str, ApiMetadata] = {}
    _groups: Dict[str, List[str]] = {}
    _scopes: Dict[ApiScope, Set[str]] = {}
    _call_stats: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def register(cls, metadata: ApiMetadata) -> bool:
        """注册API"""
        # 检查重复注册
        if metadata.name in cls._apis:
            existing = cls._apis[metadata.name]
            if existing.func != metadata.func:
                log(f"⚠️ API名称冲突: {metadata.name}", "WARNING")
                return False
            # 同名同 func 的重复注册幂等跳过，避免分组索引重复 append 污染
            return True

        # 注册API
        cls._apis[metadata.name] = metadata
        cls._groups.setdefault(metadata.group, []).append(metadata.name)
        cls._scopes.setdefault(metadata.scope, set()).add(metadata.name)

        # 初始化调用统计
        cls._call_stats[metadata.name] = {
            'call_count': 0,
            'success_count': 0,
            'error_count': 0,
            'total_time': 0.0,
            'last_called': None
        }

        api_type = "实体API" if metadata.scope == ApiScope.ENTITY else "普通API"
        log(f"✅ {api_type}注册成功: {metadata.name}")
        return True

    @classmethod
    def get(cls, name: str) -> Optional[ApiMetadata]:
        """获取API元数据"""
        return cls._apis.get(name)

    @classmethod
    async def call(cls, name: str, *args, **kwargs) -> Any:
        """调用API（ASYNC_FUNCTION 返回的协程会被自动 await）"""
        start_time = time.time()

        metadata = cls.get(name)
        if not metadata or not metadata.enabled:
            raise ValueError(f"API{'不存在' if not metadata else '已禁用'}: {name}")

        # 更新调用统计
        stats = cls._call_stats[name]
        stats['call_count'] += 1
        stats['last_called'] = start_time

        try:
            result = metadata.func(*args, **kwargs)
            if inspect.iscoroutine(result):
                result = await result
            stats['success_count'] += 1
            stats['total_time'] += time.time() - start_time
            return result
        except Exception as e:
            stats['error_count'] += 1
            log(f"❌ API调用失败: {name} - {str(e)}", "ERROR")
            raise

    @classmethod
    def get_by_group(cls, group: str) -> List[ApiMetadata]:
        """根据分组获取API列表"""
        api_names = cls._groups.get(group, [])
        return [cls._apis[name] for name in api_names if name in cls._apis]

    @classmethod
    def get_by_scope(cls, scope: ApiScope) -> List[ApiMetadata]:
        """根据作用域获取API列表"""
        api_names = cls._scopes.get(scope, set())
        return [cls._apis[name] for name in api_names if name in cls._apis]

    @classmethod
    def get_entity_apis(cls) -> List[ApiMetadata]:
        """获取所有实体API"""
        return cls.get_by_scope(ApiScope.ENTITY)

    @classmethod
    def get_regular_apis(cls) -> List[ApiMetadata]:
        """获取所有普通API（非实体API）"""
        return [api for api in cls._apis.values() if api.scope != ApiScope.ENTITY]

    @classmethod
    def get_all(cls) -> List[ApiMetadata]:
        """获取所有API"""
        return list(cls._apis.values())

    @classmethod
    def get_all_names(cls) -> List[str]:
        """获取所有API名称"""
        return list(cls._apis.keys())

    @classmethod
    def get_all_groups(cls) -> List[str]:
        """获取所有分组"""
        return list(cls._groups.keys())

    @classmethod
    def search(cls, keyword: str) -> List[ApiMetadata]:
        """搜索API"""
        keyword_lower = keyword.lower()
        return [api for api in cls._apis.values()
                if (keyword_lower in api.name.lower() or
                    keyword_lower in api.description.lower() or
                    keyword_lower in api.group.lower())]

    @classmethod
    def enable(cls, name: str) -> bool:
        """启用API"""
        if name in cls._apis:
            cls._apis[name].enabled = True
            log(f"✅ API已启用: {name}")
            return True
        return False

    @classmethod
    def disable(cls, name: str) -> bool:
        """禁用API"""
        if name in cls._apis:
            cls._apis[name].enabled = False
            log(f"⚠️ API已禁用: {name}")
            return True
        return False

    @classmethod
    def exists(cls, name: str) -> bool:
        """检查API是否存在"""
        return name in cls._apis

    @classmethod
    def unregister(cls, name: str) -> bool:
        """注销单个API"""
        try:
            if name not in cls._apis:
                return False

            metadata = cls._apis[name]

            # 从分组中移除
            if metadata.group in cls._groups and name in cls._groups[metadata.group]:
                cls._groups[metadata.group].remove(name)
                # 如果分组为空，则删除分组
                if not cls._groups[metadata.group]:
                    del cls._groups[metadata.group]

            # 从作用域中移除
            cls._scopes.get(metadata.scope, set()).discard(name)

            # 移除API和统计信息
            del cls._apis[name]
            if name in cls._call_stats:
                del cls._call_stats[name]

            log(f"✅ API注销成功: {name}")
            return True

        except Exception as e:
            log(f"❌ API注销失败: {name} - {str(e)}", "ERROR")
            return False

    @classmethod
    def unregister_by_group(cls, group: str) -> int:
        """按分组批量注销API"""
        count = 0
        api_names = cls._groups.get(group, []).copy()  # 使用副本避免迭代时修改
        
        for name in api_names:
            if cls.unregister(name):
                count += 1
                
        if count > 0:
            log(f"✅ 批量注销API成功: 分组'{group}' - {count}个API")
            
        return count

    @classmethod
    def unregister_by_prefix(cls, prefix: str) -> int:
        """按名称前缀批量注销API"""
        count = 0
        api_names = [name for name in cls._apis.keys() if name.startswith(prefix)]
        
        for name in api_names:
            if cls.unregister(name):
                count += 1
                
        if count > 0:
            log(f"✅ 批量注销API成功: 前缀'{prefix}' - {count}个API")
            
        return count

    @classmethod
    def clear(cls) -> None:
        """清空所有API"""
        cls._apis.clear()
        cls._groups.clear()
        cls._scopes.clear()
        cls._call_stats.clear()
        log("🧹 API注册表已清空")

    @classmethod
    def get_statistics(cls) -> Dict[str, Any]:
        """获取统计信息"""
        entity_apis = len(cls.get_entity_apis())
        regular_apis = len(cls.get_regular_apis())

        return {
            'total_apis': len(cls._apis),
            'entity_apis': entity_apis,
            'regular_apis': regular_apis,
            'total_groups': len(cls._groups),
            'enabled_apis': sum(1 for api in cls._apis.values() if api.enabled),
            'scopes': {s.value: len(apis) for s, apis in cls._scopes.items()}
        }


# 注册api 用此装饰器可以直接修饰函数 完成函数的封装
def api(name: Optional[str] = None, scope: ApiScope = ApiScope.PUBLIC, group: str = "default", description: str = "", tags: Optional[List[str]] = None, enabled: bool = True):
    """简化的API装饰器"""
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        # 生成API名称
        api_name = name or f"{func.__module__}.{func.__name__}"

        # 自动检测API类型
        api_type = ApiType.FUNCTION
        if inspect.iscoroutinefunction(func):
            api_type = ApiType.ASYNC_FUNCTION
        elif inspect.ismethod(func):
            api_type = ApiType.METHOD
        elif inspect.isbuiltin(func):
            api_type = ApiType.BUILTIN

        # 创建元数据
        metadata = ApiMetadata(
            name=api_name,
            func=func,
            api_type=api_type,
            scope=scope,
            group=group,
            description=description or func.__doc__ or f"{func.__name__}方法",
            tags=tags or [],
            enabled=enabled
        )

        # 注册API
        APIRegistry.register(metadata)
        
        return func

    return decorator


# 调用api
async def call_api(name: str, *args: Any, **kwargs: Any) -> Any:
    """调用API"""
    return await APIRegistry.call(name, *args, **kwargs)
