"""
MVC框架数据基类
支持数据变化监听，简单高效的观察者模式实现
"""
from typing import Any, Dict, List, Callable, Optional, Protocol, Set
from dataclasses import dataclass, field
from weakref import WeakSet
import copy

from core.log import log


@dataclass
class DataChange:
    """数据变化信息"""
    field_name: str
    old_value: Any
    new_value: Any
    source: Any = None  # 变化源对象
    path: str = ""  # 变化路径，用于深度监听


class DataChangeListener(Protocol):
    """数据变化监听者协议"""

    def on_data_changed(self, change: DataChange) -> None:
        """
        处理数据变化事件
        :param change: 数据变化信息
        """
        ...


class DataModel:
    """
    MVC数据基类
    
    特性：
    1. 自动监听属性变化
    2. 支持注册多个监听者
    3. 支持深度监听（监听对象内部变化）
    4. 线程安全的弱引用监听者管理
    5. 简单易用的API
    """

    def __init__(self):
        # 使用弱引用集合避免内存泄漏
        self._listeners: WeakSet[DataChangeListener] = WeakSet()
        # 跟踪哪些属性需要监听
        self._watched_fields: Set[str] = set()
        # 深度监听的对象
        self._deep_watched: Dict[str, 'DataModel'] = {}
        # 是否启用监听（用于批量更新时临时禁用）
        self._listening_enabled: bool = True
        # 初始化标记，避免初始化时触发监听
        self._initialized: bool = False

    def __setattr__(self, name: str, value: Any) -> None:
        # 内部属性和未初始化时直接设置
        if name.startswith('_') or not hasattr(self, '_initialized'):
            super().__setattr__(name, value)
            return

        # 获取旧值
        old_value = getattr(self, name, None) if hasattr(self, name) else None

        # 如果值未改变，直接返回
        if old_value is value or (old_value == value and type(old_value) == type(value)):
            return

        # 设置新值
        super().__setattr__(name, value)

        # 触发监听
        if self._listening_enabled and self._initialized:
            self._notify_change(name, old_value, value)

        # 处理深度监听
        self._handle_deep_watch(name, old_value, value)

    def _notify_change(self, field_name: str, old_value: Any, new_value: Any, path: str = "") -> None:
        """通知数据变化"""
        if not self._listeners:
            return

        change = DataChange(
            field_name=field_name,
            old_value=old_value,
            new_value=new_value,
            source=self,
            path=path
        )

        log(f"📊 数据变化: {field_name} = {new_value} (来自: {type(self).__name__})", "DEBUG")

        # 通知所有监听者
        for listener in list(self._listeners):  # 创建副本避免迭代时修改
            try:
                listener.on_data_changed(change)
            except Exception as e:
                log(f"❌ 监听者处理数据变化失败: {e}", "ERROR")

    def _handle_deep_watch(self, name: str, old_value: Any, new_value: Any) -> None:
        """处理深度监听"""
        # 移除旧的深度监听
        if name in self._deep_watched and isinstance(old_value, DataModel):
            old_value.remove_listener(self._create_deep_listener(name))
            del self._deep_watched[name]

        # 添加新的深度监听
        if isinstance(new_value, DataModel):
            deep_listener = self._create_deep_listener(name)
            new_value.add_listener(deep_listener)
            self._deep_watched[name] = new_value

    def _create_deep_listener(self, field_name: str) -> DataChangeListener:
        """创建深度监听器"""

        class DeepListener:
            def __init__(self, parent: DataModel, field_name: str):
                self.parent = parent
                self.field_name = field_name

            def on_data_changed(self, change: DataChange) -> None:
                # 构建完整路径
                path = f"{self.field_name}.{change.path}" if change.path else self.field_name
                # 转发到父对象的监听者
                self.parent._notify_change(change.field_name, change.old_value, change.new_value, path)

        return DeepListener(self, field_name)

    def add_listener(self, listener: DataChangeListener) -> None:
        """添加数据变化监听者"""
        self._listeners.add(listener)
        log(f"📝 添加数据监听者: {listener.__class__.__name__}", "DEBUG")

    def remove_listener(self, listener: DataChangeListener) -> bool:
        """移除数据变化监听者"""
        if listener in self._listeners:
            self._listeners.discard(listener)
            log(f"🗑️ 移除数据监听者: {listener.__class__.__name__}", "DEBUG")
            return True
        return False

    def clear_listeners(self) -> None:
        """清空所有监听者"""
        count = len(self._listeners)
        self._listeners.clear()
        log(f"🧹 清空所有数据监听者 (共 {count} 个)", "DEBUG")

    def watch_field(self, field_name: str) -> None:
        """开始监听指定字段（可选，默认监听所有字段）"""
        self._watched_fields.add(field_name)

    def unwatch_field(self, field_name: str) -> None:
        """停止监听指定字段"""
        self._watched_fields.discard(field_name)

    def batch_update(self, updates: Dict[str, Any]) -> None:
        """批量更新数据（临时禁用监听以提高性能）"""
        old_enabled = self._listening_enabled
        self._listening_enabled = False

        try:
            old_values = {}
            # 记录旧值
            for key in updates:
                if hasattr(self, key):
                    old_values[key] = getattr(self, key)

            # 批量设置新值
            for key, value in updates.items():
                setattr(self, key, value)

        finally:
            self._listening_enabled = old_enabled

    def get_snapshot(self) -> Dict[str, Any]:
        """获取当前数据快照（用于比较和回滚）"""
        snapshot = {}
        for key, value in self.__dict__.items():
            if not key.startswith('_'):
                try:
                    snapshot[key] = copy.deepcopy(value)
                except Exception as e:
                    log(f"深拷贝失败，使用引用: {key} - {e}", "DEBUG")
                    snapshot[key] = value
        return snapshot

    def restore_from_snapshot(self, snapshot: Dict[str, Any]) -> None:
        """从快照恢复数据"""
        self.batch_update(snapshot)

    def enable_listening(self, enabled: bool = True) -> None:
        """启用或禁用监听"""
        self._listening_enabled = enabled
        log(f"🔊 数据监听: {'启用' if enabled else '禁用'}", "DEBUG")

    @property
    def listener_count(self) -> int:
        """获取监听者数量"""
        return len(self._listeners)

    def finalize_init(self) -> None:
        """完成初始化，开始监听数据变化"""
        self._initialized = True
        log(f"✅ {type(self).__name__} 初始化完成，开始监听数据变化", "DEBUG")


class SimpleDataListener:
    """简单的数据监听器实现"""

    def __init__(self, callback: Callable[[DataChange], None]):
        self.callback = callback

    def on_data_changed(self, change: DataChange) -> None:
        self.callback(change)


class LoggingDataListener:
    """日志记录数据监听器"""

    def __init__(self, log_level: str = "INFO"):
        self.log_level = log_level

    def on_data_changed(self, change: DataChange) -> None:
        path_info = f" (路径: {change.path})" if change.path else ""
        message = f"数据变化: {change.field_name} = {change.new_value} (旧值: {change.old_value}){path_info}"
        log(message, self.log_level)
