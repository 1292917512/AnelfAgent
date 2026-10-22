"""独占派生索引的 SDK 子进程；父进程退出时丢弃工作区，由快照恢复。

标准输出仅承载 JSON 协议，SDK 输出转到标准错误。凭据只经匿名管道传入。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, TextIO

from filelock import FileLock

from .config import CogneeChatModelConfig, CogneeConfig, CogneeEmbeddingModelConfig
from .wire import decode, encode

METHODS = frozenset({"recall", "add", "cognify", "improve", "list_datasets", "list_data", "delete_data",
                     "delete_dataset", "verify_graph_documents", "pipeline_progress", "compact_vector_storage"})


async def serve(output: TextIO) -> None:
    from .adapters import query_embeddings
    from .errors import error_text
    from .ladybug_guard import native_restart_port
    from .sdk_client import SDKClient

    client: SDKClient | None = None
    lock: FileLock | None = None
    tasks: set[asyncio.Task[None]] = set()
    closing = False

    def abort_native(**_kwargs: Any) -> dict[str, Any]:
        os._exit(70)

    # 子进程是此端口的组合根；失控只丢弃候选区，不重启主服务。
    native_restart_port.set(abort_native)

    def reply(request_id: int, **payload: Any) -> None:
        import psutil

        try:
            payload["written_bytes"] = psutil.Process().io_counters().write_bytes
        except (AttributeError, psutil.Error):
            pass  # 平台没有写入计数；宿主熔断器会明确停用该指标。
        output.write(json.dumps({"id": request_id, **payload}, ensure_ascii=False) + "\n")
        output.flush()

    async def execute(message: dict[str, Any]) -> None:
        nonlocal client, lock, closing
        request_id = message["id"]
        try:
            method = message["method"]
            if method == "initialize":
                if client is not None:
                    raise ValueError("工作进程只允许初始化一次")
                values = message["kwargs"]["config"]
                values["chat"] = CogneeChatModelConfig(**values["chat"])
                values["embedding"] = CogneeEmbeddingModelConfig(**values["embedding"])
                config = CogneeConfig(**values).normalized()
                lock = FileLock(str(Path(config.absolute_data_root) / ".publication" / "worker.lock"), timeout=0)
                lock.acquire()
                client = SDKClient(config)
                await client.configure_resolved(message["kwargs"]["chat"], message["kwargs"]["embedding"])
                result = client.availability()
            elif method == "close":
                closing = True
                if client is not None:
                    await client.close()
                reply(request_id, result=None)
                # 原生缓存已排空；不执行第三方库的 atexit 关闭顺序。
                os._exit(0)
            elif method in METHODS and client is not None and not closing:
                kwargs = decode(message.get("kwargs", {}))
                if method == "recall":
                    for key in ("query_type", "search_type"):
                        if isinstance(kwargs.get(key), str):
                            kwargs[key] = client.search_type(kwargs[key])
                async with query_embeddings():
                    result = await getattr(client, method)(*decode(message.get("args", [])), **kwargs)
            else:
                raise ValueError("未初始化或不支持的 Cognee 调用")
            reply(request_id, result=encode(result))
        except Exception as exc:
            reply(request_id, error=error_text(exc), error_class=getattr(exc, "error_class", type(exc).__name__))

    first = True
    while True:
        line = await asyncio.to_thread(sys.stdin.buffer.readline)
        if not line:
            # EOF 表示宿主已退出；工作区始终有 dirty 标记，禁止继续脱离宿主写入。
            os._exit(0)
        message = json.loads(line)
        if first:
            first = False
            await execute(message)
            continue
        task = asyncio.create_task(execute(message))
        tasks.add(task)
        task.add_done_callback(tasks.discard)


def main(output: TextIO) -> None:
    import psutil

    parent = psutil.Process(os.getppid())

    def watch_parent() -> None:
        while parent.is_running():
            threading.Event().wait(1)
        os._exit(0)

    threading.Thread(target=watch_parent, name="cognee-parent-watch", daemon=True).start()
    os.environ["GRAPH_DATABASE_SUBPROCESS_ENABLED"] = "false"
    os.environ["VECTOR_DB_SUBPROCESS_ENABLED"] = "false"
    asyncio.run(serve(output))
