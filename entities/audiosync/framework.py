"""音源同步组件框架 — 可插拔的外部音源来源注册表。

新增同步来源只需：定义 :class:`AudioSyncSource` 子类并加 ``@sync_source``
装饰器（``sources/`` 目录下新建文件即被扫描注册）。多个来源同时配置
就绪时按 priority 小值优先（如 OpenList 优先于本地目录）。

组件契约：
- ``is_configured``：来源是否配置就绪（未就绪不参与同步）；
- ``discover_units``：发现全部录制单元（文件夹/散装文件 → unit 字典）；
- ``fetch``：取回源文件本地路径（远程下载/挂载映射），is_temp=True
  表示临时文件（调用方负责删除）；
- ``check_status``：连通性体检（面板展示用）。

录制单元字典：{path, kind: folder|file, files: [{path,name,size,mtime_ns}],
fingerprint, started_ns}——watcher 侧统一组装（_make_unit），来源只负责
枚举文件条目。
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Optional, Tuple

_LOG_TAG = "音源同步"
_SOURCES: Dict[str, "AudioSyncSource"] = {}


class AudioSyncSource:
    """音源同步来源组件基类（声明类属性即接入框架）。"""

    key: ClassVar[str] = ""
    """来源全局唯一标识（小写英文）。"""
    display_name: ClassVar[str] = ""
    priority: ClassVar[int] = 50
    """多来源就绪时的选择顺序（小值优先）。"""

    def is_configured(self) -> bool:
        """来源是否配置就绪。"""
        return False

    def desc(self) -> str:
        """来源描述（状态/日志展示，如 local:/path 或 openlist:/root）。"""
        return self.key

    async def check_status(self) -> Dict[str, Any]:
        """连通性体检：{configured, reachable, latency_ms, error}。"""
        return {"configured": self.is_configured(), "reachable": False,
                "latency_ms": 0, "error": "组件未实现体检"}

    async def scan(self, exts: Tuple[str, ...], recursive: bool) -> List[Dict[str, Any]]:
        """扫描来源，返回录制单元候选（未组装的原始枚举）。

        Returns:
            [{path, kind: "folder"|"file", files: [{path, name, size, mtime_ns}]}]
            ——单元组装（可用文件过滤/指纹/时间解析）由 watcher 统一完成。
        """
        raise NotImplementedError

    async def fetch(self, path: str) -> Tuple[str, bool]:
        """取回源文件本地路径，返回 (local_path, is_temp)。"""
        raise NotImplementedError


def sync_source(cls: type[AudioSyncSource]) -> type[AudioSyncSource]:
    """注册音源同步来源组件（实例化入注册表；同 key 覆盖）。"""
    if not cls.key:
        raise ValueError(f"{cls.__name__} 缺少 key 声明")
    _SOURCES[cls.key] = cls()
    return cls


def all_sources() -> List[AudioSyncSource]:
    return sorted(_SOURCES.values(), key=lambda s: (s.priority, s.key))


def active_source() -> Optional[AudioSyncSource]:
    """解析当前生效的同步来源（priority 链首个配置就绪者）。"""
    for source in all_sources():
        try:
            if source.is_configured():
                return source
        except Exception:
            continue
    return None
