"""AnelfAgent 进程入口 — 装配 Application 宿主并运行。

职责仅限组合根：进程级准备（参数 / 日志 / faulthandler）→ 声明启动步骤
（地基 / bootstrap 子流程 / 权限规则 / 用户 hooks / Web 服务注册）→
注入关停前置钩子 → ``app.run()``。长驻服务的启动与回收全部由
Lifecycle 宿主承载（见 core/application.py 与 core/lifecycle.py）。
"""
import argparse
import asyncio
import contextlib
import faulthandler
import os
import signal
import sys
import warnings

warnings.filterwarnings("ignore", message="urllib3.*doesn't match a supported version")

from core.application import Application
from core.config import ConfigManager
from core.log import enable_file_logging, level_emoji, log, set_log_level


def _setup_faulthandler() -> None:
    """启用故障转储：致命错误自动打印堆栈；运行期可 kill -USR1 <pid> 在线抓全线程栈。"""
    faulthandler.enable()
    with contextlib.suppress(AttributeError, ValueError, OSError, RuntimeError):
        faulthandler.register(signal.SIGUSR1, all_threads=True)


def create_application(args: argparse.Namespace) -> Application:
    """装配应用：启动步骤注册到 startup 流程，关停前置钩子注入宿主。"""
    app = Application()

    @app.startup.node(skip_on_error=False)
    async def init_foundation() -> None:
        """初始化配置中心并开启文件日志。"""
        ConfigManager.initialize()
        enable_file_logging()

    @app.startup.node(skip_on_error=False)
    async def run_bootstrap() -> None:
        """执行运行时组装（agent/runtime/bootstrap.py 子流程）。"""
        from agent.runtime.bootstrap import create_bootstrap
        await create_bootstrap().execute()

    @app.startup.node(skip_on_error=False)
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

    @app.startup.node(skip_on_error=False)
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

    if not args.no_webui:
        @app.startup.node(skip_on_error=False)
        async def register_web_service() -> None:
            """Web 服务器注册进 Lifecycle（on_start 拉起 / cleanup 关停）。"""
            from core.lifecycle import Lifecycle
            from web.server import WebServerService

            service = WebServerService()
            Lifecycle.register(
                "web_server", service,
                on_start=service.start, cleanup=service.stop,
            )

    async def _flush_pending_memory() -> None:
        """记忆自动捕获兜底：退出前强制提取各会话待定内容（限时 30s）。"""
        from agent.memory.auto_capture import flush_auto_capture
        from services._runtime import require_runtime
        await asyncio.wait_for(
            flush_auto_capture(require_runtime().mind), timeout=30.0,
        )

    def _silence_shutdown_logs() -> None:
        """屏蔽 uvicorn 关停噪音与事件循环晚期异常回调。"""
        import logging
        logging.getLogger("uvicorn.error").disabled = True
        asyncio.get_running_loop().set_exception_handler(lambda _l, _c: None)

    app.on_pre_shutdown("flush_pending_memory", _flush_pending_memory)
    app.on_pre_shutdown("silence_shutdown_logs", _silence_shutdown_logs)

    from agent.runtime.bootstrap import cancel_background_tasks
    app.on_pre_shutdown("cancel_background_tasks", cancel_background_tasks)

    return app


def main() -> None:
    """进程级准备：解析参数、设置日志、启用 faulthandler，然后进入异步主流程。"""
    parser = argparse.ArgumentParser(description='AnelfAgent')
    parser.add_argument('--log-level', choices=level_emoji.keys(), default='DEBUG')
    parser.add_argument('--no-webui', action='store_true', help='不启动 WebUI')
    args = parser.parse_args()
    set_log_level(args.log_level)
    _setup_faulthandler()

    try:
        asyncio.run(create_application(args).run())
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
