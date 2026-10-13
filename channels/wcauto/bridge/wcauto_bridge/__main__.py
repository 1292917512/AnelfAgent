"""桥服务入口：python -m wcauto_bridge [--config wcauto_bridge.json] [--doctor]。

Windows 部署见 channels/wcauto/docs/deployment-windows.md。
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from .config import load_config
from .server import HEALTH_PATH, WS_PATH, WcautoBridgeServer


def main() -> int:
    parser = argparse.ArgumentParser(prog="wcauto_bridge", description="wcauto 微信桥服务")
    parser.add_argument("--config", default="wcauto_bridge.json", help="配置文件路径")
    parser.add_argument("--host", default="", help="监听地址覆盖")
    parser.add_argument("--port", type=int, default=0, help="监听端口覆盖")
    parser.add_argument("--token", default="", help="访问令牌覆盖")
    parser.add_argument("--doctor", action="store_true", help="只做自检并退出")
    args = parser.parse_args()

    cfg = load_config(args.config if args.config else "")
    if args.host:
        cfg.host = args.host
    if args.port:
        cfg.port = args.port
    if args.token:
        cfg.token = args.token

    if cfg.host not in ("127.0.0.1", "localhost", "::1") and not cfg.token:
        print(f"拒绝启动：监听非回环地址 {cfg.host} 但未配置 token"
              "（任何主机都可调用你的微信）。请配置 token 或改用 127.0.0.1。")
        return 2

    from .backend import WeChatBackend

    events_printed = []

    def _emit(event: str, data: dict) -> None:
        events_printed.append(event)

    if args.doctor:
        backend = WeChatBackend(cfg, _emit)
        try:
            backend.start()
            import json as _json
            print(_json.dumps(backend.doctor(), ensure_ascii=False, indent=2))
            return 0
        except Exception as exc:
            print(f"自检失败: {exc}")
            return 1
        finally:
            try:
                backend.stop()
            except Exception:
                pass

    async def _run() -> None:
        server = WcautoBridgeServer(cfg)
        await server.start()
        print(f"wcauto 桥已启动: ws://{cfg.host}:{cfg.port}{WS_PATH}"
              f"（健康检查: http://{cfg.host}:{cfg.port}{HEALTH_PATH}"
              f"，认证: {'token' if cfg.token else '仅回环'}）")
        print("按 Ctrl+C 停止。")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass
        finally:
            await server.stop()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
