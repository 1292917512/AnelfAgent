"""AnelfAgent 进程入口：启动流程 → 等待关停信号 → 关停流程。

启动与关停各自由一个 FlowMachine 节点序列承载（create_launch_flow /
create_shutdown_flow），节点日志即启动时间线。
"""
import argparse
import asyncio
import contextlib
import faulthandler
import logging
import os
import signal
import sys
import warnings
from dataclasses import dataclass

warnings.filterwarnings("ignore", message="urllib3.*doesn't match a supported version")

from core.config import ConfigManager
from core.flow import FlowMachine
from core.log import enable_file_logging, level_emoji, log, set_log_level


@dataclass
class _LaunchContext:
    """启动/关停流程间共享的上下文（后台任务、关停事件、事件循环）。"""

    args: argparse.Namespace
    loop: asyncio.AbstractEventLoop
    shutdown_event: asyncio.Event
    web_task: asyncio.Task[None] | None = None
    channels_task: asyncio.Task[None] | None = None


def _setup_faulthandler() -> None:
    """启用故障转储：致命错误自动打印堆栈；运行期可 kill -USR1 <pid> 在线抓全线程栈。"""
    faulthandler.enable()
    with contextlib.suppress(AttributeError, ValueError, OSError, RuntimeError):
        faulthandler.register(signal.SIGUSR1, all_threads=True)


def create_launch_flow(ctx: _LaunchContext) -> FlowMachine:
    """构建启动流程，任一节点失败即终止启动。"""
    machine = FlowMachine()

    @machine.node(skip_on_error=False)
    async def init_foundation() -> None:
        """初始化配置中心并开启文件日志。"""
        ConfigManager.initialize()
        enable_file_logging()

    @machine.node(skip_on_error=False)
    async def run_bootstrap() -> None:
        """执行运行时组装（agent/runtime/bootstrap.py 子流程）。"""
        from agent.runtime.bootstrap import create_bootstrap
        await create_bootstrap().execute()

    @machine.node(skip_on_error=False)
    async def start_lifecycle_hooks() -> None:
        """触发所有 Lifecycle 注册的 on_start 钩子。"""
        from core.lifecycle import Lifecycle
        await Lifecycle.start_all()

    @machine.node(skip_on_error=False)
    async def init_approval_rules() -> None:
        """加载权限规则并启动热更新监听。"""
        from agent.approval import get_approval_gate
        from agent.approval.rules import LEGACY_PATH, RULES_PATH
        from agent.channel.config_watcher import get_config_watcher

        gate = get_approval_gate()
        watcher = get_config_watcher()
        for path in (RULES_PATH, LEGACY_PATH):
            if os.path.exists(path):
                gate.reload_rules(path)
                watcher.watch(path, lambda p=path: gate.reload_rules(p))
                log(f"权限规则热更新监听已启动: {path}", tag="权限")
                log(f"权限规则已加载 ({len(gate.get_rule_set().rules)} 条)", tag="权限")
                return
        log("权限规则文件不存在，使用默认（全部放行）", "WARNING", tag="权限")

    @machine.node(skip_on_error=False)
    async def init_user_hooks() -> None:
        """加载 config/hooks.json 并启动热更新监听。"""
        from agent.channel.config_watcher import get_config_watcher
        from agent.hooks import reload_hooks
        from core.path import ConfigPaths

        hooks_path = str(ConfigPaths.HOOKS)
        if not os.path.exists(hooks_path):
            return
        reload_hooks(hooks_path)
        get_config_watcher().watch(hooks_path, lambda p=hooks_path: reload_hooks(p))
        log(f"用户 hooks 热更新监听已启动: {hooks_path}", tag="Hook")

    if not ctx.args.no_webui:
        @machine.node(skip_on_error=False)
        async def start_webui() -> None:
            """后台拉起 WebUI，不等待频道登录。"""
            from web.server import start_web_server
            ctx.web_task = asyncio.create_task(
                start_web_server(), name="agent.web_server",
            )

    @machine.node(skip_on_error=False)
    async def launch_channels() -> None:
        """后台并发启动全部频道。"""
        from agent.channel import get_channel_manager

        async def _start_all() -> None:
            try:
                await get_channel_manager().start_all()
                log("全部频道启动流程完成", tag="启动")
            except Exception as exc:
                log(f"频道后台启动异常: {exc}", "ERROR", tag="启动")

        ctx.channels_task = asyncio.create_task(_start_all(), name="agent.channels_start")

    from agent.channel.supervision import is_supervisor_enabled, start_channel_supervisor
    if is_supervisor_enabled():
        @machine.node(skip_on_error=False)
        async def start_channel_watchdog() -> None:
            """启动频道看门狗（ERROR 频道自动退避重启）。"""
            from agent.channel import get_channel_manager
            start_channel_supervisor(get_channel_manager())

    @machine.node(skip_on_error=False)
    async def arm_shutdown_signals() -> None:
        """注册 SIGINT/SIGTERM 优雅关停处理器。"""
        from core.lifecycle import Lifecycle

        def _request_shutdown() -> None:
            if not ctx.shutdown_event.is_set():
                ctx.shutdown_event.set()

        Lifecycle.set_shutdown_requester(_request_shutdown)

        def _on_signal() -> None:
            if ctx.shutdown_event.is_set():
                for s in (signal.SIGINT, signal.SIGTERM):
                    with contextlib.suppress(NotImplementedError, ValueError, RuntimeError):
                        ctx.loop.remove_signal_handler(s)
                return
            _request_shutdown()

        # Windows ProactorEventLoop 不支持 add_signal_handler
        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                ctx.loop.add_signal_handler(sig, _on_signal)
        except NotImplementedError:
            def _win_handler(signum: int, frame: object) -> None:
                ctx.loop.call_soon_threadsafe(_request_shutdown)

            for sig in (signal.SIGINT, signal.SIGTERM):
                with contextlib.suppress(ValueError, OSError):
                    signal.signal(sig, _win_handler)

    return machine


def create_shutdown_flow(ctx: _LaunchContext) -> FlowMachine:
    """构建关停流程：全节点容错，按依赖逆序清理。"""
    machine = FlowMachine()

    @machine.node(skip_on_error=True)
    async def flush_pending_memory() -> None:
        """退出前强制提取各会话待定的记忆内容（限时 30s）。"""
        try:
            from agent.memory.auto_capture import flush_auto_capture
            from services._runtime import require_runtime
            await asyncio.wait_for(
                flush_auto_capture(require_runtime().mind), timeout=30.0,
            )
        except Exception:
            pass

    @machine.node(skip_on_error=True)
    async def silence_shutdown_logs() -> None:
        """屏蔽 uvicorn 关停噪音与事件循环晚期异常回调。"""
        logging.getLogger("uvicorn.error").disabled = True
        ctx.loop.set_exception_handler(lambda _l, _c: None)

    @machine.node(skip_on_error=True)
    async def signal_web_shutdown() -> None:
        """请求 Web 服务器优雅关停。"""
        if ctx.web_task and not ctx.web_task.done():
            from web.server import request_web_shutdown
            request_web_shutdown()

    @machine.node(skip_on_error=True)
    async def shutdown_mcp_bridge() -> None:
        """关闭 MCP Bridge 连接。"""
        from entities.mcp.bridge import get_mcp_bridge
        bridge = get_mcp_bridge()
        if bridge:
            bridge.shutdown()

    @machine.node(skip_on_error=True)
    async def stop_channel_watchdog() -> None:
        """停止频道看门狗。"""
        from agent.channel.supervision import stop_channel_supervisor
        await stop_channel_supervisor()

    @machine.node(skip_on_error=True)
    async def stop_channels() -> None:
        """停止全部频道（先取消未完成的后台启动）。"""
        from agent.channel import get_channel_manager
        if ctx.channels_task and not ctx.channels_task.done():
            ctx.channels_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await ctx.channels_task
        try:
            await get_channel_manager().stop_all()
        except BaseException:
            pass

    @machine.node(skip_on_error=True)
    async def shutdown_lifecycles() -> None:
        """逆序清理所有 Lifecycle 注册的单例。"""
        from core.lifecycle import Lifecycle
        await Lifecycle.shutdown_all()

    @machine.node(skip_on_error=True)
    async def await_web_exit() -> None:
        """等待 Web 服务器任务退出，必要时取消。"""
        task = ctx.web_task
        if task is None:
            return
        if not task.done():
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    return machine


async def _run(args: argparse.Namespace) -> None:
    """编排进程生命周期：启动流程 → 等待关停事件 → 关停流程。"""
    ctx = _LaunchContext(
        args=args,
        loop=asyncio.get_running_loop(),
        shutdown_event=asyncio.Event(),
    )

    result = await create_launch_flow(ctx).execute()
    if not result.success:
        log("启动流程未完成，进程退出", "ERROR", tag="启动")
        return

    await ctx.shutdown_event.wait()

    log("正在关闭...")
    await create_shutdown_flow(ctx).execute()


def main() -> None:
    """进程级准备：解析参数、设置日志、启用 faulthandler，然后进入异步主流程。"""
    parser = argparse.ArgumentParser(description='AnelfAgent')
    parser.add_argument('--log-level', choices=level_emoji.keys(), default='DEBUG')
    parser.add_argument('--no-webui', action='store_true', help='不启动 WebUI')
    args = parser.parse_args()
    set_log_level(args.log_level)
    _setup_faulthandler()

    try:
        asyncio.run(_run(args))
    except (KeyboardInterrupt, SystemExit):
        pass

    from core.lifecycle import RESTART_EXIT_CODE, Lifecycle
    if Lifecycle.restart_requested():
        log(f"收到重启请求，以退出码 {RESTART_EXIT_CODE} 退出（由外层启动脚本重新拉起）", tag="重启")
        # 第三方库可能残留非守护线程，SystemExit 会永久阻塞在解释器关闭的 join 上，故直接退出
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(RESTART_EXIT_CODE)


if __name__ == "__main__":
    main()
