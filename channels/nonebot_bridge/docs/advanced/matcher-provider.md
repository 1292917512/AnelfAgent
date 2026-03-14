# 事件响应器存储

NoneBot 默认将所有事件响应器（Matcher）存储在内存中。通过自定义 `MatcherProvider`，你可以将 Matcher 存储到外部存储系统（如 Redis），实现持久化或分布式部署。

## 默认存储

默认情况下，NoneBot 使用内存字典存储所有 Matcher，按优先级分组：

```python
# 内部结构示意
matchers = {
    1: [matcher_a, matcher_b],   # 优先级 1
    5: [matcher_c],              # 优先级 5
    10: [matcher_d, matcher_e],  # 优先级 10
}
```

## MatcherProvider 抽象类

`MatcherProvider` 继承自 `MutableMapping[int, list[type[Matcher]]]`，定义了 Matcher 存储的接口。

### 接口定义

```python
from collections.abc import MutableMapping
from nonebot.matcher import Matcher

class MatcherProvider(MutableMapping[int, list[type[Matcher]]]):
    """事件响应器存储提供者"""

    def __getitem__(self, key: int) -> list[type[Matcher]]:
        """获取指定优先级的 Matcher 列表"""
        ...

    def __setitem__(self, key: int, value: list[type[Matcher]]) -> None:
        """设置指定优先级的 Matcher 列表"""
        ...

    def __delitem__(self, key: int) -> None:
        """删除指定优先级的 Matcher 列表"""
        ...

    def __iter__(self):
        """迭代所有优先级"""
        ...

    def __len__(self) -> int:
        """返回优先级组数量"""
        ...

    def __contains__(self, key: object) -> bool:
        """检查是否存在指定优先级"""
        ...
```

### 必须实现的方法

| 方法 | 说明 |
|------|------|
| `__getitem__(priority)` | 根据优先级获取 Matcher 列表 |
| `__setitem__(priority, matchers)` | 设置指定优先级的 Matcher 列表 |
| `__delitem__(priority)` | 删除指定优先级的全部 Matcher |
| `__iter__()` | 迭代所有已注册的优先级 |
| `__len__()` | 返回已注册的优先级数量 |

## 设置自定义 Provider

使用 `matchers.set_provider()` 替换默认的存储实现：

```python
from nonebot.matcher import matchers

# 使用自定义 Provider
matchers.set_provider(MyCustomProvider)
```

> **注意**：`set_provider()` 接收的是**类**而非实例。NoneBot 会自动实例化该类。

## 自定义 Provider 示例

### 基本的自定义 Provider

```python
from collections import defaultdict
from collections.abc import Iterator
from nonebot.matcher import Matcher, MatcherProvider, matchers

class LoggingProvider(MatcherProvider):
    """带日志的 Matcher 存储"""

    def __init__(self) -> None:
        self._store: dict[int, list[type[Matcher]]] = defaultdict(list)

    def __getitem__(self, key: int) -> list[type[Matcher]]:
        return self._store[key]

    def __setitem__(self, key: int, value: list[type[Matcher]]) -> None:
        print(f"[MatcherProvider] 设置优先级 {key}: {len(value)} 个 Matcher")
        self._store[key] = value

    def __delitem__(self, key: int) -> None:
        print(f"[MatcherProvider] 删除优先级 {key}")
        del self._store[key]

    def __iter__(self) -> Iterator[int]:
        return iter(sorted(self._store.keys()))

    def __len__(self) -> int:
        return len(self._store)

    def __contains__(self, key: object) -> bool:
        return key in self._store

# 应用
matchers.set_provider(LoggingProvider)
```

### Redis 存储 Provider

以下是一个使用 Redis 存储 Matcher 信息的概念示例：

```python
import pickle
from collections.abc import Iterator
from nonebot.matcher import Matcher, MatcherProvider, matchers

class RedisMatcherProvider(MatcherProvider):
    """基于 Redis 的 Matcher 存储

    注意：Matcher 类型包含函数引用，实际序列化需要特殊处理。
    此示例主要展示 Provider 的接口设计思路。
    """

    REDIS_KEY = "nonebot:matchers"

    def __init__(self) -> None:
        import redis.asyncio as redis
        self._redis = redis.from_url("redis://localhost:6379/0")
        self._local_cache: dict[int, list[type[Matcher]]] = {}

    def __getitem__(self, key: int) -> list[type[Matcher]]:
        return self._local_cache.get(key, [])

    def __setitem__(self, key: int, value: list[type[Matcher]]) -> None:
        self._local_cache[key] = value

    def __delitem__(self, key: int) -> None:
        self._local_cache.pop(key, None)

    def __iter__(self) -> Iterator[int]:
        return iter(sorted(self._local_cache.keys()))

    def __len__(self) -> int:
        return len(self._local_cache)

    def __contains__(self, key: object) -> bool:
        return key in self._local_cache

matchers.set_provider(RedisMatcherProvider)
```

### 带过期清理的 Provider

```python
import time
from collections import defaultdict
from collections.abc import Iterator
from nonebot.matcher import Matcher, MatcherProvider, matchers

class CleaningProvider(MatcherProvider):
    """自动清理过期临时 Matcher 的存储"""

    def __init__(self) -> None:
        self._store: dict[int, list[type[Matcher]]] = defaultdict(list)
        self._last_cleanup = time.time()
        self._cleanup_interval = 60  # 每60秒清理一次

    def _maybe_cleanup(self) -> None:
        now = time.time()
        if now - self._last_cleanup < self._cleanup_interval:
            return

        self._last_cleanup = now
        for priority in list(self._store.keys()):
            original = self._store[priority]
            cleaned = [
                m for m in original
                if not (m.temp and m.expire_time and m.expire_time.timestamp() < now)
            ]
            if cleaned:
                self._store[priority] = cleaned
            else:
                del self._store[priority]

    def __getitem__(self, key: int) -> list[type[Matcher]]:
        self._maybe_cleanup()
        return self._store[key]

    def __setitem__(self, key: int, value: list[type[Matcher]]) -> None:
        self._store[key] = value

    def __delitem__(self, key: int) -> None:
        del self._store[key]

    def __iter__(self) -> Iterator[int]:
        self._maybe_cleanup()
        return iter(sorted(self._store.keys()))

    def __len__(self) -> int:
        return len(self._store)

    def __contains__(self, key: object) -> bool:
        return key in self._store

matchers.set_provider(CleaningProvider)
```

## 获取当前 Provider

```python
from nonebot.matcher import matchers

# matchers 本身就是 MatcherProvider 实例
print(type(matchers.provider))

# 遍历所有 Matcher
for priority in matchers:
    matcher_list = matchers[priority]
    print(f"优先级 {priority}: {len(matcher_list)} 个 Matcher")
    for m in matcher_list:
        print(f"  - {m.plugin_name}: type={m.type}")
```

## 注意事项

1. **设置时机**：`set_provider()` 应在 NoneBot 初始化后、加载插件前调用
2. **线程安全**：自定义 Provider 需要考虑并发访问的安全性
3. **序列化限制**：Matcher 类包含函数引用，无法直接序列化；外部存储通常只存储元信息
4. **性能考虑**：Matcher 查找是高频操作，Provider 的 `__getitem__` 应尽量高效
5. **排序保证**：`__iter__` 应返回排序后的优先级，确保 Matcher 按优先级顺序执行
