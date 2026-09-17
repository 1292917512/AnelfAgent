"""操作注册表 — 内置桌面动作与注册的 MCP 操作的统一目录。

操作（operation）= 一个可执行、可注释、可停用的能力单元。两种来源：
- 内置桌面动作（DESKTOP_ACTIONS，代码定义，不可删除）；
- MCP 工具注册的操作（存实体目录 operations.json，可增删）——把已连接
  server 的某个工具提升为带注释的一等操作，供 AI/用户语义化调用。

注释与停用对两类操作一视同仁（override 持久化）；存储为单文件 JSON，
线程锁 + 原子写（provider_keys 同款纪律）。
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.log import log
from core.path import ConfigPaths

KIND_DESKTOP = "desktop"
KIND_MCP = "mcp"


@dataclass
class OperationSpec:
    """一个操作单元的目录信息。"""

    id: str
    """desktop.*（内置）/ mcp.{server}.{工具名}（注册）。"""
    kind: str
    title: str
    description: str
    """参数说明（内置动作硬编码；MCP 取注册时的工具描述快照）。"""
    annotation: str = ""
    """注释（用户/AI 可改，注入上下文供语义化调用）。"""
    enabled: bool = True
    server: str = ""
    """MCP 操作所属 server 名。"""
    tool: str = ""
    """MCP 操作对应的注册工具名（桥的 call_tool 入口）。"""
    params: List[Dict[str, Any]] = field(default_factory=list)
    """MCP 工具的参数 schema 快照（注册时落盘，执行侧展示用）。"""
    removable: bool = True
    """内置动作为 False。"""


@dataclass(frozen=True)
class DesktopAction:
    """内置桌面动作定义（desktop 执行器的目录面）。"""

    id: str
    title: str
    description: str


DESKTOP_ACTIONS: List[DesktopAction] = [
    DesktopAction(
        "desktop.click", "单击",
        "x/y（必填）、button（left/right/middle，默认 left）",
    ),
    DesktopAction("desktop.double_click", "双击", "x/y（必填）"),
    DesktopAction("desktop.right_click", "右击", "x/y（必填）"),
    DesktopAction("desktop.move", "移动鼠标", "x/y（必填）、duration（滑动时长秒）"),
    DesktopAction("desktop.drag", "拖拽", "从 x/y 拖到 x2/y2（均必填）、duration"),
    DesktopAction(
        "desktop.scroll", "滚动",
        "amount（正数向上/负数向下）、x/y（可选，缺省在当前位置）",
    ),
    DesktopAction(
        "desktop.type", "输入文本",
        "text（仅 ASCII——pyautogui 限制；中文等非 ASCII 请先写入剪贴板再 "
        "desktop.hotkey 粘贴）、interval（逐字间隔秒）",
    ),
    DesktopAction("desktop.hotkey", "组合键", "keys（如 \"ctrl+s\"、\"ctrl+shift+t\"）"),
    DesktopAction("desktop.key", "单键", "keys（如 \"enter\"、\"escape\"、\"down\"）"),
]

_STORE_LOCK = threading.Lock()


def _store_path() -> Path:
    return Path(__file__).resolve().parent / "operations.json"


def _legacy_store_path() -> Path:
    """历史存储位置（config/operations.json，实体化前的落点）。"""
    return Path(ConfigPaths.APP_CONFIG).resolve().parent / "operations.json"


def _migrate_legacy_store() -> None:
    """一次性迁移：新文件不存在而历史文件存在时拷贝内容（不删源，幂等）。"""
    target = _store_path()
    if target.exists():
        return
    legacy = _legacy_store_path()
    try:
        if legacy.resolve() == target.resolve():
            return
        data = json.loads(legacy.read_text("utf-8"))
        if not isinstance(data, dict):
            return
        _save_store(data)
        log("操作存储已从 config/operations.json 迁移到实体目录", "INFO", tag="操作")
    except FileNotFoundError:
        return
    except Exception as exc:
        log(f"操作存储历史迁移失败（忽略，从空目录开始）: {exc}", "WARNING", tag="操作")


def _load_store() -> Dict[str, Any]:
    p = _store_path()
    try:
        data = json.loads(p.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        log(f"操作存储加载失败: {exc}", "WARNING", tag="操作")
        return {}


def _save_store(data: Dict[str, Any]) -> None:
    p = _store_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
        tmp.replace(p)
    except Exception as exc:
        log(f"操作存储保存失败: {exc}", "WARNING", tag="操作")


def _mcp_specs(raw: Any) -> List[OperationSpec]:
    """从存储段解析 MCP 操作（容错：坏条目跳过并告警）。"""
    specs: List[OperationSpec] = []
    if not isinstance(raw, list):
        return specs
    for item in raw:
        try:
            specs.append(OperationSpec(
                id=str(item["id"]), kind=KIND_MCP,
                title=str(item.get("title") or item["id"]),
                description=str(item.get("description", ""))[:400],
                annotation=str(item.get("annotation", "")),
                enabled=bool(item.get("enabled", True)),
                server=str(item["server"]), tool=str(item["tool"]),
                params=list(item.get("params") or [])[:16],
            ))
        except (KeyError, TypeError) as exc:
            log(f"MCP 操作条目损坏，已跳过: {exc}", "WARNING", tag="操作")
    return specs


def list_operations() -> List[OperationSpec]:
    """全部操作目录（内置在前，含注释/停用 override 与 MCP 注册项）。"""
    with _STORE_LOCK:
        data = _load_store()
    annotations = data.get("annotations") or {}
    disabled = set(data.get("disabled") or [])

    specs: List[OperationSpec] = []
    for action in DESKTOP_ACTIONS:
        specs.append(OperationSpec(
            id=action.id, kind=KIND_DESKTOP, title=action.title,
            description=action.description,
            annotation=str(annotations.get(action.id, "")),
            enabled=action.id not in disabled,
            removable=False,
        ))
    specs.extend(_mcp_specs(data.get("mcp_operations")))
    return specs


def get_operation(op_id: str) -> Optional[OperationSpec]:
    for spec in list_operations():
        if spec.id == op_id:
            return spec
    return None


def _mutate(mutator) -> None:
    with _STORE_LOCK:
        data = _load_store()
        mutator(data)
        _save_store(data)


def set_annotation(op_id: str, note: str) -> bool:
    """改注释（内置与 MCP 操作通用；空串清除）。"""
    if get_operation(op_id) is None:
        return False

    def _apply(data: Dict[str, Any]) -> None:
        annotations = data.setdefault("annotations", {})
        if note:
            annotations[op_id] = note
        else:
            annotations.pop(op_id, None)
        for item in data.get("mcp_operations") or []:
            if isinstance(item, dict) and item.get("id") == op_id:
                item["annotation"] = note

    _mutate(_apply)
    return True


def set_enabled(op_id: str, enabled: bool) -> bool:
    """启停操作（停用后不进上下文注入，AI 执行被拒）。"""
    if get_operation(op_id) is None:
        return False

    def _apply(data: Dict[str, Any]) -> None:
        disabled = set(data.get("disabled") or [])
        if enabled:
            disabled.discard(op_id)
        else:
            disabled.add(op_id)
        data["disabled"] = sorted(disabled)
        for item in data.get("mcp_operations") or []:
            if isinstance(item, dict) and item.get("id") == op_id:
                item["enabled"] = enabled

    _mutate(_apply)
    return True


def register_mcp_operation(
    *,
    server: str,
    tool: str,
    title: str = "",
    annotation: str = "",
    description: str = "",
    params: Optional[List[Dict[str, Any]]] = None,
) -> OperationSpec:
    """把一个 MCP 工具注册为操作（同 id 覆盖更新）。"""
    op_id = f"mcp.{server}.{tool.rsplit('__', 1)[-1]}"
    spec = OperationSpec(
        id=op_id, kind=KIND_MCP,
        title=title or tool.rsplit("__", 1)[-1],
        description=description[:400],
        annotation=annotation,
        enabled=True, server=server, tool=tool,
        params=(params or [])[:16],
    )

    def _apply(data: Dict[str, Any]) -> None:
        entries = [e for e in (data.get("mcp_operations") or [])
                   if isinstance(e, dict) and e.get("id") != op_id]
        entries.append(asdict(spec))
        data["mcp_operations"] = entries

    _mutate(_apply)
    return spec


def remove_operation(op_id: str) -> bool:
    """移除 MCP 注册操作（内置动作不可删）。"""
    spec = get_operation(op_id)
    if spec is None or not spec.removable:
        return False

    def _apply(data: Dict[str, Any]) -> None:
        data["mcp_operations"] = [
            e for e in (data.get("mcp_operations") or [])
            if not (isinstance(e, dict) and e.get("id") == op_id)
        ]

    _mutate(_apply)
    return True


# 导入期一次性迁移历史存储（真实路径执行，幂等；测试 monkeypatch 在其后不受影响）
_migrate_legacy_store()
