"""异步进程边界：真实进程、取消创建、重复取消与输出背压。"""

from __future__ import annotations

import asyncio
import sys

import pytest

from core.process import managed_process


@pytest.mark.parametrize("output", [0, 2_000_000])
async def test_cancel_reaps_child_and_pipe_buffers(output):
    entered = asyncio.Event()
    children = []
    async def run():
        async with managed_process(sys.executable, "-c",
                                   f"import sys,time;sys.stdout.write('x'*{output});sys.stdout.flush();time.sleep(30)",
                                   stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE) as proc:
            children.append(proc)
            entered.set()
            await proc.communicate()
    task = asyncio.create_task(run())
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 10)
    assert children[0].returncode is not None


async def test_repeated_cancel_during_process_creation_reclaims_created_child(monkeypatch):
    from core import process
    creation, release = asyncio.Event(), asyncio.Event()
    children = []
    original = asyncio.create_subprocess_exec
    async def slow_create(*args, **kwargs):
        proc = await original(*args, **kwargs)
        children.append(proc)
        creation.set()
        await release.wait()
        return proc
    monkeypatch.setattr(process.asyncio, "create_subprocess_exec", slow_create)
    async def run():
        async with managed_process(sys.executable, "-c", "import time;time.sleep(30)"):
            pytest.fail("cancelled creation must not enter the operation")
    task = asyncio.create_task(run())
    await asyncio.wait_for(creation.wait(), 5)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 10)
    assert children[0].returncode is not None


async def test_process_exception_reaps_child():
    children = []
    with pytest.raises(RuntimeError, match="operation failed"):
        async with managed_process(sys.executable, "-c", "import time;time.sleep(30)") as proc:
            children.append(proc)
            raise RuntimeError("operation failed")
    assert children[0].returncode is not None


@pytest.mark.parametrize("parent_exits", [False, True])
async def test_cancellation_reaps_spawned_grandchild(parent_exits):
    import psutil

    ready = asyncio.Event()
    descendants = []

    async def run():
        command = ("import subprocess,sys,time;"
                   "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);"
                   "print(p.pid,flush=True);" + ("" if parent_exits else "time.sleep(30)"))
        async with managed_process(sys.executable, "-c", command, stdout=asyncio.subprocess.PIPE) as proc:
            descendants.append(psutil.Process(int(await proc.stdout.readline())))
            if parent_exits:
                async with asyncio.timeout(5):
                    while proc.returncode is None:
                        await asyncio.sleep(.01)
            ready.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(run())
    await asyncio.wait_for(ready.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 10)
    for _ in range(100):
        try:
            if not descendants[0].is_running() or descendants[0].status() == psutil.STATUS_ZOMBIE:
                break
        except psutil.NoSuchProcess:
            break
        await asyncio.sleep(.01)
    else:
        pytest.fail("owned process group left a running grandchild")
