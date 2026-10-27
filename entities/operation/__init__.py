"""操作实体 — 桌面操控与 MCP 操作注册的统一目录、执行与态势注入。

模块划分：
- framework.py：操作注册表（内置桌面动作 + MCP 注册操作）与注释/启停
  持久化（实体目录 operations.json）；
- desktop.py：桌面独占与接管；desktop_process/worker/actions：受管子进程与可中断输入；
- executor.py：统一执行入口、执行历史、MCP 网关（entities.mcp 桥直连）；
- context.py：操作态势上下文提供者（可用操作 + 注释 + MCP 概览注入）；
- tools.py：AI 工具面（operation 组）；
- service.py / router.py：Web 面服务与路由（/api/entity/operation）。

目录名 / group 名 / 面板名 / 路由名统一为 operation，框架各发现机制自然对齐。
看屏定位用视觉组既有工具（vision_look，经 _sdk 桥），操作只负责"做"。
"""

from core.config import register_configs_safe
from entities._sdk import entity, entity_manifest

entity("operation", "操作 - 桌面操控（desktop_act，看屏用 vision_look）与 MCP 操作注册/注释/语义化执行")

entity_manifest(
    display_name="桌面操作",
    icon="MousePointerClick",
    description="桌面操控（pyautogui 九动作 + 看屏验证联动）与 MCP 操作关联的"
                "统一目录、执行与态势注入，AI 与用户共用",
    version="1.0.0",
    order=35,
    nav={"path": "/entities/operation", "label": "operation", "nav_group": "group_ability"},
    group="operation",
)

# 实体配置项：分组名 entity/operation，实体详情页配置 tab 自动展示
register_configs_safe({
    "entity/operation": {
        "operation_enabled": {
            "description": "是否允许执行桌面操控动作（高危总开关；急停可鼠标猛移屏幕左上角）",
            "default": True,
        },
        "operation_context_window_seconds": {
            "description": "操作活跃窗口（秒）：窗口内有操作执行才注入操作态势上下文，"
                           "平时零注入；每次执行滑动续期",
            "default": 600, "unit": "秒", "min": 60, "max": 7200, "advanced": True,
        },
        "operation_desktop_verify": {
            "description": "桌面动作执行后自动看屏验证（联动视觉 screen 源，动作结果附最新"
                           "画面帧；AI 可用 desktop_act 的 verify 参数逐次覆盖）",
            "default": True,
        },
        "operation_action_timeout_seconds": {
            "description": "桌面动作及回看总预算；超时先请求释放输入，再回收工作进程",
            "default": 30, "min": 5, "max": 120, "unit": "秒", "advanced": True,
        },
        "operation_user_takeover": {
            "description": "在支持的平台检测用户物理输入并停止桌面动作（当前支持 Windows）；"
                           "其他平台仍可在桌面操作页显式接管",
            "default": True,
        },
    },
})


def register_lifecycle() -> None:
    """关停先封闭桌面入口，待拥有的输入进程释放后结束。"""
    from core.lifecycle import Lifecycle

    from .desktop import get_controller
    controller = get_controller()
    Lifecycle.register("desktop_controller", controller, cleanup=controller.close,
                       on_quiesce=controller.quiesce, critical_cleanup=True)

from . import context, tools  # noqa: F401, E402  # 注册上下文提供者 + 触发 @tool 注册
