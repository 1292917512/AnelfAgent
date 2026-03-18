import asyncio
import argparse
import warnings

warnings.filterwarnings("ignore", message="urllib3.*doesn't match a supported version")

from core.log import set_log_level, level_emoji, log, enable_file_logging
from core.config import ConfigManager


def main():
    parser = argparse.ArgumentParser(description='AnelfAgent')
    parser.add_argument('--log-level', choices=level_emoji.keys(), default='DEBUG')
    parser.add_argument('--no-webui', action='store_true', help='不启动 WebUI')
    args = parser.parse_args()
    set_log_level(args.log_level)

    async def _run():
        ConfigManager.initialize()
        enable_file_logging()

        from agent.runtime.bootstrap import create_bootstrap
        await create_bootstrap().execute()

        from agent.channel import get_channel_manager
        await get_channel_manager().start_all()

        if not args.no_webui:
            from web.server import start_web_server
            asyncio.create_task(start_web_server())

        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass

        log("正在关闭...")
        try:
            from entities.mcp.bridge import get_mcp_bridge
            bridge = get_mcp_bridge()
            if bridge:
                bridge.shutdown()
        except (OSError, RuntimeError):
            pass
        try:
            await get_channel_manager().stop_all()
        except BaseException:
            pass
        try:
            from core.lifecycle import Lifecycle
            await Lifecycle.shutdown_all()
        except Exception:
            pass

    try:
        asyncio.run(_run())
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
