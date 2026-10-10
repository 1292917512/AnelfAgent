"""日志流快照、跨线程积压与订阅释放。"""

import asyncio
from collections.abc import Iterator

import pytest

from core import log as log_module
from services.logs import stream_logs


@pytest.fixture(autouse=True)
def fresh_buffer() -> Iterator[None]:
    log_module.clear_log_buffer()
    yield
    log_module.clear_log_buffer()


@pytest.mark.asyncio
async def test_snapshot_and_threaded_updates_release_listener() -> None:
    log_module._notify_listeners("INFO", "before", "test")
    listeners = len(log_module._all_listeners)
    stream = stream_logs()
    event, payload = await anext(stream)
    assert event == "snapshot"
    assert [row["message"] for row in payload["logs"]] == ["before"]
    assert len(log_module._all_listeners) == listeners + 1
    await asyncio.to_thread(log_module._notify_listeners, "ERROR", "after", "worker")
    event, payload = await asyncio.wait_for(anext(stream), 1)
    assert event == "log"
    assert payload["message"] == "after"
    assert payload["tag"] == "worker"
    assert payload["timestamp"] > 0
    await stream.aclose()
    assert len(log_module._all_listeners) == listeners


@pytest.mark.asyncio
async def test_slow_consumer_resyncs_without_recursive_logs() -> None:
    stream = stream_logs()
    await anext(stream)
    for index in range(2400):
        log_module._notify_listeners("DEBUG", str(index), "load")
    event, payload = await asyncio.wait_for(anext(stream), 1)
    assert event == "snapshot"
    assert len(payload["logs"]) == 2000
    assert payload["logs"][0]["message"] == "400"
    assert payload["logs"][-1]["message"] == "2399"
    assert len({entry["seq"] for entry in payload["logs"]}) == 2000
    log_module._notify_listeners("INFO", "after resync")
    event, payload = await asyncio.wait_for(anext(stream), 1)
    assert event == "log" and payload["message"] == "after resync"
    await stream.aclose()


@pytest.mark.asyncio
async def test_delayed_thread_notification_is_not_lost() -> None:
    stream = stream_logs()
    await anext(stream)
    callback = log_module._all_listeners[-1]
    callback({"seq": 12, "level": "INFO", "message": "newer", "timestamp": 1.0})
    assert (await asyncio.wait_for(anext(stream), 1))[1]["message"] == "newer"
    callback({"seq": 11, "level": "INFO", "message": "delayed", "timestamp": 1.0})
    assert (await asyncio.wait_for(anext(stream), 1))[1]["message"] == "delayed"
    await stream.aclose()
