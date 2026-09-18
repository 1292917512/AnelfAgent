"""人脸入库 worker 测试：投递去重 / 来源门控 / 无 worker 时 no-op。"""

from __future__ import annotations

from agent.vision.face import worker as worker_mod


def test_submit_no_worker_is_noop() -> None:
    worker_mod.set_face_worker(None)
    assert worker_mod.submit_face_image("/tmp/x.jpg", "inbound") is False


def test_worker_submit_dedup() -> None:
    w = worker_mod.FaceIngestWorker()
    w.submit("/tmp/a.jpg", "inbound")
    w.submit("/tmp/a.jpg", "inbound")  # 重复投递
    w.submit("/tmp/b.jpg", "inbound")
    assert w._queue.qsize() == 2


def test_submit_gating_by_source() -> None:
    """视觉源帧默认关（face_watch_enabled=False），入站图片默认开。"""
    w = worker_mod.FaceIngestWorker()
    worker_mod.set_face_worker(w)
    try:
        assert worker_mod.submit_face_image("/tmp/v.jpg", "vision:screen") is False
        assert worker_mod.submit_face_image("/tmp/i.jpg", "inbound") is True
        assert w._queue.qsize() == 1  # 仅 inbound 入队
    finally:
        worker_mod.set_face_worker(None)


def test_empty_path_rejected() -> None:
    w = worker_mod.FaceIngestWorker()
    worker_mod.set_face_worker(w)
    try:
        assert worker_mod.submit_face_image("", "inbound") is False
    finally:
        worker_mod.set_face_worker(None)
