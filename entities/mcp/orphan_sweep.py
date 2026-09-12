"""MCP stdio 孤儿子进程的启动清扫。

stdio server 经 ``sh -c "cmd | cmd"`` 管道拉起时，SDK 关停只终止它直接
拉起的包装进程，管道其余成员成为孤儿；崩溃 / 看门狗 SIGKILL / restart.sh
的 kill -9 兜底更是整体跳过清理。长期运行每次重启泄漏一批（实证：数月
累积约 80 对 mind-map-mcp 管道孤儿，且持有旧日志 fd 制造稀疏文件假象）。

启动时清扫：PPID==1 且 cmdline 与已配置 stdio 命令精确匹配的进程必为
历史残留——本实例的子进程父 PID 是本进程，同机其他检出副本的子进程
父 PID 是那个实例的 Python 进程，均不为 1。终止根进程及其全部后代。
"""

from __future__ import annotations

import asyncio
from typing import Collection, List, Sequence, Set, Tuple

from core.log import log

from .config import MCPServerConfig

# 进程快照：(pid, ppid, cmdline, status)
ProcSnapshot = Tuple[int, int, Tuple[str, ...], str]

_ZOMBIE = "zombie"


def select_orphan_roots(
    procs: Sequence[ProcSnapshot],
    signatures: Collection[str],
    own_pid: int,
) -> List[int]:
    """纯函数：从进程快照选出孤儿根进程 pid。

    判据：ppid==1（被 init 收养的历史残留）、cmdline 展平后与某个已配置
    stdio 命令签名精确相等、非本进程、非僵尸（僵尸由 init 收割，终止无意义）。
    """
    sig_set: Set[str] = set(signatures)
    roots: List[int] = []
    for pid, ppid, cmdline, status in procs:
        if pid == own_pid or ppid != 1 or status == _ZOMBIE:
            continue
        if " ".join(cmdline) in sig_set:
            roots.append(pid)
    return roots


def stdio_command_signatures(servers: Sequence[MCPServerConfig]) -> Set[str]:
    """从 MCP 配置提取 stdio 命令签名（"command args..." 展平，含未启用的）。

    未启用 server 的历史残留同样要清扫——server 可能是后来才被禁用/删除的。
    """
    signatures: Set[str] = set()
    for srv in servers:
        if not srv.command:
            continue
        signatures.add(" ".join([srv.command, *srv.args]))
    return signatures


def sweep_orphaned_stdio_servers(signatures: Collection[str]) -> List[int]:
    """扫描并终止匹配签名的孤儿进程树，返回被终止的根 pid 列表（同步阻塞）。

    任何异常都不外抛——清扫失败仅记日志，绝不阻塞 MCP 启动。
    """
    if not signatures:
        return []
    try:
        import psutil
    except ImportError:
        return []

    own_pid = psutil.Process().pid
    snapshots: List[ProcSnapshot] = []
    try:
        # ad_value：系统进程（launchd 等）字段无权限时降级为 None 而非抛出，
        # 单个进程不可读不拖垮整轮扫描
        for p in psutil.process_iter(
                ["pid", "ppid", "cmdline", "status"], ad_value=None):
            info = p.info
            snapshots.append((
                p.pid,
                info.get("ppid") or 0,
                tuple(info.get("cmdline") or ()),
                info.get("status") or "",
            ))
    except Exception as exc:
        log(f"MCP 孤儿清扫进程扫描失败（忽略）: {exc}", "DEBUG", tag="MCP")
        return []

    swept: List[int] = []
    for pid in select_orphan_roots(snapshots, signatures, own_pid):
        swept.append(pid)
        try:
            proc = psutil.Process(pid)
            targets = [proc, *proc.children(recursive=True)]
            for target in targets:
                try:
                    target.terminate()
                except psutil.Error:
                    pass
            _gone, alive = psutil.wait_procs(targets, timeout=1.5)
            for target in alive:
                try:
                    target.kill()
                except psutil.Error:
                    pass
            log(f"MCP 孤儿清扫: 终止残留进程树 pid={pid}（成员 {len(targets)} 个）", tag="MCP")
        except psutil.Error:
            continue
    return swept


async def sweep_orphans_async(signatures: Collection[str]) -> List[int]:
    """事件循环友好的入口（psutil 扫描与等待移出事件循环线程）。"""
    return await asyncio.to_thread(sweep_orphaned_stdio_servers, signatures)
