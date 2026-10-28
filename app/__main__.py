"""网页与命令行共用的管理入口；Electron 仅承载同一网页。"""

from __future__ import annotations

import argparse
import json
import sys
import time
import webbrowser
from pathlib import Path

from app.client import Client
from app.settings import Layout


def _local_control(layout: Layout, action: str) -> dict[str, object]:
    """直接读取或控制现有运行时，不为停止、重启和查状态启动管理服务。"""
    from app.manager import Manager
    manager = Manager(layout)
    if action == "status":
        return manager.status()
    manager.submit(action)
    while manager.status()["busy"]:
        time.sleep(0.1)
    result = manager.status()["last"]
    if not isinstance(result, dict):
        raise RuntimeError("Local control returned no result")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Anelf environment installer and supervisor")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="AnelfAgent checkout or empty install directory")
    sub = parser.add_subparsers(dest="command")
    transfer = sub.add_parser("migrate")
    transfer.add_argument("--target", type=Path, required=True)
    transfer.add_argument("--data", type=Path, required=True)
    transfer.add_argument("--cancel-file", type=Path, required=True)
    recovery = sub.add_parser("recover-migration")
    recovery.add_argument("--data", type=Path, required=True)
    for name in ("serve", "web", "status", "doctor", "setup", "install", "update", "start", "stop", "restart", "shutdown"):
        command = sub.add_parser(name)
        if name in {"serve", "web"}:
            command.add_argument("--port", type=int, default=8090)
        if name in {"setup", "install"}:
            command.add_argument("--source", choices=["auto", "github", "gitee"], default=None)
    args = parser.parse_args(argv)
    try:
        if args.command in {"migrate", "recover-migration"}:
            from app.migration import migrate, recover
            last_report = 0.0
            last_stage = ""

            def report(message: str, received: int, total: int) -> None:
                nonlocal last_report, last_stage
                now, stage = time.monotonic(), message.split(" · ", 1)[0]
                if stage == last_stage and received != total and now - last_report < 0.1:
                    return
                last_report, last_stage = now, stage
                print(json.dumps({"message": message, "received": received, "total": total}, ensure_ascii=False), flush=True)

            root = (recover(args.data) if args.command == "recover-migration" else
                    migrate(args.root, args.target, args.data, report, args.cancel_file))
            print(json.dumps({"root": root}, ensure_ascii=False), flush=True)
            return 0
        layout = Layout(args.root)
        if args.command == "serve":
            if not 0 <= args.port <= 65535:
                raise ValueError("Invalid management port")
            from app.server import serve
            serve(layout, args.port)
            return 0
        if args.command in {"status", "stop", "restart"}:
            result = _local_control(layout, args.command)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1 if result.get("ok") is False else 0
        client = Client(layout)
        client.ensure(getattr(args, "port", 8090))
        if args.command in {None, "web"}:
            webbrowser.open(client.browser_url)
            return 0
        if args.command == "doctor":
            result = client.request("/api/environment")
        elif args.command == "shutdown":
            result = client.request("/api/shutdown", {})
        else:
            body = {"action": args.command}
            if args.command in {"setup", "install"} and args.source is not None:
                body["source"] = args.source
            client.request("/api/action", body)
            while True:
                state = client.request("/api/status")
                if not state["busy"]:
                    result = state["last"]
                    break
                time.sleep(0.5)
        if sys.stdout is not None:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if isinstance(result, dict) and result.get("ok") is False else 0
    except (OSError, RuntimeError, ValueError) as exc:
        if sys.stderr is not None:
            print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
