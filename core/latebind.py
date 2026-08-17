"""进程级晚绑定端口 — 类型化的运行时引用分发原语。

供"构造注入不可行"的场景使用，准入规则见 AGENTS.md 开发约定「晚绑定」：

1. import 时装饰器注册的工具拿不到构造参数（如思维工具组）
2. 循环初始化（Mind 构造激活工具组，工具又需要 Mind）
3. 跨层桥（entities 声明端口，agent 组合根施绑）

纪律：端口由消费方所在的层声明，``set()`` 仅允许组合根
（bootstrap / agent.runtime.wiring）调用；其余场景一律构造注入。

线程语义：set 只发生在 bootstrap（事件循环线程），get 可能来自
to_thread 工作线程，属性读写原子性由 GIL 保证，热路径无需加锁。
"""
from __future__ import annotations

from typing import Any, Dict, Generic, List, TypeVar

T = TypeVar("T")

# 端口名 -> 实例（声明序），供启动健康检查与测试复位遍历
_BINDINGS: Dict[str, "LateBinding[Any]"] = {}


class WireError(RuntimeError):
    """端口未施绑即被访问（携带端口名，供启动健康检查与日志定位）。"""

    def __init__(self, name: str) -> None:
        super().__init__(f"晚绑定端口 '{name}' 尚未施绑（bootstrap 接线前被访问）")
        self.name = name


class LateBinding(Generic[T]):
    """命名绑定单元：组合根 set / 消费者 get，未施绑访问 fail-fast。

    值存放在单元素盒中：空盒 = 未施绑，``[None]`` = 施绑了 None
    （可选后端初始化失败时 None 是合法绑定值，bound 标志即事实），
    无需类型断言即可保持 T 的静态类型。
    """

    __slots__ = ("_name", "_box")

    def __init__(self, name: str) -> None:
        if name in _BINDINGS:
            raise ValueError(f"晚绑定端口名称冲突: {name}")
        _BINDINGS[name] = self
        self._name = name
        self._box: List[T] = []

    @property
    def name(self) -> str:
        """端口名（诊断用）。"""
        return self._name

    @property
    def bound(self) -> bool:
        """是否已施绑。"""
        return bool(self._box)

    def set(self, value: T) -> None:
        """施绑/重绑（替换语义，对齐 Lifecycle.register）。"""
        self._box[:] = [value]

    def get(self) -> T:
        """取绑定值，未施绑抛 WireError。"""
        if not self._box:
            raise WireError(self._name)
        return self._box[0]

    def unbind(self) -> None:
        """解除绑定（测试复位用）。"""
        self._box.clear()


def assert_wired() -> List[str]:
    """返回尚未施绑的端口名清单（启动健康检查消费）。"""
    return [name for name, port in _BINDINGS.items() if not port.bound]


def reset_all() -> None:
    """复位全部端口（测试专用）。"""
    for port in _BINDINGS.values():
        port.unbind()
