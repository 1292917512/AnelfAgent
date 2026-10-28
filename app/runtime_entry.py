"""桌面组合入口：旧检出也接入同一 HostControl，源码和生命周期保持由项目持有。"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.application import Application


def main() -> None:
    """通过原 launch.main 启动；只为缺少桌面控制的检出补注册公共控制服务。"""
    root = Path(os.environ["ANELF_PROJECT_ROOT"]).resolve()
    backend = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    import core
    # 优先使用项目 core；只有旧检出缺失的公共控制模块来自随包代码。
    core.__path__.append(str(backend / "core"))
    import launch
    from core.lifecycle import Lifecycle

    create_application = launch.create_application

    def create(args: argparse.Namespace) -> Application:
        application = create_application(args)

        @application.startup.node(skip_on_error=False, depends_on=["acquire_single_instance", "run_bootstrap",
                                     *([] if args.no_webui else ["register_web_service"])])
        async def connect_desktop_control() -> None:
            if Lifecycle.get("host_control") is not None:
                return
            from core.host_control import register_host_control

            def restart() -> dict[str, object]:
                from entities.devops.service import request_restart
                return request_restart(source="launcher")

            def web_url() -> str:
                if args.no_webui:
                    return ""
                host = os.environ.get("ANELF_WEBUI_HOST", "127.0.0.1")
                host = "127.0.0.1" if host == "0.0.0.0" else "::1" if host == "::" else host
                port = os.environ.get("ANELF_WEBUI_PORT", "8092")
                return f"http://{'[' + host + ']' if ':' in host else host}:{port}/webui/"

            if not args.no_webui:
                from web import server
                load_config = server._load_server_config

                def server_config() -> tuple[str, int]:
                    host, port = load_config()
                    return os.environ.get("ANELF_WEBUI_HOST", host), int(os.environ.get("ANELF_WEBUI_PORT", port))

                server._load_server_config = server_config
            register_host_control(root, restart=restart, web_url=web_url)

        return application

    launch.create_application = create
    launch.main()


if __name__ == "__main__":
    main()
