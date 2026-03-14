"""
内存键值缓存系统

使用示例:
    # 创建缓存变量（最简单）
    user_data = CacheVar('user_info', {'name': 'default'})
    user_data.value = {'name': 'Alice', 'age': 30}
    print(user_data.value)  # {'name': 'Alice', 'age': 30}
    
    # 创建缓存字典
    settings = CacheDict('app_settings')
    settings['theme'] = 'dark'
    settings['language'] = 'zh'
    
    # 分namespace使用不同缓存
    api_cache = CacheDict('api_data')
    user_cache = CacheDict('user_data')
    
    # 带过期时间
    temp_data = CacheVar('temp', None, ttl=60)  # 60秒后过期
"""
import time
import threading
from typing import Any, Dict, Optional, List
from core.log import debug, info
from core.exceptions import catch_exceptions


class CacheItem:
    """缓存项"""

    def __init__(self, key: str, value: Any, ttl: Optional[float] = None):
        self.key = key
        self.value = value
        self.created_at = time.time()
        self.accessed_at = self.created_at
        self.access_count = 0
        self.ttl = ttl

    def is_expired(self) -> bool:
        """检查是否过期"""
        if self.ttl is None:
            return False
        return time.time() - self.created_at > self.ttl

    def access(self):
        """记录访问"""
        self.accessed_at = time.time()
        self.access_count += 1


class Cache:
    """内存缓存"""

    def __init__(self, name: str = "default", max_size: int = 1000):
        self.name = name
        self.max_size = max_size
        self._cache: Dict[str, CacheItem] = {}
        self._lock = threading.RLock()
        self._stats = {'hits': 0, 'misses': 0, 'sets': 0, 'deletes': 0}

    def get(self, key: str) -> Any:
        """获取缓存值"""
        with self._lock:
            if key not in self._cache:
                self._stats['misses'] += 1
                return None

            item = self._cache[key]

            # 检查过期
            if item.is_expired():
                del self._cache[key]
                self._stats['misses'] += 1
                return None

            # 记录访问
            item.access()
            self._stats['hits'] += 1
            return item.value

    @catch_exceptions(reraise=False, default_value="缓存设置失败")
    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> bool:
        """设置缓存值"""
        with self._lock:
            # 检查容量限制
            if key not in self._cache and len(self._cache) >= self.max_size:
                self._evict_one()
            item = CacheItem(key, value, ttl)
            self._cache[key] = item
            self._stats['sets'] += 1
            return True

    def delete(self, key: str) -> bool:
        """删除缓存项"""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                self._stats['deletes'] += 1
                return True
            return False

    def clear(self) -> bool:
        """清空缓存"""
        with self._lock:
            self._cache.clear()
            return True

    def exists(self, key: str) -> bool:
        """检查是否存在"""
        with self._lock:
            if key not in self._cache:
                return False
            return not self._cache[key].is_expired()

    def keys(self) -> List[str]:
        """获取所有键"""
        with self._lock:
            # 清理过期项并返回有效键
            self._cleanup_expired()
            return list(self._cache.keys())

    def size(self) -> int:
        """获取缓存大小"""
        with self._lock:
            self._cleanup_expired()
            return len(self._cache)

    def stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        with self._lock:
            self._cleanup_expired()
            total_access = self._stats['hits'] + self._stats['misses']
            hit_rate = self._stats['hits'] / total_access if total_access > 0 else 0

            return {
                'size': len(self._cache),
                'max_size': self.max_size,
                'hits': self._stats['hits'],
                'misses': self._stats['misses'],
                'sets': self._stats['sets'],
                'deletes': self._stats['deletes'],
                'hit_rate': hit_rate
            }

    def _evict_one(self):
        """淘汰一个缓存项（LRU策略）"""
        if not self._cache:
            return

        # 找到最老的访问项
        oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k].accessed_at)
        del self._cache[oldest_key]

    def _cleanup_expired(self):
        """清理过期项"""
        expired_keys = [key for key, item in self._cache.items() if item.is_expired()]
        for key in expired_keys:
            del self._cache[key]


class CacheManager:
    """缓存管理器"""

    _caches: Dict[str, Cache] = {}
    _lock = threading.RLock()
    _default_cache_name = "default"

    @classmethod
    def get_cache(cls, name: str = None) -> Cache:
        """获取缓存实例"""
        cache_name = name or cls._default_cache_name

        with cls._lock:
            if cache_name not in cls._caches:
                cls._caches[cache_name] = Cache(cache_name)
                debug(f"✅ 创建新缓存: {cache_name}")

            return cls._caches[cache_name]

    @classmethod
    def create_cache(cls, name: str, max_size: int = 1000) -> Cache:
        """创建新缓存"""
        with cls._lock:
            cache = Cache(name, max_size)
            cls._caches[name] = cache
            debug(f"✅ 创建缓存: {name} (容量: {max_size})")
            return cache

    @classmethod
    def list_caches(cls) -> List[str]:
        """列出所有缓存名称"""
        return list(cls._caches.keys())

    @classmethod
    def delete_cache(cls, name: str) -> bool:
        """删除缓存"""
        with cls._lock:
            if name in cls._caches:
                del cls._caches[name]
                debug(f"🗑️ 删除缓存: {name}")
                return True
            return False

    @classmethod
    def clear_all_caches(cls) -> bool:
        """清空所有缓存"""
        with cls._lock:
            for cache in cls._caches.values():
                cache.clear()
            info("🧹 清空所有缓存")
            return True

    @classmethod
    def get_global_stats(cls) -> Dict[str, Any]:
        """获取全局统计信息"""
        with cls._lock:
            total_stats = {
                'cache_count': len(cls._caches),
                'total_items': 0,
                'total_hits': 0,
                'total_misses': 0,
                'total_sets': 0,
                'total_deletes': 0
            }

            for cache in cls._caches.values():
                stats = cache.stats()
                total_stats['total_items'] += stats['size']
                total_stats['total_hits'] += stats['hits']
                total_stats['total_misses'] += stats['misses']
                total_stats['total_sets'] += stats['sets']
                total_stats['total_deletes'] += stats['deletes']

            total_access = total_stats['total_hits'] + total_stats['total_misses']
            total_stats['overall_hit_rate'] = total_stats['total_hits'] / total_access if total_access > 0 else 0

            return total_stats


# ==================== 变量封装 ====================S

class CacheVar:
    """缓存变量 - 像普通变量一样使用"""

    def __init__(self, key: str, default_value: Any = None,
                 ttl: Optional[float] = None, cache_name: str = None):
        """
        初始化缓存变量
        
        Args:
            key: 缓存键名
            default_value: 默认值 
            ttl: 过期时间(秒)
            cache_name: 缓存实例名称
        """
        self._key = key
        self._default = default_value
        self._ttl = ttl
        self._cache_name = cache_name

        # 如果有默认值，先设置
        if default_value is not None:
            self.set(default_value)

    @property
    def value(self) -> Any:
        """获取值 - 通过 .value 属性访问"""
        cache = CacheManager.get_cache(self._cache_name)
        result = cache.get(self._key)
        return result if result is not None else self._default

    @value.setter
    def value(self, val: Any):
        """设置值 - 通过 .value 属性赋值"""
        cache = CacheManager.get_cache(self._cache_name)
        cache.set(self._key, val, self._ttl)

    def get(self) -> Any:
        """获取值"""
        return self.value

    def set(self, val: Any):
        """设置值"""
        self.value = val

    def delete(self) -> bool:
        """删除缓存项"""
        cache = CacheManager.get_cache(self._cache_name)
        return cache.delete(self._key)

    def exists(self) -> bool:
        """检查是否存在"""
        cache = CacheManager.get_cache(self._cache_name)
        return cache.exists(self._key)

    def __str__(self) -> str:
        """字符串表示"""
        return str(self.value)

    def __repr__(self) -> str:
        """调试表示"""
        return f"CacheVar('{self._key}' = {self.value})"


class CacheDict:
    """缓存字典 - 像字典一样使用"""

    def __init__(self, cache_name: str = None, ttl: Optional[float] = None):
        """
        初始化缓存字典
        
        Args:
            cache_name: 缓存实例名称
            ttl: 默认过期时间(秒)
        """
        self._cache_name = cache_name
        self._ttl = ttl

    def __getitem__(self, key: str) -> Any:
        """dict[key] - 获取值"""
        cache = CacheManager.get_cache(self._cache_name)
        result = cache.get(key)
        if result is None:
            raise KeyError(f"缓存中不存在键: {key}")
        return result

    def __setitem__(self, key: str, value: Any):
        """dict[key] = value - 设置值"""
        cache = CacheManager.get_cache(self._cache_name)
        cache.set(key, value, self._ttl)

    def __delitem__(self, key: str):
        """del dict[key] - 删除键"""
        cache = CacheManager.get_cache(self._cache_name)
        if not cache.delete(key):
            raise KeyError(f"缓存中不存在键: {key}")

    def __contains__(self, key: str) -> bool:
        """key in dict - 检查是否存在"""
        cache = CacheManager.get_cache(self._cache_name)
        return cache.exists(key)

    def get(self, key: str, default: Any = None) -> Any:
        """字典式get方法"""
        cache = CacheManager.get_cache(self._cache_name)
        result = cache.get(key)
        return result if result is not None else default

    def setdefault(self, key: str, default: Any) -> Any:
        """如果不存在则设置默认值"""
        if key not in self:
            self[key] = default
        return self[key]

    def keys(self) -> List[str]:
        """获取所有键"""
        cache = CacheManager.get_cache(self._cache_name)
        return cache.keys()

    def clear(self):
        """清空缓存"""
        cache = CacheManager.get_cache(self._cache_name)
        cache.clear()
