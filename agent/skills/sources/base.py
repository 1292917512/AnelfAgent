"""外部技能源抽象 — 技能系统的可插拔来源接口。

核心技能系统（store / matcher / curator）不感知任何外部源的存在；
新增来源只需在本包放入实现 `SkillSource` 的模块并在 `sources/__init__.py`
登记，删除模块文件即完成卸载，核心零改动。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple


@dataclass
class ExternalSkill:
    """外部技能源中的技能条目（跨源统一视图）。"""

    name: str
    slug: str
    namespace: str = ""
    description: str = ""
    category: str = ""
    downloads: int = 0
    installs: int = 0
    requires_api_key: bool = False
    homepage: str = ""
    source: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class InstallResult:
    """外部技能安装结果。"""

    ok: bool
    path: str = ""
    error: str = ""
    hint: str = ""


class SkillSource(ABC):
    """外部技能源接口（如 SkillHub 商店）。

    实现约定：
    - 模块级提供 `get_source() -> SkillSource` 工厂函数供注册表发现；
    - `search` 参数非法时抛 ValueError（映射为 PARAM 错误），网络等故障抛其他异常；
    - `install` 必须将技能落盘为平铺结构 `<skills_dir>/<slug>/SKILL.md`。
    """

    key: str = ""
    display_name: str = ""
    description: str = ""
    categories: Tuple[str, ...] = ()

    def is_available(self) -> bool:
        """源当前是否可用（轻量检查，须快速返回）。"""
        return True

    def install_hint(self) -> str:
        """安装不可用时的引导信息（如 CLI 安装命令）。"""
        return ""

    @abstractmethod
    async def search(self, query: str, category: str = "", top_k: int = 5) -> List[ExternalSkill]:
        """搜索源内技能。"""

    @abstractmethod
    def install(self, slug: str, namespace: str, skills_dir: Path) -> InstallResult:
        """安装技能到本地技能库目录。"""
