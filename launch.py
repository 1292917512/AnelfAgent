import argparse
import asyncio
import contextlib
import faulthandler
import signal
import warnings

warnings.filterwarnings("ignore", message="urllib3.*doesn't match a supported version")

from core.config import ConfigManager
from core.log import enable_file_logging, level_emoji, log, set_log_level


def _setup_faulthandler() -> None:
    """启用故障转储：致命错误自动打印堆栈；运行期可 kill -USR1 <pid> 在线抓全线程栈。"""
    faulthandler.enable()
    with contextlib.suppress(AttributeError, ValueError, OSError, RuntimeError):
        faulthandler.register(signal.SIGUSR1, all_threads=True)


def main():
    parser = argparse.ArgumentParser(description='AnelfAgent')
    parser.add_argument('--log-level', choices=level_emoji.keys(), default='DEBUG')
    parser.add_argument('--no-webui', action='store_true', help='不启动 WebUI')
    args = parser.parse_args()
    set_log_level(args.log_level)
    _setup_faulthandler()

    async def _run():
        ConfigManager.initialize()
        enable_file_logging()

        from agent.runtime.bootstrap import create_bootstrap
        await create_bootstrap().execute()

        # 触发所有 Lifecycle 注册的 on_start 钩子（实体自治的启动钩子入口）
        from core.lifecycle import Lifecycle
        await Lifecycle.start_all()

        # 初始化统一权限机制：加载规则 + 启动热更新监听
        # （新格式 config/permission_rules.json 优先，旧 approval_policies.json 自动转换）
        import os

        from agent.approval import get_approval_gate
        from agent.approval.rules import LEGACY_PATH, RULES_PATH
        from agent.channel.config_watcher import get_config_watcher
        gate = get_approval_gate()
        watcher = get_config_watcher()
        watched = False
        for path in (RULES_PATH, LEGACY_PATH):
            if os.path.exists(path):
                gate.reload_rules(path)
                watcher.watch(path, lambda p=path: gate.reload_rules(p))
                log(f"权限规则热更新监听已启动: {path}", tag="权限")
                watched = True
                break
        if watched:
            log(f"权限规则已加载 ({len(gate.get_rule_set().rules)} 条)", tag="权限")
        else:
            log("权限规则文件不存在，使用默认（全部放行）", "WARNING", tag="权限")

        # 尽早拉起 WebUI：HTTP 端口不再等待频道网络登录。
        # 频道支持运行期热启动/停止，且有看门狗退避重启兜底，后台启动安全。
        web_task: asyncio.Task[None] | None = None
        if not args.no_webui:
            from web.server import start_web_server
            web_task = asyncio.create_task(
                start_web_server(), name="agent.web_server",
            )

        # 频道后台并发启动（各自网络登录耗时不阻塞主流程）
        from agent.channel import get_channel_manager

        async def _start_channels() -> None:
            try:
                await get_channel_manager().start_all()
                log("全部频道启动流程完成", tag="启动")
            except Exception as exc:
                log(f"频道后台启动异常: {exc}", "ERROR", tag="启动")

        channels_task = asyncio.create_task(_start_channels(), name="agent.channels_start")

        # 频道看门狗：ERROR 频道自动退避重启（先启动，覆盖整个运行期）
        from agent.channel.supervision import is_supervisor_enabled, start_channel_supervisor
        if is_supervisor_enabled():
            start_channel_supervisor(get_channel_manager())

        shutdown_event = asyncio.Event()
        loop = asyncio.get_running_loop()

        def _request_shutdown() -> None:
            if not shutdown_event.is_set():
                shutdown_event.set()

        # 供 Web API 等运行期入口触发优雅关闭/重启（core.lifecycle.Lifecycle.request_shutdown）
        Lifecycle.set_shutdown_requester(_request_shutdown)

        def _on_signal() -> None:
            if shutdown_event.is_set():
                for s in (signal.SIGINT, signal.SIGTERM):
                    with contextlib.suppress(NotImplementedError, ValueError, RuntimeError):
                        loop.remove_signal_handler(s)
                return
            _request_shutdown()

        # Windows ProactorEventLoop 不支持 add_signal_handler
        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, _on_signal)
        except NotImplementedError:
            def _win_handler(signum: int, frame: object) -> None:
                loop.call_soon_threadsafe(_request_shutdown)

            for sig in (signal.SIGINT, signal.SIGTERM):
                with contextlib.suppress(ValueError, OSError):
                    signal.signal(sig, _win_handler)

        await shutdown_event.wait()

        log("正在关闭...")

        # 记忆自动捕获兜底：退出前强制提取各会话待定内容（限时 30s，不阻塞关停）
        try:
            from agent.memory.auto_capture import flush_auto_capture
            from services._runtime import require_runtime
            await asyncio.wait_for(
                flush_auto_capture(require_runtime().mind), timeout=30.0,
            )
        except Exception:
            pass

        import logging
        logging.getLogger("uvicorn.error").disabled = True
        loop.set_exception_handler(lambda _l, _c: None)

        if web_task and not web_task.done():
            from web.server import request_web_shutdown
            request_web_shutdown()

        try:
            from entities.mcp.bridge import get_mcp_bridge
            bridge = get_mcp_bridge()
            if bridge:
                bridge.shutdown()
        except (OSError, RuntimeError):
            pass
        try:
            # 先停看门狗，防止关停过程中误判 ERROR 触发重启
            from agent.channel.supervision import stop_channel_supervisor
            await stop_channel_supervisor()
        except Exception:
            pass
        if not channels_task.done():
            # 关停时若频道仍在后台启动，先取消，避免与 stop_all 竞态
            channels_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await channels_task
        try:
            await get_channel_manager().stop_all()
        except BaseException:
            pass
        try:
            from core.lifecycle import Lifecycle
            await Lifecycle.shutdown_all()
        except Exception:
            pass

        if web_task:
            if not web_task.done():
                web_task.cancel()
            try:
                await web_task
            except (asyncio.CancelledError, Exception):
                pass

    try:
        asyncio.run(_run())
    except (KeyboardInterrupt, SystemExit):
        pass

    from core.lifecycle import RESTART_EXIT_CODE, Lifecycle
    if Lifecycle.restart_requested():
        log(f"收到重启请求，以退出码 {RESTART_EXIT_CODE} 退出（由外层启动脚本重新拉起）", tag="重启")
        # 第三方库可能残留非守护线程（aiosqlite 等），SystemExit 会使解释器关闭时
        # 永久阻塞在 threading._shutdown 的 join 上；此处业务资源已清理完毕，直接退出
        import os
        import sys
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(RESTART_EXIT_CODE)


if __name__ == "__main__":
    main()
